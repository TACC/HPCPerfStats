#!/usr/bin/env python3
"""
Load raw stats files into TimescaleDB (host_data, proc_data).

Production coordinator is the greenfield ``job:v1`` queue orchestrator
(``run_sync_timedb_queue_orchestrator``): exclusive ``archive_dir`` flock,
streaming discover → ingest/append/day_close Redis jobs, sliding-window spawn
pools for ingest/append, and day_close threads (seal → raw removal → tar-drop).
The B ``run_sync_timedb_supervisor_loop`` / ``ArchiveJanitor`` coordinator is
retired.

CLI: no args (``startdate=enddate=None``) streams the full GNU find into
single orchestrator with hot+catchup ingest bands over the archive. One
``YYYY-MM-DD`` or two dates set an explicit discover range hint at entry
(orchestrator reconstruct still uses full find). Prefix ``once`` to exit after
one idle reconstruct pass. ``backlog`` / ``current`` dual-mode CLI is retired.
``--jid <JID>`` is a one-shot ingest-only path (no archival / day_close).

DB access is process-safe: pool workers use close_old_connections() at task
start and connections.close_all() at task end. Writes are serialized with a
shared lock.

Attributes:
  ARCHIVE_RESTORE_SOFT_REQUEUE_BACKOFF_S: Attribute.
  DEBUG: Attribute.
  DEFER_SYNC_PREWARM_INVALIDATION_REASONS: Attribute.
  EMPTY_QUEUE_DAY_CLOSE_POLL_SECONDS: Attribute.
  EMPTY_QUEUE_RESCAN_SLEEP_SECONDS: Attribute.
  FINALIZE_POLL_TIMEOUT_SECONDS: Attribute.
  INGEST_PER_FILE_TIMEOUT_LOG_MIN_S: Attribute.
  INGEST_STALL_WATCHDOG_IDLE_S: Attribute.
  LOCK_WAIT_LOG_THRESHOLD_SECONDS: Attribute.
  PENDING_RECONCILE_UNPROCESSED_HARD_CEILING_S: Attribute.
  PENDING_RECONCILE_UNPROCESSED_TTL_S: Attribute.
  SYNC_TIMEDB_CHECKPOINT_BASENAME: Attribute.
  SYNC_TIMEDB_CHECKPOINT_FLUSH_EVERY_FILES: Attribute.
  SYNC_TIMEDB_DEAD_LETTER_BASENAME: Attribute.
  SYNC_TIMEDB_PROCESS_TITLE: Attribute.
  _ARCHIVE_SKIP_FROM_OUTCOME: Attribute.
  _DB_COMPLETE_REASON_TO_SKIP: Attribute.
  _HOST_ITIMES_CACHE: Attribute.
  _HOST_ITIMES_CACHE_MAX_ENTRIES: Attribute.
  _HOST_ITIMES_CACHE_REFRESH_SECONDS: Attribute.
  _HOST_ITIMES_SET_OVERFLOW: Attribute.
  _HOST_SECOND_PRESENT_CACHE: Attribute.
  _HOST_SECOND_PRESENT_CACHE_MAX_ENTRIES: Attribute.
  _HOST_SECOND_PRESENT_CACHE_TTL_S: Attribute.
  _PROC_DATA_UPDATE_FIELDS: Attribute.
  _SUPERVISOR_CHILD_REAP_INTERVAL_S: Attribute.
  _SYNC_STATE_TRANSITIONS: Attribute.
  _SYNC_TIMEDB_INGEST_INLINE_ENV: Attribute.
  _TREE_RSS_DEFER_SLEEP_SECONDS: Attribute.
  _ingest_db_shard_lock_s: Attribute.
  _ingest_postgres_s: Attribute.
  _last_supervisor_child_reap_mono: Attribute.
  _sealed_archive_ingest_progress: Attribute.
  archive_thread_count: Attribute.
  chunk_size: Attribute.
  days_to_process: Attribute.
  local_timezone: Attribute.
  processed_files_max_size: Attribute.
  rescan_every_chunks: Attribute.
  should_archive: Attribute.
  tgz_archive_dir: Attribute.
  thread_count: Attribute.
"""
from __future__ import annotations

import contextvars
import ctypes
import gc
import itertools
import json
import hashlib
import multiprocessing
import os
from contextlib import contextmanager
from typing import Any, Iterator

from hpcperfstats.dbload.lib.blas_thread_env import configure_blas_thread_env

configure_blas_thread_env()

import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import types
import warnings
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import date as date_cls
from datetime import datetime, timedelta, timezone
from enum import Enum

from hpcperfstats.dbload.lib.django_bootstrap import ensure_django
from hpcperfstats.dbload.lib.process_title import (
  apply_pool_worker_process_title,
  set_daemon_process_title,
)

ensure_django()

SYNC_TIMEDB_PROCESS_TITLE = "sync_timedb.py"

from django.db import (
  DEFAULT_DB_ALIAS,
  IntegrityError,
  InterfaceError,
  close_old_connections,
  connections,
)
from django.db.utils import DatabaseError, OperationalError

import hpcperfstats.dbload.lib.conf_parser as cfg
from hpcperfstats.dbload.lib.archive_compress import (
  compressed_sibling_paths,
  daily_compressed_path_for_date,
  daily_tar_path_from_compressed,
  detect_compressed_format,
)
from hpcperfstats.dbload.lib.date_utils import (
  log_date_range,
  parse_start_end_dates,
)
from hpcperfstats.dbload.lib.db_unavailable import (
  DatabaseUnavailableExit,
  is_database_unavailable_error,
  log_and_raise_database_unavailable,
)
from hpcperfstats.dbload.lib.file_locking import file_write_lock
from hpcperfstats.dbload.lib.io_helpers import host_data_instance_from_stats_row
from hpcperfstats.dbload.lib.multiprocessing_pool_health import (
  MultiprocessingWorkerExitError,
  alive_pool_worker_count,
  close_pool_bounded,
  create_sync_timedb_spawn_pool,
  hard_exit_pool_worker_error,
  maintain_ingest_pool_after_supervisor_retire,
  pool_workers_all_idle,
  reap_pool_worker_pids,
  reap_zombie_children_of_self,
  retire_pool_worker_pid,
  sync_timedb_spawn_pool_recycle_kwargs,
  terminate_pool_bounded,
  warn_unreaped_zombie_children,
)
from hpcperfstats.dbload.lib.print_utils import (
  ingest_logging,
  log_print,
)
from hpcperfstats.dbload.lib.shutdown_utils import (
  send_sigchld_to_parent,
  shutdown_requested,
)
from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
  INGEST_PARSE_FAILED_QUARANTINE_REASON,
  _derive_stats_path_date,
  build_tar_append_member_map,
  calendar_date_from_daily_tar_path,
  cap_pending_stats_file_list,
  checkpoint_entries_snapshot,
  clear_daily_archive_members_cache,
  consume_archive_members_populate_source,
  ensure_daily_tar_restored_for_append,
  filter_files_to_add_to_archive,
  get_existing_archive_members_for_daily_archive,
  invalidate_after_daily_tar_mutation,
  iter_daily_tar_paths,
  load_checkpoint_path_set,
  merge_daily_archive_members_l1_cache,
  normalize_daily_compressed_path,
  prepare_paths_for_giant_member_append,
  quarantine_ingest_failed_raw_path,
  raw_stats_path_tar_append_decision,
  repair_truncated_daily_tar_in_place,
  replace_corrupt_tar_from_compressed_backup,
  stats_file_is_active_segment,
  verify_tar_archive_readable,
)
from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
  ArchiveMembersPopulateStalledError,
  ArchiveMembersRedisConnectionError,
  ArchiveMembersRedisUnavailableError,
  IngestArchiveLookupBudgetExceededError,
  _raise_if_ingest_deadline_exceeded,
  archive_append_inflight_for_day,
  archive_members_populate_shows_progress_for_day,
  archive_members_redis_enabled,
  build_archive_members_redis_keys,
  describe_archive_members_populate_redis_for_day,
  get_ingest_task_deadline_monotonic,
  get_ingest_task_effective_timeout_s,
  is_populate_pool_unavailable_error,
  is_transient_fnctl_populate_unavailable,
  maybe_clear_orphan_incomplete_archive_members_redis,
  redis_members_cache_is_fully_warm,
  reset_ingest_task_deadline_monotonic,
  reset_ingest_task_effective_timeout_s,
  set_ingest_task_deadline_monotonic,
  set_ingest_task_effective_timeout_s,
)
from hpcperfstats.dbload.lib.sync_timedb_ingest_timeout import (
  calendar_day_from_sealed_archive_path,
  resolve_ingest_per_file_timeout_s,
)
from hpcperfstats.dbload.lib.sync_timedb_ingest_timeout import (
  max_ingest_per_file_timeout_for_paths as _max_ingest_per_file_timeout_for_paths,
)
from hpcperfstats.dbload.lib.sync_timedb_ingest_timeout import (
  stall_abort_polls_for_paths as _stall_abort_polls_for_batch,
)
from hpcperfstats.dbload.lib.sync_timedb_ingest_worker_diagnostics import (
  count_worker_registry_entries,
  format_worker_stages_snapshot,
  prune_stale_worker_stages,
  record_worker_stage,
  update_worker_substage,
  worker_registry_shows_member_match_wait,
  worker_registry_shows_recent_progress,
)
from hpcperfstats.dbload.lib.sync_timedb_queue_orchestrator import (
  run_sync_timedb_queue_orchestrator,
)
from hpcperfstats.dbload.lib.sync_timedb_worker_memory import (
  classify_supervisor_reap_kind,
  increment_worker_tasks_on_worker,
  measure_worker_rss_after_release,
  resolve_worker_pid_from_meta_or_registry,
  should_defer_supervisor_retire,
  should_supervisor_retire_worker,
)


# No-op stubs kept for monkeypatch stability in archive helper unit tests.
def seal_dirty_daily_archives(*args: Any, **kwargs: Any) -> None:
  """
  Seal the dirty daily archives.
  
  Args:
    *args (Any): Extra positional arguments; unused unless the callee
    documents a specific leftover protocol.
    **kwargs (Any): Extra keyword arguments forwarded to the wrapped API; keys
    and value types match that callee's signature.
  
  Returns:
    None
  
  Raises:
    RuntimeError: Raised when ``seal_dirty_daily_archives`` hits a
    ``RuntimeError`` failure path.
  
  Examples:
    >>> seal_dirty_daily_archives()  # doctest: +SKIP
  """
  raise RuntimeError("seal_dirty_daily_archives is janitor-only; supervisor must not call this")


def remove_verified_archived_raw_files(*args: Any, **kwargs: Any) -> None:
  """
  Remove the verified archived raw files.
  
  Args:
    *args (Any): Extra positional arguments; unused unless the callee
    documents a specific leftover protocol.
    **kwargs (Any): Extra keyword arguments forwarded to the wrapped API; keys
    and value types match that callee's signature.
  
  Returns:
    None
  
  Raises:
    RuntimeError: Raised when ``remove_verified_archived_raw_files`` hits a
    ``RuntimeError`` failure path.
  
  Examples:
    >>> remove_verified_archived_raw_files()  # doctest: +SKIP
  """
  raise RuntimeError(
      "remove_verified_archived_raw_files is janitor-only; supervisor must not call this")


def remove_verified_uncompressed_daily_tars(*args: Any, **kwargs: Any) -> None:
  """
  Remove the verified uncompressed daily tars.
  
  Args:
    *args (Any): Extra positional arguments; unused unless the callee
    documents a specific leftover protocol.
    **kwargs (Any): Extra keyword arguments forwarded to the wrapped API; keys
    and value types match that callee's signature.
  
  Returns:
    None
  
  Raises:
    RuntimeError: Raised when ``remove_verified_uncompressed_daily_tars`` hits
    a ``RuntimeError`` failure path.
  
  Examples:
    >>> remove_verified_uncompressed_daily_tars()  # doctest: +SKIP
  """
  raise RuntimeError(
      "remove_verified_uncompressed_daily_tars is janitor-only; supervisor must not call this")
from hpcperfstats.dbload.lib import sync_timedb_host_itimes
from hpcperfstats.dbload.lib.sync_timedb_ingest_readiness import (
  filter_paths_head_ingested,
  head_timestamp_present_in_db,
  reset_sync_ingest_readiness_caches,
)
from hpcperfstats.dbload.lib.sync_timedb_parsing import (
  EVENTMAPS_BY_TYPE,
  HOST_PROC_KEYS,
  HOST_PROC_PEAK_KEYS,
  DeltaCarryState,
  apply_proc_peak_attrs_from_earlier,
  build_stats_dataframes,
  compute_deltas_and_arc,
  compute_deltas_and_arc_chunk,
  exclude_types,
  find_processing_start_index,
  find_processing_start_index_streaming,
  load_stats_file_lines,
  parse_first_timestamp_line,
  parse_first_timestamp_line_streaming,
  parse_last_timestamp_line,
  parse_last_timestamp_line_streaming,
  parse_stats_file_path,
  parse_stats_file_streaming,
  parse_stats_file_streaming_incremental,
  parse_stats_lines,
  stats_file_size_bytes,
  tail_window_timestamps_all_present_streaming,
)
from hpcperfstats.dbload.lib.sync_timedb_persistence import (
  ensure_persistence_contract,
  load_persistence_document,
  save_persistence_document,
)
from hpcperfstats.site.lib.machine.models import host_data, proc_data

# archive toggle
should_archive = True

# DEBUG message toggle
DEBUG = cfg.get_debug()

local_timezone = cfg.get_local_timezone()

# Thread count for database loading and archival (optional ini caps; see conf_parser).
thread_count = cfg.get_sync_ingest_pool_processes()
# zstd ``-T`` for archive decompress/seal (``[PIPELINE]`` / ``archive_zstd_threads``; 0 → ``-T0``).

archive_thread_count = cfg.get_sync_archive_pool_processes()

# How many days to process if run without any arguments
days_to_process = 5

# How many files to process and archive at once (alias of sync_ingest_queue_max_size).
chunk_size = cfg.get_sync_ingest_chunk_size()
# Rescan stats directory after this many processed chunks
rescan_every_chunks = 1
# Bound processed-file tracking to avoid unbounded set growth in long runs.
processed_files_max_size = 200000
SYNC_TIMEDB_CHECKPOINT_BASENAME = ".sync_timedb_state.json"
SYNC_TIMEDB_CHECKPOINT_FLUSH_EVERY_FILES = cfg.get_sync_checkpoint_flush_batch_size()

# When no pending files remain after final sealing, sleep this long (seconds)
# before exiting sync_timedb. Interruptible via shutdown_requested / SIGTERM path.
EMPTY_QUEUE_RESCAN_SLEEP_SECONDS = 30
# Poll while day-close work remains (avoid 30s exit + janitor teardown).
# 5 minutes: day-close is slow; 1s polls flooded logs without speeding progress.
EMPTY_QUEUE_DAY_CLOSE_POLL_SECONDS = 300.0

# Emit DB lock-wait logs only for sustained contention.
LOCK_WAIT_LOG_THRESHOLD_SECONDS = 30.0
FINALIZE_POLL_TIMEOUT_SECONDS = 0.05

INGEST_PER_FILE_TIMEOUT_LOG_MIN_S = 7200.0


class IngestPerFileTimeoutError(TimeoutError):
  """
  Raised when one ingest pool task exceeds its resolved per-file budget.

  Attributes:
    elapsed_s: Elapsed seconds at timeout.
    path: Stats file path.
    size_bytes: On-disk size captured at raise (0 if missing).
    stage: Worker stage token.
  """

  def __init__(
    self,
    path: str,
    stage: Any,
    elapsed_s: Any,
    size_bytes: Any = None,
  ) -> None:
    """
    Initialize a new instance.

    Args:
      path (str): String for path.
      stage (Any): Mode or kind token selecting a code path.
      elapsed_s (Any): Elapsed s passed to this helper.
      size_bytes (Any): Optional pre-stat size; otherwise ``stats_file_size_bytes``.

    Returns:
      None

    Examples:
      >>> IngestPerFileTimeoutError("x", None, None)  # doctest: +SKIP
    """
    self.path = str(path)
    self.stage = str(stage)
    self.elapsed_s = float(elapsed_s)
    if size_bytes is None:
      try:
        self.size_bytes = int(stats_file_size_bytes(self.path))
      except Exception:
        self.size_bytes = 0
    else:
      try:
        self.size_bytes = int(size_bytes)
      except (TypeError, ValueError):
        self.size_bytes = 0
    rate = (
        float(self.size_bytes) / float(self.elapsed_s)
        if float(self.elapsed_s) > 0.0
        else 0.0
    )
    super().__init__(
        "ingest per-file timeout path=%s size_bytes=%s elapsed_s=%.3f "
        "bytes_per_s=%.0f stage=%s"
        % (self.path, self.size_bytes, self.elapsed_s, rate, self.stage)
    )


class _IngestPoolInFlightTracker:
  """
  Tracks paths dispatched to an ingest pool but not yet returned via imap.
  
  Attributes:
    _batch_seen: Attribute.
    _pending: Attribute.
  """

  def __init__(self, paths: Any) -> None:
    """
    Initialize a new instance.
    
    Args:
      paths (Any): Iterable of filesystem paths as strings.
    
    Returns:
      None
    
    Examples:
      >>> _IngestPoolInFlightTracker(None)  # doctest: +SKIP
    """
    self._pending = {
        os.path.normpath(p)
        for p in (paths or ())
        if p
    }
    self._batch_seen = set(self._pending)

  def complete(self, path: str) -> None:
    """
    Mark this unit of work complete.
    
    Args:
      path (str): String for path.
    
    Returns:
      None
    
    Examples:
      >>> _IngestPoolInFlightTracker().complete("x")  # doctest: +SKIP
    """
    norm = os.path.normpath(path) if path else None
    if norm:
      self._pending.discard(norm)
      self._batch_seen.add(norm)

  def note_dispatched(self, path: str) -> None:
    """
    Note dispatched.
    
    Args:
      path (str): String for path.
    
    Returns:
      None
    
    Examples:
      >>> _IngestPoolInFlightTracker().note_dispatched("x")  # doctest: +SKIP
    """
    norm = os.path.normpath(path) if path else None
    if norm:
      self._pending.add(norm)
      self._batch_seen.add(norm)

  def batch_seen_paths(self) -> Any:
    """
    Batch seen paths.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _IngestPoolInFlightTracker().batch_seen_paths()  # doctest: +SKIP
    """
    return set(self._batch_seen)

  def all_in_flight_paths(self) -> Any:
    """
    All in flight paths.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _IngestPoolInFlightTracker().all_in_flight_paths()  # doctest: +SKIP
    """
    return set(self._pending)

  def sample_in_flight(self, max_n: int = 10) -> Any:
    """
    Sample in flight.
    
    Args:
      max_n (int): Integer value for max n.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _IngestPoolInFlightTracker().sample_in_flight(0)  # doctest: +SKIP
    """
    return sorted(self._pending)[: max(0, int(max_n))]

  def in_flight_count(self) -> Any:
    """
    In flight count.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _IngestPoolInFlightTracker().in_flight_count()  # doctest: +SKIP
    """
    return len(self._pending)


def _log_long_ingest_timeout_budget_if_needed(
  stats_file: str,
  timeout_s: Any,
) -> None:
  """
  Record long per-file budgets on the worker registry only (no WARN log).

  Size and ``timeout_s`` belong on the end-of-file ``ingest file`` outcome
  line (SOP). Do not emit a pre-work budget warning line.

  Args:
    stats_file (str): Stats file path for size lookup.
    timeout_s (Any): Resolved per-file timeout budget (seconds).

  Returns:
    None

  Examples:
    >>> _log_long_ingest_timeout_budget_if_needed("x", 8000.0)  # doctest: +SKIP
  """
  if float(timeout_s) < INGEST_PER_FILE_TIMEOUT_LOG_MIN_S:
    return
  size_bytes = stats_file_size_bytes(stats_file)
  update_worker_substage(
      "long_timeout_budget",
      timeout_s="%.1f" % float(timeout_s),
      size_bytes=str(size_bytes),
  )


def _raise_if_ingest_per_file_deadline_exceeded(
  stats_file: str,
  stage: Any,
) -> None:
  """
  Monotonic deadline check for DB phases SIGALRM cannot interrupt.
  
  Args:
    stats_file (str): String for stats file.
    stage (Any): Mode or kind token selecting a code path.
  
  Returns:
    None
  
  Raises:
    IngestPerFileTimeoutError: Raised when
    ``_raise_if_ingest_per_file_deadline_exceeded`` hits a
    ``IngestPerFileTimeoutError`` failure path.
  
  Examples:
    >>> _raise_if_ingest_per_file_deadline_exceeded("x", None)  # doctest: +SKIP
  """
  deadline = get_ingest_task_deadline_monotonic()
  if deadline is None:
    return
  if time.monotonic() >= float(deadline):
    effective = get_ingest_task_effective_timeout_s()
    if effective is not None and float(effective) > 0.0:
      timeout_s = float(effective)
    else:
      timeout_s = float(cfg.get_sync_ingest_per_file_timeout_s())
    elapsed_s = (
        timeout_s
        if timeout_s > 0.0
        else max(0.0, time.monotonic() - float(deadline))
    )
    raise IngestPerFileTimeoutError(str(stats_file), stage, elapsed_s)


def _imap_ingest_result_path(result: Any) -> Any:
  """
  Internal helper to handle imap ingest result path.
  
  Args:
    result (Any): Result passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _imap_ingest_result_path(None)  # doctest: +SKIP
  """
  if isinstance(result, (tuple, list)) and result:
    return result[0]
  return None


def _run_ingest_timed(
  stats_file: str,
  stage: Any,
  fn: Any,
  *,
  enable_sigalrm: bool = True,
) -> Any:
  """
  Run ingest worker body with optional Unix wall-clock cap.
  
  Args:
    stats_file (str): String for stats file.
    stage (Any): Mode or kind token selecting a code path.
    fn (Any): Callable invoked by this helper.
    enable_sigalrm (bool): Whether to enable enable sigalrm.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _run_ingest_timed("x", None, None, True)  # doctest: +SKIP
  """
  timeout_s = resolve_ingest_per_file_timeout_s(stats_file)
  deadline_token = None
  effective_token = None
  if timeout_s > 0.0:
    effective_token = set_ingest_task_effective_timeout_s(timeout_s)
    deadline_token = set_ingest_task_deadline_monotonic(
        time.monotonic() + timeout_s,
    )
  record_worker_stage(stats_file, stage, timeout_s=timeout_s)
  _log_long_ingest_timeout_budget_if_needed(stats_file, timeout_s)
  try:
    if timeout_s <= 0.0 or not enable_sigalrm or not hasattr(signal, "SIGALRM"):
      return fn()

    path_label = str(stats_file)
    t0 = time.monotonic()

    def _handler(signum: Any, frame: Any) -> None:
      """
      Internal helper to handle handler.
      
      Args:
        signum (Any): Signum passed to this helper.
        frame (Any): Frame passed to this helper.
      
      Returns:
        None
      
      Raises:
        IngestPerFileTimeoutError: Raised when ``_handler`` hits a
        ``IngestPerFileTimeoutError`` failure path.
      
      Examples:
        >>> _handler(None, None)  # doctest: +SKIP
      """
      del signum, frame
      raise IngestPerFileTimeoutError(
          path_label,
          stage,
          time.monotonic() - t0,
      )

    previous = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, _handler)
    try:
      if hasattr(signal, "setitimer"):
        signal.setitimer(signal.ITIMER_REAL, timeout_s)
      else:
        signal.alarm(max(1, int(timeout_s)))
      return fn()
    finally:
      if hasattr(signal, "setitimer"):
        signal.setitimer(signal.ITIMER_REAL, 0)
      else:
        signal.alarm(0)
      signal.signal(signal.SIGALRM, previous)
  finally:
    if effective_token is not None:
      reset_ingest_task_effective_timeout_s(effective_token)
    if deadline_token is not None:
      reset_ingest_task_deadline_monotonic(deadline_token)


def _merge_worker_memory_meta(result: Any, mem_meta: Any) -> Any:
  """
  Internal helper to merge the worker memory meta.
  
  Args:
    result (Any): Result passed to this helper.
    mem_meta (Any): Mem meta passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _merge_worker_memory_meta(None, None)  # doctest: +SKIP
  """
  if not mem_meta:
    return result
  stats_file, need_archival, ingest_ok, elapsed_s, meta = (
      _unpack_ingest_worker_result(result)
  )
  merged = dict(meta)
  merged.update(mem_meta)
  return _pack_ingest_worker_result(
      stats_file, need_archival, ingest_ok, elapsed_s, merged,
  )


def _handle_ingest_worker_memory_after_imap(
  *,
  pool: Any,
  registry: Any,
  result: Any,
  accumulator: Any,
  pool_health_context: Any | None = None,
  recreate_ingest_pool_fn: Any | None = None,
  on_pool_replaced: Any | None = None,
  pending_inflight: Any | None = None,
  max_inflight: Any | None = None,
) -> Any:
  """
  Internal helper to handle the ingest worker memory after imap.
  
  Args:
    pool (Any): Live handle (pool, client, or connection).
    registry (Any): Registry passed to this helper.
    result (Any): Result passed to this helper.
    accumulator (Any): Accumulator passed to this helper.
    pool_health_context (Any | None): One of ``Any``, ``None``.
    recreate_ingest_pool_fn (Any | None): One of ``Any``, ``None``.
    on_pool_replaced (Any | None): One of ``Any``, ``None``.
    pending_inflight (Any | None): One of ``Any``, ``None``.
    max_inflight (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _handle_ingest_worker_memory_after_imap(0)  # doctest: +SKIP
  """
  stats_fname, _need_archival, ingest_ok, _elapsed_s, outcome_meta = (
      _unpack_ingest_worker_result(result)
  )
  meta = dict(outcome_meta or {})
  outcome = str(meta.get("outcome") or "")
  if not outcome:
    outcome = "ingested" if meta.get("stats_rows") else "db_skip"
  reap_kind = classify_supervisor_reap_kind(
      ingest_ok=ingest_ok,
      outcome=outcome,
      meta=meta,
      path=stats_fname,
  )
  if should_supervisor_retire_worker(reap_kind):
    if str(meta.get("reconcile_skip") or "") == "yes":
      return reap_kind
    if should_defer_supervisor_retire(
        reap_kind,
        accumulator=accumulator,
        pending_inflight=pending_inflight,
        max_inflight=max_inflight,
    ):
      log_print(
          "INFO: sync_timedb worker_memory: retire deferred reap_kind=%s "
          "pending_inflight=%s max_inflight=%s path=%s"
          % (
              reap_kind,
              pending_inflight,
              max_inflight,
              stats_fname,
          ),
          flush=True,
      )
    else:
      worker_pid = resolve_worker_pid_from_meta_or_registry(
          meta, registry, stats_fname,
      )
      if worker_pid is not None:
        retire_pool_worker_pid(
            pool,
            worker_pid,
            context="ingest_%s" % reap_kind,
        )
        maintained_pool = maintain_ingest_pool_after_supervisor_retire(
            pool,
            pool_health_context=pool_health_context,
            recreate_pool_fn=recreate_ingest_pool_fn,
        )
        if maintained_pool is not pool and callable(on_pool_replaced):
          on_pool_replaced(maintained_pool)
      else:
        log_print(
            "WARN: sync_timedb worker_memory: retire skipped missing worker_pid "
            "path=%s reap_kind=%s likely_cause=meta_or_registry_gap"
            % (stats_fname, reap_kind),
            flush=True,
        )
  if accumulator is not None:
    accumulator.record_completion(reap_kind, meta)
  return reap_kind


