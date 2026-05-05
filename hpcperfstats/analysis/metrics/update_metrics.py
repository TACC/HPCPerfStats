#!/usr/bin/env python
"""Update metrics_data for jobs ending on each day in a date range.

Filters by runtime, optionally skips jobs that already have a full metrics
catalog (one row per metric with either a numeric value or no_data_reason),
matching persisted job-plot fingerprints for all plot kinds, and current
job-detail / type-detail artifact fingerprints (PostgreSQL only for the
latter two). Runs Metrics().run(jobs_list). With no CLI date arguments,
processes the last seven calendar days through today.

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
from dataclasses import dataclass, field
from types import SimpleNamespace
from datetime import datetime, timedelta
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from hpcperfstats.django_bootstrap import ensure_django
ensure_django()

from django.db import close_old_connections, connections, transaction
from django.db.models import Count, Exists, F, IntegerField, Max, OuterRef, Q, Subquery, Value
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
from hpcperfstats.site.machine.job_plot_artifacts import (
    JOB_PLOT_KINDS,
    JOB_PLOT_LAYOUT_NORMAL,
    persist_job_plot_artifacts_for_jid,
)
from hpcperfstats.site.machine.job_detail_artifacts import (
    ARTIFACT_KIND_JOB_DETAIL,
    ARTIFACT_KIND_MULTIPRECISION_MIX,
    persist_job_detail_artifacts_for_jid,
)
from hpcperfstats.site.machine.artifact_readiness_expressions import (
    DetailArtifactInputFingerprintHex,
    HostDataSchemaKeyCount,
    PlotArtifactInputFingerprintHex,
    TypeDetailFreshFingerprintRowCount,
)
from hpcperfstats.site.machine.models import (
    host_data,
    job_data,
    job_detail_artifact,
    job_plot_artifact,
    metrics_data,
)

DEBUG = cfg.get_debug()

# Populated after ``update_metrics_for_dates`` completes when
# ``HPCPERFSTATS_UPDATE_METRICS_RETURN_DIAGNOSTICS`` is truthy (tests / profiling).
LAST_UPDATE_METRICS_DIAGNOSTICS = None

# Process jobs in chunks to bound memory; full job rows are not all held at once.
CHUNK_SIZE = 500

# host_data "latest sample per host" probes: keep each round-trip bounded.
# PostgreSQL uses a per-host LATERAL + LIMIT 1 (index probe on (host, time))
# inside a short transaction with parallel workers disabled for the probe.
HOST_LAST_TIME_LOOKUP_BATCH = 64

# Running a full GC every chunk is expensive on large backfills; amortize it.
GC_COLLECT_EVERY_N_CHUNKS = 20

# When argv has no start/end dates, process this many calendar days ending today.
DEFAULT_METRICS_RANGE_DAYS = 7
GLOBAL_SCHEDULER_BATCH_SIZE = 256
READINESS_PROBE_TARGET_MIN = 64
READINESS_PROBE_TARGET_STEP = 64
READINESS_PROBE_TARGET_FAST_SUCCESS_S = 0.35
STRICT_CHECK_BATCH_MIN = 32
STRICT_CHECK_BATCH_STEP = 32
STRICT_CHECK_FAST_SUCCESS_S = 0.25
STRICT_CHECK_COOLDOWN_SECONDS = 30
RESCAN_INTERVAL_SECONDS = 5.0
DEFERRED_NOT_READY_RETRY_SECONDS = 10.0
DEFERRED_NOT_READY_MAX_RETRIES = 30
DEFERRED_NOT_READY_MAX_AGE_SECONDS = 900.0
DEFERRED_NOT_READY_QUARANTINE_SECONDS = 300.0
STALL_WARNING_EVERY_PASSES = 200
STALL_EXIT_AFTER_SECONDS = 900.0
COMPUTE_BATCH_WATCHDOG_SECONDS = 120.0
COMPUTE_BATCH_MIN_CAP = 16
COMPUTE_BATCH_DOWNSHIFT_FACTOR = 0.5
COMPUTE_BATCH_UPSHIFT_STEP = 16
RESCAN_FETCH_LIMIT = CHUNK_SIZE
TELEMETRY_SAMPLE_LIMIT = 2048


def _new_jid_telemetry():
  """Default per-jid telemetry counters for scheduler diagnostics."""
  return {
      "detail_gpu_metrics_reused": 0,
      "detail_fsio_metrics_reused": 0,
      "detail_fsio_fallback_queries": 0,
      "detail_gpu_fallback_queries": 0,
      "plot_row_lookup_queries": 0,
      "plot_row_lookup_hits": 0,
      "plot_jt_memo_host_time_hits": 0,
      "plot_jt_memo_aggregate_hits": 0,
      "plot_jt_memo_aggregate_misses": 0,
  }


def _new_scheduler_stats():
  """Default scheduler counters (reused for retry resets)."""
  return {
      "processed": 0,
      "failed": 0,
      "skipped_not_ready": 0,
      "candidate_jids": 0,
      "readiness_error_chunks": 0,
      "proxy_checked_chunks": 0,
      "proxy_rejected_jids": 0,
      "proxy_not_ready_jids": 0,
      "strict_not_ready_jids": 0,
      "strict_ready_jids": 0,
      "strict_cooldown_skips": 0,
      "deferred_not_ready_queue_size": 0,
      "deferred_not_ready_due_now": 0,
      "deferred_quarantined_jids": 0,
      "stall_exit_triggered": 0,
      "stall_reason": "",
      "ready_enqueued_total": 0,
      "ready_dequeued_total": 0,
      "inflight_jids": 0,
      "compute_batches_total": 0,
      "batch_compute_exceptions_total": 0,
      "per_jid_fallback_failures_total": 0,
      "attempted_total": 0,
      "strict_check_calls": 0,
      "strict_check_timeouts": 0,
      "strict_check_avg_latency_ms": 0.0,
      "strict_batch_size_current": STRICT_CHECK_BATCH_MIN,
  }


def _handle_strict_readiness_db_error(
    *,
    stats,
    strict_check_state,
    elapsed_s,
    batch_size_seen,
    exc,
    strict_check_cooldown_until=None,
    cooldown_jids=None,
):
  """Update counters/batch-size for strict-readiness DB failures and log once."""
  stats["strict_check_timeouts"] += 1
  stats["readiness_error_chunks"] += 1
  strict_check_state["batch_size"] = _adjust_strict_check_batch_size(
      current_size=strict_check_state["batch_size"],
      had_timeout=True,
      latency_s=elapsed_s,
      max_size=strict_check_state["max_batch_size"],
  )
  stats["strict_batch_size_current"] = strict_check_state["batch_size"]
  if strict_check_cooldown_until is not None and cooldown_jids:
    mono_now = time.monotonic()
    for jid in cooldown_jids:
      strict_check_cooldown_until[jid] = mono_now + STRICT_CHECK_COOLDOWN_SECONDS
  log_print(
      "strict readiness batch timed out size={0}; new_strict_batch_size={1}: {2}".format(
          int(batch_size_seen), strict_check_state["batch_size"], exc
      ),
      flush=True,
  )


class _PhaseTimer:
  """Collect per-phase wall-clock timings for pipeline reporting."""

  def __init__(self):
    self._lock = threading.Lock()
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
      elapsed = time.monotonic() - t0
      with self._lock:
        self._totals[key] = self._totals.get(key, 0.0) + elapsed

  def totals(self):
    with self._lock:
      return dict(self._totals)


class _PrewarmPipeline:
  """Required prewarm stage with bounded backlog and retries.

  ``submit`` / ``drain_some`` mutate ``_pending`` and ``_created_at``; call only
  from the owning thread (metrics scheduler main thread) unless guarded.
  ``run_for_jid`` is safe from concurrent scheduler compute threads (counters
  under ``_counters_lock``).
  """

  def __init__(self):
    self._mode = cfg.get_metrics_plot_prewarm_mode()
    self._workers = cfg.get_metrics_prewarm_workers()
    self._attempts = cfg.get_metrics_prewarm_retry_attempts()
    self._counters_lock = threading.Lock()
    self._pending_lock = threading.Lock()
    self._executor = None
    self._pending = set()
    self._done = 0
    self._failed = 0
    self._lag_samples = []
    self._created_at = {}
    if self._mode == "pipeline_required":
      self._executor = ThreadPoolExecutor(max_workers=self._workers)

  def _persist_detail_plot_elapsed(self, jid, shared_context):
    """Run job-detail then job-plot persistence; return wall times ``(detail_s, plots_s)``."""
    close_old_connections()
    t0 = time.monotonic()
    try:
      persist_job_detail_artifacts_for_jid(jid, context=shared_context)
    except TypeError:
      persist_job_detail_artifacts_for_jid(jid)
    detail_s = time.monotonic() - t0
    t1 = time.monotonic()
    try:
      persist_job_plot_artifacts_for_jid(jid, context=shared_context)
    except TypeError:
      persist_job_plot_artifacts_for_jid(jid)
    plots_s = time.monotonic() - t1
    return detail_s, plots_s

  def _run_one(self, jid):
    last_exc = None
    for _ in range(max(1, self._attempts)):
      try:
        self._persist_detail_plot_elapsed(jid, {})
        return
      except Exception as exc:
        last_exc = exc
    raise last_exc

  def run_for_jid(self, jid, shared_context=None):
    """Run detail+plot prewarm synchronously for one jid.

    Returns a dict with wall-clock seconds: ``detail_s``, ``plots_s`` (when the
    dict ``shared_context`` path is used), ``prewarm_total_s`` (detail+plots or
    the whole ``_run_one`` wall time when ``shared_context`` is not a dict), and
    ``undivided`` (True when only ``prewarm_total_s`` is meaningful).
    """
    if isinstance(shared_context, dict):
      detail_s, plots_s = self._persist_detail_plot_elapsed(jid, shared_context)
      timing = {
          "detail_s": detail_s,
          "plots_s": plots_s,
          "prewarm_total_s": detail_s + plots_s,
          "undivided": False,
      }
    else:
      t0 = time.monotonic()
      self._run_one(jid)
      timing = {
          "detail_s": None,
          "plots_s": None,
          "prewarm_total_s": time.monotonic() - t0,
          "undivided": True,
      }
    with self._counters_lock:
      self._done += 1
    return timing

  def submit(self, jid):
    if self._mode == "inline":
      self._run_one(jid)
      with self._counters_lock:
        self._done += 1
      return
    with self._pending_lock:
      fut = self._executor.submit(self._run_one, jid)
      self._created_at[fut] = time.monotonic()
      self._pending.add(fut)

  def has_pending(self):
    """True when async prewarm tasks are still running (``pipeline_required``)."""
    if self._mode == "inline" or self._executor is None:
      return False
    with self._pending_lock:
      return bool(self._pending)

  def drain_some(self, force=False):
    if self._mode == "inline" or not self._pending:
      return
    with self._pending_lock:
      if not self._pending:
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
        with self._counters_lock:
          self._done += 1
      except Exception as exc:
        with self._counters_lock:
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
    self._last_synced_completed_total = 0
    self._readiness_error_total = 0
    self._stop = threading.Event()
    self._thread = None
    self._extra_stats_getter = None

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

  def sync_completed_total(self, total):
    """Synchronize reporter totals from scheduler's authoritative processed count."""
    now = time.monotonic()
    with self._lock:
      current = max(0, int(total))
      # Handle restarts/resets defensively.
      if current < self._last_synced_completed_total:
        self._last_synced_completed_total = current
        self._completed_total = current
        self._completed_events.clear()
      delta = current - self._last_synced_completed_total
      if delta > 0:
        self._completed_events.append((now, delta))
        self._completed_total += delta
        self._prune_locked(now)
      self._last_synced_completed_total = current

  def readiness_errors_total(self):
    with self._lock:
      return self._readiness_error_total

  def _run(self):
    while not self._stop.wait(self._report_interval_s):
      extra = ""
      if callable(self._extra_stats_getter):
        try:
          extra_map = self._extra_stats_getter() or {}
          extra = (
              " strict_batch_size_current={0} strict_check_calls={1} "
              "strict_check_timeouts={2} strict_check_avg_latency_ms={3:.2f} "
              "proxy_not_ready_jids={4} strict_not_ready_jids={5} strict_ready_jids={6} "
              "strict_cooldown_skips={7} deferred_not_ready_queue_size={8} "
              "deferred_not_ready_due_now={9} deferred_quarantined_jids={10} "
              "ready_enqueued_total={11} ready_dequeued_total={12} inflight_jids={13} "
              "compute_batches_total={14} batch_compute_exceptions_total={15} "
              "per_jid_fallback_failures_total={16} attempted_total={17}".format(
                  int(extra_map.get("strict_batch_size_current", 0)),
                  int(extra_map.get("strict_check_calls", 0)),
                  int(extra_map.get("strict_check_timeouts", 0)),
                  float(extra_map.get("strict_check_avg_latency_ms", 0.0)),
                  int(extra_map.get("proxy_not_ready_jids", 0)),
                  int(extra_map.get("strict_not_ready_jids", 0)),
                  int(extra_map.get("strict_ready_jids", 0)),
                  int(extra_map.get("strict_cooldown_skips", 0)),
                  int(extra_map.get("deferred_not_ready_queue_size", 0)),
                  int(extra_map.get("deferred_not_ready_due_now", 0)),
                  int(extra_map.get("deferred_quarantined_jids", 0)),
                  int(extra_map.get("ready_enqueued_total", 0)),
                  int(extra_map.get("ready_dequeued_total", 0)),
                  int(extra_map.get("inflight_jids", 0)),
                  int(extra_map.get("compute_batches_total", 0)),
                  int(extra_map.get("batch_compute_exceptions_total", 0)),
                  int(extra_map.get("per_jid_fallback_failures_total", 0)),
                  int(extra_map.get("attempted_total", 0)),
              )
          )
        except Exception:
          extra = ""
      log_print(
          "metrics progress: completed_last_hour={0} processed_total={1} "
          "readiness_error_chunks_last_hour={2} readiness_error_chunks_total={3}{4}".format(
              self.completed_in_window(),
              self.completed_total(),
              self.readiness_errors_in_window(),
              self.readiness_errors_total(),
              extra,
          ),
          flush=True,
      )

  def set_extra_stats_getter(self, getter):
    self._extra_stats_getter = getter


