"""
In-worker memory release metadata and supervisor batch telemetry for ingest
pools.

Attributes:
  REAP_FAILURE: Attribute.
  REAP_KEEP: Attribute.
  REAP_RSS: Attribute.
  _FAILED_OUTCOMES: Attribute.
  _WORKER_TASKS_ON_WORKER: Attribute.
"""

from __future__ import annotations

from typing import Any

import os
import time

from hpcperfstats.dbload.lib.print_utils import log_print

REAP_KEEP = "keep"
REAP_FAILURE = "failure_reap"
REAP_RSS = "rss_reap"

_FAILED_OUTCOMES = frozenset({
    "parse_fail",
    "timeout",
    "lookup_budget",
    "quarantine",
    "active_segment",
})

_WORKER_TASKS_ON_WORKER = 0


def reset_worker_tasks_on_worker_for_tests() -> None:
  """
  Reset worker tasks on worker for tests.
  
  Returns:
    None
  
  Examples:
    >>> reset_worker_tasks_on_worker_for_tests()  # doctest: +SKIP
  """
  global _WORKER_TASKS_ON_WORKER
  _WORKER_TASKS_ON_WORKER = 0


def increment_worker_tasks_on_worker() -> Any:
  """
  Increment worker tasks on worker.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> increment_worker_tasks_on_worker()  # doctest: +SKIP
  """
  global _WORKER_TASKS_ON_WORKER
  _WORKER_TASKS_ON_WORKER += 1
  return _WORKER_TASKS_ON_WORKER


def get_worker_tasks_on_worker() -> Any:
  """
  Return the worker tasks on worker.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> get_worker_tasks_on_worker()  # doctest: +SKIP
  """
  return _WORKER_TASKS_ON_WORKER