def _log_ingest_per_file_timeout(exc: Any) -> None:
  """
  Internal helper to log the ingest per file timeout.

  Args:
    exc (Any): Exception instance being classified or logged.

  Returns:
    None

  Examples:
    >>> _log_ingest_per_file_timeout(None)  # doctest: +SKIP
  """
  size_bytes = int(getattr(exc, "size_bytes", 0) or 0)
  elapsed_s = float(getattr(exc, "elapsed_s", 0.0) or 0.0)
  rate = (float(size_bytes) / elapsed_s) if elapsed_s > 0.0 else 0.0
  log_print(
      "ERROR: ingest per-file timeout path=%s size_bytes=%s elapsed=%.1fs "
      "bytes_per_s=%.0f stage=%s"
      % (exc.path, size_bytes, elapsed_s, rate, exc.stage),
      flush=True,
  )


def _log_ingest_archive_lookup_budget_exceeded(exc: Any) -> None:
  """
  Internal helper to log the ingest archive lookup budget exceeded.
  
  Args:
    exc (Any): Exception instance being classified or logged.
  
  Returns:
    None
  
  Examples:
    >>> _log_ingest_archive_lookup_budget_exceeded(None)  # doctest: +SKIP
  """
  log_print(
      "ERROR: ingest archive lookup budget exceeded: %s"
      % exc,
      flush=True,
  )


def _unique_daily_compressed_archives_for_paths(
  paths: Any,
  tgz_archive_dir: str,
) -> Any:
  """
  Map canonical daily ``.tar.zst`` paths to ISO day tokens for chunk paths.
  
  Args:
    paths (Any): Iterable of filesystem paths as strings.
    tgz_archive_dir (str): String for tgz archive dir.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _unique_daily_compressed_archives_for_paths(None, "x")  # doctest: +SKIP
  """
  unique = {}
  for path in paths or ():
    file_date = _derive_stats_path_date(path)
    if file_date is None:
      continue
    compressed = daily_compressed_path_for_date(tgz_archive_dir, file_date)
    unique[compressed] = file_date.isoformat()
  return unique


def _paths_all_db_complete_for_prewarm_skip(paths: Any) -> Any:
  """
  True when every chunk path would db-complete skip (no tar restore/prewarm).
  
  Args:
    paths (Any): Iterable of filesystem paths as strings.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _paths_all_db_complete_for_prewarm_skip(None)  # doctest: +SKIP
  """
  if not paths:
    return False
  for stats_file in paths:
    if not stats_file or not os.path.isfile(stats_file):
      return False
    t, _jid, host = parse_first_timestamp_line_streaming(stats_file)
    if t is None or not host:
      return False
    host = str(host).strip()
    timestamp_utc = datetime.fromtimestamp(int(float(t)), tz=timezone.utc)
    if not head_timestamp_present_in_db(host, timestamp_utc):
      return False
    if _try_db_complete_head_tail_fast_path(stats_file, host, timestamp_utc) is None:
      return False
  return True




def _signal_ingest_hot_for_populate(
  day_token: Any,
  tar_path: str,
  *,
  reason: Any,
) -> None:
  """
  Early hot-path signal before populate fnctl wait (non-blocking).
  
  Args:
    day_token (Any): Day token passed to this helper.
    tar_path (str): String for tar path.
    reason (Any): Reason passed to this helper.
  
  Returns:
    None
  
  Examples:
    >>> _signal_ingest_hot_for_populate(None, "x", None)  # doctest: +SKIP
  """
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      set_ingest_tar_hot,
  )

  del tar_path  # retained for call-site compatibility
  if day_token:
    set_ingest_tar_hot(day_token, reason=reason)


def _prewarm_archive_members_redis_for_days(
  day_items: Any,
  *,
  gated_tar_restore_day_tokens: Any | None = None,
) -> Any:
  """
  Single-flight populate on supervisor before imap when Redis L2 is cold.
  
  Args:
    day_items (Any): Day items passed to this helper.
    gated_tar_restore_day_tokens (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Raises:
    ArchiveMembersRedisUnavailableError: Raised when
    ``_prewarm_archive_members_redis_for_days`` hits a
    ``ArchiveMembersRedisUnavailableError`` failure path.
  
  Examples:
    >>> _prewarm_archive_members_redis_for_days(None, None)  # doctest: +SKIP
  """
  summary_parts = []
  if not archive_members_redis_enabled():
    return "-"
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      _FNCTL_POPULATE_RETRY_DELAYS_S,
      _daily_archive_members_cache_key,
      _resolve_sealed_daily_archive_path,
      daily_archive_populate_source_exists,
  )
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      ArchiveDayIngestSkipError,
      request_archive_members_populate_and_wait,
  )

  gated_restore = set(gated_tar_restore_day_tokens or ())
  for compressed, day_token in day_items or ():
    canonical = normalize_daily_compressed_path(compressed)
    cache_key = _daily_archive_members_cache_key(canonical)
    keys = build_archive_members_redis_keys(cache_key)
    maybe_clear_orphan_incomplete_archive_members_redis(keys)
    if redis_members_cache_is_fully_warm(keys):
      summary_parts.append("%s:redis_warm" % day_token)
      continue
    if not daily_archive_populate_source_exists(canonical):
      summary_parts.append("%s:no_daily_archive" % day_token)
      continue
    sealed_path = _resolve_sealed_daily_archive_path(canonical)
    tar_path = daily_tar_path_from_compressed(canonical)
    if (
        day_token in gated_restore
        and sealed_path is not None
        and not os.path.isfile(tar_path)
    ):
      if ensure_daily_tar_restored_for_append(
          tar_path,
          cfg.get_archive_zstd_threads(),
      ):
        log_print(
            "INFO: populate_prewarm restored tar day=%s path=%s"
            % (day_token, tar_path),
            flush=True,
        )
      else:
        summary_parts.append("%s:restore_failed" % day_token)
        continue
    log_print(
        "Prewarming archive members Redis for day=%s sealed=%s"
        % (day_token, sealed_path or tar_path),
        flush=True,
    )
    _signal_ingest_hot_for_populate(day_token, tar_path, reason="chunk_prewarm")
    prewarm_recovered = False
    last_transient_exc = None
    for attempt, delay in enumerate((0.0,) + _FNCTL_POPULATE_RETRY_DELAYS_S):
      if delay:
        time.sleep(delay)
      try:
        members = request_archive_members_populate_and_wait(
            canonical,
        )
        source = consume_archive_members_populate_source(canonical)
        # Wait may re-resolve to a post-append identity (T1→T2); do not warm-check
        # the frozen entry-time keys (empty-after-prewarm with members_n>0).
        cache_key = _daily_archive_members_cache_key(canonical)
        keys = build_archive_members_redis_keys(cache_key)
        if not redis_members_cache_is_fully_warm(keys):
          client = None
          try:
            from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
                get_archive_day_ingest_skip,
                get_archive_members_redis_client,
            )
            client = get_archive_members_redis_client(required=False)
          except Exception:
            client = None
            get_archive_day_ingest_skip = None  # type: ignore[assignment]
          if (
              get_archive_day_ingest_skip is not None
              and get_archive_day_ingest_skip(keys, client=client) is not None
          ):
            summary_parts.append("%s:day_ingest_skip" % day_token)
            last_transient_exc = None
            break
          complete = (
              client.get(keys.complete_key) == "1" if client is not None else False
          )
          if complete and not (members or {}):
            source = source or "empty_archive"
          elif attempt < len(_FNCTL_POPULATE_RETRY_DELAYS_S) and (
              archive_append_inflight_for_day(day_token) or len(members or {}) > 0
          ):
            prewarm_recovered = True
            last_transient_exc = ArchiveMembersRedisUnavailableError(
                "archive members Redis cold after prewarm for day=%s "
                "canonical=%s source=%s members_n=%d append_inflight=%s"
                % (
                    day_token,
                    canonical,
                    source or "none",
                    len(members or {}),
                    archive_append_inflight_for_day(day_token),
                ),
            )
            if archive_append_inflight_for_day(day_token):
              log_print(
                  "WARNING: archive_append_inflight during archive members "
                  "prewarm day=%s attempt=%d/%d: retrying after identity drift"
                  % (
                      day_token,
                      attempt + 1,
                      len(_FNCTL_POPULATE_RETRY_DELAYS_S) + 1,
                  ),
                  flush=True,
              )
            else:
              log_print(
                  "WARNING: members returned but Redis cold during archive "
                  "members prewarm day=%s attempt=%d/%d members_n=%d "
                  "source=%s: retrying"
                  % (
                      day_token,
                      attempt + 1,
                      len(_FNCTL_POPULATE_RETRY_DELAYS_S) + 1,
                      len(members or {}),
                      source or "none",
                  ),
                  flush=True,
              )
            continue
          else:
            maybe_clear_orphan_incomplete_archive_members_redis(keys)
            raise ArchiveMembersRedisUnavailableError(
                "archive members Redis empty after prewarm for day=%s "
                "canonical=%s source=%s members_n=%d"
                % (
                    day_token,
                    canonical,
                    source or "none",
                    len(members or {}),
                ),
            )
        if not source:
          source = "redis_warm"
        if prewarm_recovered:
          summary_parts.append(
              "%s:populate_recovering:%s" % (day_token, source),
          )
        else:
          summary_parts.append("%s:%s" % (day_token, source))
        last_transient_exc = None
        break
      except ArchiveDayIngestSkipError:
        summary_parts.append("%s:day_ingest_skip" % day_token)
        last_transient_exc = None
        break
      except ArchiveMembersPopulateStalledError as exc:
        maybe_clear_orphan_incomplete_archive_members_redis(keys)
        _exit_on_archive_members_redis_unavailable(exc)
      except ArchiveMembersRedisUnavailableError as exc:
        maybe_clear_orphan_incomplete_archive_members_redis(keys)
        if is_transient_fnctl_populate_unavailable(exc) or is_populate_pool_unavailable_error(
            exc,
        ):
          prewarm_recovered = True
          last_transient_exc = exc
          if is_populate_pool_unavailable_error(exc):
            from hpcperfstats.dbload.lib.sync_timedb_populate_pool import (
                get_populate_pool_controller,
            )
            controller = get_populate_pool_controller()
            if controller is not None:
              try:
                controller.reap_and_restart()
              except Exception:
                pass
          if attempt < len(_FNCTL_POPULATE_RETRY_DELAYS_S):
            label = (
                "populate-pool unavailable"
                if is_populate_pool_unavailable_error(exc)
                else "transient fnctl"
            )
            log_print(
                "WARNING: %s during archive members prewarm "
                "day=%s attempt=%d/%d: %s"
                % (
                    label,
                    day_token,
                    attempt + 1,
                    len(_FNCTL_POPULATE_RETRY_DELAYS_S) + 1,
                    exc,
                ),
                flush=True,
            )
            continue
        _exit_on_archive_members_redis_unavailable(exc)
    else:
      if last_transient_exc is not None:
        _exit_on_archive_members_redis_unavailable(last_transient_exc)
  return ",".join(summary_parts) if summary_parts else "-"


def _prewarm_archive_members_redis_for_day_token(day_token: Any) -> None:
  """
  Internal helper to handle prewarm archive members redis for day token.
  
  Args:
    day_token (Any): Day token passed to this helper.
  
  Returns:
    None
  
  Examples:
    >>> _prewarm_archive_members_redis_for_day_token(None)  # doctest: +SKIP
  """
  if not day_token:
    return
  try:
    day_date = date_cls.fromisoformat(day_token)
  except ValueError:
    return
  compressed = daily_compressed_path_for_date(tgz_archive_dir, day_date)
  _prewarm_archive_members_redis_for_days([(compressed, day_token)])


def _reprewarm_archive_members_after_seal_phase(
  day_token: Any,
  *,
  day_raw_removal: Any,
  prewarm_fn: Any = _prewarm_archive_members_redis_for_day_token,
  log_fn: Any = log_print,
) -> None:
  """
  Re-prewarm Redis after seal invalidation, unless verify will populate.
  
  Args:
    day_token (Any): Day token passed to this helper.
    day_raw_removal (Any): Day raw removal passed to this helper.
    prewarm_fn (Any): Callable invoked by this helper.
    log_fn (Any): Callable invoked by this helper.
  
  Returns:
    None
  
  Examples:
    >>> _reprewarm_archive_members_after_seal_phase(None, None, None, None)
  """
  if day_raw_removal is not None and day_raw_removal.enabled:
    log_fn(
        "INFO: re-prewarm skipped day=%s reason=verify_will_populate"
        % day_token,
        flush=True,
    )
    return
  log_fn(
      "INFO: re-prewarm archive members Redis after seal day=%s"
      % day_token,
      flush=True,
  )
  prewarm_fn(day_token)


def _prewarm_archive_members_redis_for_chunk(
  paths: Any,
  *,
  oldest_tar: Any | None = None,
  gated_tar_restore: bool = False,
  skip_prewarm: bool = False,
) -> Any:
  """
  Single-flight populate on supervisor before imap when Redis L2 is cold.
  
  Args:
    paths (Any): Iterable of filesystem paths as strings.
    oldest_tar (Any | None): One of ``Any``, ``None``.
    gated_tar_restore (bool): Boolean flag for gated tar restore.
    skip_prewarm (bool): Whether to enable skip prewarm.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _prewarm_archive_members_redis_for_chunk(None, None, True, True)
  """
  with ingest_logging():
    return _prewarm_archive_members_redis_for_chunk_inner(
        paths,
        oldest_tar=oldest_tar,
        gated_tar_restore=gated_tar_restore,
        skip_prewarm=skip_prewarm,
    )


def _prewarm_archive_members_redis_for_chunk_inner(
  paths: Any,
  *,
  oldest_tar: Any | None = None,
  gated_tar_restore: bool = False,
  skip_prewarm: bool = False,
) -> Any:
  """
  Internal helper to handle prewarm archive members redis for chunk inner.
  
  Args:
    paths (Any): Iterable of filesystem paths as strings.
    oldest_tar (Any | None): One of ``Any``, ``None``.
    gated_tar_restore (bool): Boolean flag for gated tar restore.
    skip_prewarm (bool): Whether to enable skip prewarm.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _prewarm_archive_members_redis_for_chunk_inner(None, None, True, True)
  """
  if skip_prewarm:
    log_print(
        "INFO: chunk prewarm skipped reason=all_db_complete paths=%d"
        % len(paths or ()),
        flush=True,
    )
    return "skipped:all_db_complete"
  day_map = _unique_daily_compressed_archives_for_paths(paths, tgz_archive_dir)
  gated_tokens = set()
  if oldest_tar:
    oldest_day = calendar_date_from_daily_tar_path(oldest_tar)
    if oldest_day is not None:
      oldest_compressed = daily_compressed_path_for_date(
          tgz_archive_dir,
          oldest_day,
      )
      oldest_token = oldest_day.isoformat()
      day_map[oldest_compressed] = oldest_token
      if gated_tar_restore:
        gated_tokens.add(oldest_token)
  day_tokens = sorted(set(day_map.values()))
  log_print(
      "chunk prewarm begin paths=%d days=%s oldest_tar=%s "
      "gated_tar_restore=%s"
      % (
          len(paths or ()),
          day_tokens,
          os.path.basename(str(oldest_tar or "")),
          bool(gated_tar_restore and gated_tokens),
      ),
      flush=True,
  )
  prewarm_t0 = time.time()
  summary = _prewarm_archive_members_redis_for_days(
      list(day_map.items()),
      gated_tar_restore_day_tokens=gated_tokens,
  )
  log_print(
      "chunk prewarm complete elapsed_s=%.3f days=%s"
      % (time.time() - prewarm_t0, summary),
      flush=True,
  )
  return summary


def _prewarm_archive_members_redis_for_sealed_chunk(sealed_paths: Any) -> Any:
  """
  Prewarm Redis member maps for unique calendar days in a sealed archive chunk.
  
  Args:
    sealed_paths (Any): Iterable of filesystem paths as strings.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _prewarm_archive_members_redis_for_sealed_chunk(None)  # doctest: +SKIP
  """
  with ingest_logging():
    return _prewarm_archive_members_redis_for_sealed_chunk_inner(sealed_paths)


def _prewarm_archive_members_redis_for_sealed_chunk_inner(
  sealed_paths: Any,
) -> Any:
  """
  Prewarm Redis member maps for unique calendar days in a sealed archive chunk.
  
  Args:
    sealed_paths (Any): Iterable of filesystem paths as strings.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _prewarm_archive_members_redis_for_sealed_chunk_inner(None)
  """
  day_items = []
  seen = set()
  for sealed_path in sealed_paths or ():
    if not sealed_path:
      continue
    norm = os.path.normpath(str(sealed_path))
    if norm in seen:
      continue
    seen.add(norm)
    day_token = calendar_day_from_sealed_archive_path(sealed_path)
    if not day_token:
      continue
    day_items.append((sealed_path, day_token))
  day_tokens = sorted({token for _, token in day_items})
  log_print(
      "archive chunk prewarm begin sealed_paths=%d days=%s"
      % (len(day_items), day_tokens),
      flush=True,
  )
  prewarm_t0 = time.time()
  summary = _prewarm_archive_members_redis_for_days(day_items)
  log_print(
      "archive chunk prewarm complete elapsed_s=%.3f days=%s"
      % (time.time() - prewarm_t0, summary),
      flush=True,
  )
  return summary


def _calendar_day_hint_from_paths(paths: Any) -> Any:
  """
  Best-effort calendar day from first in-flight stats path filename epoch.
  
  Args:
    paths (Any): Iterable of filesystem paths as strings.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _calendar_day_hint_from_paths(None)  # doctest: +SKIP
  """
  for path in paths or ():
    if not path:
      continue
    base = os.path.basename(str(path))
    if base.isdigit():
      try:
        return datetime.fromtimestamp(
            int(base), tz=timezone.utc,
        ).strftime("%Y-%m-%d")
      except (TypeError, ValueError, OSError, OverflowError):
        pass
  return ""


def _calendar_day_hint_from_sealed_paths(sealed_paths: Any) -> Any:
  """
  Best-effort calendar day from sealed daily archive paths.
  
  Args:
    sealed_paths (Any): Iterable of filesystem paths as strings.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _calendar_day_hint_from_sealed_paths(None)  # doctest: +SKIP
  """
  for path in sealed_paths or ():
    day = calendar_day_from_sealed_archive_path(path)
    if day:
      return day
  return ""


def _distinct_calendar_days_from_sealed_paths(
  sealed_paths: Any,
  max_days: int = 8,
) -> Any:
  """
  Internal helper to handle distinct calendar days from sealed paths.
  
  Args:
    sealed_paths (Any): Iterable of filesystem paths as strings.
    max_days (int): Integer value for max days.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _distinct_calendar_days_from_sealed_paths(None, 0)  # doctest: +SKIP
  """
  days = []
  seen = set()
  for path in sealed_paths or ():
    day = calendar_day_from_sealed_archive_path(path)
    if not day or day in seen:
      continue
    seen.add(day)
    days.append(day)
    if len(days) >= max(1, int(max_days)):
      break
  return days


def _distinct_calendar_days_from_paths(paths: Any, max_days: int = 8) -> Any:
  """
  Internal helper to handle distinct calendar days from paths.
  
  Args:
    paths (Any): Iterable of filesystem paths as strings.
    max_days (int): Integer value for max days.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _distinct_calendar_days_from_paths(None, 0)  # doctest: +SKIP
  """
  days = []
  seen = set()
  for path in paths or ():
    day = _calendar_day_hint_from_paths([path])
    if not day or day in seen:
      continue
    seen.add(day)
    days.append(day)
    if len(days) >= max(1, int(max_days)):
      break
  return days


def _in_flight_file_meta_from_paths(paths: Any, max_n: int = 10) -> Any:
  """
  Internal helper to handle in flight file meta from paths.
  
  Args:
    paths (Any): Iterable of filesystem paths as strings.
    max_n (int): Integer value for max n.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _in_flight_file_meta_from_paths(None, 0)  # doctest: +SKIP
  """
  parts = []
  for path in (paths or [])[: max(0, int(max_n))]:
    if not path:
      continue
    base = os.path.basename(str(path))
    try:
      size_bytes = os.path.getsize(path)
    except OSError:
      size_bytes = -1
    parts.append("%s:%d" % (base, int(size_bytes)))
  return ",".join(parts) if parts else "-"


INGEST_STALL_WATCHDOG_IDLE_S = 1800.0
# Soft TTL: force refresh when incomplete fingerprint is absent/zero.
PENDING_RECONCILE_UNPROCESSED_TTL_S = 120.0
# Hard ceiling: valid incomplete fingerprint may reuse past soft TTL (caps often
# take 200–400s; soft-only expiry caused perpetual full rebuilds).
PENDING_RECONCILE_UNPROCESSED_HARD_CEILING_S = 900.0
_SUPERVISOR_CHILD_REAP_INTERVAL_S = 60.0
_last_supervisor_child_reap_mono = 0.0


class IngestStallDiagnostics:
  """
  Supervisor-thread state included on pool imap stall WARN/ERROR lines.
  
  Attributes:
    chunk_archive_elapsed_s: Attribute.
    chunk_batch_size: Attribute.
    chunk_ingest_elapsed_s: Attribute.
    chunk_prewarm_elapsed_s: Attribute.
    chunk_prewarm_summary: Attribute.
    current_imap_batch_max_timeout_s: Attribute.
    current_imap_batch_size: Attribute.
    current_imap_in_flight: Attribute.
    day_close_manifest: Attribute.
    dynamic_stall_abort_after_polls: Attribute.
    dynamic_stall_wall_s: Attribute.
    imap_batch_cap: Attribute.
    ingest_pipeline: Attribute.
    last_imap_completion_monotonic: Attribute.
    worker_registry: Attribute.
  """

  def __init__(self) -> None:
    """
    Initialize a new instance.
    
    Returns:
      None
    
    Examples:
      >>> IngestStallDiagnostics()  # doctest: +SKIP
    """
    self.last_imap_completion_monotonic = None
    self.ingest_pipeline = "combined"
    self.imap_batch_cap = 0
    self.chunk_batch_size = 0
    self.current_imap_batch_size = 0
    self.current_imap_in_flight = 0
    self.current_imap_batch_max_timeout_s = 0.0
    self.dynamic_stall_abort_after_polls = 0
    self.dynamic_stall_wall_s = 0.0
    self.day_close_manifest = None
    self.worker_registry = None
    self.chunk_prewarm_summary = "-"
    self.chunk_prewarm_elapsed_s = 0.0
    self.chunk_ingest_elapsed_s = 0.0
    self.chunk_archive_elapsed_s = 0.0

  def note_imap_completion(self) -> None:
    """
    Note imap completion.
    
    Returns:
      None
    
    Examples:
      >>> IngestStallDiagnostics().note_imap_completion()  # doctest: +SKIP
    """
    self.last_imap_completion_monotonic = time.monotonic()

  def seconds_since_last_imap_completion(self) -> Any:
    """
    Seconds since last imap completion.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> IngestStallDiagnostics().seconds_since_last_imap_completion()
    """
    last = self.last_imap_completion_monotonic
    if last is None:
      return -1.0
    return max(0.0, time.monotonic() - float(last))

  def format_day_close_pipeline_detail(self) -> Any:
    """
    Format the day close pipeline detail.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> IngestStallDiagnostics().format_day_close_pipeline_detail()
    """
    coord = self.day_close_manifest
    if coord is None:
      return "0 detail=-"
    try:
      active = coord.active_or_submitted_tar_paths()
    except Exception:
      return "0 detail=unavailable"
    details = []
    for tar_path in sorted(active)[:8]:
      snap = coord.entry_progress_snapshot(tar_path) or {}
      age_s = snap.get("last_progress_age_s")
      age_text = "%.0f" % float(age_s) if age_s is not None else ""
      details.append(
          "%s:%s:%s:%s"
          % (
              os.path.basename(str(tar_path)),
              snap.get("status") or "",
              snap.get("last_progress") or "",
              age_text,
          )
      )
    detail = ";".join(details) if details else "-"
    return "%d detail=%s" % (len(active), detail)


def _pool_stall_wall_seconds() -> Any:
  """
  INI ceiling stall wall (maximum across batches).
  
  Returns:
    Any: Open return polymorphism from ``_pool_stall_wall_seconds``: concrete
    type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> _pool_stall_wall_seconds()  # doctest: +SKIP
  """
  poll_s = float(cfg.get_sync_pool_poll_timeout_s())
  abort_n = int(cfg.get_sync_pool_stall_abort_after_timeouts())
  if poll_s <= 0.0:
    return 0.0
  return poll_s * abort_n


def _dynamic_stall_wall_seconds(stall_diagnostics: Any) -> Any:
  """
  Active imap sub-batch stall wall, or INI ceiling when unset.
  
  Args:
    stall_diagnostics (Any): Stall diagnostics passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _dynamic_stall_wall_seconds(None)  # doctest: +SKIP
  """
  if stall_diagnostics is not None:
    dynamic_wall = float(
        getattr(stall_diagnostics, "dynamic_stall_wall_s", 0.0) or 0.0,
    )
    if dynamic_wall > 0.0:
      return dynamic_wall
  return _pool_stall_wall_seconds()


def _ingest_stall_defer_long_budget(
  stall_diagnostics: Any,
  consecutive_timeouts: Any,
) -> Any:
  """
  Defer when worker registry budget exceeds batch precompute (safety net).
  
  Args:
    stall_diagnostics (Any): Stall diagnostics passed to this helper.
    consecutive_timeouts (Any): Consecutive timeouts passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _ingest_stall_defer_long_budget(None, None)  # doctest: +SKIP
  """
  if stall_diagnostics is None:
    return False, ""
  effective = _max_effective_ingest_timeout_from_registry(
      getattr(stall_diagnostics, "worker_registry", None),
  )
  if effective is None:
    return False, ""
  batch_max_s = float(
      getattr(stall_diagnostics, "current_imap_batch_max_timeout_s", 0.0) or 0.0,
  )
  if batch_max_s <= 0.0 or effective <= batch_max_s:
    return False, ""
  poll_s = float(cfg.get_sync_pool_poll_timeout_s())
  estimated_stall_s = int(consecutive_timeouts) * poll_s
  if estimated_stall_s < effective:
    return True, "long_ingest_budget"
  return False, ""


def _sample_looks_like_sealed_archives(sample: Any) -> Any:
  """
  Internal helper to handle sample looks like sealed archives.
  
  Args:
    sample (Any): Sample passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _sample_looks_like_sealed_archives(None)  # doctest: +SKIP
  """
  for path in sample or ():
    base = os.path.basename(str(path))
    if base.endswith(".tar.zst") or base.endswith(".tar.gz"):
      return True
  return False


def _ingest_stall_defer_state(
  day_hint: Any,
  progress_state: Any,
  *,
  stall_diagnostics: Any | None = None,
  consecutive_timeouts: int = 0,
  pool: Any | None = None,
  sample: Any | None = None,
  day_hint_from_sample_fn: Any | None = None,
) -> Any:
  """
  Internal helper to ingest the stall defer state.
  
  Args:
    day_hint (Any): Day hint passed to this helper.
    progress_state (Any): Progress state passed to this helper.
    stall_diagnostics (Any | None): One of ``Any``, ``None``.
    consecutive_timeouts (int): Integer value for consecutive timeouts.
    pool (Any | None): One of ``Any``, ``None``.
    sample (Any | None): One of ``Any``, ``None``.
    day_hint_from_sample_fn (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _ingest_stall_defer_state(None, None, None, 0, None, None, None)
  """
  registry = (
      getattr(stall_diagnostics, "worker_registry", None)
      if stall_diagnostics is not None
      else None
  )
  active_pool = pool
  if active_pool is None and stall_diagnostics is not None:
    active_pool = getattr(stall_diagnostics, "active_pool", None)
  sample_list = list(sample or ())
  # Check redis populate / long-budget defer BEFORE idle-ghost: workers blocked
  # in hrtimer_nanosleep during Redis populate wait look idle to ps/wchan.
  defer_on, defer_reason = _ingest_stall_defer_long_budget(
      stall_diagnostics,
      consecutive_timeouts,
  )
  if defer_on:
    return True, defer_reason
  if worker_registry_shows_member_match_wait(registry, pool=active_pool):
    return True, "member_match_wait"
  if worker_registry_shows_recent_progress(registry, pool=active_pool):
    return True, "worker_progress_active"
  day_hint_resolved = day_hint
  if not day_hint_resolved:
    if callable(day_hint_from_sample_fn) and sample:
      day_hint_resolved = day_hint_from_sample_fn(sample)
    elif sample:
      day_hint_resolved = _calendar_day_hint_from_sealed_paths(sample)
  if day_hint_resolved and archive_members_populate_shows_progress_for_day(
      day_hint_resolved,
      tgz_archive_dir,
      progress_state=progress_state,
  ):
    return True, "redis_populate_active"
  if (
      active_pool is not None
      and sample_list
      and pool_workers_all_idle(active_pool)
      and not worker_registry_shows_recent_progress(registry, pool=active_pool)
  ):
    return False, "idle_pool_ghost_inflight"
  if not day_hint_resolved:
    return False, "no_day_hint"
  if archive_members_redis_enabled():
    try:
      day_date = date_cls.fromisoformat(day_hint_resolved)
      compressed = daily_compressed_path_for_date(tgz_archive_dir, day_date)
      from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
          _daily_archive_members_cache_key,
      )
      cache_key = _daily_archive_members_cache_key(
          normalize_daily_compressed_path(compressed),
      )
      keys = build_archive_members_redis_keys(cache_key)
      if redis_members_cache_is_fully_warm(keys):
        return False, "redis_warm"
    except (ValueError, TypeError):
      pass
  return False, "redis_populate_inactive"


