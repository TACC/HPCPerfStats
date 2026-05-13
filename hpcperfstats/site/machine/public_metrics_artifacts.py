"""Prewarmed gzip-compressed public dashboard payloads (expansion-factor aggregates).

Computed only from ``update_metrics`` scheduler passes — HTTP handlers must not
reaggregate heavy ranges here.

Rows are **not** deleted when inputs change: invalidation sets ``rebuild_required``
so the bundle omits those periods until :func:`refresh_public_expansion_factor_artifacts`
(or the parallel scheduler path) recomputes and clears the flag.

The scheduler runs :func:`refresh_public_expansion_factor_artifacts_parallel` on
the metrics ``multiprocessing`` pool (one calendar month or one calendar year
per task) and **completes** that pass before starting job-based metrics.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
import math
import time
from collections import defaultdict
from datetime import date, datetime
from multiprocessing import TimeoutError as MultiprocessingTimeoutError
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from django.db.models import F, Max, Min, Q
from django.utils import timezone as dj_tz

from hpcperfstats.site.machine.models import job_data, public_metrics_artifact

logger = logging.getLogger(__name__)

APP_PUBLIC_METRICS_SCHEMA_VERSION = 2

PAYLOAD_ENCODING_GZIP_JSON = "gzip_json"

# Expansion-factor aggregates derived from scheduler timestamps only (see Carlson EF definitions).
PUBLIC_EF_MONTH_DAILY = "ef_month_daily"
PUBLIC_EF_YEAR_WEEKLY = "ef_year_weekly"

# Task kind strings for :func:`_public_ef_period_worker` (pickled by multiprocessing).
_PUBLIC_EF_KIND_MONTH = "month"
_PUBLIC_EF_KIND_YEAR = "year"

# Upper-exclusive EF histogram bin edges (last bucket captures overflow).
EF_HIST_BIN_EDGES: Tuple[float, ...] = (
    0.0,
    0.5,
    1.0,
    1.25,
    1.5,
    2.0,
    2.5,
    3.0,
    4.0,
    5.0,
    7.0,
    10.0,
    15.0,
    20.0,
    30.0,
    50.0,
    100.0,
)


def compute_scheduler_expansion_factor_seconds(
    submit_time: Optional[datetime],
    start_time: Optional[datetime],
    runtime_seconds: Optional[float],
    ncores: Optional[int],
) -> Optional[float]:
  """Return EF as (queue_wait + runtime) / (ncores * runtime); ``None`` when invalid."""
  if runtime_seconds is None or runtime_seconds <= 0:
    return None
  nc = int(ncores or 0)
  if nc <= 0:
    return None
  if submit_time is None or start_time is None:
    return None
  qw = (start_time - submit_time).total_seconds()
  if qw < 0:
    return None
  denom = float(nc) * float(runtime_seconds)
  if denom <= 0:
    return None
  return (qw + float(runtime_seconds)) / denom


def _histogram_counts(values: Sequence[float]) -> Tuple[List[float], List[int]]:
  edges = list(EF_HIST_BIN_EDGES)
  counts = [0] * len(edges)
  # Last bucket is overflow for values >= edges[-1]
  for raw in values:
    if raw is None or not math.isfinite(raw):
      continue
    v = float(raw)
    placed = False
    for i in range(len(edges) - 1):
      lo = edges[i]
      hi = edges[i + 1]
      if v >= lo and v < hi:
        counts[i] += 1
        placed = True
        break
    if not placed and v >= edges[-1]:
      counts[-1] += 1
  return edges, counts


def _eligible_jobs_filter() -> Q:
  return Q(runtime__gt=0, ncores__gt=0, start_time__gte=F("submit_time"))


def _streaming_jid_epoch_fingerprint(prefix: str, qs) -> str:
  """Stable fingerprint over sorted ``(jid, end_time)`` pairs."""
  h = hashlib.sha256()
  h.update(str(APP_PUBLIC_METRICS_SCHEMA_VERSION).encode())
  h.update(b"|")
  h.update(prefix.encode())
  h.update(b"|")
  for jid, et in qs.order_by("jid").values_list("jid", "end_time").iterator(chunk_size=2048):
    h.update(str(jid).encode())
    h.update(b"|")
    if et is None:
      h.update(b"none")
    else:
      h.update(str(et.timestamp()).encode())
    h.update(b"\n")
  return h.hexdigest()[:64]


def _period_month_bounds(year_month: str) -> Tuple[datetime, datetime]:
  year_s, mon_s = year_month.split("-", 1)
  y = int(year_s)
  m = int(mon_s)
  start = datetime(y, m, 1, tzinfo=dj_tz.utc)
  if m == 12:
    end = datetime(y + 1, 1, 1, tzinfo=dj_tz.utc)
  else:
    end = datetime(y, m + 1, 1, tzinfo=dj_tz.utc)
  return start, end


def _period_year_bounds(year: int) -> Tuple[datetime, datetime]:
  start = datetime(year, 1, 1, tzinfo=dj_tz.utc)
  end = datetime(year + 1, 1, 1, tzinfo=dj_tz.utc)
  return start, end


def _month_keys_present() -> List[str]:
  qs = (
      job_data.objects.filter(_eligible_jobs_filter())
      .aggregate(mn=Min("end_time"), mx=Max("end_time"))
  )
  mn, mx = qs.get("mn"), qs.get("mx")
  if mn is None or mx is None:
    return []
  cur = date(mn.year, mn.month, 1)
  last = date(mx.year, mx.month, 1)
  keys: List[str] = []
  while cur <= last:
    keys.append(f"{cur.year:04d}-{cur.month:02d}")
    if cur.month == 12:
      cur = date(cur.year + 1, 1, 1)
    else:
      cur = date(cur.year, cur.month + 1, 1)
  return keys


def _year_keys_present() -> List[str]:
  qs = job_data.objects.filter(_eligible_jobs_filter()).aggregate(
      mn=Min("end_time"),
      mx=Max("end_time"),
  )
  mn, mx = qs.get("mn"), qs.get("mx")
  if mn is None or mx is None:
    return []
  return [str(y) for y in range(mn.year, mx.year + 1)]


def _payload_from_daily_means(daily_means: List[float]) -> Dict[str, Any]:
  edges, counts = _histogram_counts(daily_means)
  return {
      "scheduler_expansion_factor_daily_means_in_month_count": len(daily_means),
      "histogram_bin_edges": edges,
      "histogram_counts": counts,
      "expansion_factor_definition": (
          "(queue_wait_seconds + runtime_seconds) / (ncores * runtime_seconds)"
      ),
  }


def _payload_from_weekly_means(weekly_means: List[float]) -> Dict[str, Any]:
  edges, counts = _histogram_counts(weekly_means)
  return {
      "scheduler_expansion_factor_weekly_means_in_year_count": len(weekly_means),
      "histogram_bin_edges": edges,
      "histogram_counts": counts,
      "expansion_factor_definition": (
          "(queue_wait_seconds + runtime_seconds) / (ncores * runtime_seconds)"
      ),
  }


def _attach_ef_histogram_bokeh_item(
    payload: Dict[str, Any],
    *,
    period_key: str,
    subtitle: str,
) -> None:
  edges = payload.get("histogram_bin_edges")
  counts = payload.get("histogram_counts")
  if not isinstance(edges, list) or not isinstance(counts, list):
    return
  from hpcperfstats.site.machine.public_metrics_bokeh import (
      build_public_expansion_factor_histogram_json_item,
  )

  bokeh_item = build_public_expansion_factor_histogram_json_item(
      period_key=period_key,
      period_kind=subtitle,
      edges=edges,
      counts=counts,
  )
  if bokeh_item is not None:
    payload["bokeh_histogram_json_item"] = bokeh_item


def _build_month_daily_payload(year_month: str) -> Dict[str, Any]:
  start, end = _period_month_bounds(year_month)
  qs = (
      job_data.objects.filter(_eligible_jobs_filter())
      .filter(end_time__gte=start, end_time__lt=end)
      .only("jid", "submit_time", "start_time", "runtime", "ncores", "end_time")
  )
  day_sum: Dict[date, float] = defaultdict(float)
  day_cnt: Dict[date, int] = defaultdict(int)
  for row in qs.iterator(chunk_size=2048):
    ef = compute_scheduler_expansion_factor_seconds(
        row.submit_time,
        row.start_time,
        row.runtime,
        row.ncores,
    )
    if ef is None:
      continue
    d = row.end_time.date() if row.end_time else None
    if d is None:
      continue
    day_sum[d] += ef
    day_cnt[d] += 1
  daily_means = []
  for d in sorted(day_sum.keys()):
    n = day_cnt[d]
    if n <= 0:
      continue
    daily_means.append(day_sum[d] / float(n))
  payload = _payload_from_daily_means(daily_means)
  payload["year_month"] = year_month
  _attach_ef_histogram_bokeh_item(
      payload,
      period_key=year_month,
      subtitle="histogram of daily mean EF (days in month)",
  )
  return payload


def _build_year_weekly_payload(year_str: str) -> Dict[str, Any]:
  year = int(year_str)
  start, end = _period_year_bounds(year)
  qs = (
      job_data.objects.filter(_eligible_jobs_filter())
      .filter(end_time__gte=start, end_time__lt=end)
      .only("jid", "submit_time", "start_time", "runtime", "ncores", "end_time")
  )
  week_sum: Dict[Tuple[int, int], float] = defaultdict(float)
  week_cnt: Dict[Tuple[int, int], int] = defaultdict(int)
  for row in qs.iterator(chunk_size=2048):
    ef = compute_scheduler_expansion_factor_seconds(
        row.submit_time,
        row.start_time,
        row.runtime,
        row.ncores,
    )
    if ef is None:
      continue
    et = row.end_time
    if et is None:
      continue
    iso_year, iso_week, _ = et.date().isocalendar()
    key = (iso_year, iso_week)
    week_sum[key] += ef
    week_cnt[key] += 1
  weekly_means: List[float] = []
  for key in sorted(week_sum.keys()):
    n = week_cnt[key]
    if n <= 0:
      continue
    weekly_means.append(week_sum[key] / float(n))
  payload = _payload_from_weekly_means(weekly_means)
  payload["calendar_year"] = year_str
  _attach_ef_histogram_bokeh_item(
      payload,
      period_key=year_str,
      subtitle="histogram of weekly mean EF (ISO weeks in year)",
  )
  return payload


def _upsert_row(scope: str, period_key: str, fingerprint: str, payload: Dict[str, Any]):
  blob = gzip.compress(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
  public_metrics_artifact.objects.update_or_create(
      scope=scope,
      period_key=period_key,
      defaults={
          "payload_compressed": blob,
          "payload_encoding": PAYLOAD_ENCODING_GZIP_JSON,
          "input_fingerprint": fingerprint,
          "rebuild_required": False,
      },
  )


def decompress_public_payload(row: public_metrics_artifact) -> Dict[str, Any]:
  if row.payload_encoding != PAYLOAD_ENCODING_GZIP_JSON:
    raise ValueError("unsupported encoding")
  raw = gzip.decompress(bytes(row.payload_compressed))
  return json.loads(raw.decode("utf-8"))


def _prune_orphan_public_ef_rows(months: Sequence[str], years: Sequence[str]) -> None:
  """Remove persisted rows for periods with no backing job_data (retention / deletes)."""
  month_qs = public_metrics_artifact.objects.filter(scope=PUBLIC_EF_MONTH_DAILY)
  if months:
    month_qs.exclude(period_key__in=list(months)).delete()
  else:
    month_qs.delete()
  year_qs = public_metrics_artifact.objects.filter(scope=PUBLIC_EF_YEAR_WEEKLY)
  if years:
    year_qs.exclude(period_key__in=list(years)).delete()
  else:
    year_qs.delete()


def _sync_reconcile_public_ef_month(ym: str) -> Dict[str, int]:
  """Fingerprint check + optional rebuild for one ``YYYY-MM`` period (any process)."""
  start, end = _period_month_bounds(ym)
  qs = (
      job_data.objects.filter(_eligible_jobs_filter())
      .filter(end_time__gte=start, end_time__lt=end)
      .only("jid", "end_time")
  )
  fp = _streaming_jid_epoch_fingerprint(f"{PUBLIC_EF_MONTH_DAILY}:{ym}", qs)
  meta = (
      public_metrics_artifact.objects.filter(
          scope=PUBLIC_EF_MONTH_DAILY, period_key=ym
      )
      .values("input_fingerprint", "rebuild_required")
      .first()
  )
  if (
      meta is not None
      and not meta["rebuild_required"]
      and meta["input_fingerprint"] == fp
  ):
    return {"rebuilt_month_periods": 0, "skipped_month_periods": 1}
  payload = _build_month_daily_payload(ym)
  _upsert_row(PUBLIC_EF_MONTH_DAILY, ym, fp, payload)
  return {"rebuilt_month_periods": 1, "skipped_month_periods": 0}


def _sync_reconcile_public_ef_year(ys: str) -> Dict[str, int]:
  """Fingerprint check + optional rebuild for one calendar year period (any process)."""
  year_int = int(ys)
  start, end = _period_year_bounds(year_int)
  qs = (
      job_data.objects.filter(_eligible_jobs_filter())
      .filter(end_time__gte=start, end_time__lt=end)
      .only("jid", "end_time")
  )
  fp = _streaming_jid_epoch_fingerprint(f"{PUBLIC_EF_YEAR_WEEKLY}:{ys}", qs)
  meta = (
      public_metrics_artifact.objects.filter(
          scope=PUBLIC_EF_YEAR_WEEKLY, period_key=ys
      )
      .values("input_fingerprint", "rebuild_required")
      .first()
  )
  if (
      meta is not None
      and not meta["rebuild_required"]
      and meta["input_fingerprint"] == fp
  ):
    return {"rebuilt_year_periods": 0, "skipped_year_periods": 1}
  payload = _build_year_weekly_payload(ys)
  _upsert_row(PUBLIC_EF_YEAR_WEEKLY, ys, fp, payload)
  return {"rebuilt_year_periods": 1, "skipped_year_periods": 0}


def _public_ef_reset_fork_inherited_db() -> None:
  """Drop connections inherited after ``multiprocessing`` fork before ORM queries.

  Forked worker processes share their parent's Postgres socket unless closed.
  Django's ``QuerySet.iterator(chunk_size=…)`` declares named server-side
  cursors; two processes multiplexing one session yields ``cursor … already exists``.
  """
  from django.db import close_old_connections, connections

  connections.close_all()
  close_old_connections()


def _public_ef_parent_refresh_connections_after_forked_children() -> None:
  """Reinitialize this process's DB handles after forked workers close shared fds.

  ``connections.close_all()`` in a worker closes inherited sockets that were
  still referenced by Django's wrappers in this (parent) process; discard and
  reconnect before any subsequent ORM in the scheduler thread.
  """
  from django.db import close_old_connections, connections

  connections.close_all()
  close_old_connections()


def _public_ef_period_worker(task: Tuple[str, str]) -> Dict[str, int]:
  """Picklable multiprocessing worker: reconcile exactly one month or year period."""
  from hpcperfstats.django_bootstrap import ensure_django

  ensure_django()
  _public_ef_reset_fork_inherited_db()
  kind, key = task
  try:
    if kind == _PUBLIC_EF_KIND_MONTH:
      return _sync_reconcile_public_ef_month(key)
    if kind == _PUBLIC_EF_KIND_YEAR:
      return _sync_reconcile_public_ef_year(key)
    logger.error("public EF worker unknown kind=%s key=%s", kind, key)
    return {"worker_exceptions": 1}
  except Exception:
    logger.exception("public EF period worker failed kind=%s key=%s", kind, key)
    return {"worker_exceptions": 1}


def refresh_public_expansion_factor_artifacts_parallel(
    pool: Any,
    *,
    poll_timeout_s: float = 5.0,
    no_progress_timeout_s: float = 120.0,
    progress_callback=None,
) -> Dict[str, int]:
  """Recompute stale month/year EF rows using ``pool`` (one period per task).

  Callers typically pass ``Metrics.ensure_pool()`` from ``update_metrics`` so
  /pub aggregates finish before the same pool runs per-job metrics.
  """
  months = _month_keys_present()
  years = _year_keys_present()
  totals: Dict[str, int] = defaultdict(int)
  totals["month_periods_total"] = len(months)
  totals["year_periods_total"] = len(years)
  tasks: List[Tuple[str, str]] = [
      (_PUBLIC_EF_KIND_MONTH, ym) for ym in months
  ] + [
      (_PUBLIC_EF_KIND_YEAR, ys) for ys in years
  ]
  totals["tasks_total"] = len(tasks)
  if not tasks:
    totals["tasks_completed"] = 0
    totals["pending_tasks"] = 0
    totals["degraded"] = 0
    _prune_orphan_public_ef_rows(months, years)
    return dict(totals)

  iterator = pool.imap_unordered(_public_ef_period_worker, tasks, chunksize=1)
  next_with_timeout = getattr(iterator, "next", None)
  completed = 0
  last_progress_at = time.monotonic()
  while completed < len(tasks):
    try:
      if callable(next_with_timeout):
        piece = next_with_timeout(timeout=max(0.0, float(poll_timeout_s)))
      else:
        piece = next(iterator)
    except StopIteration:
      break
    except MultiprocessingTimeoutError:
      stalled_for_s = max(0.0, time.monotonic() - last_progress_at)
      if callable(progress_callback):
        progress_callback({
            "tasks_total": len(tasks),
            "tasks_completed": completed,
            "pending_tasks": max(0, len(tasks) - completed),
            "stalled_for_s": stalled_for_s,
        })
      if stalled_for_s >= max(float(poll_timeout_s), float(no_progress_timeout_s)):
        totals["watchdog_timeouts"] += 1
        break
      continue
    completed += 1
    last_progress_at = time.monotonic()
    for k, v in piece.items():
      totals[k] += int(v)

  totals["tasks_completed"] = completed
  totals["pending_tasks"] = max(0, len(tasks) - completed)
  totals["degraded"] = int(
      totals.get("worker_exceptions", 0) > 0
      or totals.get("watchdog_timeouts", 0) > 0
      or totals["pending_tasks"] > 0
  )

  _public_ef_parent_refresh_connections_after_forked_children()
  _prune_orphan_public_ef_rows(months, years)
  return dict(totals)


def refresh_public_expansion_factor_artifacts() -> Dict[str, int]:
  """Recompute stale monthly/yearly expansion-factor histogram artifacts (sequential)."""
  months = _month_keys_present()
  years = _year_keys_present()
  totals: Dict[str, int] = defaultdict(int)
  totals["month_periods_total"] = len(months)
  totals["year_periods_total"] = len(years)

  for ym in months:
    for k, v in _sync_reconcile_public_ef_month(ym).items():
      totals[k] += int(v)
  for ys in years:
    for k, v in _sync_reconcile_public_ef_year(ys).items():
      totals[k] += int(v)

  _prune_orphan_public_ef_rows(months, years)
  return dict(totals)


def refresh_public_expansion_factor_artifacts_safe() -> None:
  try:
    stats = refresh_public_expansion_factor_artifacts()
    logger.info("public metrics artifacts refreshed: %s", stats)
  except Exception:
    logger.exception("public metrics artifact refresh failed")


def assemble_public_monthly_metrics_bundle() -> Dict[str, Any]:
  """Merge persisted artifacts into one JSON-safe bundle for the public API.

  Omits periods with ``rebuild_required`` so stale histograms are not served
  after invalidation until the scheduler recomputes them.
  """
  monthly_histograms: Dict[str, Any] = {}
  yearly_histograms: Dict[str, Any] = {}
  for row in public_metrics_artifact.objects.filter(
      scope=PUBLIC_EF_MONTH_DAILY, rebuild_required=False
  ).order_by("period_key"):
    monthly_histograms[row.period_key] = decompress_public_payload(row)
  for row in public_metrics_artifact.objects.filter(
      scope=PUBLIC_EF_YEAR_WEEKLY, rebuild_required=False
  ).order_by("period_key"):
    yearly_histograms[row.period_key] = decompress_public_payload(row)
  ready = bool(monthly_histograms or yearly_histograms)
  return {
      "status": "ready" if ready else "loading",
      "detail": None if ready else "dashboard_metrics_not_ready",
      "retry_hint": None if ready else "retry_after_pipeline_refresh",
      "schema_version": APP_PUBLIC_METRICS_SCHEMA_VERSION,
      "sections": {
          "expansion_factor": {
              "monthly_daily_histograms": monthly_histograms,
              "yearly_weekly_histograms": yearly_histograms,
          },
      },
  }


def invalidate_public_metrics_artifacts_for_jids(jids: Iterable[str]) -> None:
  """Mark EF aggregates stale for calendar periods touched by the given accounting rows."""
  jid_list = [j for j in jids if j]
  if not jid_list:
    return
  months: set[str] = set()
  years: set[str] = set()
  for et in job_data.objects.filter(jid__in=jid_list).values_list("end_time", flat=True):
    if et is None:
      continue
    months.add(f"{et.year:04d}-{et.month:02d}")
    years.add(str(et.year))
  try:
    if months:
      public_metrics_artifact.objects.filter(
          scope=PUBLIC_EF_MONTH_DAILY,
          period_key__in=sorted(months),
      ).update(rebuild_required=True)
    if years:
      public_metrics_artifact.objects.filter(
          scope=PUBLIC_EF_YEAR_WEEKLY,
          period_key__in=sorted(years),
      ).update(rebuild_required=True)
  except Exception:
    logger.exception("failed to mark public_metrics_artifact rows stale for jids")


def invalidate_all_public_metrics_artifacts() -> None:
  """Mark every prewarmed public dashboard artifact row for rebuild."""
  try:
    public_metrics_artifact.objects.all().update(rebuild_required=True)
  except Exception:
    logger.exception("failed to mark public_metrics_artifact rows stale")
