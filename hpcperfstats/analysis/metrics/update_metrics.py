#!/usr/bin/env python
"""Update metrics_data for jobs ending on each day in a date range.

Filters by runtime, optionally skips jobs that already have a full metrics
catalog (one row per metric with either a numeric value or no_data_reason),
runs Metrics().run(jobs_list). With no CLI date arguments, processes the last
seven calendar days through today.

Processing order: **newest calendar day first**, and within each day **newest job
first** (``end_time`` descending, then ``jid`` descending as a stable tiebreaker).

"""
import contextlib
import functools
import gc
import os
import threading
import signal
import sys
import time
from types import SimpleNamespace
from datetime import datetime, timedelta
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from hpcperfstats.django_bootstrap import ensure_django
ensure_django()

from django.db import close_old_connections, connections, transaction
from django.db.models import BooleanField, Case, Count, Exists, F, IntegerField, Max, OuterRef, Q, Subquery, Value, When
from django.db.models.functions import Coalesce
from django.db.utils import OperationalError, DatabaseError

import hpcperfstats.conf_parser as cfg
from hpcperfstats.analysis.metrics import metrics
from hpcperfstats.analysis.metrics.live_host_sample_count import (
    live_distinct_host_time_count_expression,
)
from hpcperfstats.analysis.metrics.metrics import expected_job_metric_row_count
from hpcperfstats.analysis.metrics.db_retry import run_with_db_retry
from hpcperfstats.print_utils import log_print
from hpcperfstats.dbload.date_utils import log_date_range, parse_start_end_dates
from hpcperfstats.shutdown_utils import (
    shutdown_requested,
    send_sigchld_to_parent,
    sleep_until_shutdown,
)
from hpcperfstats.site.machine.job_plot_artifacts import persist_job_plot_artifacts_for_jid
from hpcperfstats.site.machine.models import host_data, job_data, metrics_data

DEBUG = cfg.get_debug()

# Process jobs in chunks to bound memory; full job rows are not all held at once.
CHUNK_SIZE = 500

# host_data "latest sample per host" probes: keep each round-trip bounded.
# PostgreSQL uses a per-host LATERAL + LIMIT 1 (index probe on (host, time))
# inside a short transaction with parallel workers disabled for the probe.
HOST_LAST_TIME_LOOKUP_BATCH = 64
READINESS_QUERY_TIMEOUT_MS = 120000

# Running a full GC every chunk is expensive on large backfills; amortize it.
GC_COLLECT_EVERY_N_CHUNKS = 20

# When argv has no start/end dates, process this many calendar days ending today.
DEFAULT_METRICS_RANGE_DAYS = 7
GLOBAL_SCHEDULER_BATCH_SIZE = 256
READINESS_PROBE_TARGET_MIN = 64
READINESS_PROBE_TARGET_STEP = 64
READINESS_PROBE_TARGET_FAST_SUCCESS_S = 0.35


class _PhaseTimer:
  """Collect per-phase wall-clock timings for pipeline reporting."""

  def __init__(self):
    self._totals = {
        "candidate_sql_s": 0.0,
        "readiness_s": 0.0,
        "metrics_compute_s": 0.0,
        "prewarm_s": 0.0,
    }

  @contextlib.contextmanager
  def phase(self, key):
    t0 = time.monotonic()
    try:
      yield
    finally:
      self._totals[key] = self._totals.get(key, 0.0) + (
          time.monotonic() - t0
      )

  def totals(self):
    return dict(self._totals)


class _PrewarmPipeline:
  """Required prewarm stage with bounded backlog and retries."""

  def __init__(self):
    self._mode = cfg.get_metrics_plot_prewarm_mode()
    self._workers = cfg.get_metrics_prewarm_workers()
    self._attempts = cfg.get_metrics_prewarm_retry_attempts()
    self._executor = None
    self._pending = set()
    self._done = 0
    self._failed = 0
    self._lag_samples = []
    self._created_at = {}
    if self._mode == "pipeline_required":
      self._executor = ThreadPoolExecutor(max_workers=self._workers)

  def _run_one(self, jid):
    last_exc = None
    for _ in range(max(1, self._attempts)):
      try:
        close_old_connections()
        persist_job_plot_artifacts_for_jid(jid)
        return
      except Exception as exc:
        last_exc = exc
    raise last_exc

  def submit(self, jid):
    if self._mode == "inline":
      self._run_one(jid)
      self._done += 1
      return
    fut = self._executor.submit(self._run_one, jid)
    self._created_at[fut] = time.monotonic()
    self._pending.add(fut)

  def drain_some(self, force=False):
    if self._mode == "inline" or not self._pending:
      return
    if force:
      done = set(self._pending)
      pending = set()
    else:
      done, pending = wait(
          self._pending,
          timeout=0,
          return_when=FIRST_COMPLETED,
      )
    self._pending = pending
    for fut in done:
      start = self._created_at.pop(fut, None)
      if start is not None:
        self._lag_samples.append(time.monotonic() - start)
      try:
        fut.result()
        self._done += 1
      except Exception as exc:
        self._failed += 1
        log_print("plot artifact prewarm failed: {0}".format(exc))

  def finish(self):
    if self._mode == "inline":
      return
    while self._pending:
      self.drain_some(force=True)
    self._executor.shutdown(wait=True)

  def stats(self):
    total = self._done + self._failed
    p95_lag = 0.0
    if self._lag_samples:
      vals = sorted(self._lag_samples)
      p95_lag = vals[max(0, int(len(vals) * 0.95) - 1)]
    return {
        "prewarm_backlog_jobs": len(self._pending),
        "prewarm_lag_seconds_p95": round(p95_lag, 3),
        "prewarm_success_ratio": (float(self._done) / float(total)) if total else 1.0,
        "prewarm_done_jobs": self._done,
        "prewarm_failed_jobs": self._failed,
    }


