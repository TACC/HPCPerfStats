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

The global scheduler (**``update_metrics_for_dates``**) first finishes all
`/pub/` expansion-factor aggregate artifacts using a metrics-sized process pool
(one calendar month or one calendar year per worker task), then resets worker
processes and begins job readiness checks plus ``Metrics.run`` batches.

"""
import contextlib
import functools
import gc
import os
import threading
import signal
import sys
import time
import traceback
from dataclasses import dataclass, field
from types import SimpleNamespace
from datetime import datetime, timedelta
from collections import defaultdict, deque
from concurrent.futures import CancelledError, FIRST_COMPLETED, ThreadPoolExecutor, wait
from hpcperfstats.django_bootstrap import ensure_django

ensure_django()

UPDATE_METRICS_PROCESS_TITLE = "update_metrics.py"

from django.db import close_old_connections, connections, transaction
from django.utils import timezone as django_timezone
from django.db.models import BooleanField, Case, Count, Exists, F, IntegerField, Max, Min, OuterRef, Q, Subquery, Value, When
from django.db.models.query import QuerySet
from django.db.models.functions import Coalesce
from django.db.utils import OperationalError, DatabaseError

import hpcperfstats.conf_parser as cfg
from hpcperfstats.analysis.metrics import metrics
from hpcperfstats.analysis.metrics.live_host_sample_count import (
    live_distinct_host_time_count_expression,
)
from hpcperfstats.analysis.metrics.metrics import expected_job_metric_row_count
from hpcperfstats.analysis.metrics.db_retry import run_with_db_retry
from hpcperfstats.dbload.db_unavailable import (
    DatabaseUnavailableExit,
    is_database_unavailable_error,
    log_and_raise_database_unavailable,
)
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
from hpcperfstats.site.machine.public_metrics_artifacts import (
    refresh_public_expansion_factor_artifacts_parallel,
    refresh_public_expansion_factor_artifacts_safe,
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
# When rescan loops add no new jids, sleep backs off (capped) to cut duplicate SQL.
RESCAN_IDLE_INTERVAL_MAX_SECONDS = 60.0
STALL_WARNING_EVERY_PASSES = 200
STALL_EXIT_AFTER_SECONDS = 900.0
# User-facing stall_reason strings (docs/TESTING.md); producer sets no_ready_candidates;
# consumer sets compute_* / worker_* after sustained zero processed progress.
DOCUMENTED_SCHEDULER_STALL_REASONS = frozenset({
    "no_ready_candidates",
    "compute_stuck_inflight",
    "compute_all_failed",
})
CONSUMER_STALL_EXIT_REASONS = frozenset({
    "compute_stuck_inflight",
    "compute_all_failed",
    "worker_failed_outcomes",
    "parent_persist_failed",
})
COMPUTE_BATCH_MIN_CAP = 16
COMPUTE_BATCH_DOWNSHIFT_FACTOR = 0.5
COMPUTE_BATCH_UPSHIFT_STEP = 16
COMPUTE_BATCH_WORKER_MULTIPLIER = 2
COMPUTE_BATCH_ABSOLUTE_MAX = 64
RESCAN_FETCH_LIMIT = CHUNK_SIZE
TELEMETRY_SAMPLE_LIMIT = 2048
RESCAN_SEEN_MULTIPLIER = 16
RESCAN_SEEN_MIN_CAP = 4096
STALL_RECOVERY_PER_JID_TIMEOUT_SECONDS = 60.0
STALL_RECOVERY_PER_JID_POLL_TIMEOUT_SECONDS = 2.0
STALL_RECOVERY_MAX_WALL_SECONDS = 300.0
PREWARM_FUTURE_RESULT_TIMEOUT_SECONDS = 60.0
PREWARM_DRAIN_MAX_WALL_SECONDS = 300.0
PREWARM_FINISH_MAX_WALL_SECONDS = 300.0
PREWARM_FINISH_WAIT_SLICE_SECONDS = 0.25
PUBLIC_EF_PHASE_POLL_TIMEOUT_SECONDS = float(
    os.environ.get("HPCPERFSTATS_PUBLIC_EF_PHASE_POLL_TIMEOUT_S", "5.0")
)
PUBLIC_EF_PHASE_NO_PROGRESS_TIMEOUT_SECONDS = float(
    os.environ.get("HPCPERFSTATS_PUBLIC_EF_PHASE_NO_PROGRESS_TIMEOUT_S", "120.0")
)
STRICT_READINESS_DB_TIMEOUT_MS = int(
    os.environ.get("HPCPERFSTATS_STRICT_READINESS_DB_TIMEOUT_MS", "120000")
)
STRICT_READINESS_DB_LOCK_TIMEOUT_MS = int(
    os.environ.get("HPCPERFSTATS_STRICT_READINESS_DB_LOCK_TIMEOUT_MS", "10000")
)

# Non-zero exit so supervisord ``autorestart`` replaces a wedged scheduler pass.
METRICS_SCHEDULER_STALL_EXIT_CODE = 1


class MetricsSchedulerStallExit(BaseException):
  """Scheduler hit ``STALL_EXIT_AFTER_SECONDS`` with no ready progress; restart."""

  exit_code = METRICS_SCHEDULER_STALL_EXIT_CODE

  def __init__(self, *, stall_reason=None):
    self.stall_reason = stall_reason or "unknown"
    super().__init__(
        "metrics scheduler stalled ({0})".format(self.stall_reason)
    )


def _maybe_trigger_consumer_stall_exit(stats, consumer_stall_since, scheduler_shared_lock):
  """Set ``stall_exit_triggered`` when compute-stage stall persists with zero processed."""
  if consumer_stall_since is None:
    return False
  if time.monotonic() - consumer_stall_since < STALL_EXIT_AFTER_SECONDS:
    return False
  with scheduler_shared_lock:
    if int(stats["processed"]) != 0:
      return False
    reason = stats.get("stall_reason") or ""
    if reason not in CONSUMER_STALL_EXIT_REASONS:
      return False
    stats["stall_exit_triggered"] = 1
  return True


def _job_window_runtime_seconds(start_time, end_time):
  """Return job accounting-window length in seconds, or None if not computable."""
  if start_time is None or end_time is None:
    return None
  try:
    return max(0.0, (end_time - start_time).total_seconds())
  except Exception:
    return None


def _effective_prewarm_drain_batch_budget_s(n_successful):
  """Scaled time budget for draining async prewarm after each metrics batch."""
  n = max(0, int(n_successful))
  base = float(cfg.get_metrics_prewarm_drain_batch_budget_base_s())
  per_job = float(cfg.get_metrics_prewarm_drain_budget_per_successful_job_s())
  ceiling = float(cfg.get_metrics_prewarm_drain_batch_budget_max_s())
  raw = base + float(n) * per_job
  if ceiling <= 0.0:
    return max(0.0, raw)
  return max(0.0, min(ceiling, raw))


def _batch_window_cost_pair_for_ref(ref):
  """Return (sum_budget_delta, per_job_runtime_for_max_cap) for cost-aware batching."""
  if bool(getattr(ref, "artifact_only", False)):
    return 0.0, 0.0
  rt = getattr(ref, "runtime_s", None)
  if rt is None:
    rt = float(cfg.get_metrics_compute_batch_unknown_runtime_seconds())
  else:
    rt = float(rt)
  return rt, rt


def _pop_candidates_for_compute_batch_locked(ready_queue, cap):
  """Pop up to ``cap`` candidates using optional window / per-job runtime caps (0 = off)."""
  max_window = float(cfg.get_metrics_compute_batch_max_window_seconds())
  max_single = float(cfg.get_metrics_compute_batch_max_single_job_runtime_seconds())
  out = []
  sum_w = 0.0
  max_rt = 0.0
  while ready_queue and len(out) < cap:
    ref = ready_queue[0]
    add_sum, add_max = _batch_window_cost_pair_for_ref(ref)
    new_sum = sum_w + add_sum
    new_max_rt = max(max_rt, add_max)
    if max_window > 0.0 and new_sum > max_window and out:
      break
    if max_window > 0.0 and new_sum > max_window and not out:
      out.append(ready_queue.popleft())
      break
    if max_single > 0.0 and new_max_rt > max_single and out:
      break
    if max_single > 0.0 and new_max_rt > max_single and not out:
      out.append(ready_queue.popleft())
      break
    out.append(ready_queue.popleft())
    sum_w = new_sum
    max_rt = new_max_rt
  return out


def _chunk_rows_to_candidate_refs(rows):
  """Map queryset ``values_list`` rows to candidate refs (supports test stubs)."""
  if not rows:
    return []
  first = rows[0]
  if not isinstance(first, (tuple, list)):
    return [_candidate_ref(row) for row in rows]
  refs = []
  for row in rows:
    n = len(row)
    if n >= 4:
      jid, end_time, start_time, artifact_only = row[0], row[1], row[2], row[3]
      refs.append(_candidate_ref(
          jid,
          bool(artifact_only),
          runtime_s=_job_window_runtime_seconds(start_time, end_time),
      ))
    elif n == 3:
      jid, end_time, artifact_only = row[0], row[1], row[2]
      refs.append(_candidate_ref(
          jid,
          bool(artifact_only),
          runtime_s=_job_window_runtime_seconds(None, end_time),
      ))
    elif n == 2:
      jid, artifact_only = row[0], row[1]
      refs.append(_candidate_ref(jid, bool(artifact_only)))
    else:
      refs.append(_candidate_ref(row[0]))
  return refs


def _add_bounded_seen_jid(seen_set, seen_order, jid, cap):
  """Insert jid into seen structures, evicting oldest entries past cap."""
  if jid in seen_set:
    return False
  seen_set.add(jid)
  seen_order.append(jid)
  limit = max(1, int(cap))
  while len(seen_order) > limit:
    old = seen_order.popleft()
    seen_set.discard(old)
  return True


def _merge_deferred_retry_at(existing_retry_at, candidate_retry_at):
  """Choose retry timestamp without pushing an existing defer farther out."""
  if existing_retry_at is None:
    return float(candidate_retry_at)
  return min(float(existing_retry_at), float(candidate_retry_at))


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
      "worker_failed_outcomes_total": 0,
      "parent_persist_failures_total": 0,
      "attempted_total": 0,
      "public_ef_degraded": 0,
      "public_ef_worker_exceptions_total": 0,
      "public_ef_watchdog_timeouts_total": 0,
      "public_ef_pending_tasks": 0,
      "strict_check_calls": 0,
      "strict_check_timeouts": 0,
      "strict_check_avg_latency_ms": 0.0,
      "strict_batch_size_current": STRICT_CHECK_BATCH_MIN,
  }


def _log_exception_details(prefix, exc):
  """Emit one-line-per-frame diagnostics for collectors that drop multiline logs."""
  log_print(
      "{0}: exception_type={1} exception_repr={2!r}".format(
          prefix, type(exc).__name__, exc
      ),
      flush=True,
  )
  tb_lines = traceback.format_exception(type(exc), exc, exc.__traceback__)
  for raw in tb_lines:
    for line in str(raw).splitlines():
      if line.strip():
        log_print("{0}: traceback {1}".format(prefix, line), flush=True)
  cause = getattr(exc, "__cause__", None)
  if cause is not None:
    log_print(
        "{0}: cause_type={1} cause_repr={2!r}".format(
            prefix, type(cause).__name__, cause
        ),
        flush=True,
    )
  context = getattr(exc, "__context__", None)
  if context is not None and context is not cause:
    log_print(
        "{0}: context_type={1} context_repr={2!r}".format(
            prefix, type(context).__name__, context
        ),
        flush=True,
    )


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
        "public_ef_artifacts_s": 0.0,
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
    self._backlog_cap = max(self._workers, cfg.get_metrics_prewarm_backlog_cap())
    self._backpressure_wait_s = cfg.get_metrics_prewarm_backpressure_wait_s()
    self._counters_lock = threading.Lock()
    self._pending_lock = threading.Lock()
    self._executor = None
    self._pending = set()
    self._pending_jids = {}
    self._done = 0
    self._failed = 0
    self._backpressure_events = 0
    self._inline_fallback_jobs = 0
    self._evicted_pending_jobs = 0
    self._lag_samples = deque(maxlen=TELEMETRY_SAMPLE_LIMIT)
    self._created_at = {}
    self._last_backpressure_log_at = 0.0
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
    from hpcperfstats.process_title import set_daemon_thread_title

    set_daemon_thread_title(
        "",
        script_name=UPDATE_METRICS_PROCESS_TITLE,
        role="prewarm-pool",
    )
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

  def _oldest_pending_age_locked(self, now=None):
    if not self._pending:
      return 0.0
    if now is None:
      now = time.monotonic()
    oldest_start = None
    for fut in self._pending:
      start = self._created_at.get(fut, now)
      if oldest_start is None or start < oldest_start:
        oldest_start = start
    if oldest_start is None:
      return 0.0
    return max(0.0, now - oldest_start)

  def _maybe_log_backpressure(self, *, jid, pending, oldest_age_s, action):
    now = time.monotonic()
    if action == "drain_wait" and ((now - self._last_backpressure_log_at) < 5.0):
      return
    self._last_backpressure_log_at = now
    log_print(
        "plot artifact prewarm backlog pressure pending={0} cap={1} "
        "oldest_pending_age_s={2:.3f} jid={3} action={4}".format(
            int(pending),
            int(self._backlog_cap),
            float(oldest_age_s),
            jid,
            action,
        ),
        flush=True,
    )

  def _run_inline_fallback(self, jid):
    try:
      self._run_one(jid)
      with self._counters_lock:
        self._done += 1
    except Exception as exc:
      with self._counters_lock:
        self._failed += 1
      log_print("plot artifact prewarm failed: {0}".format(exc))

  def _evict_oldest_pending_locked(self):
    """Cancel the oldest async prewarm task to make room (never blocks scheduler)."""
    if not self._pending:
      return None
    oldest_fut = None
    oldest_start = None
    for fut in self._pending:
      start = self._created_at.get(fut)
      if start is None:
        continue
      if oldest_start is None or start < oldest_start:
        oldest_start = start
        oldest_fut = fut
    if oldest_fut is None:
      return None
    evicted_jid = self._pending_jids.pop(oldest_fut, None)
    self._pending.discard(oldest_fut)
    self._created_at.pop(oldest_fut, None)
    try:
      oldest_fut.cancel()
    except Exception:
      pass
    with self._counters_lock:
      self._failed += 1
      self._evicted_pending_jobs += 1
    return evicted_jid

  def submit(self, jid):
    if self._mode == "inline":
      self._run_one(jid)
      with self._counters_lock:
        self._done += 1
      return
    with self._pending_lock:
      pending_now = len(self._pending)
      oldest_age_s = self._oldest_pending_age_locked()
    if pending_now >= self._backlog_cap:
      with self._counters_lock:
        self._backpressure_events += 1
      self._maybe_log_backpressure(
          jid=jid,
          pending=pending_now,
          oldest_age_s=oldest_age_s,
          action="drain_wait",
      )
      self.drain_some(wait_timeout_s=self._backpressure_wait_s)
      with self._pending_lock:
        pending_now = len(self._pending)
        oldest_age_s = self._oldest_pending_age_locked()
      if pending_now >= self._backlog_cap:
        with self._pending_lock:
          evicted_jid = self._evict_oldest_pending_locked()
          pending_now = len(self._pending)
          oldest_age_s = self._oldest_pending_age_locked()
        with self._counters_lock:
          self._backpressure_events += 1
        self._maybe_log_backpressure(
            jid=jid,
            pending=pending_now,
            oldest_age_s=oldest_age_s,
            action="evict_oldest",
        )
        if evicted_jid is not None:
          log_print(
              "plot artifact prewarm evicted oldest pending jid={0} for jid={1}".format(
                  evicted_jid,
                  jid,
              ),
              flush=True,
          )
    with self._pending_lock:
      if len(self._pending) >= self._backlog_cap:
        return
      fut = self._executor.submit(self._run_one, jid)
      self._created_at[fut] = time.monotonic()
      self._pending_jids[fut] = jid
      self._pending.add(fut)

  def has_pending(self):
    """True when async prewarm tasks are still running (``pipeline_required``)."""
    if self._mode == "inline" or self._executor is None:
      return False
    with self._pending_lock:
      return bool(self._pending)

  def drain_some(self, force=False, wait_timeout_s=0.0):
    if self._mode == "inline" or not self._pending:
      return
    with self._pending_lock:
      if not self._pending:
        return
      timeout_s = max(0.0, float(wait_timeout_s or 0.0))
      if timeout_s > 0.0 or not force:
        wait(
            self._pending,
            timeout=timeout_s,
            return_when=FIRST_COMPLETED,
        )
      done = {fut for fut in self._pending if fut.done()}
      if not done:
        return
      self._pending.difference_update(done)
    for fut in done:
      self._pending_jids.pop(fut, None)
      start = self._created_at.pop(fut, None)
      if start is not None:
        self._lag_samples.append(time.monotonic() - start)
      try:
        fut.result()
        with self._counters_lock:
          self._done += 1
      except CancelledError:
        with self._counters_lock:
          self._failed += 1
        log_print("plot artifact prewarm cancelled", flush=True)
      except Exception as exc:
        with self._counters_lock:
          self._failed += 1
        log_print("plot artifact prewarm failed: {0}".format(exc))

  def finish(self):
    if self._mode == "inline":
      return
    started = time.monotonic()
    deadline = started + PREWARM_FINISH_MAX_WALL_SECONDS
    while True:
      with self._pending_lock:
        has_pending = bool(self._pending)
      if not has_pending:
        break
      remaining_s = max(0.0, deadline - time.monotonic())
      if remaining_s <= 0.0:
        with self._pending_lock:
          oldest_age_s = self._oldest_pending_age_locked()
          timed_out_count = len(self._pending)
          for fut in list(self._pending):
            fut.cancel()
            self._created_at.pop(fut, None)
            self._pending_jids.pop(fut, None)
          self._pending = set()
          self._pending_jids = {}
        with self._counters_lock:
          self._failed += timed_out_count
        log_print(
            "plot artifact prewarm finish timeout after {0:.1f}s; cancelling pending={1} "
            "oldest_pending_age_s={2:.3f}".format(
                time.monotonic() - started,
                timed_out_count,
                oldest_age_s,
            ),
            flush=True,
        )
        break
      self.drain_some(
          force=True,
          wait_timeout_s=min(PREWARM_FINISH_WAIT_SLICE_SECONDS, remaining_s),
      )
    self._executor.shutdown(wait=False, cancel_futures=True)

  def stats(self):
    with self._pending_lock:
      backlog_jobs = len(self._pending)
      oldest_pending_age_s = self._oldest_pending_age_locked()
    with self._counters_lock:
      done = self._done
      failed = self._failed
      lag_samples = list(self._lag_samples)
      backpressure_events = self._backpressure_events
      inline_fallback_jobs = self._inline_fallback_jobs
      evicted_pending_jobs = self._evicted_pending_jobs
    total = done + failed
    p95_lag = 0.0
    if lag_samples:
      vals = sorted(lag_samples)
      p95_lag = vals[max(0, int(len(vals) * 0.95) - 1)]
    return {
        "prewarm_backlog_jobs": backlog_jobs,
        "prewarm_oldest_pending_age_s": round(oldest_pending_age_s, 3),
        "prewarm_lag_seconds_p95": round(p95_lag, 3),
        "prewarm_success_ratio": (float(done) / float(total)) if total else 1.0,
        "prewarm_done_jobs": done,
        "prewarm_failed_jobs": failed,
        "prewarm_backpressure_events": backpressure_events,
        "prewarm_inline_fallback_jobs": inline_fallback_jobs,
        "prewarm_evicted_pending_jobs": evicted_pending_jobs,
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
    from hpcperfstats.process_title import set_daemon_thread_title

    set_daemon_thread_title(
        "",
        script_name=UPDATE_METRICS_PROCESS_TITLE,
        role="completion-reporter",
    )
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
              "per_jid_fallback_failures_total={16} worker_failed_outcomes_total={17} "
              "parent_persist_failures_total={18} attempted_total={19} "
              "public_ef_degraded={20} public_ef_worker_exceptions_total={21} "
              "public_ef_watchdog_timeouts_total={22} public_ef_pending_tasks={23} "
              "prewarm_backlog_jobs={24} prewarm_oldest_pending_age_s={25:.3f} "
              "prewarm_backpressure_events={26} prewarm_inline_fallback_jobs={27}".format(
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
                  int(extra_map.get("worker_failed_outcomes_total", 0)),
                  int(extra_map.get("parent_persist_failures_total", 0)),
                  int(extra_map.get("attempted_total", 0)),
                  int(extra_map.get("public_ef_degraded", 0)),
                  int(extra_map.get("public_ef_worker_exceptions_total", 0)),
                  int(extra_map.get("public_ef_watchdog_timeouts_total", 0)),
                  int(extra_map.get("public_ef_pending_tasks", 0)),
                  int(extra_map.get("prewarm_backlog_jobs", 0)),
                  float(extra_map.get("prewarm_oldest_pending_age_s", 0.0)),
                  int(extra_map.get("prewarm_backpressure_events", 0)),
                  int(extra_map.get("prewarm_inline_fallback_jobs", 0)),
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
    try:
      with conn.cursor() as cursor:
        if restore_ms > 0:
          cursor.execute("SET statement_timeout = %s", [restore_ms])
        else:
          cursor.execute("SET statement_timeout = 0")
    except (OperationalError, DatabaseError):
      pass


def _today_datetime():
  """Local now for default date-range bounds (monkeypatch in tests)."""
  return datetime.today()


@contextlib.contextmanager
def _pg_local_readiness_timeouts():
  """Apply bounded DB and lock timeouts to strict-readiness probes."""
  conn = connections["default"]
  if conn.vendor != "postgresql":
    yield
    return
  using = getattr(conn, "alias", None) or "default"
  with transaction.atomic(using=using):
    with conn.cursor() as cursor:
      cursor.execute(
          "SET LOCAL statement_timeout = %s",
          [max(1, int(STRICT_READINESS_DB_TIMEOUT_MS))],
      )
      cursor.execute(
          "SET LOCAL lock_timeout = %s",
          [max(1, int(STRICT_READINESS_DB_LOCK_TIMEOUT_MS))],
      )
      cursor.execute("SET LOCAL max_parallel_workers_per_gather = 0")
    yield


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


def _end_time_calendar_day_half_open_bounds(sched_date):
  """Return ``[start, end)`` aware datetimes for jobs whose ``end_time`` is that calendar day.

  Mirrors ``DateTimeField`` ``__date`` lookup semantics in the default timezone
  while keeping a plain range on ``end_time`` so btree indexes apply.

  Naive ``datetime`` values use their ``.date()`` component (same as the prior
  ``end_time__date=sched_date.date()`` filter). Timezone-aware values use
  ``localtime`` so the calendar day matches the active/default zone.
  """
  if isinstance(sched_date, datetime):
    if django_timezone.is_naive(sched_date):
      day = sched_date.date()
    else:
      day = django_timezone.localtime(sched_date).date()
  else:
    day = sched_date
  start = django_timezone.make_aware(
      datetime.combine(day, datetime.min.time())
  )
  end = start + timedelta(days=1)
  return start, end


def _jobs_queryset(date, min_time, rerun):
  """Jobs ending on ``date`` with runtime >= min_time, newest first (end_time, jid)."""
  day_lo, day_hi = _end_time_calendar_day_half_open_bounds(date)
  qs = job_data.objects.filter(
      end_time__gte=day_lo,
      end_time__lt=day_hi,
  ).exclude(runtime__lt=min_time)
  if rerun:
    return qs.annotate(
        artifact_only_candidate=Value(False, output_field=BooleanField()),
    ).order_by("-end_time", "-jid")
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
  artifact_only = Q(pk__isnull=True)
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
    artifact_only = need_plot_artifacts | need_detail_artifacts
  annotated = annotated.annotate(
      artifact_only_candidate=Case(
          When(artifact_only, then=Value(True)),
          default=Value(False),
          output_field=BooleanField(),
      ),
  )
  return annotated.filter(need_metrics | artifact_only).order_by("-end_time", "-jid")


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
  # Primary path: keyset pagination for real ORM querysets only (test doubles use offset).
  if isinstance(queryset, QuerySet):
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
      rows = list(
          page_qs.values_list(
              "jid", "end_time", "start_time", "artifact_only_candidate",
          )[:chunk_size]
      )
      if not rows:
        break
      chunk = _chunk_rows_to_candidate_refs(rows)
      total += len(chunk)
      yield chunk, total
      last_jid, last_end_time, _start_time, _artifact_only = rows[-1]
    return

  offset = 0
  try:
    pk_values = queryset.values_list(
        "jid", "end_time", "start_time", "artifact_only_candidate",
    )
  except Exception:
    try:
      pk_values = queryset.values_list("jid", "end_time", "artifact_only_candidate")
    except Exception:
      try:
        pk_values = queryset.values_list("jid", "artifact_only_candidate")
      except Exception:
        pk_values = queryset.values_list("jid", flat=True)
  while True:
    rows = list(pk_values[offset:offset + chunk_size])
    if not rows:
      break
    chunk = _chunk_rows_to_candidate_refs(rows)
    total += len(chunk)
    yield chunk, total
    offset += chunk_size


def _candidate_ref(
    jid,
    artifact_only=False,
    runtime_s=None,
    telemetry_first_time=None,
    telemetry_last_time=None,
):
  ns = SimpleNamespace(jid=jid, artifact_only=bool(artifact_only))
  if runtime_s is not None:
    ns.runtime_s = float(runtime_s)
  if telemetry_first_time is not None:
    ns.telemetry_first_time = telemetry_first_time
  if telemetry_last_time is not None:
    ns.telemetry_last_time = telemetry_last_time
  return ns


def _job_refs_from_jids(jids):
  """Return lightweight job references that only carry jid + artifact-only state.

  metrics.Metrics().run() only requires ``job.jid``. Using tiny objects instead
  of ORM model instances avoids per-chunk model allocation and a redundant DB
  round-trip, which lowers memory usage and query pressure for large backfills.
  """
  refs = []
  for item in jids:
    if hasattr(item, "jid"):
      rt = getattr(item, "runtime_s", None)
      tft = getattr(item, "telemetry_first_time", None)
      tlt = getattr(item, "telemetry_last_time", None)
      refs.append(_candidate_ref(
          item.jid,
          getattr(item, "artifact_only", False),
          runtime_s=rt,
          telemetry_first_time=tft,
          telemetry_last_time=tlt,
      ))
    else:
      refs.append(_candidate_ref(item))
  return refs


def _attach_telemetry_bounds_to_candidate(candidate, bounds_by_jid):
  """Set precomputed in-window bounds on a candidate ref when available."""
  bounds = bounds_by_jid.get(candidate.jid)
  if not bounds:
    return candidate
  min_t, max_t = bounds
  if min_t is not None:
    candidate.telemetry_first_time = min_t
  if max_t is not None:
    candidate.telemetry_last_time = max_t
  return candidate


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


_COVERAGE_DEFER_LOGGED = set()
_COVERAGE_DEFER_LOGGED_CAP = 10000


def reset_metrics_coverage_defer_log_session():
  """Clear once-per-session coverage defer logs (scheduler startup)."""
  global _COVERAGE_MARGIN_WARN_LOGGED
  _COVERAGE_DEFER_LOGGED.clear()
  _COVERAGE_MARGIN_WARN_LOGGED = False


_COVERAGE_MARGIN_WARN_LOGGED = False
_COVERAGE_MARGIN_WARN_CAP_SECONDS = 86400.0 * 7


def _datetimes_mixed_naive_aware(*values):
  """Return True when any non-null datetimes disagree on aware vs naive."""
  flags = []
  for value in values:
    if value is None:
      continue
    flags.append(django_timezone.is_aware(value))
  return len(set(flags)) > 1


def evaluate_job_window_coverage_ready(
    start_time,
    end_time,
    first_in_window,
    last_in_window,
    *,
    start_margin_s=None,
    end_margin_s=None,
):
  """Return ``(ready, reason)`` for dual-edge in-window coverage (job aggregate)."""
  if start_margin_s is None:
    start_margin_s = float(cfg.get_metrics_readiness_start_margin_seconds())
  else:
    start_margin_s = float(start_margin_s)
  if end_margin_s is None:
    end_margin_s = float(cfg.get_metrics_readiness_end_margin_seconds())
  else:
    end_margin_s = float(end_margin_s)
  reason = {
      "start_ok": False,
      "end_ok": False,
      "start_lag_s": None,
      "end_lag_s": None,
      "start_margin_s": start_margin_s,
      "end_margin_s": end_margin_s,
      "mixed_naive_aware": False,
      "margin_exceeds_duration": False,
  }
  if start_time is None or end_time is None:
    return False, reason
  if first_in_window is None or last_in_window is None:
    return False, reason
  if _datetimes_mixed_naive_aware(
      start_time, end_time, first_in_window, last_in_window):
    reason["mixed_naive_aware"] = True
    return False, reason
  duration_s = (end_time - start_time).total_seconds()
  if duration_s > 0 and (start_margin_s + end_margin_s) > duration_s:
    reason["margin_exceeds_duration"] = True
    global _COVERAGE_MARGIN_WARN_LOGGED
    if not _COVERAGE_MARGIN_WARN_LOGGED:
      _COVERAGE_MARGIN_WARN_LOGGED = True
      if start_margin_s + end_margin_s > _COVERAGE_MARGIN_WARN_CAP_SECONDS:
        log_print(
            "metrics_readiness: start_margin_s + end_margin_s exceeds job "
            "duration (and margins are very large); jobs may never become ready",
            flush=True,
        )
  start_deadline = start_time + timedelta(seconds=start_margin_s)
  end_floor = end_time - timedelta(seconds=end_margin_s)
  reason["start_lag_s"] = (first_in_window - start_time).total_seconds()
  reason["end_lag_s"] = (end_time - last_in_window).total_seconds()
  reason["start_ok"] = first_in_window <= start_deadline
  reason["end_ok"] = last_in_window >= end_floor
  return reason["start_ok"] and reason["end_ok"], reason


def _log_metrics_deferred_coverage_once(jid, reason):
  if jid in _COVERAGE_DEFER_LOGGED:
    return
  if len(_COVERAGE_DEFER_LOGGED) >= _COVERAGE_DEFER_LOGGED_CAP:
    return
  _COVERAGE_DEFER_LOGGED.add(jid)
  log_print(
      "metrics_deferred_coverage jid={0} start_ok={1} end_ok={2} "
      "start_lag_s={3} end_lag_s={4} start_margin_s={5} end_margin_s={6}".format(
          jid,
          reason.get("start_ok"),
          reason.get("end_ok"),
          reason.get("start_lag_s"),
          reason.get("end_lag_s"),
          reason.get("start_margin_s"),
          reason.get("end_margin_s"),
      ),
      flush=True,
  )


def _legacy_all_hosts_sample_after_end(end_time, hosts, latest_by_host):
  if end_time is None or not hosts:
    return False
  return all(
      (latest_by_host.get(host) is not None and latest_by_host[host] > end_time)
      for host in hosts
  )


def _aggregate_bounds_from_host_map(hosts, host_min, host_max):
  """Combine per-host in-window bounds into one ``(min_time, max_time)``."""
  min_time = None
  max_time = None
  for host in hosts:
    chunk_min = host_min.get(host)
    chunk_max = host_max.get(host)
    if chunk_min is not None:
      min_time = chunk_min if min_time is None else min(min_time, chunk_min)
    if chunk_max is not None:
      max_time = chunk_max if max_time is None else max(max_time, chunk_max)
  return min_time, max_time


def _in_window_per_host_bounds(hosts, start_time, end_time):
  """Return ``(host_min_map, host_max_map)`` for ``hosts`` in ``[start, end]``."""
  host_min = {}
  host_max = {}
  if not hosts or start_time is None or end_time is None:
    return host_min, host_max
  host_list = sorted(set(hosts))
  batch = max(1, int(HOST_LAST_TIME_LOOKUP_BATCH))
  for i in range(0, len(host_list), batch):
    chunk = host_list[i:i + batch]
    qs = (
        host_data.objects.filter(
            host__in=chunk,
            time__gte=start_time,
            time__lte=end_time,
        )
        .values("host")
        .annotate(mn=Min("time"), mx=Max("time"))
    )
    for row in qs:
      host = row.get("host")
      if row.get("mn") is not None:
        host_min[host] = row["mn"]
      if row.get("mx") is not None:
        host_max[host] = row["mx"]
  return host_min, host_max


def _in_window_min_max_for_hosts(hosts, start_time, end_time):
  """Return ``(min_time, max_time)`` for host_data in ``[start_time, end_time]``."""
  if not hosts or start_time is None or end_time is None:
    return None, None
  host_min, host_max = _in_window_per_host_bounds(hosts, start_time, end_time)
  return _aggregate_bounds_from_host_map(hosts, host_min, host_max)


def _in_window_min_max_by_job_rows_reference(jobs):
  """Reference per-job loop (tests); prefer :func:`_in_window_min_max_by_job_rows`."""
  bounds = {}
  for row in jobs:
    jid = row["jid"]
    bounds[jid] = _in_window_min_max_for_hosts(
        _fqdn_hosts_for_job(row),
        row.get("start_time"),
        row.get("end_time"),
    )
  return bounds


def _in_window_min_max_by_job_rows(jobs):
  """Map jid -> ``(min_time, max_time)`` using batched host aggregates per window."""
  bounds = {}
  if not jobs:
    return bounds
  window_groups = defaultdict(list)
  for row in jobs:
    start_time = row.get("start_time")
    end_time = row.get("end_time")
    if start_time is None or end_time is None:
      bounds[row["jid"]] = (None, None)
      continue
    window_groups[(start_time, end_time)].append(row)
  for (start_time, end_time), group_rows in window_groups.items():
    jid_to_hosts = {}
    unique_hosts = set()
    for row in group_rows:
      hosts = _fqdn_hosts_for_job(row)
      jid_to_hosts[row["jid"]] = hosts
      unique_hosts.update(hosts)
    host_min, host_max = _in_window_per_host_bounds(
        unique_hosts, start_time, end_time)
    for jid, hosts in jid_to_hosts.items():
      bounds[jid] = _aggregate_bounds_from_host_map(hosts, host_min, host_max)
  for row in jobs:
    bounds.setdefault(row["jid"], (None, None))
  return bounds


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
  """Return ready jids from pre-fetched job rows (jid/start/end/host_list)."""
  ready, _bounds = _ready_jids_and_bounds_from_job_rows(jobs)
  return ready


def _ready_jids_and_bounds_from_job_rows(jobs, *, precomputed_bounds_by_jid=None):
  """Return ``(ready_jids, bounds_by_jid)`` for pre-fetched job rows."""
  if not jobs:
    return [], {}

  if cfg.get_metrics_readiness_require_window_coverage():
    start_margin_s = float(cfg.get_metrics_readiness_start_margin_seconds())
    end_margin_s = float(cfg.get_metrics_readiness_end_margin_seconds())
    bounds_by_jid = (
        precomputed_bounds_by_jid
        if precomputed_bounds_by_jid is not None
        else _in_window_min_max_by_job_rows(jobs)
    )
    ready = []
    for row in jobs:
      jid = row["jid"]
      start_time = row.get("start_time")
      end_time = row.get("end_time")
      if start_time is None or end_time is None:
        continue
      min_t, max_t = bounds_by_jid.get(jid, (None, None))
      is_ready, reason = evaluate_job_window_coverage_ready(
          start_time,
          end_time,
          min_t,
          max_t,
          start_margin_s=start_margin_s,
          end_margin_s=end_margin_s,
      )
      if is_ready:
        ready.append(jid)
      else:
        _log_metrics_deferred_coverage_once(jid, reason)
    return ready, bounds_by_jid

  unique_hosts = set()
  job_hosts = {}
  for row in jobs:
    hosts = set(_fqdn_hosts_for_job(row))
    job_hosts[row["jid"]] = hosts
    unique_hosts.update(hosts)

  latest_by_host = _latest_sample_time_by_host(unique_hosts)

  ready = []
  bounds_by_jid = {}
  for row in jobs:
    jid = row["jid"]
    end_time = row.get("end_time")
    hosts = job_hosts.get(jid) or set()
    bounds_by_jid[jid] = (None, None)
    if _legacy_all_hosts_sample_after_end(end_time, hosts, latest_by_host):
      ready.append(jid)
  return ready, bounds_by_jid


def _filter_jids_with_samples_after_end(jids):
  """Keep jids that pass readiness (window coverage or legacy post-end per host)."""
  if not jids:
    return []

  jobs = list(
      job_data.objects.filter(jid__in=jids)
      .order_by("jid")
      .values("jid", "start_time", "end_time", "host_list")
  )
  ready, _bounds = _ready_jids_and_bounds_from_job_rows(jobs)
  return ready


def _filter_jids_with_samples_after_end_and_bounds(jids):
  """Like :func:`_filter_jids_with_samples_after_end` but also return bounds map."""
  if not jids:
    return [], {}

  jobs = list(
      job_data.objects.filter(jid__in=jids)
      .order_by("jid")
      .values("jid", "start_time", "end_time", "host_list")
  )
  return _ready_jids_and_bounds_from_job_rows(jobs)


def _strict_host_list_coverage_bucket(
    start_time,
    end_time,
    min_in_window,
    max_in_window,
    *,
    start_margin_s=None,
    end_margin_s=None,
):
  """Classify host_list-scoped in-window bounds (canonical strict/proxy semantics)."""
  if start_time is None or end_time is None:
    return "unknown"
  if min_in_window is None and max_in_window is None:
    return "unknown"
  ready, _reason = evaluate_job_window_coverage_ready(
      start_time,
      end_time,
      min_in_window,
      max_in_window,
      start_margin_s=start_margin_s,
      end_margin_s=end_margin_s,
  )
  if not ready:
    return "reject"
  return "unknown"


def _proxy_window_coverage_bucket(start_time, end_time, min_in_window, max_in_window):
  """Reject when host_list in-window min/max proves coverage failure."""
  return _strict_host_list_coverage_bucket(
      start_time, end_time, min_in_window, max_in_window)


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

  Returns ``'reject'`` when host_list-scoped in-window data proves not-ready, or
  ``'unknown'`` when the strict host_list probe must decide (including
  non-PostgreSQL, where bulk code treats every jid as unknown).
  """
  if connections["default"].vendor != "postgresql":
    return "unknown"
  end_row = (
      job_data.objects.filter(jid=jid)
      .values("jid", "start_time", "end_time", "host_list")
      .first()
  )
  if not end_row:
    return "unknown"
  start_time = end_row.get("start_time")
  end_time = end_row.get("end_time")
  if cfg.get_metrics_readiness_require_window_coverage():
    min_t, max_t = _in_window_min_max_for_hosts(
        _fqdn_hosts_for_job(end_row),
        start_time,
        end_time,
    )
    return _proxy_window_coverage_bucket(
        start_time, end_time, min_t, max_t)
  max_time = (
      host_data.objects.filter(jid=jid)
      .aggregate(max_time=Max("time"))
      .get("max_time")
  )
  return _proxy_readiness_bucket(end_time, max_time)