def _format_redis_populate_for_in_flight_days(
  paths: Any,
  max_days: int = 3,
) -> Any:
  """
  Internal helper to format the redis populate for in flight days.
  
  Args:
    paths (Any): Iterable of filesystem paths as strings.
    max_days (int): Integer value for max days.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _format_redis_populate_for_in_flight_days(None, 0)  # doctest: +SKIP
  """
  days = _distinct_calendar_days_from_paths(paths, max_days=max_days)
  if not days:
    return ""
  parts = [
      "%s{%s}"
      % (day, describe_archive_members_populate_redis_for_day(day, tgz_archive_dir))
      for day in days
  ]
  return " redis_by_day=" + " ".join(parts)


def _format_redis_populate_for_sealed_paths(
  sealed_paths: Any,
  max_days: int = 3,
) -> Any:
  """
  Internal helper to format the redis populate for sealed paths.
  
  Args:
    sealed_paths (Any): Iterable of filesystem paths as strings.
    max_days (int): Integer value for max days.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _format_redis_populate_for_sealed_paths(None, 0)  # doctest: +SKIP
  """
  days = _distinct_calendar_days_from_sealed_paths(sealed_paths, max_days=max_days)
  if not days:
    return ""
  parts = [
      "%s{%s}"
      % (day, describe_archive_members_populate_redis_for_day(day, tgz_archive_dir))
      for day in days
  ]
  return " redis_by_day=" + " ".join(parts)


def _max_effective_ingest_timeout_from_registry(registry: Any) -> Any:
  """
  Internal helper to handle max effective ingest timeout from registry.
  
  Args:
    registry (Any): Registry passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _max_effective_ingest_timeout_from_registry(None)  # doctest: +SKIP
  """
  if registry is None:
    return None
  best = None
  try:
    items = registry.items()
  except Exception:
    return None
  for _pid, raw in items:
    if not isinstance(raw, dict):
      continue
    raw_timeout = raw.get("timeout_s")
    if raw_timeout is None:
      continue
    try:
      value = float(raw_timeout)
    except (TypeError, ValueError):
      continue
    if best is None or value > best:
      best = value
  return best


def _warn_if_pool_stall_wall_below_ingest_timeout_max() -> None:
  """
  Internal helper to handle warn if pool stall wall below ingest timeout max.
  
  Returns:
    None
  
  Examples:
    >>> _warn_if_pool_stall_wall_below_ingest_timeout_max()  # doctest: +SKIP
  """
  poll_s = float(cfg.get_sync_pool_poll_timeout_s())
  abort_n = int(cfg.get_sync_pool_stall_abort_after_timeouts())
  max_per_file = float(cfg.get_sync_ingest_per_file_timeout_max_s())
  if max_per_file <= 0.0 or poll_s <= 0.0:
    return
  stall_wall_s = poll_s * abort_n
  if stall_wall_s > max_per_file:
    return
  min_abort = int(max_per_file / poll_s) + 1
  log_print(
      "WARN: sync_pool stall ceiling wall %.0fs (abort=%d poll=%.0fs) is not "
      "above sync_ingest_per_file_timeout_max_s=%.0fs; raise "
      "sync_pool_stall_abort_after_timeouts ceiling to at least %d "
      "(wall %.0fs at current poll; per-batch abort is dynamic from largest file)"
      % (
          stall_wall_s,
          abort_n,
          poll_s,
          max_per_file,
          min_abort,
          min_abort * poll_s,
      ),
      flush=True,
  )


def _build_ingest_stall_log_suffix(
  *,
  sample: Any,
  day_hint: Any,
  stall_diagnostics: Any,
  progress_state: Any,
  alive_workers: Any,
  consecutive: Any,
  poll_timeout_s: Any,
  distinct_days_from_sample_fn: Any | None = None,
  redis_populate_for_sample_fn: Any | None = None,
) -> Any:
  """
  Internal helper to build the ingest stall log suffix.
  
  Args:
    sample (Any): Sample passed to this helper.
    day_hint (Any): Day hint passed to this helper.
    stall_diagnostics (Any): Stall diagnostics passed to this helper.
    progress_state (Any): Progress state passed to this helper.
    alive_workers (Any): Alive workers passed to this helper.
    consecutive (Any): Consecutive passed to this helper.
    poll_timeout_s (Any): Poll timeout s passed to this helper.
    distinct_days_from_sample_fn (Any | None): One of ``Any``, ``None``.
    redis_populate_for_sample_fn (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _build_ingest_stall_log_suffix(0)  # doctest: +SKIP
  """
  defer_on, defer_reason = _ingest_stall_defer_state(
      day_hint,
      progress_state,
      stall_diagnostics=stall_diagnostics,
      consecutive_timeouts=consecutive,
      pool=getattr(stall_diagnostics, "active_pool", None),
      sample=sample,
  )
  floor_timeout_s = float(cfg.get_sync_ingest_per_file_timeout_s())
  max_timeout_s = float(cfg.get_sync_ingest_per_file_timeout_max_s())
  diag = stall_diagnostics or IngestStallDiagnostics()
  worker_registry = diag.worker_registry
  if worker_registry is not None:
    prune_stale_worker_stages(worker_registry, max_age_s=900.0)
  worker_stages = format_worker_stages_snapshot(
      worker_registry,
      prefer_paths=sample,
  )
  registry_n = count_worker_registry_entries(worker_registry)
  effective_timeout_s = _max_effective_ingest_timeout_from_registry(worker_registry)
  effective_text = (
      "%.1f" % effective_timeout_s
      if effective_timeout_s is not None
      else "-"
  )
  in_flight_n = len(sample or ())
  since_last = diag.seconds_since_last_imap_completion()
  since_text = "%.1f" % since_last if since_last >= 0.0 else "-"
  ingest_pool_n = int(cfg.get_sync_ingest_pool_processes())
  registry_gap = ""
  if registry_n < in_flight_n:
    registry_gap = " worker_registry_gap=%d" % (in_flight_n - registry_n)
  batch_max_s = float(getattr(diag, "current_imap_batch_max_timeout_s", 0.0) or 0.0)
  dynamic_abort = int(getattr(diag, "dynamic_stall_abort_after_polls", 0) or 0)
  dynamic_wall = float(getattr(diag, "dynamic_stall_wall_s", 0.0) or 0.0)
  if distinct_days_from_sample_fn is None:
    distinct_days_fn = _distinct_calendar_days_from_paths
  else:
    distinct_days_fn = distinct_days_from_sample_fn
  if redis_populate_for_sample_fn is None:
    redis_suffix_fn = _format_redis_populate_for_in_flight_days
  else:
    redis_suffix_fn = redis_populate_for_sample_fn
  return (
      " sync_ingest_per_file_timeout_s=%s sync_ingest_per_file_timeout_max_s=%s"
      " batch_max_ingest_timeout_s=%.1f dynamic_stall_abort_after=%d"
      " dynamic_stall_wall_s=%.0f effective_ingest_timeout_s=%s"
      " stall_defer=%s defer_reason=%s imap_batch_cap=%d chunk_batch=%d imap_batch=%d"
      " distinct_in_flight_days=%s in_flight_file_meta=%s"
      " seconds_since_last_imap_completion=%s ingest_pipeline=%s"
      " sync_ingest_pool_processes=%s day_close=%s chunk_prewarm=%s"
      " worker_registry_n=%d in_flight_n=%d worker_stages=%s%s%s"
      % (
          floor_timeout_s,
          max_timeout_s,
          batch_max_s,
          dynamic_abort,
          dynamic_wall,
          effective_text,
          "on" if defer_on else "off",
          defer_reason,
          int(diag.imap_batch_cap or 0),
          int(diag.chunk_batch_size or 0),
          int(diag.current_imap_batch_size or len(sample)),
          ",".join(distinct_days_fn(sample)) or "-",
          _in_flight_file_meta_from_paths(sample),
          since_text,
          diag.ingest_pipeline or "combined",
          ingest_pool_n,
          diag.format_day_close_pipeline_detail(),
          diag.chunk_prewarm_summary or "-",
          registry_n,
          in_flight_n,
          worker_stages,
          registry_gap,
          redis_suffix_fn(sample),
      )
  )


def _make_ingest_stall_warning_fn(
  tracker: Any,
  *,
  pool: Any,
  thread_count: int,
  chunk_counter: Any,
  pending_count: int,
  stall_diagnostics: Any | None = None,
  progress_state: Any | None = None,
  day_hint_from_sample_fn: Any | None = None,
  distinct_days_from_sample_fn: Any | None = None,
  redis_populate_for_sample_fn: Any | None = None,
) -> Any:
  """
  Internal helper to make the ingest stall warning function.
  
  Args:
    tracker (Any): Tracker passed to this helper.
    pool (Any): Live handle (pool, client, or connection).
    thread_count (int): Integer value for thread count.
    chunk_counter (Any): Chunk counter passed to this helper.
    pending_count (int): Integer value for pending count.
    stall_diagnostics (Any | None): One of ``Any``, ``None``.
    progress_state (Any | None): One of ``Any``, ``None``.
    day_hint_from_sample_fn (Any | None): One of ``Any``, ``None``.
    distinct_days_from_sample_fn (Any | None): One of ``Any``, ``None``.
    redis_populate_for_sample_fn (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _make_ingest_stall_warning_fn(0)  # doctest: +SKIP
  """
  def on_stall_warning(
    consecutive: Any,
    abort_after: Any,
    poll_timeout_s: Any,
    context: Any,
  ) -> None:
    """
    On stall warning.
    
    Args:
      consecutive (Any): Consecutive passed to this helper.
      abort_after (Any): Abort after passed to this helper.
      poll_timeout_s (Any): Poll timeout s passed to this helper.
      context (Any): Context passed to this helper.
    
    Returns:
      None
    
    Examples:
      >>> on_stall_warning(None, None, None, None)  # doctest: +SKIP
    """
    sample = tracker.sample_in_flight() if tracker is not None else []
    active_pool = pool() if callable(pool) else pool
    alive_workers = alive_pool_worker_count(active_pool)
    pool_workers = int(thread_count) if thread_count else alive_workers
    if callable(day_hint_from_sample_fn):
      day_hint = day_hint_from_sample_fn(sample)
    else:
      day_hint = _calendar_day_hint_from_paths(sample)
    extra = _build_ingest_stall_log_suffix(
        sample=sample,
        day_hint=day_hint,
        stall_diagnostics=stall_diagnostics,
        progress_state=progress_state or {},
        alive_workers=alive_workers,
        consecutive=consecutive,
        poll_timeout_s=poll_timeout_s,
        distinct_days_from_sample_fn=distinct_days_from_sample_fn,
        redis_populate_for_sample_fn=redis_populate_for_sample_fn,
    )
    redis_hint = ""
    if day_hint and "redis_by_day=" not in extra:
      redis_hint = " " + describe_archive_members_populate_redis_for_day(
          day_hint,
          tgz_archive_dir,
      )
    log_print(
        "WARN: pool imap stall progress consecutive_timeouts=%d/%d "
        "poll_timeout_s=%.3f estimated_stall_s=%.1f context=%s chunk=%d "
        "pending=%d pool_workers_alive=%d/%d in_flight_n=%d "
        "in_flight_day_hint=%s in_flight_sample=%s%s%s"
        % (
            consecutive,
            abort_after,
            poll_timeout_s,
            consecutive * poll_timeout_s,
            context or "pool",
            int(chunk_counter),
            int(pending_count),
            alive_workers,
            pool_workers,
            len(sample),
            day_hint or "-",
            sample,
            redis_hint,
            extra,
        ),
        flush=True,
    )

  return on_stall_warning


def _should_emit_stall_defer_warn(
  defer_reason: Any,
  defer_log_state: Any,
  interval_s: Any,
) -> Any:
  """
  Return True when a pool imap stall defer WARN should be logged.
  
  Args:
    defer_reason (Any): Defer reason passed to this helper.
    defer_log_state (Any): Defer log state passed to this helper.
    interval_s (Any): Interval s passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _should_emit_stall_defer_warn(None, None, None)  # doctest: +SKIP
  """
  if interval_s <= 0.0:
    return True
  now_mono = time.monotonic()
  if defer_log_state.get("last_defer_reason") != defer_reason:
    defer_log_state["last_defer_reason"] = defer_reason
    defer_log_state["last_log_mono"] = now_mono
    return True
  last_log_mono = float(defer_log_state.get("last_log_mono") or 0.0)
  if now_mono - last_log_mono >= interval_s:
    defer_log_state["last_log_mono"] = now_mono
    return True
  return False


def _make_ingest_stall_poll_fn(
  tracker: Any,
  progress_state: Any,
  stall_diagnostics: Any | None = None,
  *,
  day_hint_from_sample_fn: Any | None = None,
  supervisor_reap_fn: Any | None = None,
) -> Any:
  """
  Defer pool imap stall abort while Redis populate shows progress.
  
  Args:
    tracker (Any): Tracker passed to this helper.
    progress_state (Any): Progress state passed to this helper.
    stall_diagnostics (Any | None): One of ``Any``, ``None``.
    day_hint_from_sample_fn (Any | None): One of ``Any``, ``None``.
    supervisor_reap_fn (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _make_ingest_stall_poll_fn(None, None, None, None, None)
  """
  defer_log_state = {}

  def on_stall_poll(
    consecutive: Any,
    context: Any,
    pool_health_context: Any,
  ) -> Any:
    """
    On stall poll.
    
    Args:
      consecutive (Any): Consecutive passed to this helper.
      context (Any): Context passed to this helper.
      pool_health_context (Any): Pool health context passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> on_stall_poll(None, None, None)  # doctest: +SKIP
    """
    del context
    if supervisor_reap_fn is not None:
      try:
        supervisor_reap_fn()
      except Exception:
        pass
    sample = []
    if tracker is not None:
      sample = tracker.sample_in_flight()
    elif pool_health_context.get("in_flight_sample"):
      sample = pool_health_context["in_flight_sample"]
    active_pool = pool_health_context.get("active_pool")
    if callable(day_hint_from_sample_fn):
      day_hint = day_hint_from_sample_fn(sample)
    else:
      day_hint = _calendar_day_hint_from_paths(sample)
    defer_on, defer_reason = _ingest_stall_defer_state(
        day_hint,
        progress_state,
        stall_diagnostics=stall_diagnostics,
        consecutive_timeouts=consecutive,
        pool=active_pool,
        sample=sample,
        day_hint_from_sample_fn=day_hint_from_sample_fn,
    )
    if not defer_on:
      defer_log_state.clear()
      return False
    log_interval_s = float(cfg.get_sync_pool_stall_defer_log_interval_s())
    if not _should_emit_stall_defer_warn(defer_reason, defer_log_state, log_interval_s):
      return True
    poll_s = float(cfg.get_sync_pool_poll_timeout_s())
    estimated_stall_s = consecutive * poll_s
    worker_stages = format_worker_stages_snapshot(
        getattr(stall_diagnostics, "worker_registry", None)
        if stall_diagnostics is not None
        else None,
        prefer_paths=sample,
    )
    if defer_reason == "long_ingest_budget":
      effective = _max_effective_ingest_timeout_from_registry(
          getattr(stall_diagnostics, "worker_registry", None)
          if stall_diagnostics is not None
          else None,
      )
      batch_max_s = float(
          getattr(stall_diagnostics, "current_imap_batch_max_timeout_s", 0.0)
          if stall_diagnostics is not None
          else 0.0,
      )
      log_print(
          "WARN: pool imap stall deferred: long ingest budget "
          "effective_ingest_timeout_s=%.1f batch_max_ingest_timeout_s=%.1f "
          "dynamic_stall_wall_s=%.0f consecutive_timeouts=%d "
          "estimated_stall_s=%.1f in_flight_n=%d worker_stages=%s"
          % (
              effective or 0.0,
              batch_max_s,
              _dynamic_stall_wall_seconds(stall_diagnostics),
              int(consecutive),
              estimated_stall_s,
              len(sample),
              worker_stages,
          ),
          flush=True,
      )
      return True
    if defer_reason == "worker_progress_active":
      log_print(
          "WARN: pool imap stall deferred: worker progress active "
          "consecutive_timeouts=%d in_flight_n=%d estimated_stall_s=%.1f "
          "worker_stages=%s"
          % (
              int(consecutive),
              len(sample),
              estimated_stall_s,
              worker_stages,
          ),
          flush=True,
      )
      return True
    redis_snapshot = describe_archive_members_populate_redis_for_day(
        day_hint,
        tgz_archive_dir,
    )
    log_print(
        "WARN: pool imap stall deferred: Redis populate active for day=%s (%s) "
        "consecutive_timeouts=%d in_flight_n=%d estimated_stall_s=%.1f "
        "worker_stages=%s"
        % (
            day_hint,
            redis_snapshot,
            int(consecutive),
            len(sample),
            consecutive * float(cfg.get_sync_pool_poll_timeout_s()),
            worker_stages,
        ),
        flush=True,
    )
    return True

  return on_stall_poll


def _effective_ingest_imap_inflight_cap(
  thread_count: int,
  path_count: int,
) -> Any:
  """
  Sliding-window inflight equals live pool size (capped by.
  
    ``sync_ingest_pool_processes``).
  
  Args:
    thread_count (int): Integer value for thread count.
    path_count (int): Integer value for path count.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _effective_ingest_imap_inflight_cap(0, 0)  # doctest: +SKIP
  """
  return max(1, min(int(path_count), int(thread_count)))


def _update_sliding_window_stall_diagnostics(
  stall_diagnostics: Any,
  in_flight_paths: Any,
  inflight_cap: Any,
) -> None:
  """
  Internal helper to update the sliding window stall diagnostics.
  
  Args:
    stall_diagnostics (Any): Stall diagnostics passed to this helper.
    in_flight_paths (Any): Iterable of filesystem paths as strings.
    inflight_cap (Any): Inflight cap passed to this helper.
  
  Returns:
    None
  
  Examples:
    >>> _update_sliding_window_stall_diagnostics(None, None, None)
  """
  if stall_diagnostics is None:
    return
  poll_s = float(cfg.get_sync_pool_poll_timeout_s())
  batch_max_s = _max_ingest_per_file_timeout_for_paths(in_flight_paths)
  batch_abort = _stall_abort_polls_for_batch(in_flight_paths or ())
  stall_diagnostics.current_imap_in_flight = len(in_flight_paths or ())
  stall_diagnostics.current_imap_batch_max_timeout_s = batch_max_s
  stall_diagnostics.dynamic_stall_abort_after_polls = batch_abort
  stall_diagnostics.dynamic_stall_wall_s = batch_abort * poll_s
  stall_diagnostics.imap_batch_cap = inflight_cap


def _clear_ingest_worker_file_caches() -> None:
  """
  Drop per-process host itimes caches after parse segments.
  
  Returns:
    None
  
  Examples:
    >>> _clear_ingest_worker_file_caches()  # doctest: +SKIP
  """
  sync_timedb_host_itimes.reset_host_itimes_caches()


def _clear_ingest_worker_memory_caches() -> None:
  """
  Full per-task cache sweep including daily archive member L1.
  
  Returns:
    None
  
  Examples:
    >>> _clear_ingest_worker_memory_caches()  # doctest: +SKIP
  """
  _clear_ingest_worker_file_caches()
  clear_daily_archive_members_cache()


def _release_ingest_worker_heap() -> None:
  """
  Return parse heap to the OS on Linux (mid-task or end-of-task trim).
  
  Returns:
    None
  
  Examples:
    >>> _release_ingest_worker_heap()  # doctest: +SKIP
  """
  _clear_ingest_worker_file_caches()
  if not cfg.get_sync_ingest_malloc_trim_after_file():
    return
  gc.collect()
  try:
    libc = ctypes.CDLL("libc.so.6")
    libc.malloc_trim(0)
  except (OSError, AttributeError):
    pass


def _release_ingest_worker_memory(stats_file: str = "") -> Any:
  """
  Full per-task worker memory release; returns telemetry meta for supervisor.
  
  Args:
    stats_file (str): String for stats file.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _release_ingest_worker_memory("x")  # doctest: +SKIP
  """
  from hpcperfstats.dbload.lib.sync_timedb_worker_memory import (
      release_spawn_pool_worker_memory,
  )

  release_spawn_pool_worker_memory()
  increment_worker_tasks_on_worker()
  return measure_worker_rss_after_release(stats_file)


@dataclass
class SealedArchiveIngestProgress:
  """
  Per-sealed-day file counter for ``sync_timedb_archive`` completion logs.
  
  Attributes:
    completed_files: Attribute.
    total_files: Attribute.
  """

  total_files: int
  completed_files: int = 0


_sealed_archive_ingest_progress: contextvars.ContextVar = contextvars.ContextVar(
    "sealed_archive_ingest_progress",
    default=None,
)


def set_sealed_archive_ingest_progress(total_files: int) -> None:
  """
  Begin sealed-archive member progress (``sync_timedb_archive`` workers only).
  
  Args:
    total_files (int): Integer value for total files.
  
  Returns:
    None
  
  Examples:
    >>> set_sealed_archive_ingest_progress(0)  # doctest: +SKIP
  """
  try:
    total = int(total_files)
  except (TypeError, ValueError):
    total = 0
  _sealed_archive_ingest_progress.set(
      SealedArchiveIngestProgress(total_files=max(0, total)),
  )


def clear_sealed_archive_ingest_progress() -> None:
  """
  Clear sealed archive ingest progress.
  
  Returns:
    None
  
  Examples:
    >>> clear_sealed_archive_ingest_progress()  # doctest: +SKIP
  """
  _sealed_archive_ingest_progress.set(None)


def advance_sealed_archive_ingest_progress(count: int = 1) -> None:
  """
  Count sealed-archive members done without ingest (for example oversize skips).
  
  Args:
    count (int): Integer value for count.
  
  Returns:
    None
  
  Examples:
    >>> advance_sealed_archive_ingest_progress(0)  # doctest: +SKIP
  """
  progress = _sealed_archive_ingest_progress.get()
  if progress is None or progress.total_files <= 0:
    return
  try:
    n = int(count)
  except (TypeError, ValueError):
    n = 0
  if n > 0:
    progress.completed_files += n


def _sealed_archive_ingest_remaining_pair() -> Any:
  """
  Return ``(remaining, total)`` after incrementing completed, or ``None``.
  
  Returns:
    Any: Open return polymorphism from
    ``_sealed_archive_ingest_remaining_pair``: concrete type depends on inputs
    and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> _sealed_archive_ingest_remaining_pair()  # doctest: +SKIP
  """
  progress = _sealed_archive_ingest_progress.get()
  if progress is None or progress.total_files <= 0:
    return None
  advance_sealed_archive_ingest_progress(1)
  remaining = max(0, progress.total_files - progress.completed_files)
  return remaining, progress.total_files


def _spawn_pool_recycle_kwargs() -> Any:
  """
  Internal helper to handle spawn pool recycle kwargs.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _spawn_pool_recycle_kwargs()  # doctest: +SKIP
  """
  return sync_timedb_spawn_pool_recycle_kwargs(pool_kind_log_label="ingest-pool")

# Set to 1/yes/true so ingest runs in the parent process (no spawn pool). Required
# for pytest-django: pool workers would reconnect with default [DEFAULT] dbname instead
# of the test database created for the session.
_SYNC_TIMEDB_INGEST_INLINE_ENV = "HPCPERFSTATS_SYNC_TIMEDB_INGEST_INLINE"


def _sync_timedb_ingest_inline_requested() -> Any:
  """
  Internal helper to sync the timedb ingest inline requested.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _sync_timedb_ingest_inline_requested()  # doctest: +SKIP
  """
  return os.environ.get(_SYNC_TIMEDB_INGEST_INLINE_ENV, "").strip().lower() in (
      "1", "yes", "true")

# Rows per bulk_create batch to limit peak memory per worker (see sync_bulk_create_batch_size).
def bulk_create_batch_size() -> Any:
  """
  Bulk create batch size.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> bulk_create_batch_size()  # doctest: +SKIP
  """
  return cfg.get_sync_bulk_create_batch_size()
_HOST_ITIMES_CACHE = sync_timedb_host_itimes._HOST_ITIMES_CACHE
_HOST_ITIMES_CACHE_REFRESH_SECONDS = sync_timedb_host_itimes._HOST_ITIMES_CACHE_REFRESH_SECONDS
_HOST_ITIMES_CACHE_MAX_ENTRIES = sync_timedb_host_itimes._HOST_ITIMES_CACHE_MAX_ENTRIES
_HOST_ITIMES_SET_OVERFLOW = sync_timedb_host_itimes.HOST_ITIMES_SET_OVERFLOW
_HOST_SECOND_PRESENT_CACHE = sync_timedb_host_itimes._HOST_SECOND_PRESENT_CACHE
_HOST_SECOND_PRESENT_CACHE_TTL_S = sync_timedb_host_itimes._HOST_SECOND_PRESENT_CACHE_TTL_S
_HOST_SECOND_PRESENT_CACHE_MAX_ENTRIES = sync_timedb_host_itimes._HOST_SECOND_PRESENT_CACHE_MAX_ENTRIES
_TREE_RSS_DEFER_SLEEP_SECONDS = 5.0

tgz_archive_dir = cfg.get_daily_archive_dir_path()


def _reap_supervisor_pool_children(
  ingest_pool: Any,
  archive_pool: Any,
  populate_pool_controller: Any,
  *,
  context: str = "chunk_boundary",
) -> None:
  """
  Reap dead pool workers and zombies; restart dead populate-pool workers.
  
  Each step is fault-isolated so a closed/foreign ``Process.is_alive()`` raise
  in pool reap cannot skip zombie ``waitpid`` or the unreaped-zombie WARN.
  
  Args:
    ingest_pool (Any): Ingest pool passed to this helper.
    archive_pool (Any): Archive pool passed to this helper.
    populate_pool_controller (Any): Populate pool controller passed to this
    helper.
    context (str): String for context.
  
  Returns:
    None
  
  Examples:
    >>> _reap_supervisor_pool_children(None, None, None, "x")  # doctest: +SKIP
  """
  def _step(name: Any, fn: Any) -> None:
    """
    Internal helper to handle step.
    
    Args:
      name (Any): Name passed to this helper.
      fn (Any): Callable invoked by this helper.
    
    Returns:
      None
    
    Examples:
      >>> _step(None, None)  # doctest: +SKIP
    """
    try:
      fn()
    except Exception as exc:
      log_print(
          "WARN: supervisor child hygiene step failed step=%s context=%s "
          "err=%s: %s"
          % (name, context, type(exc).__name__, exc),
          flush=True,
      )

  _step(
      "reap_ingest_pool",
      lambda: reap_pool_worker_pids(
          ingest_pool, context="%s_ingest" % context,
      ),
  )
  _step(
      "reap_archive_pool",
      lambda: reap_pool_worker_pids(
          archive_pool, context="%s_archive" % context,
      ),
  )
  _step(
      "reap_zombie_children",
      lambda: reap_zombie_children_of_self(context=context),
  )
  if populate_pool_controller is not None:
    try:
      populate_pool_controller.reap_and_restart()
    except Exception as exc:
      log_print(
          "WARN: populate-pool reap_and_restart failed: %s" % exc,
          flush=True,
      )
  _step(
      "warn_unreaped_zombies",
      lambda: warn_unreaped_zombie_children(context=context),
  )