class _CompletionReporter:
  """Background heartbeat reporter for recent completion throughput."""

  def __init__(self, report_interval_s=3600, window_s=3600):
    self._report_interval_s = max(5, int(report_interval_s))
    self._window_s = max(60, int(window_s))
    self._lock = threading.Lock()
    self._completed_events = deque()
    self._readiness_error_events = deque()
    self._completed_total = 0
    self._readiness_error_total = 0
    self._stop = threading.Event()
    self._thread = None

  def start(self):
    if self._thread is not None:
      return
    self._thread = threading.Thread(
        target=self._run,
        name="metrics-completion-reporter",
        daemon=True,
    )
    self._thread.start()

  def stop(self):
    self._stop.set()
    if self._thread is not None:
      self._thread.join(timeout=self._report_interval_s + 1)

  def _prune_locked(self, now):
    cutoff = now - self._window_s
    while self._completed_events and self._completed_events[0][0] < cutoff:
      self._completed_events.popleft()
    while (
        self._readiness_error_events
        and self._readiness_error_events[0][0] < cutoff
    ):
      self._readiness_error_events.popleft()

  def record_completed(self, count):
    if count <= 0:
      return
    now = time.monotonic()
    with self._lock:
      self._completed_events.append((now, int(count)))
      self._completed_total += int(count)
      self._prune_locked(now)

  def completed_in_window(self):
    now = time.monotonic()
    with self._lock:
      self._prune_locked(now)
      return sum(c for _, c in self._completed_events)

  def record_readiness_error_chunk(self, count=1):
    if count <= 0:
      return
    now = time.monotonic()
    with self._lock:
      self._readiness_error_events.append((now, int(count)))
      self._readiness_error_total += int(count)
      self._prune_locked(now)

  def readiness_errors_in_window(self):
    now = time.monotonic()
    with self._lock:
      self._prune_locked(now)
      return sum(c for _, c in self._readiness_error_events)

  def completed_total(self):
    with self._lock:
      return self._completed_total

  def readiness_errors_total(self):
    with self._lock:
      return self._readiness_error_total

  def _run(self):
    while not self._stop.wait(self._report_interval_s):
      log_print(
          "metrics progress: completed_last_hour={0} processed_total={1} "
          "readiness_error_chunks_last_hour={2} readiness_error_chunks_total={3}".format(
              self.completed_in_window(),
              self.completed_total(),
              self.readiness_errors_in_window(),
              self.readiness_errors_total(),
          ),
          flush=True,
      )


@contextlib.contextmanager
def _pg_session_statement_timeout_for_metrics_batch():
  """Temporarily disable PostgreSQL ``statement_timeout`` for long metrics batch queries.

  ``_jobs_queryset`` annotated scans (metrics subqueries + live distinct-time counts)
  can exceed the default session ``statement_timeout`` (often 2 minutes). Keyset
  pagination must not fall back to offset slicing on timeout — that repeats the
  same expensive SQL. Restore the configured timeout when the block exits.
  """
  conn = connections["default"]
  if conn.vendor != "postgresql":
    yield
    return
  restore_ms = cfg.get_db_statement_timeout_ms()
  with conn.cursor() as cursor:
    cursor.execute("SET statement_timeout = 0")
  try:
    yield
  finally:
    with conn.cursor() as cursor:
      if restore_ms > 0:
        cursor.execute("SET statement_timeout = %s", [restore_ms])
      else:
        cursor.execute("SET statement_timeout = 0")


def _today_datetime():
  """Local now for default date-range bounds (monkeypatch in tests)."""
  return datetime.today()


def _default_metrics_date_range():
  """Return (start, end) datetimes at local midnight: inclusive last N days through today."""
  end = datetime.combine(_today_datetime().date(), datetime.min.time())
  start = end - timedelta(days=DEFAULT_METRICS_RANGE_DAYS - 1)
  return start, end


def _shutdown_db_best_effort():
  """Close DB connections without failing shutdown."""
  try:
    close_old_connections()
    connections.close_all()
  except Exception:
    pass