def _proxy_reject_not_ready_jids(jids):
  """Cheap jid-level prefilter: reject only when host_list in-window data proves not-ready.

  Uses the same host_list + window aggregate as strict readiness (not ``host_data.jid``).
  When ingest does not tag ``host_data.jid``, or jid-scoped rows lag host_list samples,
  keep the jid in the ``unknown`` set and let the full readiness probe decide.

  Uses bounded ``jid__in`` batches and batched host aggregates (no correlated
  ``Exists`` per row) to avoid PostgreSQL ``statement_timeout`` on large chunks.
  """
  if not jids:
    return set(), []
  if connections["default"].vendor != "postgresql":
    return set(), list(jids)
  use_coverage = cfg.get_metrics_readiness_require_window_coverage()
  batch = max(1, int(cfg.get_metrics_proxy_reject_jid_batch_size()))
  reject = set()
  unknown = []
  for sub in _iter_subbatches(jids, batch):
    job_rows = list(
        job_data.objects.filter(jid__in=sub)
        .values("jid", "start_time", "end_time", "host_list")
    )
    job_by_jid = {r["jid"]: r for r in job_rows}
    if use_coverage:
      sub_rows = [job_by_jid[jid] for jid in sub if jid in job_by_jid]
      bounds_by_jid = _in_window_min_max_by_job_rows(sub_rows)
      for jid in sub:
        row = job_by_jid.get(jid)
        if row is None:
          unknown.append(jid)
          continue
        start_time = row.get("start_time")
        end_time = row.get("end_time")
        if start_time is None or end_time is None:
          unknown.append(jid)
          continue
        min_t, max_t = bounds_by_jid.get(jid, (None, None))
        bucket = _proxy_window_coverage_bucket(
            start_time, end_time, min_t, max_t)
        if bucket == "reject":
          reject.add(jid)
        else:
          unknown.append(jid)
      continue
    end_by_jid = {jid: row.get("end_time") for jid, row in job_by_jid.items()}
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


