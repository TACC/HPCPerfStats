"""In-worker memory release metadata and supervisor batch telemetry for ingest pools."""

from __future__ import annotations

import os
import time

from hpcperfstats.dbload.lib.print_utils import log_print

REAP_KEEP = "keep"
REAP_FAILURE = "failure_reap"
REAP_RSS = "rss_reap"
REAP_GIANT = "giant_reap"

_FAILED_OUTCOMES = frozenset({
    "parse_fail",
    "timeout",
    "lookup_budget",
    "quarantine",
    "active_segment",
})

_WORKER_TASKS_ON_WORKER = 0


def reset_worker_tasks_on_worker_for_tests():
  global _WORKER_TASKS_ON_WORKER
  _WORKER_TASKS_ON_WORKER = 0


def increment_worker_tasks_on_worker():
  global _WORKER_TASKS_ON_WORKER
  _WORKER_TASKS_ON_WORKER += 1
  return _WORKER_TASKS_ON_WORKER


def get_worker_tasks_on_worker():
  return _WORKER_TASKS_ON_WORKER


def release_spawn_pool_worker_memory():
  """Drop worker-local caches and return heap after any spawn pool task.

  Shared by ingest, archive, sealed-archive CLI, populate, metrics, and
  public expansion-factor pools. Ingest telemetry (task counter + RSS meta)
  is applied separately by ``sync_timedb._release_ingest_worker_memory``.
  """
  import ctypes
  import gc

  import hpcperfstats.dbload.lib.conf_parser as cfg
  from hpcperfstats.dbload.lib import sync_timedb_host_itimes
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      clear_daily_archive_members_cache,
  )
  from hpcperfstats.dbload.lib.sync_timedb_ingest_worker_diagnostics import (
      clear_worker_stage,
  )

  sync_timedb_host_itimes.reset_host_itimes_caches()
  clear_daily_archive_members_cache()
  if cfg.get_sync_ingest_malloc_trim_after_file():
    gc.collect()
    try:
      ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
      pass
  clear_worker_stage()


def ingest_pool_width():
  import hpcperfstats.dbload.lib.conf_parser as cfg

  cap = int(cfg.get_sync_pool_process_cap())
  width = int(cfg.get_sync_ingest_pool_processes())
  return max(1, min(cap, width))


def compute_rss_recycle_threshold_mib():
  import hpcperfstats.dbload.lib.conf_parser as cfg

  tree_limit = int(cfg.get_sync_process_tree_rss_limit_mb())
  if tree_limit <= 0:
    return 0.0
  fraction = float(cfg.get_sync_ingest_cooperative_recycle_rss_fraction())
  return round(fraction * tree_limit / ingest_pool_width(), 1)


def _worker_rss_mib():
  from hpcperfstats.dbload.lib.process_memory import read_process_rss_bytes

  rss_bytes = read_process_rss_bytes()
  if rss_bytes <= 0:
    return 0.0
  return round(rss_bytes / (1024 * 1024), 1)


def measure_worker_rss_after_release(stats_file):
  """Measure RSS after release; optional recheck when above threshold."""
  import hpcperfstats.dbload.lib.conf_parser as cfg
  from hpcperfstats.dbload.lib.sync_timedb_ingest_timeout import (
      is_giant_ingest_budget,
  )

  threshold = compute_rss_recycle_threshold_mib()
  rss_mib = _worker_rss_mib()
  rss_recheck_fired = "no"
  delay_ms = int(cfg.get_sync_ingest_rss_recheck_delay_ms())
  if threshold > 0 and rss_mib > threshold and delay_ms > 0:
    time.sleep(delay_ms / 1000.0)
    rss_mib = _worker_rss_mib()
    rss_recheck_fired = "yes"
  giant = "yes" if is_giant_ingest_budget(stats_file) else "no"
  request_worker_recycle = "no"
  if giant == "yes" and cfg.get_sync_ingest_cooperative_recycle_after_giant():
    request_worker_recycle = "yes"
  elif threshold > 0 and rss_mib > threshold:
    request_worker_recycle = "yes"
  return {
      "worker_pid": os.getpid(),
      "tasks_on_worker": get_worker_tasks_on_worker(),
      "rss_mib_after_release": rss_mib,
      "recycle_threshold_mib": threshold,
      "giant": giant,
      "rss_recheck_fired": rss_recheck_fired,
      "request_worker_recycle": request_worker_recycle,
  }