def _install_sigterm_handler(exit_code=143):
  """Install SIGTERM handler that requests shutdown.

  Important: do not raise `SystemExit` from inside the signal handler. Raising
  exceptions from signal delivery can interrupt GC/finalizers and lead to noisy
  "Exception ignored in: ..." tracebacks during interpreter teardown.
  """
  sigterm_received = [False]

  previous_handler = signal.getsignal(signal.SIGTERM)

  def _sigterm_handler(signum, frame):
    sigterm_received[0] = True
    shutdown_requested[0] = True
    # Request a graceful stop; the main loops check `shutdown_requested`.
    _ = exit_code  # Keep closure stable if we later surface the exit code.

  signal.signal(signal.SIGTERM, _sigterm_handler)
  return previous_handler, sigterm_received, _sigterm_handler


def _notify_parent_if_sigterm(sigterm_received):
  """Send SIGCHLD back to parent when SIGTERM triggered."""
  if sigterm_received and sigterm_received[0]:
    send_sigchld_to_parent()


@functools.lru_cache(maxsize=1)
def _expected_job_metrics_row_count():
  """Catalog row count; metrics definitions are fixed for the process lifetime."""
  return expected_job_metric_row_count()


@functools.lru_cache(maxsize=1)
def _host_name_suffix():
  """FQDN suffix used by host_data host names."""
  return "." + cfg.get_host_name_ext()


def _jobs_queryset(date, min_time, rerun):
  """Jobs ending on ``date`` with runtime >= min_time, newest first (end_time, jid)."""
  qs = job_data.objects.filter(end_time__date=date.date()).exclude(
      runtime__lt=min_time)
  if rerun:
    return qs.order_by("-end_time", "-jid")
  # Jobs need a metrics pass if row count is below the full catalog, or any
  # row still has null value without an explicit no_data_reason (legacy / stuck).
  # Use scalar subqueries on metrics_data (jid_id index) instead of joining
  # metrics_data twice via reverse Count(), which avoids row multiplication.
  expected = _expected_job_metrics_row_count()
  stale_q = Q(value__isnull=True) & (
      Q(no_data_reason__isnull=True) | Q(no_data_reason="")
  )
  int0 = Value(0, output_field=IntegerField())
  md_total_sq = Subquery(
      metrics_data.objects.filter(jid_id=OuterRef("jid"))
      .values("jid_id")
      .annotate(c=Count("id"))
      .values("c")[:1],
      output_field=IntegerField(),
  )
  stale_count_sq = Subquery(
      metrics_data.objects.filter(jid_id=OuterRef("jid"))
      .filter(stale_q)
      .values("jid_id")
      .annotate(c=Count("id"))
      .values("c")[:1],
      output_field=IntegerField(),
  )
  annotated = qs.annotate(
      md_count=Coalesce(md_total_sq, int0),
      stale_null=Coalesce(stale_count_sq, int0),
  )
  need_metrics = Q(md_count__lt=expected) | Q(stale_null__gt=0)
  maybe_live_distinct = Q(md_count__gte=expected) & Q(stale_null=0)
  # PostgreSQL only: re-run when host_data has more per-host sample times (sum
  # of COUNT(DISTINCT time) per host) than at last metrics persist (same window
  # + FQDN host list as jid_table).
  if connections["default"].vendor == "postgresql":
    annotated = annotated.annotate(
        live_distinct_time_count=live_distinct_host_time_count_expression(
            _host_name_suffix(),
        ),
    )
    live_distinct_needs_refresh = (
        maybe_live_distinct
        & Q(metrics_distinct_time_count__isnull=False)
        & Q(live_distinct_time_count__gt=F("metrics_distinct_time_count"))
    )
    need_metrics |= live_distinct_needs_refresh
  return annotated.filter(need_metrics).order_by("-end_time", "-jid")


def _iter_chunked_pks(queryset, chunk_size):
  """Yield (pk_list, total_so_far) in bounded chunks via queryset slicing.

  We intentionally avoid a long-lived streaming cursor here because the caller
  performs additional ORM queries while iterating chunks. On PostgreSQL, mixing
  a still-open server-side cursor with nested queries on the same connection can
  trigger protocol desynchronisation errors.

  For Django QuerySets ordered by ``-end_time, -jid`` we use keyset pagination
  to avoid large OFFSET scans during big backfills. For non-ORM/fake query-like
  objects (e.g. unit-test stubs), we transparently fall back to offset slicing.
  """
  total = 0
  # Primary path: keyset pagination for ORM querysets.
  try:
    last_end_time = None
    last_jid = None
    while True:
      page_qs = queryset
      if last_end_time is not None and last_jid is not None:
        page_qs = page_qs.filter(
            Q(end_time__lt=last_end_time) | (
                Q(end_time=last_end_time) & Q(jid__lt=last_jid)
            )
        )
      rows = list(page_qs.values_list("jid", "end_time")[:chunk_size])
      if not rows:
        break
      chunk = [jid for jid, _ in rows]
      total += len(chunk)
      yield chunk, total
      last_jid, last_end_time = rows[-1]
    return
  except (OperationalError, DatabaseError):
    # Do not fall back to offset slicing: same expensive SQL and masks timeouts.
    raise
  except Exception:
    # Fall back to bounded offset slicing for test doubles/non-ORM objects.
    pass

  offset = 0
  pk_values = queryset.values_list("jid", flat=True)
  while True:
    chunk = list(pk_values[offset:offset + chunk_size])
    if not chunk:
      break
    total += len(chunk)
    yield chunk, total
    offset += chunk_size