def _maybe_reap_supervisor_pool_children_throttled(
  ingest_pool: Any,
  archive_pool: Any,
  populate_pool_controller: Any,
  *,
  context: str = "throttled",
) -> Any:
  """
  Run supervisor child hygiene at most once per.
  
    ``_SUPERVISOR_CHILD_REAP_INTERVAL_S``.
  
  Args:
    ingest_pool (Any): Ingest pool passed to this helper.
    archive_pool (Any): Archive pool passed to this helper.
    populate_pool_controller (Any): Populate pool controller passed to this
    helper.
    context (str): String for context.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _maybe_reap_supervisor_pool_children_throttled(None, None, None, "x")
  """
  global _last_supervisor_child_reap_mono
  now_mono = time.monotonic()
  if now_mono - _last_supervisor_child_reap_mono < _SUPERVISOR_CHILD_REAP_INTERVAL_S:
    return False
  _last_supervisor_child_reap_mono = now_mono
  _reap_supervisor_pool_children(
      ingest_pool,
      archive_pool,
      populate_pool_controller,
      context=context,
  )
  return True


def _exit_on_archive_members_redis_unavailable(exc: Any) -> None:
  """
  Fatal exit when Redis L2 contract fails during ingest or startup.
  
  Populate-pool-down / refuse-stream is recoverable (enqueue + wait / ensure
  pool) and must not map to immediate ``sys.exit(1)``.
  
  Args:
    exc (Any): Exception instance being classified or logged.
  
  Returns:
    None
  
  Examples:
    >>> _exit_on_archive_members_redis_unavailable(None)  # doctest: +SKIP
  """
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      is_populate_pool_unavailable_error,
  )

  log_print("ERROR: %s" % exc, flush=True)
  if is_populate_pool_unavailable_error(exc):
    log_print(
        "WARNING: populate-pool unavailable is not an immediate L2 fatal; "
        "ensure/restart populate-pool and wait within populate_max_seconds "
        "(ingest/archive must enqueue, never sealed-stream).",
        flush=True,
    )
    return
  if isinstance(exc, ArchiveMembersRedisConnectionError):
    log_print(
        "ERROR: sync_archive_members_redis_enabled=yes requires a reachable "
        "Redis at [CACHE] redis_location.",
        flush=True,
    )
  elif isinstance(exc, ArchiveMembersPopulateStalledError):
    redis_reachable = False
    try:
      from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
          get_archive_members_redis_client,
      )
      client = get_archive_members_redis_client(required=False)
      if client is not None:
        client.ping()
        redis_reachable = True
    except Exception:
      redis_reachable = False
    if redis_reachable:
      log_print(
          "ERROR: archive members populate stalled or timed out "
          "(Redis at [CACHE] redis_location=%s is reachable)."
          % cfg.get_redis_location(),
          flush=True,
      )
    else:
      log_print(
          "ERROR: archive members populate stalled or timed out.",
          flush=True,
      )
  else:
    log_print(
        "ERROR: archive members Redis L2 contract failed.",
        flush=True,
    )
  sys.exit(1)


@contextmanager
def _sync_worker_db_task() -> Iterator[Any]:
  """
  Refresh DB connections at worker task start and release them at end.
  
  Yields:
    Iterator[Any]: Open return polymorphism from ``_sync_worker_db_task``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> _sync_worker_db_task()  # doctest: +SKIP
  """
  close_old_connections()
  try:
    yield
  finally:
    try:
      connections.close_all()
    except Exception:
      pass


def _ensure_daily_archive_dir_exists() -> None:
  """
  Create daily archive dir, tolerating races.
  
  Returns:
    None
  
  Raises:
    Exception: Raised when ``_ensure_daily_archive_dir_exists`` hits a
    ``Exception`` failure path.
  
  Examples:
    >>> _ensure_daily_archive_dir_exists()  # doctest: +SKIP
  """
  try:
    os.makedirs(tgz_archive_dir, exist_ok=True)
  except OSError:
    if not os.path.isdir(tgz_archive_dir):
      raise


def _count_daily_tars(daily_archive_dir: str) -> Any:
  """
  Count daily ``.tar`` files in the archive directory.
  
  Args:
    daily_archive_dir (str): String for daily archive dir.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _count_daily_tars("x")  # doctest: +SKIP
  """
  if not daily_archive_dir or not os.path.isdir(daily_archive_dir):
    return 0
  return sum(1 for _ in iter_daily_tar_paths(daily_archive_dir))


def _log_db_lock_wait(batch_kind: Any, stats_file: str, lock_wait: Any) -> None:
  """
  Log Manager write-shard acquire waits above the threshold.

  Log text keeps the historical ``DB lock wait`` token; this is **not**
  Postgres ``lock_timeout`` — it is multiprocessing ``Manager`` shard
  ``acquire`` wait time (``sync_write_lock_shards``).

  Args:
    batch_kind (Any): Batch kind token (``proc`` / ``host``).
    stats_file (str): Stats file path for the waiting worker.
    lock_wait (Any): Seconds spent waiting to acquire the shard lock.

  Returns:
    None

  Examples:
    >>> _log_db_lock_wait("proc", "/x", 0.0)
  """
  if lock_wait <= LOCK_WAIT_LOG_THRESHOLD_SECONDS:
    return
  log_print(
      "DB lock wait %s batch file=%s wait=%.3fs" % (
          batch_kind, stats_file, lock_wait
      ),
      flush=True,
  )


_ingest_db_shard_lock_s: contextvars.ContextVar[float] = contextvars.ContextVar(
    "ingest_db_shard_lock_s",
    default=0.0,
)
_ingest_postgres_s: contextvars.ContextVar[float] = contextvars.ContextVar(
    "ingest_postgres_s",
    default=0.0,
)


def _reset_ingest_write_timing() -> None:
  """
  Zero per-file Manager-acquire and Postgres-hold timing accumulators.

  Returns:
    None

  Examples:
    >>> _reset_ingest_write_timing()
  """
  _ingest_db_shard_lock_s.set(0.0)
  _ingest_postgres_s.set(0.0)


def _add_ingest_db_shard_lock_s(delta_s: float) -> None:
  """
  Add Manager write-shard acquire wait seconds for this file.

  Args:
    delta_s (float): Non-negative seconds to accumulate.

  Returns:
    None

  Examples:
    >>> _reset_ingest_write_timing()
    >>> _add_ingest_db_shard_lock_s(1.25)
  """
  delta = float(delta_s)
  if delta <= 0.0:
    return
  _ingest_db_shard_lock_s.set(float(_ingest_db_shard_lock_s.get()) + delta)


def _add_ingest_postgres_s(delta_s: float) -> None:
  """
  Add seconds spent holding the Manager shard during ORM writes.

  Args:
    delta_s (float): Non-negative hold seconds to accumulate.

  Returns:
    None

  Examples:
    >>> _reset_ingest_write_timing()
    >>> _add_ingest_postgres_s(0.5)
  """
  delta = float(delta_s)
  if delta <= 0.0:
    return
  _ingest_postgres_s.set(float(_ingest_postgres_s.get()) + delta)


def _snapshot_ingest_write_timing() -> dict[str, float]:
  """
  Return accumulated ``db_shard_lock_s`` and ``postgres_s`` for this file.

  Returns:
    dict[str, float]: Timing keys for outcome meta / logs.

  Examples:
    >>> _reset_ingest_write_timing()
    >>> _snapshot_ingest_write_timing()["db_shard_lock_s"]
    0.0
  """
  return {
      "db_shard_lock_s": float(_ingest_db_shard_lock_s.get()),
      "postgres_s": float(_ingest_postgres_s.get()),
  }


def _merge_ingest_write_timing_into_meta(meta: Any) -> dict[str, Any]:
  """
  Copy outcome meta and attach write-path timing snapshot keys.

  Args:
    meta (Any): Existing outcome meta mapping or ``None``.

  Returns:
    dict[str, Any]: Meta with ``db_shard_lock_s`` / ``postgres_s`` set.

  Examples:
    >>> _reset_ingest_write_timing()
    >>> _merge_ingest_write_timing_into_meta({})["postgres_s"]
    0.0
  """
  out = dict(meta or {})
  out.update(_snapshot_ingest_write_timing())
  return out



@dataclass(frozen=True)
class ArchiveTask:
  """
  Hold ArchiveTask state and behavior.
  
  Attributes:
    archive_info: ``archive_info``.
    attempt: ``attempt``.
  """
  archive_info: tuple
  attempt: int = 1


@dataclass(frozen=True)
class ArchiveAppendOutcome:
  """
  Archive pool append result plumbed to supervisor finalize.
  
  Attributes:
    ok: Attribute.
    redis_merge_ok: Attribute.
    skip_finalize_invalidate: Attribute.
    skipped_paths: Attribute.
    soft_requeue: Attribute.
    gate_skipped: Attribute.
  """

  ok: bool = True
  redis_merge_ok: bool = False
  skip_finalize_invalidate: bool = True
  # Oversized paths skipped after convert_fail_skip (still finalized as archived).
  skipped_paths: tuple = ()
  # Restore race: requeue on heap without burning archive_retry attempts.
  soft_requeue: bool = False
  # DB ingest gate blocked every path; hand off to ingest before append ACK.
  gate_skipped: bool = False

  def __bool__(self) -> Any:
    """
    Return the truth value of this object.
    
    Returns:
      Any: Open return polymorphism from ``__bool__``: concrete type depends
      on inputs and branch (mapping, scalar, handle, or ``None``-like empty).
    
    Examples:
      >>> __bool__()  # doctest: +SKIP
    """
    return self.ok


# Invalidation reasons that must not sync-re-prewarm while a hold blocks waiters.
DEFER_SYNC_PREWARM_INVALIDATION_REASONS = frozenset({
    "archive_finalize",
    "tar_restore_pre",
    "tar_restore",
})

# Match former dispatch restore-skip backoff constant.
ARCHIVE_RESTORE_SOFT_REQUEUE_BACKOFF_S = 15.0


def should_defer_sync_prewarm_for_invalidation_reason(reason: Any) -> Any:
  """
  Return True if defer sync prewarm for invalidation reason.
  
  Args:
    reason (Any): Reason passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> should_defer_sync_prewarm_for_invalidation_reason(None)
  """
  return reason in DEFER_SYNC_PREWARM_INVALIDATION_REASONS


def _archive_append_outcome_is_soft_requeue(result: Any) -> Any:
  """
  Internal helper to archive the append outcome is soft requeue.
  
  Args:
    result (Any): Result passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _archive_append_outcome_is_soft_requeue(None)  # doctest: +SKIP
  """
  return isinstance(result, ArchiveAppendOutcome) and bool(result.soft_requeue)


def _archive_append_outcome_is_gate_skip(result: Any) -> Any:
  """
  Internal helper: append worker returned gate-skipped paths only.
  
  Args:
    result (Any): Result passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _archive_append_outcome_is_gate_skip(None)  # doctest: +SKIP
  """
  return isinstance(result, ArchiveAppendOutcome) and bool(result.gate_skipped)


def _archive_task_succeeded(result: Any) -> Any:
  """
  Internal helper to archive the task succeeded.
  
  Args:
    result (Any): Result passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _archive_task_succeeded(None)  # doctest: +SKIP
  """
  if result is False or result is None:
    return False
  if isinstance(result, ArchiveAppendOutcome):
    if result.soft_requeue or result.gate_skipped:
      return False
    return result.ok
  return bool(result)


def _archive_finalize_skip_invalidate_log_reason(result: Any) -> Any:
  """
  Internal helper to archive the finalize skip invalidate log reason.
  
  Args:
    result (Any): Result passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _archive_finalize_skip_invalidate_log_reason(None)  # doctest: +SKIP
  """
  if isinstance(result, ArchiveAppendOutcome) and result.redis_merge_ok:
    return "redis_merge_warm"
  return "no_tar_mutation_or_worker_invalidated"


def _shutdown_ingest_pools(
  ingest_pool: Any,
  *,
  force_terminate: bool = False,
) -> None:
  """
  Bounded shutdown for ingest pool (terminate after worker OOM/SIGKILL).
  
  Args:
    ingest_pool (Any): Ingest pool passed to this helper.
    force_terminate (bool): Whether to enable force terminate.
  
  Returns:
    None
  
  Examples:
    >>> _shutdown_ingest_pools(None, True)  # doctest: +SKIP
  """
  close_pool_bounded(ingest_pool, force_terminate=force_terminate)


def _cap_pending_stats_files_list(paths: Any, ingest_queue_max: Any) -> Any:
  """
  Internal helper to handle cap pending stats files list.
  
  Args:
    paths (Any): Iterable of filesystem paths as strings.
    ingest_queue_max (Any): Ingest queue max passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _cap_pending_stats_files_list(None, None)  # doctest: +SKIP
  """
  return cap_pending_stats_file_list(paths, ingest_queue_max, log_fn=log_print)


def _prior_day_tars_from_archive_mapping(
  ar_file_mapping: Any,
  *,
  local_tz: Any,
) -> Any:
  """
  Normalized prior-calendar daily ``.tar`` paths referenced by chunk mapping.
  
  Args:
    ar_file_mapping (Any): Ar file mapping passed to this helper.
    local_tz (Any): Local tz passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _prior_day_tars_from_archive_mapping(None, None)  # doctest: +SKIP
  """
  today_local = datetime.now(local_tz).date()
  chunk_tars = set()
  for archive_path in ar_file_mapping:
    tar_path = os.path.normpath(daily_tar_path_from_compressed(archive_path))
    base = os.path.basename(tar_path)
    if not base.endswith(".tar"):
      continue
    try:
      day_date = datetime.strptime(base[:-4], "%Y-%m-%d").date()
    except ValueError:
      continue
    if day_date < today_local:
      chunk_tars.add(tar_path)
  return chunk_tars


def _handle_pool_worker_exit_fatal(
  exc: Any,
  *,
  ingest_pool: Any | None = None,
  archive_pool: Any | None = None,
) -> None:
  """
  ``os._exit`` immediately — do not wait on pool terminate or context managers.
  
  Args:
    exc (Any): Exception instance being classified or logged.
    ingest_pool (Any | None): One of ``Any``, ``None``.
    archive_pool (Any | None): One of ``Any``, ``None``.
  
  Returns:
    None
  
  Examples:
    >>> _handle_pool_worker_exit_fatal(None, None, None)  # doctest: +SKIP
  """
  del ingest_pool, archive_pool
  hard_exit_pool_worker_error(exc)


def _reraise_or_handle_pool_worker_exit(
  exc: Any,
  *,
  ingest_pool: Any,
  archive_pool: Any | None = None,
) -> None:
  """
  Terminate ingest/archive pools and re-raise worker death.
  
  Args:
    exc (Any): Exception instance being classified or logged.
    ingest_pool (Any): Ingest pool passed to this helper.
    archive_pool (Any | None): One of ``Any``, ``None``.
  
  Returns:
    None
  
  Raises:
    Exception: Raised when ``_reraise_or_handle_pool_worker_exit`` hits a
    ``Exception`` failure path.
    exc: Raised when ``_reraise_or_handle_pool_worker_exit`` hits a ``exc``
    failure path.
  
  Examples:
    >>> _reraise_or_handle_pool_worker_exit(None, None, None)  # doctest: +SKIP
  """
  if isinstance(exc, MultiprocessingWorkerExitError):
    ctx = getattr(exc, "context", "") or "pool_worker_exit"
    terminate_pool_bounded(ingest_pool, context=ctx)
    terminate_pool_bounded(archive_pool, context=ctx)
    raise
  raise exc



class SyncFileState(str, Enum):
  """
  Hold SyncFileState state and behavior.
  
  Subclasses ``str``, extending that type with this class's fields and behavior.
  
  Subclasses ``str``, extending that type with this class's fields and behavior.
  """
  DISCOVERED = "discovered"
  PARSED = "parsed"
  WRITTEN = "written"
  ARCHIVE_QUEUED = "archive_queued"
  ARCHIVE_FAILED_RETRYABLE = "archive_failed_retryable"
  ARCHIVED = "archived"


_SYNC_STATE_TRANSITIONS = {
    SyncFileState.DISCOVERED: {
        SyncFileState.WRITTEN,
        SyncFileState.ARCHIVE_QUEUED,
    },
    SyncFileState.WRITTEN: {
        SyncFileState.ARCHIVE_QUEUED,
        SyncFileState.ARCHIVED,
    },
    SyncFileState.ARCHIVE_QUEUED: {
        SyncFileState.ARCHIVE_FAILED_RETRYABLE,
        SyncFileState.ARCHIVED,
    },
    SyncFileState.ARCHIVE_FAILED_RETRYABLE: {
        SyncFileState.ARCHIVE_QUEUED,
        SyncFileState.ARCHIVED,
    },
    SyncFileState.ARCHIVED: set(),
}

SYNC_TIMEDB_DEAD_LETTER_BASENAME = ".sync_timedb_dead_letter.json"


def _transition_file_state(
  file_states: Any,
  path: str,
  new_state: Any,
) -> Any:
  """
  Best-effort state transition validator for per-file supervisor state.
  
  Args:
    file_states (Any): File states passed to this helper.
    path (str): String for path.
    new_state (Any): New state passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _transition_file_state(None, "x", None)  # doctest: +SKIP
  """
  current = file_states.get(path)
  if current is None:
    file_states[path] = new_state
    return True
  # Re-entrant ingest while raw remains on disk or archive dispatch replays:
  # db-complete re-ingest may complete again before append finalizes.
  if new_state == SyncFileState.WRITTEN and current in (
      SyncFileState.ARCHIVE_QUEUED,
      SyncFileState.ARCHIVED,
  ):
    file_states[path] = new_state
    return True
  # Archive replay may queue paths already marked ARCHIVED in checkpoint.
  if (
      current == SyncFileState.ARCHIVED
      and new_state == SyncFileState.ARCHIVE_QUEUED
  ):
    return True
  allowed = _SYNC_STATE_TRANSITIONS.get(current, set())
  if new_state in allowed or new_state == current:
    file_states[path] = new_state
    return True
  log_print(
      "Invalid sync_timedb file state transition path=%s current=%s new=%s"
      % (path, current, new_state),
      flush=True,
  )
  return False


def _load_dead_letter_entries(path: str) -> Any:
  """
  Internal helper to load the dead letter entries.
  
  Args:
    path (str): String for path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _load_dead_letter_entries("x")  # doctest: +SKIP
  """
  raw = load_persistence_document(path, "archive_dead_letter", default=[])
  if not isinstance(raw, list):
    return []
  out = []
  for item in raw:
    if not isinstance(item, dict):
      continue
    archive_info = item.get("archive_info")
    paths = item.get("paths")
    attempt = item.get("attempt", 1)
    if not isinstance(archive_info, list) or len(archive_info) != 2:
      continue
    if not isinstance(paths, list):
      continue
    try:
      attempt = int(attempt)
    except (TypeError, ValueError):
      attempt = 1
    out.append({
        "task": ArchiveTask(archive_info=(archive_info[0], list(archive_info[1])), attempt=max(1, attempt)),
        "paths": list(paths),
        "retry_at": 0.0,
    })
  return out


def _save_dead_letter_entries(path: str, entries: Any) -> None:
  """
  Internal helper to save the dead letter entries.
  
  Args:
    path (str): String for path.
    entries (Any): Entries passed to this helper.
  
  Returns:
    None
  
  Examples:
    >>> _save_dead_letter_entries("x", None)  # doctest: +SKIP
  """
  payload = []
  for item in entries:
    task = item.get("task")
    if task is None:
      continue
    payload.append({
        "archive_info": [task.archive_info[0], list(task.archive_info[1])],
        "paths": list(item.get("paths", [])),
        "attempt": int(task.attempt),
    })
  save_persistence_document(path, "archive_dead_letter", payload)


def _host_recent_timestamps_cached(
  hostname: Any,
  ts_low: Any,
  ts_high: Any,
) -> Any:
  """
  Internal helper to handle host recent timestamps cached.
  
  Args:
    hostname (Any): Hostname passed to this helper.
    ts_low (Any): Ts low passed to this helper.
    ts_high (Any): Ts high passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _host_recent_timestamps_cached(None, None, None)  # doctest: +SKIP
  """
  return sync_timedb_host_itimes.host_recent_timestamps_cached(
      hostname, ts_low, ts_high)


def _pick_write_lock_for_path(lock_or_locks: Any, stats_file: str) -> Any:
  """
  Internal helper to handle pick write lock for path.
  
  Args:
    lock_or_locks (Any): Lock or locks passed to this helper.
    stats_file (str): String for stats file.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _pick_write_lock_for_path(None, "x")  # doctest: +SKIP
  """
  if isinstance(lock_or_locks, list) and lock_or_locks:
    digest = hashlib.blake2b(
        os.path.basename(str(stats_file or "")).encode("utf-8"),
        digest_size=8,
    ).digest()
    idx = int.from_bytes(digest, "big") % len(lock_or_locks)
    return lock_or_locks[idx]
  return lock_or_locks


def _host_timestamp_second_present_in_db(host: Any, unix_second: Any) -> Any:
  """
  Internal helper to handle host timestamp second present in db.
  
  Args:
    host (Any): Host passed to this helper.
    unix_second (Any): Unix second passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _host_timestamp_second_present_in_db(None, None)  # doctest: +SKIP
  """
  return sync_timedb_host_itimes.host_timestamp_second_present_in_db(
      host, unix_second)


def _reset_sync_runtime_caches() -> None:
  """
  Clear per-process ingest caches between sync_timedb sessions.
  
  Returns:
    None
  
  Examples:
    >>> _reset_sync_runtime_caches()  # doctest: +SKIP
  """
  reset_sync_ingest_readiness_caches()
  sync_timedb_host_itimes.reset_host_itimes_caches()


def _should_stream_stats_file(stats_file: str, stats_file_contents: Any) -> Any:
  """
  Internal helper to check whether we should stream stats file.
  
  Args:
    stats_file (str): String for stats file.
    stats_file_contents (Any): Stats file contents passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _should_stream_stats_file("x", None)  # doctest: +SKIP
  """
  if stats_file_contents is not None:
    return False
  size = stats_file_size_bytes(stats_file)
  max_bytes = cfg.get_sync_ingest_max_file_read_bytes()
  if max_bytes > 0 and size > max_bytes:
    return True
  stream_dup_bytes = cfg.get_sync_ingest_stream_duplicate_scan_bytes()
  if stream_dup_bytes > 0 and size > stream_dup_bytes:
    return True
  return False


def _timestamp_second_present_for_duplicate(
  host: Any,
  unix_second: Any,
  timestamp_utc: Any,
) -> Any:
  """
  Return whether ``unix_second`` for ``host`` is present in DB (indexed exists.
  
    probe).
  
  Args:
    host (Any): Host passed to this helper.
    unix_second (Any): Unix second passed to this helper.
    timestamp_utc (Any): Timestamp utc passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _timestamp_second_present_for_duplicate(None, None, None)
  """
  del timestamp_utc  # kept for call-site stability; wide itimes window not needed here
  return _host_timestamp_second_present_in_db(host, unix_second)


def _try_db_complete_head_tail_fast_path(
  stats_file: str,
  host: Any,
  head_timestamp_utc: Any,
  *,
  lines: Any | None = None,
) -> Any:
  """
  When head and tail seconds are in DB, skip full duplicate scan (returns.
  
    start_idx=-1).
  
  Args:
    stats_file (str): String for stats file.
    host (Any): Host passed to this helper.
    head_timestamp_utc (Any): Head timestamp utc passed to this helper.
    lines (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _try_db_complete_head_tail_fast_path("x", None, None, None)
  """
  # Live listend dual-write can place head+tail while middles were dropped from
  # the live queue — never trust this weak probe when live ingest is enabled.
  if cfg.get_listend_db_ingest_enabled():
    return None
  del head_timestamp_utc  # callers pass head ts for API stability; fast path uses tail only
  if lines is not None:
    tail_t, _tail_jid, tail_host = parse_last_timestamp_line(lines)
  else:
    tail_t, _tail_jid, tail_host = parse_last_timestamp_line_streaming(stats_file)
  if tail_t is None:
    return None
  tail_host = str(tail_host or host).strip()
  tail_unix = int(float(tail_t))
  tail_ts_utc = datetime.fromtimestamp(tail_unix, tz=timezone.utc)
  if not _timestamp_second_present_for_duplicate(tail_host, tail_unix, tail_ts_utc):
    return None
  return -1, True


def _try_db_complete_tail_window_fast_path(
  stats_file: str,
  host: Any,
  timestamp_utc: Any,
  *,
  itimes_set: Any | None = None,
  timestamp_present: Any | None = None,
) -> Any:
  """
  Bounded tail-line probe for large head-present files before full duplicate.
  
    scan.
  
  Args:
    stats_file (str): String for stats file.
    host (Any): Host passed to this helper.
    timestamp_utc (Any): Timestamp utc passed to this helper.
    itimes_set (Any | None): One of ``Any``, ``None``.
    timestamp_present (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _try_db_complete_tail_window_fast_path("x", None, None, None, None)
  """
  if cfg.get_listend_db_ingest_enabled():
    return None
  stream_thresh = cfg.get_sync_ingest_stream_duplicate_scan_bytes()
  if stream_thresh <= 0:
    return None
  if stats_file_size_bytes(stats_file) <= stream_thresh:
    return None
  update_worker_substage("parse:tail_window")
  if itimes_set is None and timestamp_present is None:
    ts_low = timestamp_utc - timedelta(hours=48)
    ts_high = timestamp_utc + timedelta(hours=72)
    itimes_set = _host_recent_timestamps_cached(host, ts_low, ts_high)
    if itimes_set is _HOST_ITIMES_SET_OVERFLOW:
      itimes_set = None
      probe_count = {"n": 0}
      max_overflow_probes = cfg.get_sync_host_itimes_cache_max_timestamps_per_entry()

      def _timestamp_present_with_budget(unix_second: Any) -> Any:
        """
        Internal helper to handle timestamp present with budget.
        
        Args:
          unix_second (Any): Unix second passed to this helper.
        
        Returns:
          Any: Value produced by this call (type depends on inputs).
        
        Raises:
          IngestArchiveLookupBudgetExceededError: Raised when
          ``_timestamp_present_with_budget`` hits a
          ``IngestArchiveLookupBudgetExceededError`` failure path.
        
        Examples:
          >>> _timestamp_present_with_budget(None)  # doctest: +SKIP
        """
        probe_count["n"] += 1
        if probe_count["n"] % 100 == 0:
          _raise_if_ingest_deadline_exceeded()
        if probe_count["n"] > max_overflow_probes:
          raise IngestArchiveLookupBudgetExceededError(
              "itimes overflow DB probe budget exceeded path=%s probes=%d"
              % (stats_file, probe_count["n"]),
          )
        update_worker_substage("itimes_overflow_db")
        return _host_timestamp_second_present_in_db(host, unix_second)

      timestamp_present = _timestamp_present_with_budget
  if (
      tail_window_timestamps_all_present_streaming(
          stats_file,
          itimes_set,
          timestamp_present=timestamp_present,
      )
  ):
    return -1, True
  return None


def _calendar_days_touched_by_paths(paths: Any) -> Any:
  """
  Internal helper to handle calendar days touched by paths.
  
  Args:
    paths (Any): Iterable of filesystem paths as strings.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _calendar_days_touched_by_paths(None)  # doctest: +SKIP
  """
  days = set()
  for path in paths or ():
    day = _calendar_day_hint_from_paths([path])
    if day:
      days.add(day)
  return days