@contextlib.contextmanager
def _temporary_metrics_run_timeouts(*, poll_timeout_s=None, stall_timeout_s=None):
  """Temporarily override Metrics.run poll/stall timeout env vars."""
  poll_key = "HPCPERFSTATS_METRICS_RUN_POLL_TIMEOUT_S"
  stall_key = "HPCPERFSTATS_METRICS_RUN_STALL_TIMEOUT_S"
  prev_poll = os.environ.get(poll_key)
  prev_stall = os.environ.get(stall_key)
  try:
    if poll_timeout_s is not None:
      os.environ[poll_key] = str(float(poll_timeout_s))
    if stall_timeout_s is not None:
      os.environ[stall_key] = str(float(stall_timeout_s))
    yield
  finally:
    if prev_poll is None:
      os.environ.pop(poll_key, None)
    else:
      os.environ[poll_key] = prev_poll
    if prev_stall is None:
      os.environ.pop(stall_key, None)
    else:
      os.environ[stall_key] = prev_stall


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
  """Fill ``ready_queue`` from date iterators and strict readiness.

  ``on_not_ready_jid`` when provided is called as ``(jid, candidate=None)`` where
  ``candidate`` is the scheduler ref object when known (for deferred metadata).
  """

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

  def _strict_ready_fallback_one(jid, candidate_by_jid=None):
    """Single-jid strict readiness after batch failure (same semantics as legacy path)."""
    with scheduler_shared_lock:
      if strict_check_cooldown_until.get(jid, 0.0) > time.monotonic():
        return None
    t0 = time.monotonic()
    try:
      with phase_timer.phase("readiness_s"):
        with _pg_local_readiness_timeouts():
          strict_ready, bounds_by_jid = (
              _filter_jids_with_samples_after_end_and_bounds([jid])
          )
    except (OperationalError, DatabaseError) as exc:
      if is_database_unavailable_error(exc):
        log_and_raise_database_unavailable(
            exc, context="update_metrics strict readiness (single)"
        )
      return None
    latency = time.monotonic() - t0
    _strict_ready_record_latency(latency, 1)
    if not strict_ready:
      return None
    ready_jid = strict_ready[0]
    lookup = candidate_by_jid if candidate_by_jid is not None else {}
    candidate = lookup.get(ready_jid, _candidate_ref(ready_jid))
    return _attach_telemetry_bounds_to_candidate(candidate, bounds_by_jid)

  def _process_pk_chunk(state, pk_chunk):
    """Process ``pk_chunk`` in order; set ``pending_tail`` if prefetch fills mid-chunk.

    Returns ``True`` when ``len(ready_queue) >= prefetch_chunks`` and the caller
    should stop scheduling more readiness work in this invocation.

    Uses batched proxy rejection and batched strict readiness probes to avoid
    per-jid round-trips on the producer thread.
    """
    ordered = [
        candidate if hasattr(candidate, "jid") else _candidate_ref(candidate)
        for candidate in list(pk_chunk)
    ]
    candidate_by_jid = {candidate.jid: candidate for candidate in ordered}
    chunk_job_by_jid = {}
    chunk_bounds_by_jid = None
    if (
        connections["default"].vendor == "postgresql"
        and cfg.get_metrics_readiness_require_window_coverage()
    ):
      chunk_jids = [candidate.jid for candidate in ordered]
      chunk_job_rows = list(
          job_data.objects.filter(jid__in=chunk_jids)
          .values("jid", "start_time", "end_time", "host_list")
      )
      chunk_job_by_jid = {row["jid"]: row for row in chunk_job_rows}
      chunk_bounds_by_jid = _in_window_min_max_by_job_rows(
          [chunk_job_by_jid[jid] for jid in chunk_jids if jid in chunk_job_by_jid]
      )
      reject_set = set()
      for candidate in ordered:
        jid = candidate.jid
        row = chunk_job_by_jid.get(jid)
        if row is None:
          continue
        start_time = row.get("start_time")
        end_time = row.get("end_time")
        if start_time is None or end_time is None:
          continue
        min_t, max_t = chunk_bounds_by_jid.get(jid, (None, None))
        if _proxy_window_coverage_bucket(start_time, end_time, min_t, max_t) == "reject":
          reject_set.add(jid)
    else:
      reject_set, _ = _proxy_reject_not_ready_jids([candidate.jid for candidate in ordered])
      reject_set = set(reject_set)
    for candidate in ordered:
      jid = candidate.jid
      if on_candidate_jid is not None:
        on_candidate_jid(candidate)
      with scheduler_shared_lock:
        stats["candidate_jids"] += 1

    for candidate in ordered:
      jid = candidate.jid
      if jid not in reject_set:
        continue
      if on_not_ready_jid is not None:
        on_not_ready_jid(jid, candidate)
      with scheduler_shared_lock:
        stats["proxy_not_ready_jids"] += 1
        stats["proxy_rejected_jids"] += 1
        stats["skipped_not_ready"] += 1

    unknown_ordered = [
        candidate for candidate in ordered if candidate.jid not in reject_set
    ]
    bs = max(1, int(strict_check_state["batch_size"]))
    pos = 0
    while pos < len(unknown_ordered):
      if len(ready_queue) >= prefetch_chunks:
        state["pending_tail"] = unknown_ordered[pos:]
        return True
      sub_end = min(pos + bs, len(unknown_ordered))
      sub = unknown_ordered[pos:sub_end]
      batch_mono = time.monotonic()
      cooldown_candidates = []
      for candidate in sub:
        jid = candidate.jid
        with scheduler_shared_lock:
          if strict_check_cooldown_until.get(jid, 0.0) > batch_mono:
            cooldown_candidates.append(candidate)
      cooldown_jid_set = {c.jid for c in cooldown_candidates}
      work = [candidate for candidate in sub if candidate.jid not in cooldown_jid_set]
      for candidate in cooldown_candidates:
        if on_not_ready_jid is not None:
          on_not_ready_jid(candidate.jid, candidate)
        with scheduler_shared_lock:
          stats["strict_not_ready_jids"] += 1
          stats["strict_cooldown_skips"] += 1
          stats["skipped_not_ready"] += 1
      pos = sub_end
      if not work:
        continue
      try:
        with phase_timer.phase("readiness_s"):
          with _pg_local_readiness_timeouts():
            if (
                chunk_bounds_by_jid is not None
                and all(candidate.jid in chunk_job_by_jid for candidate in work)
            ):
              jobs_for_strict = [chunk_job_by_jid[candidate.jid] for candidate in work]
              ready_list, bounds_by_jid = _ready_jids_and_bounds_from_job_rows(
                  jobs_for_strict,
                  precomputed_bounds_by_jid=chunk_bounds_by_jid,
              )
            else:
              ready_list, bounds_by_jid = (
                  _filter_jids_with_samples_after_end_and_bounds(
                      [candidate.jid for candidate in work]
                  )
              )
      except (OperationalError, DatabaseError) as exc:
        if is_database_unavailable_error(exc):
          log_and_raise_database_unavailable(
              exc, context="update_metrics strict readiness (batch)"
          )
        failed_fallback_jids = []
        prefetch_stop = False
        for candidate in work:
          ready_candidate = _strict_ready_fallback_one(
              candidate.jid, candidate_by_jid)
          if ready_candidate is None:
            failed_fallback_jids.append(candidate.jid)
            if on_not_ready_jid is not None:
              on_not_ready_jid(candidate.jid, candidate)
            with scheduler_shared_lock:
              stats["strict_not_ready_jids"] += 1
              stats["skipped_not_ready"] += 1
          else:
            with scheduler_shared_lock:
              stats["strict_ready_jids"] += 1
            ready_queue.append(ready_candidate)
            if len(ready_queue) >= prefetch_chunks:
              tail = []
              seen = False
              for j2 in work:
                if j2.jid == ready_candidate.jid:
                  seen = True
                  continue
                if seen:
                  tail.append(j2)
              tail.extend(unknown_ordered[pos:])
              if tail:
                state["pending_tail"] = tail
              prefetch_stop = True
        with scheduler_shared_lock:
          _handle_strict_readiness_db_error(
              stats=stats,
              strict_check_state=strict_check_state,
              elapsed_s=(time.monotonic() - batch_mono),
              batch_size_seen=len(work),
              exc=exc,
              strict_check_cooldown_until=strict_check_cooldown_until,
              cooldown_jids=failed_fallback_jids,
          )
        if prefetch_stop:
          return True
        continue

      latency = time.monotonic() - batch_mono
      _strict_ready_record_latency(latency, len(work))
      ready_set = set(ready_list)
      for k, candidate in enumerate(work):
        if candidate.jid in ready_set:
          with scheduler_shared_lock:
            stats["strict_ready_jids"] += 1
          _attach_telemetry_bounds_to_candidate(candidate, bounds_by_jid)
          ready_queue.append(candidate)
          if len(ready_queue) >= prefetch_chunks:
            tail = work[k + 1:] + unknown_ordered[pos:]
            if tail:
              state["pending_tail"] = tail
            return True
        else:
          if on_not_ready_jid is not None:
            on_not_ready_jid(candidate.jid, candidate)
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
    rescan_seen_order,
    rescan_seen_cap,
    rescan_lock,
    stop_event,
):
  """Start background thread that periodically discovers newly eligible jobs."""
  if not dates:
    return None

  def _rescan_loop():
    from hpcperfstats.process_title import set_daemon_thread_title

    set_daemon_thread_title(
        "",
        script_name=UPDATE_METRICS_PROCESS_TITLE,
        role="candidate-rescan",
    )
    close_old_connections()
    idle_rounds = 0
    try:
      while not shutdown_requested[0] and not stop_event.is_set():
        added_any = False
        for d in dates:
          if shutdown_requested[0] or stop_event.is_set():
            break
          try:
            qs = _jobs_queryset(d, min_time, rerun)
            candidates = list(
                qs.values_list(
                    "jid", "start_time", "end_time", "artifact_only_candidate",
                )[:RESCAN_FETCH_LIMIT]
            )
          except Exception:
            continue
          if not candidates:
            continue
          with rescan_lock:
            for row in candidates:
              if len(row) >= 4:
                jid, st, et, artifact_only = row[0], row[1], row[2], row[3]
              elif len(row) >= 2:
                jid, artifact_only = row[0], row[1]
                st, et = None, None
              else:
                continue
              if jid in rescan_seen_jids:
                continue
              _add_bounded_seen_jid(
                  rescan_seen_jids,
                  rescan_seen_order,
                  jid,
                  cap=rescan_seen_cap,
              )
              rescan_candidate_jids.append(
                  _candidate_ref(
                      jid,
                      bool(artifact_only),
                      runtime_s=_job_window_runtime_seconds(st, et),
                  )
              )
              added_any = True
        if added_any:
          idle_rounds = 0
        else:
          idle_rounds += 1
        wait_s = min(
            float(RESCAN_IDLE_INTERVAL_MAX_SECONDS),
            float(RESCAN_INTERVAL_SECONDS)
            * (2 ** min(idle_rounds, 6)),
        )
        stop_event.wait(wait_s)
    finally:
      close_old_connections()

  thread = threading.Thread(
      target=_rescan_loop,
      name="metrics-candidate-rescan",
      daemon=True,
  )
  thread.start()
  return thread