def _job_refs_from_jids(jids):
  """Return lightweight job references that only carry jid.

  metrics.Metrics().run() only requires ``job.jid``. Using tiny objects instead
  of ORM model instances avoids per-chunk model allocation and a redundant DB
  round-trip, which lowers memory usage and query pressure for large backfills.
  """
  return [SimpleNamespace(jid=jid) for jid in jids]


def _fqdn_hosts_for_job(job_row):
  """Return job host_list as FQDN hostnames used by host_data."""
  suffix = _host_name_suffix()
  hosts = []
  for host in (job_row.get("host_list") or []):
    h = str(host or "").strip()
    if not h:
      continue
    hosts.append(h if "." in h else (h + suffix))
  return hosts


def _latest_sample_time_by_host(hosts):
  """Map host -> max(host_data.time) for ``hosts``, using bounded batches."""
  latest_by_host = {}
  if not hosts:
    return latest_by_host
  host_list = sorted(hosts)
  batch = max(1, int(HOST_LAST_TIME_LOOKUP_BATCH))
  conn = connections["default"]
  if conn.vendor == "postgresql":
    ops = conn.ops
    tbl = ops.quote_name(host_data._meta.db_table)
    col_host = ops.quote_name("host")
    col_time = ops.quote_name("time")
    # DISTINCT ON + ORDER BY over many hypertable chunks can still trigger huge
    # parallel sorts. One backward index scan per host (LATERAL LIMIT 1) stays
    # bounded when (host, time) is indexed.
    sql = (
        "SELECT h.host_val, m.{t} FROM unnest(%s::text[]) AS h(host_val) "
        "LEFT JOIN LATERAL ("
        " SELECT d.{t} FROM {tbl} d WHERE d.{h} = h.host_val "
        " ORDER BY d.{t} DESC LIMIT 1"
        ") AS m ON TRUE"
    ).format(h=col_host, t=col_time, tbl=tbl)
    using = getattr(conn, "alias", None) or "default"
    for i in range(0, len(host_list), batch):
      chunk = host_list[i:i + batch]
      with transaction.atomic(using=using):
        with conn.cursor() as cursor:
          cursor.execute("SET LOCAL max_parallel_workers_per_gather = 0")
          cursor.execute(sql, [chunk])
          for row_host, row_time in cursor.fetchall():
            if row_time is not None:
              latest_by_host[row_host] = row_time
    return latest_by_host

  for i in range(0, len(host_list), batch):
    chunk = host_list[i:i + batch]
    qs = (
        host_data.objects.filter(host__in=chunk)
        .values("host")
        .annotate(last_time=Max("time"))
    )
    for row in qs:
      latest_by_host[row.get("host")] = row.get("last_time")
  return latest_by_host


def _ready_jids_from_job_rows(jobs):
  """Return ready jids from pre-fetched job rows (jid/end_time/host_list)."""
  if not jobs:
    return []

  unique_hosts = set()
  job_hosts = {}
  for row in jobs:
    # De-duplicate host_list entries; gating is based on unique hosts and each
    # unique host must have data strictly after job end.
    hosts = set(_fqdn_hosts_for_job(row))
    job_hosts[row["jid"]] = hosts
    unique_hosts.update(hosts)

  latest_by_host = _latest_sample_time_by_host(unique_hosts)

  ready = []
  for row in jobs:
    jid = row["jid"]
    end_time = row.get("end_time")
    hosts = job_hosts.get(jid) or set()
    if end_time is None or not hosts:
      continue
    if all(
        (latest_by_host.get(host) is not None and latest_by_host[host] > end_time)
        for host in hosts
    ):
      ready.append(jid)
  return ready