def _completed_ingest_calendar_days(
  *,
  chunk_paths: Any,
  pending_before: Any,
  pending_after: Any,
) -> Any:
  """
  Calendar days with no remaining pending ingest paths after this chunk.
  
  Args:
    chunk_paths (Any): Iterable of filesystem paths as strings.
    pending_before (Any): Pending before passed to this helper.
    pending_after (Any): Pending after passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _completed_ingest_calendar_days(None, None, None)  # doctest: +SKIP
  """
  touched = _calendar_days_touched_by_paths(list(chunk_paths) + list(pending_before))
  if not touched:
    return []
  still_pending = _calendar_days_touched_by_paths(pending_after)
  return sorted(day for day in touched if day not in still_pending)


def _invalidate_jid_caches(stats: Any, proc_stats: Any) -> None:
  """
  Internal helper to handle invalidate job id caches.
  
  Args:
    stats (Any): Stats passed to this helper.
    proc_stats (Any): Proc stats passed to this helper.
  
  Returns:
    None
  
  Examples:
    >>> _invalidate_jid_caches(None, None)  # doctest: +SKIP
  """
  try:
    from hpcperfstats.site.lib.machine.cache_utils import (
        invalidate_jid_derived_cache_keys,
        invalidate_job_plot_cache_keys_for_jids,
    )

    jids = set()
    if stats is not None and not stats.empty and "jid" in stats.columns:
      jids.update(str(x) for x in stats["jid"].dropna().unique())
    if proc_stats is not None and not proc_stats.empty and "jid" in proc_stats.columns:
      jids.update(str(x) for x in proc_stats["jid"].dropna().unique())
    if jids:
      invalidate_jid_derived_cache_keys(jids)
      invalidate_job_plot_cache_keys_for_jids(jids)
  except Exception:
    pass


@contextmanager
def _held_ingest_write_lock(
  write_lock: Any,
  stats_file: str,
  kind: Any,
) -> Iterator[Any]:
  """
  Acquire Manager write shard; always release (including deadline raises).

  Acquire wait suspends per-file SIGALRM and extends the monotonic deadline
  (same class as Redis populate wait). Hold time during ORM/bulk_create is
  charged to the budget and summed into the ``postgres_s`` timing token.

  Args:
    write_lock (Any): Multiprocessing Manager lock (or shard) to acquire.
    stats_file (str): Stats file path for lock-wait logging.
    kind (Any): Batch kind token (``proc`` / ``host``).

  Yields:
    Iterator[Any]: Control while the shard lock is held.

  Examples:
    >>> with _held_ingest_write_lock(type("L", (), {
    ...   "acquire": lambda self: None, "release": lambda self: None,
    ... })(), "/x", "proc"):
    ...   pass
  """
  from hpcperfstats.dbload.lib.sync_timedb_ingest_sigalrm import (
      suspend_ingest_sigalrm_for_non_work_wait,
  )

  wait_t0 = time.monotonic()
  with suspend_ingest_sigalrm_for_non_work_wait():
    write_lock.acquire()
  wait_s = time.monotonic() - wait_t0
  _add_ingest_db_shard_lock_s(wait_s)
  hold_t0 = time.monotonic()
  try:
    _log_db_lock_wait(kind, stats_file, wait_s)
    yield
  finally:
    _add_ingest_postgres_s(time.monotonic() - hold_t0)
    write_lock.release()


def _reset_ingest_db_connection_after_write_error() -> None:
  """
  Best-effort rollback + close so the next ORM call gets a fresh socket.
  
  Required after interrupted ``bulk_create`` (e.g. SIGALRM) before single-row
  fallback; otherwise psycopg raises ``another command is already in progress``.
  
  Returns:
    None
  
  Examples:
    >>> _reset_ingest_db_connection_after_write_error()  # doctest: +SKIP
  """
  try:
    connections[DEFAULT_DB_ALIAS].rollback()
  except Exception:
    pass
  try:
    close_old_connections()
  except Exception:
    pass


def _is_psycopg_connection_desync(exc: Any) -> Any:
  """
  True for wire-protocol errors that warrant connection reset (ingest write.
  
    path).
  
  Args:
    exc (Any): Exception instance being classified or logged.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _is_psycopg_connection_desync(None)  # doctest: +SKIP
  """
  if isinstance(exc, (InterfaceError, OperationalError)):
    return True
  msg = str(exc).lower()
  return (
      "already in progress" in msg
      or "lost synchronization" in msg
  )


def _apply_ingest_session_statement_timeout() -> None:
  """
  Raise this worker's Postgres ``statement_timeout`` to the ingest file budget.

  Default portal ``db_statement_timeout_ms`` (~120s) aborts multi-hour
  ``bulk_create`` on giant files. Ingest workers use the per-file timeout
  ceiling instead (P1-20).

  Returns:
    None

  Examples:
    >>> callable(_apply_ingest_session_statement_timeout)
    True
  """
  try:
    max_s = float(cfg.get_sync_ingest_per_file_timeout_max_s())
  except Exception:
    max_s = 86400.0
  ms = max(0, int(max_s * 1000.0))
  try:
    from django.db import connection
    with connection.cursor() as cursor:
      cursor.execute("SET statement_timeout = %d" % ms)
  except Exception:
    return


def _ingest_ok_from_host_write_path(
  *,
  individual_need_archival: bool | None,
) -> bool:
  """
  Map host-write fallback outcome onto the packed ingest ``ingest_ok`` flag.

  Bulk-create success leaves ``individual_need_archival`` unset (``None``) and
  is ingest-ok. The individual-insert path returns ``need_archival=False``
  when non-integrity errors occurred; that must not report ingest-ok.

  Args:
    individual_need_archival (bool | None): ``None`` when bulk host insert
      succeeded; otherwise the boolean from
      :func:`_insert_host_data_individually`.

  Returns:
    bool: True when host rows were written or skipped as duplicates only.

  Examples:
    >>> _ingest_ok_from_host_write_path(individual_need_archival=None)
    True
    >>> _ingest_ok_from_host_write_path(individual_need_archival=False)
    False
    >>> _ingest_ok_from_host_write_path(individual_need_archival=True)
    True
  """
  if individual_need_archival is None:
    return True
  return bool(individual_need_archival)


def _write_stats_payload_to_db(
  lock: Any,
  stats_file: str,
  stats: Any,
  proc_stats: Any,
  need_archival: bool = True,
) -> Any:
  """
  Persist parsed payload into DB using fixed-size batches and lock sharding.
  
  Args:
    lock (Any): Lock object used to serialize access.
    stats_file (str): String for stats file.
    stats (Any): Stats passed to this helper.
    proc_stats (Any): Proc stats passed to this helper.
    need_archival (bool): Boolean flag for need archival.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Raises:
    Exception: Raised when ``_write_stats_payload_to_db`` hits a ``Exception``
    failure path.
  
  Examples:
    >>> _write_stats_payload_to_db(None, "x", None, None, True)
  """
  update_worker_substage("db_write")
  write_lock = _pick_write_lock_for_path(lock, stats_file)
  individual_need_archival = None
  try:
    try:
      proc_it = proc_stats.itertuples(index=False)
      while True:
        _raise_if_ingest_per_file_deadline_exceeded(stats_file, "db_write_proc")
        batch = list(itertools.islice(proc_it, bulk_create_batch_size()))
        if not batch:
          break
        proc_objs = [
            proc_data(**_proc_data_row_kwargs(row)) for row in batch
        ]
        proc_objs = _peak_merge_proc_objs_with_existing(proc_objs)
        with _held_ingest_write_lock(write_lock, stats_file, "proc"):
          _raise_if_ingest_per_file_deadline_exceeded(stats_file, "db_write_proc")
          proc_data.objects.bulk_create(
              proc_objs,
              update_conflicts=True,
              unique_fields=["jid", "host", "proc"],
              update_fields=_PROC_DATA_UPDATE_FIELDS,
          )
    except Exception as e:
      _reraise_if_ingest_control_flow(e)
      if is_database_unavailable_error(e):
        log_and_raise_database_unavailable(
            e, context="sync_timedb proc_data bulk_create"
        )
      if DEBUG:
        log_print("error in proc_data bulk_create: %s\nFile %s" % (e, stats_file))
      _reset_ingest_db_connection_after_write_error()
      with _held_ingest_write_lock(write_lock, stats_file, "proc"):
        _insert_proc_data_individually(proc_stats)
  except Exception:
    raise
  try:
    try:
      with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=".*[Dd]iscarding nonzero nanoseconds.*",
            category=UserWarning,
        )
        stats_it = stats.itertuples(index=False)
        while True:
          _raise_if_ingest_per_file_deadline_exceeded(stats_file, "db_write_host")
          batch = list(itertools.islice(stats_it, bulk_create_batch_size()))
          if not batch:
            break
          host_objs = [host_data_instance_from_stats_row(row) for row in batch]
          with _held_ingest_write_lock(write_lock, stats_file, "host"):
            _raise_if_ingest_per_file_deadline_exceeded(stats_file, "db_write_host")
            host_data.objects.bulk_create(host_objs, ignore_conflicts=True)
    except Exception as e:
      _reraise_if_ingest_control_flow(e)
      if is_database_unavailable_error(e):
        log_and_raise_database_unavailable(
            e, context="sync_timedb host_data bulk_create"
        )
      if DEBUG:
        log_print("error in host_data bulk_create:", str(e))
      _reset_ingest_db_connection_after_write_error()
      with _held_ingest_write_lock(write_lock, stats_file, "host"):
        need_archival = _insert_host_data_individually(stats)
        individual_need_archival = need_archival
  except Exception:
    raise

  _invalidate_jid_caches(stats, proc_stats)
  if DEBUG:
    log_print("File successfully added to DB")
  return (
      stats_file,
      need_archival,
      _ingest_ok_from_host_write_path(
          individual_need_archival=individual_need_archival,
      ),
  )


def _ingest_remaining_count(
  pending_total: Any,
  chunk_ingest_finished: Any,
) -> Any:
  """
  Internal helper to ingest the remaining count.
  
  Args:
    pending_total (Any): Pending total passed to this helper.
    chunk_ingest_finished (Any): Chunk ingest finished passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _ingest_remaining_count(None, None)  # doctest: +SKIP
  """
  return max(0, int(pending_total) - int(chunk_ingest_finished) - 1)


def _ingest_path_is_supplement(stats_fname: Any, chunk_paths_norm: Any) -> Any:
  """
  Internal helper to ingest the path is supplement.
  
  Args:
    stats_fname (Any): Stats fname passed to this helper.
    chunk_paths_norm (Any): Chunk paths norm passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _ingest_path_is_supplement(None, None)  # doctest: +SKIP
  """
  norm = os.path.normpath(stats_fname) if stats_fname else None
  if not norm:
    return False
  return norm not in chunk_paths_norm


_DB_COMPLETE_REASON_TO_SKIP = {
    "db_complete_head_tail": "head_tail",
    "db_complete_tail_window": "tail_window",
    "db_complete_full_scan": "full_scan",
}

_ARCHIVE_SKIP_FROM_OUTCOME = {
    "quarantine": "quarantine",
    "parse_fail": "parse_fail",
    "active_segment": "active_segment",
    "lookup_budget": "lookup_budget",
    "timeout": "timeout",
}


def _db_skip_token_from_complete_reason(reason: Any) -> Any:
  """
  Internal helper to handle db skip token from complete reason.
  
  Args:
    reason (Any): Reason passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _db_skip_token_from_complete_reason(None)  # doctest: +SKIP
  """
  if not reason:
    return "no"
  return _DB_COMPLETE_REASON_TO_SKIP.get(str(reason), "no")


def _ingest_outcome_meta(**kwargs: Any) -> Any:
  """
  Internal helper to ingest the outcome meta.
  
  Args:
    **kwargs (Any): Extra keyword arguments forwarded to the wrapped API; keys
    and value types match that callee's signature.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _ingest_outcome_meta()  # doctest: +SKIP
  """
  return {key: value for key, value in kwargs.items() if value is not None}


@dataclass
class IngestFileOutcome:
  """
  Packed per-file ingest outcome for logging and mark recording.

  Attributes:
    archive_skip: Archive skip token when archival was skipped.
    db_shard_lock_s: Sum of Manager shard acquire waits (seconds).
    db_skip: DB-complete skip token (``no`` when not skipped).
    elapsed_s: Total wall seconds for the worker task.
    fail_reason: Failure / timeout stage token when present.
    ingest_ok: Whether the path finished ingest-ok.
    need_archival: Whether tar append is still needed.
    outcome: Outcome token (``ingested``, ``timeout``, …).
    parse_elapsed_s: Parse/load stage seconds when known.
    path: Stats file path.
    postgres_s: Sum of shard-hold / ORM write seconds.
    proc_rows: Proc row count when known.
    stats_rows: Host stats row count when known.
    stats_rows_parsed: Parsed stats rows when known.
    timeout_s: Resolved per-file timeout budget (seconds) when known.
  """
  path: str
  elapsed_s: float
  ingest_ok: bool
  need_archival: bool
  outcome: str
  db_skip: str = "no"
  parse_elapsed_s: float | None = None
  db_shard_lock_s: float | None = None
  postgres_s: float | None = None
  timeout_s: float | None = None
  stats_rows: int | None = None
  stats_rows_parsed: int | None = None
  proc_rows: int | None = None
  fail_reason: str | None = None
  archive_skip: str | None = None


def _archive_skip_token_for_outcome(outcome: Any) -> Any:
  """
  Infer archive log token when meta omitted but archival was skipped.
  
  Args:
    outcome (Any): Outcome passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _archive_skip_token_for_outcome(None)  # doctest: +SKIP
  """
  if outcome.need_archival:
    return "yes"
  if outcome.archive_skip:
    return outcome.archive_skip
  mapped = _ARCHIVE_SKIP_FROM_OUTCOME.get(outcome.outcome)
  if mapped:
    return mapped
  if outcome.outcome == "parse_fail" and outcome.fail_reason == "invalid_stats_path":
    return "invalid_stats_path"
  if outcome.ingest_ok and outcome.outcome == "ingested":
    return "db_write_error"
  return "not_needed"


def _need_archival_and_archive_skip_meta(stats_file: str, first_ts: Any) -> Any:
  """
  Tar-append decision for DB-complete ingest; returns meta fragment.
  
  Args:
    stats_file (str): String for stats file.
    first_ts (Any): First ts passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _need_archival_and_archive_skip_meta("x", None)  # doctest: +SKIP
  """
  if not should_archive:
    return False, {"archive_skip": "should_archive_false"}
  need_archival, skip_reason = raw_stats_path_tar_append_decision(
      stats_file,
      tgz_archive_dir,
      first_ts=first_ts,
  )
  meta = {}
  if not need_archival and skip_reason:
    meta["archive_skip"] = skip_reason
  return need_archival, meta


def _pack_ingest_worker_result(
  stats_file: str,
  need_archival: Any,
  ingest_ok: Any,
  elapsed_s: Any,
  outcome_meta: Any | None = None,
) -> Any:
  """
  Internal helper to handle pack ingest worker result.
  
  Args:
    stats_file (str): String for stats file.
    need_archival (Any): Need archival passed to this helper.
    ingest_ok (Any): Ingest ok passed to this helper.
    elapsed_s (Any): Elapsed s passed to this helper.
    outcome_meta (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _pack_ingest_worker_result("x", None, None, None, None)
  """
  meta = dict(outcome_meta or {})
  if meta.get("timeout_s") is None:
    effective = get_ingest_task_effective_timeout_s()
    if effective is not None:
      meta["timeout_s"] = float(effective)
    else:
      try:
        meta["timeout_s"] = float(
            resolve_ingest_per_file_timeout_s(str(stats_file or "")),
        )
      except Exception:
        pass
  return (stats_file, need_archival, ingest_ok, float(elapsed_s), meta)


def _unpack_ingest_worker_result(result: Any) -> Any:
  """
  Internal helper to handle unpack ingest worker result.
  
  Args:
    result (Any): Result passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _unpack_ingest_worker_result(None)  # doctest: +SKIP
  """
  if not isinstance(result, (tuple, list)) or not result:
    return ("", False, False, 0.0, {})
  if len(result) >= 5:
    meta = result[4]
    return (
        result[0],
        result[1],
        result[2],
        float(result[3]),
        dict(meta) if isinstance(meta, dict) else {},
    )
  if len(result) >= 4:
    return result[0], result[1], result[2], float(result[3]), {}
  if len(result) >= 3:
    return result[0], result[1], result[2], 0.0, {}
  return result[0], result[1], True, 0.0, {}


def _unpack_parse_payload_result(result: Any) -> Any:
  """
  Internal helper to handle unpack parse payload result.
  
  Args:
    result (Any): Result passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _unpack_parse_payload_result(None)  # doctest: +SKIP
  """
  if not isinstance(result, (tuple, list)):
    return ("", None, False, False, 0.0, {})
  if len(result) >= 6:
    meta = result[5]
    return (
        result[0],
        result[1],
        result[2],
        result[3],
        float(result[4]),
        dict(meta) if isinstance(meta, dict) else {},
    )
  if len(result) >= 5:
    return result[0], result[1], result[2], result[3], float(result[4]), {}
  return ("", None, False, False, 0.0, {})


def _ingest_file_outcome_from_worker(
  stats_file: str,
  need_archival: Any,
  ingest_ok: Any,
  elapsed_s: Any,
  outcome_meta: Any,
) -> Any:
  """
  Internal helper to ingest the file outcome from worker.
  
  Args:
    stats_file (str): String for stats file.
    need_archival (Any): Need archival passed to this helper.
    ingest_ok (Any): Ingest ok passed to this helper.
    elapsed_s (Any): Elapsed s passed to this helper.
    outcome_meta (Any): Outcome meta passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _ingest_file_outcome_from_worker("x", None, None, None, None)
  """
  meta = dict(outcome_meta or {})
  outcome = str(meta.get("outcome") or "")
  if not outcome:
    if ingest_ok:
      outcome = "ingested" if meta.get("stats_rows") else "db_skip"
    else:
      outcome = "parse_fail"
  db_skip = str(meta.get("db_skip") or "no")
  timeout_s = meta.get("timeout_s")
  if timeout_s is None:
    effective = get_ingest_task_effective_timeout_s()
    if effective is not None:
      timeout_s = float(effective)
  return IngestFileOutcome(
      path=str(stats_file or ""),
      elapsed_s=float(elapsed_s),
      ingest_ok=bool(ingest_ok),
      need_archival=bool(need_archival),
      outcome=outcome,
      db_skip=db_skip,
      parse_elapsed_s=meta.get("parse_elapsed_s"),
      db_shard_lock_s=meta.get("db_shard_lock_s"),
      postgres_s=meta.get("postgres_s"),
      timeout_s=(
          float(timeout_s) if timeout_s is not None else None
      ),
      stats_rows=meta.get("stats_rows"),
      stats_rows_parsed=meta.get("stats_rows_parsed"),
      proc_rows=meta.get("proc_rows"),
      fail_reason=meta.get("fail_reason"),
      archive_skip=meta.get("archive_skip"),
  )


def _resolve_outcome_timeout_s(outcome: Any) -> float:
  """
  Resolve the per-file timeout budget for an outcome log line.

  Prefers ``outcome.timeout_s``, then the task effective timeout, then
  ``resolve_ingest_per_file_timeout_s(path)``.

  Args:
    outcome (Any): ``IngestFileOutcome`` (or duck-typed) instance.

  Returns:
    float: Timeout budget in seconds (``0.0`` when disabled/unknown).

  Examples:
    >>> class _O:
    ...   timeout_s = 3600.0
    ...   path = "/x"
    >>> _resolve_outcome_timeout_s(_O())
    3600.0
  """
  if getattr(outcome, "timeout_s", None) is not None:
    return float(outcome.timeout_s)
  effective = get_ingest_task_effective_timeout_s()
  if effective is not None:
    return float(effective)
  path = str(getattr(outcome, "path", "") or "")
  if path:
    try:
      return float(resolve_ingest_per_file_timeout_s(path))
    except Exception:
      return 0.0
  return 0.0


def _log_ingest_file_outcome(
  outcome: Any,
  *,
  remaining: Any | None = None,
  supplement: bool = False,
) -> None:
  """
  Log one end-of-file ingest outcome line (SOP size/budget/timing).

  Args:
    outcome (Any): ``IngestFileOutcome`` for this path.
    remaining (Any | None): Optional remaining-file counter.
    supplement (bool): When True, append ``supplement=yes``.

  Returns:
    None

  Examples:
    >>> _log_ingest_file_outcome(None, None, True)  # doctest: +SKIP
  """
  timeout_s = _resolve_outcome_timeout_s(outcome)
  parts = [
      "ingest file path=%s" % outcome.path,
      "outcome=%s" % outcome.outcome,
      "elapsed_s=%.1f" % float(outcome.elapsed_s),
      "timeout_s=%.1f" % float(timeout_s),
      "ingest_ok=%s" % ("yes" if outcome.ingest_ok else "no"),
      "archive=%s" % _archive_skip_token_for_outcome(outcome),
      "db_skip=%s" % (outcome.db_skip or "no"),
      "size_bytes=%d" % stats_file_size_bytes(outcome.path),
  ]
  if outcome.parse_elapsed_s is not None:
    parts.append("parse_elapsed_s=%.1f" % float(outcome.parse_elapsed_s))
  if outcome.db_shard_lock_s is not None:
    parts.append("db_shard_lock_s=%.1f" % float(outcome.db_shard_lock_s))
  if outcome.postgres_s is not None:
    parts.append("postgres_s=%.1f" % float(outcome.postgres_s))
  if outcome.stats_rows is not None:
    parts.append("stats_rows=%d" % int(outcome.stats_rows))
  if outcome.stats_rows_parsed is not None:
    parts.append("stats_rows_parsed=%d" % int(outcome.stats_rows_parsed))
  if outcome.proc_rows is not None:
    parts.append("proc_rows=%d" % int(outcome.proc_rows))
  if outcome.fail_reason:
    parts.append("fail_reason=%s" % outcome.fail_reason)
  if remaining is not None:
    parts.append("remaining=%d" % max(0, int(remaining)))
  if supplement:
    parts.append("supplement=yes")
  remaining_pair = _sealed_archive_ingest_remaining_pair()
  if remaining_pair is not None:
    sealed_remaining, sealed_total = remaining_pair
    parts.append("sealed_remaining=%d/%d" % (sealed_remaining, sealed_total))
  log_print(" ".join(parts), flush=True)


def _log_ingest_outcome_from_packed_result(
  result: Any,
  *,
  remaining: Any | None = None,
  supplement: bool = False,
) -> None:
  """
  Log ``ingest file`` outcome from a packed worker result (no mark side effects).

  Args:
    result (Any): Packed ingest tuple from ``add_stats_file_to_db``.
    remaining (Any | None): Optional remaining-file counter.
    supplement (bool): When True, append ``supplement=yes``.

  Returns:
    None

  Examples:
    >>> _log_ingest_outcome_from_packed_result(
    ...   ("", False, True, 0.0, {"outcome": "ingested"}),
    ... )  # doctest: +SKIP
  """
  stats_file, need_archival, ingest_ok, elapsed_s, outcome_meta = (
      _unpack_ingest_worker_result(result)
  )
  outcome = _ingest_file_outcome_from_worker(
      stats_file,
      need_archival,
      ingest_ok,
      elapsed_s,
      outcome_meta,
  )
  _log_ingest_file_outcome(
      outcome,
      remaining=remaining,
      supplement=supplement,
  )


def _record_ingest_marks_from_worker_result(
  result: Any,
  *,
  log_fn: Any = log_print,
) -> None:
  """
  Persist file-complete and zero-host ingest marks from a packed worker result.

  Called from the spawn ingest worker, ``--jid``, and the orchestrator drain
  before ACK so reconstruct complete predicates can skip the path. Pass
  ``log_fn=None`` from the coordinator drain to avoid a second INFO line when
  the worker already logged the mark.

  Args:
    result (Any): Packed ingest tuple from ``add_stats_file_to_db``.
    log_fn (Any): Logger for mark INFO lines; ``None`` suppresses INFO.

  Returns:
    None

  Examples:
    >>> _record_ingest_marks_from_worker_result(
    ...   ("", False, False, 0.0, {}),
    ... ) is None
    True
  """
  stats_file, need_archival, ingest_ok, elapsed_s, outcome_meta = (
      _unpack_ingest_worker_result(result)
  )
  outcome = _ingest_file_outcome_from_worker(
      stats_file,
      need_archival,
      ingest_ok,
      elapsed_s,
      outcome_meta,
  )
  from hpcperfstats.dbload.lib.sync_timedb_zero_host_ingest_mark import (
      maybe_record_zero_host_ingest_mark_from_outcome,
  )
  maybe_record_zero_host_ingest_mark_from_outcome(
      stats_file,
      ingest_ok=outcome.ingest_ok,
      outcome=outcome.outcome,
      stats_rows=outcome.stats_rows,
      stats_rows_parsed=outcome.stats_rows_parsed,
      log_fn=log_fn,
  )
  from hpcperfstats.dbload.lib.sync_timedb_file_complete_ingest_mark import (
      maybe_record_file_complete_ingest_mark_from_outcome,
  )
  maybe_record_file_complete_ingest_mark_from_outcome(
      stats_file,
      ingest_ok=outcome.ingest_ok,
      outcome=outcome.outcome,
      db_skip=outcome.db_skip,
      log_fn=log_fn,
  )


def _log_ingest_worker_result(
  result: Any,
  *,
  remaining: Any | None = None,
  supplement: bool = False,
) -> None:
  """
  Internal helper to log the ingest worker result.
  
  Args:
    result (Any): Result passed to this helper.
    remaining (Any | None): One of ``Any``, ``None``.
    supplement (bool): Boolean flag for supplement.
  
  Returns:
    None
  
  Examples:
    >>> _log_ingest_worker_result(None, None, True)  # doctest: +SKIP
  """
  stats_file, need_archival, ingest_ok, elapsed_s, outcome_meta = (
      _unpack_ingest_worker_result(result)
  )
  outcome = _ingest_file_outcome_from_worker(
      stats_file,
      need_archival,
      ingest_ok,
      elapsed_s,
      outcome_meta,
  )
  _log_ingest_file_outcome(
      outcome,
      remaining=remaining,
      supplement=supplement,
  )
  _record_ingest_marks_from_worker_result(result)


def _quarantine_failed_ingest_parse(
  stats_file: str,
  error_detail: Any | None = None,
) -> Any:
  """
  Move permanently unparseable closed raw into DLO; return True when handled.
  
  Args:
    stats_file (str): String for stats file.
    error_detail (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _quarantine_failed_ingest_parse("x", None)  # doctest: +SKIP
  """
  archive_dir = cfg.get_archive_dir_path()
  if not archive_dir:
    return False
  return quarantine_ingest_failed_raw_path(
      stats_file,
      archive_dir,
      INGEST_PARSE_FAILED_QUARANTINE_REASON,
      error_detail=error_detail,
  )


def _parse_failure_after_quarantine(
  stats_file: str,
  parse_elapsed: Any,
  error_detail: Any | None = None,
) -> Any:
  """
  Quarantine on permanent parse failure; ingest_ok=True when DLO move succeeds.
  
  Args:
    stats_file (str): String for stats file.
    parse_elapsed (Any): Parse elapsed passed to this helper.
    error_detail (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _parse_failure_after_quarantine("x", None, None)  # doctest: +SKIP
  """
  if _quarantine_failed_ingest_parse(stats_file, error_detail=error_detail):
    meta = _ingest_outcome_meta(
        outcome="quarantine",
        fail_reason=error_detail,
        archive_skip="quarantine",
    )
    return (stats_file, None, False, True, parse_elapsed, meta)
  meta = _ingest_outcome_meta(
      outcome="parse_fail",
      fail_reason=error_detail,
      archive_skip="parse_fail",
  )
  return (stats_file, None, False, False, parse_elapsed, meta)