def _run_public_ef_artifacts_parallel_phase(shared_pool, phase_timer):
  """Build /pub EF artifacts on the metrics pool before any job compute."""
  last_progress_log = {"at": 0.0}

  def _progress_update(snapshot):
    now = time.monotonic()
    if (now - last_progress_log["at"]) < PUBLIC_EF_PHASE_POLL_TIMEOUT_SECONDS:
      return
    last_progress_log["at"] = now
    log_print(
        "metrics scheduler: waiting on /pub/ EF artifacts completed={0}/{1} pending={2} "
        "no_progress_s={3:.1f}".format(
            int(snapshot.get("tasks_completed", 0)),
            int(snapshot.get("tasks_total", 0)),
            int(snapshot.get("pending_tasks", 0)),
            float(snapshot.get("stalled_for_s", 0.0)),
        ),
        flush=True,
    )

  with phase_timer.phase("public_ef_artifacts_s"):
    pub_stats = refresh_public_expansion_factor_artifacts_parallel(
        shared_pool,
        poll_timeout_s=PUBLIC_EF_PHASE_POLL_TIMEOUT_SECONDS,
        no_progress_timeout_s=PUBLIC_EF_PHASE_NO_PROGRESS_TIMEOUT_SECONDS,
        progress_callback=_progress_update,
    )
  if not isinstance(pub_stats, dict):
    pub_stats = {}
  if (
      int(pub_stats.get("degraded", 0)) > 0
      or int(pub_stats.get("worker_exceptions", 0)) > 0
      or int(pub_stats.get("watchdog_timeouts", 0)) > 0
      or int(pub_stats.get("pending_tasks", 0)) > 0
  ):
    log_print(
        "metrics scheduler: /pub/ EF artifacts degraded before job compute {0}".format(
            pub_stats
        ),
        flush=True,
    )
  else:
    log_print(
        "metrics scheduler: finished /pub/ EF artifacts (parallel) before job compute "
        "{0}".format(pub_stats),
        flush=True,
    )
  return pub_stats