def _filter_jids_with_samples_after_end(jids):
  """Keep jids where every job host has latest host_data.time strictly after end_time."""
  if not jids:
    return []

  conn = connections["default"]
  if conn.vendor == "postgresql":
    ops = conn.ops
    job_tbl = ops.quote_name(job_data._meta.db_table)
    host_tbl = ops.quote_name(host_data._meta.db_table)
    sql = """
      WITH jobs AS (
        SELECT j.jid, j.end_time, j.host_list
        FROM {job_tbl} j
        WHERE j.jid = ANY(%s::text[])
      ),
      exploded_hosts AS (
        SELECT jobs.jid, jobs.end_time, unnest(jobs.host_list) AS host_raw
        FROM jobs
      ),
      fqdn_hosts AS (
        SELECT
          eh.jid,
          eh.end_time,
          CASE
            WHEN position('.' in eh.host_raw) > 0 THEN eh.host_raw
            ELSE eh.host_raw || %s
          END AS host_name
        FROM exploded_hosts eh
      ),
      latest AS (
        SELECT fh.jid, fh.host_name, max(h.time) AS last_time, max(fh.end_time) AS end_time
        FROM fqdn_hosts fh
        LEFT JOIN {host_tbl} h ON h.host = fh.host_name
        GROUP BY fh.jid, fh.host_name
      )
      SELECT jid
      FROM latest
      GROUP BY jid
      HAVING count(*) FILTER (WHERE last_time IS NULL OR last_time <= end_time) = 0
      ORDER BY jid
    """.format(job_tbl=job_tbl, host_tbl=host_tbl)
    using = getattr(conn, "alias", None) or "default"
    try:
      with transaction.atomic(using=using):
        with conn.cursor() as cursor:
          cursor.execute("SET LOCAL statement_timeout = %s", [READINESS_QUERY_TIMEOUT_MS])
          cursor.execute(sql, [list(jids), _host_name_suffix()])
          return [row[0] for row in cursor.fetchall()]
    except (OperationalError, DatabaseError) as exc:
      log_print(
          "readiness SQL timed out/failed for {0} jids; falling back to ORM readiness path: {1}".format(
              len(jids), exc
          ),
          flush=True,
      )
      jobs = list(
          job_data.objects.filter(jid__in=jids).values("jid", "end_time", "host_list")
      )
      return _ready_jids_from_job_rows(jobs)

  jobs = list(
      job_data.objects.filter(jid__in=jids).values("jid", "end_time", "host_list")
  )
  return _ready_jids_from_job_rows(jobs)


def _proxy_reject_not_ready_jids(jids):
  """Cheap jid-level prefilter: reject jids with no post-end sample at all."""
  if not jids:
    return set(), []
  if connections["default"].vendor != "postgresql":
    return set(), list(jids)
  has_post_end_sample = Exists(
      host_data.objects.filter(
          jid=OuterRef("jid"),
          time__gt=OuterRef("end_time"),
      )
  )
  rows = list(
      job_data.objects.filter(jid__in=jids)
      .annotate(
          has_post_end=Case(
              When(end_time__isnull=True, then=Value(False)),
              default=has_post_end_sample,
              output_field=BooleanField(),
          )
      )
      .values_list("jid", "has_post_end")
  )
  reject = {jid for jid, has_post_end in rows if not has_post_end}
  unknown = [jid for jid, has_post_end in rows if has_post_end]
  return reject, unknown