def resolve_worker_pid_from_meta_or_registry(meta, registry, path):
  """Resolve worker PID from outcome meta or diagnostics registry."""
  if isinstance(meta, dict):
    worker_pid = meta.get("worker_pid")
    if worker_pid is not None:
      try:
        return int(worker_pid)
      except (TypeError, ValueError):
        pass
  if registry is None or not path:
    return None
  norm_path = os.path.normpath(str(path))
  try:
    items = list(registry.items())
  except Exception:
    return None
  for pid, raw in items:
    pid_s = str(pid)
    if pid_s.startswith("dispatch:"):
      continue
    if not isinstance(raw, dict):
      continue
    entry_path = raw.get("path")
    if entry_path and os.path.normpath(str(entry_path)) == norm_path:
      try:
        return int(pid_s)
      except ValueError:
        continue
  return None


def classify_supervisor_reap_kind(
    *,
    ingest_ok,
    outcome,
    meta,
    path,
):
  import hpcperfstats.dbload.lib.conf_parser as cfg
  from hpcperfstats.dbload.lib.sync_timedb_ingest_timeout import (
      is_giant_ingest_budget,
  )

  if not ingest_ok:
    return REAP_FAILURE
  outcome_s = str(outcome or "")
  if outcome_s in _FAILED_OUTCOMES:
    return REAP_FAILURE
  if (
      cfg.get_sync_ingest_cooperative_recycle_after_giant()
      and is_giant_ingest_budget(path)
  ):
    return REAP_GIANT
  threshold = 0.0
  rss_mib = 0.0
  if isinstance(meta, dict):
    try:
      threshold = float(meta.get("recycle_threshold_mib") or 0.0)
    except (TypeError, ValueError):
      threshold = 0.0
    try:
      rss_mib = float(meta.get("rss_mib_after_release") or 0.0)
    except (TypeError, ValueError):
      rss_mib = 0.0
  if threshold <= 0:
    threshold = compute_rss_recycle_threshold_mib()
  if threshold > 0 and rss_mib > threshold:
    return REAP_RSS
  if isinstance(meta, dict) and str(meta.get("request_worker_recycle") or "") == "yes":
    if is_giant_ingest_budget(path) and cfg.get_sync_ingest_cooperative_recycle_after_giant():
      return REAP_GIANT
    if threshold > 0 and rss_mib > threshold:
      return REAP_RSS
  return REAP_KEEP


def should_supervisor_retire_worker(reap_kind):
  import hpcperfstats.dbload.lib.conf_parser as cfg

  if reap_kind == REAP_KEEP:
    return False
  if cfg.get_sync_ingest_pool_maxtasksperchild() > 0:
    return False
  if reap_kind == REAP_FAILURE:
    return cfg.get_sync_ingest_recycle_worker_on_failure()
  return True