def _reset_metrics_pool_after_public_phase(metrics_manager):
  """Recreate worker processes after /pub phase before job metrics.

  Public EF workers and job-metrics workers both use ORM-heavy paths. Recreating
  the pool between phases avoids carrying any mixed server-cursor/session state
  into the first metrics batch.
  """
  resetter = getattr(metrics_manager, "reset_pool_hard", None)
  if callable(resetter):
    try:
      resetter()
    except Exception as exc:
      log_print(
          "metrics scheduler: pool reset after /pub phase failed; recreating lazily: {0}".format(exc),
          flush=True,
      )


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
    rescan_seen_order,
    rescan_seen_cap,
    rescan_lock,
):
  """Start background producer that fills ready_queue from readiness checks."""
  def _producer_loop():
    from hpcperfstats.process_title import set_daemon_thread_title

    set_daemon_thread_title(
        "",
        script_name=UPDATE_METRICS_PROCESS_TITLE,
        role="readiness-producer",
    )
    close_old_connections()
    rr_cursor = {"idx": 0}
    deferred_not_ready = {}
    deferred_meta = {}
    last_processed_total = 0
    last_progress_at = time.monotonic()

    def _remember_candidate(candidate):
      jid = getattr(candidate, "jid", candidate)
      ao = getattr(candidate, "artifact_only", False)
      rt = getattr(candidate, "runtime_s", None)
      ref = _candidate_ref(jid, ao, runtime_s=rt)
      jid = ref.jid
      with rescan_lock:
        _add_bounded_seen_jid(
            rescan_seen_jids,
            rescan_seen_order,
            jid,
            cap=rescan_seen_cap,
        )
      meta = deferred_meta.setdefault(
          jid,
          {"first_seen": time.monotonic(), "attempts": 0, "artifact_only": False},
      )
      meta["artifact_only"] = bool(ref.artifact_only)
      if rt is not None:
        meta["runtime_s"] = float(rt)

    def _defer_not_ready_jid(jid, runtime_s=None):
      now = time.monotonic()
      meta = deferred_meta.setdefault(
          jid,
          {"first_seen": now, "attempts": 0, "artifact_only": False},
      )
      meta["attempts"] += 1
      if runtime_s is not None:
        meta["runtime_s"] = float(runtime_s)
      age_s = max(0.0, now - float(meta["first_seen"]))
      max_retries = int(cfg.get_metrics_deferred_not_ready_max_retries())
      use_quarantine = (
          meta["attempts"] >= max_retries
          or age_s >= float(cfg.get_metrics_deferred_not_ready_max_age_seconds())
      )
      if use_quarantine:
        retry_after = float(cfg.get_metrics_deferred_not_ready_quarantine_seconds())
        with scheduler_shared_lock:
          stats["deferred_quarantined_jids"] += 1
      else:
        retry_after = float(cfg.get_metrics_deferred_not_ready_retry_seconds())
      candidate_retry_at = now + retry_after
      deferred_not_ready[jid] = _merge_deferred_retry_at(
          deferred_not_ready.get(jid),
          candidate_retry_at,
      )

    try:
      while not shutdown_requested[0]:
        with scheduler_shared_lock:
          processed_now = int(stats["processed"])
        if processed_now > last_processed_total:
          last_processed_total = processed_now
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

        def _defer_hit(jid, candidate=None):
          rt = getattr(candidate, "runtime_s", None) if candidate is not None else None
          deferred_hits.append((jid, rt))

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
        if deferred_not_ready:
          rescan_due = [
              candidate for candidate in rescan_due
              if deferred_not_ready.get(candidate.jid, 0.0) <= now
          ]
        extra_candidates = []
        seen_extra = set()
        deferred_candidate_refs = [
            _candidate_ref(
                jid,
                deferred_meta.get(jid, {}).get("artifact_only", False),
                runtime_s=deferred_meta.get(jid, {}).get("runtime_s"),
            )
            for jid in deferred_due
        ]
        for candidate in deferred_candidate_refs + rescan_due:
          jid = candidate.jid
          if jid in seen_extra:
            continue
          seen_extra.add(jid)
          extra_candidates.append(candidate)
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
              on_not_ready_jid=_defer_hit,
              on_candidate_jid=_remember_candidate,
          )
          for jid, rt in deferred_hits:
            _defer_not_ready_jid(jid, runtime_s=rt)
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
            on_not_ready_jid=_defer_hit,
            on_candidate_jid=_remember_candidate,
        )
        for jid, rt in deferred_hits:
          _defer_not_ready_jid(jid, runtime_s=rt)
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
        if local_ready:
          for candidate in local_ready:
            deferred_meta.pop(candidate.jid, None)
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
                  int(stats.get("processed", 0)) + int(stats.get("failed", 0)),
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
      metrics_manager.run([job_ref], pool=metrics_manager.ensure_pool())
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
  artifact_only = bool(getattr(job_ref, "artifact_only", False))
  t_metrics_start = time.monotonic()
  if artifact_only:
    run_outcome = {
        "jid": context.jid,
        "ok": True,
        "status": "artifact_only",
        "error_type": None,
        "error_message": None,
        "persist_s": 0.0,
    }
  else:
    try:
      if metrics_run_lock is not None:
        with metrics_run_lock:
          run_outcomes = metrics_manager.run([job_ref], pool=metrics_manager.ensure_pool())
      else:
        run_outcomes = metrics_manager.run([job_ref], pool=metrics_manager.ensure_pool())
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
          "failure_kind": type(exc).__name__,
          "error_type": type(exc).__name__,
          "error_message": str(exc),
          "persist_s": 0.0,
      }
    if run_outcomes is None:
      run_outcomes = [{
          "jid": context.jid,
          "ok": True,
          "status": "ok",
          "error_type": None,
          "error_message": None,
          "persist_s": 0.0,
      }]
    run_outcome = (
        run_outcomes[0]
        if run_outcomes else {
            "jid": context.jid,
            "ok": False,
            "status": "missing_run_outcome",
            "error_type": "MissingRunOutcome",
            "error_message": "Metrics.run returned no per-jid outcome",
            "persist_s": 0.0,
        }
    )
  if not run_outcome.get("ok"):
    log_print(
        "metrics scheduler: failed jid={0}; status={1}; skipping prewarm error_type={2} error={3!r}".format(
            context.jid,
            run_outcome.get("status"),
            run_outcome.get("error_type"),
            run_outcome.get("error_message"),
        ),
        flush=True,
    )
    return {
        "ok": False,
        "jid": context.jid,
        "metrics_s": time.monotonic() - t_metrics_start,
        "prewarm_s": 0.0,
        "telemetry": telemetry,
        "failure_kind": run_outcome.get("status"),
        "error_type": run_outcome.get("error_type"),
        "error_message": run_outcome.get("error_message"),
        "persist_s": run_outcome.get("persist_s", 0.0),
    }
  metrics_elapsed = 0.0 if artifact_only else (time.monotonic() - t_metrics_start)
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
      "failure_kind": run_outcome.get("status"),
      "error_type": run_outcome.get("error_type"),
      "error_message": run_outcome.get("error_message"),
      "persist_s": run_outcome.get("persist_s", 0.0),
  }