@contextlib.contextmanager
def _pg_session_statement_timeout_for_metrics_batch():
  """Temporarily disable PostgreSQL ``statement_timeout`` for long metrics batch queries.

  ``_jobs_queryset`` annotated scans (metrics subqueries, live distinct-time counts,
  and PostgreSQL-only plot/detail fingerprint probes)
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


def _metrics_telemetry_enabled():
  """Opt-in per-jid telemetry for compose-backed tuning runs."""
  v = os.environ.get("HPCPERFSTATS_METRICS_TELEMETRY", "").strip().lower()
  return v in ("1", "true", "yes", "on")


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
    plot_fp_match_sq = Subquery(
        job_plot_artifact.objects.filter(
            jid_id=OuterRef("jid"),
            layout=JOB_PLOT_LAYOUT_NORMAL,
            plot_kind__in=list(JOB_PLOT_KINDS),
            input_fingerprint=OuterRef("expected_plot_input_fp"),
        )
        .values("jid_id")
        .annotate(c=Count("id"))
        .values("c")[:1],
        output_field=IntegerField(),
    )
    job_detail_ok = Exists(
        job_detail_artifact.objects.filter(
            jid_id=OuterRef("jid"),
            artifact_kind=ARTIFACT_KIND_JOB_DETAIL,
            artifact_scope="",
            input_fingerprint=OuterRef("expected_detail_input_fp"),
        )
    )
    multiprecision_mix_ok = Exists(
        job_detail_artifact.objects.filter(
            jid_id=OuterRef("jid"),
            artifact_kind=ARTIFACT_KIND_MULTIPRECISION_MIX,
            artifact_scope="",
            input_fingerprint=OuterRef("expected_detail_input_fp"),
        )
    )
    annotated = annotated.annotate(
        expected_plot_input_fp=PlotArtifactInputFingerprintHex(
            _host_name_suffix(),
        ),
        expected_detail_input_fp=DetailArtifactInputFingerprintHex(),
        schema_type_slot_count=HostDataSchemaKeyCount(),
        type_detail_fresh_row_count=TypeDetailFreshFingerprintRowCount(),
    )
    annotated = annotated.annotate(
        plot_fp_row_matches=Coalesce(plot_fp_match_sq, int0),
        job_detail_row_ok=job_detail_ok,
        multiprecision_mix_row_ok=multiprecision_mix_ok,
    )
    metrics_complete = Q(md_count__gte=expected) & Q(stale_null=0)
    need_plot_artifacts = metrics_complete & Q(
        plot_fp_row_matches__lt=len(JOB_PLOT_KINDS),
    )
    need_detail_artifacts = metrics_complete & (
        Q(job_detail_row_ok=False)
        | Q(multiprecision_mix_row_ok=False)
        | Q(schema_type_slot_count__gt=F("type_detail_fresh_row_count"))
    )
    need_metrics |= need_plot_artifacts | need_detail_artifacts
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

  jobs = list(
      job_data.objects.filter(jid__in=jids)
      .order_by("jid")
      .values("jid", "end_time", "host_list")
  )
  return _ready_jids_from_job_rows(jobs)


def _proxy_readiness_has_any_and_post_end(end_time, max_time):
  """Return ``(has_any_jid, has_post_end)`` matching legacy ``Exists`` semantics.

  ``has_any_jid``: any ``host_data`` row for the jid exists.
  ``has_post_end``: ``end_time`` is set and some row has ``time > end_time``.
  """
  has_any = max_time is not None
  has_post = (
      end_time is not None
      and max_time is not None
      and max_time > end_time
  )
  return has_any, has_post


def _proxy_readiness_bucket(end_time, max_time):
  """Return ``'reject'`` or ``'unknown'`` for one jid's proxy inputs.

  Matches the per-jid branch of :func:`_proxy_reject_not_ready_jids` using
  ``end_time`` and ``Max(time)`` for that jid.
  """
  has_any_jid, has_post_end = _proxy_readiness_has_any_and_post_end(end_time, max_time)
  if has_any_jid and (not has_post_end):
    return "reject"
  return "unknown"


def _proxy_readiness_for_jid(jid):
  """ORM proxy for one jid: same semantics as bulk :func:`_proxy_reject_not_ready_jids`.

  Returns ``'reject'`` when jid-keyed ``host_data`` proves not-ready, or
  ``'unknown'`` when the strict host_list probe must decide (including
  non-PostgreSQL, where bulk code treats every jid as unknown).
  """
  if connections["default"].vendor != "postgresql":
    return "unknown"
  end_row = job_data.objects.filter(jid=jid).values("jid", "end_time").first()
  end_time = None if not end_row else end_row["end_time"]
  max_time = (
      host_data.objects.filter(jid=jid)
      .aggregate(max_time=Max("time"))
      .get("max_time")
  )
  return _proxy_readiness_bucket(end_time, max_time)


def _proxy_reject_not_ready_jids(jids):
  """Cheap jid-level prefilter: reject only when jid-keyed host_data proves not-ready.

  Some ingest paths may not populate ``host_data.jid`` for every row. In that
  case, keep the jid in the ``unknown`` set and let the full readiness probe
  decide using host_list/time-window logic.

  Uses bounded ``jid__in`` batches and ``Max(time)`` aggregates (no correlated
  ``Exists`` per row) to avoid PostgreSQL ``statement_timeout`` on large chunks.
  """
  if not jids:
    return set(), []
  if connections["default"].vendor != "postgresql":
    return set(), list(jids)
  batch = max(1, int(cfg.get_metrics_proxy_reject_jid_batch_size()))
  reject = set()
  unknown = []
  for sub in _iter_subbatches(jids, batch):
    end_rows = job_data.objects.filter(jid__in=sub).values("jid", "end_time")
    end_by_jid = {r["jid"]: r["end_time"] for r in end_rows}
    max_rows = (
        host_data.objects.filter(jid__in=sub)
        .values("jid")
        .annotate(max_time=Max("time"))
        .values("jid", "max_time")
    )
    max_by_jid = {r["jid"]: r["max_time"] for r in max_rows}
    for jid in sub:
      bucket = _proxy_readiness_bucket(
          end_by_jid.get(jid), max_by_jid.get(jid)
      )
      if bucket == "reject":
        reject.add(jid)
      else:
        unknown.append(jid)
  return reject, unknown


def _adjust_readiness_probe_target(current_target, had_error, elapsed_s, produced_ready, max_target):
  """Adaptive target size for per-pass readiness probes."""
  target = max(READINESS_PROBE_TARGET_MIN, int(current_target))
  if had_error:
    return max(READINESS_PROBE_TARGET_MIN, target // 2)
  if produced_ready and elapsed_s <= READINESS_PROBE_TARGET_FAST_SUCCESS_S:
    return min(int(max_target), target + READINESS_PROBE_TARGET_STEP)
  return target


def _adjust_strict_check_batch_size(current_size, had_timeout, latency_s, max_size):
  size = max(STRICT_CHECK_BATCH_MIN, int(current_size))
  if had_timeout:
    return max(STRICT_CHECK_BATCH_MIN, size // 2)
  if latency_s <= STRICT_CHECK_FAST_SUCCESS_S:
    return min(int(max_size), size + STRICT_CHECK_BATCH_STEP)
  return size


def _iter_subbatches(values, batch_size):
  step = max(1, int(batch_size))
  for i in range(0, len(values), step):
    yield values[i:i + step]


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
        "pending_tail": None,
    })
  return date_states


def _fill_ready_queue(
    date_states,
    ready_queue,
    mode,
    prefetch_chunks,
    phase_timer,
    stats,
    strict_check_state,
    strict_check_cooldown_until,
    rr_cursor,
    scheduler_shared_lock,
    on_not_ready_jid=None,
    on_candidate_jid=None,
):
  now = time.monotonic()

  def _resume_or_next_pk_chunk(state):
    """Return ``(pk_list_or_none, is_new_iter_chunk)``."""
    tail = state.get("pending_tail")
    if tail:
      state["pending_tail"] = None
      return tail, False
    try:
      pk_chunk, _ = next(state["iter"])
      return pk_chunk, True
    except StopIteration:
      return None, True

  def _strict_ready_record_latency(latency_s, n_calls):
    """Update rolling strict-check latency and adaptive batch size (``n_calls`` DB calls)."""
    with scheduler_shared_lock:
      prev = stats["strict_check_avg_latency_ms"]
      per_ms = (latency_s / max(1, n_calls)) * 1000.0
      prev_total = stats["strict_check_calls"]
      stats["strict_check_calls"] += n_calls
      if prev_total == 0:
        stats["strict_check_avg_latency_ms"] = per_ms
      else:
        stats["strict_check_avg_latency_ms"] = (prev * 0.85) + (per_ms * 0.15)
      strict_check_state["batch_size"] = _adjust_strict_check_batch_size(
          current_size=strict_check_state["batch_size"],
          had_timeout=False,
          latency_s=latency_s / max(1, n_calls),
          max_size=strict_check_state["max_batch_size"],
      )
      stats["strict_batch_size_current"] = strict_check_state["batch_size"]

  def _strict_ready_fallback_one(jid):
    """Single-jid strict readiness after batch failure (same semantics as legacy path)."""
    with scheduler_shared_lock:
      if strict_check_cooldown_until.get(jid, 0.0) > time.monotonic():
        return None
    t0 = time.monotonic()
    try:
      with phase_timer.phase("readiness_s"):
        strict_ready = _filter_jids_with_samples_after_end([jid])
    except (OperationalError, DatabaseError) as exc:
      with scheduler_shared_lock:
        _handle_strict_readiness_db_error(
            stats=stats,
            strict_check_state=strict_check_state,
            elapsed_s=(time.monotonic() - t0),
            batch_size_seen=1,
            exc=exc,
            strict_check_cooldown_until=strict_check_cooldown_until,
            cooldown_jids=[jid],
        )
      return None
    latency = time.monotonic() - t0
    _strict_ready_record_latency(latency, 1)
    if not strict_ready:
      return None
    return strict_ready[0]

  def _process_pk_chunk(state, pk_chunk):
    """Process ``pk_chunk`` in order; set ``pending_tail`` if prefetch fills mid-chunk.

    Returns ``True`` when ``len(ready_queue) >= prefetch_chunks`` and the caller
    should stop scheduling more readiness work in this invocation.

    Uses batched proxy rejection and batched strict readiness probes to avoid
    per-jid round-trips on the producer thread.
    """
    ordered = list(pk_chunk)
    for jid in ordered:
      if on_candidate_jid is not None:
        on_candidate_jid(jid)
      with scheduler_shared_lock:
        stats["candidate_jids"] += 1

    reject_set, _ = _proxy_reject_not_ready_jids(ordered)
    reject_set = set(reject_set)
    for jid in ordered:
      if jid not in reject_set:
        continue
      if on_not_ready_jid is not None:
        on_not_ready_jid(jid)
      with scheduler_shared_lock:
        stats["proxy_not_ready_jids"] += 1
        stats["proxy_rejected_jids"] += 1
        stats["skipped_not_ready"] += 1

    unknown_ordered = [jid for jid in ordered if jid not in reject_set]
    bs = max(1, int(strict_check_state["batch_size"]))
    pos = 0
    while pos < len(unknown_ordered):
      if len(ready_queue) >= prefetch_chunks:
        state["pending_tail"] = unknown_ordered[pos:]
        return True
      sub_end = min(pos + bs, len(unknown_ordered))
      sub = unknown_ordered[pos:sub_end]
      batch_mono = time.monotonic()
      cooldown_jids = []
      for jid in sub:
        with scheduler_shared_lock:
          if strict_check_cooldown_until.get(jid, 0.0) > batch_mono:
            cooldown_jids.append(jid)
      work = [jid for jid in sub if jid not in cooldown_jids]
      for jid in cooldown_jids:
        if on_not_ready_jid is not None:
          on_not_ready_jid(jid)
        with scheduler_shared_lock:
          stats["strict_not_ready_jids"] += 1
          stats["strict_cooldown_skips"] += 1
          stats["skipped_not_ready"] += 1
      pos = sub_end
      if not work:
        continue
      try:
        with phase_timer.phase("readiness_s"):
          ready_list = _filter_jids_with_samples_after_end(work)
      except (OperationalError, DatabaseError) as exc:
        with scheduler_shared_lock:
          _handle_strict_readiness_db_error(
              stats=stats,
              strict_check_state=strict_check_state,
              elapsed_s=(time.monotonic() - batch_mono),
              batch_size_seen=len(work),
              exc=exc,
              strict_check_cooldown_until=strict_check_cooldown_until,
              cooldown_jids=work,
          )
        for jid in work:
          ready_jid = _strict_ready_fallback_one(jid)
          if ready_jid is None:
            if on_not_ready_jid is not None:
              on_not_ready_jid(jid)
            with scheduler_shared_lock:
              stats["strict_not_ready_jids"] += 1
              stats["skipped_not_ready"] += 1
          else:
            with scheduler_shared_lock:
              stats["strict_ready_jids"] += 1
            ready_queue.append(ready_jid)
            if len(ready_queue) >= prefetch_chunks:
              tail = []
              seen = False
              for j2 in work:
                if j2 == ready_jid:
                  seen = True
                  continue
                if seen:
                  tail.append(j2)
              tail.extend(unknown_ordered[pos:])
              if tail:
                state["pending_tail"] = tail
              return True
        continue

      latency = time.monotonic() - batch_mono
      _strict_ready_record_latency(latency, len(work))
      ready_set = set(ready_list)
      for k, jid in enumerate(work):
        if jid in ready_set:
          with scheduler_shared_lock:
            stats["strict_ready_jids"] += 1
          ready_queue.append(jid)
          if len(ready_queue) >= prefetch_chunks:
            tail = work[k + 1:] + unknown_ordered[pos:]
            if tail:
              state["pending_tail"] = tail
            return True
        else:
          if on_not_ready_jid is not None:
            on_not_ready_jid(jid)
          with scheduler_shared_lock:
            stats["strict_not_ready_jids"] += 1
            stats["skipped_not_ready"] += 1
    return False

  if mode == "strict_date":
    active = [s for s in date_states if not s["done"]]
    if not active:
      return
    state = active[0]
    while len(ready_queue) < prefetch_chunks:
      pk_chunk, is_new = _resume_or_next_pk_chunk(state)
      if pk_chunk is None:
        state["done"] = True
        break
      # Count new keyset pages only (not ``pending_tail`` resumes after mid-chunk prefetch).
      if is_new:
        with scheduler_shared_lock:
          stats["proxy_checked_chunks"] += 1
      if _process_pk_chunk(state, pk_chunk):
        return
    return

  active = [s for s in date_states if not s["done"]]
  if not active:
    return
  start = int(rr_cursor.get("idx", 0)) % len(active)
  ordered = active[start:] + active[:start]
  rr_cursor["idx"] = (start + 1) % len(active)
  for state in ordered:
    if len(ready_queue) >= prefetch_chunks:
      break
    pk_chunk, is_new = _resume_or_next_pk_chunk(state)
    if pk_chunk is None:
      state["done"] = True
      continue
    if is_new:
      with scheduler_shared_lock:
        stats["proxy_checked_chunks"] += 1
    if _process_pk_chunk(state, pk_chunk):
      break


def _start_candidate_rescan_thread(
    *,
    dates,
    min_time,
    rerun,
    rescan_candidate_jids,
    rescan_seen_jids,
    rescan_lock,
    stop_event,
):
  """Start background thread that periodically discovers newly eligible jobs."""
  if not dates:
    return None

  def _rescan_loop():
    close_old_connections()
    try:
      while not shutdown_requested[0] and not stop_event.is_set():
        for d in dates:
          if shutdown_requested[0] or stop_event.is_set():
            break
          try:
            qs = _jobs_queryset(d, min_time, rerun)
            candidates = list(qs.values_list("jid", flat=True)[:RESCAN_FETCH_LIMIT])
          except Exception:
            continue
          if not candidates:
            continue
          with rescan_lock:
            for jid in candidates:
              if jid in rescan_seen_jids:
                continue
              rescan_seen_jids.add(jid)
              rescan_candidate_jids.append(jid)
        stop_event.wait(RESCAN_INTERVAL_SECONDS)
    finally:
      close_old_connections()

  thread = threading.Thread(
      target=_rescan_loop,
      name="metrics-candidate-rescan",
      daemon=True,
  )
  thread.start()
  return thread


def _start_readiness_producer(
    *,
    date_states,
    ready_queue,
    ready_queue_lock,
    producer_done,
    scheduler_mode,
    prefetch_ready_cap,
    readiness_probe_target,
    strict_check_state,
    strict_check_cooldown_until,
    phase_timer,
    stats,
    completion_reporter,
    scheduler_shared_lock,
    rescan_candidate_jids,
    rescan_seen_jids,
    rescan_lock,
):
  """Start background producer that fills ready_queue from readiness checks."""
  def _producer_loop():
    close_old_connections()
    rr_cursor = {"idx": 0}
    deferred_not_ready = {}
    deferred_meta = {}
    last_attempted_total = 0
    last_progress_at = time.monotonic()

    def _remember_candidate(jid):
      with rescan_lock:
        rescan_seen_jids.add(jid)
      deferred_meta.setdefault(jid, {"first_seen": time.monotonic(), "attempts": 0})

    def _defer_not_ready_jid(jid):
      now = time.monotonic()
      meta = deferred_meta.setdefault(jid, {"first_seen": now, "attempts": 0})
      meta["attempts"] += 1
      age_s = max(0.0, now - float(meta["first_seen"]))
      use_quarantine = (
          meta["attempts"] >= DEFERRED_NOT_READY_MAX_RETRIES
          or age_s >= DEFERRED_NOT_READY_MAX_AGE_SECONDS
      )
      if use_quarantine:
        retry_after = DEFERRED_NOT_READY_QUARANTINE_SECONDS
        with scheduler_shared_lock:
          stats["deferred_quarantined_jids"] += 1
      else:
        retry_after = DEFERRED_NOT_READY_RETRY_SECONDS
      deferred_not_ready[jid] = now + retry_after

    try:
      while not shutdown_requested[0]:
        with scheduler_shared_lock:
          attempted_now = int(stats["processed"]) + int(stats["failed"])
        if attempted_now > last_attempted_total:
          last_attempted_total = attempted_now
          last_progress_at = time.monotonic()
        with ready_queue_lock:
          current_depth = len(ready_queue)
        if current_depth >= prefetch_ready_cap:
          time.sleep(0.05)
          continue
        started = time.monotonic()
        with scheduler_shared_lock:
          prev_errors = stats["readiness_error_chunks"]
          probe_cap = readiness_probe_target["value"]
        local_ready = deque()
        deferred_hits = []
        now = time.monotonic()
        deferred_due = [
            jid for jid, retry_at in deferred_not_ready.items()
            if retry_at <= now
        ]
        for jid in deferred_due:
          deferred_not_ready.pop(jid, None)
        with rescan_lock:
          rescan_due = list(rescan_candidate_jids)
          rescan_candidate_jids.clear()
        extra_candidates = []
        seen_extra = set()
        for jid in deferred_due + rescan_due:
          if jid in seen_extra:
            continue
          seen_extra.add(jid)
          extra_candidates.append(jid)
        probe_target = max(
            READINESS_PROBE_TARGET_MIN,
            min(probe_cap, prefetch_ready_cap - current_depth),
        )
        if extra_candidates and len(local_ready) < probe_target:
          extra_state = [{
              "date": None,
              "iter": iter([(extra_candidates, len(extra_candidates))]),
              "done": False,
              "pending_tail": None,
          }]
          _fill_ready_queue(
              extra_state,
              local_ready,
              "strict_date",
              prefetch_chunks=max(1, probe_target),
              phase_timer=phase_timer,
              stats=stats,
              strict_check_state=strict_check_state,
              strict_check_cooldown_until=strict_check_cooldown_until,
              rr_cursor={"idx": 0},
              scheduler_shared_lock=scheduler_shared_lock,
              on_not_ready_jid=deferred_hits.append,
              on_candidate_jid=_remember_candidate,
          )
          for jid in deferred_hits:
            _defer_not_ready_jid(jid)
          if len(local_ready) >= probe_target:
            with scheduler_shared_lock:
              readiness_probe_target["value"] = _adjust_readiness_probe_target(
                  current_target=readiness_probe_target["value"],
                  had_error=(stats["readiness_error_chunks"] > prev_errors),
                  elapsed_s=(time.monotonic() - started),
                  produced_ready=bool(local_ready),
                  max_target=prefetch_ready_cap,
              )
            with ready_queue_lock:
              ready_queue.extend(local_ready)
            with scheduler_shared_lock:
              stats["ready_enqueued_total"] += len(local_ready)
            continue
        _fill_ready_queue(
            date_states,
            local_ready,
            scheduler_mode,
            prefetch_chunks=max(1, probe_target),
            phase_timer=phase_timer,
            stats=stats,
            strict_check_state=strict_check_state,
            strict_check_cooldown_until=strict_check_cooldown_until,
            rr_cursor=rr_cursor,
            scheduler_shared_lock=scheduler_shared_lock,
            on_not_ready_jid=deferred_hits.append,
            on_candidate_jid=_remember_candidate,
        )
        for jid in deferred_hits:
          _defer_not_ready_jid(jid)
        with scheduler_shared_lock:
          readiness_probe_target["value"] = _adjust_readiness_probe_target(
              current_target=readiness_probe_target["value"],
              had_error=(stats["readiness_error_chunks"] > prev_errors),
              elapsed_s=(time.monotonic() - started),
              produced_ready=bool(local_ready),
              max_target=prefetch_ready_cap,
          )
          err_chunks = stats["readiness_error_chunks"]
          stats["deferred_not_ready_queue_size"] = len(deferred_not_ready)
          stats["deferred_not_ready_due_now"] = len(deferred_due)
        if err_chunks > completion_reporter.readiness_errors_total():
          completion_reporter.record_readiness_error_chunk(
              err_chunks - completion_reporter.readiness_errors_total()
          )
        if local_ready:
          with ready_queue_lock:
            ready_queue.extend(local_ready)
          with scheduler_shared_lock:
            stats["ready_enqueued_total"] += len(local_ready)
          continue
        with rescan_lock:
          has_rescan_backlog = bool(rescan_candidate_jids)
        if all(s["done"] for s in date_states) and (not deferred_not_ready) and (not has_rescan_backlog):
          break
        stalled_for_s = time.monotonic() - last_progress_at
        if stalled_for_s >= STALL_EXIT_AFTER_SECONDS:
          with scheduler_shared_lock:
            stats["stall_exit_triggered"] = 1
            stats["stall_reason"] = "no_ready_candidates"
          log_print(
              "metrics scheduler: no progress for {0:.1f}s; exiting producer "
              "(attempted_total={1} candidate_jids={2} deferred_not_ready={3} rescan_backlog={4}).".format(
                  stalled_for_s,
                  attempted_now,
                  stats.get("candidate_jids", 0),
                  len(deferred_not_ready),
                  int(has_rescan_backlog),
              ),
              flush=True,
          )
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
  """Run metrics per-jid and return (succeeded_jids, failed_count)."""
  if not job_refs:
    return [], 0
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


@dataclass
class PerJidComputeContext:
  """Shared per-jid context for the compute + artifact pipeline."""
  jid: str
  artifact_context: dict = field(default_factory=dict)


def _compute_and_prewarm_jid(
    metrics_manager,
    prewarm_pipeline,
    job_ref,
    shared_pool,
    metrics_run_lock=None,
):
  """Compute metrics and immediately prewarm detail/plot artifacts for one jid.

  When ``metrics_run_lock`` is set (concurrent scheduler threads), only
  ``metrics_manager.run`` is taken under the lock: ``multiprocessing.Pool.imap``
  from multiple threads on one pool is unsafe; ``prewarm_pipeline.run_for_jid``
  runs outside the lock so prewarm can overlap other jobs' metrics phases.
  """
  context = PerJidComputeContext(jid=job_ref.jid)
  telemetry = _new_jid_telemetry()
  context.artifact_context["_telemetry"] = telemetry
  t_metrics_start = time.monotonic()
  try:
    if metrics_run_lock is not None:
      with metrics_run_lock:
        metrics_manager.run([job_ref], pool=shared_pool)
    else:
      metrics_manager.run([job_ref], pool=shared_pool)
  except Exception as exc:
    log_print(
        "metrics scheduler: failed jid={0}; skipping and continuing: {1}".format(
            context.jid, exc
        ),
        flush=True,
    )
    return {
        "ok": False,
        "jid": context.jid,
        "metrics_s": time.monotonic() - t_metrics_start,
        "prewarm_s": 0.0,
        "telemetry": telemetry,
    }
  metrics_elapsed = time.monotonic() - t_metrics_start
  t_prewarm_start = time.monotonic()
  try:
    prewarm_timing = prewarm_pipeline.run_for_jid(
        context.jid, shared_context=context.artifact_context
    )
  except Exception as exc:
    log_print(
        "metrics scheduler: prewarm failed jid={0}; continuing: {1}".format(
            context.jid, exc
        ),
        flush=True,
    )
    prewarm_timing = None
  prewarm_elapsed = time.monotonic() - t_prewarm_start
  if prewarm_timing is not None:
    total_s = metrics_elapsed + float(prewarm_timing["prewarm_total_s"])
    if prewarm_timing.get("undivided"):
      log_print(
          "jid={0} compute complete total={1:.1f}s metrics={2:.1f}s "
          "prewarm_job_detail+plots={3:.1f}s".format(
              context.jid,
              total_s,
              metrics_elapsed,
              float(prewarm_timing["prewarm_total_s"]),
          ),
          flush=True,
      )
    else:
      log_print(
          "jid={0} compute complete total={1:.1f}s metrics={2:.1f}s "
          "job_detail={3:.1f}s job_plots={4:.1f}s".format(
              context.jid,
              total_s,
              metrics_elapsed,
              float(prewarm_timing["detail_s"]),
              float(prewarm_timing["plots_s"]),
          ),
          flush=True,
      )
  return {
      "ok": True,
      "jid": context.jid,
      "metrics_s": metrics_elapsed,
      "prewarm_s": prewarm_elapsed,
      "telemetry": telemetry,
  }


def _empty_jid_outcome_telemetry():
  """Telemetry dict shape expected by the scheduler consumer."""
  return _new_jid_telemetry()


def _compute_jid_outcomes_batch(
    job_refs,
    metrics_manager,
    prewarm_pipeline,
    shared_pool,
):
  """Run one batched ``Metrics.run`` then optional prewarm (submit/drain).

  Returns outcome dicts sorted by ``jid`` for stable counters/telemetry.

  A single ``Metrics.run(job_refs, …)`` saturates the process pool; prewarm
  uses ``_PrewarmPipeline.submit`` + ``drain_some`` when ``pipeline_required``,
  or runs inline when configured. Set ``[DEFAULT] metrics_scheduler_skip_prewarm``
  (or env ``HPCPERFSTATS_METRICS_SCHEDULER_SKIP_PREWARM``) to skip plot/detail
  persistence for catch-up runs.
  """
  if not job_refs:
    return []
  if shutdown_requested[0]:
    return [{
        "ok": False,
        "jid": r.jid,
        "metrics_s": 0.0,
        "prewarm_s": 0.0,
        "telemetry": _empty_jid_outcome_telemetry(),
    } for r in job_refs]
  skip_prewarm = cfg.get_metrics_scheduler_skip_prewarm()
  t_batch = time.monotonic()
  try:
    metrics_manager.run(job_refs, pool=shared_pool)
  except Exception as exc:
    log_print(
        "metrics scheduler: batch Metrics.run failed size={0}: {1}".format(
            len(job_refs), exc
        ),
        flush=True,
    )
    # Do not fail the whole dequeue batch on one batch-level exception (e.g.
    # malformed payload causing an exception during persistence). Retry per jid
    # so unaffected jobs can still progress.
    t_recover = time.monotonic()
    succeeded = []
    failed = []
    for ref in job_refs:
      try:
        metrics_manager.run([ref], pool=shared_pool)
        succeeded.append(ref)
      except Exception as one_exc:
        failed.append(ref)
        log_print(
            "metrics scheduler: per-jid Metrics.run failed jid={0} after batch failure: {1}".format(
                ref.jid, one_exc
            ),
            flush=True,
        )
    total_elapsed = time.monotonic() - t_recover
    n_total = max(1, len(job_refs))
    per_metrics = total_elapsed / n_total
    telem = _empty_jid_outcome_telemetry()
    outcomes = []
    for ref in sorted(succeeded, key=lambda r: r.jid):
      outcomes.append({
          "ok": True,
          "jid": ref.jid,
          "metrics_s": per_metrics,
          "prewarm_s": 0.0,
          "telemetry": dict(telem),
          "_batch_exception": True,
          "_fallback_failed": False,
      })
    for ref in sorted(failed, key=lambda r: r.jid):
      outcomes.append({
          "ok": False,
          "jid": ref.jid,
          "metrics_s": per_metrics,
          "prewarm_s": 0.0,
          "telemetry": dict(telem),
          "_batch_exception": True,
          "_fallback_failed": True,
      })
    return outcomes
  metrics_elapsed = time.monotonic() - t_batch
  n = max(1, len(job_refs))
  per_metrics = metrics_elapsed / n
  telem = _empty_jid_outcome_telemetry()
  if skip_prewarm:
    ordered = sorted(job_refs, key=lambda r: r.jid)
    return [{
        "ok": True,
        "jid": r.jid,
        "metrics_s": per_metrics,
        "prewarm_s": 0.0,
        "telemetry": dict(telem),
        "_batch_exception": False,
        "_fallback_failed": False,
    } for r in ordered]

  t_prewarm = time.monotonic()
  for job_ref in job_refs:
    if shutdown_requested[0]:
      break
    prewarm_pipeline.submit(job_ref.jid)
  while prewarm_pipeline.has_pending():
    if shutdown_requested[0]:
      break
    prewarm_pipeline.drain_some()
  prewarm_elapsed = time.monotonic() - t_prewarm
  per_prewarm = prewarm_elapsed / n
  ordered = sorted(job_refs, key=lambda r: r.jid)
  return [{
      "ok": True,
      "jid": r.jid,
      "metrics_s": per_metrics,
      "prewarm_s": per_prewarm,
      "telemetry": dict(telem),
      "_batch_exception": False,
      "_fallback_failed": False,
  } for r in ordered]


def update_metrics_for_dates(dates, rerun=False):
  """Global scheduler across dates to keep workers saturated."""
  global LAST_UPDATE_METRICS_DIAGNOSTICS
  LAST_UPDATE_METRICS_DIAGNOSTICS = None
  close_old_connections()
  min_time = 300
  phase_timer = _PhaseTimer()
  scheduler_mode = cfg.get_metrics_scheduler_mode()
  prefetch_target = cfg.get_metrics_scheduler_ready_queue_target()
  prefetch_chunks = cfg.get_metrics_scheduler_prefetch_chunks()
  stats = _new_scheduler_stats()

  def _run():
    nonlocal phase_timer, stats
    # ``run_with_db_retry`` may invoke ``_run`` again; reset diagnostics per attempt
    # so retries report one scheduler pass, not cumulative counters.
    phase_timer = _PhaseTimer()
    stats = _new_scheduler_stats()
    with _pg_session_statement_timeout_for_metrics_batch():
      metrics_manager = metrics.Metrics()
      prewarm_pipeline = _PrewarmPipeline()
      ready_queue = deque()
      rescan_lock = threading.Lock()
      rescan_candidate_jids = deque()
      rescan_seen_jids = set()
      rescan_stop_event = threading.Event()
      prefetch_ready_cap = max(
          1, min(prefetch_target, prefetch_chunks * CHUNK_SIZE)
      )
      readiness_probe_target = {
          "value": max(READINESS_PROBE_TARGET_MIN, min(prefetch_ready_cap, CHUNK_SIZE))
      }
      strict_check_state = {
          "batch_size": STRICT_CHECK_BATCH_MIN,
          "max_batch_size": max(STRICT_CHECK_BATCH_MIN, min(prefetch_ready_cap, CHUNK_SIZE)),
      }
      strict_check_cooldown_until = {}
      scheduler_shared_lock = threading.Lock()
      completion_reporter = _CompletionReporter()

      def _extra_stats_snapshot():
        with scheduler_shared_lock:
          return {
              "strict_batch_size_current": stats["strict_batch_size_current"],
              "strict_check_calls": stats["strict_check_calls"],
              "strict_check_timeouts": stats["strict_check_timeouts"],
              "strict_check_avg_latency_ms": stats["strict_check_avg_latency_ms"],
              "proxy_not_ready_jids": stats["proxy_not_ready_jids"],
              "strict_not_ready_jids": stats["strict_not_ready_jids"],
              "strict_ready_jids": stats["strict_ready_jids"],
              "strict_cooldown_skips": stats["strict_cooldown_skips"],
              "deferred_not_ready_queue_size": stats["deferred_not_ready_queue_size"],
              "deferred_not_ready_due_now": stats["deferred_not_ready_due_now"],
              "deferred_quarantined_jids": stats["deferred_quarantined_jids"],
              "ready_enqueued_total": stats["ready_enqueued_total"],
              "ready_dequeued_total": stats["ready_dequeued_total"],
              "inflight_jids": stats["inflight_jids"],
              "compute_batches_total": stats["compute_batches_total"],
              "batch_compute_exceptions_total": stats["batch_compute_exceptions_total"],
              "per_jid_fallback_failures_total": stats["per_jid_fallback_failures_total"],
              "attempted_total": stats["attempted_total"],
          }

      completion_reporter.set_extra_stats_getter(_extra_stats_snapshot)
      completion_reporter.start()
      batch_cap = min(prefetch_target, GLOBAL_SCHEDULER_BATCH_SIZE)
      effective_batch_cap = max(COMPUTE_BATCH_MIN_CAP, int(batch_cap))
      log_print(
          "Starting metrics scheduler days={0} mode={1} prefetch_ready_cap={2} "
          "compute_batch_cap={3} prewarm_mode={4}".format(
              len(dates),
              scheduler_mode,
              prefetch_ready_cap,
              effective_batch_cap,
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
          strict_check_state=strict_check_state,
          strict_check_cooldown_until=strict_check_cooldown_until,
          phase_timer=phase_timer,
          stats=stats,
          completion_reporter=completion_reporter,
          scheduler_shared_lock=scheduler_shared_lock,
          rescan_candidate_jids=rescan_candidate_jids,
          rescan_seen_jids=rescan_seen_jids,
          rescan_lock=rescan_lock,
      )
      rescan_thread = _start_candidate_rescan_thread(
          dates=dates,
          min_time=min_time,
          rerun=rerun,
          rescan_candidate_jids=rescan_candidate_jids,
          rescan_seen_jids=rescan_seen_jids,
          rescan_lock=rescan_lock,
          stop_event=rescan_stop_event,
      )
      t0 = time.monotonic()
      compute_batches = 0
      stall_iters = 0
      telemetry_enabled = _metrics_telemetry_enabled()
      telemetry_metrics_samples = []
      telemetry_prewarm_samples = []
      telemetry_first_jid_s = None
      telemetry_plot_row_lookup_queries = 0
      telemetry_plot_row_lookup_hits = 0
      telemetry_plot_jt_memo_host_time_hits = 0
      telemetry_plot_jt_memo_aggregate_hits = 0
      telemetry_plot_jt_memo_aggregate_misses = 0
      telemetry_detail_fsio_metrics_reused = 0
      telemetry_detail_gpu_metrics_reused = 0
      telemetry_detail_fsio_fallback_queries = 0
      telemetry_detail_gpu_fallback_queries = 0
      try:
        while not shutdown_requested[0]:
          with ready_queue_lock:
            if ready_queue:
              jobs_this_round = []
              while ready_queue and len(jobs_this_round) < effective_batch_cap:
                jobs_this_round.append(ready_queue.popleft())
            else:
              jobs_this_round = []
          with scheduler_shared_lock:
            if jobs_this_round:
              stats["ready_dequeued_total"] += len(jobs_this_round)
              stats["inflight_jids"] += len(jobs_this_round)
          if not jobs_this_round:
            if producer_done.is_set():
              break
            stall_iters += 1
            if stall_iters == 1 or stall_iters % STALL_WARNING_EVERY_PASSES == 0:
              pending_days = sum(1 for s in date_states if not s["done"]) if not producer_done.is_set() else 0
              with scheduler_shared_lock:
                cand = stats["candidate_jids"]
                skipped = stats["skipped_not_ready"]
                proxy_not_ready = stats["proxy_not_ready_jids"]
                strict_not_ready = stats["strict_not_ready_jids"]
                deferred_q = stats["deferred_not_ready_queue_size"]
                deferred_due = stats["deferred_not_ready_due_now"]
                strict_ready = stats["strict_ready_jids"]
                enq = stats["ready_enqueued_total"]
                deq = stats["ready_dequeued_total"]
                inflight = stats["inflight_jids"]
                if inflight > 0 and not stats.get("stall_reason"):
                  stats["stall_reason"] = "compute_stuck_inflight"
              log_print(
                  "metrics scheduler: no ready jobs yet "
                  "(pending_days={0} candidate_jids={1} skipped_not_ready={2} "
                  "proxy_not_ready_jids={3} strict_not_ready_jids={4} strict_ready_jids={5} "
                  "deferred_not_ready_queue_size={6} deferred_not_ready_due_now={7} "
                  "ready_enqueued_total={8} ready_dequeued_total={9} inflight_jids={10} stall_pass={11}); "
                  "still scanning candidates.".format(
                      pending_days,
                      cand,
                      skipped,
                      proxy_not_ready,
                      strict_not_ready,
                      strict_ready,
                      deferred_q,
                      deferred_due,
                      enq,
                      deq,
                      inflight,
                      stall_iters,
                  ),
                  flush=True,
              )
            time.sleep(0.05)
            continue
          stall_iters = 0
          job_refs = _job_refs_from_jids(jobs_this_round)
          log_print(
              "metrics scheduler: compute batch starting size={0} inflight_jids={1} batch_cap={2}".format(
                  len(job_refs),
                  len(job_refs),
                  effective_batch_cap,
              ),
              flush=True,
          )
          batch_start = time.monotonic()
          with phase_timer.phase("metrics_compute_s"):
            succeeded_jids = []
            failed_count = 0
            jid_outcomes = _compute_jid_outcomes_batch(
                job_refs,
                metrics_manager,
                prewarm_pipeline,
                shared_pool,
            )
            for jid_outcome in jid_outcomes:
              if jid_outcome["ok"]:
                succeeded_jids.append(jid_outcome["jid"])
                if telemetry_enabled:
                  if len(telemetry_metrics_samples) < TELEMETRY_SAMPLE_LIMIT:
                    telemetry_metrics_samples.append(float(jid_outcome["metrics_s"]))
                  if len(telemetry_prewarm_samples) < TELEMETRY_SAMPLE_LIMIT:
                    telemetry_prewarm_samples.append(float(jid_outcome["prewarm_s"]))
                  if telemetry_first_jid_s is None:
                    telemetry_first_jid_s = max(0.0, time.monotonic() - t0)
                  tmap = jid_outcome.get("telemetry", {})
                  telemetry_plot_row_lookup_queries += int(
                      tmap.get("plot_row_lookup_queries", 0)
                  )
                  telemetry_plot_row_lookup_hits += int(
                      tmap.get("plot_row_lookup_hits", 0)
                  )
                  telemetry_plot_jt_memo_host_time_hits += int(
                      tmap.get("plot_jt_memo_host_time_hits", 0)
                  )
                  telemetry_plot_jt_memo_aggregate_hits += int(
                      tmap.get("plot_jt_memo_aggregate_hits", 0)
                  )
                  telemetry_plot_jt_memo_aggregate_misses += int(
                      tmap.get("plot_jt_memo_aggregate_misses", 0)
                  )
                  telemetry_detail_fsio_metrics_reused += int(
                      tmap.get("detail_fsio_metrics_reused", 0)
                  )
                  telemetry_detail_gpu_metrics_reused += int(
                      tmap.get("detail_gpu_metrics_reused", 0)
                  )
                  telemetry_detail_fsio_fallback_queries += int(
                      tmap.get("detail_fsio_fallback_queries", 0)
                  )
                  telemetry_detail_gpu_fallback_queries += int(
                      tmap.get("detail_gpu_fallback_queries", 0)
                  )
              else:
                failed_count += 1
          batch_elapsed = time.monotonic() - batch_start
          has_batch_exception = any(bool(o.get("_batch_exception")) for o in jid_outcomes)
          fallback_failed = sum(1 for o in jid_outcomes if bool(o.get("_fallback_failed")))
          with scheduler_shared_lock:
            stats["processed"] += len(succeeded_jids)
            stats["failed"] += failed_count
            stats["attempted_total"] = stats["processed"] + stats["failed"]
            stats["compute_batches_total"] += 1
            if has_batch_exception:
              stats["batch_compute_exceptions_total"] += 1
            stats["per_jid_fallback_failures_total"] += fallback_failed
            stats["inflight_jids"] = max(0, stats["inflight_jids"] - len(job_refs))
            proc_total = stats["processed"]
            fail_total = stats["failed"]
            attempted_total = stats["attempted_total"]
            if attempted_total > 0 and proc_total == 0 and fail_total > 0:
              stats["stall_reason"] = "compute_all_failed"
          completion_reporter.sync_completed_total(proc_total)
          compute_batches += 1
          if batch_elapsed >= COMPUTE_BATCH_WATCHDOG_SECONDS:
            new_cap = max(
                COMPUTE_BATCH_MIN_CAP,
                int(max(COMPUTE_BATCH_MIN_CAP, effective_batch_cap) * COMPUTE_BATCH_DOWNSHIFT_FACTOR),
            )
            if new_cap < effective_batch_cap:
              effective_batch_cap = new_cap
            log_print(
                "metrics scheduler: compute watchdog elapsed_s={0:.1f} size={1} attempted_total={2} "
                "new_compute_batch_cap={3}".format(
                    batch_elapsed,
                    len(job_refs),
                    attempted_total,
                    effective_batch_cap,
                ),
                flush=True,
            )
          elif len(job_refs) >= effective_batch_cap and fail_total == 0 and batch_elapsed < 5.0:
            effective_batch_cap = min(batch_cap, effective_batch_cap + COMPUTE_BATCH_UPSHIFT_STEP)
          if compute_batches == 1 or compute_batches % 25 == 0:
            log_print(
                "metrics scheduler: compute batch {0} size={1} "
                "processed_total={2} failed_total={3} attempted_total={4} "
                "batch_compute_exceptions_total={5} per_jid_fallback_failures_total={6} "
                "compute_batch_elapsed_s={7:.2f} next_batch_cap={8}".format(
                    compute_batches,
                    len(job_refs),
                    proc_total,
                    fail_total,
                    attempted_total,
                    int(has_batch_exception),
                    fallback_failed,
                    batch_elapsed,
                    effective_batch_cap,
                ),
                flush=True,
            )
          with phase_timer.phase("prewarm_s"):
            pass
          if (
              GC_COLLECT_EVERY_N_CHUNKS > 0
              and compute_batches % GC_COLLECT_EVERY_N_CHUNKS == 0
              and gc.get_count()[0] > 10000
          ):
            gc.collect()
      finally:
        rescan_stop_event.set()
        producer_done.set()
        producer.join(timeout=2.0)
        if rescan_thread is not None:
          rescan_thread.join(timeout=2.0)
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
      telemetry_suffix = ""
      if telemetry_enabled and telemetry_metrics_samples:
        sm = sorted(telemetry_metrics_samples)
        sp = sorted(telemetry_prewarm_samples) if telemetry_prewarm_samples else [0.0]
        p50m = sm[len(sm) // 2]
        p95m = sm[max(0, int(len(sm) * 0.95) - 1)]
        p50p = sp[len(sp) // 2]
        p95p = sp[max(0, int(len(sp) * 0.95) - 1)]
        telemetry_suffix = (
            " telemetry_enabled=1 telemetry_first_jid_s={0:.3f} "
            "telemetry_metrics_p50_s={1:.3f} telemetry_metrics_p95_s={2:.3f} "
            "telemetry_prewarm_p50_s={3:.3f} telemetry_prewarm_p95_s={4:.3f} "
            "telemetry_plot_row_lookup_queries={5} telemetry_plot_row_lookup_hits={6} "
            "telemetry_plot_jt_host_time_hits={7} telemetry_plot_jt_aggregate_hits={8} "
            "telemetry_plot_jt_aggregate_misses={9} telemetry_detail_fsio_metrics_reused={10} "
            "telemetry_detail_gpu_metrics_reused={11} telemetry_detail_fsio_fallback_queries={12} "
            "telemetry_detail_gpu_fallback_queries={13}".format(
                float(telemetry_first_jid_s or 0.0),
                float(p50m),
                float(p95m),
                float(p50p),
                float(p95p),
                int(telemetry_plot_row_lookup_queries),
                int(telemetry_plot_row_lookup_hits),
                int(telemetry_plot_jt_memo_host_time_hits),
                int(telemetry_plot_jt_memo_aggregate_hits),
                int(telemetry_plot_jt_memo_aggregate_misses),
                int(telemetry_detail_fsio_metrics_reused),
                int(telemetry_detail_gpu_metrics_reused),
                int(telemetry_detail_fsio_fallback_queries),
                int(telemetry_detail_gpu_fallback_queries),
            )
        )
      with scheduler_shared_lock:
        snap = {
            "processed": stats["processed"],
            "failed": stats["failed"],
            "candidate_jids": stats["candidate_jids"],
            "skipped_not_ready": stats["skipped_not_ready"],
            "readiness_error_chunks": stats["readiness_error_chunks"],
            "proxy_checked_chunks": stats["proxy_checked_chunks"],
            "proxy_rejected_jids": stats["proxy_rejected_jids"],
            "proxy_not_ready_jids": stats["proxy_not_ready_jids"],
            "strict_not_ready_jids": stats["strict_not_ready_jids"],
            "strict_ready_jids": stats["strict_ready_jids"],
            "strict_cooldown_skips": stats["strict_cooldown_skips"],
            "deferred_not_ready_queue_size": stats["deferred_not_ready_queue_size"],
            "deferred_not_ready_due_now": stats["deferred_not_ready_due_now"],
            "deferred_quarantined_jids": stats["deferred_quarantined_jids"],
            "stall_exit_triggered": stats["stall_exit_triggered"],
            "stall_reason": stats["stall_reason"],
            "ready_enqueued_total": stats["ready_enqueued_total"],
            "ready_dequeued_total": stats["ready_dequeued_total"],
            "inflight_jids": stats["inflight_jids"],
            "compute_batches_total": stats["compute_batches_total"],
            "batch_compute_exceptions_total": stats["batch_compute_exceptions_total"],
            "per_jid_fallback_failures_total": stats["per_jid_fallback_failures_total"],
            "attempted_total": stats["attempted_total"],
            "readiness_probe_value": readiness_probe_target["value"],
            "strict_batch_size_current": stats["strict_batch_size_current"],
            "strict_check_calls": stats["strict_check_calls"],
            "strict_check_timeouts": stats["strict_check_timeouts"],
            "strict_check_avg_latency_ms": stats["strict_check_avg_latency_ms"],
        }
      log_print(
          "Finished metrics scheduler mode={0}: processed={1} failed={2} "
          "candidate_jids={3} skipped_not_ready={4} readiness_error_chunks={5} "
          "proxy_checked_chunks={6} proxy_rejected_jids={7} proxy_not_ready_jids={8} "
          "strict_not_ready_jids={9} strict_ready_jids={10} strict_cooldown_skips={11} "
          "deferred_not_ready_queue_size={12} deferred_not_ready_due_now={13} "
          "deferred_quarantined_jids={14} stall_exit_triggered={15} stall_reason={16} "
          "ready_enqueued_total={17} ready_dequeued_total={18} inflight_jids={19} "
          "compute_batches_total={20} batch_compute_exceptions_total={21} "
          "per_jid_fallback_failures_total={22} attempted_total={23} readiness_probe_target={24} "
          "strict_batch_size_current={25} strict_check_calls={26} strict_check_timeouts={27} "
          "strict_check_avg_latency_ms={28:.2f} completed_last_hour={29} elapsed_s={30:.2f} jobs_per_min={31:.2f} "
          "worker_busy_ratio={32:.3f} phase_candidate_s={33:.2f} "
          "phase_readiness_s={34:.2f} phase_compute_s={35:.2f} phase_prewarm_s={36:.2f} "
          "prewarm_backlog_jobs={37} prewarm_lag_seconds_p95={38:.3f} "
          "prewarm_success_ratio={39:.3f}{40}".format(
              scheduler_mode,
              snap["processed"],
              snap["failed"],
              snap["candidate_jids"],
              snap["skipped_not_ready"],
              snap["readiness_error_chunks"],
              snap["proxy_checked_chunks"],
              snap["proxy_rejected_jids"],
              snap["proxy_not_ready_jids"],
              snap["strict_not_ready_jids"],
              snap["strict_ready_jids"],
              snap["strict_cooldown_skips"],
              snap["deferred_not_ready_queue_size"],
              snap["deferred_not_ready_due_now"],
              snap["deferred_quarantined_jids"],
              snap["stall_exit_triggered"],
              snap["stall_reason"] or "n/a",
              snap["ready_enqueued_total"],
              snap["ready_dequeued_total"],
              snap["inflight_jids"],
              snap["compute_batches_total"],
              snap["batch_compute_exceptions_total"],
              snap["per_jid_fallback_failures_total"],
              snap["attempted_total"],
              snap["readiness_probe_value"],
              snap["strict_batch_size_current"],
              snap["strict_check_calls"],
              snap["strict_check_timeouts"],
              snap["strict_check_avg_latency_ms"],
              completion_reporter.completed_in_window(),
              elapsed,
              (snap["processed"] * 60.0) / elapsed,
              worker_busy_ratio,
              totals["candidate_sql_s"],
              totals["readiness_s"],
              totals["metrics_compute_s"],
              totals["prewarm_s"],
              prewarm_stats["prewarm_backlog_jobs"],
              prewarm_stats["prewarm_lag_seconds_p95"],
              prewarm_stats["prewarm_success_ratio"],
              telemetry_suffix,
          ),
          flush=True,
      )

      if os.environ.get(
          "HPCPERFSTATS_UPDATE_METRICS_RETURN_DIAGNOSTICS", ""
      ).strip().lower() in ("1", "yes", "true", "on"):
        global LAST_UPDATE_METRICS_DIAGNOSTICS
        LAST_UPDATE_METRICS_DIAGNOSTICS = {
            "phase_totals": dict(totals),
            "stats": dict(snap),
            "elapsed_s": elapsed,
            "jobs_per_min": (snap["processed"] * 60.0) / elapsed,
            "prewarm_stats": dict(prewarm_stats),
            "scheduler_mode": scheduler_mode,
        }

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

  If ``sleep_after`` is true, the function sleeps 60s at the end (legacy
  supervisor loop). Default is true when ``sleep_after`` is omitted.
  Environment variable ``HPCPERFSTATS_UPDATE_METRICS_MAIN_SLEEP_AFTER`` can
  override the default: ``0``/``no``/``false`` disable sleep and
  ``1``/``yes``/``true`` enable it.

  Dates in the parsed range are processed **newest day first**; see module
  docstring for per-day job order.
  """
  if argv is None:
    argv = sys.argv

  if sleep_after is None:
    env_sleep_after = os.environ.get(
        "HPCPERFSTATS_UPDATE_METRICS_MAIN_SLEEP_AFTER", ""
    ).strip().lower()
    if env_sleep_after in ("0", "no", "false"):
      sleep_after = False
    elif env_sleep_after in ("1", "yes", "true"):
      sleep_after = True
    else:
      sleep_after = True

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
    sleep_until_shutdown(600)


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