def release_spawn_pool_worker_memory() -> None:
  """
  Drop worker-local caches and return heap after any spawn pool task.
  
  Shared by ingest, archive, sealed-archive CLI, populate, metrics, and
  public expansion-factor pools. Ingest telemetry (task counter + RSS meta)
  is applied separately by ``sync_timedb._release_ingest_worker_memory``.
  
  Returns:
    None
  
  Examples:
    >>> release_spawn_pool_worker_memory()  # doctest: +SKIP
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


def ingest_pool_width() -> Any:
  """
  Ingest the pool width.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> ingest_pool_width()  # doctest: +SKIP
  """
  import hpcperfstats.dbload.lib.conf_parser as cfg

  cap = int(cfg.get_sync_pool_process_cap())
  width = int(cfg.get_sync_ingest_pool_processes())
  return max(1, min(cap, width))


def compute_rss_recycle_threshold_mib() -> Any:
  """
  Compute the rss recycle threshold mib.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> compute_rss_recycle_threshold_mib()  # doctest: +SKIP
  """
  import hpcperfstats.dbload.lib.conf_parser as cfg

  tree_limit = int(cfg.get_sync_process_tree_rss_limit_mb())
  if tree_limit <= 0:
    return 0.0
  fraction = float(cfg.get_sync_ingest_cooperative_recycle_rss_fraction())
  return round(fraction * tree_limit / ingest_pool_width(), 1)


def _worker_rss_mib() -> Any:
  """
  Internal helper to handle worker rss mib.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _worker_rss_mib()  # doctest: +SKIP
  """
  from hpcperfstats.dbload.lib.process_memory import read_process_rss_bytes

  rss_bytes = read_process_rss_bytes()
  if rss_bytes <= 0:
    return 0.0
  return round(rss_bytes / (1024 * 1024), 1)


def measure_worker_rss_after_release(stats_file: str) -> Any:
  """
  Measure RSS after release; optional recheck when above threshold.
  
  Args:
    stats_file (str): String for stats file.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> measure_worker_rss_after_release("x")  # doctest: +SKIP
  """
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
  # Informational only (soak logs / giant pool supplement context); does not retire.
  giant = "yes" if is_giant_ingest_budget(stats_file) else "no"
  request_worker_recycle = "no"
  if threshold > 0 and rss_mib > threshold:
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


def resolve_worker_pid_from_meta_or_registry(
  meta: Any,
  registry: Any,
  path: str,
) -> Any:
  """
  Resolve worker PID from outcome meta or diagnostics registry.
  
  Args:
    meta (Any): Meta passed to this helper.
    registry (Any): Registry passed to this helper.
    path (str): String for path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> resolve_worker_pid_from_meta_or_registry(None, None, "x")
  """
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


def _rss_threshold_and_mib(meta: Any) -> Any:
  """
  Internal helper to handle rss threshold and mib.
  
  Args:
    meta (Any): Meta passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _rss_threshold_and_mib(None)  # doctest: +SKIP
  """
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
  return threshold, rss_mib


def classify_supervisor_reap_kind(
  *,
  ingest_ok: Any,
  outcome: Any,
  meta: Any,
  path: str,
) -> Any:
  """
  Classify supervisor reap kind.
  
  Args:
    ingest_ok (Any): Ingest ok passed to this helper.
    outcome (Any): Outcome passed to this helper.
    meta (Any): Meta passed to this helper.
    path (str): String for path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> classify_supervisor_reap_kind(None, None, None, "x")  # doctest: +SKIP
  """
  _ = path
  if not ingest_ok:
    return REAP_FAILURE
  outcome_s = str(outcome or "")
  if outcome_s in _FAILED_OUTCOMES:
    return REAP_FAILURE
  # RC-J: never supervisor-retire on db_skip from path budget alone; RSS may still.
  if outcome_s == "db_skip":
    threshold, rss_mib = _rss_threshold_and_mib(meta)
    if threshold > 0 and rss_mib > threshold:
      return REAP_RSS
    return REAP_KEEP
  threshold, rss_mib = _rss_threshold_and_mib(meta)
  if threshold > 0 and rss_mib > threshold:
    return REAP_RSS
  if isinstance(meta, dict) and str(meta.get("request_worker_recycle") or "") == "yes":
    if threshold > 0 and rss_mib > threshold:
      return REAP_RSS
  return REAP_KEEP


def should_supervisor_retire_worker(reap_kind: Any) -> Any:
  """
  Return True if supervisor retire worker.
  
  Args:
    reap_kind (Any): Reap kind passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> should_supervisor_retire_worker(None)  # doctest: +SKIP
  """
  import hpcperfstats.dbload.lib.conf_parser as cfg

  if reap_kind == REAP_KEEP:
    return False
  if cfg.get_sync_ingest_pool_maxtasksperchild() > 0:
    return False
  if reap_kind == REAP_FAILURE:
    return cfg.get_sync_ingest_recycle_worker_on_failure()
  return True


def should_defer_supervisor_retire(
  reap_kind: Any,
  *,
  accumulator: Any | None = None,
  pending_inflight: Any | None = None,
  max_inflight: Any | None = None,
) -> Any:
  """
  Defer cooperative retire during catch-up when the pool is near max inflight.
  
  Args:
    reap_kind (Any): Reap kind passed to this helper.
    accumulator (Any | None): One of ``Any``, ``None``.
    pending_inflight (Any | None): One of ``Any``, ``None``.
    max_inflight (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> should_defer_supervisor_retire(None, None, None, None)  # doctest: +SKIP
  """
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
    catchup_retires = accumulator.retires_rss_reap
    if catchup_retires >= max(2, accumulator.retires_total):
      return accumulator.retires_this_window >= max(1, retire_cap - 1)
  return False


def _percentile(values: Any, pct: Any) -> Any:
  """
  Internal helper to handle percentile.
  
  Args:
    values (Any): Values passed to this helper.
    pct (Any): Pct passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _percentile(None, None)  # doctest: +SKIP
  """
  if not values:
    return 0
  ordered = sorted(values)
  idx = int(round((pct / 100.0) * (len(ordered) - 1)))
  idx = max(0, min(len(ordered) - 1, idx))
  return ordered[idx]


def _pct(count: int, total: Any) -> Any:
  """
  Internal helper to handle pct.
  
  Args:
    count (int): Integer value for count.
    total (Any): Total passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _pct(0, None)  # doctest: +SKIP
  """
  if total <= 0:
    return 0.0
  return round(100.0 * count / total, 1)


class WorkerMemoryBatchAccumulator:
  """
  In-memory batch counters; one ``batch_summary`` log line per ingest chunk.
  
  Attributes:
    _chunks_since_flush: Attribute.
    _rss_mib_after: Attribute.
    _tasks_on_worker: Attribute.
    completions: Attribute.
    keep_worker: Attribute.
    retires_failure_reap: Attribute.
    retires_rss_reap: Attribute.
    retires_this_window: Attribute.
    rss_recheck_fired: Attribute.
  """

  def __init__(self) -> None:
    """
    Initialize a new instance.
    
    Returns:
      None
    
    Examples:
      >>> WorkerMemoryBatchAccumulator()  # doctest: +SKIP
    """
    self.completions = 0
    self.keep_worker = 0
    self.retires_failure_reap = 0
    self.retires_rss_reap = 0
    self.retires_this_window = 0
    self._tasks_on_worker = []
    self._rss_mib_after = []
    self.rss_recheck_fired = 0
    self._chunks_since_flush = 0

  @property
  def retires_total(self) -> Any:
    """
    Retires total.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> WorkerMemoryBatchAccumulator().retires_total()  # doctest: +SKIP
    """
    return self.retires_failure_reap + self.retires_rss_reap

  def record_completion(self, reap_kind: Any, meta: Any | None = None) -> None:
    """
    Record completion.
    
    Args:
      reap_kind (Any): Reap kind passed to this helper.
      meta (Any | None): One of ``Any``, ``None``.
    
    Returns:
      None
    
    Examples:
      >>> WorkerMemoryBatchAccumulator().record_completion(None, None)
    """
    self.completions += 1
    if reap_kind == REAP_KEEP:
      self.keep_worker += 1
    elif reap_kind == REAP_FAILURE:
      self.retires_failure_reap += 1
      self.retires_this_window += 1
    elif reap_kind == REAP_RSS:
      self.retires_rss_reap += 1
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

  def maybe_flush(
    self,
    chunk_index: Any,
    *,
    ingest_pool: Any | None = None,
    archive_pool: Any | None = None,
  ) -> None:
    """
    Maybe flush.
    
    Args:
      chunk_index (Any): Chunk index passed to this helper.
      ingest_pool (Any | None): One of ``Any``, ``None``.
      archive_pool (Any | None): One of ``Any``, ``None``.
    
    Returns:
      None
    
    Examples:
      >>> WorkerMemoryBatchAccumulator().maybe_flush(None, None, None)
    """
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
        "retires_failure_reap=%d retires_rss_reap=%d "
        "retire_rate_pct=%.1f failure_reap_pct=%.1f rss_reap_pct=%.1f "
        "tasks_on_worker_min=%d tasks_on_worker_p50=%d "
        "tasks_on_worker_max=%d rss_mib_after_p50=%.1f rss_mib_after_max=%.1f "
        "rss_recheck_fired=%d tree_rss_mib=%.1f ingest_pool_rss_mib=%.1f "
        "threshold_mib=%.1f maxtasksperchild=%d"
        % (
            int(chunk_index),
            total,
            self.keep_worker,
            retires_total,
            self.retires_failure_reap,
            self.retires_rss_reap,
            _pct(retires_total, total),
            _pct(self.retires_failure_reap, total),
            _pct(self.retires_rss_reap, total),
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
        ),
        flush=True,
    )
    self.completions = 0
    self.keep_worker = 0
    self.retires_failure_reap = 0
    self.retires_rss_reap = 0
    self.retires_this_window = 0
    self._tasks_on_worker = []
    self._rss_mib_after = []
    self.rss_recheck_fired = 0