def _reraise_if_ingest_control_flow(exc: Any) -> None:
  """
  Do not swallow timeout / lookup-budget control flow into DLO quarantine.
  
  Bare ``except Exception`` around parse helpers previously converted
  ``IngestPerFileTimeoutError`` into ``outcome=quarantine`` /
  ``reason=ingest_parse_failed``, permanently dead-lettering paths that must
  remain on disk for retry (``ingest_ok=False``, ``outcome=timeout``).
  
  Args:
    exc (Any): Exception instance being classified or logged.
  
  Returns:
    None
  
  Raises:
    exc: Raised when ``_reraise_if_ingest_control_flow`` hits a ``exc``
    failure path.
  
  Examples:
    >>> _reraise_if_ingest_control_flow(None)  # doctest: +SKIP
  """
  if isinstance(
      exc,
      (IngestPerFileTimeoutError, IngestArchiveLookupBudgetExceededError),
  ):
    raise exc
  return


def _parse_stats_file_payload(
  stats_file: str,
  stats_file_contents: Any | None = None,
  *,
  use_ingest_timer: bool = True,
) -> Any:
  """
  Parse stats file into payload for deferred DB writer stage.
  
  Returns (stats_file, payload, need_archival, ingest_ok, parse_elapsed_s).
  
  Args:
    stats_file (str): String for stats file.
    stats_file_contents (Any | None): One of ``Any``, ``None``.
    use_ingest_timer (bool): Whether to enable use ingest timer.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _parse_stats_file_payload("x", None, True)  # doctest: +SKIP
  """
  impl = lambda: _parse_stats_file_payload_impl(
      stats_file, stats_file_contents=stats_file_contents,
  )
  if not use_ingest_timer:
    try:
      return impl()
    except IngestArchiveLookupBudgetExceededError as exc:
      _log_ingest_archive_lookup_budget_exceeded(exc)
      return (
          stats_file,
          None,
          False,
          False,
          0.0,
          _ingest_outcome_meta(outcome="lookup_budget", archive_skip="lookup_budget"),
      )
  try:
    return _run_ingest_timed(
        stats_file,
        "parse",
        impl,
    )
  except IngestPerFileTimeoutError as exc:
    _log_ingest_per_file_timeout(exc)
    return (
        stats_file,
        None,
        False,
        False,
        exc.elapsed_s,
        _ingest_outcome_meta(
            outcome="timeout",
            fail_reason=exc.stage,
            archive_skip="timeout",
        ),
    )
  except IngestArchiveLookupBudgetExceededError as exc:
    _log_ingest_archive_lookup_budget_exceeded(exc)
    return (
        stats_file,
        None,
        False,
        False,
        0.0,
        _ingest_outcome_meta(outcome="lookup_budget", archive_skip="lookup_budget"),
    )


def _duplicate_window_start_index(
  stats_file: str,
  *,
  host: Any,
  timestamp_utc: Any,
  lines: Any | None = None,
) -> Any:
  """
  Return (start_idx, need_archival) for duplicate detection.
  
  Args:
    stats_file (str): String for stats file.
    host (Any): Host passed to this helper.
    timestamp_utc (Any): Timestamp utc passed to this helper.
    lines (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _duplicate_window_start_index("x", None, None, None)  # doctest: +SKIP
  """
  ts_low = timestamp_utc - timedelta(hours=48)
  ts_high = timestamp_utc + timedelta(hours=72)
  itimes_set = _host_recent_timestamps_cached(host, ts_low, ts_high)
  timestamp_present = None
  if itimes_set is _HOST_ITIMES_SET_OVERFLOW:
    itimes_set = None
    overflow_logged = {"done": False}
    probe_count = {"n": 0}
    max_overflow_probes = cfg.get_sync_host_itimes_cache_max_timestamps_per_entry()

    def _timestamp_present_with_budget(unix_second: Any) -> Any:
      """
      Internal helper to handle timestamp present with budget.
      
      Args:
        unix_second (Any): Unix second passed to this helper.
      
      Returns:
        Any: Value produced by this call (type depends on inputs).
      
      Raises:
        IngestArchiveLookupBudgetExceededError: Raised when
        ``_timestamp_present_with_budget`` hits a
        ``IngestArchiveLookupBudgetExceededError`` failure path.
      
      Examples:
        >>> _timestamp_present_with_budget(None)  # doctest: +SKIP
      """
      probe_count["n"] += 1
      if probe_count["n"] == 1 and not overflow_logged["done"]:
        log_print(
            "WARN: duplicate scan itimes_set overflow path=%s host=%s"
            % (stats_file, host),
            flush=True,
        )
        overflow_logged["done"] = True
      if probe_count["n"] % 100 == 0:
        _raise_if_ingest_deadline_exceeded()
      if probe_count["n"] > max_overflow_probes:
        raise IngestArchiveLookupBudgetExceededError(
            "itimes overflow DB probe budget exceeded path=%s probes=%d"
            % (stats_file, probe_count["n"]),
        )
      update_worker_substage("itimes_overflow_db")
      return _host_timestamp_second_present_in_db(host, unix_second)

    timestamp_present = _timestamp_present_with_budget
  if lines is not None:
    return find_processing_start_index(
        lines,
        itimes_set,
        timestamp_present=timestamp_present,
    )
  return find_processing_start_index_streaming(
      stats_file,
      itimes_set,
      timestamp_present=timestamp_present,
  )


def _resolve_streaming_ingest_start(
  stats_file: str,
  parse_elapsed_fn: Any,
) -> Any:
  """
  Duplicate scan for streaming-eligible segments.
  
  Returns ``(True, early_return)`` when parse can be skipped (including failures
  encoded as a 5-tuple), or ``(False, (start_line_idx, need_archival))`` when
  parsing should proceed.
  
  Args:
    stats_file (str): String for stats file.
    parse_elapsed_fn (Any): Callable invoked by this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _resolve_streaming_ingest_start("x", None)  # doctest: +SKIP
  """
  t, _jid, host = parse_first_timestamp_line_streaming(stats_file)
  if t is None:
    return (
        True,
        _parse_failure_after_quarantine(
            stats_file, parse_elapsed_fn(), error_detail="initial_timestamp_not_found",
        ),
    )
  if not host:
    return (
        True,
        _parse_failure_after_quarantine(
            stats_file, parse_elapsed_fn(), error_detail="initial_host_not_found",
        ),
    )
  host = str(host).strip()
  timestamp_utc = datetime.fromtimestamp(int(float(t)), tz=timezone.utc)
  head_present = head_timestamp_present_in_db(host, timestamp_utc)
  if not head_present:
    return False, (0, True)
  db_complete_reason = None
  fast = _try_db_complete_head_tail_fast_path(stats_file, host, timestamp_utc)
  if fast is not None:
    start_idx, need_archival = fast
    db_complete_reason = "db_complete_head_tail"
  else:
    tail_fast = _try_db_complete_tail_window_fast_path(
        stats_file, host, timestamp_utc,
    )
    if tail_fast is not None:
      start_idx, need_archival = tail_fast
      db_complete_reason = "db_complete_tail_window"
    else:
      start_idx, need_archival = _duplicate_window_start_index(
          stats_file,
          host=host,
          timestamp_utc=timestamp_utc,
      )
      if start_idx == -1:
        db_complete_reason = "db_complete_full_scan"
  if start_idx == -1:
    need_archival, archive_skip_meta = _need_archival_and_archive_skip_meta(
        stats_file,
        t,
    )
    parse_elapsed = parse_elapsed_fn()
    meta = _ingest_outcome_meta(
        outcome="db_skip",
        db_skip=_db_skip_token_from_complete_reason(
            db_complete_reason or "db_complete_full_scan",
        ),
        parse_elapsed_s=parse_elapsed,
        **archive_skip_meta,
    )
    return True, (stats_file, None, need_archival, True, parse_elapsed, meta)
  return False, (int(start_idx), need_archival)


def _ingest_reconcile_skip_result(stats_file: str) -> Any:
  """
  Return an ingest result tuple when DB idempotency says re-dispatch is.
  
    unnecessary.
  
  Args:
    stats_file (str): String for stats file.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _ingest_reconcile_skip_result("x")  # doctest: +SKIP
  """
  t0 = time.time()
  done, result = _resolve_streaming_ingest_start(
      stats_file,
      lambda: time.time() - t0,
  )
  if not done:
    return None
  (
      stats_file_local,
      _payload,
      need_archival,
      ingest_ok,
      _parse_elapsed,
      outcome_meta,
  ) = _unpack_parse_payload_result(result)
  meta = dict(outcome_meta or {})
  # Supervisor-side skip: no worker ran; do not retire a pool PID.
  meta["reconcile_skip"] = "yes"
  return _pack_ingest_worker_result(
      stats_file_local,
      need_archival,
      ingest_ok,
      time.time() - t0,
      meta,
  )


def _ingest_unhealed_recover_soft_fail_result(stats_file: str) -> Any:
  """
  Soft-fail one path after identical skip_no pending survives recover thrash.
  
  Keeps the raw file on disk (``ingest_ok=False``, no quarantine) so a later
  chunk/operator pass can retry; continues the imap session for peer paths.
  
  Args:
    stats_file (str): String for stats file.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _ingest_unhealed_recover_soft_fail_result("x")  # doctest: +SKIP
  """
  meta = _ingest_outcome_meta(
      outcome="soft_fail",
      fail_reason="idle_pool_unhealed_after_recover",
      reconcile_skip="yes",
  )
  return _pack_ingest_worker_result(
      stats_file,
      False,
      False,
      0.0,
      meta,
  )


def _parse_stats_file_payload_impl_streaming(stats_file: str) -> Any:
  """
  Bounded-memory parse path for segments larger than.
  
    ``sync_ingest_max_file_read_bytes``.
  
  Args:
    stats_file (str): String for stats file.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _parse_stats_file_payload_impl_streaming("x")  # doctest: +SKIP
  """
  parse_t0 = time.time()

  def _parse_elapsed() -> Any:
    """
    Internal helper to parse the elapsed.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _parse_elapsed()  # doctest: +SKIP
    """
    return time.time() - parse_t0

  with _sync_worker_db_task():
    try:
      done, result = _resolve_streaming_ingest_start(stats_file, _parse_elapsed)
      if done:
        return result
      start_line_idx, need_archival = result
      try:
        update_worker_substage("parse:accumulate")
        stats_list, proc_stats_list = parse_stats_file_streaming(
            stats_file,
            start_line_idx=start_line_idx,
            parse_start_idx=0,
            exclude_types_list=exclude_types,
        )
      except Exception as e:
        _reraise_if_ingest_control_flow(e)
        return _parse_failure_after_quarantine(
            stats_file, _parse_elapsed(), error_detail=str(e),
        )
      update_worker_substage("parse:dataframes")
      stats, proc_stats = build_stats_dataframes(stats_list, proc_stats_list)
      del stats_list
      del proc_stats_list
      if stats.empty and proc_stats.empty:
        if DEBUG:
          log_print("Unable to process stats file %s" % stats_file)
        return _parse_failure_after_quarantine(
            stats_file, _parse_elapsed(), error_detail="empty stats and proc_stats",
        )
      update_worker_substage("parse:deltas_arc")
      stats = compute_deltas_and_arc(stats)
      parse_elapsed = _parse_elapsed()
      meta = _ingest_outcome_meta(
          outcome="ingested",
          parse_elapsed_s=parse_elapsed,
          stats_rows=len(stats),
          proc_rows=len(proc_stats),
      )
      return (stats_file, (stats, proc_stats), need_archival, True, parse_elapsed, meta)
    except FileNotFoundError:
      load_err = "stats_file_disappeared"
      return _parse_failure_after_quarantine(
          stats_file, _parse_elapsed(), error_detail=load_err,
      )


def _add_stats_file_to_db_streaming_incremental(
  lock: Any,
  stats_file: str,
  t0: Any,
) -> Any:
  """
  Parse → DB → parse loop for large segments (combined ingest only).
  
  Args:
    lock (Any): Lock object used to serialize access.
    stats_file (str): String for stats file.
    t0 (Any): T0 passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _add_stats_file_to_db_streaming_incremental(None, "x", None)
  """
  parse_t0 = time.time()
  carry = DeltaCarryState()
  total_stats_rows = 0
  total_stats_rows_parsed = 0
  total_proc_rows = 0
  need_archival = True
  ingest_ok = True
  flush_rows = bulk_create_batch_size()

  def _parse_elapsed() -> Any:
    """
    Internal helper to parse the elapsed.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _parse_elapsed()  # doctest: +SKIP
    """
    return time.time() - parse_t0

  def _on_chunk(stats_list: Any, proc_stats_list: Any) -> None:
    """
    Internal helper to handle on chunk.
    
    Args:
      stats_list (Any): Stats list passed to this helper.
      proc_stats_list (Any): Proc stats list passed to this helper.
    
    Returns:
      None
    
    Examples:
      >>> _on_chunk(None, None)  # doctest: +SKIP
    """
    nonlocal need_archival, ingest_ok, total_stats_rows
    nonlocal total_stats_rows_parsed, total_proc_rows
    parsed_stats_n = len(stats_list) if stats_list else 0
    update_worker_substage("parse:dataframes")
    stats_chunk, proc_chunk = build_stats_dataframes(stats_list, proc_stats_list)
    del stats_list
    del proc_stats_list
    if stats_chunk.empty and proc_chunk.empty:
      del stats_chunk
      del proc_chunk
      return
    update_worker_substage("parse:deltas_arc")
    stats_chunk = compute_deltas_and_arc_chunk(stats_chunk, carry=carry)
    chunk_stats_rows = len(stats_chunk)
    chunk_proc_rows = len(proc_chunk)
    if parsed_stats_n > 0 and chunk_stats_rows == 0:
      log_print(
          "WARN: sync_timedb: nonempty stats frame collapsed to empty "
          "delta/arc path=%s parsed_rows=%d"
          % (stats_file, parsed_stats_n),
          flush=True,
      )
    stats_file_local, need_archival, chunk_ok = _write_stats_payload_to_db(
        lock,
        stats_file,
        stats_chunk,
        proc_chunk,
        need_archival=need_archival,
    )
    del stats_chunk
    del proc_chunk
    if not chunk_ok:
      ingest_ok = False
    else:
      total_stats_rows += chunk_stats_rows
      total_stats_rows_parsed += parsed_stats_n
      total_proc_rows += chunk_proc_rows
    _release_ingest_worker_heap()

  with _sync_worker_db_task():
    try:
      done, result = _resolve_streaming_ingest_start(stats_file, _parse_elapsed)
      if done:
        (
            _stats_file,
            _payload,
            need_archival,
            early_ok,
            _early_parse_elapsed,
            outcome_meta,
        ) = _unpack_parse_payload_result(result)
        elapsed_total = time.time() - t0
        if not early_ok:
          return _pack_ingest_worker_result(
              _stats_file, need_archival, False, elapsed_total, outcome_meta,
          )
        if _payload is None:
          return _pack_ingest_worker_result(
              _stats_file, need_archival, True, elapsed_total, outcome_meta,
          )
      else:
        start_line_idx, need_archival = result
        try:
          update_worker_substage("parse:accumulate")
          # Feed the prefix (parse_start_idx) so schema registers; do not
          # physically skip lines (RC-0).
          parse_stats_file_streaming_incremental(
              stats_file,
              start_line_idx=0,
              parse_start_idx=start_line_idx,
              flush_rows=flush_rows,
              on_chunk=_on_chunk,
              exclude_types_list=exclude_types,
          )
        except Exception as e:
          _reraise_if_ingest_control_flow(e)
          failure = _parse_failure_after_quarantine(
              stats_file, _parse_elapsed(), error_detail=str(e),
          )
          (
              _stats_file,
              _payload,
              _need,
              early_ok,
              _,
              outcome_meta,
          ) = _unpack_parse_payload_result(failure)
          return _pack_ingest_worker_result(
              _stats_file, _need, early_ok, time.time() - t0, outcome_meta,
          )
        if (
            total_stats_rows_parsed == 0
            and total_stats_rows == 0
            and total_proc_rows == 0
        ):
          if DEBUG:
            log_print("Unable to process stats file %s" % stats_file)
          failure = _parse_failure_after_quarantine(
              stats_file, _parse_elapsed(), error_detail="empty stats and proc_stats",
          )
          (
              _stats_file,
              _payload,
              _need,
              early_ok,
              _,
              outcome_meta,
          ) = _unpack_parse_payload_result(failure)
          return _pack_ingest_worker_result(
              _stats_file, _need, early_ok, time.time() - t0, outcome_meta,
          )
      elapsed = time.time() - t0
      meta = _ingest_outcome_meta(
          outcome="ingested",
          parse_elapsed_s=_parse_elapsed(),
          stats_rows=total_stats_rows,
          stats_rows_parsed=total_stats_rows_parsed,
          proc_rows=total_proc_rows,
      )
      return _pack_ingest_worker_result(
          stats_file,
          need_archival,
          ingest_ok,
          elapsed,
          _merge_ingest_write_timing_into_meta(meta),
      )
    except FileNotFoundError:
      failure = _parse_failure_after_quarantine(
          stats_file, _parse_elapsed(), error_detail="stats_file_disappeared",
      )
      (
          _stats_file,
          _payload,
          _need,
          early_ok,
          _,
          outcome_meta,
      ) = _unpack_parse_payload_result(failure)
      return _pack_ingest_worker_result(
          _stats_file, _need, early_ok, time.time() - t0, outcome_meta,
      )


def _parse_stats_file_payload_impl(
  stats_file: str,
  stats_file_contents: Any | None = None,
) -> Any:
  """
  Implementation for :func:`_parse_stats_file_payload` (parse stage only).
  
  Args:
    stats_file (str): String for stats file.
    stats_file_contents (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _parse_stats_file_payload_impl("x", None)  # doctest: +SKIP
  """
  lines = None
  parse_t0 = time.time()

  def _parse_elapsed() -> Any:
    """
    Internal helper to parse the elapsed.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _parse_elapsed()  # doctest: +SKIP
    """
    return time.time() - parse_t0

  with _sync_worker_db_task():
    try:
      from hpcperfstats.dbload.lib.sync_timedb_stats_find import (
          is_internal_archive_stats_path,
      )

      if is_internal_archive_stats_path(stats_file):
        return (
            stats_file,
            None,
            False,
            False,
            _parse_elapsed(),
            _ingest_outcome_meta(
                outcome="skip",
                fail_reason="internal_archive_sidecar",
                archive_skip="internal_archive_sidecar",
            ),
        )
      hostname, _ = parse_stats_file_path(stats_file)
      if hostname is None:
        return (
            stats_file,
            None,
            False,
            False,
            _parse_elapsed(),
            _ingest_outcome_meta(
                outcome="parse_fail",
                fail_reason="invalid_stats_path",
                archive_skip="invalid_stats_path",
            ),
        )
      if stats_file_is_active_segment(stats_file):
        if DEBUG:
          log_print("Skipping active segment (still linked to current): %s" % stats_file)
        return (
            stats_file,
            None,
            False,
            False,
            _parse_elapsed(),
            _ingest_outcome_meta(
                outcome="active_segment",
                archive_skip="active_segment",
            ),
        )
      if _should_stream_stats_file(stats_file, stats_file_contents):
        return _parse_stats_file_payload_impl_streaming(stats_file)
      lines, load_err = load_stats_file_lines(stats_file, stats_file_contents)
      if load_err is not None:
        return _parse_failure_after_quarantine(
            stats_file, _parse_elapsed(), error_detail=load_err,
        )
      t, _jid, host = parse_first_timestamp_line(lines)
      if t is None:
        return _parse_failure_after_quarantine(
            stats_file,
            _parse_elapsed(),
            error_detail="initial_timestamp_not_found",
        )
      if not host:
        return _parse_failure_after_quarantine(
            stats_file,
            _parse_elapsed(),
            error_detail="initial_host_not_found",
        )
      host = str(host).strip()
      timestamp_utc = datetime.fromtimestamp(int(float(t)), tz=timezone.utc)
      head_present = head_timestamp_present_in_db(host, timestamp_utc)
      if not head_present:
        # New file head is not in DB yet; it still needs archival post-ingest.
        start_idx, need_archival = 0, True
        db_complete_reason = None
      else:
        db_complete_reason = None
        fast = _try_db_complete_head_tail_fast_path(
            stats_file, host, timestamp_utc, lines=lines,
        )
        if fast is not None:
          start_idx, need_archival = fast
          db_complete_reason = "db_complete_head_tail"
        else:
          tail_fast = _try_db_complete_tail_window_fast_path(
              stats_file, host, timestamp_utc,
          )
          if tail_fast is not None:
            start_idx, need_archival = tail_fast
            db_complete_reason = "db_complete_tail_window"
          else:
            start_idx, need_archival = _duplicate_window_start_index(
                stats_file,
                host=host,
                timestamp_utc=timestamp_utc,
                lines=lines,
            )
            if start_idx == -1:
              db_complete_reason = "db_complete_full_scan"
      if start_idx == -1:
        need_archival, archive_skip_meta = _need_archival_and_archive_skip_meta(
            stats_file,
            t,
        )
        parse_elapsed = _parse_elapsed()
        meta = _ingest_outcome_meta(
            outcome="db_skip",
            db_skip=_db_skip_token_from_complete_reason(
                db_complete_reason or "db_complete_full_scan",
            ),
            parse_elapsed_s=parse_elapsed,
            **archive_skip_meta,
        )
        return (stats_file, None, need_archival, True, parse_elapsed, meta)
      lines = lines[start_idx:]
      try:
        update_worker_substage("parse:accumulate")
        stats_list, proc_stats_list = parse_stats_lines(
            lines,
            0,
            eventmaps_by_type=EVENTMAPS_BY_TYPE,
            exclude_types_list=exclude_types,
        )
      except Exception as e:
        _reraise_if_ingest_control_flow(e)
        return _parse_failure_after_quarantine(
            stats_file, _parse_elapsed(), error_detail=str(e),
        )
      update_worker_substage("parse:dataframes")
      stats, proc_stats = build_stats_dataframes(stats_list, proc_stats_list)
      del stats_list
      del proc_stats_list
      if stats.empty and proc_stats.empty:
        if DEBUG:
          log_print("Unable to process stats file %s" % stats_file)
        return _parse_failure_after_quarantine(
            stats_file, _parse_elapsed(), error_detail="empty stats and proc_stats",
        )
      update_worker_substage("parse:deltas_arc")
      stats = compute_deltas_and_arc(stats)
      parse_elapsed = _parse_elapsed()
      meta = _ingest_outcome_meta(
          outcome="ingested",
          parse_elapsed_s=parse_elapsed,
          stats_rows=len(stats),
          proc_rows=len(proc_stats),
      )
      return (stats_file, (stats, proc_stats), need_archival, True, parse_elapsed, meta)
    finally:
      if lines is not None:
        del lines


def _db_writer_worker(lock: Any, db_task: Any) -> Any:
  """
  Worker entrypoint for DB-writer pool.
  
  db_task is (stats_file, payload, need_archival, parse_elapsed_s).
  Returns (stats_file, need_archival, ingest_ok, total_elapsed_s).
  
  Args:
    lock (Any): Lock object used to serialize access.
    db_task (Any): Db task passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _db_writer_worker(None, None)  # doctest: +SKIP
  """
  stats_file = db_task[0] if db_task else ""
  try:
    return _run_ingest_timed(
        stats_file,
        "write",
        lambda: _db_writer_worker_impl(lock, db_task),
    )
  except IngestPerFileTimeoutError as exc:
    _log_ingest_per_file_timeout(exc)
    need_archival = db_task[2] if db_task and len(db_task) >= 3 else False
    return (stats_file, need_archival, False, exc.elapsed_s)


def _db_writer_worker_impl(lock: Any, db_task: Any) -> Any:
  """
  Implementation for :func:`_db_writer_worker` (DB write stage only).
  
  Args:
    lock (Any): Lock object used to serialize access.
    db_task (Any): Db task passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _db_writer_worker_impl(None, None)  # doctest: +SKIP
  """
  stats = None
  proc_stats = None
  with _sync_worker_db_task():
    try:
      stats_file, payload, need_archival, parse_elapsed_s = db_task
      if payload is None:
        return (stats_file, need_archival, True, float(parse_elapsed_s))
      stats, proc_stats = payload
      write_t0 = time.time()
      stats_file, need_archival, ingest_ok = _write_stats_payload_to_db(
          lock, stats_file, stats, proc_stats, need_archival
      )
      write_elapsed = time.time() - write_t0
      return (
          stats_file,
          need_archival,
          ingest_ok,
          float(parse_elapsed_s) + write_elapsed,
      )
    finally:
      if stats is not None:
        del stats
      if proc_stats is not None:
        del proc_stats


def _ingest_parse_and_write_file(
  lock: Any,
  stats_file: str,
  stats_file_contents: Any | None = None,
) -> Any:
  """
  Combined ingest worker: parse and DB write in one pool task (small parent.
  
    tuple).
  
  Args:
    lock (Any): Lock object used to serialize access.
    stats_file (str): String for stats file.
    stats_file_contents (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _ingest_parse_and_write_file(None, "x", None)  # doctest: +SKIP
  """
  return add_stats_file_to_db(
      lock, stats_file, stats_file_contents=stats_file_contents
  )


# This routine will read the file until a timestamp is read that is not in the database. It then reads in the rest of the file.
def add_stats_file_to_db(
  lock: Any,
  stats_file: str,
  stats_file_contents: Any | None = None,
) -> Any:
  """
  Parse a stats file, map hardware counters, compute deltas/arc, and bulk-.
  
    insert.
  
    into host_data and proc_data.
  
  Returns (stats_file, need_archival, ingest_ok, elapsed_s) where elapsed_s is
    wall
  seconds for the attempted ingest path. Uses lock for DB writes.
  
  Args:
    lock (Any): Lock object used to serialize access.
    stats_file (str): String for stats file.
    stats_file_contents (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> add_stats_file_to_db(None, "x", None)  # doctest: +SKIP
  """
  result = None
  record_worker_stage(stats_file, "ingest", substage="worker_entry")
  _apply_ingest_session_statement_timeout()
  try:
    try:
      result = _run_ingest_timed(
          stats_file,
          "ingest",
          lambda: _add_stats_file_to_db_impl(
              lock, stats_file, stats_file_contents=stats_file_contents
          ),
      )
    except IngestPerFileTimeoutError as exc:
      _log_ingest_per_file_timeout(exc)
      result = _pack_ingest_worker_result(
          stats_file,
          False,
          False,
          exc.elapsed_s,
          _merge_ingest_write_timing_into_meta(
              _ingest_outcome_meta(
                  outcome="timeout",
                  fail_reason=exc.stage,
                  archive_skip="timeout",
              ),
          ),
      )
    except IngestArchiveLookupBudgetExceededError as exc:
      _log_ingest_archive_lookup_budget_exceeded(exc)
      result = _pack_ingest_worker_result(
          stats_file,
          False,
          False,
          0.0,
          _merge_ingest_write_timing_into_meta(
              _ingest_outcome_meta(
                  outcome="lookup_budget",
                  archive_skip="lookup_budget",
              ),
          ),
      )
  finally:
    mem_meta = _release_ingest_worker_memory(stats_file)
    if result is not None:
      result = _merge_worker_memory_meta(result, mem_meta)
  return result