def _adjust_readiness_probe_target(current_target, had_error, elapsed_s, produced_ready, max_target):
  """Adaptive target size for per-pass readiness probes."""
  target = max(READINESS_PROBE_TARGET_MIN, int(current_target))
  if had_error:
    return max(READINESS_PROBE_TARGET_MIN, target // 2)
  if produced_ready and elapsed_s <= READINESS_PROBE_TARGET_FAST_SUCCESS_S:
    return min(int(max_target), target + READINESS_PROBE_TARGET_STEP)
  return target


def update_metrics(date, rerun=False):
  """Compute and persist metrics for all jobs ending on date (runtime >= min_time).

  If not rerun, skip jobs that already have the full metrics catalog (each metric
  has a value or no_data_reason). Uses metrics.Metrics().run(jobs_list).

  Memory-optimized: filters in DB, processes in chunks, no full-list cache.
  """
  return update_metrics_for_dates([date], rerun=rerun)


def _build_date_chunk_iterators(dates, min_time, rerun, phase_timer):
  date_states = []
  for d in dates:
    with phase_timer.phase("candidate_sql_s"):
      qs = _jobs_queryset(d, min_time, rerun)
    date_states.append({
        "date": d,
        "iter": _iter_chunked_pks(qs, CHUNK_SIZE),
        "done": False,
    })
  return date_states


def _fill_ready_queue(date_states, ready_queue, mode, prefetch_chunks, phase_timer, stats):
  if mode == "strict_date":
    active = [s for s in date_states if not s["done"]]
    if not active:
      return
    state = active[0]
    while len(ready_queue) < prefetch_chunks:
      try:
        pk_chunk, _ = next(state["iter"])
      except StopIteration:
        state["done"] = True
        break
      proxy_reject, proxy_unknown = _proxy_reject_not_ready_jids(pk_chunk)
      stats["proxy_checked_chunks"] += 1
      stats["proxy_rejected_jids"] += len(proxy_reject)
      try:
        with phase_timer.phase("readiness_s"):
          ready_jids = _filter_jids_with_samples_after_end(proxy_unknown)
      except (OperationalError, DatabaseError) as exc:
        log_print(
            "readiness check failed for strict-date chunk size={0}; skipping chunk: {1}".format(
                len(proxy_unknown), exc
            ),
            flush=True,
        )
        stats["readiness_error_chunks"] += 1
        ready_jids = []
      stats["candidate_jids"] += len(pk_chunk)
      stats["skipped_not_ready"] += (len(pk_chunk) - len(ready_jids))
      if ready_jids:
        ready_queue.extend(ready_jids)
    return

  for state in [s for s in date_states if not s["done"]]:
    if len(ready_queue) >= prefetch_chunks:
      break
    try:
      pk_chunk, _ = next(state["iter"])
    except StopIteration:
      state["done"] = True
      continue
    proxy_reject, proxy_unknown = _proxy_reject_not_ready_jids(pk_chunk)
    stats["proxy_checked_chunks"] += 1
    stats["proxy_rejected_jids"] += len(proxy_reject)
    try:
      with phase_timer.phase("readiness_s"):
        ready_jids = _filter_jids_with_samples_after_end(proxy_unknown)
    except (OperationalError, DatabaseError) as exc:
      log_print(
          "readiness check failed for global chunk size={0}; skipping chunk: {1}".format(
              len(proxy_unknown), exc
          ),
          flush=True,
      )
      stats["readiness_error_chunks"] += 1
      ready_jids = []
    stats["candidate_jids"] += len(pk_chunk)
    stats["skipped_not_ready"] += (len(pk_chunk) - len(ready_jids))
    if ready_jids:
      ready_queue.extend(ready_jids)


def _start_readiness_producer(
    *,
    date_states,
    ready_queue,
    ready_queue_lock,
    producer_done,
    scheduler_mode,
    prefetch_ready_cap,
    readiness_probe_target,
    phase_timer,
    stats,
    completion_reporter,
):
  """Start background producer that fills ready_queue from readiness checks."""
  def _producer_loop():
    close_old_connections()
    try:
      while not shutdown_requested[0]:
        with ready_queue_lock:
          current_depth = len(ready_queue)
        if current_depth >= prefetch_ready_cap:
          time.sleep(0.05)
          continue
        started = time.monotonic()
        prev_errors = stats["readiness_error_chunks"]
        local_ready = deque()
        probe_target = max(
            READINESS_PROBE_TARGET_MIN,
            min(readiness_probe_target["value"], prefetch_ready_cap - current_depth),
        )
        _fill_ready_queue(
            date_states,
            local_ready,
            scheduler_mode,
            prefetch_chunks=max(1, probe_target),
            phase_timer=phase_timer,
            stats=stats,
        )
        readiness_probe_target["value"] = _adjust_readiness_probe_target(
            current_target=readiness_probe_target["value"],
            had_error=(stats["readiness_error_chunks"] > prev_errors),
            elapsed_s=(time.monotonic() - started),
            produced_ready=bool(local_ready),
            max_target=prefetch_ready_cap,
        )
        if stats["readiness_error_chunks"] > completion_reporter.readiness_errors_total():
          completion_reporter.record_readiness_error_chunk(
              stats["readiness_error_chunks"] - completion_reporter.readiness_errors_total()
          )
        if local_ready:
          with ready_queue_lock:
            ready_queue.extend(local_ready)
          continue
        if all(s["done"] for s in date_states):
          break
        time.sleep(0.05)
    finally:
      producer_done.set()
      close_old_connections()

  producer = threading.Thread(
      target=_producer_loop,
      name="metrics-readiness-producer",
      daemon=True,
  )
  producer.start()
  return producer


def _compute_metrics_batch(metrics_manager, job_refs, shared_pool):
  """Run a batch; if it fails, isolate bad jobs and continue."""
  if not job_refs:
    return [], 0
  try:
    metrics_manager.run(job_refs, pool=shared_pool)
    return [j.jid for j in job_refs], 0
  except Exception as exc:
    log_print(
        "metrics scheduler: batch failed (size={0}), retrying jobs one-by-one: {1}".format(
            len(job_refs), exc
        ),
        flush=True,
    )

  succeeded = []
  failed = 0
  for job_ref in job_refs:
    if shutdown_requested[0]:
      break
    try:
      metrics_manager.run([job_ref], pool=shared_pool)
      succeeded.append(job_ref.jid)
    except Exception as job_exc:
      failed += 1
      log_print(
          "metrics scheduler: failed jid={0}; skipping and continuing: {1}".format(
              job_ref.jid, job_exc
          ),
          flush=True,
      )
  return succeeded, failed


def update_metrics_for_dates(dates, rerun=False):
  """Global scheduler across dates to keep workers saturated."""
  close_old_connections()
  min_time = 300
  phase_timer = _PhaseTimer()
  scheduler_mode = cfg.get_metrics_scheduler_mode()
  prefetch_target = cfg.get_metrics_scheduler_ready_queue_target()
  prefetch_chunks = cfg.get_metrics_scheduler_prefetch_chunks()
  stats = {
      "processed": 0,
      "failed": 0,
      "skipped_not_ready": 0,
      "candidate_jids": 0,
      "readiness_error_chunks": 0,
      "proxy_checked_chunks": 0,
      "proxy_rejected_jids": 0,
  }

  def _run():
    with _pg_session_statement_timeout_for_metrics_batch():
      metrics_manager = metrics.Metrics()
      prewarm_pipeline = _PrewarmPipeline()
      completion_reporter = _CompletionReporter()
      completion_reporter.start()
      ready_queue = deque()
      prefetch_ready_cap = max(
          1, min(prefetch_target, prefetch_chunks * CHUNK_SIZE)
      )
      readiness_probe_target = {
          "value": max(READINESS_PROBE_TARGET_MIN, min(prefetch_ready_cap, CHUNK_SIZE))
      }
      batch_cap = min(prefetch_target, GLOBAL_SCHEDULER_BATCH_SIZE)
      log_print(
          "Starting metrics scheduler days={0} mode={1} prefetch_ready_cap={2} "
          "compute_batch_cap={3} prewarm_mode={4}".format(
              len(dates),
              scheduler_mode,
              prefetch_ready_cap,
              batch_cap,
              cfg.get_metrics_plot_prewarm_mode(),
          ),
          flush=True,
      )
      date_states = _build_date_chunk_iterators(
          dates, min_time, rerun, phase_timer
      )
      log_print(
          "Candidate iterators ready for {0} day(s); first keyset/readiness "
          "queries may be slow on large databases.".format(len(date_states)),
          flush=True,
      )
      shared_pool = metrics_manager.ensure_pool()
      log_print("Metrics worker pool ready.", flush=True)
      ready_queue_lock = threading.Lock()
      producer_done = threading.Event()
      producer = _start_readiness_producer(
          date_states=date_states,
          ready_queue=ready_queue,
          ready_queue_lock=ready_queue_lock,
          producer_done=producer_done,
          scheduler_mode=scheduler_mode,
          prefetch_ready_cap=prefetch_ready_cap,
          readiness_probe_target=readiness_probe_target,
          phase_timer=phase_timer,
          stats=stats,
          completion_reporter=completion_reporter,
      )
      t0 = time.monotonic()
      compute_batches = 0
      stall_iters = 0
      try:
        while not shutdown_requested[0]:
          with ready_queue_lock:
            if ready_queue:
              jobs_this_round = []
              while ready_queue and len(jobs_this_round) < batch_cap:
                jobs_this_round.append(ready_queue.popleft())
            else:
              jobs_this_round = []
          if not jobs_this_round:
            if producer_done.is_set():
              break
            stall_iters += 1
            if stall_iters == 1 or stall_iters % 200 == 0:
              pending_days = sum(1 for s in date_states if not s["done"]) if not producer_done.is_set() else 0
              log_print(
                  "metrics scheduler: no ready jobs yet "
                  "(pending_days={0} candidate_jids={1} skipped_not_ready={2} stall_pass={3}); "
                  "still scanning candidates.".format(
                      pending_days,
                      stats["candidate_jids"],
                      stats["skipped_not_ready"],
                      stall_iters,
                  ),
                  flush=True,
              )
            time.sleep(0.05)
            continue
          stall_iters = 0
          job_refs = _job_refs_from_jids(jobs_this_round)
          with phase_timer.phase("metrics_compute_s"):
            succeeded_jids, failed_count = _compute_metrics_batch(
                metrics_manager, job_refs, shared_pool
            )
          stats["processed"] += len(succeeded_jids)
          stats["failed"] += failed_count
          completion_reporter.record_completed(len(succeeded_jids))
          compute_batches += 1
          if compute_batches == 1 or compute_batches % 25 == 0:
            log_print(
                "metrics scheduler: compute batch {0} size={1} "
                "processed_total={2} failed_total={3}".format(
                    compute_batches,
                    len(job_refs),
                    stats["processed"],
                    stats["failed"],
                ),
                flush=True,
            )
          with phase_timer.phase("prewarm_s"):
            for jid in succeeded_jids:
              prewarm_pipeline.submit(jid)
            prewarm_pipeline.drain_some(force=False)
          if (
              GC_COLLECT_EVERY_N_CHUNKS > 0
              and compute_batches % GC_COLLECT_EVERY_N_CHUNKS == 0
              and gc.get_count()[0] > 10000
          ):
            gc.collect()
      finally:
        producer_done.set()
        producer.join(timeout=2.0)
        with phase_timer.phase("prewarm_s"):
          prewarm_pipeline.finish()
        completion_reporter.stop()
        metrics_manager.close_pool()

      elapsed = max(0.001, time.monotonic() - t0)
      totals = phase_timer.totals()
      prewarm_stats = prewarm_pipeline.stats()
      worker_busy_ratio = (
          totals["metrics_compute_s"] / elapsed if elapsed > 0 else 0.0
      )
      log_print(
          "Finished metrics scheduler mode={0}: processed={1} failed={2} "
          "candidate_jids={3} skipped_not_ready={4} readiness_error_chunks={5} "
          "proxy_checked_chunks={6} proxy_rejected_jids={7} readiness_probe_target={8} "
          "completed_last_hour={9} elapsed_s={10:.2f} jobs_per_min={11:.2f} "
          "worker_busy_ratio={12:.3f} phase_candidate_s={13:.2f} "
          "phase_readiness_s={14:.2f} phase_compute_s={15:.2f} phase_prewarm_s={16:.2f} "
          "prewarm_backlog_jobs={17} prewarm_lag_seconds_p95={18:.3f} "
          "prewarm_success_ratio={19:.3f}".format(
              scheduler_mode,
              stats["processed"],
              stats["failed"],
              stats["candidate_jids"],
              stats["skipped_not_ready"],
              stats["readiness_error_chunks"],
              stats["proxy_checked_chunks"],
              stats["proxy_rejected_jids"],
              readiness_probe_target["value"],
              completion_reporter.completed_in_window(),
              elapsed,
              (stats["processed"] * 60.0) / elapsed,
              worker_busy_ratio,
              totals["candidate_sql_s"],
              totals["readiness_s"],
              totals["metrics_compute_s"],
              totals["prewarm_s"],
              prewarm_stats["prewarm_backlog_jobs"],
              prewarm_stats["prewarm_lag_seconds_p95"],
              prewarm_stats["prewarm_success_ratio"],
          ),
          flush=True,
      )

  run_with_db_retry(
      _run,
      attempts=2,
      on_retry=lambda exc, _attempt: log_print(
          "Database error while updating metrics for dates {0}, retrying once: {1}".format(
              ",".join(d.strftime("%Y-%m-%d") for d in dates),
              exc,
          )
      ),
  )


def main(argv=None, sleep_after=None):
  """Entry point for updating metrics_data for a date or date range.

  When invoked as a script, argv defaults to sys.argv. Management commands
  can pass a custom argv list (e.g. parsed from options).

  If ``sleep_after`` is true, the function sleeps 300s at the end (legacy
  supervisor loop). Default is false. Opt in explicitly or set environment
  variable ``HPCPERFSTATS_UPDATE_METRICS_MAIN_SLEEP_AFTER`` to ``1``/``true``
  /``yes`` when ``sleep_after`` is omitted.

  Dates in the parsed range are processed **newest day first**; see module
  docstring for per-day job order.
  """
  if argv is None:
    argv = sys.argv

  if sleep_after is None:
    sleep_after = os.environ.get(
        "HPCPERFSTATS_UPDATE_METRICS_MAIN_SLEEP_AFTER", ""
    ).strip().lower() in ("1", "yes", "true")

  #################################################################
  default_start, default_end = _default_metrics_date_range()
  startdate, enddate = parse_start_end_dates(argv, default_start, default_end)

  if cfg.get_sync_enable_cpuset_priority_budget():
    budget = cfg.derive_pipeline_cpuset_priority_budget()
    overlap_mode = cfg.get_pipeline_overlap_mode()
    log_print(
        "Metrics cpuset budget effective_cores=%d metrics_cap=%d overlap_mode=%s"
        % (budget["effective_cores"], budget["metrics_cap"], overlap_mode)
    )

  log_date_range("metrics to update", startdate, enddate)
  #################################################################

  day_count = (enddate - startdate).days + 1
  # Newest calendar day first (end of range / today before older days).
  all_dates = [enddate - timedelta(days=i) for i in range(day_count)]
  log_print(
      "Date order (newest first): {0}".format(
          ", ".join(d.strftime("%Y-%m-%d") for d in all_dates)
      ),
      flush=True,
  )
  scheduler_mode = cfg.get_metrics_scheduler_mode()
  if scheduler_mode == "strict_date":
    for d in all_dates:
      if shutdown_requested[0]:
        break
      result = update_metrics(d)
      log_print(result)
  else:
    result = update_metrics_for_dates(all_dates)
    log_print(result)

  if sleep_after and not shutdown_requested[0]:
    # Close DB connections before long sleep to avoid idle connections.
    close_old_connections()
    connections.close_all()
    sleep_until_shutdown(300)


if __name__ == "__main__":
  previous_sigterm_handler = None
  sigterm_received = None
  try:
    previous_sigterm_handler, sigterm_received, _ = _install_sigterm_handler(
        exit_code=143
    )
    main()
    if shutdown_requested[0]:
      sys.exit(143)
  finally:
    _shutdown_db_best_effort()
    _notify_parent_if_sigterm(sigterm_received)
    if previous_sigterm_handler is not None:
      try:
        signal.signal(signal.SIGTERM, previous_sigterm_handler)
      except Exception:
        pass