def should_defer_supervisor_retire(
    reap_kind,
    *,
    accumulator=None,
    pending_inflight=None,
    max_inflight=None,
):
  """Defer cooperative retire during catch-up when the pool is near max inflight."""
  import hpcperfstats.dbload.lib.conf_parser as cfg

  if reap_kind in (REAP_KEEP, REAP_FAILURE):
    return False
  if cfg.get_sync_ingest_pool_maxtasksperchild() > 0:
    return False
  try:
    inflight = int(pending_inflight or 0)
    cap = int(max_inflight or 0)
  except (TypeError, ValueError):
    return False
  if cap <= 0 or inflight < max(1, cap - 1):
    return False
  if accumulator is None:
    return False
  retire_cap = max(1, cap // 3)
  if accumulator.retires_this_window >= retire_cap:
    return True
  if accumulator.completions >= 8:
    catchup_retires = accumulator.retires_giant_reap + accumulator.retires_rss_reap
    if catchup_retires >= max(2, accumulator.retires_total):
      return accumulator.retires_this_window >= max(1, retire_cap - 1)
  return False


def _percentile(values, pct):
  if not values:
    return 0
  ordered = sorted(values)
  idx = int(round((pct / 100.0) * (len(ordered) - 1)))
  idx = max(0, min(len(ordered) - 1, idx))
  return ordered[idx]


def _pct(count, total):
  if total <= 0:
    return 0.0
  return round(100.0 * count / total, 1)


class WorkerMemoryBatchAccumulator:
  """In-memory batch counters; one ``batch_summary`` log line per ingest chunk."""

  def __init__(self):
    self.completions = 0
    self.keep_worker = 0
    self.retires_failure_reap = 0
    self.retires_rss_reap = 0
    self.retires_giant_reap = 0
    self.retires_this_window = 0
    self._tasks_on_worker = []
    self._rss_mib_after = []
    self.rss_recheck_fired = 0
    self._chunks_since_flush = 0

  @property
  def retires_total(self):
    return (
        self.retires_failure_reap
        + self.retires_rss_reap
        + self.retires_giant_reap
    )

  def record_completion(self, reap_kind, meta=None):
    self.completions += 1
    if reap_kind == REAP_KEEP:
      self.keep_worker += 1
    elif reap_kind == REAP_FAILURE:
      self.retires_failure_reap += 1
      self.retires_this_window += 1
    elif reap_kind == REAP_RSS:
      self.retires_rss_reap += 1
      self.retires_this_window += 1
    elif reap_kind == REAP_GIANT:
      self.retires_giant_reap += 1
      self.retires_this_window += 1
    if isinstance(meta, dict):
      try:
        self._tasks_on_worker.append(int(meta.get("tasks_on_worker") or 0))
      except (TypeError, ValueError):
        pass
      try:
        self._rss_mib_after.append(float(meta.get("rss_mib_after_release") or 0.0))
      except (TypeError, ValueError):
        pass
      if str(meta.get("rss_recheck_fired") or "") == "yes":
        self.rss_recheck_fired += 1

  def maybe_flush(self, chunk_index, *, ingest_pool=None, archive_pool=None):
    import hpcperfstats.dbload.lib.conf_parser as cfg
    from hpcperfstats.dbload.lib.process_memory import format_tree_rss_breakdown_mb

    if not cfg.get_sync_ingest_worker_memory_telemetry():
      return
    every_n = max(1, int(cfg.get_sync_ingest_worker_memory_telemetry_every_n_chunks()))
    self._chunks_since_flush += 1
    if self._chunks_since_flush < every_n:
      return
    self._chunks_since_flush = 0
    if self.completions <= 0:
      return
    breakdown = format_tree_rss_breakdown_mb(ingest_pool, archive_pool)
    threshold = compute_rss_recycle_threshold_mib()
    total = self.completions
    retires_total = self.retires_total
    log_print(
        "INFO: sync_timedb worker_memory: event=batch_summary batch=%d "
        "completions=%d keep_worker=%d retires_total=%d "
        "retires_failure_reap=%d retires_rss_reap=%d retires_giant_reap=%d "
        "retire_rate_pct=%.1f failure_reap_pct=%.1f rss_reap_pct=%.1f "
        "giant_reap_pct=%.1f tasks_on_worker_min=%d tasks_on_worker_p50=%d "
        "tasks_on_worker_max=%d rss_mib_after_p50=%.1f rss_mib_after_max=%.1f "
        "rss_recheck_fired=%d tree_rss_mib=%.1f ingest_pool_rss_mib=%.1f "
        "threshold_mib=%.1f maxtasksperchild=%d cooperative_recycle_after_giant=%s"
        % (
            int(chunk_index),
            total,
            self.keep_worker,
            retires_total,
            self.retires_failure_reap,
            self.retires_rss_reap,
            self.retires_giant_reap,
            _pct(retires_total, total),
            _pct(self.retires_failure_reap, total),
            _pct(self.retires_rss_reap, total),
            _pct(self.retires_giant_reap, total),
            min(self._tasks_on_worker) if self._tasks_on_worker else 0,
            _percentile(self._tasks_on_worker, 50),
            max(self._tasks_on_worker) if self._tasks_on_worker else 0,
            _percentile(self._rss_mib_after, 50),
            max(self._rss_mib_after) if self._rss_mib_after else 0.0,
            self.rss_recheck_fired,
            breakdown.get("tree_total_mb", 0.0),
            breakdown.get("ingest_pool_mb", 0.0),
            threshold,
            cfg.get_sync_ingest_pool_maxtasksperchild(),
            "yes" if cfg.get_sync_ingest_cooperative_recycle_after_giant() else "no",
        ),
        flush=True,
    )
    self.completions = 0
    self.keep_worker = 0
    self.retires_failure_reap = 0
    self.retires_rss_reap = 0
    self.retires_giant_reap = 0
    self.retires_this_window = 0
    self._tasks_on_worker = []
    self._rss_mib_after = []
    self.rss_recheck_fired = 0