def _add_stats_file_to_db_impl(
  lock: Any,
  stats_file: str,
  stats_file_contents: Any | None = None,
) -> Any:
  """
  Implementation for :func:`add_stats_file_to_db` (parse + write combined).
  
  Args:
    lock (Any): Lock object used to serialize access.
    stats_file (str): String for stats file.
    stats_file_contents (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Raises:
    Exception: Raised when ``_add_stats_file_to_db_impl`` hits a ``Exception``
    failure path.
  
  Examples:
    >>> _add_stats_file_to_db_impl(None, "x", None)  # doctest: +SKIP
  """
  stats = None
  proc_stats = None
  payload = None
  t0 = time.time()
  _reset_ingest_write_timing()
  if _should_stream_stats_file(stats_file, stats_file_contents):
    return _add_stats_file_to_db_streaming_incremental(
        lock, stats_file, t0,
    )
  with _sync_worker_db_task():
    try:
      parse_result = _parse_stats_file_payload(
          stats_file,
          stats_file_contents=stats_file_contents,
          use_ingest_timer=False,
      )
      (
          stats_file,
          payload,
          need_archival,
          ingest_ok,
          parse_elapsed,
          outcome_meta,
      ) = _unpack_parse_payload_result(parse_result)
      elapsed_total = time.time() - t0
      if not ingest_ok:
        return _pack_ingest_worker_result(
            stats_file, need_archival, False, elapsed_total, outcome_meta,
        )
      if payload is None:
        return _pack_ingest_worker_result(
            stats_file, need_archival, True, elapsed_total, outcome_meta,
        )
      stats, proc_stats = payload
      stats_rows = len(stats)
      proc_rows = len(proc_stats)
      stats_file, need_archival, ingest_ok = _write_stats_payload_to_db(
          lock, stats_file, stats, proc_stats, need_archival=need_archival
      )
      elapsed_total = time.time() - t0
      meta = dict(outcome_meta)
      meta.update(
          outcome="ingested",
          parse_elapsed_s=parse_elapsed,
          stats_rows=stats_rows,
          proc_rows=proc_rows,
      )
      return _pack_ingest_worker_result(
          stats_file,
          need_archival,
          ingest_ok,
          elapsed_total,
          _merge_ingest_write_timing_into_meta(meta),
      )
    except (OperationalError, DatabaseError) as exc:
      if is_database_unavailable_error(exc):
        log_and_raise_database_unavailable(
            exc, context="sync_timedb add_stats_file_to_db"
        )
      raise
    finally:
      if stats is not None:
        del stats
      if proc_stats is not None:
        del proc_stats
      if payload is not None:
        del payload


def _load_sync_checkpoint(state_path: str) -> Any:
  """
  Load checkpoint entries from persistence envelope, returning [] on invalid.
  
  Args:
    state_path (str): String for state path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _load_sync_checkpoint("x")  # doctest: +SKIP
  """
  raw = load_persistence_document(state_path, "ingest_checkpoint", default=[])
  if not isinstance(raw, list):
    return []
  entries = []
  for item in raw:
    if not isinstance(item, dict):
      continue
    path = item.get("path")
    size = item.get("size")
    mtime = item.get("mtime")
    if not isinstance(path, str):
      continue
    try:
      size = int(size)
      mtime = int(mtime)
    except (TypeError, ValueError):
      continue
    entries.append({"path": path, "size": size, "mtime": mtime})
  return entries


def _save_sync_checkpoint(state_path: str, completed_entries: Any) -> None:
  """
  Atomically save checkpoint entries via persistence API.
  
  Args:
    state_path (str): String for state path.
    completed_entries (Any): Completed entries passed to this helper.
  
  Returns:
    None
  
  Examples:
    >>> _save_sync_checkpoint("x", None)  # doctest: +SKIP
  """
  save_persistence_document(
      state_path,
      "ingest_checkpoint",
      list(completed_entries),
  )


def _load_json_list(path: str) -> Any:
  """
  Load JSON list content; return None when invalid/unreadable.
  
  Args:
    path (str): String for path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _load_json_list("x")  # doctest: +SKIP
  """
  try:
    with open(path, "r", encoding="utf-8") as fh:
      raw = json.load(fh)
  except (OSError, ValueError, TypeError):
    return None
  if not isinstance(raw, list):
    return None
  return raw


def _save_json_atomic(path: str, payload: Any) -> None:
  """
  Atomically persist JSON payload to path with parent mkdir.
  
  Args:
    path (str): String for path.
    payload (Any): Value to inspect (typically a numeric scalar).
  
  Returns:
    None
  
  Examples:
    >>> _save_json_atomic("x", None)  # doctest: +SKIP
  """
  parent = os.path.dirname(str(path))
  if parent:
    os.makedirs(parent, exist_ok=True)
  tmp_path = "%s.tmp" % path
  with open(tmp_path, "w", encoding="utf-8") as fh:
    json.dump(payload, fh)
  os.replace(tmp_path, path)


def _path_fingerprint(path: str) -> Any:
  """
  Return path fingerprint used for restart-safe processed tracking.
  
  Args:
    path (str): String for path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _path_fingerprint("x")  # doctest: +SKIP
  """
  try:
    return {
        "path": path,
        "size": int(os.path.getsize(path)),
        "mtime": int(os.path.getmtime(path)),
    }
  except OSError:
    return None


def _add_processed_path(
  path: str,
  processed_files: Any,
  processed_files_order: Any,
  checkpoint_entries: Any,
  checkpoint_path: str,
  *,
  file_states: Any | None = None,
) -> Any:
  """
  Record processed path in memory and checkpoint buffer.
  
  Args:
    path (str): String for path.
    processed_files (Any): Iterable of filesystem paths as strings.
    processed_files_order (Any): Processed files order passed to this helper.
    checkpoint_entries (Any): Checkpoint entries passed to this helper.
    checkpoint_path (str): String for checkpoint path.
    file_states (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _add_processed_path("x", None, None, None, "x", None)
  """
  fp = _path_fingerprint(path)
  if fp is None:
    return False
  processed_files.add(path)
  processed_files_order.append(path)
  checkpoint_entries.append(fp)
  while len(processed_files_order) > processed_files_max_size:
    old_path = processed_files_order.popleft()
    processed_files.discard(old_path)
    if file_states is not None:
      file_states.pop(old_path, None)
  while len(checkpoint_entries) > processed_files_max_size:
    checkpoint_entries.popleft()
  return True


def _remove_processed_path(
  path: str,
  processed_files: Any,
  processed_files_order: Any,
  checkpoint_entries: Any,
  checkpoint_path: str,
  *,
  file_states: Any | None = None,
  host_scan_hints: Any | None = None,
  persist: bool = True,
) -> Any:
  """
  Undo checkpoint/processed markers so a path re-enters the ingest loop.
  
  Args:
    path (str): String for path.
    processed_files (Any): Iterable of filesystem paths as strings.
    processed_files_order (Any): Processed files order passed to this helper.
    checkpoint_entries (Any): Checkpoint entries passed to this helper.
    checkpoint_path (str): String for checkpoint path.
    file_states (Any | None): One of ``Any``, ``None``.
    host_scan_hints (Any | None): One of ``Any``, ``None``.
    persist (bool): Boolean flag for persist.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _remove_processed_path("x", None, None, None, "x", None, None, True)
  """
  path = str(path)
  processed_files.discard(path)
  try:
    processed_files_order.remove(path)
  except ValueError:
    pass
  fp = _path_fingerprint(path)
  snapshot = checkpoint_entries_snapshot(checkpoint_entries)
  if fp is not None:
    kept = deque()
    for entry in snapshot:
      if (
          entry.get("path") == path
          and entry.get("size") == fp["size"]
          and entry.get("mtime") == fp["mtime"]
      ):
        continue
      kept.append(entry)
    checkpoint_entries.clear()
    checkpoint_entries.extend(kept)
  else:
    kept = deque(
        entry for entry in snapshot if entry.get("path") != path
    )
    checkpoint_entries.clear()
    checkpoint_entries.extend(kept)
  if file_states is not None:
    file_states[path] = SyncFileState.DISCOVERED
  if isinstance(host_scan_hints, dict):
    host_scan_hints.pop(path, None)
  if persist and checkpoint_path:
    _save_sync_checkpoint(checkpoint_path, checkpoint_entries)
  return True


def _batch_remove_processed_paths(
  paths: Any,
  processed_files: Any,
  processed_files_order: Any,
  checkpoint_entries: Any,
  checkpoint_path: str,
  *,
  file_states: Any | None = None,
  host_scan_hints: Any | None = None,
  persist: bool = True,
) -> Any:
  """
  Single-pass checkpoint clear for a path set (handoff hot path).
  
  Args:
    paths (Any): Iterable of filesystem paths as strings.
    processed_files (Any): Iterable of filesystem paths as strings.
    processed_files_order (Any): Processed files order passed to this helper.
    checkpoint_entries (Any): Checkpoint entries passed to this helper.
    checkpoint_path (str): String for checkpoint path.
    file_states (Any | None): One of ``Any``, ``None``.
    host_scan_hints (Any | None): One of ``Any``, ``None``.
    persist (bool): Boolean flag for persist.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _batch_remove_processed_paths(0)  # doctest: +SKIP
  """
  path_set = set()
  paths_with_fp = {}
  paths_without_fp = set()
  for raw in paths or ():
    if not raw:
      continue
    path = str(raw)
    if not os.path.isfile(path):
      continue
    path_set.add(path)
    fp = _path_fingerprint(path)
    if fp is not None:
      paths_with_fp[path] = fp
    else:
      paths_without_fp.add(path)

  if not path_set:
    return 0

  for path in path_set:
    processed_files.discard(path)
    try:
      processed_files_order.remove(path)
    except ValueError:
      pass
    if file_states is not None:
      file_states[path] = SyncFileState.DISCOVERED
    if isinstance(host_scan_hints, dict):
      host_scan_hints.pop(path, None)

  kept = deque()
  for entry in checkpoint_entries_snapshot(checkpoint_entries):
    path = entry.get("path")
    if path in paths_without_fp:
      continue
    if path in paths_with_fp:
      fp = paths_with_fp[path]
      if (
          entry.get("size") == fp["size"]
          and entry.get("mtime") == fp["mtime"]
      ):
        continue
    kept.append(entry)
  checkpoint_entries.clear()
  checkpoint_entries.extend(kept)

  if persist and checkpoint_path:
    _save_sync_checkpoint(checkpoint_path, checkpoint_entries)
  return len(path_set)


def _proc_field_or_none(row: Any, name: Any) -> Any:
  """
  Return a scalar proc_data field from an itertuples row, mapping NaN to None.
  
  Args:
    row (Any): Value to inspect (typically a numeric scalar).
    name (Any): Name passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _proc_field_or_none(None, None)  # doctest: +SKIP
  """
  val = getattr(row, name, None)
  if val is None:
    return None
  try:
    # pandas may emit float('nan') for missing numeric cells.
    if val != val:  # noqa: PLR0124 — NaN check without importing math/pandas
      return None
  except Exception:
    pass
  return val


_PROC_DATA_UPDATE_FIELDS = ("device",) + HOST_PROC_KEYS


def _peak_merge_proc_objs_with_existing(proc_objs: list) -> list:
  """
  Raise ``vm_stk`` / ``vm_exe`` / ``vm_lib`` from existing ``proc_data`` rows.

  Incoming objs keep last-write for other KEYS; peak fields use GREATEST with
  any matching DB row so lower later samples cannot drop stored highs.

  Args:
    proc_objs (list): ``proc_data`` instances about to be upserted.

  Returns:
    list: Same list (objs mutated in place when a DB peer exists).

  Examples:
    >>> _peak_merge_proc_objs_with_existing([])
    []
  """
  if not proc_objs:
    return proc_objs
  from django.db.models import Q

  q = Q()
  for obj in proc_objs:
    q |= Q(jid=obj.jid, host=obj.host, proc=obj.proc)
  existing = {
      (row.jid, row.host, row.proc): row
      for row in proc_data.objects.filter(q).only(
          "jid", "host", "proc", *HOST_PROC_PEAK_KEYS
      )
  }
  if not existing:
    return proc_objs
  for obj in proc_objs:
    prior = existing.get((obj.jid, obj.host, obj.proc))
    if prior is not None:
      apply_proc_peak_attrs_from_earlier(prior, obj)
  return proc_objs


def _proc_data_row_kwargs(row: Any) -> Any:
  """
  Build kwargs for proc_data create/update from a parsed DataFrame row.
  
  Args:
    row (Any): Value to inspect (typically a numeric scalar).
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _proc_data_row_kwargs(None)  # doctest: +SKIP
  """
  kwargs = {
      "jid": row.jid,
      "host": row.host,
      "proc": row.proc,
      "device": _proc_field_or_none(row, "device"),
  }
  for key in HOST_PROC_KEYS:
    kwargs[key] = _proc_field_or_none(row, key)
  return kwargs


def _insert_proc_data_individually(proc_stats_df: Any) -> None:
  """
  Fallback: upsert proc_data rows one by one (update on unique conflict).
  
  Args:
    proc_stats_df (Any): Proc stats df passed to this helper.
  
  Returns:
    None
  
  Examples:
    >>> _insert_proc_data_individually(None)  # doctest: +SKIP
  """
  def _save_proc_row(row: Any) -> None:
    """
    Internal helper to save the proc row.
    
    Args:
      row (Any): Value to inspect (typically a numeric scalar).
    
    Returns:
      None
    
    Examples:
      >>> _save_proc_row(None)  # doctest: +SKIP
    """
    kwargs = _proc_data_row_kwargs(row)
    try:
      prior = proc_data.objects.only(
          "jid", "host", "proc", *HOST_PROC_PEAK_KEYS
      ).get(jid=kwargs["jid"], host=kwargs["host"], proc=kwargs["proc"])
    except proc_data.DoesNotExist:
      prior = None
    if prior is not None:
      peak_holder = types.SimpleNamespace(
          **{k: kwargs.get(k) for k in HOST_PROC_PEAK_KEYS}
      )
      apply_proc_peak_attrs_from_earlier(prior, peak_holder)
      for key in HOST_PROC_PEAK_KEYS:
        kwargs[key] = getattr(peak_holder, key)
    defaults = {k: kwargs[k] for k in _PROC_DATA_UPDATE_FIELDS}
    proc_data.objects.update_or_create(
        jid=kwargs["jid"],
        host=kwargs["host"],
        proc=kwargs["proc"],
        defaults=defaults,
    )

  unique_violations = _insert_rows_individually(
      rows=proc_stats_df.itertuples(index=False),
      save_row=_save_proc_row,
      error_prefix="error in single proc_data insert:",
  )
  if DEBUG:
    log_print("Existing Rows Found in DB: %s" % unique_violations)


def _insert_host_data_individually(stats_df: Any) -> Any:
  """
  Fallback: insert host_data rows one by one, skipping duplicates. Returns.
  
    need_archival.
  
  Args:
    stats_df (Any): Stats df passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _insert_host_data_individually(None)  # doctest: +SKIP
  """
  need_archival = True
  unique_violations = 0
  with warnings.catch_warnings():
    warnings.filterwarnings(
        "ignore",
        message=".*[Dd]iscarding nonzero nanoseconds.*",
        category=UserWarning,
    )
    def _save_host_row(row: Any) -> None:
      """
      Internal helper to save the host row.
      
      Args:
        row (Any): Value to inspect (typically a numeric scalar).
      
      Returns:
        None
      
      Examples:
        >>> _save_host_row(None)  # doctest: +SKIP
      """
      host_data_instance_from_stats_row(row).save(force_insert=True)

    unique_violations, non_integrity_errors = _insert_rows_individually(
        rows=stats_df.itertuples(index=False),
        save_row=_save_host_row,
        error_prefix="error in single host_data insert:",
        return_non_integrity_errors=True,
    )
    if non_integrity_errors > 0:
      need_archival = False
  if DEBUG:
    log_print("Existing Rows Found in DB: %s" % unique_violations)
  return need_archival


def _insert_rows_individually(
  *,
  rows: Any,
  save_row: Any,
  error_prefix: Any,
  return_non_integrity_errors: bool = False,
) -> Any:
  """
  Insert rows one-by-one and count duplicate violations.
  
  On psycopg connection desync (``another command is already in progress``),
  reset once, retry the current row, then abort the loop so remaining rows do
  not spam identical errors on a dead connection.
  
  Args:
    rows (Any): Rows passed to this helper.
    save_row (Any): Save row passed to this helper.
    error_prefix (Any): Error prefix passed to this helper.
    return_non_integrity_errors (bool): Boolean flag for return non integrity
    errors.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _insert_rows_individually(None, None, None, True)  # doctest: +SKIP
  """
  unique_violations = 0
  non_integrity_errors = 0
  desync_reset_used = False
  for row in rows:
    try:
      save_row(row)
      continue
    except IntegrityError:
      unique_violations += 1
      continue
    except Exception as e:
      if (not desync_reset_used) and _is_psycopg_connection_desync(e):
        desync_reset_used = True
        _reset_ingest_db_connection_after_write_error()
        try:
          save_row(row)
          continue
        except IntegrityError:
          unique_violations += 1
          continue
        except Exception as e2:
          non_integrity_errors += 1
          log_print(error_prefix, str(e2), "row:", row)
          break
      non_integrity_errors += 1
      log_print(error_prefix, str(e), "row:", row)
      if _is_psycopg_connection_desync(e):
        break
  if return_non_integrity_errors:
    return unique_violations, non_integrity_errors
  return unique_violations




def _decompress_compressed_archive(archive_compressed_path: str) -> Any:
  """
  Decompress ``.tar.zst`` or legacy ``.tar.gz`` to sibling ``.tar``.
  
  Returns True when a verified sibling ``.tar`` exists afterward.
  
  Args:
    archive_compressed_path (str): String for archive compressed path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _decompress_compressed_archive("x")  # doctest: +SKIP
  """
  if not archive_compressed_path or not os.path.isfile(archive_compressed_path):
    zst_path, gz_path = compressed_sibling_paths(
        daily_tar_path_from_compressed(archive_compressed_path or ""),
    )
    if os.path.isfile(zst_path):
      archive_compressed_path = zst_path
    elif os.path.isfile(gz_path):
      archive_compressed_path = gz_path
    else:
      return False
  tar_path = daily_tar_path_from_compressed(archive_compressed_path)
  fmt = detect_compressed_format(archive_compressed_path)
  if fmt not in ("zst", "gz"):
    return os.path.isfile(tar_path)
  return ensure_daily_tar_restored_for_append(
      tar_path, cfg.get_archive_zstd_threads())


def _restore_daily_tar_or_log_failure(
  archive_tar_fname: Any,
  *,
  context: Any,
) -> Any:
  """
  Internal helper to handle restore daily tar or log failure.
  
  Args:
    archive_tar_fname (Any): Archive tar fname passed to this helper.
    context (Any): Context passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _restore_daily_tar_or_log_failure(None, None)  # doctest: +SKIP
  """
  if ensure_daily_tar_restored_for_append(
      archive_tar_fname, cfg.get_archive_zstd_threads()):
    return True
  log_print(
      "ERROR: could not restore daily tar %s; leaving raw stats files in place: %s"
      % (context, archive_tar_fname),
      flush=True,
  )
  return False


def format_tar_append_failure_log(
  tar_path: str,
  exc: Any,
  *,
  retry: bool = False,
) -> Any:
  """
  Build ERROR line for tar append failure; fold CalledProcessError.stderr.
  
  Args:
    tar_path (str): String for tar path.
    exc (Any): Exception instance being classified or logged.
    retry (bool): Boolean flag for retry.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> format_tar_append_failure_log("x", None, True)  # doctest: +SKIP
  """
  prefix = "ERROR: retry tar append failed" if retry else "ERROR: tar append failed"
  stderr = getattr(exc, "stderr", None)
  if isinstance(stderr, bytes):
    stderr = stderr.decode("utf-8", errors="replace")
  stderr_text = (stderr or "").strip()
  marker = ""
  if "out of off_t range" in stderr_text:
    marker = " marker=off_t_range"
  elif stderr_text and "tar:" in stderr_text.lower():
    marker = " marker=tar_warning_or_error"
  if stderr_text:
    return (
        "%s for %s (%s)%s; tar append stderr: %s; leaving raw stats files in place"
        % (prefix, tar_path, exc, marker, stderr_text)
    )
  return "%s for %s (%s)%s; leaving raw stats files in place" % (
      prefix,
      tar_path,
      exc,
      marker,
  )


def _append_to_tar(tar_path: str, file_paths: Any) -> None:
  """
  Append file_paths to tar at tar_path. Does nothing if file_paths is empty.
  
  Uses GNU/BSD ``tar -r -f`` with ``-C /``, ``--null -T`` and relative member
  paths so argv stays tiny and absolute ``-T`` path warnings are avoided.
  Always passes ``--posix`` (pax) so members larger than 8 GiB - 1 succeed on
  pax-capable archives. Skips paths that disappeared before append (race).
  Batches via ``sync_timedb_tar_append_batch_size`` (default 1024).
  
  Args:
    tar_path (str): String for tar path.
    file_paths (Any): Iterable of filesystem paths as strings.
  
  Returns:
    None
  
  Raises:
    RuntimeError: Raised when ``_append_to_tar`` hits a ``RuntimeError``
    failure path.
    subprocess.CalledProcessError: Raised when ``_append_to_tar`` hits a
    ``subprocess.CalledProcessError`` failure path.
  
  Examples:
    >>> _append_to_tar("x", None)  # doctest: +SKIP
  """
  if not file_paths:
    return
  # Amortize subprocess overhead for large archive bursts.
  batch = max(1, int(cfg.get_sync_timedb_tar_append_batch_size()))
  if len(file_paths) >= 512:
    batch = max(batch, 256)
  elif len(file_paths) >= 128:
    batch = max(batch, 128)
  tar_timeout_s = 3600.0
  for off in range(0, len(file_paths), batch):
    chunk = file_paths[off : off + batch]
    fd, list_path = tempfile.mkstemp(prefix="hps_tar_append_", suffix=".lst")
    result = None
    try:
      tar_bin = shutil.which("tar") or "/bin/tar"
      with file_write_lock(tar_path):
        # F13: re-filter under the write lock so concurrent delete/day_close
        # cannot race lexists → tar -r with a stale path list.
        present = [p for p in chunk if os.path.lexists(p)]
        n_missing = len(chunk) - len(present)
        if n_missing:
          log_print(
              "Archive append: skipped %d missing path(s) in batch %d-%d"
              % (n_missing, off + 1, off + len(chunk)),
              flush=True,
          )
        if not present:
          continue
        with os.fdopen(fd, "wb") as lf:
          fd = -1  # ownership transferred
          for p in present:
            # Member path relative to ``-C /`` (no leading slash in -T file).
            abs_p = os.path.abspath(p)
            rel = abs_p[1:] if abs_p.startswith(os.sep) else abs_p
            lf.write(os.fsencode(rel) + b"\0")
        zst_path, gz_path = compressed_sibling_paths(tar_path)
        if not os.path.exists(tar_path) and (
            os.path.isfile(zst_path) or os.path.isfile(gz_path)
        ):
          raise RuntimeError(
              "refusing to create daily tar while sealed archive exists "
              "without restored sibling: %s" % tar_path,
          )
        tar_args = [
            tar_bin,
            "-r",
            "--posix",
            "-C",
            "/",
            "-f",
            tar_path,
            "--null",
            "-T",
            list_path,
        ]
        result = subprocess.run(
            tar_args,
            capture_output=True,
            text=True,
            check=False,
            timeout=tar_timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
      raise RuntimeError(
          "tar append timed out after %ss: %s" % (tar_timeout_s, tar_path),
      ) from exc
    finally:
      if fd >= 0:
        try:
          os.close(fd)
        except OSError:
          pass
      try:
        os.remove(list_path)
      except OSError:
        pass
    if result is None:
      continue
    if result.stdout:
      log_print(result.stdout, flush=True)
    if result.stderr:
      log_print(result.stderr, flush=True)
    if result.returncode != 0:
      raise subprocess.CalledProcessError(
          result.returncode,
          result.args,
          output=result.stdout,
          stderr=result.stderr,
      )
    if DEBUG:
      log_print(
          "Archived batch %d-%d (%d file(s)) -> %s"
          % (off + 1, off + len(present), len(present), tar_path),
          flush=True,
      )


def archive_stats_files(archive_info: Any) -> Any:
  """
  Append stats files to a daily ``.tar`` (verify, recover, dedupe).
  
  zstd sealing and removal of raw stats run on the day_close queue workers
  (seal → raw removal → tar-drop), not after each append.
  
  Args:
    archive_info (Any): Archive info passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> archive_stats_files(None)  # doctest: +SKIP
  """
  archive_path = archive_info[0] if archive_info else ""
  record_worker_stage(archive_path, "archive_append")
  result = None
  try:
    with _sync_worker_db_task():
      result = _archive_stats_files_body(archive_info)
  finally:
    _release_ingest_worker_memory(archive_path)
  return result


def _lookup_existing_members_for_archive_append(
  archive_fname: Any,
  archive_tar_fname: Any,
) -> Any:
  """
  Return (members, members_source) for archive append.

  When a sibling mutable ``.tar`` exists, membership comes from an open-tar
  scan (authoritative for append). Redis/sealed fast paths apply only when the
  mutable ``.tar`` is absent (sealed-only days).

  Args:
    archive_fname (Any): Archive fname passed to this helper.
    archive_tar_fname (Any): Archive tar fname passed to this helper.

  Returns:
    Any: ``(member_map, members_source)`` where ``members_source`` is one of
    ``redis``, ``sealed_stream``, or ``tar_scan``.

  Examples:
    >>> _lookup_existing_members_for_archive_append(None, None)
  """
  canonical = normalize_daily_compressed_path(archive_fname)
  tar_exists = os.path.exists(archive_tar_fname)
  if tar_exists:
    from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
        get_mutable_tar_authority_member_map,
    )
    return (
        get_mutable_tar_authority_member_map(archive_tar_fname),
        "tar_scan",
    )
  if cfg.get_sync_archive_members_redis_enabled():
    from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      _daily_archive_members_cache_key,
    )
    from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      archive_members_redis_enabled,
      build_archive_members_redis_keys,
      redis_members_cache_is_fully_warm,
    )
    if archive_members_redis_enabled():
      keys = build_archive_members_redis_keys(
          _daily_archive_members_cache_key(canonical),
      )
      was_warm = redis_members_cache_is_fully_warm(keys)
      from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
          archive_pre_append_member_lookup_context,
      )
      with archive_pre_append_member_lookup_context():
        members = get_existing_archive_members_for_daily_archive(canonical)
      members_source = "redis" if was_warm else "sealed_stream"
      return members, members_source
  # Redis off + sealed-only day: local sealed scan (no mutable tar).
  members = get_existing_archive_members_for_daily_archive(canonical)
  return members, ("sealed_stream" if members else "tar_scan")


def _log_archive_job_begin(archive_tar_fname: Any, members_source: Any) -> int:
  """
  Capture tar size for archive_job_done; DEBUG-only begin line.

  Args:
    archive_tar_fname (Any): Archive tar fname passed to this helper.
    members_source (Any): Members source passed to this helper.

  Returns:
    int: On-disk tar size in bytes (0 when missing).

  Examples:
    >>> _log_archive_job_begin(None, None)  # doctest: +SKIP
  """
  day_token = calendar_date_from_daily_tar_path(archive_tar_fname) or "?"
  tar_bytes = os.path.getsize(archive_tar_fname) if os.path.isfile(archive_tar_fname) else 0
  if DEBUG:
    log_print(
        "DEBUG: archive_job_begin day=%s tar_bytes=%s members_source=%s"
        % (day_token, tar_bytes, members_source),
        flush=True,
    )
  return int(tar_bytes)