def _empty_jid_outcome_telemetry():
  """Telemetry dict shape expected by the scheduler consumer."""
  return _new_jid_telemetry()


def _scheduler_jid_outcome(
    *,
    ok,
    jid,
    metrics_s,
    prewarm_s,
    telemetry,
    batch_exception=False,
    fallback_failed=False,
    failure_kind=None,
    error_type=None,
    error_message=None,
    persist_s=0.0,
):
  """Canonical scheduler-facing per-jid outcome dict."""
  return {
      "ok": bool(ok),
      "jid": jid,
      "metrics_s": float(max(0.0, metrics_s)),
      "prewarm_s": float(max(0.0, prewarm_s)),
      "telemetry": dict(telemetry or _empty_jid_outcome_telemetry()),
      "_batch_exception": bool(batch_exception),
      "_fallback_failed": bool(fallback_failed),
      "failure_kind": failure_kind,
      "error_type": error_type,
      "error_message": error_message,
      "persist_s": float(max(0.0, persist_s)),
  }


def _compute_jid_outcomes_batch(
    job_refs,
    metrics_manager,
    prewarm_pipeline,
    shared_pool,
    batch_timing=None,
):
  """Run one batched ``Metrics.run`` then optional prewarm (submit/drain).

  Returns outcome dicts sorted by ``jid`` for stable counters/telemetry.

  A single ``Metrics.run(job_refs, …)`` saturates the process pool; prewarm
  uses ``_PrewarmPipeline.submit`` + ``drain_some`` when ``pipeline_required``,
  or runs inline when configured. Set ``[PIPELINE] metrics_scheduler_skip_prewarm``
  (or env ``HPCPERFSTATS_METRICS_SCHEDULER_SKIP_PREWARM``) to skip plot/detail
  persistence for catch-up runs.

  When ``batch_timing`` is a dict, it is populated with ``metrics_wall_s``,
  ``prewarm_wall_s``, and ``batch_wall_s`` for scheduler watchdogs.
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
  metrics_job_refs = [
      ref for ref in job_refs if not bool(getattr(ref, "artifact_only", False))
  ]
  artifact_only_refs = [
      ref for ref in job_refs if bool(getattr(ref, "artifact_only", False))
  ]
  t_batch = time.monotonic()
  timing = {} if batch_timing is None else batch_timing
  if cfg.get_metrics_per_jid_phase_diagnostics_enabled():
    n = len(job_refs)
    head = min(24, n)
    jids_head = ",".join(r.jid for r in job_refs[:head])
    ao_head = ",".join(
        "1" if bool(getattr(r, "artifact_only", False)) else "0"
        for r in job_refs[:head]
    )
    suffix = "" if n <= head else " …(+{0} more)".format(n - head)
    log_print(
      "metrics scheduler: phase=batch_compute size={0} jids[{1}]={2}{3} "
      "artifact_only[{1}]={4}".format(n, head, jids_head, suffix, ao_head),
      flush=True,
    )
  metrics_run_outcomes = []
  t_metrics_start = None
  try:
    if metrics_job_refs:
      t_metrics_start = time.monotonic()
      metrics_run_outcomes = metrics_manager.run(
          metrics_job_refs,
          pool=metrics_manager.ensure_pool(),
      )
      if metrics_run_outcomes is None:
        metrics_run_outcomes = [{
            "jid": ref.jid,
            "ok": True,
            "status": "ok",
            "error_type": None,
            "error_message": None,
            "persist_s": 0.0,
        } for ref in metrics_job_refs]
  except Exception as exc:
    batch_failed_with_stall = isinstance(exc, metrics.MetricsRunWorkerStallError)
    if isinstance(exc, metrics.MetricsRunWorkerStallError):
      log_print(
          "metrics scheduler: compute batch aborted due to worker stall "
          "(stall_duration_s={0:.1f} pool_reset_confirmed={1})".format(
              float(exc.stalled_for_s),
              1 if exc.pool_reset_confirmed else 0,
          ),
          flush=True,
      )
    log_print(
        "metrics scheduler: batch Metrics.run failed size={0}: {1}".format(
            len(job_refs), exc
        ),
        flush=True,
    )
    _log_exception_details("metrics scheduler: batch Metrics.run", exc)
    # Do not fail the whole dequeue batch on one batch-level exception (e.g.
    # malformed payload causing an exception during persistence). Retry per jid
    # so unaffected jobs can still progress.
    t_recover = time.monotonic()
    succeeded = []
    failed = []
    recovery_budget_hit = False
    stalled_recovery_skipped = 0
    timeout_ctx = _temporary_metrics_run_timeouts(
        poll_timeout_s=STALL_RECOVERY_PER_JID_POLL_TIMEOUT_SECONDS,
        stall_timeout_s=STALL_RECOVERY_PER_JID_TIMEOUT_SECONDS,
    ) if batch_failed_with_stall else contextlib.nullcontext()
    with timeout_ctx:
      for idx, ref in enumerate(metrics_job_refs):
        if (
            batch_failed_with_stall
            and (time.monotonic() - t_recover) >= STALL_RECOVERY_MAX_WALL_SECONDS
        ):
          remaining = metrics_job_refs[idx:]
          failed.extend([
              (ref, {
                  "jid": ref.jid,
                  "ok": False,
                  "status": "stall_recovery_budget_exhausted",
                  "error_type": "RecoveryBudgetExhausted",
                  "error_message": "per-jid stall recovery budget exhausted",
                  "persist_s": 0.0,
              })
              for ref in remaining
          ])
          stalled_recovery_skipped += len(remaining)
          recovery_budget_hit = True
          break
        try:
          run_outcomes = metrics_manager.run([ref], pool=metrics_manager.ensure_pool())
          if run_outcomes is None:
            run_outcomes = [{
                "jid": ref.jid,
                "ok": True,
                "status": "ok",
                "error_type": None,
                "error_message": None,
                "persist_s": 0.0,
            }]
          run_outcome = (
              run_outcomes[0]
              if run_outcomes else {
                  "jid": ref.jid,
                  "ok": False,
                  "status": "missing_run_outcome",
                  "error_type": "MissingRunOutcome",
                  "error_message": "Metrics.run returned no per-jid outcome",
                  "persist_s": 0.0,
              }
          )
          if run_outcome.get("ok"):
            succeeded.append((ref, run_outcome))
          else:
            failed.append((ref, run_outcome))
        except Exception as one_exc:
          failed.append((ref, {
              "jid": ref.jid,
              "ok": False,
              "status": "per_jid_retry_exception",
              "error_type": type(one_exc).__name__,
              "error_message": str(one_exc),
              "persist_s": 0.0,
          }))
          log_print(
              "metrics scheduler: per-jid Metrics.run failed jid={0} after batch failure: {1}".format(
                  ref.jid, one_exc
              ),
              flush=True,
          )
          _log_exception_details(
              "metrics scheduler: per-jid Metrics.run jid={0}".format(ref.jid),
              one_exc,
          )
    if recovery_budget_hit:
      log_print(
          "metrics scheduler: stall recovery time budget exhausted after {0:.1f}s; "
          "marking remaining jids as failed without further retries count={1}".format(
              time.monotonic() - t_recover,
              stalled_recovery_skipped,
          ),
          flush=True,
      )
    total_elapsed = time.monotonic() - t_recover
    n_total = max(1, len(metrics_job_refs))
    per_metrics = total_elapsed / n_total
    telem = _empty_jid_outcome_telemetry()
    outcomes = []
    for ref, base_outcome in sorted(succeeded, key=lambda item: item[0].jid):
      outcomes.append(_scheduler_jid_outcome(
          ok=True,
          jid=ref.jid,
          metrics_s=per_metrics,
          prewarm_s=0.0,
          telemetry=telem,
          batch_exception=True,
          fallback_failed=False,
          failure_kind=base_outcome.get("status"),
          error_type=base_outcome.get("error_type"),
          error_message=base_outcome.get("error_message"),
          persist_s=base_outcome.get("persist_s", 0.0),
      ))
    for ref, base_outcome in sorted(failed, key=lambda item: item[0].jid):
      outcomes.append(_scheduler_jid_outcome(
          ok=False,
          jid=ref.jid,
          metrics_s=per_metrics,
          prewarm_s=0.0,
          telemetry=telem,
          batch_exception=True,
          fallback_failed=True,
          failure_kind=base_outcome.get("status"),
          error_type=base_outcome.get("error_type"),
          error_message=base_outcome.get("error_message"),
          persist_s=base_outcome.get("persist_s", 0.0),
      ))
    for ref in sorted(artifact_only_refs, key=lambda item: item.jid):
      outcomes.append(_scheduler_jid_outcome(
          ok=True,
          jid=ref.jid,
          metrics_s=0.0,
          prewarm_s=0.0,
          telemetry=telem,
          batch_exception=True,
          fallback_failed=False,
          failure_kind="artifact_only",
          error_type=None,
          error_message=None,
          persist_s=0.0,
      ))
    timing["metrics_wall_s"] = max(0.0, time.monotonic() - t_batch)
    timing["prewarm_wall_s"] = 0.0
    timing["batch_wall_s"] = max(0.0, time.monotonic() - t_batch)
    return outcomes
  if t_metrics_start is not None:
    timing["metrics_wall_s"] = max(0.0, time.monotonic() - t_metrics_start)
  else:
    timing["metrics_wall_s"] = 0.0
  metrics_elapsed = timing["metrics_wall_s"]
  n = max(1, len(metrics_job_refs))
  per_metrics = metrics_elapsed / n if metrics_job_refs else 0.0
  telem = _empty_jid_outcome_telemetry()
  by_jid = {}
  for base_outcome in metrics_run_outcomes or []:
    if not isinstance(base_outcome, dict):
      continue
    by_jid[str(base_outcome.get("jid"))] = base_outcome
  normalized = []
  successful_refs = []
  for ref in job_refs:
    if bool(getattr(ref, "artifact_only", False)):
      base_outcome = {
          "jid": ref.jid,
          "ok": True,
          "status": "artifact_only",
          "error_type": None,
          "error_message": None,
          "persist_s": 0.0,
      }
    else:
      base_outcome = by_jid.get(str(ref.jid))
      if base_outcome is None:
        base_outcome = {
            "jid": ref.jid,
            "ok": False,
            "status": "missing_run_outcome",
            "error_type": "MissingRunOutcome",
            "error_message": "Metrics.run returned no per-jid outcome",
            "persist_s": 0.0,
        }
    normalized.append((ref, base_outcome))
    if base_outcome.get("ok"):
      successful_refs.append(ref)
  if skip_prewarm:
    timing["prewarm_wall_s"] = 0.0
    timing["batch_wall_s"] = max(0.0, time.monotonic() - t_batch)
    ordered = sorted(normalized, key=lambda item: item[0].jid)
    return [
        _scheduler_jid_outcome(
            ok=bool(base_outcome.get("ok")),
            jid=ref.jid,
            metrics_s=(0.0 if bool(getattr(ref, "artifact_only", False)) else per_metrics),
            prewarm_s=0.0,
            telemetry=telem,
            batch_exception=False,
            fallback_failed=False,
            failure_kind=base_outcome.get("status"),
            error_type=base_outcome.get("error_type"),
            error_message=base_outcome.get("error_message"),
            persist_s=base_outcome.get("persist_s", 0.0),
        )
        for ref, base_outcome in ordered
    ]

  t_prewarm = time.monotonic()
  for job_ref in successful_refs:
    if shutdown_requested[0]:
      break
    prewarm_pipeline.submit(job_ref.jid)
  drain_started = time.monotonic()
  drain_budget_s = _effective_prewarm_drain_batch_budget_s(len(successful_refs))
  while prewarm_pipeline.has_pending():
    if shutdown_requested[0]:
      break
    if (time.monotonic() - drain_started) >= drain_budget_s:
      prewarm_snapshot = prewarm_pipeline.stats()
      pending = prewarm_snapshot.get("prewarm_backlog_jobs", 0)
      oldest_age_s = prewarm_snapshot.get("prewarm_oldest_pending_age_s", 0.0)
      log_print(
          "metrics scheduler: prewarm drain budget hit after {0:.1f}s "
          "(budget_s={4:.2f}) size={1} pending={2} "
          "oldest_pending_age_s={3:.3f}; remaining work deferred".format(
              time.monotonic() - drain_started,
              len(job_refs),
              int(pending),
              float(oldest_age_s),
              float(drain_budget_s),
          ),
          flush=True,
      )
      break
    prewarm_pipeline.drain_some()
  prewarm_elapsed = time.monotonic() - t_prewarm
  timing["prewarm_wall_s"] = max(0.0, prewarm_elapsed)
  timing["batch_wall_s"] = max(0.0, time.monotonic() - t_batch)
  per_prewarm = prewarm_elapsed / n
  ordered = sorted(normalized, key=lambda item: item[0].jid)
  return [
      _scheduler_jid_outcome(
          ok=bool(base_outcome.get("ok")),
          jid=ref.jid,
          metrics_s=(0.0 if bool(getattr(ref, "artifact_only", False)) else per_metrics),
          prewarm_s=(per_prewarm if base_outcome.get("ok") else 0.0),
          telemetry=telem,
          batch_exception=False,
          fallback_failed=False,
          failure_kind=base_outcome.get("status"),
          error_type=base_outcome.get("error_type"),
          error_message=base_outcome.get("error_message"),
          persist_s=base_outcome.get("persist_s", 0.0),
      )
      for ref, base_outcome in ordered
  ]


def update_metrics_for_dates(dates, rerun=False):
  """Global scheduler across dates to keep workers saturated."""
  global LAST_UPDATE_METRICS_DIAGNOSTICS
  LAST_UPDATE_METRICS_DIAGNOSTICS = None
  close_old_connections()
  reset_metrics_coverage_defer_log_session()
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
    scheduled_stall_exit = None
    with _pg_session_statement_timeout_for_metrics_batch():
      metrics_manager = metrics.Metrics()
      prewarm_pipeline = _PrewarmPipeline()
      ready_queue = deque()
      rescan_lock = threading.Lock()
      rescan_candidate_jids = deque()
      rescan_seen_jids = set()
      rescan_seen_order = deque()
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
      rescan_seen_cap = max(
          RESCAN_SEEN_MIN_CAP,
          prefetch_ready_cap * RESCAN_SEEN_MULTIPLIER,
      )
      strict_check_cooldown_until = {}
      scheduler_shared_lock = threading.Lock()
      completion_reporter = _CompletionReporter()

      def _extra_stats_snapshot():
        prewarm_snapshot = prewarm_pipeline.stats()
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
              "worker_failed_outcomes_total": stats["worker_failed_outcomes_total"],
              "parent_persist_failures_total": stats["parent_persist_failures_total"],
              "attempted_total": stats["attempted_total"],
              "public_ef_degraded": stats["public_ef_degraded"],
              "public_ef_worker_exceptions_total": stats["public_ef_worker_exceptions_total"],
              "public_ef_watchdog_timeouts_total": stats["public_ef_watchdog_timeouts_total"],
              "public_ef_pending_tasks": stats["public_ef_pending_tasks"],
              "prewarm_backlog_jobs": prewarm_snapshot["prewarm_backlog_jobs"],
              "prewarm_oldest_pending_age_s": prewarm_snapshot["prewarm_oldest_pending_age_s"],
              "prewarm_backpressure_events": prewarm_snapshot["prewarm_backpressure_events"],
              "prewarm_inline_fallback_jobs": prewarm_snapshot["prewarm_inline_fallback_jobs"],
          }

      completion_reporter.set_extra_stats_getter(_extra_stats_snapshot)
      completion_reporter.start()
      batch_cap = min(prefetch_target, GLOBAL_SCHEDULER_BATCH_SIZE)
      worker_count = max(1, int(cfg.get_metrics_pool_process_count()))
      worker_scaled_cap = max(
          COMPUTE_BATCH_MIN_CAP,
          worker_count * COMPUTE_BATCH_WORKER_MULTIPLIER,
      )
      effective_batch_cap = max(
          COMPUTE_BATCH_MIN_CAP,
          min(
              int(batch_cap),
              int(worker_scaled_cap),
              int(COMPUTE_BATCH_ABSOLUTE_MAX),
          ),
      )
      log_print(
          "Starting metrics scheduler days={0} mode={1} prefetch_ready_cap={2} "
          "compute_batch_cap={3} worker_count={4} prewarm_mode={5}".format(
              len(dates),
              scheduler_mode,
              prefetch_ready_cap,
              effective_batch_cap,
              worker_count,
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
      shared_pool = metrics_manager.ensure_pool(pool_kind="public-ef-pool")
      log_print("Metrics worker pool ready.", flush=True)
      pub_stats = _run_public_ef_artifacts_parallel_phase(shared_pool, phase_timer)
      with scheduler_shared_lock:
        stats["public_ef_degraded"] = int(pub_stats.get("degraded", 0))
        stats["public_ef_worker_exceptions_total"] = int(
            pub_stats.get("worker_exceptions", 0)
        )
        stats["public_ef_watchdog_timeouts_total"] = int(
            pub_stats.get("watchdog_timeouts", 0)
        )
        stats["public_ef_pending_tasks"] = int(pub_stats.get("pending_tasks", 0))
      _reset_metrics_pool_after_public_phase(metrics_manager)
      shared_pool = metrics_manager.ensure_pool(pool_kind="metrics-pool")
      log_print("Metrics worker pool recycled after /pub phase.", flush=True)
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
          rescan_seen_order=rescan_seen_order,
          rescan_seen_cap=rescan_seen_cap,
          rescan_lock=rescan_lock,
      )
      rescan_thread = _start_candidate_rescan_thread(
          dates=dates,
          min_time=min_time,
          rerun=rerun,
          rescan_candidate_jids=rescan_candidate_jids,
          rescan_seen_jids=rescan_seen_jids,
          rescan_seen_order=rescan_seen_order,
          rescan_seen_cap=rescan_seen_cap,
          rescan_lock=rescan_lock,
          stop_event=rescan_stop_event,
      )
      t0 = time.monotonic()
      compute_batches = 0
      stall_iters = 0
      consumer_stall_since = None
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
          if _maybe_trigger_consumer_stall_exit(
              stats, consumer_stall_since, scheduler_shared_lock,
          ):
            break
          with ready_queue_lock:
            if ready_queue:
              jobs_this_round = _pop_candidates_for_compute_batch_locked(
                  ready_queue,
                  effective_batch_cap,
              )
            else:
              jobs_this_round = []
          with scheduler_shared_lock:
            if jobs_this_round:
              stats["ready_dequeued_total"] += len(jobs_this_round)
              stats["inflight_jids"] += len(jobs_this_round)
          if not jobs_this_round:
            if _maybe_trigger_consumer_stall_exit(
                stats, consumer_stall_since, scheduler_shared_lock,
            ):
              break
            if producer_done.is_set():
              with scheduler_shared_lock:
                proc = int(stats["processed"])
                reason = stats.get("stall_reason") or ""
              if (
                  proc == 0
                  and reason in CONSUMER_STALL_EXIT_REASONS
                  and consumer_stall_since is not None
              ):
                if not _maybe_trigger_consumer_stall_exit(
                    stats, consumer_stall_since, scheduler_shared_lock,
                ):
                  time.sleep(0.05)
                  continue
              break
            stall_iters += 1
            with scheduler_shared_lock:
              inflight = stats["inflight_jids"]
              proc = int(stats["processed"])
              if inflight > 0 and not stats.get("stall_reason"):
                stats["stall_reason"] = "compute_stuck_inflight"
            if inflight > 0 and proc == 0:
              if consumer_stall_since is None:
                consumer_stall_since = time.monotonic()
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
            if _maybe_trigger_consumer_stall_exit(
                stats, consumer_stall_since, scheduler_shared_lock,
            ):
              break
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
          batch_phase_timing = {}
          with phase_timer.phase("metrics_compute_s"):
            succeeded_jids = []
            failed_count = 0
            jid_outcomes = _compute_jid_outcomes_batch(
                job_refs,
                metrics_manager,
                prewarm_pipeline,
                shared_pool,
                batch_timing=batch_phase_timing,
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
          metrics_watchdog_s = float(cfg.get_metrics_compute_watchdog_seconds())
          total_watchdog_s = float(cfg.get_metrics_compute_total_watchdog_seconds())
          metrics_phase_s = float(batch_phase_timing.get("metrics_wall_s", batch_elapsed))
          prewarm_phase_s = float(batch_phase_timing.get("prewarm_wall_s", 0.0))
          batch_wall_s = float(batch_phase_timing.get("batch_wall_s", batch_elapsed))
          downshift = False
          if metrics_phase_s >= metrics_watchdog_s:
            downshift = True
          if total_watchdog_s > 0.0 and batch_wall_s >= total_watchdog_s:
            downshift = True
          has_batch_exception = any(bool(o.get("_batch_exception")) for o in jid_outcomes)
          fallback_failed = sum(1 for o in jid_outcomes if bool(o.get("_fallback_failed")))
          worker_failed_outcomes = sum(
              1
              for o in jid_outcomes
              if (not o["ok"])
              and o.get("failure_kind")
              in ("worker_db_error", "worker_compute_error", "worker_stall_timeout")
          )
          parent_persist_failures = sum(
              1
              for o in jid_outcomes
              if (not o["ok"]) and str(o.get("failure_kind") or "").startswith("parent_persist")
          )
          with scheduler_shared_lock:
            stats["processed"] += len(succeeded_jids)
            stats["failed"] += failed_count
            stats["attempted_total"] = stats["processed"] + stats["failed"]
            stats["compute_batches_total"] += 1
            if has_batch_exception:
              stats["batch_compute_exceptions_total"] += 1
            stats["per_jid_fallback_failures_total"] += fallback_failed
            stats["worker_failed_outcomes_total"] += worker_failed_outcomes
            stats["parent_persist_failures_total"] += parent_persist_failures
            stats["inflight_jids"] = max(0, stats["inflight_jids"] - len(job_refs))
            proc_total = stats["processed"]
            fail_total = stats["failed"]
            attempted_total = stats["attempted_total"]
            if attempted_total > 0 and proc_total == 0 and fail_total > 0:
              if parent_persist_failures > 0:
                stats["stall_reason"] = "parent_persist_failed"
              elif worker_failed_outcomes > 0:
                stats["stall_reason"] = "worker_failed_outcomes"
              else:
                stats["stall_reason"] = "compute_all_failed"
          if proc_total > 0:
            consumer_stall_since = None
          elif proc_total == 0 and fail_total > 0:
            with scheduler_shared_lock:
              reason = stats.get("stall_reason") or ""
            if reason in CONSUMER_STALL_EXIT_REASONS:
              if consumer_stall_since is None:
                consumer_stall_since = time.monotonic()
              elif _maybe_trigger_consumer_stall_exit(
                  stats, consumer_stall_since, scheduler_shared_lock,
              ):
                break
          completion_reporter.sync_completed_total(proc_total)
          compute_batches += 1
          if downshift:
            new_cap = max(
                COMPUTE_BATCH_MIN_CAP,
                int(max(COMPUTE_BATCH_MIN_CAP, effective_batch_cap) * COMPUTE_BATCH_DOWNSHIFT_FACTOR),
            )
            if new_cap < effective_batch_cap:
              effective_batch_cap = new_cap
            log_print(
                "metrics scheduler: compute watchdog metrics_phase_elapsed_s={0:.1f} "
                "prewarm_phase_elapsed_s={1:.1f} batch_wall_s={2:.1f} "
                "metrics_watchdog_s={3:.1f} total_watchdog_s={4:.1f} size={5} attempted_total={6} "
                "new_compute_batch_cap={7}".format(
                    metrics_phase_s,
                    prewarm_phase_s,
                    batch_wall_s,
                    metrics_watchdog_s,
                    total_watchdog_s,
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
                "worker_failed_outcomes_total={7} parent_persist_failures_total={8} "
                "compute_batch_elapsed_s={9:.2f} next_batch_cap={10}".format(
                    compute_batches,
                    len(job_refs),
                    proc_total,
                    fail_total,
                    attempted_total,
                    int(has_batch_exception),
                    fallback_failed,
                    worker_failed_outcomes,
                    parent_persist_failures,
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
          refresh_public_expansion_factor_artifacts_safe()
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
            "worker_failed_outcomes_total": stats["worker_failed_outcomes_total"],
            "parent_persist_failures_total": stats["parent_persist_failures_total"],
            "attempted_total": stats["attempted_total"],
            "public_ef_degraded": stats["public_ef_degraded"],
            "public_ef_worker_exceptions_total": stats["public_ef_worker_exceptions_total"],
            "public_ef_watchdog_timeouts_total": stats["public_ef_watchdog_timeouts_total"],
            "public_ef_pending_tasks": stats["public_ef_pending_tasks"],
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
          "per_jid_fallback_failures_total={22} worker_failed_outcomes_total={23} "
          "parent_persist_failures_total={24} attempted_total={25} "
          "public_ef_degraded={26} public_ef_worker_exceptions_total={27} "
          "public_ef_watchdog_timeouts_total={28} public_ef_pending_tasks={29} "
          "readiness_probe_target={30} strict_batch_size_current={31} strict_check_calls={32} "
          "strict_check_timeouts={33} strict_check_avg_latency_ms={34:.2f} "
          "completed_last_hour={35} elapsed_s={36:.2f} jobs_per_min={37:.2f} "
          "worker_busy_ratio={38:.3f} phase_candidate_s={39:.2f} "
          "phase_readiness_s={40:.2f} phase_pub_ef_s={41:.2f} phase_compute_s={42:.2f} phase_prewarm_s={43:.2f} "
          "prewarm_backlog_jobs={44} prewarm_oldest_pending_age_s={45:.3f} "
          "prewarm_lag_seconds_p95={46:.3f} prewarm_success_ratio={47:.3f} "
          "prewarm_backpressure_events={48} prewarm_inline_fallback_jobs={49}{50}".format(
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
              snap["worker_failed_outcomes_total"],
              snap["parent_persist_failures_total"],
              snap["attempted_total"],
              snap["public_ef_degraded"],
              snap["public_ef_worker_exceptions_total"],
              snap["public_ef_watchdog_timeouts_total"],
              snap["public_ef_pending_tasks"],
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
              totals.get("public_ef_artifacts_s", 0.0),
              totals["metrics_compute_s"],
              totals["prewarm_s"],
              prewarm_stats["prewarm_backlog_jobs"],
              prewarm_stats["prewarm_oldest_pending_age_s"],
              prewarm_stats["prewarm_lag_seconds_p95"],
              prewarm_stats["prewarm_success_ratio"],
              prewarm_stats["prewarm_backpressure_events"],
              prewarm_stats["prewarm_inline_fallback_jobs"],
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

      if snap.get("stall_exit_triggered"):
        log_print(
            "metrics scheduler: stall exit triggered (stall_reason={0}); "
            "exiting for supervisor restart.".format(
                snap.get("stall_reason") or "unknown"
            ),
            flush=True,
        )
        scheduled_stall_exit = MetricsSchedulerStallExit(
            stall_reason=snap.get("stall_reason"),
        )
    if scheduled_stall_exit is not None:
      raise scheduled_stall_exit

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
  from hpcperfstats.process_title import set_daemon_process_title

  set_daemon_process_title(name=UPDATE_METRICS_PROCESS_TITLE, role="main")
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
  except DatabaseUnavailableExit:
    sys.exit(2)
  except MetricsSchedulerStallExit as exc:
    sys.exit(exc.exit_code)
  finally:
    _shutdown_db_best_effort()
    _notify_parent_if_sigterm(sigterm_received)
    if previous_sigterm_handler is not None:
      try:
        signal.signal(signal.SIGTERM, previous_sigterm_handler)
      except Exception:
        pass
