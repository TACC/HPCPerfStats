"""
Prewarmed gzip-compressed public dashboard payloads (expansion-factor
aggregates).

Computed only from ``update_metrics`` scheduler passes — HTTP handlers must not
reaggregate heavy ranges here.

Rows are **not** deleted when inputs change: invalidation sets
``rebuild_required`` so the bundle omits those periods until
:func:`refresh_public_expansion_factor_artifacts` (or the parallel scheduler
path) recomputes and clears the flag.

The scheduler runs :func:`refresh_public_expansion_factor_artifacts_parallel` on
the metrics in-process thread pool (one calendar month or one calendar year per
task) and **completes** that pass before starting job-based metrics.

Attributes:
  APP_PUBLIC_METRICS_SCHEMA_VERSION: Attribute.
  EF_HIST_BIN_EDGES: Attribute.
  HOST_PLOT_MAX_WINDOW_DAYS: Attribute.
  PAYLOAD_ENCODING_GZIP_JSON: Attribute.
  PUBLIC_EF_MONTH_DAILY: Attribute.
  PUBLIC_EF_YEAR_WEEKLY: Attribute.
  _PUBLIC_EF_KIND_MONTH: Attribute.
  _PUBLIC_EF_KIND_YEAR: Attribute.
  _PUBLIC_METRICS_INVALIDATE_LOCK_TIMEOUT_MS: Attribute.
  _PUBLIC_METRICS_INVALIDATE_STATEMENT_TIMEOUT_MS: Attribute.
  logger: Attribute.
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
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from django.db import connection, transaction
from django.db.models import F, Max, Min, Q
from django.db.utils import OperationalError
from django.utils import timezone as dj_tz

from hpcperfstats.site.lib.machine.models import job_data, public_metrics_artifact

logger = logging.getLogger(__name__)

APP_PUBLIC_METRICS_SCHEMA_VERSION = 2

PAYLOAD_ENCODING_GZIP_JSON = "gzip_json"

# Expansion-factor aggregates derived from scheduler timestamps only (see Carlson EF definitions).
PUBLIC_EF_MONTH_DAILY = "ef_month_daily"
PUBLIC_EF_YEAR_WEEKLY = "ef_year_weekly"

# Best-effort invalidate under concurrent refresh (row locks on large payloads).
# Fail fast vs waiting for the connection's full statement_timeout.
_PUBLIC_METRICS_INVALIDATE_LOCK_TIMEOUT_MS = 2000
_PUBLIC_METRICS_INVALIDATE_STATEMENT_TIMEOUT_MS = 5000

# Task kind strings for :func:`_public_ef_period_worker`.
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
  """
  Return EF as (queue_wait + runtime) / (ncores * runtime); ``None`` when.
  
    invalid.
  
  Args:
    submit_time (Optional[datetime]): Submit time, or None when absent.
    start_time (Optional[datetime]): Start time, or None when absent.
    runtime_seconds (Optional[float]): Runtime seconds, or None when absent.
    ncores (Optional[int]): Ncores, or None when absent.
  
  Returns:
    Optional[float]: Optional[float] — the result, or None when unavailable.
  
  Examples:
    >>> compute_scheduler_expansion_factor_seconds(None, None, None, None)
  """
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
  """
  Internal helper to handle histogram counts.
  
  Args:
    values (Sequence[float]): Sequence for values.
  
  Returns:
    Tuple[List[float], List[int]]: Tuple[List[float], List[int]] produced by
    this call.
  
  Examples:
    >>> _histogram_counts([])  # doctest: +SKIP
  """
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
  """
  Internal helper to handle eligible jobs filter.
  
  Returns:
    Q: Q produced by this call.
  
  Examples:
    >>> _eligible_jobs_filter()  # doctest: +SKIP
  """
  return Q(runtime__gt=0, ncores__gt=0, start_time__gte=F("submit_time"))


def _iter_queryset_rows(qs: Any, *, chunk_size: int = 2048) -> Iterator[Any]:
  """
  Iterate ``qs`` without server-side cursors when Django tests forbid them.
  
  Args:
    qs (Any): Qs passed to this helper.
    chunk_size (int): Integer value for chunk size.
  
  Yields:
    Iterator[Any]: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _iter_queryset_rows(None, 0)  # doctest: +SKIP
  """
  from django.test.testcases import DatabaseOperationForbidden

  try:
    row_iter = qs.iterator(chunk_size=chunk_size)
  except DatabaseOperationForbidden:
    yield from qs
    return
  try:
    yield from row_iter
  except DatabaseOperationForbidden:
    yield from qs


def _streaming_jid_epoch_fingerprint(prefix: str, qs: Any) -> str:
  """
  Stable fingerprint over sorted ``(jid, end_time)`` pairs.
  
  Args:
    prefix (str): String for prefix.
    qs (Any): Qs passed to this helper.
  
  Returns:
    str: str produced by this call.
  
  Examples:
    >>> _streaming_jid_epoch_fingerprint("x", None)  # doctest: +SKIP
  """
  h = hashlib.sha256()
  h.update(str(APP_PUBLIC_METRICS_SCHEMA_VERSION).encode())
  h.update(b"|")
  h.update(prefix.encode())
  h.update(b"|")
  # Avoid QuerySet.iterator(): Django 6 test clients forbid server-side chunked_cursor.
  for jid, et in qs.order_by("jid").values_list("jid", "end_time"):
    h.update(str(jid).encode())
    h.update(b"|")
    if et is None:
      h.update(b"none")
    else:
      h.update(str(et.timestamp()).encode())
    h.update(b"\n")
  return h.hexdigest()[:64]


def _period_month_bounds(year_month: str) -> Tuple[datetime, datetime]:
  """
  Internal helper to handle period month bounds.
  
  Args:
    year_month (str): String for year month.
  
  Returns:
    Tuple[datetime, datetime]: Tuple[datetime, datetime] produced by this
    call.
  
  Examples:
    >>> _period_month_bounds("x")  # doctest: +SKIP
  """
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
  """
  Internal helper to handle period year bounds.
  
  Args:
    year (int): Integer value for year.
  
  Returns:
    Tuple[datetime, datetime]: Tuple[datetime, datetime] produced by this
    call.
  
  Examples:
    >>> _period_year_bounds(0)  # doctest: +SKIP
  """
  start = datetime(year, 1, 1, tzinfo=dj_tz.utc)
  end = datetime(year + 1, 1, 1, tzinfo=dj_tz.utc)
  return start, end


def _month_keys_present() -> List[str]:
  """
  Internal helper to check if the month keys is present.
  
  Returns:
    List[str]: List[str] produced by this call.
  
  Examples:
    >>> _month_keys_present()  # doctest: +SKIP
  """
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
  """
  Internal helper to check if the year keys is present.
  
  Returns:
    List[str]: List[str] produced by this call.
  
  Examples:
    >>> _year_keys_present()  # doctest: +SKIP
  """
  qs = job_data.objects.filter(_eligible_jobs_filter()).aggregate(
      mn=Min("end_time"),
      mx=Max("end_time"),
  )
  mn, mx = qs.get("mn"), qs.get("mx")
  if mn is None or mx is None:
    return []
  return [str(y) for y in range(mn.year, mx.year + 1)]


def _payload_from_daily_means(daily_means: List[float]) -> Dict[str, Any]:
  """
  Internal helper to handle payload from daily means.
  
  Args:
    daily_means (List[float]): Sequence for daily means.
  
  Returns:
    Dict[str, Any]: Dict[str, Any] produced by this call.
  
  Examples:
    >>> _payload_from_daily_means([])  # doctest: +SKIP
  """
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
  """
  Internal helper to handle payload from weekly means.
  
  Args:
    weekly_means (List[float]): Sequence for weekly means.
  
  Returns:
    Dict[str, Any]: Dict[str, Any] produced by this call.
  
  Examples:
    >>> _payload_from_weekly_means([])  # doctest: +SKIP
  """
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
  """
  Internal helper to handle attach ef histogram bokeh item.
  
  Args:
    payload (Dict[str, Any]): Mapping for payload.
    period_key (str): String for period key.
    subtitle (str): String for subtitle.
  
  Returns:
    None
  
  Examples:
    >>> _attach_ef_histogram_bokeh_item({}, "x", "x")  # doctest: +SKIP
  """
  edges = payload.get("histogram_bin_edges")
  counts = payload.get("histogram_counts")
  if not isinstance(edges, list) or not isinstance(counts, list):
    return
  from hpcperfstats.site.lib.machine.public_metrics_bokeh import (
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
  """
  Internal helper to build the month daily payload.
  
  Args:
    year_month (str): String for year month.
  
  Returns:
    Dict[str, Any]: Dict[str, Any] produced by this call.
  
  Examples:
    >>> _build_month_daily_payload("x")  # doctest: +SKIP
  """
  start, end = _period_month_bounds(year_month)
  qs = (
      job_data.objects.filter(_eligible_jobs_filter())
      .filter(end_time__gte=start, end_time__lt=end)
      .only("jid", "submit_time", "start_time", "runtime", "ncores", "end_time")
  )
  day_sum: Dict[date, float] = defaultdict(float)
  day_cnt: Dict[date, int] = defaultdict(int)
  for row in _iter_queryset_rows(qs):
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
  """
  Internal helper to build the year weekly payload.
  
  Args:
    year_str (str): String for year str.
  
  Returns:
    Dict[str, Any]: Dict[str, Any] produced by this call.
  
  Examples:
    >>> _build_year_weekly_payload("x")  # doctest: +SKIP
  """
  year = int(year_str)
  start, end = _period_year_bounds(year)
  qs = (
      job_data.objects.filter(_eligible_jobs_filter())
      .filter(end_time__gte=start, end_time__lt=end)
      .only("jid", "submit_time", "start_time", "runtime", "ncores", "end_time")
  )
  week_sum: Dict[Tuple[int, int], float] = defaultdict(float)
  week_cnt: Dict[Tuple[int, int], int] = defaultdict(int)
  for row in _iter_queryset_rows(qs):
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


def _upsert_row(
  scope: str,
  period_key: str,
  fingerprint: str,
  payload: Dict[str, Any],
) -> None:
  """
  Internal helper to handle upsert row.
  
  Args:
    scope (str): String for scope.
    period_key (str): String for period key.
    fingerprint (str): String for fingerprint.
    payload (Dict[str, Any]): Mapping for payload.
  
  Returns:
    None
  
  Examples:
    >>> _upsert_row("x", "x", "x", {})  # doctest: +SKIP
  """
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
  """
  Decompress public payload.
  
  Args:
    row (public_metrics_artifact): Row.
  
  Returns:
    Dict[str, Any]: Dict[str, Any] produced by this call.
  
  Raises:
    ValueError: Raised when ``decompress_public_payload`` hits a
    ``ValueError`` failure path.
  
  Examples:
    >>> decompress_public_payload(None)  # doctest: +SKIP
  """
  if row.payload_encoding != PAYLOAD_ENCODING_GZIP_JSON:
    raise ValueError("unsupported encoding")
  raw = gzip.decompress(bytes(row.payload_compressed))
  return json.loads(raw.decode("utf-8"))


def _prune_orphan_public_ef_rows(
  months: Sequence[str],
  years: Sequence[str],
) -> None:
  """
  Remove persisted rows for periods with no backing job_data (retention /.
  
    deletes).
  
  Args:
    months (Sequence[str]): Sequence for months.
    years (Sequence[str]): Sequence for years.
  
  Returns:
    None
  
  Examples:
    >>> _prune_orphan_public_ef_rows([], [])  # doctest: +SKIP
  """
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
  """
  Fingerprint check + optional rebuild for one ``YYYY-MM`` period (any process).
  
  Args:
    ym (str): String for ym.
  
  Returns:
    Dict[str, int]: Dict[str, int] produced by this call.
  
  Examples:
    >>> _sync_reconcile_public_ef_month("x")  # doctest: +SKIP
  """
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
  """
  Fingerprint check + optional rebuild for one calendar year period (any.
  
    process).
  
  Args:
    ys (str): String for ys.
  
  Returns:
    Dict[str, int]: Dict[str, int] produced by this call.
  
  Examples:
    >>> _sync_reconcile_public_ef_year("x")  # doctest: +SKIP
  """
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


def _public_ef_period_worker(task: Tuple[str, str]) -> Dict[str, int]:
  """
  Reconcile exactly one month or year period in a metrics thread.
  
  Args:
    task (Tuple[str, str]): Sequence for task.
  
  Returns:
    Dict[str, int]: Dict[str, int] produced by this call.
  
  Examples:
    >>> _public_ef_period_worker([])  # doctest: +SKIP
  """
  from hpcperfstats.dbload.lib.django_bootstrap import ensure_django

  ensure_django()
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
  progress_callback: Any | None = None,
) -> Dict[str, int]:
  """
  Recompute stale month/year EF rows using ``pool`` (one period per task).
  
  Callers typically pass ``Metrics.ensure_pool()`` from ``update_metrics`` so
  /pub aggregates finish before the same pool runs per-job metrics.
  
  Args:
    pool (Any): Live handle (pool, client, or connection).
    poll_timeout_s (float): Floating-point value for poll timeout s.
    no_progress_timeout_s (float): Floating-point value for no progress
    timeout s.
    progress_callback (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Dict[str, int]: Dict[str, int] produced by this call.
  
  Examples:
    >>> refresh_public_expansion_factor_artifacts_parallel(None, 0, 0, None)
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
  iterator_close = getattr(iterator, "close", None)
  completed = 0
  last_progress_at = time.monotonic()
  while completed < len(tasks):
    try:
      if callable(next_with_timeout):
        piece = next_with_timeout(timeout=max(0.0, float(poll_timeout_s)))
      else:
        piece = next(iterator)
    except StopIteration:
      if callable(iterator_close):
        iterator_close()
      break
    except TimeoutError:
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
        if callable(iterator_close):
          iterator_close()
        break
      continue
    completed += 1
    last_progress_at = time.monotonic()
    for k, v in piece.items():
      totals[k] += int(v)

  if callable(iterator_close):
    iterator_close()
  totals["tasks_completed"] = completed
  totals["pending_tasks"] = max(0, len(tasks) - completed)
  totals["degraded"] = int(
      totals.get("worker_exceptions", 0) > 0
      or totals.get("watchdog_timeouts", 0) > 0
      or totals["pending_tasks"] > 0
  )

  _prune_orphan_public_ef_rows(months, years)
  return dict(totals)


def refresh_public_expansion_factor_artifacts() -> Dict[str, int]:
  """
  Recompute stale monthly/yearly expansion-factor histogram artifacts.
  
    (sequential).
  
  Returns:
    Dict[str, int]: Dict[str, int] produced by this call.
  
  Examples:
    >>> refresh_public_expansion_factor_artifacts()  # doctest: +SKIP
  """
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
  """
  Refresh public expansion factor artifacts safe.
  
  Returns:
    None
  
  Examples:
    >>> refresh_public_expansion_factor_artifacts_safe()  # doctest: +SKIP
  """
  try:
    stats = refresh_public_expansion_factor_artifacts()
    logger.info("public metrics artifacts refreshed: %s", stats)
  except Exception:
    logger.exception("public metrics artifact refresh failed")


HOST_PLOT_MAX_WINDOW_DAYS = 7


def assemble_public_dashboard_meta_bundle() -> Dict[str, Any]:
  """
  Return dashboard status and period keys without decompressing histogram.
  
    payloads.
  
  Returns:
    Dict[str, Any]: Dict[str, Any] produced by this call.
  
  Examples:
    >>> assemble_public_dashboard_meta_bundle()  # doctest: +SKIP
  """
  monthly_keys = list(
      public_metrics_artifact.objects.filter(
          scope=PUBLIC_EF_MONTH_DAILY, rebuild_required=False
      )
      .order_by("period_key")
      .values_list("period_key", flat=True)
  )
  yearly_keys = list(
      public_metrics_artifact.objects.filter(
          scope=PUBLIC_EF_YEAR_WEEKLY, rebuild_required=False
      )
      .order_by("period_key")
      .values_list("period_key", flat=True)
  )
  ready = bool(monthly_keys or yearly_keys)
  return {
      "status": "ready" if ready else "loading",
      "detail": None if ready else "dashboard_metrics_not_ready",
      "retry_hint": None if ready else "retry_after_pipeline_refresh",
      "schema_version": APP_PUBLIC_METRICS_SCHEMA_VERSION,
      "sections": {
          "expansion_factor": {
              "monthly_period_keys": monthly_keys,
              "yearly_period_keys": yearly_keys,
          },
      },
  }


def load_public_expansion_factor_period(
  grouping: str,
  period_key: str,
) -> Optional[Dict[str, Any]]:
  """
  Load one expansion-factor histogram block for ``grouping`` (monthly|yearly).
  
  Args:
    grouping (str): String for grouping.
    period_key (str): String for period key.
  
  Returns:
    Optional[Dict[str, Any]]: Optional[Dict[str, Any]] — the result, or None
    when unavailable.
  
  Examples:
    >>> load_public_expansion_factor_period("x", "x")  # doctest: +SKIP
  """
  grouping_norm = (grouping or "").strip().lower()
  if grouping_norm == "monthly":
    scope = PUBLIC_EF_MONTH_DAILY
  elif grouping_norm == "yearly":
    scope = PUBLIC_EF_YEAR_WEEKLY
  else:
    return None
  period = (period_key or "").strip()
  if not period:
    return None
  row = (
      public_metrics_artifact.objects.filter(
          scope=scope,
          period_key=period,
          rebuild_required=False,
      )
      .first()
  )
  if row is None:
    return None
  return decompress_public_payload(row)


def assemble_public_monthly_metrics_bundle() -> Dict[str, Any]:
  """
  Merge persisted artifacts into one JSON-safe bundle for the public API.
  
  Omits periods with ``rebuild_required`` so stale histograms are not served
  after invalidation until the scheduler recomputes them.
  
  Returns:
    Dict[str, Any]: Dict[str, Any] produced by this call.
  
  Examples:
    >>> assemble_public_monthly_metrics_bundle()  # doctest: +SKIP
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
  """
  Mark EF aggregates stale for calendar periods touched by the given accounting.
  
    rows.
  
  Updates one primary key at a time under a short PostgreSQL ``lock_timeout`` so
  concurrent ``update_or_create`` / refresh holds do not wait until the session
  ``statement_timeout`` (which previously logged ERROR with a full traceback).
  Locked or timed-out rows are skipped with a warning; other periods still
    update.
  
  Args:
    jids (Iterable[str]): Jids.
  
  Returns:
    None
  
  Examples:
    >>> invalidate_public_metrics_artifacts_for_jids(None)  # doctest: +SKIP
  """
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
      _mark_public_metrics_rebuild_required(
          PUBLIC_EF_MONTH_DAILY, sorted(months),
      )
    if years:
      _mark_public_metrics_rebuild_required(
          PUBLIC_EF_YEAR_WEEKLY, sorted(years),
      )
  except Exception:
    logger.exception("failed to mark public_metrics_artifact rows stale for jids")


def invalidate_all_public_metrics_artifacts() -> None:
  """
  Mark every prewarmed public dashboard artifact row for rebuild.
  
  Returns:
    None
  
  Examples:
    >>> invalidate_all_public_metrics_artifacts()  # doctest: +SKIP
  """
  try:
    pks = list(
        public_metrics_artifact.objects.filter(rebuild_required=False).values_list(
            "pk", flat=True,
        )
    )
    _mark_public_metrics_rebuild_required_by_pks(pks)
  except Exception:
    logger.exception("failed to mark public_metrics_artifact rows stale")


def _mark_public_metrics_rebuild_required(
  scope: str,
  period_keys: Sequence[str],
) -> int:
  """
  Mark matching non-stale rows ``rebuild_required``; return rows updated.
  
  Args:
    scope (str): String for scope.
    period_keys (Sequence[str]): Sequence for period keys.
  
  Returns:
    int: int produced by this call.
  
  Examples:
    >>> _mark_public_metrics_rebuild_required("x", [])  # doctest: +SKIP
  """
  if not period_keys:
    return 0
  pks = list(
      public_metrics_artifact.objects.filter(
          scope=scope,
          period_key__in=list(period_keys),
          rebuild_required=False,
      ).values_list("pk", flat=True)
  )
  return _mark_public_metrics_rebuild_required_by_pks(pks)


def _mark_public_metrics_rebuild_required_by_pks(pks: Sequence[int]) -> int:
  """
  Per-pk rebuild flag updates with short lock/statement timeouts (Postgres).
  
  Args:
    pks (Sequence[int]): Sequence for pks.
  
  Returns:
    int: int produced by this call.
  
  Examples:
    >>> _mark_public_metrics_rebuild_required_by_pks([])  # doctest: +SKIP
  """
  updated_n = 0
  for pk in pks:
    try:
      if _update_public_metrics_rebuild_required_one(int(pk)):
        updated_n += 1
    except OperationalError as exc:
      # Expected under concurrent refresh holding the row; do not ERROR+traceback.
      logger.warning(
          "public_metrics_artifact rebuild mark skipped pk=%s: %s",
          pk,
          exc,
      )
  return updated_n


def _update_public_metrics_rebuild_required_one(pk: int) -> bool:
  """
  Return True when the row was marked ``rebuild_required``.
  
  Args:
    pk (int): Integer value for pk.
  
  Returns:
    bool: True or False for this check.
  
  Raises:
    Exception: Raised when ``_update_public_metrics_rebuild_required_one``
    hits a ``Exception`` failure path.
  
  Examples:
    >>> _update_public_metrics_rebuild_required_one(0)  # doctest: +SKIP
  """
  using = getattr(connection, "alias", None) or "default"
  with transaction.atomic(using=using):
    if connection.vendor == "postgresql":
      try:
        with connection.cursor() as cursor:
          cursor.execute(
              "SET LOCAL lock_timeout = %s",
              [_PUBLIC_METRICS_INVALIDATE_LOCK_TIMEOUT_MS],
          )
          cursor.execute(
              "SET LOCAL statement_timeout = %s",
              [_PUBLIC_METRICS_INVALIDATE_STATEMENT_TIMEOUT_MS],
          )
      except Exception as exc:
        from django.test.testcases import DatabaseOperationForbidden

        if not isinstance(exc, DatabaseOperationForbidden):
          raise
    n = public_metrics_artifact.objects.filter(
        pk=pk, rebuild_required=False,
    ).update(rebuild_required=True)
  return int(n or 0) > 0