def _archive_stats_files_body(archive_info: Any) -> Any:
  """
  Internal helper to archive the stats files body.
  
  Args:
    archive_info (Any): Archive info passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _archive_stats_files_body(None)  # doctest: +SKIP
  """
  archive_fname, stats_files = archive_info
  archive_tar_fname = daily_tar_path_from_compressed(archive_fname)
  day_token = calendar_date_from_daily_tar_path(archive_tar_fname) or "?"
  job_start = time.monotonic()
  job_begin_logged = False
  job_outcome = "fail"
  members_source = "tar_scan"
  tar_bytes = 0
  mapped_n = 0
  to_add_n = 0
  appended_n = 0
  append_inflight_set = False
  skipped_oversized = ()

  def _ensure_job_begin_logged(source: Any) -> None:
    """
    Internal helper to ensure the job begin logged.

    Args:
      source (Any): Source passed to this helper.

    Returns:
      None

    Examples:
      >>> _ensure_job_begin_logged(None)  # doctest: +SKIP
    """
    nonlocal job_begin_logged, members_source, tar_bytes
    if not job_begin_logged:
      members_source = source
      tar_bytes = _log_archive_job_begin(archive_tar_fname, source)
      job_begin_logged = True

  try:
    stats_files, gate_skipped = filter_paths_head_ingested(
        stats_files, log_fn=log_print,
    )
    if not stats_files:
      job_outcome = "gate_skip"
      return ArchiveAppendOutcome(
          ok=False,
          gate_skipped=True,
          skipped_paths=tuple(gate_skipped or ()),
          skip_finalize_invalidate=True,
      )
    from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
        daily_tar_restore_in_progress_for_day,
        set_archive_append_inflight,
    )
    if (
        day_token
        and day_token != "?"
        and daily_tar_restore_in_progress_for_day(day_token)
    ):
      job_outcome = "soft_skip"
      log_print(
          "INFO: archive_job soft_skip day=%s reason=daily_tar_restore"
          % day_token,
          flush=True,
      )
      return ArchiveAppendOutcome(
          ok=False,
          soft_requeue=True,
          skip_finalize_invalidate=True,
      )
    set_archive_append_inflight(day_token, reason="archive_job")
    append_inflight_set = True
    existing_members = {}
    zst_path, gz_path = compressed_sibling_paths(archive_tar_fname)
    sealed_exists = (
        os.path.isfile(zst_path)
        or os.path.isfile(gz_path)
        or (
            os.path.isfile(archive_fname)
            and bool(detect_compressed_format(archive_fname))
        )
    )

    # Membership before restore: Redis/sealed can answer to_add without
    # materializing a multi-hundred-GB mutable .tar (noop sealed days).
    redis_warm_members = None
    if os.path.exists(archive_tar_fname) and cfg.get_sync_archive_members_redis_enabled():
      from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
          _daily_archive_members_cache_key,
          maybe_invalidate_open_tar_redis_divergence_for_append_batch,
      )
      from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
          archive_members_redis_enabled,
          archive_pre_append_member_lookup_context,
          build_archive_members_redis_keys,
          redis_members_cache_is_fully_warm,
      )
      if archive_members_redis_enabled():
        canonical = normalize_daily_compressed_path(archive_fname)
        keys = build_archive_members_redis_keys(
            _daily_archive_members_cache_key(canonical),
        )
        if redis_members_cache_is_fully_warm(keys):
          with archive_pre_append_member_lookup_context():
            redis_warm_members = get_existing_archive_members_for_daily_archive(
                canonical,
            )
    if sealed_exists or os.path.exists(archive_tar_fname):
      existing_members, members_source = _lookup_existing_members_for_archive_append(
          archive_fname, archive_tar_fname,
      )
      _ensure_job_begin_logged(members_source)
      if redis_warm_members is not None:
        maybe_invalidate_open_tar_redis_divergence_for_append_batch(
            archive_fname,
            stats_files,
            redis_warm_members,
            existing_members,
        )

    mapped_n = len(stats_files)
    stats_files_to_tar = filter_files_to_add_to_archive(
        stats_files, existing_members, debug=DEBUG)
    to_add_n = len(stats_files_to_tar)
    appended_n = 0
    if not stats_files_to_tar:
      job_outcome = "ok"
      if DEBUG:
        log_print(
            "DEBUG: archive_job_duty day=%s mapped=%d to_add=%d appended=%d"
            % (day_token, mapped_n, to_add_n, appended_n),
            flush=True,
        )
      return ArchiveAppendOutcome(
          skip_finalize_invalidate=True,
          skipped_paths=skipped_oversized,
      )

    # Restore / decompress only when append will mutate the daily tar.
    if not os.path.exists(archive_tar_fname):
      if os.path.isfile(zst_path):
        if not _decompress_compressed_archive(zst_path):
          log_print(
              "ERROR: could not restore daily tar from sealed zst before append; "
              "leaving raw stats files in place: %s" % zst_path,
              flush=True,
          )
          return False
      elif os.path.isfile(gz_path):
        if not _decompress_compressed_archive(gz_path):
          log_print(
              "ERROR: could not restore daily tar from sealed gzip before append; "
              "leaving raw stats files in place: %s" % gz_path,
              flush=True,
          )
          return False
      elif os.path.isfile(archive_fname) and detect_compressed_format(archive_fname):
        if not _decompress_compressed_archive(archive_fname):
          log_print(
              "ERROR: could not restore daily tar from sealed archive before append; "
              "leaving raw stats files in place: %s" % archive_fname,
              flush=True,
          )
          return False
    if not os.path.exists(archive_tar_fname) and sealed_exists:
      log_print(
          "ERROR: sealed archive present but daily tar missing after decompress; "
          "leaving raw stats files in place: %s" % archive_tar_fname,
          flush=True,
      )
      return False

    # Corrupt/truncated .tar can make Python's tarfile reader return {} while GNU
    # tar still refuses append (exit 2). Recover before append so we never raise
    # without trying restore-from-.gz (same as post-append path).
    tar_unreadable = False
    if os.path.isfile(archive_tar_fname):
      try:
        tar_unreadable = not verify_tar_archive_readable(archive_tar_fname)
      except TimeoutError:
        log_print(
            "WARNING: fnctl read lock timeout verifying tar before append; "
            "deferring append for %s"
            % archive_tar_fname,
            flush=True,
        )
        return False
    if tar_unreadable:
      log_print(
          "Daily tar unreadable before append; attempting in-place repair then "
          "sealed restore: %s" % archive_tar_fname,
          flush=True,
      )
      repaired = repair_truncated_daily_tar_in_place(
          archive_tar_fname,
          log_fn=log_print,
          tgz_archive_dir=os.path.dirname(archive_tar_fname),
          yield_phase="append_tar_repair",
      )
      if repaired and verify_tar_archive_readable(archive_tar_fname):
        tar_unreadable = False
      if tar_unreadable and not replace_corrupt_tar_from_compressed_backup(
          archive_tar_fname, zst_path, gz_path, cfg.get_archive_zstd_threads(),
      ):
        log_print(
            "ERROR: could not restore daily tar before append; leaving raw stats "
            "files in place: %s" % archive_fname,
            flush=True,
        )
        return False
      if tar_unreadable and os.path.exists(archive_tar_fname):
        existing_members, members_source = _lookup_existing_members_for_archive_append(
            archive_fname, archive_tar_fname,
        )
        _ensure_job_begin_logged(members_source)
        stats_files_to_tar = filter_files_to_add_to_archive(
            stats_files, existing_members, debug=DEBUG)
        to_add_n = len(stats_files_to_tar)
        if not stats_files_to_tar:
          job_outcome = "ok"
          if DEBUG:
            log_print(
                "DEBUG: archive_job_duty day=%s mapped=%d to_add=%d appended=%d"
                % (day_token, mapped_n, to_add_n, appended_n),
                flush=True,
            )
          return ArchiveAppendOutcome(
              skip_finalize_invalidate=True,
              skipped_paths=skipped_oversized,
          )
      else:
        existing_members = {}

    if stats_files_to_tar:
      if not _restore_daily_tar_or_log_failure(
          archive_tar_fname, context="before append"):
        return False
      _ensure_job_begin_logged(members_source)
      before_convert_mtime = (
          os.path.getmtime(archive_tar_fname)
          if os.path.isfile(archive_tar_fname)
          else None
      )
      stats_files_to_tar, skipped_list = prepare_paths_for_giant_member_append(
          archive_tar_fname,
          stats_files_to_tar,
          log_fn=log_print,
      )
      skipped_oversized = tuple(skipped_list)
      to_add_n = len(stats_files_to_tar)
      if (
          before_convert_mtime is not None
          and os.path.isfile(archive_tar_fname)
          and os.path.getmtime(archive_tar_fname) != before_convert_mtime
      ):
        invalidate_after_daily_tar_mutation(
            archive_fname,
            reason="pax_convert",
            log_fn=log_print,
        )
    try:
      _append_to_tar(archive_tar_fname, stats_files_to_tar)
    except (subprocess.CalledProcessError, RuntimeError) as exc:
      log_print(
          format_tar_append_failure_log(archive_tar_fname, exc, retry=False),
          flush=True,
      )
      return False

    if stats_files_to_tar:
      if not verify_tar_archive_readable(archive_tar_fname):
        log_print(
            "Daily tar failed integrity check after append; recovering from "
            "sealed archive or clearing for rebuild: %s" % archive_tar_fname,
            flush=True,
        )
        if not replace_corrupt_tar_from_compressed_backup(
            archive_tar_fname, zst_path, gz_path, cfg.get_archive_zstd_threads(),
        ):
          log_print(
              "ERROR: could not restore daily tar from %s; leaving raw stats "
              "files in place" % archive_fname,
              flush=True,
          )
          return False
        if os.path.exists(archive_tar_fname):
          existing_after, members_source = _lookup_existing_members_for_archive_append(
              archive_fname, archive_tar_fname,
          )
          _ensure_job_begin_logged(members_source)
        else:
          existing_after = {}
        to_retry = filter_files_to_add_to_archive(
            stats_files_to_tar, existing_after, debug=DEBUG)
        if to_retry:
          if not _restore_daily_tar_or_log_failure(
              archive_tar_fname, context="before retry append"):
            return False
          try:
            _append_to_tar(archive_tar_fname, to_retry)
          except (subprocess.CalledProcessError, RuntimeError) as exc:
            log_print(
                format_tar_append_failure_log(
                    archive_tar_fname, exc, retry=True),
                flush=True,
            )
            return False
          stats_files_to_tar = to_retry
        if not verify_tar_archive_readable(archive_tar_fname):
          log_print(
              "ERROR: daily tar still unreadable after recovery append; leaving "
              "raw stats files in place: %s" % archive_tar_fname,
              flush=True,
          )
          return False
    if stats_files_to_tar:
      from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
          _daily_archive_members_cache_key,
      )
      from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
          merge_appended_members_into_redis,
      )

      canonical = normalize_daily_compressed_path(archive_fname)
      cache_key = _daily_archive_members_cache_key(canonical)
      member_map = build_tar_append_member_map(stats_files_to_tar)
      appended_n = len(member_map)
      saw_dupes = len(member_map) < len(stats_files_to_tar)
      merged = False
      worker_invalidated = False
      try:
        merged = merge_appended_members_into_redis(
            cache_key,
            member_map,
            saw_duplicates=saw_dupes,
        )
      except Exception as exc:
        log_print(
            "WARNING: tar_append redis merge failed for %s: %s; invalidating"
            % (canonical, exc),
            flush=True,
        )
      if merged:
        merge_daily_archive_members_l1_cache(canonical, member_map)
        day_date = calendar_date_from_daily_tar_path(archive_tar_fname)
        if DEBUG:
          log_print(
              "DEBUG: tar_append redis merge day=%s members=%d"
              % (
                  day_date.isoformat() if day_date is not None else canonical,
                  len(member_map),
              ),
              flush=True,
          )
      else:
        invalidate_after_daily_tar_mutation(
            archive_fname,
            reason="tar_append",
            log_fn=log_print,
        )
        worker_invalidated = True
      from hpcperfstats.dbload.lib.sync_timedb_zero_host_ingest_mark import (
          clear_zero_host_ingest_marks,
      )
      clear_zero_host_ingest_marks(stats_files_to_tar, log_fn=log_print)
      job_outcome = "ok"
      if DEBUG:
        log_print(
            "DEBUG: archive_job_duty day=%s mapped=%d to_add=%d appended=%d"
            % (day_token, mapped_n, to_add_n, appended_n),
            flush=True,
        )
      return ArchiveAppendOutcome(
          redis_merge_ok=merged,
          skip_finalize_invalidate=merged or worker_invalidated,
          skipped_paths=skipped_oversized,
      )
    job_outcome = "ok"
    if DEBUG:
      log_print(
          "DEBUG: archive_job_duty day=%s mapped=%d to_add=%d appended=%d"
          % (day_token, mapped_n, to_add_n, appended_n),
          flush=True,
      )
    return ArchiveAppendOutcome(
        skip_finalize_invalidate=True,
        skipped_paths=skipped_oversized,
    )
  finally:
    if append_inflight_set:
      from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
          clear_archive_append_inflight,
      )
      clear_archive_append_inflight(day_token)
    if job_begin_logged:
      log_print(
          "INFO: archive_job_done day=%s elapsed_s=%.3f outcome=%s "
          "tar_bytes=%s members_source=%s mapped=%d to_add=%d appended=%d"
          % (
              day_token,
              time.monotonic() - job_start,
              job_outcome,
              tar_bytes,
              members_source,
              mapped_n,
              to_add_n,
              appended_n,
          ),
          flush=True,
      )


def _build_fallback_archive_mapping_by_mtime(
  files_to_be_archived: Any,
  tgz_dir: str,
) -> Any:
  """
  Best-effort fallback mapping when timestamp-head parsing fails.
  
  Buckets by local file mtime date so archival can still progress and raw files
  are not stranded indefinitely.
  
  Args:
    files_to_be_archived (Any): Files to be archived passed to this helper.
    tgz_dir (str): String for tgz dir.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _build_fallback_archive_mapping_by_mtime(None, "x")  # doctest: +SKIP
  """
  fallback = defaultdict(list)
  for path in files_to_be_archived:
    try:
      file_date = datetime.fromtimestamp(os.path.getmtime(path))
    except OSError:
      continue
    archive_fname = daily_compressed_path_for_date(tgz_dir, file_date)
    fallback[archive_fname].append(path)
  return dict(fallback)


def _normalize_archive_groups_by_tgz(archive_mapping: Any) -> Any:
  """
  Return stable per-tgz archive tasks as ``[(tgz_path, [paths...]), ...]``.
  
  The archival pipeline is intentionally threaded by tgz group (one task per
  ``YYYY-MM-DD.tar.zst`` path) so each worker handles a complete archive group
  rather than interleaving members across unrelated tgz files.
  
  Args:
    archive_mapping (Any): Archive mapping passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _normalize_archive_groups_by_tgz(None)  # doctest: +SKIP
  """
  if not archive_mapping:
    return []
  tasks = []
  for tgz_path in sorted(archive_mapping):
    tasks.append((tgz_path, list(archive_mapping[tgz_path])))
  return tasks

def database_startup() -> None:
  """
  Print DB version, database size, and optionally chunk compression stats for.
  
    host_data.
  
  Returns:
    None
  
  Raises:
    Exception: Raised when ``database_startup`` hits a ``Exception`` failure
    path.
  
  Examples:
    >>> database_startup()  # doctest: +SKIP
  """
  from django.db import connection
  try:
    with connection.cursor() as cur:
      # Single round-trip for version + size
      cur.execute(
          "SELECT version(), pg_size_pretty(pg_database_size(%s));",
          [cfg.get_db_name()],
      )
      row = cur.fetchone()
      if row:
        if DEBUG:
          log_print("Postgresql server version:", row[0])
        log_print("Database Size:", row[1])
      if DEBUG:
        try:
          cur.execute(
              "SELECT chunk_name,before_compression_total_bytes/(1024*1024*1024),after_compression_total_bytes/(1024*1024*1024) FROM chunk_compression_stats('host_data');"
          )
          for x in cur.fetchall():
            try:
              log_print("{0} Size: {1:8.1f} {2:8.1f}".format(*x))
            except Exception:
              pass
        except Exception:
          pass
      else:
        log_print("Reading Chunk Data")
  except (OperationalError, DatabaseError) as exc:
    if is_database_unavailable_error(exc):
      log_and_raise_database_unavailable(
          exc, context="sync_timedb database_startup"
      )
    raise


def parse_sync_timedb_argv(argv: Any) -> Any:
  """
  Parse CLI argv into ``(run_once, startdate, enddate)`` (same rules as.
  
    ``sync_timedb``).
  
  Args:
    argv (Any): CLI argument list (``sys.argv``-like).
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Raises:
    SystemExit: Raised when ``parse_sync_timedb_argv`` hits a ``SystemExit``
    failure path.
  
  Examples:
    >>> parse_sync_timedb_argv(None)  # doctest: +SKIP
  """
  argv_for_dates = list(argv)
  run_once = False
  if len(argv_for_dates) > 1 and argv_for_dates[1] == "once":
    run_once = True
    argv_for_dates = [argv_for_dates[0]] + argv_for_dates[2:]

  if len(argv_for_dates) > 1 and argv_for_dates[1] in (
      "all",
      "backlog",
      "current",
  ):
    raise SystemExit(
        "CLI modes 'all'/'backlog'/'current' are retired; "
        "run with no date args (hot+catchup bands), a YYYY-MM-DD, "
        "or a start/end date range"
    )

  if len(argv_for_dates) <= 1:
    return run_once, None, None

  now_local = datetime.today()
  default_start = datetime.combine(
      now_local.date(), datetime.min.time()) - timedelta(days=days_to_process)
  default_end = now_local
  startdate, enddate = parse_start_end_dates(
      argv_for_dates, default_start, default_end)

  if len(argv_for_dates) == 2:
    try:
      single_day = datetime.strptime(argv_for_dates[1], "%Y-%m-%d")
    except ValueError:
      pass
    else:
      startdate = single_day
      enddate = datetime.combine(single_day.date(), datetime.max.time())

  return run_once, startdate, enddate


def run_sync_timedb_jid_ingest(jid: Any) -> Any:
  """
  One-shot ingest-only path for ``--jid`` (no archive / day-close / janitor).
  
  Returns process exit code **0** on success (including zero matching files) or
  **1** on missing job / empty hosts / fatal ingest failure.
  
  Args:
    jid (Any): Jid passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> run_sync_timedb_jid_ingest(None)  # doctest: +SKIP
  """
  from collections import deque

  from hpcperfstats.dbload.lib.sync_timedb_jid_scope import (
      JobIngestScopeError,
      resolve_job_ingest_scope,
  )
  from hpcperfstats.dbload.lib.sync_timedb_stats_find import (
      collect_host_scoped_stats_paths,
  )

  global should_archive
  prev_should_archive = should_archive
  should_archive = False
  try:
    try:
      scope = resolve_job_ingest_scope(jid)
    except JobIngestScopeError as exc:
      log_print("sync_timedb --jid: %s" % exc, flush=True)
      return 1

    host_name_ext = cfg.get_host_name_ext().strip()
    if not host_name_ext:
      log_print(
          "ERROR: DEFAULT.host_name_ext must be set; sync_timedb --jid uses "
          "archive subdirectories named with this suffix.",
          flush=True,
      )
      return 1

    archive_dir = cfg.get_archive_dir_path()
    if not archive_dir or not os.path.isdir(archive_dir):
      log_print(
          "sync_timedb --jid: archive_dir missing or not a directory: %s"
          % archive_dir,
          flush=True,
      )
      return 1

    ensure_persistence_contract(
        archive_dir, log_fn=log_print, allow_reset=False,
    )
    checkpoint_path = os.path.join(archive_dir, SYNC_TIMEDB_CHECKPOINT_BASENAME)

    log_print(
        "sync_timedb --jid: jid=%s hosts=%d window_start=%s window_end=%s"
        % (
            scope.jid,
            len(scope.hosts),
            scope.window_start.isoformat(),
            scope.window_end.isoformat(),
        ),
        flush=True,
    )

    paths = collect_host_scoped_stats_paths(
        archive_dir,
        scope.hosts,
        scope.window_start,
        scope.window_end,
        log_fn=log_print,
    )
    checkpoint_paths = load_checkpoint_path_set(checkpoint_path)
    pending = [
        p for p in paths
        if os.path.normpath(p) not in checkpoint_paths
    ]
    log_print(
        "sync_timedb --jid: discovered=%d pending_after_checkpoint=%d"
        % (len(paths), len(pending)),
        flush=True,
    )

    if not pending:
      log_print("sync_timedb --jid: nothing to ingest jid=%s" % scope.jid, flush=True)
      return 0

    write_lock = threading.Lock()
    checkpoint_entries = deque(_load_sync_checkpoint(checkpoint_path))
    processed_files = set(checkpoint_paths)
    processed_files_order = deque(processed_files)
    ok_n = 0
    fail_n = 0
    for index, path in enumerate(pending):
      if shutdown_requested[0]:
        log_print("sync_timedb --jid: shutdown requested", flush=True)
        break
      result = add_stats_file_to_db(write_lock, path)
      stats_fname, _need_archival, ingest_ok, elapsed_s, outcome_meta = (
          _unpack_ingest_worker_result(result)
      )
      _record_ingest_marks_from_worker_result(result)
      remaining = max(0, len(pending) - index - 1)
      log_print(
          "sync_timedb --jid: ingest path=%s ok=%s elapsed_s=%.3f remaining=%d "
          "outcome=%s"
          % (
              stats_fname,
              int(bool(ingest_ok)),
              float(elapsed_s or 0.0),
              remaining,
              outcome_meta.get("outcome", ""),
          ),
          flush=True,
      )
      if not ingest_ok:
        fail_n += 1
        continue
      ok_n += 1
      _add_processed_path(
          stats_fname,
          processed_files,
          processed_files_order,
          checkpoint_entries,
          checkpoint_path,
      )
      try:
        _save_sync_checkpoint(checkpoint_path, checkpoint_entries)
      except OSError as exc:
        log_print(
            "ERROR: sync_timedb --jid checkpoint flush failed path=%s: %s"
            % (checkpoint_path, exc),
            flush=True,
        )
        return 1

    log_print(
        "sync_timedb --jid: done jid=%s ok=%d fail=%d"
        % (scope.jid, ok_n, fail_n),
        flush=True,
    )
    return 1 if fail_n and ok_n == 0 else 0
  finally:
    should_archive = prev_should_archive
    try:
      from django.db import close_old_connections, connections

      close_old_connections()
      connections.close_all()
    except Exception:
      pass


def run_sync_timedb_supervisor_from_parsed(
  run_once: Any,
  startdate: Any,
  enddate: Any,
) -> None:
  """
  Run one supervisor session after ``database_startup()`` (CLI or in-process.
  
    tests).
  
  Args:
    run_once (Any): Run once passed to this helper.
    startdate (Any): Time value (``datetime``, ISO string, sentinel, or
    ``None``).
    enddate (Any): Time value (``datetime``, ISO string, sentinel, or
    ``None``).
  
  Returns:
    None
  
  Examples:
    >>> run_sync_timedb_supervisor_from_parsed(None, None, None)
  """
  _reset_sync_runtime_caches()
  if startdate is None and enddate is None:
    log_print(
        "###Date Range of stats files to ingest: entire archive "
        "(orchestrator hot+catchup bands)####")
  else:
    log_date_range("stats files to ingest", startdate, enddate)

  host_name_ext = cfg.get_host_name_ext().strip()
  if not host_name_ext:
    log_print(
        "ERROR: DEFAULT.host_name_ext must be set; sync_timedb uses archive "
        "subdirectories whose names end with this suffix.")
    sys.exit(1)

  try:
    from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
        ArchiveMembersRedisUnavailableError,
        verify_archive_members_redis_startup,
    )
    verify_archive_members_redis_startup()
  except ArchiveMembersRedisUnavailableError as exc:
    _exit_on_archive_members_redis_unavailable(exc)

  _warn_if_pool_stall_wall_below_ingest_timeout_max()

  directory = cfg.get_archive_dir_path()

  manager = multiprocessing.Manager()
  try:
    log_print(
        "Pipeline absolute pools effective_cores=%d sync_ingest=%d sync_archive=%d "
        "metrics=%d write_lock_shards=%d"
        % (
            cfg.get_effective_cores(),
            cfg.get_sync_ingest_pool_processes(),
            cfg.get_sync_archive_pool_processes(),
            cfg.get_metrics_pool_processes(),
            cfg.get_sync_write_lock_shards(),
        ),
        flush=True,
    )
    lock_shards = max(1, int(cfg.get_sync_write_lock_shards()))
    if lock_shards == 1:
      manager_lock = manager.Lock()
    else:
      manager_lock = [manager.Lock() for _ in range(lock_shards)]
      log_print("Using %d sync_timedb write-lock shards" % lock_shards, flush=True)
    with create_sync_timedb_spawn_pool(
        processes=archive_thread_count,
        initializer=apply_pool_worker_process_title,
        initargs=(SYNC_TIMEDB_PROCESS_TITLE, "archive-pool"),
        pool_kind_log_label="archive-pool",
    ) as archive_pool:
      try:
        run_sync_timedb_queue_orchestrator(
            directory,
            startdate,
            enddate,
            host_name_ext,
            manager_lock,
            archive_pool,
            run_once=run_once,
            log_fn=log_print,
        )
      except MultiprocessingWorkerExitError as exc:
        hard_exit_pool_worker_error(exc)

    if DEBUG:
      log_print("sync_timedb finished")
  finally:
    manager.shutdown()


def run_ingest_entire_archive_once_for_tests() -> None:
  """
  In-process equivalent of ``python sync_timedb.py once`` (full archive).
  
  Uses the active Django database (e.g. pytest-django ``test_*``), unlike a
  subprocess which would connect to ``[DEFAULT] dbname`` from ini only. Forces
  single-process ingest so spawn workers do not open the non-test database.
  
  Returns:
    None
  
  Examples:
    >>> run_ingest_entire_archive_once_for_tests()  # doctest: +SKIP
  """
  old_inline = os.environ.get(_SYNC_TIMEDB_INGEST_INLINE_ENV)
  os.environ[_SYNC_TIMEDB_INGEST_INLINE_ENV] = "1"
  try:
    database_startup()
    run_sync_timedb_supervisor_from_parsed(True, None, None)
  finally:
    if old_inline is None:
      os.environ.pop(_SYNC_TIMEDB_INGEST_INLINE_ENV, None)
    else:
      os.environ[_SYNC_TIMEDB_INGEST_INLINE_ENV] = old_inline


if __name__ == '__main__':
  # Use a mutable container so the SIGTERM handler can update state without
  # relying on `nonlocal` (which is only valid for enclosing function scopes).
  sigterm_received = {"value": False}

  def _sigterm_handler(signum: Any, frame: Any) -> None:
    """
    Internal helper to handle sigterm handler.
    
    Args:
      signum (Any): Signum passed to this helper.
      frame (Any): Frame passed to this helper.
    
    Returns:
      None
    
    Raises:
      SystemExit: Raised when ``_sigterm_handler`` hits a ``SystemExit``
      failure path.
    
    Examples:
      >>> _sigterm_handler(None, None)  # doctest: +SKIP
    """
    sigterm_received["value"] = True
    shutdown_requested[0] = True
    raise SystemExit(143)

  previous_sigterm_handler = signal.getsignal(signal.SIGTERM)
  signal.signal(signal.SIGTERM, _sigterm_handler)
  try:
    set_daemon_process_title(name=SYNC_TIMEDB_PROCESS_TITLE, role="main")
    database_startup()
    from hpcperfstats.dbload.lib.sync_timedb_jid_scope import (
        parse_sync_timedb_jid_cli_arg,
    )

    jid, jid_err = parse_sync_timedb_jid_cli_arg(sys.argv)
    if jid_err is not None:
      log_print(jid_err, flush=True)
      sys.exit(1)
    if jid is not None:
      sys.exit(run_sync_timedb_jid_ingest(jid))
    run_once, startdate, enddate = parse_sync_timedb_argv(sys.argv)
    run_sync_timedb_supervisor_from_parsed(run_once, startdate, enddate)
    if shutdown_requested[0]:
      sys.exit(143)
  except DatabaseUnavailableExit:
    sys.exit(2)
  except MultiprocessingWorkerExitError as exc:
    from hpcperfstats.dbload.lib.multiprocessing_pool_health import (
        hard_exit_pool_worker_error,
    )

    hard_exit_pool_worker_error(exc)
  finally:
    signal.signal(signal.SIGTERM, previous_sigterm_handler)
    # Best-effort cleanup + parent notification when SIGTERM is received.
    if sigterm_received["value"]:
      try:
        close_old_connections()
        connections.close_all()
      except Exception:
        pass
      try:
        send_sigchld_to_parent()
      except Exception:
        pass
    signal.signal(signal.SIGTERM, previous_sigterm_handler)
