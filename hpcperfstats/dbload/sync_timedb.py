#!/usr/bin/env python3
"""Load raw stats files into TimescaleDB (host_data, proc_data). Parses stats, applies hardware counter maps, computes deltas/arc, bulk-inserts, and optionally archives processed files (append to daily ``.tar``; seal to ``.tar.zst`` and raw/``.tar`` cleanup via the background ``ArchiveJanitor``). Runs in parallel with configurable chunk size.

**Hot path (supervisor thread):** discover → ingest → checkpoint → dispatch append (up to ``sync_archive_pool_processes`` concurrent daily-tar slots; one day per slot). Ingest never blocks on seal, zstd, raw delete, or uncompressed ``.tar`` removal.

**Cold path (``ArchiveJanitor`` coordinator thread):** day-debt queue drained within ``archive_janitor_budget_seconds`` using a ``ThreadPoolExecutor`` of up to ``sync_day_close_max_inflight`` (default **4**) parallel ``DAY_CLOSE`` workers (continuous refill on completion). Each tick discovers checkpoint-complete ``DAY_CLOSE`` candidates and enqueues debt (same inflight cap). Workers run seal → verify → delete → tar_drop (steady-state ingest is not gated on raw deletion). Snapshot/hints refresh at supervisor startup (CLI ``all`` only) and on scheduled maintenance passes. Per-day lock cleanup (once per tick), dedupe-before-seal, and DB head-ingest gate unchanged. Progress persists in ``.sync_archive_maint_hints.json`` v2 (``debt_queue``, ``day_phases``).

**Startup maintenance (``all`` only):** when ``startdate == 'all'``, the janitor thread builds the canonical snapshot and may discover/enqueue ``DAY_CLOSE`` debt, but does not run ``_close_one_day`` until the supervisor clears the ingest gate. Boot handoff discovery runs once on the first main-loop iteration after gate clear. Date-range runs skip startup maintenance and begin ingest immediately.

Append and raw delete stay DB-gated when ``sync_archive_require_db_ingest=yes``. Finalize uses soft defer (``allow_defer``) under ingest backlog instead of blocking the supervisor.

When the ingest queue is empty, rescans for new stats files. After a rescan still finds nothing pending, it sleeps ``EMPTY_QUEUE_RESCAN_SLEEP_SECONDS`` (default 30s) and exits the loop iteration (continuous mode repeats).

CLI: no args uses a sliding window (see ``days_to_process``) through now. One ``YYYY-MM-DD`` ingests that calendar day only. Two dates ``YYYY-MM-DD YYYY-MM-DD`` set an explicit range. First arg ``all`` scans every host stats dir under ``archive_dir`` (subdirs whose names end with ``DEFAULT.host_name_ext`` from ini). Prefix ``once`` to exit after one idle rescan (no 300s sleep), e.g. ``once all`` or ``once 2024-01-15``.

DB access is process-safe: pool workers use close_old_connections() at task start and connections.close_all() at task end so connections do not linger between files. Writes are serialized with a shared lock.

"""
import contextvars
import ctypes
import gc
import itertools
import heapq
import json
import multiprocessing
from contextlib import contextmanager
import os

from hpcperfstats.dbload.lib.blas_thread_env import configure_blas_thread_env

configure_blas_thread_env()

import shutil
import signal
import tempfile
import subprocess
import sys
import threading
import time
import types
import warnings
from collections import deque
from collections import defaultdict
from dataclasses import dataclass
from datetime import date as date_cls
from datetime import datetime, timedelta, timezone
from enum import Enum
from functools import partial
from hpcperfstats.dbload.lib.django_bootstrap import ensure_django
from hpcperfstats.dbload.lib.process_title import (
    apply_pool_worker_process_title,
    set_daemon_process_title,
)

ensure_django()

SYNC_TIMEDB_PROCESS_TITLE = "sync_timedb.py"

from django.db import IntegrityError, close_old_connections, connections
from django.db.utils import DatabaseError, OperationalError

import hpcperfstats.dbload.lib.conf_parser as cfg
from hpcperfstats.dbload.lib.date_utils import log_date_range, parse_start_end_dates
from hpcperfstats.dbload.lib.db_unavailable import (
    DatabaseUnavailableExit,
    is_database_unavailable_error,
    log_and_raise_database_unavailable,
    reraise_database_unavailable_chain,
)
from hpcperfstats.dbload.lib.io_helpers import host_data_instance_from_stats_row
from hpcperfstats.dbload.lib.multiprocessing_pool_health import (
    MultiprocessingPoolStallError,
    MultiprocessingWorkerExitError,
    alive_pool_worker_count,
    async_result_get_watch_pool,
    close_pool_bounded,
    create_sync_timedb_spawn_pool,
    dedupe_ingest_paths_preserve_order,
    hard_exit_pool_worker_error,
    imap_sliding_window_watch_pool,
    imap_unordered_watch_pool,
    maintain_ingest_pool_after_supervisor_retire,
    pool_workers_all_idle,
    probe_ingest_pool_dispatch,
    reclaim_excess_ingest_pool_children,
    reap_pool_worker_pids,
    reap_zombie_children_of_self,
    retire_pool_worker_pid,
    warn_unreaped_zombie_children,
    sync_timedb_spawn_pool_recycle_kwargs,
    terminate_pool_bounded,
)
from hpcperfstats.dbload.lib.sync_timedb_ingest_timeout import (
    any_giant_ingest_budget_in_flight,
    calendar_day_from_sealed_archive_path,
    is_giant_ingest_budget,
    iter_giant_supplement_paths,
    max_ingest_per_file_timeout_for_paths as _max_ingest_per_file_timeout_for_paths,
    resolve_ingest_per_file_timeout_s,
    stall_abort_polls_for_paths as _stall_abort_polls_for_batch,
)
from hpcperfstats.dbload.lib.process_memory import (
    format_tree_rss_breakdown_mb,
    read_process_rss_bytes,
    read_sync_timedb_tree_rss_bytes,
)
from hpcperfstats.dbload.lib.archive_compress import (
    compressed_sibling_paths,
    daily_compressed_path_for_date,
    daily_tar_path_from_compressed,
    detect_compressed_format,
)
from hpcperfstats.dbload.lib.print_utils import log_print
from hpcperfstats.dbload.lib.shutdown_utils import (
    shutdown_requested,
    send_sigchld_to_parent,
    sleep_until_shutdown,
)
from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
    augment_unprocessed_by_tar_with_pending_paths,
    build_archive_mapping,
    build_chunk_day_histogram,
    build_live_unprocessed_by_tar_for_reconcile,
    build_day_close_disqualified_daily_tars,
    build_remaining_raw_stats_by_daily_gz,
    build_unprocessed_raw_by_daily_tar,
    build_tar_append_member_map,
    clear_daily_archive_members_cache,
    consume_archive_members_populate_source,
    daily_tar_eligible_for_day_close_submit,
    merge_daily_archive_members_l1_cache,
    pending_minus_chunk,
    select_ingest_chunk_paths,
    try_reuse_pending_reconcile_unprocessed_cache,
    age_misbucket_handoff_priority_paths,
    resolve_unmapped_closed_raw_daily_tars,
    daily_tar_path_for_stats_path,
    calendar_date_from_daily_tar_path,
    invalidate_after_daily_tar_mutation,
    prepare_paths_for_giant_member_append,
    days_ingest_complete_by_checkpoint,
    oldest_checkpoint_incomplete_tar,
    aligned_on_disk_unprocessed_paths_for_tar,
    all_on_disk_unprocessed_paths,
    prepend_checkpoint_incomplete_paths_to_pending,
    reconcile_orphan_inflight_for_oldest_tar,
    load_checkpoint_path_set,
    resolved_checkpoint_path_set,
    daily_tar_paths_for_stats_paths,
    daily_tar_paths_from_pending_archive_tasks,
    filter_files_to_add_to_archive,
    get_existing_archive_members,
    get_existing_archive_members_for_daily_archive,
    iter_daily_tar_paths,
    replace_corrupt_tar_from_compressed_backup,
    ensure_daily_tar_restored_for_append,
    cap_pending_stats_file_list,
    cap_pending_stats_with_blocked_retention,
    build_giant_supplement_pending_tail,
    merge_rescan_discovered_into_pending,
    resolve_idle_rescan_closed_paths,
    supplement_pending_paths_from_closed_paths,
    chunk_was_cross_day_defer_dispatch,
    all_ingest_outcomes_db_skip_head_tail,
    sort_pending_stats_paths_oldest_first,
    INGEST_PARSE_FAILED_QUARANTINE_REASON,
    quarantine_ingest_failed_raw_path,
    raw_stats_path_tar_append_decision,
    rescan_pending_stats_files,
    set_archive_members_invalidation_hook,
    stats_file_is_active_segment,
    stats_path_ingest_sort_epoch,
    verify_tar_archive_readable,
    _derive_stats_path_date,
    normalize_daily_compressed_path,
)
from hpcperfstats.dbload.lib.sync_timedb_ingest_worker_diagnostics import (
    apply_ingest_pool_worker_init,
    clear_dispatch_worker_stages,
    count_worker_registry_entries,
    format_worker_stages_snapshot,
    prune_stale_worker_stages,
    record_worker_stage,
    seed_dispatch_worker_stages,
    update_worker_substage,
    worker_registry_shows_recent_progress,
)
from hpcperfstats.dbload.lib.sync_timedb_worker_memory import (
    WorkerMemoryBatchAccumulator,
    classify_supervisor_reap_kind,
    increment_worker_tasks_on_worker,
    measure_worker_rss_after_release,
    resolve_worker_pid_from_meta_or_registry,
    should_supervisor_retire_worker,
    should_defer_supervisor_retire,
)
from hpcperfstats.dbload.lib.sync_timedb_day_close_manifest import (
    DayCloseManifestCoordinator,
)
from hpcperfstats.dbload.lib.file_locking import file_write_lock
from hpcperfstats.dbload.lib.sync_timedb_archive_dispatch import ArchiveDispatchCoordinator
from hpcperfstats.dbload.lib.sync_timedb_archive_janitor import ArchiveJanitor
from hpcperfstats.dbload.lib.sync_timedb_day_raw_removal import (
    DayRawRemovalCoordinator,
)
from hpcperfstats.dbload.lib.sync_timedb_startup_archive_scan import (
    StartupArchiveScanCoordinator,
)
from hpcperfstats.dbload.lib import sync_timedb_mode_heartbeat as mode_heartbeat
from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
    ArchiveMembersPopulateStalledError,
    ArchiveMembersRedisConnectionError,
    ArchiveMembersRedisUnavailableError,
    IngestArchiveLookupBudgetExceededError,
    archive_members_populate_shows_progress_for_day,
    archive_members_redis_enabled,
    build_archive_members_redis_keys,
    describe_archive_members_populate_redis_for_day,
    get_ingest_task_deadline_monotonic,
    get_ingest_task_effective_timeout_s,
    idle_pool_recover_skip_reason_for_paths,
    is_populate_pool_unavailable_error,
    is_transient_fnctl_populate_unavailable,
    maybe_clear_orphan_incomplete_archive_members_redis,
    redis_members_cache_is_fully_warm,
    reset_ingest_task_deadline_monotonic,
    reset_ingest_task_effective_timeout_s,
    set_ingest_task_deadline_monotonic,
    set_ingest_task_effective_timeout_s,
    _raise_if_ingest_deadline_exceeded,
)

# Supervisor tests monkeypatch these names; cold-path maintenance lives in ArchiveJanitor.
def seal_dirty_daily_archives(*args, **kwargs):
  raise RuntimeError("seal_dirty_daily_archives is janitor-only; supervisor must not call this")


def remove_verified_archived_raw_files(*args, **kwargs):
  raise RuntimeError(
      "remove_verified_archived_raw_files is janitor-only; supervisor must not call this")


def remove_verified_uncompressed_daily_tars(*args, **kwargs):
  raise RuntimeError(
      "remove_verified_uncompressed_daily_tars is janitor-only; supervisor must not call this")
from hpcperfstats.dbload.lib import sync_timedb_host_itimes
from hpcperfstats.dbload.lib.sync_timedb_ingest_readiness import (
    filter_paths_head_ingested,
    head_timestamp_present_in_db,
    reset_sync_ingest_readiness_caches,
    stats_file_head_ingested_in_db,
)
from hpcperfstats.dbload.lib.sync_timedb_persistence import (
    ensure_persistence_contract,
    load_persistence_document,
    save_persistence_document,
)
from hpcperfstats.dbload.lib.sync_timedb_parsing import (
    EVENTMAPS_BY_TYPE,
    DeltaCarryState,
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
# Max paths per ``tar -T`` batch (limits list-file size; argv stays tiny).
tar_append_batch_size = 256
# Rescan stats directory after this many processed chunks
rescan_every_chunks = 1
# Bound processed-file tracking to avoid unbounded set growth in long runs.
processed_files_max_size = 200000
SYNC_TIMEDB_CHECKPOINT_BASENAME = ".sync_timedb_state.json"
SYNC_TIMEDB_CHECKPOINT_FLUSH_EVERY_FILES = cfg.get_sync_checkpoint_flush_batch_size()

# When no pending files remain after final sealing, sleep this long (seconds)
# before exiting sync_timedb. Interruptible via shutdown_requested / SIGTERM path.
EMPTY_QUEUE_RESCAN_SLEEP_SECONDS = 30

# Emit DB lock-wait logs only for sustained contention.
LOCK_WAIT_LOG_THRESHOLD_SECONDS = 30.0
FINALIZE_POLL_TIMEOUT_SECONDS = 0.05

INGEST_PER_FILE_TIMEOUT_LOG_MIN_S = 1800.0


class IngestPerFileTimeoutError(TimeoutError):
  """Raised when one ingest pool task exceeds its resolved per-file budget."""

  def __init__(self, path, stage, elapsed_s):
    self.path = str(path)
    self.stage = str(stage)
    self.elapsed_s = float(elapsed_s)
    super().__init__(
        "ingest per-file timeout path=%s stage=%s elapsed_s=%.3f"
        % (self.path, self.stage, self.elapsed_s)
    )


class _IngestPoolInFlightTracker:
  """Tracks paths dispatched to an ingest pool but not yet returned via imap."""

  def __init__(self, paths):
    self._pending = {
        os.path.normpath(p)
        for p in (paths or ())
        if p
    }
    self._batch_seen = set(self._pending)

  def complete(self, path):
    norm = os.path.normpath(path) if path else None
    if norm:
      self._pending.discard(norm)
      self._batch_seen.add(norm)

  def note_dispatched(self, path):
    norm = os.path.normpath(path) if path else None
    if norm:
      self._pending.add(norm)
      self._batch_seen.add(norm)

  def batch_seen_paths(self):
    return set(self._batch_seen)

  def all_in_flight_paths(self):
    return set(self._pending)

  def sample_in_flight(self, max_n=10):
    return sorted(self._pending)[: max(0, int(max_n))]

  def in_flight_count(self):
    return len(self._pending)


def _log_long_ingest_timeout_budget_if_needed(stats_file, timeout_s):
  if timeout_s < INGEST_PER_FILE_TIMEOUT_LOG_MIN_S:
    return
  size_bytes = stats_file_size_bytes(stats_file)
  log_print(
      "WARN: ingest per-file timeout budget path=%s size_bytes=%s timeout_s=%.1f"
      % (stats_file, size_bytes, timeout_s),
      flush=True,
  )
  update_worker_substage(
      "long_timeout_budget",
      timeout_s="%.1f" % timeout_s,
      size_bytes=str(size_bytes),
  )


def _raise_if_ingest_per_file_deadline_exceeded(stats_file, stage):
  """Monotonic deadline check for DB phases SIGALRM cannot interrupt."""
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


def _imap_ingest_result_path(result):
  if isinstance(result, (tuple, list)) and result:
    return result[0]
  return None


def _run_ingest_timed(stats_file, stage, fn, *, enable_sigalrm=True):
  """Run ingest worker body with optional Unix wall-clock cap."""
  timeout_s = resolve_ingest_per_file_timeout_s(stats_file)
  deadline_token = None
  effective_token = None
  if timeout_s > 0.0:
    effective_token = set_ingest_task_effective_timeout_s(timeout_s)
    deadline_token = set_ingest_task_deadline_monotonic(
        time.monotonic() + timeout_s,
    )
  record_worker_stage(stats_file, stage)
  _log_long_ingest_timeout_budget_if_needed(stats_file, timeout_s)
  try:
    if timeout_s <= 0.0 or not enable_sigalrm or not hasattr(signal, "SIGALRM"):
      return fn()

    path_label = str(stats_file)
    t0 = time.monotonic()

    def _handler(signum, frame):
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


def _merge_worker_memory_meta(result, mem_meta):
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
    pool,
    registry,
    result,
    accumulator,
    pool_health_context=None,
    recreate_ingest_pool_fn=None,
    on_pool_replaced=None,
    pending_inflight=None,
    max_inflight=None,
):
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


def _log_ingest_per_file_timeout(exc):
  log_print(
      "ERROR: ingest per-file timeout path=%s elapsed=%.1fs stage=%s"
      % (exc.path, exc.elapsed_s, exc.stage),
      flush=True,
  )


def _log_ingest_archive_lookup_budget_exceeded(exc):
  log_print(
      "ERROR: ingest archive lookup budget exceeded: %s"
      % exc,
      flush=True,
  )


def _unique_daily_compressed_archives_for_paths(paths, tgz_archive_dir):
  """Map canonical daily ``.tar.zst`` paths to ISO day tokens for chunk paths."""
  unique = {}
  for path in paths or ():
    file_date = _derive_stats_path_date(path)
    if file_date is None:
      continue
    compressed = daily_compressed_path_for_date(tgz_archive_dir, file_date)
    unique[compressed] = file_date.isoformat()
  return unique


def _paths_all_db_complete_for_prewarm_skip(paths):
  """True when every chunk path would db-complete skip (no tar restore/prewarm)."""
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


_ARCHIVE_JANITOR_REF = {}


def _signal_ingest_hot_for_populate(day_token, tar_path, *, reason):
  """Early hot-path signal before populate fnctl wait (non-blocking)."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      set_ingest_tar_hot,
  )

  if day_token:
    set_ingest_tar_hot(day_token, reason=reason)
  janitor = _ARCHIVE_JANITOR_REF.get("janitor")
  if janitor is not None and tar_path:
    janitor.signal_day_close_yield(tar_path, reason=reason)


def _prewarm_archive_members_redis_for_days(
    day_items,
    *,
    gated_tar_restore_day_tokens=None,
):
  """Single-flight populate on supervisor before imap when Redis L2 is cold."""
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


def _prewarm_archive_members_redis_for_day_token(day_token):
  if not day_token:
    return
  try:
    day_date = date_cls.fromisoformat(day_token)
  except ValueError:
    return
  compressed = daily_compressed_path_for_date(tgz_archive_dir, day_date)
  _prewarm_archive_members_redis_for_days([(compressed, day_token)])


def _reprewarm_archive_members_after_seal_phase(
    day_token,
    *,
    day_raw_removal,
    prewarm_fn=_prewarm_archive_members_redis_for_day_token,
    log_fn=log_print,
):
  """Re-prewarm Redis after seal invalidation, unless verify will populate."""
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
    paths,
    *,
    oldest_tar=None,
    gated_tar_restore=False,
    skip_prewarm=False,
):
  """Single-flight populate on supervisor before imap when Redis L2 is cold."""
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
      "sync_timedb: chunk prewarm begin paths=%d days=%s oldest_tar=%s "
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
      "sync_timedb: chunk prewarm complete elapsed_s=%.3f days=%s"
      % (time.time() - prewarm_t0, summary),
      flush=True,
  )
  return summary


def _prewarm_archive_members_redis_for_sealed_chunk(sealed_paths):
  """Prewarm Redis member maps for unique calendar days in a sealed archive chunk."""
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
      "sync_timedb: archive chunk prewarm begin sealed_paths=%d days=%s"
      % (len(day_items), day_tokens),
      flush=True,
  )
  prewarm_t0 = time.time()
  summary = _prewarm_archive_members_redis_for_days(day_items)
  log_print(
      "sync_timedb: archive chunk prewarm complete elapsed_s=%.3f days=%s"
      % (time.time() - prewarm_t0, summary),
      flush=True,
  )
  return summary


def _calendar_day_hint_from_paths(paths):
  """Best-effort calendar day from first in-flight stats path filename epoch."""
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


def _calendar_day_hint_from_sealed_paths(sealed_paths):
  """Best-effort calendar day from sealed daily archive paths."""
  for path in sealed_paths or ():
    day = calendar_day_from_sealed_archive_path(path)
    if day:
      return day
  return ""


def _distinct_calendar_days_from_sealed_paths(sealed_paths, max_days=8):
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


def _distinct_calendar_days_from_paths(paths, max_days=8):
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


def _in_flight_file_meta_from_paths(paths, max_n=10):
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
# Skip full live unprocessed rebuild when oldest incomplete snapshot is unchanged.
PENDING_RECONCILE_UNPROCESSED_TTL_S = 120.0
_SUPERVISOR_CHILD_REAP_INTERVAL_S = 60.0
_last_supervisor_child_reap_mono = 0.0


class IngestStallDiagnostics:
  """Supervisor-thread state included on pool imap stall WARN/ERROR lines."""

  def __init__(self):
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

  def note_imap_completion(self):
    self.last_imap_completion_monotonic = time.monotonic()

  def seconds_since_last_imap_completion(self):
    last = self.last_imap_completion_monotonic
    if last is None:
      return -1.0
    return max(0.0, time.monotonic() - float(last))

  def format_day_close_pipeline_detail(self):
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


def _pool_stall_wall_seconds():
  """INI ceiling stall wall (maximum across batches)."""
  poll_s = float(cfg.get_sync_pool_poll_timeout_s())
  abort_n = int(cfg.get_sync_pool_stall_abort_after_timeouts())
  if poll_s <= 0.0:
    return 0.0
  return poll_s * abort_n


def _dynamic_stall_wall_seconds(stall_diagnostics):
  """Active imap sub-batch stall wall, or INI ceiling when unset."""
  if stall_diagnostics is not None:
    dynamic_wall = float(
        getattr(stall_diagnostics, "dynamic_stall_wall_s", 0.0) or 0.0,
    )
    if dynamic_wall > 0.0:
      return dynamic_wall
  return _pool_stall_wall_seconds()


def _ingest_stall_defer_long_budget(stall_diagnostics, consecutive_timeouts):
  """Defer when worker registry budget exceeds batch precompute (safety net)."""
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


def _sample_looks_like_sealed_archives(sample):
  for path in sample or ():
    base = os.path.basename(str(path))
    if base.endswith(".tar.zst") or base.endswith(".tar.gz"):
      return True
  return False


def _ingest_stall_defer_state(
    day_hint,
    progress_state,
    *,
    stall_diagnostics=None,
    consecutive_timeouts=0,
    pool=None,
    sample=None,
    day_hint_from_sample_fn=None,
):
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
  pipeline = (
      getattr(stall_diagnostics, "ingest_pipeline", "")
      if stall_diagnostics is not None
      else ""
  )
  if pipeline == "sealed_archive_backfill" or _sample_looks_like_sealed_archives(sample):
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


def _format_redis_populate_for_in_flight_days(paths, max_days=3):
  days = _distinct_calendar_days_from_paths(paths, max_days=max_days)
  if not days:
    return ""
  parts = [
      "%s{%s}"
      % (day, describe_archive_members_populate_redis_for_day(day, tgz_archive_dir))
      for day in days
  ]
  return " redis_by_day=" + " ".join(parts)


def _format_redis_populate_for_sealed_paths(sealed_paths, max_days=3):
  days = _distinct_calendar_days_from_sealed_paths(sealed_paths, max_days=max_days)
  if not days:
    return ""
  parts = [
      "%s{%s}"
      % (day, describe_archive_members_populate_redis_for_day(day, tgz_archive_dir))
      for day in days
  ]
  return " redis_by_day=" + " ".join(parts)


def _max_effective_ingest_timeout_from_registry(registry):
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


def _warn_if_pool_stall_wall_below_ingest_timeout_max():
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
    sample,
    day_hint,
    stall_diagnostics,
    progress_state,
    alive_workers,
    consecutive,
    poll_timeout_s,
    distinct_days_from_sample_fn=None,
    redis_populate_for_sample_fn=None,
):
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
  worker_stages = format_worker_stages_snapshot(worker_registry)
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
  overlap_mode = cfg.get_pipeline_overlap_mode()
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
      " pipeline_overlap_mode=%s day_close=%s chunk_prewarm=%s"
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
          overlap_mode,
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
    tracker,
    *,
    pool,
    thread_count,
    chunk_counter,
    pending_count,
    stall_diagnostics=None,
    progress_state=None,
    day_hint_from_sample_fn=None,
    distinct_days_from_sample_fn=None,
    redis_populate_for_sample_fn=None,
):
  def on_stall_warning(consecutive, abort_after, poll_timeout_s, context):
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


def _should_emit_stall_defer_warn(defer_reason, defer_log_state, interval_s):
  """Return True when a pool imap stall defer WARN should be logged."""
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
    tracker,
    progress_state,
    stall_diagnostics=None,
    *,
    day_hint_from_sample_fn=None,
    supervisor_reap_fn=None,
):
  """Defer pool imap stall abort while Redis populate shows progress."""
  defer_log_state = {}

  def on_stall_poll(consecutive, context, pool_health_context):
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


def _imap_ingest_pool(
    pool,
    fn,
    paths,
    *,
    context,
    tracker,
    thread_count,
    chunk_counter,
    pending_count,
    ingest_pool=None,
    archive_pool=None,
    stall_poll_state=None,
    stall_diagnostics=None,
    populate_pool_controller=None,
):
  if pool is None:
    return iter(())
  if stall_poll_state is None:
    stall_poll_state = {}
  batch_abort = _stall_abort_polls_for_batch(paths)
  poll_s = float(cfg.get_sync_pool_poll_timeout_s())
  if stall_diagnostics is not None:
    stall_diagnostics.current_imap_batch_size = len(paths or ())
    stall_diagnostics.imap_batch_cap = _effective_ingest_imap_inflight_cap(
        thread_count,
        len(paths or ()),
    )
    stall_diagnostics.current_imap_batch_max_timeout_s = (
        _max_ingest_per_file_timeout_for_paths(paths)
    )
    stall_diagnostics.dynamic_stall_abort_after_polls = batch_abort
    stall_diagnostics.dynamic_stall_wall_s = batch_abort * poll_s
  pool_health_context = {
      "ingest_pool": ingest_pool if ingest_pool is not None else pool,
      "archive_pool": archive_pool,
      "expected_pool_workers": thread_count,
      "in_flight_sample_fn": (
          tracker.sample_in_flight if tracker is not None else None
      ),
  }

  def _supervisor_reap():
    _maybe_reap_supervisor_pool_children_throttled(
        ingest_pool if ingest_pool is not None else pool,
        archive_pool,
        populate_pool_controller,
        context="stall_poll",
    )

  iterator = imap_unordered_watch_pool(
      pool,
      fn,
      paths,
      context=context,
      stall_abort_after_timeouts=batch_abort,
      on_stall_warning=_make_ingest_stall_warning_fn(
          tracker,
          pool=pool,
          thread_count=thread_count,
          chunk_counter=chunk_counter,
          pending_count=pending_count,
          stall_diagnostics=stall_diagnostics,
          progress_state=stall_poll_state,
      ),
      on_stall_poll=_make_ingest_stall_poll_fn(
          tracker,
          stall_poll_state,
          stall_diagnostics=stall_diagnostics,
          supervisor_reap_fn=_supervisor_reap,
      ),
      pool_health_context=pool_health_context,
      on_stall_fatal_summary=(
          lambda consecutive, abort_after, poll_timeout_s, ctx: _build_ingest_stall_log_suffix(
              sample=tracker.sample_in_flight() if tracker is not None else [],
              day_hint=_calendar_day_hint_from_paths(
                  tracker.sample_in_flight() if tracker is not None else [],
              ),
              stall_diagnostics=stall_diagnostics,
              progress_state=stall_poll_state,
              alive_workers=alive_pool_worker_count(pool),
              consecutive=consecutive,
              poll_timeout_s=poll_timeout_s,
          )
          if tracker is not None or stall_diagnostics is not None
          else ""
      ),
  )
  for item in iterator:
    if stall_diagnostics is not None:
      stall_diagnostics.note_imap_completion()
    yield item


def _effective_ingest_imap_inflight_cap(thread_count, path_count):
  """Sliding-window inflight equals live pool size (capped by ``sync_pool_process_cap``)."""
  return max(1, min(int(path_count), int(thread_count)))


def _update_sliding_window_stall_diagnostics(stall_diagnostics, in_flight_paths, inflight_cap):
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


def _imap_ingest_paths_batched(
    pool,
    fn,
    paths,
    *,
    thread_count,
    context,
    tracker,
    chunk_counter,
    pending_count,
    ingest_pool=None,
    archive_pool=None,
    stall_diagnostics=None,
    pending_tail=None,
    replenish_pending_tail_fn=None,
    populate_pool_controller=None,
    on_ingest_pool_replaced=None,
    pool_health_context=None,
):
  """Cap concurrent pool tasks below full chunk size for RSS safety (sliding window)."""
  inflight_cap = _effective_ingest_imap_inflight_cap(thread_count, len(paths))
  stall_poll_state = {}
  dispatch_registry = (
      stall_diagnostics.worker_registry
      if stall_diagnostics is not None
      else None
  )
  if stall_diagnostics is not None:
    stall_diagnostics.current_imap_batch_size = len(paths or ())
  if dispatch_registry is not None:
    seed_dispatch_worker_stages(dispatch_registry, paths)
  active_ingest_pool = ingest_pool if ingest_pool is not None else pool
  if pool_health_context is None:
    pool_health_context = {}
  pool_health_context.setdefault("ingest_pool", active_ingest_pool)
  pool_health_context.setdefault("active_pool", active_ingest_pool)
  pool_health_context.setdefault("archive_pool", archive_pool)
  pool_health_context.setdefault("expected_pool_workers", thread_count)
  if pool_health_context.get("in_flight_sample_fn") is None:
    pool_health_context["in_flight_sample_fn"] = (
        tracker.sample_in_flight if tracker is not None else None
    )
  supplement_log_state = {"logged": False, "empty_logged": False, "replenish_n": 0}
  pending_tail_state = list(pending_tail or ())

  def _supervisor_reap():
    _maybe_reap_supervisor_pool_children_throttled(
        pool_health_context.get("ingest_pool") or active_ingest_pool,
        archive_pool,
        populate_pool_controller,
        context="stall_poll",
    )

  def _format_giant_in_flight_snapshot(in_flight_paths):
    entries = []
    for path in in_flight_paths or ():
      if not is_giant_ingest_budget(path):
        continue
      try:
        size_bytes = int(stats_file_size_bytes(path))
      except (TypeError, ValueError, OSError):
        size_bytes = 0
      timeout_s = resolve_ingest_per_file_timeout_s(path)
      entries.append(
          "%s:%d:%.0fs"
          % (os.path.basename(str(path)), size_bytes, float(timeout_s)),
      )
    return entries

  def _supplement_exclude(in_flight_paths):
    exclude = {
        os.path.normpath(str(p))
        for p in (in_flight_paths or ())
        if p
    }
    if tracker is not None:
      exclude.update(
          os.path.normpath(str(p))
          for p in tracker.batch_seen_paths()
          if p
      )
    return exclude

  def _select_giant_supplement(slots_needed, exclude):
    return list(
        iter_giant_supplement_paths(
            pending_tail_state,
            limit=int(slots_needed),
            exclude=exclude,
        ),
    )

  def _classify_supplement_empty(exclude):
    """Distinguish empty reservoir vs size-filter dryness for operator logs."""
    if not pending_tail_state:
      return "exhausted"
    soft = int(cfg.get_sync_ingest_giant_pool_supplement_max_bytes())
    hard = int(cfg.get_sync_ingest_giant_pool_supplement_large_max_bytes())
    hard = max(soft, hard)
    saw_any = False
    for path in pending_tail_state:
      if not path:
        continue
      norm = os.path.normpath(str(path))
      if norm in exclude:
        continue
      saw_any = True
      try:
        size = int(stats_file_size_bytes(path))
      except (TypeError, ValueError, OSError):
        continue
      if 0 < size < hard:
        return "size_filter"
    return "exhausted" if not saw_any else "size_filter"

  def _giant_pool_supplement_paths_fn(slots_needed, in_flight_paths):
    if not cfg.get_sync_ingest_giant_pool_supplement_enabled():
      return []
    if slots_needed <= 0:
      return []
    if not any_giant_ingest_budget_in_flight(in_flight_paths):
      return []
    exclude = _supplement_exclude(in_flight_paths)
    selected = _select_giant_supplement(slots_needed, exclude)
    if (
        not selected
        and callable(replenish_pending_tail_fn)
    ):
      refreshed = list(replenish_pending_tail_fn(exclude) or ())
      if refreshed:
        pending_tail_state[:] = refreshed
        supplement_log_state["replenish_n"] += 1
        log_print(
            "INFO: sync_timedb: giant pool supplement replenish "
            "pending_tail_n=%d supplement_queue=%d replenish_n=%d "
            "idle_slots=%d"
            % (
                len(pending_tail_state),
                int(cfg.get_sync_ingest_giant_pool_supplement_queue_size()),
                int(supplement_log_state["replenish_n"]),
                int(slots_needed),
            ),
            flush=True,
        )
        selected = _select_giant_supplement(slots_needed, exclude)
    if not selected:
      if not supplement_log_state["empty_logged"]:
        supplement_log_state["empty_logged"] = True
        reason = _classify_supplement_empty(exclude)
        log_print(
            "INFO: sync_timedb: giant pool supplement empty reason=%s "
            "pending_tail_n=%d idle_slots=%d"
            % (reason, len(pending_tail_state), int(slots_needed)),
            flush=True,
        )
      return []
    if dispatch_registry is not None:
      seed_dispatch_worker_stages(dispatch_registry, selected)
    if tracker is not None:
      for path in selected:
        tracker.note_dispatched(path)
    if not supplement_log_state["logged"]:
      supplement_log_state["logged"] = True
      tail_sample = [
          os.path.basename(str(path))
          for path in pending_tail_state[:5]
          if path
      ]
      log_print(
          "INFO: sync_timedb: giant pool supplement begin pending_tail_n=%d "
          "pending_tail_sample=%s in_flight_giants=%s selected=%s "
          "idle_slots=%d trigger_budget_s=%.0f"
          % (
              len(pending_tail_state),
              tail_sample,
              _format_giant_in_flight_snapshot(in_flight_paths),
              [os.path.basename(str(path)) for path in selected],
              int(slots_needed),
              float(cfg.get_sync_ingest_giant_pool_supplement_trigger_budget_s()),
          ),
          flush=True,
      )
    return selected

  def _on_in_flight_change(in_flight_paths):
    _update_sliding_window_stall_diagnostics(
        stall_diagnostics,
        in_flight_paths,
        inflight_cap,
    )

  def _on_idle_pool_ghost_fatal(pending_paths):
    if dispatch_registry is not None and pending_paths:
      clear_dispatch_worker_stages(dispatch_registry, pending_paths)

  def _on_reconcile_redispatch(path):
    if dispatch_registry is not None and path:
      seed_dispatch_worker_stages(dispatch_registry, [path])
    if tracker is not None and path:
      tracker.note_dispatched(path)

  def _on_idle_pool_stuck_after_redispatch(
      stuck_pool,
      pending_paths,
      pending_async,
      apply_fn,
  ):
    """Skip-or-recreate ingest Pool after full-redispatch thrash (exit-124 B1)."""
    unique_paths, duplicate_pending_n, duplicate_sample = (
        dedupe_ingest_paths_preserve_order(pending_paths)
    )
    sample_text = ",".join(duplicate_sample[:5]) if duplicate_sample else "-"
    log_print(
        "INFO: pool_recover skip_probe begin unique_pending_n=%d "
        "duplicate_pending_n=%d duplicate_sample=%s"
        % (len(unique_paths), duplicate_pending_n, sample_text),
        flush=True,
    )
    collected = []
    remaining = []
    skip_yes_n = 0
    skip_no_n = 0
    skip_no_sample = []
    for path in unique_paths:
      skip_item = None
      try:
        skip_item = _ingest_reconcile_skip_result(path)
      except Exception:
        skip_item = None
      if skip_item is not None:
        skip_yes_n += 1
        collected.append((path, skip_item))
      else:
        skip_no_n += 1
        if len(skip_no_sample) < 5:
          skip_no_sample.append(os.path.basename(str(path)))
        remaining.append(path)
    log_print(
        "INFO: pool_recover skip_probe done skip_yes_n=%d skip_no_n=%d "
        "skip_no_sample=%s"
        % (skip_yes_n, skip_no_n, skip_no_sample or "-"),
        flush=True,
    )
    # RC-F/G/H: do not clear pending_async until new pool probe OK.
    log_print("INFO: pool_recover terminate begin", flush=True)
    terminate_started = time.monotonic()
    terminate_pool_bounded(
        stuck_pool,
        timeout_s=30.0,
        context="idle_pool_recover",
        kill_workers_first=True,
        abandon_after_kill=True,
    )
    log_print(
        "INFO: pool_recover terminate elapsed_s=%.3f"
        % (time.monotonic() - terminate_started,),
        flush=True,
    )
    try:
      close_pool_bounded(stuck_pool, timeout_s=5.0, force_terminate=True)
    except Exception:
      pass
    log_print("INFO: pool_recover respawn begin", flush=True)
    initargs = (
        SYNC_TIMEDB_PROCESS_TITLE,
        "ingest-pool",
        dispatch_registry,
    )
    new_pool = create_sync_timedb_spawn_pool(
        processes=thread_count,
        initializer=apply_ingest_pool_worker_init,
        initargs=initargs,
        pool_kind_log_label="ingest-pool",
    )
    try:
      reclaim_excess_ingest_pool_children(
          new_pool,
          expected=thread_count,
          context="idle_pool_recover",
      )
    except Exception:
      pass
    alive_workers = alive_pool_worker_count(new_pool)
    if alive_workers <= 0:
      log_print(
          "ERROR: pool_recover respawn failed alive_workers=0",
          flush=True,
      )
      raise MultiprocessingPoolStallError(
          "replacement ingest pool has no alive workers",
          dead_pids=[],
          context="idle_pool_recover",
          exit_code=124,
          likely_cause="idle_pool_taskqueue_dead",
      )
    if not probe_ingest_pool_dispatch(
        new_pool,
        context="idle_pool_recover",
    ):
      raise MultiprocessingPoolStallError(
          "replacement ingest pool dispatch_probe failed",
          dead_pids=[],
          context="idle_pool_recover",
          exit_code=124,
          likely_cause="idle_pool_taskqueue_dead",
      )
    pool_health_context["ingest_pool"] = new_pool
    pool_health_context["active_pool"] = new_pool
    if callable(on_ingest_pool_replaced):
      try:
        on_ingest_pool_replaced(new_pool)
      except Exception:
        pass
    apply_async = getattr(new_pool, "apply_async", None)
    if not callable(apply_async):
      raise MultiprocessingPoolStallError(
          "replacement ingest pool missing apply_async",
          dead_pids=[],
          context="idle_pool_recover",
          exit_code=124,
          likely_cause="idle_pool_taskqueue_dead",
      )
    pending_async.clear()
    for path in remaining:
      if dispatch_registry is not None and path:
        seed_dispatch_worker_stages(dispatch_registry, [path])
      if tracker is not None and path:
        tracker.note_dispatched(path)
      pending_async[apply_async(apply_fn, (path,))] = path
    log_print(
        "INFO: pool_recover resubmit n=%d alive_workers=%d"
        % (len(remaining), alive_workers),
        flush=True,
    )
    return {"pool": new_pool, "collected": collected}

  def _current_ingest_pool():
    return pool_health_context.get("ingest_pool") or active_ingest_pool

  iterator = imap_sliding_window_watch_pool(
      pool,
      fn,
      paths,
      max_inflight=inflight_cap,
      context=context,
      stall_abort_polls_fn=_stall_abort_polls_for_batch,
      on_in_flight_change=_on_in_flight_change,
      supplement_paths_fn=_giant_pool_supplement_paths_fn,
      on_idle_pool_ghost_fatal=_on_idle_pool_ghost_fatal,
      on_reconcile_redispatch=_on_reconcile_redispatch,
      resolve_reconcile_skip_result=_ingest_reconcile_skip_result,
      on_idle_pool_stuck_after_redispatch=_on_idle_pool_stuck_after_redispatch,
      skip_idle_pool_recover_fn=lambda pending_paths: (
          idle_pool_recover_skip_reason_for_paths(
              pending_paths,
              tgz_archive_dir=tgz_archive_dir,
          )
      ),
      on_stall_warning=_make_ingest_stall_warning_fn(
          tracker,
          pool=_current_ingest_pool,
          thread_count=thread_count,
          chunk_counter=chunk_counter,
          pending_count=pending_count,
          stall_diagnostics=stall_diagnostics,
          progress_state=stall_poll_state,
      ),
      on_stall_poll=_make_ingest_stall_poll_fn(
          tracker,
          stall_poll_state,
          stall_diagnostics=stall_diagnostics,
          supervisor_reap_fn=_supervisor_reap,
      ),
      pool_health_context=pool_health_context,
      on_stall_fatal_summary=(
          lambda consecutive, abort_after, poll_timeout_s, ctx: _build_ingest_stall_log_suffix(
              sample=tracker.sample_in_flight() if tracker is not None else [],
              day_hint=_calendar_day_hint_from_paths(
                  tracker.sample_in_flight() if tracker is not None else [],
              ),
              stall_diagnostics=stall_diagnostics,
              progress_state=stall_poll_state,
              alive_workers=alive_pool_worker_count(_current_ingest_pool()),
              consecutive=consecutive,
              poll_timeout_s=poll_timeout_s,
          )
          if tracker is not None or stall_diagnostics is not None
          else ""
      ),
  )
  for item in iterator:
    if stall_diagnostics is not None:
      stall_diagnostics.note_imap_completion()
    completed_path = _imap_ingest_result_path(item)
    if dispatch_registry is not None and completed_path:
      clear_dispatch_worker_stages(dispatch_registry, [completed_path])
    yield item


def _clear_ingest_worker_file_caches():
  """Drop per-process host itimes caches after parse segments."""
  sync_timedb_host_itimes.reset_host_itimes_caches()


def _clear_ingest_worker_memory_caches():
  """Full per-task cache sweep including daily archive member L1."""
  _clear_ingest_worker_file_caches()
  clear_daily_archive_members_cache()


def _release_ingest_worker_heap():
  """Return parse heap to the OS on Linux (mid-task or end-of-task trim)."""
  _clear_ingest_worker_file_caches()
  if not cfg.get_sync_ingest_malloc_trim_after_file():
    return
  gc.collect()
  try:
    libc = ctypes.CDLL("libc.so.6")
    libc.malloc_trim(0)
  except (OSError, AttributeError):
    pass


def _release_ingest_worker_memory(stats_file=""):
  """Full per-task worker memory release; returns telemetry meta for supervisor."""
  from hpcperfstats.dbload.lib.sync_timedb_worker_memory import (
      release_spawn_pool_worker_memory,
  )

  release_spawn_pool_worker_memory()
  increment_worker_tasks_on_worker()
  return measure_worker_rss_after_release(stats_file)


def _worker_rss_mib():
  rss_bytes = read_process_rss_bytes()
  if rss_bytes <= 0:
    return 0.0
  return round(rss_bytes / (1024 * 1024), 1)


@dataclass
class SealedArchiveIngestProgress:
  """Per-sealed-day file counter for ``sync_timedb_archive`` completion logs."""

  total_files: int
  completed_files: int = 0


_sealed_archive_ingest_progress: contextvars.ContextVar = contextvars.ContextVar(
    "sealed_archive_ingest_progress",
    default=None,
)


def set_sealed_archive_ingest_progress(total_files: int) -> None:
  """Begin sealed-archive member progress (``sync_timedb_archive`` workers only)."""
  try:
    total = int(total_files)
  except (TypeError, ValueError):
    total = 0
  _sealed_archive_ingest_progress.set(
      SealedArchiveIngestProgress(total_files=max(0, total)),
  )


def clear_sealed_archive_ingest_progress() -> None:
  _sealed_archive_ingest_progress.set(None)


def advance_sealed_archive_ingest_progress(count=1) -> None:
  """Count sealed-archive members done without ingest (for example oversize skips)."""
  progress = _sealed_archive_ingest_progress.get()
  if progress is None or progress.total_files <= 0:
    return
  try:
    n = int(count)
  except (TypeError, ValueError):
    n = 0
  if n > 0:
    progress.completed_files += n


def _sealed_archive_ingest_remaining_pair():
  """Return ``(remaining, total)`` after incrementing completed, or ``None``."""
  progress = _sealed_archive_ingest_progress.get()
  if progress is None or progress.total_files <= 0:
    return None
  advance_sealed_archive_ingest_progress(1)
  remaining = max(0, progress.total_files - progress.completed_files)
  return remaining, progress.total_files


def _spawn_pool_recycle_kwargs():
  return sync_timedb_spawn_pool_recycle_kwargs(pool_kind_log_label="ingest-pool")

# Set to 1/yes/true so ingest runs in the parent process (no spawn pool). Required
# for pytest-django: pool workers would reconnect with default [DEFAULT] dbname instead
# of the test database created for the session.
_SYNC_TIMEDB_INGEST_INLINE_ENV = "HPCPERFSTATS_SYNC_TIMEDB_INGEST_INLINE"


def _sync_timedb_ingest_inline_requested():
  return os.environ.get(_SYNC_TIMEDB_INGEST_INLINE_ENV, "").strip().lower() in (
      "1", "yes", "true")

# Rows per bulk_create batch to limit peak memory per worker (see sync_bulk_create_batch_size).
def bulk_create_batch_size():
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
    ingest_pool,
    archive_pool,
    populate_pool_controller,
    *,
    context="chunk_boundary",
):
  """Reap dead pool workers and zombies; restart dead populate-pool workers."""
  reap_pool_worker_pids(ingest_pool, context="%s_ingest" % context)
  reap_pool_worker_pids(archive_pool, context="%s_archive" % context)
  reap_zombie_children_of_self(context=context)
  if populate_pool_controller is not None:
    try:
      populate_pool_controller.reap_and_restart()
    except Exception as exc:
      log_print(
          "WARN: populate-pool reap_and_restart failed: %s" % exc,
          flush=True,
      )
  warn_unreaped_zombie_children(context=context)


def _maybe_reap_supervisor_pool_children_throttled(
    ingest_pool,
    archive_pool,
    populate_pool_controller,
    *,
    context="throttled",
):
  """Run supervisor child hygiene at most once per ``_SUPERVISOR_CHILD_REAP_INTERVAL_S``."""
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


def _exit_on_archive_members_redis_unavailable(exc):
  """Fatal exit when Redis L2 contract fails during ingest or startup.

  Populate-pool-down / refuse-stream is recoverable (enqueue + wait / ensure
  pool) and must not map to immediate ``sys.exit(1)``.
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
def _sync_worker_db_task():
  """Refresh DB connections at worker task start and release them at end."""
  close_old_connections()
  try:
    yield
  finally:
    try:
      connections.close_all()
    except Exception:
      pass


def _ensure_daily_archive_dir_exists():
  """Create daily archive dir, tolerating races."""
  try:
    os.makedirs(tgz_archive_dir, exist_ok=True)
  except OSError:
    if not os.path.isdir(tgz_archive_dir):
      raise


def _count_daily_tars(daily_archive_dir):
  """Count daily ``.tar`` files in the archive directory."""
  if not daily_archive_dir or not os.path.isdir(daily_archive_dir):
    return 0
  return sum(1 for _ in iter_daily_tar_paths(daily_archive_dir))


def _log_db_lock_wait(batch_kind, stats_file, lock_wait):
  """Log DB lock contention only when wait exceeds threshold."""
  if lock_wait <= LOCK_WAIT_LOG_THRESHOLD_SECONDS:
    return
  log_print(
      "DB lock wait %s batch file=%s wait=%.3fs" % (
          batch_kind, stats_file, lock_wait
      ),
      flush=True,
  )



@dataclass(frozen=True)
class ArchiveTask:
  archive_info: tuple
  attempt: int = 1


@dataclass(frozen=True)
class ArchiveAppendOutcome:
  """Archive pool append result plumbed to supervisor finalize."""

  ok: bool = True
  redis_merge_ok: bool = False
  skip_finalize_invalidate: bool = True
  # Oversized paths skipped after convert_fail_skip (still finalized as archived).
  skipped_paths: tuple = ()

  def __bool__(self):
    return self.ok


def _archive_task_succeeded(result):
  if result is False or result is None:
    return False
  if isinstance(result, ArchiveAppendOutcome):
    return result.ok
  return bool(result)


def _archive_finalize_skip_invalidate_log_reason(result):
  if isinstance(result, ArchiveAppendOutcome) and result.redis_merge_ok:
    return "redis_merge_warm"
  return "no_tar_mutation_or_worker_invalidated"


def _shutdown_ingest_pools(ingest_pool, *, force_terminate=False):
  """Bounded shutdown for ingest pool (terminate after worker OOM/SIGKILL)."""
  close_pool_bounded(ingest_pool, force_terminate=force_terminate)


def _cap_pending_stats_files_list(paths, ingest_queue_max):
  return cap_pending_stats_file_list(paths, ingest_queue_max, log_fn=log_print)


def _maybe_exit_on_supervisor_rss_limit(chunk_counter):
  """Fail fast with exit 137 when supervisor RSS exceeds configured limit."""
  limit_mb = cfg.get_sync_supervisor_rss_limit_mb()
  if limit_mb <= 0:
    return
  every_n = cfg.get_sync_supervisor_rss_check_every_n_chunks()
  if int(chunk_counter) % every_n != 0:
    return
  rss_bytes = read_process_rss_bytes()
  limit_bytes = int(limit_mb) * 1024 * 1024
  if rss_bytes <= 0 or rss_bytes <= limit_bytes:
    return
  log_print(
      "ERROR: sync_timedb supervisor RSS %.1f MiB exceeds limit %d MiB; exiting"
      % (rss_bytes / (1024.0 * 1024.0), limit_mb),
      flush=True,
  )
  raise SystemExit(137)


def _maybe_wait_tree_rss_before_chunk(ingest_pool, archive_pool):
  """Defer starting a new chunk while the process tree is over the RSS cap."""
  limit_mb = cfg.get_sync_process_tree_rss_limit_mb()
  if limit_mb <= 0:
    return
  limit_bytes = int(limit_mb) * 1024 * 1024
  for attempt in range(60):
    tree_bytes = read_sync_timedb_tree_rss_bytes(ingest_pool, archive_pool)
    if tree_bytes <= 0 or tree_bytes <= limit_bytes:
      return
    if attempt == 0:
      breakdown = format_tree_rss_breakdown_mb(ingest_pool, archive_pool)
      log_print(
          "sync_timedb tree RSS %.1f MiB exceeds limit %d MiB "
          "(supervisor=%.1f ingest=%.1f archive=%.1f); "
          "deferring chunk dispatch"
          % (
              breakdown["tree_total_mb"],
              limit_mb,
              breakdown["supervisor_mb"],
              breakdown["ingest_pool_mb"],
              breakdown["archive_pool_mb"],
          ),
          flush=True,
      )
    time.sleep(_TREE_RSS_DEFER_SLEEP_SECONDS)


def _maybe_apply_tree_rss_governor(
    chunk_counter,
    ingest_pool,
    archive_pool,
):
  """Tree RSS backpressure and optional hard exit after each chunk."""
  every_n = cfg.get_sync_process_tree_rss_check_every_n_chunks()
  if int(chunk_counter) % every_n != 0:
    return
  exit_mb = cfg.get_sync_process_tree_rss_exit_mb()
  limit_mb = cfg.get_sync_process_tree_rss_limit_mb()
  if exit_mb <= 0 and limit_mb <= 0:
    _maybe_exit_on_supervisor_rss_limit(chunk_counter)
    return
  tree_bytes = read_sync_timedb_tree_rss_bytes(ingest_pool, archive_pool)
  if exit_mb > 0 and tree_bytes > int(exit_mb) * 1024 * 1024:
    breakdown = format_tree_rss_breakdown_mb(ingest_pool, archive_pool)
    log_print(
        "ERROR: sync_timedb process tree RSS %.1f MiB exceeds exit cap %d MiB "
        "(supervisor=%.1f ingest=%.1f archive=%.1f); exiting"
        % (
            breakdown["tree_total_mb"],
            exit_mb,
            breakdown["supervisor_mb"],
            breakdown["ingest_pool_mb"],
            breakdown["archive_pool_mb"],
        ),
        flush=True,
    )
    raise SystemExit(137)
  if limit_mb <= 0:
    _maybe_exit_on_supervisor_rss_limit(chunk_counter)


def _prior_day_tars_from_archive_mapping(ar_file_mapping, *, local_tz):
  """Normalized prior-calendar daily ``.tar`` paths referenced by chunk mapping."""
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
    exc,
    *,
    ingest_pool=None,
    archive_pool=None,
):
  """``os._exit`` immediately — do not wait on pool terminate or context managers."""
  del ingest_pool, archive_pool
  hard_exit_pool_worker_error(exc)


def _reraise_or_handle_pool_worker_exit(
    exc,
    *,
    ingest_pool,
    archive_pool=None,
):
  """Terminate ingest/archive pools and re-raise worker death."""
  if isinstance(exc, MultiprocessingWorkerExitError):
    ctx = getattr(exc, "context", "") or "pool_worker_exit"
    terminate_pool_bounded(ingest_pool, context=ctx)
    terminate_pool_bounded(archive_pool, context=ctx)
    raise
  raise exc



class SyncFileState(str, Enum):
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
    file_states,
    path,
    new_state,
    *,
    handoff_priority_paths=None,
):
  """Best-effort state transition validator for per-file supervisor state."""
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


def _load_dead_letter_entries(path):
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


def _save_dead_letter_entries(path, entries):
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


def _host_recent_timestamps_cached(hostname, ts_low, ts_high):
  return sync_timedb_host_itimes.host_recent_timestamps_cached(
      hostname, ts_low, ts_high)


def _pick_write_lock_for_path(lock_or_locks, stats_file):
  if isinstance(lock_or_locks, list) and lock_or_locks:
    idx = abs(hash(stats_file)) % len(lock_or_locks)
    return lock_or_locks[idx]
  return lock_or_locks


def _host_timestamp_second_present_in_db(host, unix_second):
  return sync_timedb_host_itimes.host_timestamp_second_present_in_db(
      host, unix_second)


def _reset_sync_runtime_caches():
  """Clear per-process ingest caches between sync_timedb sessions."""
  reset_sync_ingest_readiness_caches()
  sync_timedb_host_itimes.reset_host_itimes_caches()


def _should_stream_stats_file(stats_file, stats_file_contents):
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


def _timestamp_second_present_for_duplicate(host, unix_second, timestamp_utc):
  """Return whether ``unix_second`` for ``host`` is present in DB (indexed exists probe)."""
  del timestamp_utc  # kept for call-site stability; wide itimes window not needed here
  return _host_timestamp_second_present_in_db(host, unix_second)


def _try_db_complete_head_tail_fast_path(
    stats_file,
    host,
    head_timestamp_utc,
    *,
    lines=None,
):
  """When head and tail seconds are in DB, skip full duplicate scan (returns start_idx=-1)."""
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
    stats_file,
    host,
    timestamp_utc,
    *,
    itimes_set=None,
    timestamp_present=None,
):
  """Bounded tail-line probe for large head-present files before full duplicate scan."""
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

      def _timestamp_present_with_budget(unix_second):
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


def _calendar_days_touched_by_paths(paths):
  days = set()
  for path in paths or ():
    day = _calendar_day_hint_from_paths([path])
    if day:
      days.add(day)
  return days


def _completed_ingest_calendar_days(*, chunk_paths, pending_before, pending_after):
  """Calendar days with no remaining pending ingest paths after this chunk."""
  touched = _calendar_days_touched_by_paths(list(chunk_paths) + list(pending_before))
  if not touched:
    return []
  still_pending = _calendar_days_touched_by_paths(pending_after)
  return sorted(day for day in touched if day not in still_pending)


def _invalidate_jid_caches(stats, proc_stats):
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


def _write_stats_payload_to_db(lock, stats_file, stats, proc_stats, need_archival=True):
  """Persist parsed payload into DB using fixed-size batches and lock sharding."""
  update_worker_substage("db_write")
  write_lock = _pick_write_lock_for_path(lock, stats_file)
  try:
    try:
      proc_it = proc_stats.itertuples(index=False)
      while True:
        _raise_if_ingest_per_file_deadline_exceeded(stats_file, "db_write_proc")
        batch = list(itertools.islice(proc_it, bulk_create_batch_size()))
        if not batch:
          break
        proc_objs = [
            proc_data(jid=row.jid, host=row.host, proc=row.proc) for row in batch
        ]
        lock_wait_t0 = time.time()
        write_lock.acquire()
        lock_wait = time.time() - lock_wait_t0
        _log_db_lock_wait("proc", stats_file, lock_wait)
        _raise_if_ingest_per_file_deadline_exceeded(stats_file, "db_write_proc")
        try:
          proc_data.objects.bulk_create(proc_objs, ignore_conflicts=True)
        finally:
          write_lock.release()
    except Exception as e:
      if is_database_unavailable_error(e):
        log_and_raise_database_unavailable(
            e, context="sync_timedb proc_data bulk_create"
        )
      if DEBUG:
        log_print("error in proc_data bulk_create: %s\nFile %s" % (e, stats_file))
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
          lock_wait_t0 = time.time()
          write_lock.acquire()
          lock_wait = time.time() - lock_wait_t0
          _log_db_lock_wait("host", stats_file, lock_wait)
          _raise_if_ingest_per_file_deadline_exceeded(stats_file, "db_write_host")
          try:
            host_data.objects.bulk_create(host_objs, ignore_conflicts=True)
          finally:
            write_lock.release()
    except Exception as e:
      if is_database_unavailable_error(e):
        log_and_raise_database_unavailable(
            e, context="sync_timedb host_data bulk_create"
        )
      if DEBUG:
        log_print("error in host_data bulk_create:", str(e))
      need_archival = _insert_host_data_individually(stats)
  except Exception:
    raise

  _invalidate_jid_caches(stats, proc_stats)
  if DEBUG:
    log_print("File successfully added to DB")
  return (stats_file, need_archival, True)


def _ingest_remaining_count(pending_total, chunk_ingest_finished):
  return max(0, int(pending_total) - int(chunk_ingest_finished) - 1)


def _ingest_path_is_supplement(stats_fname, chunk_paths_norm):
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


def _db_skip_token_from_complete_reason(reason):
  if not reason:
    return "no"
  return _DB_COMPLETE_REASON_TO_SKIP.get(str(reason), "no")


def _ingest_outcome_meta(**kwargs):
  return {key: value for key, value in kwargs.items() if value is not None}


@dataclass
class IngestFileOutcome:
  path: str
  elapsed_s: float
  ingest_ok: bool
  need_archival: bool
  outcome: str
  db_skip: str = "no"
  parse_elapsed_s: float | None = None
  stats_rows: int | None = None
  proc_rows: int | None = None
  fail_reason: str | None = None
  archive_skip: str | None = None


def _archive_skip_token_for_outcome(outcome):
  """Infer archive log token when meta omitted but archival was skipped."""
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


def _need_archival_and_archive_skip_meta(stats_file, first_ts):
  """Tar-append decision for DB-complete ingest; returns meta fragment."""
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
    stats_file,
    need_archival,
    ingest_ok,
    elapsed_s,
    outcome_meta=None,
):
  meta = dict(outcome_meta or {})
  return (stats_file, need_archival, ingest_ok, float(elapsed_s), meta)


def _unpack_ingest_worker_result(result):
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


def _unpack_parse_payload_result(result):
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
    stats_file,
    need_archival,
    ingest_ok,
    elapsed_s,
    outcome_meta,
):
  meta = dict(outcome_meta or {})
  outcome = str(meta.get("outcome") or "")
  if not outcome:
    if ingest_ok:
      outcome = "ingested" if meta.get("stats_rows") else "db_skip"
    else:
      outcome = "parse_fail"
  db_skip = str(meta.get("db_skip") or "no")
  return IngestFileOutcome(
      path=str(stats_file or ""),
      elapsed_s=float(elapsed_s),
      ingest_ok=bool(ingest_ok),
      need_archival=bool(need_archival),
      outcome=outcome,
      db_skip=db_skip,
      parse_elapsed_s=meta.get("parse_elapsed_s"),
      stats_rows=meta.get("stats_rows"),
      proc_rows=meta.get("proc_rows"),
      fail_reason=meta.get("fail_reason"),
      archive_skip=meta.get("archive_skip"),
  )


def _log_ingest_file_outcome(
    outcome,
    *,
    remaining=None,
    supplement=False,
):
  parts = [
      "ingest file path=%s" % outcome.path,
      "outcome=%s" % outcome.outcome,
      "elapsed_s=%.1f" % float(outcome.elapsed_s),
      "ingest_ok=%s" % ("yes" if outcome.ingest_ok else "no"),
      "archive=%s" % _archive_skip_token_for_outcome(outcome),
      "db_skip=%s" % (outcome.db_skip or "no"),
      "size_bytes=%d" % stats_file_size_bytes(outcome.path),
  ]
  if outcome.parse_elapsed_s is not None:
    parts.append("parse_elapsed_s=%.1f" % float(outcome.parse_elapsed_s))
  if outcome.stats_rows is not None:
    parts.append("stats_rows=%d" % int(outcome.stats_rows))
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


def _log_ingest_worker_result(result, *, remaining=None, supplement=False):
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
  from hpcperfstats.dbload.lib.sync_timedb_zero_host_ingest_mark import (
      maybe_record_zero_host_ingest_mark_from_outcome,
  )
  maybe_record_zero_host_ingest_mark_from_outcome(
      stats_file,
      ingest_ok=outcome.ingest_ok,
      outcome=outcome.outcome,
      stats_rows=outcome.stats_rows,
      log_fn=log_print,
  )


def _quarantine_failed_ingest_parse(stats_file, error_detail=None):
  """Move permanently unparseable closed raw into DLO; return True when handled."""
  archive_dir = cfg.get_archive_dir_path()
  if not archive_dir:
    return False
  return quarantine_ingest_failed_raw_path(
      stats_file,
      archive_dir,
      INGEST_PARSE_FAILED_QUARANTINE_REASON,
      error_detail=error_detail,
  )


def _parse_failure_after_quarantine(stats_file, parse_elapsed, error_detail=None):
  """Quarantine on permanent parse failure; ingest_ok=True when DLO move succeeds."""
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


def _parse_stats_file_payload(stats_file, stats_file_contents=None, *, use_ingest_timer=True):
  """Parse stats file into payload for deferred DB writer stage.

  Returns (stats_file, payload, need_archival, ingest_ok, parse_elapsed_s).
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
    stats_file,
    *,
    host,
    timestamp_utc,
    lines=None,
):
  """Return (start_idx, need_archival) for duplicate detection."""
  ts_low = timestamp_utc - timedelta(hours=48)
  ts_high = timestamp_utc + timedelta(hours=72)
  itimes_set = _host_recent_timestamps_cached(host, ts_low, ts_high)
  timestamp_present = None
  if itimes_set is _HOST_ITIMES_SET_OVERFLOW:
    itimes_set = None
    overflow_logged = {"done": False}
    probe_count = {"n": 0}
    max_overflow_probes = cfg.get_sync_host_itimes_cache_max_timestamps_per_entry()

    def _timestamp_present_with_budget(unix_second):
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


def _resolve_streaming_ingest_start(stats_file, parse_elapsed_fn):
  """Duplicate scan for streaming-eligible segments.

  Returns ``(True, early_return)`` when parse can be skipped (including failures
  encoded as a 5-tuple), or ``(False, (start_line_idx, need_archival))`` when
  parsing should proceed.
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


def _ingest_reconcile_skip_result(stats_file):
  """Return an ingest result tuple when DB idempotency says re-dispatch is unnecessary."""
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


def _parse_stats_file_payload_impl_streaming(stats_file):
  """Bounded-memory parse path for segments larger than ``sync_ingest_max_file_read_bytes``."""
  parse_t0 = time.time()

  def _parse_elapsed():
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


def _add_stats_file_to_db_streaming_incremental(lock, stats_file, t0):
  """Parse → DB → parse loop for large segments (combined ingest only)."""
  parse_t0 = time.time()
  carry = DeltaCarryState()
  total_stats_rows = 0
  total_proc_rows = 0
  need_archival = True
  ingest_ok = True
  flush_rows = bulk_create_batch_size()

  def _parse_elapsed():
    return time.time() - parse_t0

  def _on_chunk(stats_list, proc_stats_list):
    nonlocal need_archival, ingest_ok, total_stats_rows, total_proc_rows
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
          parse_stats_file_streaming_incremental(
              stats_file,
              start_line_idx=start_line_idx,
              parse_start_idx=0,
              flush_rows=flush_rows,
              on_chunk=_on_chunk,
              exclude_types_list=exclude_types,
          )
        except Exception as e:
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
        if total_stats_rows == 0 and total_proc_rows == 0:
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
          proc_rows=total_proc_rows,
      )
      return _pack_ingest_worker_result(
          stats_file, need_archival, ingest_ok, elapsed, meta,
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


def _parse_stats_file_payload_impl(stats_file, stats_file_contents=None):
  """Implementation for :func:`_parse_stats_file_payload` (parse stage only)."""
  lines = None
  parse_t0 = time.time()

  def _parse_elapsed():
    return time.time() - parse_t0

  with _sync_worker_db_task():
    try:
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


def _db_writer_worker(lock, db_task):
  """Worker entrypoint for DB-writer pool.

  db_task is (stats_file, payload, need_archival, parse_elapsed_s).
  Returns (stats_file, need_archival, ingest_ok, total_elapsed_s).
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


def _db_writer_worker_impl(lock, db_task):
  """Implementation for :func:`_db_writer_worker` (DB write stage only)."""
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


def _ingest_parse_and_write_file(lock, stats_file, stats_file_contents=None):
  """Combined ingest worker: parse and DB write in one pool task (small parent tuple)."""
  return add_stats_file_to_db(
      lock, stats_file, stats_file_contents=stats_file_contents
  )


# This routine will read the file until a timestamp is read that is not in the database. It then reads in the rest of the file.
def add_stats_file_to_db(lock, stats_file, stats_file_contents=None):
  """Parse a stats file, map hardware counters, compute deltas/arc, and bulk-insert into host_data and proc_data.

  Returns (stats_file, need_archival, ingest_ok, elapsed_s) where elapsed_s is wall
  seconds for the attempted ingest path. Uses lock for DB writes.

    """
  result = None
  record_worker_stage(stats_file, "ingest", substage="worker_entry")
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
          _ingest_outcome_meta(
              outcome="timeout",
              fail_reason=exc.stage,
              archive_skip="timeout",
          ),
      )
    except IngestArchiveLookupBudgetExceededError as exc:
      _log_ingest_archive_lookup_budget_exceeded(exc)
      result = _pack_ingest_worker_result(
          stats_file,
          False,
          False,
          0.0,
          _ingest_outcome_meta(outcome="lookup_budget", archive_skip="lookup_budget"),
      )
  finally:
    mem_meta = _release_ingest_worker_memory(stats_file)
    if result is not None:
      result = _merge_worker_memory_meta(result, mem_meta)
  return result


def _add_stats_file_to_db_impl(lock, stats_file, stats_file_contents=None):
  """Implementation for :func:`add_stats_file_to_db` (parse + write combined)."""
  stats = None
  proc_stats = None
  payload = None
  t0 = time.time()
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
          stats_file, need_archival, ingest_ok, elapsed_total, meta,
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


def _load_sync_checkpoint(state_path):
  """Load checkpoint entries from persistence envelope, returning [] on invalid."""
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


def _save_sync_checkpoint(state_path, completed_entries):
  """Atomically save checkpoint entries via persistence API."""
  save_persistence_document(
      state_path,
      "ingest_checkpoint",
      list(completed_entries),
  )


def _load_json_list(path):
  """Load JSON list content; return None when invalid/unreadable."""
  try:
    with open(path, "r", encoding="utf-8") as fh:
      raw = json.load(fh)
  except (OSError, ValueError, TypeError):
    return None
  if not isinstance(raw, list):
    return None
  return raw


def _save_json_atomic(path, payload):
  """Atomically persist JSON payload to path with parent mkdir."""
  parent = os.path.dirname(str(path))
  if parent:
    os.makedirs(parent, exist_ok=True)
  tmp_path = "%s.tmp" % path
  with open(tmp_path, "w", encoding="utf-8") as fh:
    json.dump(payload, fh)
  os.replace(tmp_path, path)


def _path_fingerprint(path):
  """Return path fingerprint used for restart-safe processed tracking."""
  try:
    return {
        "path": path,
        "size": int(os.path.getsize(path)),
        "mtime": int(os.path.getmtime(path)),
    }
  except OSError:
    return None


def _add_processed_path(
    path,
    processed_files,
    processed_files_order,
    checkpoint_entries,
    checkpoint_path,
    *,
    file_states=None,
    handoff_priority_paths=None,
):
  """Record processed path in memory and checkpoint buffer."""
  fp = _path_fingerprint(path)
  if fp is None:
    return False
  processed_files.add(path)
  processed_files_order.append(path)
  checkpoint_entries.append(fp)
  if handoff_priority_paths is not None:
    handoff_priority_paths.discard(path)
  while len(processed_files_order) > processed_files_max_size:
    old_path = processed_files_order.popleft()
    processed_files.discard(old_path)
    if file_states is not None:
      file_states.pop(old_path, None)
  while len(checkpoint_entries) > processed_files_max_size:
    checkpoint_entries.popleft()
  return True


def _remove_processed_path(
    path,
    processed_files,
    processed_files_order,
    checkpoint_entries,
    checkpoint_path,
    *,
    file_states=None,
    host_scan_hints=None,
    persist=True,
):
  """Undo checkpoint/processed markers so a path re-enters the ingest loop."""
  path = str(path)
  processed_files.discard(path)
  try:
    processed_files_order.remove(path)
  except ValueError:
    pass
  fp = _path_fingerprint(path)
  if fp is not None:
    kept = deque()
    for entry in checkpoint_entries:
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
    kept = deque(entry for entry in checkpoint_entries if entry.get("path") != path)
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
    paths,
    processed_files,
    processed_files_order,
    checkpoint_entries,
    checkpoint_path,
    *,
    file_states=None,
    host_scan_hints=None,
    persist=True,
):
  """Single-pass checkpoint clear for a path set (handoff hot path)."""
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
  for entry in checkpoint_entries:
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


def _insert_proc_data_individually(proc_stats_df):
  """Fallback: insert proc_data rows one by one, skipping duplicates.

    """
  unique_violations = _insert_rows_individually(
      rows=proc_stats_df.itertuples(index=False),
      save_row=lambda row: proc_data(jid=row.jid, host=row.host, proc=row.proc).save(),
      error_prefix="error in single proc_data insert:",
  )
  if DEBUG:
    log_print("Existing Rows Found in DB: %s" % unique_violations)


def _insert_host_data_individually(stats_df):
  """Fallback: insert host_data rows one by one, skipping duplicates. Returns need_archival.

    """
  need_archival = True
  unique_violations = 0
  with warnings.catch_warnings():
    warnings.filterwarnings(
        "ignore",
        message=".*[Dd]iscarding nonzero nanoseconds.*",
        category=UserWarning,
    )
    def _save_host_row(row):
      # force_insert: avoid Django's "pk exists -> UPDATE" path. Updates on
      # compressed Timescale chunks decompress huge tuple batches and hit
      # timescaledb.max_tuples_decompressed_per_dml_transaction; duplicates
      # should be skipped (IntegrityError), not merged via UPDATE.
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
    rows,
    save_row,
    error_prefix,
    return_non_integrity_errors=False,
):
  """Insert rows one-by-one and count duplicate violations."""
  unique_violations = 0
  non_integrity_errors = 0
  for row in rows:
    try:
      save_row(row)
    except IntegrityError:
      unique_violations += 1
    except Exception as e:
      non_integrity_errors += 1
      log_print(error_prefix, str(e), "row:", row)
  if return_non_integrity_errors:
    return unique_violations, non_integrity_errors
  return unique_violations




def _decompress_compressed_archive(archive_compressed_path):
  """Decompress ``.tar.zst`` or legacy ``.tar.gz`` to sibling ``.tar``.

  Returns True when a verified sibling ``.tar`` exists afterward.
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


def _restore_daily_tar_or_log_failure(archive_tar_fname, *, context):
  if ensure_daily_tar_restored_for_append(
      archive_tar_fname, cfg.get_archive_zstd_threads()):
    return True
  log_print(
      "ERROR: could not restore daily tar %s; leaving raw stats files in place: %s"
      % (context, archive_tar_fname),
      flush=True,
  )
  return False


def format_tar_append_failure_log(tar_path, exc, *, retry=False):
  """Build ERROR line for tar append failure; fold CalledProcessError.stderr."""
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


def _append_to_tar(tar_path, file_paths):
  """Append file_paths to tar at tar_path. Does nothing if file_paths is empty.

  Uses GNU/BSD ``tar -r -f`` with ``-C /``, ``--null -T`` and relative member
  paths so argv stays tiny and absolute ``-T`` path warnings are avoided.
  Always passes ``--posix`` (pax) so members larger than 8 GiB - 1 succeed on
  pax-capable archives. Skips paths that disappeared before append (race).
  Batches via ``tar_append_batch_size``.
  """
  if not file_paths:
    return
  tar_exists = os.path.exists(tar_path)
  zst_path, gz_path = compressed_sibling_paths(tar_path)
  if not tar_exists and (os.path.isfile(zst_path) or os.path.isfile(gz_path)):
    raise RuntimeError(
        "refusing to create daily tar while sealed archive exists without "
        "restored sibling: %s" % tar_path,
    )
  # Amortize subprocess overhead for large archive bursts.
  batch = max(1, int(tar_append_batch_size))
  if len(file_paths) >= 512:
    batch = max(batch, 256)
  elif len(file_paths) >= 128:
    batch = max(batch, 128)
  for off in range(0, len(file_paths), batch):
    chunk = file_paths[off : off + batch]
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
    fd, list_path = tempfile.mkstemp(prefix="hps_tar_append_", suffix=".lst")
    try:
      with os.fdopen(fd, "wb") as lf:
        for p in present:
          # Member path relative to ``-C /`` (no leading slash in -T file).
          abs_p = os.path.abspath(p)
          rel = abs_p[1:] if abs_p.startswith(os.sep) else abs_p
          lf.write(os.fsencode(rel) + b"\0")
      tar_bin = shutil.which("tar") or "/bin/tar"
      with file_write_lock(tar_path):
        tar_args = [
            tar_bin,
            "-r" if tar_exists else "-c",
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
        )
    finally:
      try:
        os.remove(list_path)
      except OSError:
        pass
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
    log_print(
        "Archived batch %d-%d (%d file(s)) -> %s"
        % (off + 1, off + len(present), len(present), tar_path),
        flush=True,
    )
    tar_exists = True


def archive_stats_files(archive_info):
  """Append stats files to a daily ``.tar`` (verify, recover, dedupe).

  zstd sealing and removal of raw stats run on the ``ArchiveJanitor`` cold path
  (startup + every ``rescan_every_chunks`` ingest chunks), not after each append.
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


def _lookup_existing_members_for_archive_append(archive_fname, archive_tar_fname):
  """Return (members, members_source) for archive append; Redis-first when L2 on."""
  if not os.path.exists(archive_tar_fname):
    return {}, "tar_scan"
  canonical = normalize_daily_compressed_path(archive_fname)
  if cfg.get_sync_archive_members_redis_enabled():
    from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
        archive_members_redis_enabled,
        build_archive_members_redis_keys,
        redis_members_cache_is_fully_warm,
    )
    from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
        _daily_archive_members_cache_key,
    )
    if archive_members_redis_enabled():
      keys = build_archive_members_redis_keys(
          _daily_archive_members_cache_key(canonical),
      )
      members_source = (
          "redis" if redis_members_cache_is_fully_warm(keys) else "tar_scan"
      )
      from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
          archive_pre_append_member_lookup_context,
      )
      with archive_pre_append_member_lookup_context():
        members = get_existing_archive_members_for_daily_archive(canonical)
      return members, members_source
  return get_existing_archive_members(archive_tar_fname), "tar_scan"


def _log_archive_job_begin(archive_tar_fname, members_source):
  day_token = calendar_date_from_daily_tar_path(archive_tar_fname) or "?"
  tar_bytes = os.path.getsize(archive_tar_fname) if os.path.isfile(archive_tar_fname) else 0
  log_print(
      "INFO: archive_job_begin day=%s tar_bytes=%s members_source=%s"
      % (day_token, tar_bytes, members_source),
      flush=True,
  )


def _archive_stats_files_body(archive_info):
  archive_fname, stats_files = archive_info
  archive_tar_fname = daily_tar_path_from_compressed(archive_fname)
  day_token = calendar_date_from_daily_tar_path(archive_tar_fname) or "?"
  job_start = time.monotonic()
  job_begin_logged = False
  job_outcome = "fail"
  members_source = "tar_scan"
  append_inflight_set = False
  skipped_oversized = ()

  def _ensure_job_begin_logged(source):
    nonlocal job_begin_logged
    if not job_begin_logged:
      _log_archive_job_begin(archive_tar_fname, source)
      job_begin_logged = True

  try:
    stats_files, _skipped = filter_paths_head_ingested(stats_files, log_fn=log_print)
    if not stats_files:
      job_outcome = "ok"
      return ArchiveAppendOutcome(skip_finalize_invalidate=True)
    from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
        set_archive_append_inflight,
    )
    set_archive_append_inflight(day_token, reason="archive_job")
    append_inflight_set = True
    existing_members = {}
    zst_path, gz_path = compressed_sibling_paths(archive_tar_fname)
    sealed_exists = os.path.isfile(zst_path) or os.path.isfile(gz_path)

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
    if os.path.exists(archive_tar_fname):
      existing_members, members_source = _lookup_existing_members_for_archive_append(
          archive_fname, archive_tar_fname,
      )
      _ensure_job_begin_logged(members_source)

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
          "Daily tar unreadable before append; recovering from sealed archive or "
          "clearing: %s" % archive_tar_fname,
          flush=True,
      )
      if not replace_corrupt_tar_from_compressed_backup(
          archive_tar_fname, zst_path, gz_path, cfg.get_archive_zstd_threads(),
      ):
        log_print(
            "ERROR: could not restore daily tar before append; leaving raw stats "
            "files in place: %s" % archive_fname,
            flush=True,
        )
        return False
      if os.path.exists(archive_tar_fname):
        existing_members, members_source = _lookup_existing_members_for_archive_append(
            archive_fname, archive_tar_fname,
        )
        _ensure_job_begin_logged(members_source)
      else:
        existing_members = {}

    mapped_n = len(stats_files)
    stats_files_to_tar = filter_files_to_add_to_archive(
        stats_files, existing_members, debug=DEBUG)
    to_add_n = len(stats_files_to_tar)
    appended_n = 0
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
        log_print(
            "INFO: tar_append redis merge day=%s members=%d"
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
      log_print(
          "INFO: archive_job_duty day=%s mapped=%d to_add=%d appended=%d"
          % (day_token, mapped_n, to_add_n, appended_n),
          flush=True,
      )
      return ArchiveAppendOutcome(
          redis_merge_ok=merged,
          skip_finalize_invalidate=merged or worker_invalidated,
          skipped_paths=skipped_oversized,
      )
    job_outcome = "ok"
    log_print(
        "INFO: archive_job_duty day=%s mapped=%d to_add=%d appended=%d"
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
          "INFO: archive_job_done day=%s elapsed_s=%.3f outcome=%s"
          % (day_token, time.monotonic() - job_start, job_outcome),
          flush=True,
      )


def _build_fallback_archive_mapping_by_mtime(files_to_be_archived, tgz_dir):
  """Best-effort fallback mapping when timestamp-head parsing fails.

  Buckets by local file mtime date so archival can still progress and raw files
  are not stranded indefinitely.
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


def _normalize_archive_groups_by_tgz(archive_mapping):
  """Return stable per-tgz archive tasks as ``[(tgz_path, [paths...]), ...]``.

  The archival pipeline is intentionally threaded by tgz group (one task per
  ``YYYY-MM-DD.tar.zst`` path) so each worker handles a complete archive group
  rather than interleaving members across unrelated tgz files.
  """
  if not archive_mapping:
    return []
  tasks = []
  for tgz_path in sorted(archive_mapping):
    tasks.append((tgz_path, list(archive_mapping[tgz_path])))
  return tasks

def database_startup():
  """Print DB version, database size, and optionally chunk compression stats for host_data."""
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


def run_sync_timedb_supervisor_loop(
    directory,
    startdate,
    enddate,
    host_name_ext,
    manager_lock,
    archive_pool,
    run_once=False,
):
  """Rescan archive, ingest pending files in chunks; cold-path work on janitor thread.

  If ``run_once`` is True, exit after the first rescan that finds no pending files
  (no ``EMPTY_QUEUE_RESCAN_SLEEP_SECONDS`` idle wait). Used by pipeline E2E tests.
  """
  ingest_queue_max = max(1, int(cfg.get_sync_ingest_queue_max_size()))
  archive_queue_max = max(1, int(cfg.get_sync_archive_queue_max_size()))
  archive_retry_max_attempts = max(1, int(cfg.get_sync_archive_retry_max_attempts()))
  archive_retry_backoff_base = max(0.0, float(cfg.get_sync_archive_retry_backoff_base_seconds()))
  archive_retry_backoff_max = max(0.0, float(cfg.get_sync_archive_retry_backoff_max_seconds()))
  ingest_first_durability = bool(cfg.get_sync_enable_ingest_first_durability_mode())
  ingest_t0 = time.time()
  startup_gate_cleared_t0 = None
  run_startup_maintenance = startdate == "all"
  newest_first = startdate == "current"
  proximity_days = int(cfg.get_sync_ingest_current_proximity_days())

  def _day_chunk_gate_prefix():
    return "youngest_day_chunk_gate" if newest_first else "oldest_day_chunk_gate"

  def _day_gate_tar_key():
    return "youngest_tar" if newest_first else "oldest_tar"

  def _optional_heartbeat_redis_client():
    try:
      from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
          get_archive_members_redis_client,
      )
      return get_archive_members_redis_client(required=False)
    except Exception:
      return None

  def _publish_current_mode_heartbeat(active_paths):
    if not newest_first:
      return None
    try:
      return mode_heartbeat.publish_current_heartbeat(
          archive_dir=directory,
          active_paths=active_paths,
          daily_archive_dir=tgz_archive_dir,
          redis_client=_optional_heartbeat_redis_client(),
      )
    except Exception as exc:
      log_print(
          "sync_timedb: current heartbeat publish failed err=%s" % exc,
          flush=True,
      )
      return None

  def _all_should_exit_for_current_proximity(pending_paths):
    if startdate != "all" or not pending_paths:
      return False
    next_path = pending_paths[0]
    next_day = mode_heartbeat.calendar_day_from_stats_path(
        next_path, tgz_archive_dir)
    if next_day is None:
      return False
    try:
      heartbeat = mode_heartbeat.read_current_heartbeat(
          archive_dir=directory,
          redis_client=_optional_heartbeat_redis_client(),
      )
    except Exception:
      return False
    if not mode_heartbeat.should_all_exit_for_current_proximity(
        next_pending_day=next_day,
        heartbeat=heartbeat,
        proximity_days=proximity_days,
    ):
      return False
    log_print(
        "sync_timedb: all exiting near current"
        " next_pending_day=%s current_oldest_active_day=%s"
        " proximity_days=%d"
        % (
            next_day.isoformat(),
            heartbeat.get("oldest_active_day"),
            proximity_days,
        ),
        flush=True,
    )
    return True

  pending_archive_tasks = []
  _archive_heap_seq = itertools.count()
  dead_letter_dirty = False
  perf_stats = {
      "archive_finalize_wait_s": 0.0,
      "archive_finalize_calls": 0,
      "archive_dispatch_count": 0,
      "archive_dispatch_items": 0,
      "archive_dispatch_s": 0.0,
      "archive_worker_stall_events": 0,
  }

  def _retry_delay(attempt):
    delay = archive_retry_backoff_base * (2 ** max(0, attempt - 1))
    if archive_retry_backoff_max > 0:
      delay = min(delay, archive_retry_backoff_max)
    return max(0.0, delay)

  def _pending_dead_letter_entries():
    return sorted(
        [
            item
            for _retry_at, _attempt, _seq, item in pending_archive_tasks
            if item["task"].attempt > archive_retry_max_attempts
        ],
        key=lambda d: (float(d.get("retry_at", 0.0)), d["task"].attempt),
    )

  def _persist_dead_letters_if_needed(force=False):
    nonlocal dead_letter_dirty
    if not (dead_letter_dirty or force):
      return
    _save_dead_letter_entries(dead_letter_path, _pending_dead_letter_entries())
    dead_letter_dirty = False

  def _enqueue_archive_task(task_payload):
    nonlocal dead_letter_dirty
    item = dict(task_payload)
    item["retry_at"] = float(item.get("retry_at", 0.0))
    with archive_state_lock:
      heapq.heappush(
          pending_archive_tasks,
          (
              item["retry_at"],
              int(item["task"].attempt),
              next(_archive_heap_seq),
              item,
          ),
      )
    if item["task"].attempt > archive_retry_max_attempts:
      dead_letter_dirty = True

  def _track_pending_append_groups(groups):
    """Record per-day raw paths handed to archive dispatch/queue (disqualifier)."""
    with archive_state_lock:
      for compressed_path, paths in groups:
        tar_path = os.path.normpath(daily_tar_path_from_compressed(compressed_path))
        pending_append_by_daily_tar.setdefault(tar_path, set()).update(paths)

  def _build_deferred_paths_for_items(items):
    return [
        {
            "task": ArchiveTask(archive_info=item, attempt=1),
            "paths": list(item[1]),
            "retry_at": time.time(),
        }
        for item in items
    ]

  def _log_pending_archive_heap(*, context):
    with archive_state_lock:
      heap_n = len(pending_archive_tasks)
      heap_tars = sorted(
          daily_tar_paths_from_pending_archive_tasks(pending_archive_tasks))
    if heap_n <= 0:
      return
    labels = ",".join(os.path.basename(t) for t in heap_tars[:32])
    if len(heap_tars) > 32:
      labels += ",..."
    log_print(
        "INFO: pending_archive_heap context=%s n=%d tars=%s"
        % (context, heap_n, labels),
        flush=True,
    )

  def _dispatch_due_archive_retries(*, allow_idle_stale=False):
    if (
        not allow_idle_stale
        and not pending_stats_files
        and not pending_archive_tasks
        and reconcile_refs.get("suppress_idle_archive_retries")
    ):
      return
    if not pending_archive_tasks or not archive_dispatch.has_capacity():
      return
    due_tasks = []
    now = time.time()
    with archive_state_lock:
      while pending_archive_tasks and len(due_tasks) < archive_queue_max:
        retry_at, _attempt, _seq, task_payload = pending_archive_tasks[0]
        if retry_at > now:
          break
        heapq.heappop(pending_archive_tasks)
        due_tasks.append(task_payload)
    if not due_tasks:
      return
    archive_items = [d["task"].archive_info for d in due_tasks]

    def _enqueue_overflow(item):
      _enqueue_archive_task({
          "task": ArchiveTask(archive_info=item, attempt=1),
          "paths": list(item[1]),
          "retry_at": time.time(),
      })

    stats = archive_dispatch.dispatch_disjoint_items(
        archive_items,
        archive_queue_max=archive_queue_max,
        build_deferred_paths_fn=lambda items: [
            next(d for d in due_tasks if d["task"].archive_info == item)
            for item in items
        ],
        track_pending_append_fn=_track_pending_append_groups,
        transition_queued_fn=lambda p: _transition_file_state(
            file_states, p, SyncFileState.ARCHIVE_QUEUED),
        enqueue_overflow_fn=_enqueue_overflow,
    )
    perf_stats["archive_dispatch_s"] += stats.get("dispatch_s", 0.0)
    perf_stats["archive_dispatch_count"] += 1
    perf_stats["archive_dispatch_items"] += stats.get("submitted", 0)
    if stats.get("queued", 0) > 0 or pending_archive_tasks:
      _log_pending_archive_heap(context="after_drain")

  def _discard_inflight_archive_path(p):
    """Drop a path from in-flight tracking and its per-day append cache bucket."""
    with archive_state_lock:
      inflight_archive_paths.discard(p)
      for tar_path, bucket in list(pending_append_by_daily_tar.items()):
        if p in bucket:
          bucket.discard(p)
          if not bucket:
            del pending_append_by_daily_tar[tar_path]
          break

  def _in_flight_archive_daily_tars():
    tars = set(daily_tar_paths_from_pending_archive_tasks(pending_archive_tasks))
    for slot in archive_dispatch.slots:
      tars.update(slot.daily_tars)
    return tars

  def _capture_disqualification_inputs():
    """Snapshot supervisor archive state for day disqualification (under lock)."""
    with archive_state_lock:
      return {
          "pending_stats_paths": list(pending_stats_files),
          "inflight_paths": set(inflight_archive_paths),
          "pending_append_by_daily_tar": {
              tar_path: set(paths)
              for tar_path, paths in pending_append_by_daily_tar.items()
              if paths
          },
          "in_flight_archive_tars": _in_flight_archive_daily_tars(),
          "pending_archive_task_tars": daily_tar_paths_from_pending_archive_tasks(
              pending_archive_tasks),
      }

  day_raw_removal = None
  day_close_manifest = None
  day_close_rescan_pending = False
  chunk_in_progress = False
  active_chunk_ingest_tracker = None
  chunk_dispatch_paths = set()
  deferred_archive_finalize_prewarm_days = set()
  max_ingest_sort_epoch_by_tar: dict[str, int] = {}

  def _get_active_ingest_protected_paths():
    paths = set()
    if handoff_priority_paths:
      paths |= set(handoff_priority_paths)
    if chunk_in_progress and active_chunk_ingest_tracker is not None:
      paths |= active_chunk_ingest_tracker.all_in_flight_paths()
    if chunk_dispatch_paths:
      paths |= set(chunk_dispatch_paths)
    return paths

  def _get_ingest_active_skip_paths():
    captured = _capture_disqualification_inputs()
    skip_paths = set(captured["pending_stats_paths"])
    skip_paths |= set(captured["inflight_paths"])
    for paths in captured["pending_append_by_daily_tar"].values():
      skip_paths |= set(paths)
    skip_paths |= _get_active_ingest_protected_paths()
    return skip_paths

  def _get_quarantine_skip_paths():
    captured = _capture_disqualification_inputs()
    skip_paths = set(captured["pending_stats_paths"])
    skip_paths |= set(captured["inflight_paths"])
    for paths in captured["pending_append_by_daily_tar"].values():
      skip_paths |= set(paths)
    if day_raw_removal is not None:
      skip_paths |= day_raw_removal.paths_pending_delete()
    skip_paths |= _get_active_ingest_protected_paths()
    return skip_paths

  def _classify_quarantine_skip_path(path):
    path_norm = os.path.normpath(str(path or ""))
    if not path_norm:
      return "active_ingest"
    if handoff_priority_paths and path_norm in handoff_priority_paths:
      return "handoff"
    if chunk_dispatch_paths and path_norm in chunk_dispatch_paths:
      return "chunk_dispatch"
    if chunk_in_progress and active_chunk_ingest_tracker is not None:
      if path_norm in active_chunk_ingest_tracker.all_in_flight_paths():
        return "inflight"
    captured = _capture_disqualification_inputs()
    if path_norm in captured["pending_stats_paths"]:
      return "pending_stats"
    if path_norm in captured["inflight_paths"]:
      return "inflight"
    for paths in captured["pending_append_by_daily_tar"].values():
      if path_norm in paths:
        return "pending_append"
    if day_raw_removal is not None:
      if path_norm in day_raw_removal.paths_pending_delete():
        return "paths_pending_delete"
    return "active_ingest"

  startup_archive_scan = None

  def _resolve_unmapped_closed_raw_tars():
    coord_snap = (
        startup_archive_scan.get_snapshot()
        if startup_archive_scan is not None else None
    )
    with archive_janitor._accrual_snapshot_lock:
      accrual_snap = archive_janitor._accrual_snapshot
    return resolve_unmapped_closed_raw_daily_tars(
        coordinator_snapshot=coord_snap,
        accrual_snapshot=accrual_snap,
        archive_data_dir=directory,
        host_name_ext=host_name_ext,
        tgz_archive_dir=tgz_archive_dir,
        log_fn=log_print,
    )

  def _janitor_disqualified_daily_tars():
    captured = _capture_disqualification_inputs()
    unmapped = _resolve_unmapped_closed_raw_tars()
    return set(build_day_close_disqualified_daily_tars(
        tgz_archive_dir=tgz_archive_dir,
        remaining_raw_by_gz=None,
        inflight_paths=captured["inflight_paths"],
        pending_append_by_daily_tar=captured["pending_append_by_daily_tar"],
        in_flight_archive_tars=captured["in_flight_archive_tars"],
        pending_archive_task_tars=captured["pending_archive_task_tars"],
        unmapped_closed_raw_tars=set(unmapped or ()),
    ))

  def _janitor_delete_disqualified_daily_tars():
    disqualified = _janitor_disqualified_daily_tars()
    captured = _capture_disqualification_inputs()
    for path in captured["pending_stats_paths"]:
      tar_path = daily_tar_path_for_stats_path(path, tgz_archive_dir)
      if tar_path:
        disqualified.add(os.path.normpath(tar_path))
    for path in _get_active_ingest_protected_paths():
      tar_path = daily_tar_path_for_stats_path(path, tgz_archive_dir)
      if tar_path:
        disqualified.add(os.path.normpath(tar_path))
    return disqualified

  def _rescan_processed_exclusions():
    exclude = set(processed_files) | set(inflight_archive_paths) | set(
        handoff_priority_paths,
    )
    if day_raw_removal is not None:
      exclude_fn = getattr(day_raw_removal, "rescan_exclude_paths", None)
      if callable(exclude_fn):
        exclude |= exclude_fn()
    return exclude

  def _get_unmapped_closed_raw_tars():
    return _resolve_unmapped_closed_raw_tars()

  def _get_accrual_maintenance_snapshot():
    with archive_janitor._accrual_snapshot_lock:
      return archive_janitor._accrual_snapshot

  def _build_unprocessed_by_tar_for_day_close(*, pending_stats_paths):
    unprocessed_by_tar = build_unprocessed_raw_by_daily_tar(
        directory,
        host_name_ext,
        tgz_archive_dir,
        checkpoint_path=checkpoint_path,
        maintenance_snapshot=_get_accrual_maintenance_snapshot(),
    )
    return augment_unprocessed_by_tar_with_pending_paths(
        unprocessed_by_tar,
        pending_stats_paths=pending_stats_paths,
        tgz_archive_dir=tgz_archive_dir,
        checkpoint_path=checkpoint_path,
    )

  def _build_day_close_candidate_inputs():
    captured = _capture_disqualification_inputs()
    captured["unmapped_closed_raw_tars"] = _get_unmapped_closed_raw_tars()
    captured["unprocessed_by_tar"] = _build_unprocessed_by_tar_for_day_close(
        pending_stats_paths=captured["pending_stats_paths"],
    )
    return captured

  def _has_active_append_for_tar(tar_path: str) -> bool:
    captured = _capture_disqualification_inputs()
    tar_norm = os.path.normpath(tar_path)
    append_bucket = captured["pending_append_by_daily_tar"].get(tar_norm)
    if append_bucket:
      return True
    inflight_tars = daily_tar_paths_for_stats_paths(
        captured["inflight_paths"],
        tgz_archive_dir,
    )
    return tar_norm in {os.path.normpath(t) for t in inflight_tars}

  def _record_ingest_sort_epochs_for_paths(paths):
    for stats_path in paths or ():
      tar_path = daily_tar_path_for_stats_path(stats_path, tgz_archive_dir)
      if not tar_path:
        continue
      tar_norm = os.path.normpath(tar_path)
      epoch = stats_path_ingest_sort_epoch(stats_path)
      if epoch is None:
        continue
      prev = max_ingest_sort_epoch_by_tar.get(tar_norm)
      if prev is None or epoch > prev:
        max_ingest_sort_epoch_by_tar[tar_norm] = epoch

  def _log_checkpoint_day_close_chunk_progress(
      *,
      unprocessed_by_tar,
      checkpoint_deferred_archive=0,
  ):
    if not cfg.get_sync_day_close_candidate_report():
      return
    ranked = []
    for tar_path in iter_daily_tar_paths(tgz_archive_dir):
      tar_norm = os.path.normpath(tar_path)
      unprocessed = (unprocessed_by_tar or {}).get(tar_norm, ()) or ()
      on_disk = sum(1 for path in unprocessed if os.path.isfile(path))
      day_date = calendar_date_from_daily_tar_path(tar_norm)
      if day_date is None:
        continue
      ranked.append((day_date, tar_norm, on_disk))
    ranked.sort(key=lambda item: item[0])
    parts = []
    for day_date, tar_norm, on_disk in ranked[:3]:
      parts.append(
          "%s unprocessed=%d checkpoint_complete=%s"
          % (
              day_date.isoformat(),
              on_disk,
              "yes" if on_disk == 0 else "no",
          ),
      )
    if parts:
      log_print(
          "sync_timedb: checkpoint day-close progress oldest_days=%s"
          % "; ".join(parts),
          flush=True,
      )
    if checkpoint_deferred_archive:
      log_print(
          "sync_timedb: checkpoint deferred archive finalize count=%d"
          % checkpoint_deferred_archive,
          flush=True,
      )

  def _should_defer_immediate_day_close():
    if handoff_priority_paths:
      return True, "handoff_priority", len(handoff_priority_paths)
    return False, "", 0

  _should_defer_archive_finalize_day_close = _should_defer_immediate_day_close

  def _log_immediate_day_close_defer(context, reason, extra):
    if reason == "handoff_priority":
      log_print(
          "INFO: immediate day_close defer context=%s reason=handoff_priority "
          "pending_handoff=%d"
          % (context, extra),
          flush=True,
      )
    else:
      log_print(
          "INFO: immediate day_close defer context=%s reason=%s"
          % (context, reason),
          flush=True,
      )
    if context == "archive_finalize":
      if reason == "handoff_priority":
        log_print(
            "INFO: archive_finalize defer immediate day_close "
            "reason=handoff_priority pending_handoff=%d"
            % extra,
            flush=True,
        )
      else:
        log_print(
            "INFO: archive_finalize defer immediate day_close reason=%s"
            % reason,
            flush=True,
        )

  def _maybe_enqueue_immediate_day_close(*, context: str):
    if not tgz_archive_dir:
      return
    defer_day_close, defer_reason, defer_extra = (
        _should_defer_immediate_day_close()
    )
    if defer_day_close:
      _log_immediate_day_close_defer(context, defer_reason, defer_extra)
      if context in ("chunk_end", "archive_finalize", "idle_finalize"):
        _reconcile_pending_with_oldest_checkpoint_incomplete()
      return
    disqualified = _janitor_disqualified_daily_tars()
    with archive_janitor._hints_state_lock:
      day_phases = dict(archive_janitor._day_phases)
    unprocessed_by_tar = _build_unprocessed_by_tar_for_day_close(
        pending_stats_paths=list(pending_stats_files),
    )
    remaining_raw_by_gz = _get_accrual_remaining_raw_by_gz()
    candidates = days_ingest_complete_by_checkpoint(
        unprocessed_by_tar,
        tgz_archive_dir=tgz_archive_dir,
        day_phases=day_phases,
        remaining_raw_by_gz=remaining_raw_by_gz,
        local_tz=archive_janitor.local_tz,
        disqualified_daily_tars=disqualified,
    )
    submitted_any = False
    disqualified = _janitor_disqualified_daily_tars()
    max_inflight = cfg.get_sync_day_close_max_inflight()
    live_workers = archive_janitor._day_close_live_worker_tar_paths()
    for tar_path in candidates:
      tar_norm = os.path.normpath(str(tar_path or ""))
      if tar_norm in immediate_day_close_attempted_tars:
        continue
      if day_close_manifest is not None and hasattr(
          day_close_manifest, "active_discover_cap_tar_paths",
      ):
        cap_active = day_close_manifest.active_discover_cap_tar_paths(
            live_worker_tars=live_workers,
        )
      else:
        cap_active = set(live_workers)
      if len(cap_active) >= max_inflight:
        break
      if day_close_manifest is not None and day_close_manifest.enqueue_day_close(
          tar_norm,
          reason="day_ingest_complete:%s" % context,
          disqualified_daily_tars=disqualified,
      ):
        immediate_day_close_attempted_tars.add(tar_norm)
        submitted_any = True
    if submitted_any:
      archive_janitor.signal_work_available()

  def _reconcile_orphan_inflight_for_oldest_tar(oldest_tar, blocked_paths):
    captured = _capture_disqualification_inputs()
    reclaimed = reconcile_orphan_inflight_for_oldest_tar(
        oldest_tar=oldest_tar,
        blocked_paths=blocked_paths,
        inflight_archive_paths=captured["inflight_paths"],
        pending_append_by_daily_tar=captured["pending_append_by_daily_tar"],
        in_flight_archive_tars=captured["in_flight_archive_tars"],
        tgz_archive_dir=tgz_archive_dir,
        last_reclaim_monotonic_by_path=orphan_inflight_reclaim_last_monotonic,
        log_fn=log_print,
    )
    if reclaimed:
      with archive_state_lock:
        for path in reclaimed:
          inflight_archive_paths.discard(path)
    return reclaimed

  def _apply_archive_finalize_results(deferred_paths, results):
    nonlocal checkpoint_dirty_count
    nonlocal dead_letter_dirty
    nonlocal archive_finalize_needs_post_reconcile
    if not isinstance(results, list):
      results = [results]
    if len(results) != len(deferred_paths):
      log_print(
          "Archive result cardinality mismatch: deferred=%d results=%d; "
          "unmatched paths will be retried"
          % (len(deferred_paths), len(results)),
          flush=True,
      )
    for task_payload, result in zip(deferred_paths, results):
      archive_task = task_payload["task"]
      archive_paths = task_payload["paths"]
      if _archive_task_succeeded(result):
        skip_finalize_invalidate = (
            isinstance(result, ArchiveAppendOutcome)
            and result.skip_finalize_invalidate
        )
        skipped_set = set()
        if isinstance(result, ArchiveAppendOutcome):
          skipped_set = {
              os.path.normpath(p) for p in (result.skipped_paths or ()) if p
          }
        archive_path = archive_task.archive_info[0]
        day_date = calendar_date_from_daily_tar_path(
            daily_tar_path_from_compressed(archive_path),
        )
        day_token = day_date.isoformat() if day_date is not None else None
        if skip_finalize_invalidate:
          log_print(
              "INFO: archive_finalize skip invalidate day=%s reason=%s"
              % (
                  day_token or archive_path,
                  _archive_finalize_skip_invalidate_log_reason(result),
              ),
              flush=True,
          )
        else:
          invalidate_after_daily_tar_mutation(
              archive_path,
              reason="archive_finalize",
              log_fn=log_print,
          )
        for p in archive_paths:
          if os.path.normpath(p) in skipped_set:
            log_print(
                "INFO: archive_finalize convert_fail_skip path=%s day=%s"
                % (p, day_token or archive_path),
                flush=True,
            )
          _transition_file_state(file_states, p, SyncFileState.ARCHIVED)
          added = _add_processed_path(
              p, processed_files, processed_files_order, checkpoint_entries,
              checkpoint_path, file_states=file_states,
              handoff_priority_paths=handoff_priority_paths)
          if added:
            checkpoint_dirty_count += 1
          _discard_inflight_archive_path(p)
      else:
        next_attempt = archive_task.attempt + 1
        if next_attempt <= archive_retry_max_attempts:
          for p in archive_paths:
            _transition_file_state(file_states, p, SyncFileState.ARCHIVE_FAILED_RETRYABLE)
          retry_at = time.time() + _retry_delay(next_attempt)
          _enqueue_archive_task({
              "task": ArchiveTask(archive_info=archive_task.archive_info, attempt=next_attempt),
              "paths": archive_paths,
              "retry_at": retry_at,
          })
          log_print(
              "Archive task retry scheduled attempt=%d paths=%d delay_s=%.2f"
              % (next_attempt, len(archive_paths), max(0.0, retry_at - time.time())),
              flush=True,
          )
        else:
          log_print(
              "Archive retries exhausted for paths=%d; leaving for rescan"
              % len(archive_paths),
              flush=True,
          )
          dead_letter_entry = {
              "task": ArchiveTask(
                  archive_info=archive_task.archive_info,
                  attempt=next_attempt,
              ),
              "paths": list(archive_paths),
              "retry_at": 0.0,
          }
          _enqueue_archive_task(dead_letter_entry)
          if ingest_first_durability:
            for p in archive_paths:
              log_print(
                  "ingest_first_archive_abandoned_raw path=%s "
                  "reason=archive_retries_exhausted"
                  % p,
                  flush=True,
              )
              _transition_file_state(file_states, p, SyncFileState.ARCHIVED)
              added = _add_processed_path(
                  p, processed_files, processed_files_order, checkpoint_entries,
                  checkpoint_path, file_states=file_states,
                  handoff_priority_paths=handoff_priority_paths)
              if added:
                checkpoint_dirty_count += 1
              _discard_inflight_archive_path(p)
          else:
            for p in archive_paths:
              _transition_file_state(file_states, p, SyncFileState.ARCHIVE_FAILED_RETRYABLE)
              _discard_inflight_archive_path(p)
          _persist_dead_letters_if_needed()
    if len(deferred_paths) > len(results):
      for task_payload in deferred_paths[len(results):]:
        archive_task = task_payload["task"]
        archive_paths = task_payload["paths"]
        next_attempt = archive_task.attempt + 1
        if next_attempt <= archive_retry_max_attempts:
          retry_at = time.time() + _retry_delay(next_attempt)
          _enqueue_archive_task({
              "task": ArchiveTask(archive_info=archive_task.archive_info, attempt=next_attempt),
              "paths": archive_paths,
              "retry_at": retry_at,
          })
          for p in archive_paths:
            _transition_file_state(file_states, p, SyncFileState.ARCHIVE_FAILED_RETRYABLE)
            _discard_inflight_archive_path(p)
          log_print(
              "Archive task retry scheduled attempt=%d paths=%d delay_s=%.2f"
              % (next_attempt, len(archive_paths), max(0.0, retry_at - time.time())),
              flush=True,
          )
        else:
          _enqueue_archive_task({
              "task": ArchiveTask(archive_info=archive_task.archive_info, attempt=next_attempt),
              "paths": list(archive_paths),
              "retry_at": 0.0,
          })
          for p in archive_paths:
            _transition_file_state(file_states, p, SyncFileState.ARCHIVE_FAILED_RETRYABLE)
            _discard_inflight_archive_path(p)
    _flush_checkpoint_if_needed()
    # Drain overflow heap before long post_finalize_reconcile so calendar days
    # beyond current capacity start without waiting for the next ingest chunk.
    _dispatch_due_archive_retries(allow_idle_stale=True)
    finalized_paths = []
    for task_payload, result in zip(deferred_paths, results):
      if _archive_task_succeeded(result):
        finalized_paths.extend(task_payload.get("paths") or ())
    if finalized_paths:
      _record_ingest_sort_epochs_for_paths(finalized_paths)
      unprocessed = _live_unprocessed_by_tar_for_reconcile()
      tar_norm = oldest_checkpoint_incomplete_tar(
          unprocessed,
          tgz_archive_dir=tgz_archive_dir,
            newest_first=newest_first,
      )
      blocked = (
          _checkpoint_unblocked_paths_for_tar(unprocessed, tar_norm)
          if tar_norm
          else []
      )
      inflight_oldest_n = 0
      if tar_norm:
        inflight_oldest_n = sum(
            1
            for path in inflight_archive_paths
            if tar_norm in daily_tar_paths_for_stats_paths(
                [path],
                tgz_archive_dir,
            )
        )
      log_print(
          "sync_timedb: post_finalize_reconcile oldest_tar=%s incomplete_n=%d "
          "inflight_oldest_n=%d"
          % (tar_norm or "", len(blocked), inflight_oldest_n),
          flush=True,
      )
      _reconcile_pending_with_oldest_checkpoint_incomplete()
      archive_finalize_needs_post_reconcile = False
      defer_day_close, defer_reason, defer_extra = (
          _should_defer_immediate_day_close()
      )
      if defer_day_close:
        _log_immediate_day_close_defer("archive_finalize", defer_reason, defer_extra)
        archive_janitor.signal_work_available()
      else:
        _maybe_enqueue_immediate_day_close(context="archive_finalize")

  def _finalize_archive_slot(slot, *, force=False, allow_defer=False, context=""):
    ready_fn = getattr(slot.async_result, "ready", None)
    if callable(ready_fn):
      try:
        if not ready_fn():
          if allow_defer:
            log_print(
                "Archive finalize deferred context=%s reason=not_ready"
                % (context or "unknown"),
                flush=True,
            )
            return False
          if not force:
            return False
      except Exception:
        pass
    finalize_t0 = time.time()
    results = async_result_get_watch_pool(
        slot.async_result,
        archive_pool,
        poll_timeout_s=FINALIZE_POLL_TIMEOUT_SECONDS,
        context="archive_finalize",
    )
    perf_stats["archive_finalize_wait_s"] += max(0.0, time.time() - finalize_t0)
    perf_stats["archive_finalize_calls"] += 1
    _apply_archive_finalize_results(slot.deferred_paths, results)
    return True

  def _finalize_archive_slots_if_needed(force=False, allow_defer=False, context=""):
    if not archive_dispatch.slots:
      return False
    perf_stats["archive_worker_stall_events"] += (
        archive_dispatch.log_stalled_slots())
    if not force:
      finalized = archive_dispatch.prune_finished_slots(
          lambda slot: _finalize_archive_slot(
              slot, force=True, context=context or "prune_ready"))
      if finalized:
        _dispatch_due_archive_retries(allow_idle_stale=True)
      return bool(archive_dispatch.slots)

    finalized_any = False
    for slot in list(archive_dispatch.slots):
      ready_fn = getattr(slot.async_result, "ready", None)
      is_ready = True
      if callable(ready_fn):
        try:
          is_ready = bool(ready_fn())
        except Exception:
          is_ready = True
      if not is_ready:
        if allow_defer:
          log_print(
              "Archive finalize deferred context=%s reason=not_ready"
              % (context or "unknown"),
              flush=True,
          )
          continue
        if not force:
          continue
      # Free capacity before finalize/reconcile so heap drain can use the slot.
      archive_dispatch.slots = [
          s for s in archive_dispatch.slots if s is not slot
      ]
      if _finalize_archive_slot(
          slot, force=True, allow_defer=False, context=context):
        finalized_any = True
        _dispatch_due_archive_retries(allow_idle_stale=True)
      else:
        archive_dispatch.slots.append(slot)
    return finalized_any

  def _flush_checkpoint_if_needed(force=False):
    nonlocal checkpoint_dirty_count
    if checkpoint_dirty_count <= 0:
      return
    if (not force and
        checkpoint_dirty_count < SYNC_TIMEDB_CHECKPOINT_FLUSH_EVERY_FILES):
      return
    try:
      _save_sync_checkpoint(checkpoint_path, checkpoint_entries)
      checkpoint_dirty_count = 0
    except OSError as exc:
      log_print(
          "ERROR: checkpoint flush failed path=%s errno=%s: %s"
          % (
              checkpoint_path,
              getattr(exc, "errno", ""),
              exc,
          ),
          flush=True,
      )

  def _invalidate_host_scan_hints_for_paths(paths):
    if not isinstance(host_scan_hints, dict):
      return
    for path in paths or ():
      host_dir = os.path.dirname(path)
      host_scan_hints.pop(host_dir, None)

  reconcile_refs = {
      "ingest_gate_cleared": False,
      "get_startup_snapshot": lambda: None,
      "get_accrual_snapshot": lambda: None,
      "warned_live_reconcile_fallback": False,
      "last_unprocessed_by_tar": None,
      "last_reconcile_oldest_tar": "",
      "last_reconcile_incomplete_n": None,
      "oldest_day_gate_stall_blocked_n": None,
      "last_cap_pending_monotonic": 0.0,
      "last_chunk_ingest_summary_mono": None,
      "last_ingest_stall_watchdog_mono": 0.0,
      "chunk_ingest_outcomes": [],
      "last_chunk_archival_n": 0,
      "suppress_idle_archive_retries": False,
  }

  def _maybe_log_ingest_stall_watchdog(
      *,
      incomplete_n,
      oldest_tar=None,
      in_flight_paths=None,
  ):
    """ERROR when gate blocked but no chunk progress for INGEST_STALL_WATCHDOG_IDLE_S."""
    if incomplete_n <= 0:
      return
    last_summary = reconcile_refs.get("last_chunk_ingest_summary_mono")
    if last_summary is None:
      return
    idle_s = time.monotonic() - float(last_summary)
    if idle_s < INGEST_STALL_WATCHDOG_IDLE_S:
      return
    if chunk_in_progress:
      return
    flight = list(in_flight_paths or ())
    if active_chunk_ingest_tracker is not None:
      flight.extend(active_chunk_ingest_tracker.sample_in_flight())
    if any_giant_ingest_budget_in_flight(flight):
      return
    registry = getattr(stall_diagnostics, "worker_registry", None)
    if registry is not None:
      effective = _max_effective_ingest_timeout_from_registry(registry)
      if effective is not None and effective > 0.0:
        if idle_s < float(effective):
          return
    now_mono = time.monotonic()
    last_log = float(reconcile_refs.get("last_ingest_stall_watchdog_mono") or 0.0)
    if now_mono - last_log < INGEST_STALL_WATCHDOG_IDLE_S:
      return
    reconcile_refs["last_ingest_stall_watchdog_mono"] = now_mono
    _maybe_reap_supervisor_pool_children_throttled(
        ingest_pool,
        archive_pool,
        populate_pool_controller,
        context="stall_watchdog",
    )
    log_print(
        "ERROR: ingest_stall_watchdog incomplete_n=%d oldest_tar=%s "
        "idle_since_chunk_summary_s=%.0f chunk_in_progress=%d "
        "giant_in_flight=%d"
        % (
            incomplete_n,
            oldest_tar or "",
            idle_s,
            int(chunk_in_progress),
            int(any_giant_ingest_budget_in_flight(flight)),
        ),
        flush=True,
    )

  def _resolve_reconcile_maintenance_snapshot(*, prefer_startup=False):
    if prefer_startup or not reconcile_refs["ingest_gate_cleared"]:
      snap = reconcile_refs["get_startup_snapshot"]()
      if snap is not None:
        return snap, "coordinator"
      if not reconcile_refs["warned_live_reconcile_fallback"]:
        reconcile_refs["warned_live_reconcile_fallback"] = True
        log_print(
            "WARN: pending reconcile cap: no startup coordinator snapshot; "
            "using live scan",
            flush=True,
        )
      return None, "live"
    accrual = reconcile_refs["get_accrual_snapshot"]()
    if accrual is not None:
      return accrual, "accrual"
    return None, "live"

  def _reconcile_checkpoint_paths():
    return resolved_checkpoint_path_set(checkpoint_path, checkpoint_entries)

  def _checkpoint_unblocked_paths_for_tar(unprocessed_by_tar, tar_norm):
    """Tar-aligned on-disk unprocessed paths not yet in checkpoint merge."""
    paths = aligned_on_disk_unprocessed_paths_for_tar(
        unprocessed_by_tar,
        tar_norm,
        tgz_archive_dir=tgz_archive_dir,
    )
    checkpoint_paths = _reconcile_checkpoint_paths()
    if not checkpoint_paths:
      return paths
    return [path for path in paths if path not in checkpoint_paths]

  def _all_checkpoint_unblocked_paths(unprocessed_by_tar):
    """All on-disk unprocessed paths (any tar) not yet in checkpoint merge."""
    paths = all_on_disk_unprocessed_paths(unprocessed_by_tar)
    checkpoint_paths = _reconcile_checkpoint_paths()
    if not checkpoint_paths:
      return paths
    return [path for path in paths if path not in checkpoint_paths]

  def _live_unprocessed_by_tar_for_reconcile():
    snapshot, _source = _resolve_reconcile_maintenance_snapshot()
    return build_live_unprocessed_by_tar_for_reconcile(
        directory,
        host_name_ext,
        tgz_archive_dir,
        checkpoint_path=checkpoint_path,
        checkpoint_paths=_reconcile_checkpoint_paths(),
        pending_stats_paths=list(pending_stats_files),
        maintenance_snapshot=snapshot,
    )

  def _apply_handoff_priority_to_pending(pending):
    if not handoff_priority_paths:
      return list(pending or ())
    blocked = sort_pending_stats_paths_oldest_first(
        handoff_priority_paths, newest_first=newest_first)
    return prepend_checkpoint_incomplete_paths_to_pending(
        pending,
        blocked,
        exclude=inflight_archive_paths,
        newest_first=newest_first,
    )

  def _cap_pending_stats_with_handoff_priority(
      paths,
      *,
      oldest_tar=None,
      oldest_tar_reserved_paths=None,
  ):
    blocked_reserved = list(oldest_tar_reserved_paths or ())
    if handoff_priority_paths or blocked_reserved:
      return cap_pending_stats_with_blocked_retention(
          paths,
          max_size=ingest_queue_max,
          blocked_paths=blocked_reserved,
          handoff_priority_paths=handoff_priority_paths,
          log_fn=log_print,
          newest_first=newest_first,
      )
    return cap_pending_stats_file_list(
        sort_pending_stats_paths_oldest_first(
            list(paths or ()), newest_first=newest_first),
        ingest_queue_max,
        log_fn=log_print,
        newest_first=newest_first,
    )

  def _resolve_closed_paths_for_cap():
    coordinator = reconcile_refs["get_startup_snapshot"]()
    accrual = reconcile_refs["get_accrual_snapshot"]()
    closed_paths, _source = resolve_idle_rescan_closed_paths(
        coordinator_snapshot=coordinator,
        accrual_snapshot=accrual,
    )
    return closed_paths

  def _invalidate_pending_reconcile_unprocessed_cache():
    reconcile_refs["last_unprocessed_by_tar"] = None
    reconcile_refs["last_reconcile_oldest_tar"] = ""
    reconcile_refs["last_reconcile_incomplete_n"] = None
    reconcile_refs["last_reconcile_newest_first"] = None
    reconcile_refs["last_cap_pending_monotonic"] = 0.0

  def _store_pending_reconcile_unprocessed_cache(
      unprocessed,
      *,
      oldest_tar,
      incomplete_n,
      mono_now=None,
  ):
    reconcile_refs["last_unprocessed_by_tar"] = unprocessed
    reconcile_refs["last_reconcile_oldest_tar"] = oldest_tar or ""
    reconcile_refs["last_reconcile_incomplete_n"] = int(incomplete_n)
    reconcile_refs["last_reconcile_newest_first"] = bool(newest_first)
    reconcile_refs["last_cap_pending_monotonic"] = (
        time.monotonic() if mono_now is None else float(mono_now)
    )

  def _cached_unprocessed_reusable_for_cap(*, mono_now):
    """Return cached unprocessed map when TTL + incomplete fingerprint allow skip."""
    return try_reuse_pending_reconcile_unprocessed_cache(
        cached=reconcile_refs.get("last_unprocessed_by_tar"),
        last_mono=reconcile_refs.get("last_cap_pending_monotonic", 0.0),
        mono_now=mono_now,
        ttl_s=PENDING_RECONCILE_UNPROCESSED_TTL_S,
        last_incomplete_n=reconcile_refs.get("last_reconcile_incomplete_n"),
        last_oldest_tar=reconcile_refs.get("last_reconcile_oldest_tar") or "",
        stall_incomplete_n=reconcile_refs.get("oldest_day_gate_stall_blocked_n"),
        newest_first=newest_first,
        last_newest_first=reconcile_refs.get("last_reconcile_newest_first"),
    )

  def _cap_pending_after_rescan(paths, *, handoff=False, idle_refill=False):
    cap_t0 = time.time()
    _snapshot, source = _resolve_reconcile_maintenance_snapshot(
        prefer_startup=(handoff or idle_refill),
    )
    if idle_refill and _snapshot is not None:
      log_print(
          "sync_timedb: idle cap reconcile source=%s pending_in=%d"
          % (source, len(paths or ())),
          flush=True,
      )
    if (
        handoff
        and _snapshot is not None
    ):
      log_print(
          "sync_timedb: pending reconcile cap begin source=%s handoff=light"
          % source,
          flush=True,
      )
      capped = _cap_pending_stats_with_handoff_priority(
          _apply_handoff_priority_to_pending(paths),
      )
      log_print(
          "sync_timedb: pending reconcile cap done elapsed_s=%.3f "
          "handoff_light=1 incomplete_n=0 capped_pending=%d"
          % (time.time() - cap_t0, len(capped)),
          flush=True,
      )
      return capped
    mono_now = time.monotonic()
    reuse = _cached_unprocessed_reusable_for_cap(mono_now=mono_now)
    skip_reason = None
    if reuse is not None:
      unprocessed, tar_norm_cached, incomplete_cached, skip_reason = reuse
      log_print(
          "sync_timedb: pending reconcile cap skipped "
          "reason=%s oldest_tar=%s incomplete_n=%d"
          % (skip_reason, tar_norm_cached or "", incomplete_cached),
          flush=True,
      )
    else:
      unprocessed = None
    if unprocessed is None:
      log_print(
          "sync_timedb: pending reconcile cap begin source=%s" % source,
          flush=True,
      )
      unprocessed = _live_unprocessed_by_tar_for_reconcile()
    disk_checkpoint_paths = load_checkpoint_path_set(checkpoint_path)
    merged_checkpoint_paths = _reconcile_checkpoint_paths()
    memory_extra_n = len(merged_checkpoint_paths - disk_checkpoint_paths)
    if skip_reason is not None:
      tar_norm = reconcile_refs.get("last_reconcile_oldest_tar") or ""
    else:
      tar_norm = oldest_checkpoint_incomplete_tar(
          unprocessed,
          tgz_archive_dir=tgz_archive_dir,
          newest_first=newest_first)
    all_unprocessed = sort_pending_stats_paths_oldest_first(
        _all_checkpoint_unblocked_paths(unprocessed),
        newest_first=newest_first,
    )
    blocked = (
        sort_pending_stats_paths_oldest_first(
            _checkpoint_unblocked_paths_for_tar(unprocessed, tar_norm),
            newest_first=newest_first,
        )
        if tar_norm
        else []
    )
    if skip_reason is None:
      _store_pending_reconcile_unprocessed_cache(
          unprocessed,
          oldest_tar=tar_norm,
          incomplete_n=len(blocked),
          mono_now=mono_now,
      )
    blocked_set = set(all_unprocessed)
    reconcile_exclude = (
        (processed_files | inflight_archive_paths) - blocked_set
    )
    reserved_blocked = blocked[:chunk_size] if tar_norm else None
    capped = _cap_pending_stats_with_handoff_priority(
        prepend_checkpoint_incomplete_paths_to_pending(
            paths,
            all_unprocessed,
            exclude=reconcile_exclude,
        ),
        oldest_tar=tar_norm,
        oldest_tar_reserved_paths=reserved_blocked,
    )
    closed_paths = _resolve_closed_paths_for_cap()
    if closed_paths is None:
      log_print(
          "sync_timedb: pending cap supplement skipped reason=no_closed_paths",
          flush=True,
      )
    elif len(capped) < ingest_queue_max or all_unprocessed:
      capped = supplement_pending_paths_from_closed_paths(
          capped,
          closed_paths=closed_paths,
          max_size=ingest_queue_max,
          processed_exclude=reconcile_exclude,
          log_fn=log_print,
          newest_first=newest_first,
      )
    log_print(
        "sync_timedb: pending reconcile cap done elapsed_s=%.3f "
        "oldest_tar=%s incomplete_n=%d capped_pending=%d%s"
        % (
            time.time() - cap_t0,
            tar_norm or "",
            len(blocked),
            len(capped),
            (
                " checkpoint_merge disk_n=%d memory_extra_n=%d"
                % (len(disk_checkpoint_paths), memory_extra_n)
                if memory_extra_n > 0
                else ""
            ),
        ),
        flush=True,
    )
    return capped

  def _maybe_handle_cross_day_db_complete_after_chunk(
      *,
      stats_files_chunk,
      successful_paths,
      files_to_be_archived,
      oldest_tar_for_chunk,
      incomplete_n,
  ):
    outcomes = list(reconcile_refs.get("chunk_ingest_outcomes") or ())
    reconcile_refs["chunk_ingest_outcomes"] = []
    if not chunk_was_cross_day_defer_dispatch(
        stats_files_chunk,
        oldest_tar_for_chunk,
        incomplete_n=incomplete_n,
        tgz_archive_dir=tgz_archive_dir,
    ):
      return
    if files_to_be_archived:
      return
    if not all_ingest_outcomes_db_skip_head_tail(outcomes):
      return
    if len(successful_paths) != len(stats_files_chunk):
      return
    log_print(
        "sync_timedb: %s_cross_day_db_complete %s=%s "
        "path_n=%d"
        % (
            _day_chunk_gate_prefix(),
            _day_gate_tar_key(),
            oldest_tar_for_chunk or "",
            len(stats_files_chunk),
        ),
        flush=True,
    )
    reconcile_refs["oldest_day_gate_stall_blocked_n"] = None
    _invalidate_pending_reconcile_unprocessed_cache()
    archive_janitor.signal_work_available()
    _maybe_enqueue_immediate_day_close(context="cross_day_db_complete")

  def _reconcile_pending_with_oldest_checkpoint_incomplete():
    nonlocal pending_stats_files
    pending_stats_files = _cap_pending_after_rescan(pending_stats_files)

  def _advance_pending_after_chunk(stats_files_chunk, successful_paths):
    nonlocal pending_stats_files
    successful_set = set(successful_paths)
    failed_chunk_paths = [
        path for path in stats_files_chunk if path not in successful_set
    ]
    if failed_chunk_paths:
      _invalidate_host_scan_hints_for_paths(failed_chunk_paths)
    # Non-prefix chunks (oldest-tar / handoff) must use set-difference, not
    # pending[len(chunk):] — that requeues in-flight chunk paths and drops head.
    tail = pending_minus_chunk(
        pending_stats_files, stats_files_chunk, newest_first=newest_first)
    successful_set = set(successful_paths)
    tail = [path for path in tail if path not in successful_set]
    if failed_chunk_paths:
      failed_chunk_paths = sort_pending_stats_paths_oldest_first(
          failed_chunk_paths,
          newest_first=newest_first,
      )
      failed_set = set(failed_chunk_paths)
      requeue_exclude = (
          (processed_files | inflight_archive_paths) - failed_set
      )
      pending_stats_files = _apply_handoff_priority_to_pending(
          prepend_checkpoint_incomplete_paths_to_pending(
              tail,
              failed_chunk_paths,
              exclude=requeue_exclude,
          ),
      )
    else:
      pending_stats_files = _apply_handoff_priority_to_pending(tail)

  def _ingest_paths_on_supervisor_thread(paths):
    """Bounded short ingest on supervisor thread (inline env fallback)."""
    successful_paths = []
    files_to_be_archived = []
    for index, path in enumerate(paths):
      result = add_stats_file_to_db(manager_lock, path)
      remaining = _ingest_remaining_count(len(paths), index)
      _log_ingest_worker_result(result, remaining=remaining)
      stats_fname, need_archival, ingest_ok, _elapsed, _meta = (
          _unpack_ingest_worker_result(result)
      )
      if not ingest_ok:
        continue
      _transition_file_state(
          file_states,
          stats_fname,
          SyncFileState.WRITTEN,
          handoff_priority_paths=handoff_priority_paths,
      )
      successful_paths.append(stats_fname)
      if should_archive and need_archival:
        files_to_be_archived.append(stats_fname)
    return successful_paths, files_to_be_archived

  def _ingest_explicit_path_batch(
      paths,
      *,
      context_label,
      pending_total,
      batch_chunk_counter,
      pending_tail=None,
      oldest_tar=None,
      gated_tar_restore=False,
  ):
    """Ingest an explicit path list via pool imap or supervisor-thread fallback."""
    nonlocal pool_worker_exit, active_chunk_ingest_tracker, ingest_pool

    successful_paths = []
    files_to_be_archived = []
    chunk_ingest_finished = 0
    active_chunk_ingest_tracker = None
    chunk_paths_norm = {
        os.path.normpath(path)
        for path in (paths or ())
        if path
    }
    state_reingest_paths = handoff_priority_paths | chunk_paths_norm
    reconcile_refs["chunk_ingest_outcomes"] = []
    skip_prewarm = _paths_all_db_complete_for_prewarm_skip(paths)
    effective_gated_restore = gated_tar_restore and not skip_prewarm

    def _replenish_giant_pending_tail(exclude):
      if not cfg.get_sync_ingest_giant_pool_supplement_enabled():
        return []
      supplement_queue = int(cfg.get_sync_ingest_giant_pool_supplement_queue_size())
      closed_paths = _resolve_closed_paths_for_cap()
      if not closed_paths:
        return []
      return build_giant_supplement_pending_tail(
          [],
          closed_paths=closed_paths,
          supplement_queue=supplement_queue,
          processed_exclude=exclude,
          log_fn=None,
          newest_first=newest_first,
      )

    stall_diagnostics.chunk_batch_size = len(paths)
    prewarm_t0 = time.time()
    stall_diagnostics.chunk_prewarm_summary = (
        _prewarm_archive_members_redis_for_chunk(
            paths,
            oldest_tar=oldest_tar,
            gated_tar_restore=effective_gated_restore,
            skip_prewarm=skip_prewarm,
        )
    )
    stall_diagnostics.chunk_prewarm_elapsed_s = time.time() - prewarm_t0
    ingest_t0 = time.time()

    k = 0
    active_workers = 0
    imap_context = "sync_timedb %s" % context_label
    log_print(
        "sync_timedb: chunk imap start paths=%d prewarm=%s context=%s"
        % (
            len(paths or ()),
            stall_diagnostics.chunk_prewarm_summary or "-",
            context_label,
        ),
        flush=True,
    )

    if _sync_timedb_ingest_inline_requested() or ingest_pool is None:
      try:
        inline_successful, inline_archived = _ingest_paths_on_supervisor_thread(
            paths)
        return inline_successful, inline_archived, active_workers, k
      finally:
        active_chunk_ingest_tracker = None

    try:
      add_stats_file = partial(add_stats_file_to_db, manager_lock)
      ingest_tracker = _IngestPoolInFlightTracker(paths)
      active_chunk_ingest_tracker = ingest_tracker

      def _replace_ingest_pool(new_pool):
        nonlocal ingest_pool
        ingest_pool = new_pool
        maintenance_pool_health["ingest_pool"] = new_pool
        maintenance_pool_health["active_pool"] = new_pool

      def _recreate_ingest_pool_maintenance():
        return create_sync_timedb_spawn_pool(
            processes=thread_count,
            initializer=apply_ingest_pool_worker_init,
            initargs=(
                SYNC_TIMEDB_PROCESS_TITLE,
                "ingest-pool",
                stall_diagnostics.worker_registry,
            ),
            pool_kind_log_label="ingest-pool",
        )

      maintenance_pool_health = {
          "ingest_pool": ingest_pool,
          "active_pool": ingest_pool,
          "expected_pool_workers": thread_count,
      }
      inflight_cap = _effective_ingest_imap_inflight_cap(thread_count, len(paths))

      results_iter = _imap_ingest_paths_batched(
          ingest_pool,
          add_stats_file,
          paths,
          thread_count=thread_count,
          context=imap_context,
          tracker=ingest_tracker,
          chunk_counter=batch_chunk_counter,
          pending_count=pending_total,
          ingest_pool=ingest_pool,
          archive_pool=archive_pool,
          stall_diagnostics=stall_diagnostics,
          pending_tail=pending_tail,
          replenish_pending_tail_fn=_replenish_giant_pending_tail,
          populate_pool_controller=populate_pool_controller,
          on_ingest_pool_replaced=_replace_ingest_pool,
          pool_health_context=maintenance_pool_health,
      )
      for result in results_iter:
        stats_fname, need_archival, ingest_ok, elapsed_s, outcome_meta = (
            _unpack_ingest_worker_result(result)
        )
        meta = dict(outcome_meta or {})
        outcome = str(meta.get("outcome") or "")
        if not outcome:
          outcome = "ingested" if meta.get("stats_rows") else "db_skip"
        db_skip = str(meta.get("db_skip") or "no")
        reconcile_refs["chunk_ingest_outcomes"].append(
            (stats_fname, outcome, db_skip),
        )
        if ingest_tracker is not None:
          ingest_tracker.complete(stats_fname)
        k += 1
        active_workers = max(active_workers, min(thread_count, k))
        remaining = _ingest_remaining_count(pending_total, chunk_ingest_finished)
        _log_ingest_worker_result(
            result,
            remaining=remaining,
            supplement=_ingest_path_is_supplement(stats_fname, chunk_paths_norm),
        )
        # RC-K: live in-flight count (not fake min(cap, thread_count)).
        live_inflight = (
            ingest_tracker.in_flight_count()
            if ingest_tracker is not None
            else 0
        )
        _handle_ingest_worker_memory_after_imap(
            pool=ingest_pool,
            registry=stall_diagnostics.worker_registry,
            result=result,
            accumulator=worker_memory_accumulator,
            pool_health_context=maintenance_pool_health,
            recreate_ingest_pool_fn=_recreate_ingest_pool_maintenance,
            on_pool_replaced=_replace_ingest_pool,
            pending_inflight=live_inflight,
            max_inflight=inflight_cap,
        )
        chunk_ingest_finished += 1
        if ingest_ok:
          _transition_file_state(
            file_states,
            stats_fname,
            SyncFileState.WRITTEN,
            handoff_priority_paths=state_reingest_paths,
        )
          successful_paths.append(stats_fname)
          if should_archive and need_archival:
            files_to_be_archived.append(stats_fname)
    except MultiprocessingWorkerExitError as exc:
      pool_worker_exit = True
      _handle_pool_worker_exit_fatal(
          exc,
          ingest_pool=ingest_pool,
          archive_pool=archive_pool,
      )
    except DatabaseUnavailableExit:
      raise
    except ArchiveMembersRedisUnavailableError as exc:
      if is_populate_pool_unavailable_error(exc):
        log_print("ERROR: %s" % exc, flush=True)
        log_print(
            "WARNING: populate-pool unavailable during ingest is not L2 fatal; "
            "ensuring populate-pool and continuing chunk (files retry later).",
            flush=True,
        )
        if populate_pool_controller is not None:
          try:
            populate_pool_controller.reap_and_restart()
          except Exception:
            pass
      else:
        _exit_on_archive_members_redis_unavailable(exc)
    except Exception as exc:
      error_context = "sync_timedb ingest pool"
      reraise_database_unavailable_chain(exc, context=error_context)
      raise
    finally:
      active_chunk_ingest_tracker = None

    stall_diagnostics.chunk_ingest_elapsed_s = time.time() - ingest_t0
    return successful_paths, files_to_be_archived, active_workers, k

  def _finalize_ingest_archive_batch(
      successful_paths,
      files_to_be_archived,
      *,
      context_label,
  ):
    nonlocal checkpoint_dirty_count
    if files_to_be_archived:
      _ensure_daily_archive_dir_exists()
    ar_file_mapping = build_archive_mapping(
        files_to_be_archived,
        tgz_archive_dir,
    )
    if not ar_file_mapping and files_to_be_archived:
      ar_file_mapping = _build_fallback_archive_mapping_by_mtime(
          files_to_be_archived,
          tgz_archive_dir,
      )
    _finalize_archive_slots_if_needed(force=False)
    archived_candidates = set(files_to_be_archived)
    immediate_paths = [
        p for p in successful_paths if p not in archived_candidates
    ]
    deferred_paths = [p for p in successful_paths if p in archived_candidates]
    for p in immediate_paths:
      _transition_file_state(file_states, p, SyncFileState.ARCHIVED)
      added = _add_processed_path(
          p, processed_files, processed_files_order, checkpoint_entries,
          checkpoint_path, file_states=file_states,
          handoff_priority_paths=handoff_priority_paths)
      if added:
        checkpoint_dirty_count += 1
    _flush_checkpoint_if_needed()
    if ar_file_mapping:
      archive_items_all = _normalize_archive_groups_by_tgz(ar_file_mapping)
      with archive_state_lock:
        inflight_archive_paths.update(deferred_paths)
      _track_pending_append_groups(archive_items_all)
      for p in deferred_paths:
        _transition_file_state(file_states, p, SyncFileState.ARCHIVE_QUEUED)
      def _enqueue_overflow_item(item):
        _enqueue_archive_task({
            "task": ArchiveTask(archive_info=item, attempt=1),
            "paths": list(item[1]),
            "retry_at": time.time(),
        })
        for p in item[1]:
          _transition_file_state(file_states, p, SyncFileState.ARCHIVE_QUEUED)

      batch_stats = archive_dispatch.dispatch_disjoint_items(
          archive_items_all,
          archive_queue_max=archive_queue_max,
          build_deferred_paths_fn=_build_deferred_paths_for_items,
          track_pending_append_fn=_track_pending_append_groups,
          transition_queued_fn=lambda p: _transition_file_state(
              file_states, p, SyncFileState.ARCHIVE_QUEUED),
          enqueue_overflow_fn=_enqueue_overflow_item,
      )
      if batch_stats.get("queued", 0) > 0 or pending_archive_tasks:
        _log_pending_archive_heap(context="after_batch_dispatch")
      _dispatch_due_archive_retries()
    elif deferred_paths:
      log_print(
          "Deferring processed marker for %d file(s): archival mapping missing"
          % len(deferred_paths),
          flush=True,
      )
    _finalize_archive_slots_if_needed(
        force=True,
        allow_defer=False,
        context=context_label,
    )
    _dispatch_due_archive_retries()
    _flush_checkpoint_if_needed(force=True)

  def _run_ingest_archive_paths_batch(paths, context_label):
    successful_paths, files_to_be_archived, _active_workers, _k = (
        _ingest_explicit_path_batch(
            paths,
            context_label=context_label,
            pending_total=len(paths),
            batch_chunk_counter=0,
        )
    )
    _finalize_ingest_archive_batch(
        successful_paths,
        files_to_be_archived,
        context_label=context_label,
    )
    successful_set = set(successful_paths)
    failed_paths = [path for path in paths if path not in successful_set]
    if failed_paths:
      _invalidate_host_scan_hints_for_paths(failed_paths)
    return successful_paths, failed_paths

  log_print(
      "sync_timedb: day_close immediate enqueue after chunk_end, archive_finalize, "
      "and idle_finalize; janitor tick discover is steady-state backstop; "
      "idle rescan sleep %s s"
      % (int(EMPTY_QUEUE_RESCAN_SLEEP_SECONDS),),
      flush=True,
  )
  host_scan_hints = {}
  idle_since_empty_queue = None
  worker_idle_loops = 0
  ingest_pool = None
  populate_pool_controller = None
  pool_worker_exit = False
  stall_diagnostics = IngestStallDiagnostics()
  stall_diagnostics.ingest_pipeline = "combined"
  chunk_size = cfg.get_sync_ingest_chunk_size()
  processed_files = set()
  file_states = {}
  processed_files_order = deque()
  checkpoint_entries = deque()
  checkpoint_dirty_count = 0
  inflight_archive_paths = set()
  pending_stats_files = []
  handoff_priority_paths = set()
  handoff_source_tar_by_path = {}
  immediate_day_close_attempted_tars = set()
  chunk_counter = 0
  worker_memory_accumulator = WorkerMemoryBatchAccumulator()
  oldest_day_chunk_gate_stall_last_log = 0.0
  oldest_day_gate_empty_chunk_spins = 0
  handoff_priority_stall_last_log = 0.0
  archive_finalize_needs_post_reconcile = False
  orphan_inflight_reclaim_last_monotonic = {}
  # Per-day cache of raw paths queued/in-flight for tar append (loss-safe
  # disqualification source for ArchiveJanitor ticks). Mirrors the
  # archive lifecycle: populated when groups are dispatched/queued, pruned when a
  # path leaves ``inflight_archive_paths`` on finalize. Never used to mark a path
  # ARCHIVED before its archive job finalizes successfully.
  pending_append_by_daily_tar = {}
  # Guards cross-thread reads of supervisor archive state by the single-flight
  # janitor background thread. Critical sections are tiny (copy/heap mutate only);
  # file locks + sealed-archive validation remain the data-loss safety net.
  archive_state_lock = threading.Lock()
  checkpoint_path = os.path.join(directory, SYNC_TIMEDB_CHECKPOINT_BASENAME)
  dead_letter_path = os.path.join(directory, SYNC_TIMEDB_DEAD_LETTER_BASENAME)

  ensure_persistence_contract(directory, log_fn=log_print)

  for entry in _load_sync_checkpoint(checkpoint_path):
    fp = _path_fingerprint(entry["path"])
    if fp is None:
      continue
    if fp["size"] != entry["size"] or fp["mtime"] != entry["mtime"]:
      continue
    processed_files.add(entry["path"])
    file_states[entry["path"]] = SyncFileState.ARCHIVED
    processed_files_order.append(entry["path"])
    checkpoint_entries.append(entry)

  for entry in _load_dead_letter_entries(dead_letter_path):
    _enqueue_archive_task(entry)

  if not _sync_timedb_ingest_inline_requested():
    diagnostics_manager = multiprocessing.Manager()
    worker_diagnostics_registry = diagnostics_manager.dict()
    stall_diagnostics.worker_registry = worker_diagnostics_registry
    stall_diagnostics.diagnostics_manager = diagnostics_manager
    ingest_pool_kind = "ingest-pool"
    ingest_pool = create_sync_timedb_spawn_pool(
        processes=thread_count,
        initializer=apply_ingest_pool_worker_init,
        initargs=(
            SYNC_TIMEDB_PROCESS_TITLE,
            ingest_pool_kind,
            worker_diagnostics_registry,
        ),
        pool_kind_log_label=ingest_pool_kind,
    )
    from hpcperfstats.dbload.lib.sync_timedb_populate_pool import (
        PopulatePoolController,
        set_populate_pool_controller,
    )

    populate_pool_controller = PopulatePoolController()
    set_populate_pool_controller(populate_pool_controller)
    populate_pool_controller.start(
        script_name=SYNC_TIMEDB_PROCESS_TITLE,
        registry=worker_diagnostics_registry,
    )
  archive_dispatch = ArchiveDispatchCoordinator(
      archive_pool=archive_pool,
      max_inflight=cfg.get_sync_archive_max_inflight_jobs(),
      archive_stats_files_fn=archive_stats_files,
      log_fn=log_print,
      pending_stats_count_fn=lambda: len(pending_stats_files),
  )
  day_raw_removal = DayRawRemovalCoordinator(
      archive_data_dir=directory,
      host_name_ext=host_name_ext,
      tgz_archive_dir=tgz_archive_dir,
      log_fn=log_print,
      get_quarantine_skip_paths=_get_quarantine_skip_paths,
      get_ingest_active_skip_paths=_get_ingest_active_skip_paths,
      classify_quarantine_skip_path=_classify_quarantine_skip_path,
      ingest_ready_fn=stats_file_head_ingested_in_db,
      process_title=SYNC_TIMEDB_PROCESS_TITLE,
  )
  day_close_manifest = DayCloseManifestCoordinator(
      archive_data_dir=directory,
      host_name_ext=host_name_ext,
      tgz_archive_dir=tgz_archive_dir,
      local_tz=local_timezone,
      log_fn=log_print,
      get_disqualified_daily_tars=_janitor_disqualified_daily_tars,
      day_raw_removal_coordinator=day_raw_removal,
      process_title=SYNC_TIMEDB_PROCESS_TITLE,
  )
  stall_diagnostics.day_close_manifest = day_close_manifest
  startup_archive_scan = StartupArchiveScanCoordinator(
      archive_data_dir=directory,
      host_name_ext=host_name_ext,
      tgz_archive_dir=tgz_archive_dir,
      log_fn=log_print,
  )

  def _get_startup_snapshot_for_rescan(*, idle_refill=False):
    """Supervisor rescan may single-flight fallback-build after wait timeout."""
    snap = startup_archive_scan.get_snapshot()
    if snap is not None:
      return snap
    if idle_refill:
      accrual = reconcile_refs["get_accrual_snapshot"]()
      if accrual is not None and accrual.closed_paths:
        return accrual
      wait_t0 = time.time()
      last_log = wait_t0
      wait_limit = min(
          30.0,
          max(5.0, float(cfg.get_sync_startup_snapshot_wait_seconds())),
      )
      while time.time() - wait_t0 < wait_limit:
        snap = startup_archive_scan.get_snapshot()
        if snap is not None:
          return snap
        accrual = reconcile_refs["get_accrual_snapshot"]()
        if accrual is not None and accrual.closed_paths:
          return accrual
        now = time.time()
        if now - last_log >= 30.0:
          log_print(
              "sync_timedb: idle_rescan_snapshot_wait elapsed_s=%.1f"
              % (now - wait_t0),
              flush=True,
          )
          last_log = now
        if startup_archive_scan.is_startup_heavy_maintenance_idle():
          break
        sleep_until_shutdown(0.5)
      return startup_archive_scan.get_snapshot()
    return startup_archive_scan.wait_for_snapshot(allow_build=True)

  def _startup_closed_paths_for_rescan(*, idle_refill=False):
    snap = startup_archive_scan.get_snapshot()
    if snap is not None and snap.closed_paths:
      if idle_refill:
        log_print(
            "sync_timedb: idle_rescan_snapshot_source=coordinator closed_paths=%d"
            % len(snap.closed_paths),
            flush=True,
        )
      return list(snap.closed_paths)
    if idle_refill:
      accrual = reconcile_refs["get_accrual_snapshot"]()
      if accrual is not None and accrual.closed_paths:
        log_print(
            "sync_timedb: idle_rescan_snapshot_source=accrual closed_paths=%d"
            % len(accrual.closed_paths),
            flush=True,
        )
        return list(accrual.closed_paths)
    return None

  def _get_accrual_remaining_raw_by_gz():
    with archive_janitor._accrual_snapshot_lock:
      snap = archive_janitor._accrual_snapshot
    if snap is None or not snap.remaining_raw_by_gz:
      return None
    return dict(snap.remaining_raw_by_gz)

  def _get_maintenance_snapshot_for_day_raw():
    remaining = _get_accrual_remaining_raw_by_gz()
    if remaining is not None:
      return types.SimpleNamespace(remaining_raw_by_gz=remaining)
    return startup_archive_scan.get_snapshot()

  day_raw_removal.get_maintenance_snapshot = _get_maintenance_snapshot_for_day_raw

  def _rescan_pending_with_progress(*, idle_refill=False):
    rescan_t0 = time.time()
    log_print("sync_timedb: pending rescan begin", flush=True)
    closed_paths = _startup_closed_paths_for_rescan(idle_refill=idle_refill)
    if (
        closed_paths is None
        and run_startup_maintenance
        and not idle_refill
    ):
      _get_startup_snapshot_for_rescan(idle_refill=False)
      closed_paths = _startup_closed_paths_for_rescan(idle_refill=False)
    elif closed_paths is None and idle_refill:
      _get_startup_snapshot_for_rescan(idle_refill=True)
      closed_paths = _startup_closed_paths_for_rescan(idle_refill=True)
    paths = rescan_pending_stats_files(
        directory,
        startdate,
        enddate,
        host_name_ext,
        _rescan_processed_exclusions(),
        host_scan_hints=host_scan_hints,
        startup_closed_paths=closed_paths,
        force_snapshot_paths=idle_refill,
        log_fn=log_print,
        newest_first=newest_first,
    )
    log_print(
        "sync_timedb: pending rescan done pending=%d elapsed_s=%.3f"
        % (len(paths), time.time() - rescan_t0),
        flush=True,
    )
    _maybe_reap_supervisor_pool_children_throttled(
        ingest_pool,
        archive_pool,
        populate_pool_controller,
        context="pending_rescan",
    )
    return paths

  def _get_ingest_pool_in_flight_count():
    if active_chunk_ingest_tracker is None:
      return 0
    return active_chunk_ingest_tracker.in_flight_count()

  def _get_chunk_in_progress():
    return bool(chunk_in_progress)

  def _get_startup_ingest_gate_cleared():
    return bool(reconcile_refs.get("ingest_gate_cleared"))

  def _get_chunk_day_tokens():
    if not chunk_in_progress:
      return set()
    return set(reconcile_refs.get("chunk_day_tokens") or ())

  archive_janitor = ArchiveJanitor(
      archive_data_dir=directory,
      host_name_ext=host_name_ext,
      tgz_archive_dir=tgz_archive_dir,
      local_tz=local_timezone,
      log_fn=log_print,
      get_disqualified_daily_tars=_janitor_disqualified_daily_tars,
      get_delete_disqualified_daily_tars=_janitor_delete_disqualified_daily_tars,
      get_pending_stats_count=lambda: len(pending_stats_files),
      get_idle_seconds=lambda: (
          max(0.0, time.time() - float(idle_since_empty_queue))
          if idle_since_empty_queue is not None else 0.0
      ),
      get_quarantine_skip_paths=_get_quarantine_skip_paths,
      ingest_ready_fn=stats_file_head_ingested_in_db,
      archive_stats_files_fn=archive_stats_files,
      day_raw_removal_coordinator=day_raw_removal,
      day_close_manifest_coordinator=day_close_manifest,
      get_day_close_candidate_inputs=_build_day_close_candidate_inputs,
      get_tree_rss_bytes=lambda: read_sync_timedb_tree_rss_bytes(ingest_pool, archive_pool),
      startup_snapshot_coordinator=startup_archive_scan,
      get_ingest_pool_in_flight_count=_get_ingest_pool_in_flight_count,
      get_chunk_in_progress=_get_chunk_in_progress,
      get_chunk_day_tokens=_get_chunk_day_tokens,
      get_startup_ingest_gate_cleared=_get_startup_ingest_gate_cleared,
      process_title=SYNC_TIMEDB_PROCESS_TITLE,
  )
  _ARCHIVE_JANITOR_REF["janitor"] = archive_janitor
  reconcile_refs["get_startup_snapshot"] = startup_archive_scan.get_snapshot
  reconcile_refs["get_accrual_snapshot"] = (
      archive_janitor.get_accrual_snapshot_for_reconcile
  )

  def _async_enqueue_day_close(tar_norm, reason):
    if archive_janitor._enqueue_day_close(tar_norm):
      archive_janitor.signal_work_available()
      return True
    return False

  day_close_manifest.enqueue_day_close_fn = _async_enqueue_day_close
  day_close_manifest.get_inflight_tar_paths_fn = archive_janitor._debt_heap_tar_paths

  def _on_async_day_phase(tar_norm, phase):
    nonlocal day_close_rescan_pending
    from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
        calendar_date_from_daily_tar_path,
    )
    from hpcperfstats.dbload.lib.sync_timedb_archive_maint import day_phase_hint_entry
    with archive_janitor._hints_state_lock:
      archive_janitor._day_phases[os.path.normpath(tar_norm)] = (
          day_phase_hint_entry(tar_norm, phase)
      )
    if phase == "tar_dropped":
      day_close_rescan_pending = True
      archive_janitor.signal_work_available()
    elif phase == "sealed" and chunk_in_progress:
      day_date = calendar_date_from_daily_tar_path(tar_norm)
      if day_date is not None:
        day_token = day_date.isoformat()
        tracker = active_chunk_ingest_tracker
        if tracker is not None:
          day_hint = _calendar_day_hint_from_paths(tracker.sample_in_flight())
          if day_hint and day_hint != day_token:
            return
        _reprewarm_archive_members_after_seal_phase(
            day_token,
            day_raw_removal=day_raw_removal,
        )

  def _day_close_enqueue_eligible(tar_norm):
    tar_norm = os.path.normpath(str(tar_norm or ""))
    captured = _build_day_close_candidate_inputs()
    with archive_janitor._hints_state_lock:
      day_phases = dict(archive_janitor._day_phases)
    remaining = _get_accrual_remaining_raw_by_gz()
    if remaining is None:
      remaining = build_remaining_raw_stats_by_daily_gz(
          directory,
          host_name_ext,
          tgz_archive_dir,
      )
    return daily_tar_eligible_for_day_close_submit(
        tar_norm,
        unprocessed_by_tar=captured.get("unprocessed_by_tar"),
        disqualified_daily_tars=_janitor_disqualified_daily_tars(),
        day_phases=day_phases,
        remaining_raw_by_gz=remaining,
        local_tz=local_timezone,
        day_raw_removal=day_raw_removal,
        tgz_archive_dir=tgz_archive_dir,
    )

  day_close_manifest.submit_eligible_fn = _day_close_enqueue_eligible
  day_close_manifest.on_day_phase = _on_async_day_phase

  def _flush_deferred_archive_finalize_prewarm():
    if not deferred_archive_finalize_prewarm_days:
      return
    days = sorted(deferred_archive_finalize_prewarm_days)
    deferred_archive_finalize_prewarm_days.clear()
    for day_token in days:
      log_print(
          "INFO: deferred prewarm flush day=%s reason=archive_finalize"
          % day_token,
          flush=True,
      )
      _prewarm_archive_members_redis_for_day_token(day_token)

  def _archive_members_invalidation_hook(_canonical, day_token, reason=None):
    if reason == "archive_finalize":
      if day_token:
        deferred_archive_finalize_prewarm_days.add(day_token)
      return
    if not chunk_in_progress or not day_token:
      return
    tracker = active_chunk_ingest_tracker
    if tracker is not None:
      day_hint = _calendar_day_hint_from_paths(tracker.sample_in_flight())
      if day_hint and day_hint != day_token:
        return
    log_print(
        "INFO: re-prewarm archive members Redis after cache invalidation day=%s"
        % day_token,
        flush=True,
    )
    _prewarm_archive_members_redis_for_day_token(day_token)

  set_archive_members_invalidation_hook(_archive_members_invalidation_hook)

  handoff_requeued_tars_this_boot = set()
  boot_handoffs_processed = False
  startup_ingest_gate_cleared = False
  startup_gate_cleared_logged = False

  def _mark_startup_ingest_gate_cleared():
    nonlocal startup_ingest_gate_cleared
    nonlocal startup_gate_cleared_t0
    nonlocal startup_gate_cleared_logged
    if startup_ingest_gate_cleared:
      return
    startup_ingest_gate_cleared = True
    reconcile_refs["ingest_gate_cleared"] = True
    startup_gate_cleared_t0 = time.time()
    if startup_gate_cleared_logged:
      return
    startup_gate_cleared_logged = True
    log_print(
        "sync_timedb: startup_elapsed_s=%.3f boot_handoff summary "
        "handoff_requeued_tars=%d"
        % (
            startup_gate_cleared_t0 - ingest_t0,
            len(handoff_requeued_tars_this_boot),
        ),
        flush=True,
    )

  def _clear_handoff_priority_for_tar(tar_norm):
    tar_norm = os.path.normpath(str(tar_norm or ""))
    if not tar_norm:
      return
    to_drop = [
        path
        for path in handoff_priority_paths
        if tar_norm in daily_tar_paths_for_stats_paths(
            [path],
            tgz_archive_dir,
        )
    ]
    for path in to_drop:
      handoff_priority_paths.discard(path)
      handoff_source_tar_by_path.pop(path, None)

  def _age_misbucket_handoff_priority_paths():
    """Drop sticky handoff leads whose derived day has no daily archive source."""
    clear_sources = age_misbucket_handoff_priority_paths(
        handoff_priority_paths,
        tgz_archive_dir=tgz_archive_dir,
        handoff_source_tar_by_path=handoff_source_tar_by_path,
        log_fn=log_print,
    )
    for source_tar in clear_sources:
      if day_close_manifest is not None:
        clear_fn = getattr(
            day_close_manifest,
            "clear_deferred_waiting_on_ingest",
            None,
        )
        if callable(clear_fn):
          clear_fn(source_tar)
      archive_janitor.signal_work_available()
    return len(clear_sources)

  def _filter_handoff_requeue_paths(paths):
    filtered = []
    for path in (paths or ()):
      if not path or not os.path.isfile(path):
        continue
      if day_raw_removal is not None:
        skip_fn = getattr(
            day_raw_removal,
            "_closed_raw_path_is_quarantine_skip",
            None,
        )
        if callable(skip_fn) and skip_fn(path):
          continue
      filtered.append(str(path))
    return filtered

  def _on_day_close_pipeline_complete(_tar_path):
    nonlocal day_close_rescan_pending
    day_close_rescan_pending = True
    archive_janitor.signal_work_available()

  def _requeue_day_close_handoff_paths(
      tar_norm,
      paths,
      reason,
  ):
    nonlocal pending_stats_files
    tar_norm = os.path.normpath(str(tar_norm or ""))
    if not tar_norm:
      return
    requeued_paths = _filter_handoff_requeue_paths(paths)
    if not requeued_paths and day_raw_removal is not None:
      fallback_fn = getattr(
          day_raw_removal,
          "paths_for_closed_raw_handoff_requeue",
          None,
      )
      if callable(fallback_fn):
        requeued_paths = _filter_handoff_requeue_paths(fallback_fn(tar_norm))
    retryable_on_disk = len(requeued_paths)
    if tar_norm in handoff_requeued_tars_this_boot:
      new_paths = [
          path for path in requeued_paths if path not in handoff_priority_paths
      ]
      if not new_paths:
        log_print(
            "sync_timedb: day_close handoff requeue skip day=%s reason=%s "
            "detail=same_boot_duplicate retryable_on_disk=%d"
            % (
                calendar_date_from_daily_tar_path(tar_norm).isoformat()
                if calendar_date_from_daily_tar_path(tar_norm) is not None
                else tar_norm,
                reason or "",
                retryable_on_disk,
            ),
            flush=True,
        )
        return
      requeued_paths = new_paths
    elif not requeued_paths:
      log_print(
          "sync_timedb: day_close handoff requeue skip day=%s reason=%s "
          "detail=paths=0 retryable_on_disk=%d"
          % (
              calendar_date_from_daily_tar_path(tar_norm).isoformat()
              if calendar_date_from_daily_tar_path(tar_norm) is not None
              else tar_norm,
              reason or "",
              retryable_on_disk,
          ),
          flush=True,
      )
      return
    _batch_remove_processed_paths(
        requeued_paths,
        processed_files,
        processed_files_order,
        checkpoint_entries,
        checkpoint_path,
        file_states=file_states,
        host_scan_hints=host_scan_hints,
        persist=False,
    )
    max_slice = cfg.get_sync_ingest_chunk_size()
    if len(requeued_paths) > max_slice:
      day_token = (
          calendar_date_from_daily_tar_path(tar_norm).isoformat()
          if calendar_date_from_daily_tar_path(tar_norm) is not None
          else tar_norm
      )
      log_print(
          "sync_timedb: day_close handoff steady-chunk enqueue day=%s paths=%d "
          "handoff_mode=steady_chunk chunk_size=%d reason=%s"
          % (
              day_token,
              len(requeued_paths),
              cfg.get_sync_ingest_chunk_size(),
              reason or "",
          ),
          flush=True,
      )
    for path in requeued_paths:
      handoff_priority_paths.add(path)
      handoff_source_tar_by_path[path] = tar_norm
    _flush_checkpoint_if_needed(force=True)
    if day_close_manifest is not None:
      day_close_manifest.defer_for_ingest_handoff(tar_norm)
    handoff_requeued_tars_this_boot.add(tar_norm)
    pending_stats_files = _cap_pending_after_rescan(
        _apply_handoff_priority_to_pending(pending_stats_files),
        handoff=True,
    )
    source_day = (
        calendar_date_from_daily_tar_path(tar_norm).isoformat()
        if calendar_date_from_daily_tar_path(tar_norm) is not None
        else tar_norm
    )
    sample_path = requeued_paths[0]
    derived = _derive_stats_path_date(sample_path)
    derived_day = derived.isoformat() if derived is not None else ""
    log_print(
        "sync_timedb: day_close handoff requeue day=%s paths=%d reason=%s "
        "checkpoint_cleared=yes queue_head=yes sample_path=%s "
        "source_day=%s derived_day=%s"
        % (
            source_day,
            len(requeued_paths),
            reason or "",
            sample_path,
            source_day,
            derived_day,
        ),
        flush=True,
    )
    archive_janitor.signal_work_available()

  def _process_boot_handoffs_once():
    nonlocal pending_stats_files, boot_handoffs_processed
    if boot_handoffs_processed or not run_startup_maintenance:
      return
    boot_handoffs_processed = True
    if day_raw_removal is None or not day_raw_removal.enabled:
      return
    seen_tars = set()
    handoff_entries = []
    for tar_norm, paths in day_raw_removal.discover_manifest_handoffs():
      tar_norm = os.path.normpath(str(tar_norm or ""))
      if not tar_norm or tar_norm in seen_tars:
        continue
      seen_tars.add(tar_norm)
      handoff_entries.append((tar_norm, paths, "boot_manifest_handoff"))
    for tar_norm, paths in day_raw_removal.discover_closed_raw_on_disk_handoffs():
      tar_norm = os.path.normpath(str(tar_norm or ""))
      if not tar_norm or tar_norm in seen_tars:
        continue
      seen_tars.add(tar_norm)
      handoff_entries.append((tar_norm, paths, "boot_closed_raw_handoff"))
    if not handoff_entries:
      return
    log_print(
        "sync_timedb: boot handoff discover tars=%d"
        % len(handoff_entries),
        flush=True,
    )
    for tar_norm, paths, reason in handoff_entries:
      if reason == "boot_closed_raw_handoff":
        requeued = day_raw_removal.requeue_closed_raw_paths_for_ingest(
            tar_norm,
            reason=reason,
            paths=paths,
        )
        if not requeued:
          handoff_requeued_tars_this_boot.add(tar_norm)
          archive_janitor.signal_work_available()
        continue
      _requeue_day_close_handoff_paths(tar_norm, paths, reason)

  day_raw_removal.on_pipeline_complete = _on_day_close_pipeline_complete
  day_raw_removal.on_handoff_to_ingest = _requeue_day_close_handoff_paths

  def _maybe_apply_day_close_rescan():
    nonlocal day_close_rescan_pending, pending_stats_files
    if not day_close_rescan_pending or day_raw_removal is None:
      return
    removed = day_raw_removal.consumed_paths()
    for path in removed:
      file_states.pop(path, None)
      if isinstance(host_scan_hints, dict):
        host_scan_hints.pop(path, None)
    pending_stats_files = _cap_pending_after_rescan(
        merge_rescan_discovered_into_pending(
            pending_stats_files,
            rescan_pending_stats_files(
                directory,
                startdate,
                enddate,
                host_name_ext,
                _rescan_processed_exclusions(),
                host_scan_hints=host_scan_hints,
                newest_first=newest_first,
            ),
            processed_exclude=_rescan_processed_exclusions(),
            newest_first=newest_first,
        ),
    )
    day_close_rescan_pending = False
    log_print(
        "Day raw removal pipeline complete; rescanned pending=%d"
        % len(pending_stats_files),
        flush=True,
    )

  try:
    _ensure_daily_archive_dir_exists()
    if run_startup_maintenance:
      log_print(cfg.format_sync_timedb_non_default_settings_line(), flush=True)
      log_print("sync_timedb: maintenance pass reason=startup", flush=True)
      startup_archive_scan.note_startup_maintenance_pending()
      archive_janitor.signal_scheduled_maintenance_pass(reason="startup")
      archive_janitor.enqueue_startup_debt()
      _get_startup_snapshot_for_rescan(idle_refill=False)
      _mark_startup_ingest_gate_cleared()
      log_print(
          "sync_timedb: startup ingest gate cleared; ingest may begin "
          "(heavy maintenance may still run on janitor thread)",
          flush=True,
      )
    else:
      log_print(
          "sync_timedb: startup maintenance skipped "
          "(CLI 'current' and date-range skip heavy startup; "
          "pass 'all' for full-archive startup pass)",
          flush=True,
      )
      startup_archive_scan.mark_startup_heavy_maintenance_finished()
      _mark_startup_ingest_gate_cleared()

    while not shutdown_requested[0]:
      _process_boot_handoffs_once()
      _maybe_apply_day_close_rescan()

      if not pending_stats_files:
        if idle_since_empty_queue is None:
          idle_since_empty_queue = time.time()
        archive_janitor.signal_work_available()
        _dispatch_due_archive_retries(allow_idle_stale=False)
        _finalize_archive_slots_if_needed(force=True, context="pre_rescan")
        _maybe_enqueue_immediate_day_close(context="idle_finalize")
        discovered = _rescan_pending_with_progress(idle_refill=True)
        pending_stats_files = _cap_pending_after_rescan(
            merge_rescan_discovered_into_pending(
                pending_stats_files,
                discovered,
                processed_exclude=_rescan_processed_exclusions(),
                newest_first=newest_first,
            ),
            idle_refill=True,
        )
        if pending_stats_files:
          reconcile_refs["suppress_idle_archive_retries"] = False
          for p in pending_stats_files:
            _transition_file_state(file_states, p, SyncFileState.DISCOVERED)
          log_print(
              "Number of host stats files to process = ",
              len(pending_stats_files),
              flush=True,
          )
          chunk_counter = 0
          worker_idle_loops = 0
          continue
        archive_janitor.signal_work_available()
        discovered = _rescan_pending_with_progress(idle_refill=True)
        pending_stats_files = _cap_pending_after_rescan(
            merge_rescan_discovered_into_pending(
                pending_stats_files,
                discovered,
                processed_exclude=_rescan_processed_exclusions(),
                newest_first=newest_first,
            ),
            idle_refill=True,
        )
        if pending_stats_files:
          idle_since_empty_queue = None
          for p in pending_stats_files:
            _transition_file_state(file_states, p, SyncFileState.DISCOVERED)
          log_print(
              "Number of host stats files to process = ",
              len(pending_stats_files),
              flush=True,
          )
          chunk_counter = 0
          worker_idle_loops = 0
        else:
          worker_idle_loops += 1
          log_print("Worker idle loops while waiting for pending files: %d" % worker_idle_loops)
          log_print("No pending stats files after rescan", flush=True)
          _finalize_archive_slots_if_needed(force=True)
          _maybe_enqueue_immediate_day_close(context="idle_finalize")
          log_print(
              "Sleeping %s s before exiting sync_timedb"
              % EMPTY_QUEUE_RESCAN_SLEEP_SECONDS,
              flush=True,
          )
          if run_once:
            log_print(
                "sync_timedb once mode: no pending files, exiting supervisor loop",
                flush=True,
            )
            break
          sleep_until_shutdown(EMPTY_QUEUE_RESCAN_SLEEP_SECONDS)
          _flush_checkpoint_if_needed(force=True)
          break

      while pending_stats_files:
        if _all_should_exit_for_current_proximity(pending_stats_files):
          # Clear pending so the outer loop can idle-exit (run_once) instead of
          # re-entering this chunk loop forever with the same proximate head.
          pending_stats_files = []
          break
        _maybe_wait_tree_rss_before_chunk(ingest_pool, archive_pool)
        _maybe_apply_day_close_rescan()
        idle_since_empty_queue = None
        _finalize_archive_slots_if_needed(
            force=True,
            allow_defer=True,
            context="chunk_boundary",
        )
        _reconcile_pending_with_oldest_checkpoint_incomplete()
        if shutdown_requested[0]:
          log_print("Exiting due to SIGTERM")
          break
        if DEBUG:
          log_print(
              "Begining Chunk(%s) #%s Processing" % (chunk_size, chunk_counter))

        chunk_t0 = time.time()
        unprocessed_for_chunk = reconcile_refs.get("last_unprocessed_by_tar")
        oldest_tar_for_chunk = reconcile_refs.get("last_reconcile_oldest_tar") or ""
        if unprocessed_for_chunk is None:
          unprocessed_for_chunk = _live_unprocessed_by_tar_for_reconcile()
          oldest_tar_for_chunk = oldest_checkpoint_incomplete_tar(
              unprocessed_for_chunk,
              tgz_archive_dir=tgz_archive_dir,
                    newest_first=newest_first,
          )
          blocked_for_store = (
              _checkpoint_unblocked_paths_for_tar(
                  unprocessed_for_chunk,
                  oldest_tar_for_chunk,
              )
              if oldest_tar_for_chunk
              else []
          )
          _store_pending_reconcile_unprocessed_cache(
              unprocessed_for_chunk,
              oldest_tar=oldest_tar_for_chunk,
              incomplete_n=len(blocked_for_store),
          )
        elif not oldest_tar_for_chunk:
          oldest_tar_for_chunk = oldest_checkpoint_incomplete_tar(
              unprocessed_for_chunk,
              tgz_archive_dir=tgz_archive_dir,
                    newest_first=newest_first,
          )
        raw_blocked_paths = (
            aligned_on_disk_unprocessed_paths_for_tar(
                unprocessed_for_chunk,
                oldest_tar_for_chunk,
                tgz_archive_dir=tgz_archive_dir,
            )
            if oldest_tar_for_chunk
            else []
        )
        blocked_paths = (
            _checkpoint_unblocked_paths_for_tar(
                unprocessed_for_chunk,
                oldest_tar_for_chunk,
            )
            if oldest_tar_for_chunk
            else []
        )
        incomplete_n = len(blocked_paths)
        if raw_blocked_paths and incomplete_n == 0:
          log_print(
              "sync_timedb: %s_all_db_complete %s=%s "
              "raw_incomplete_n=%d"
              % (
                  _day_chunk_gate_prefix(),
                  _day_gate_tar_key(),
                  oldest_tar_for_chunk,
                  len(raw_blocked_paths),
              ),
              flush=True,
          )
        handoff_inflight_n = 0
        handoff_priority_n = len(handoff_priority_paths)
        handoff_cross_day_n = 0
        if oldest_tar_for_chunk:
          handoff_inflight_n = sum(
              1
              for path in inflight_archive_paths
              if oldest_tar_for_chunk in daily_tar_paths_for_stats_paths(
                  [path],
                  tgz_archive_dir,
              )
          )
          handoff_cross_day_n = sum(
              1
              for path in handoff_priority_paths
              if oldest_tar_for_chunk not in daily_tar_paths_for_stats_paths(
                  [path],
                  tgz_archive_dir,
              )
          )
        pending_paths_before_chunk = list(pending_stats_files)
        _age_misbucket_handoff_priority_paths()
        handoff_priority_n = len(handoff_priority_paths)
        if oldest_tar_for_chunk:
          handoff_cross_day_n = sum(
              1
              for path in handoff_priority_paths
              if oldest_tar_for_chunk not in daily_tar_paths_for_stats_paths(
                  [path],
                  tgz_archive_dir,
              )
          )
        stats_files_chunk = select_ingest_chunk_paths(
            pending_stats_files,
            oldest_tar=oldest_tar_for_chunk,
            unprocessed_by_tar=unprocessed_for_chunk,
            inflight_archive_paths=inflight_archive_paths,
            tgz_archive_dir=tgz_archive_dir,
            chunk_size=chunk_size,
            handoff_priority_paths=handoff_priority_paths,
            log_fn=log_print,
            newest_first=newest_first,
        )
        _publish_current_mode_heartbeat(
            list(inflight_archive_paths) + list(stats_files_chunk or ()),
        )
        if oldest_tar_for_chunk and (incomplete_n or handoff_inflight_n):
          oldest_aligned_in_chunk_n = sum(
              1
              for path in stats_files_chunk
              if oldest_tar_for_chunk in daily_tar_paths_for_stats_paths(
                  [path],
                  tgz_archive_dir,
              )
          )
          handoff_lead_in_chunk_n = sum(
              1
              for path in stats_files_chunk
              if path in handoff_priority_paths
              and oldest_tar_for_chunk not in daily_tar_paths_for_stats_paths(
                  [path],
                  tgz_archive_dir,
              )
          )
          chunk_pad_n = max(
              0,
              len(stats_files_chunk)
              - oldest_aligned_in_chunk_n
              - handoff_lead_in_chunk_n,
          )
          log_print(
              "sync_timedb: %s %s=%s incomplete_n=%d "
              "handoff_inflight_n=%d handoff_priority_n=%d handoff_cross_day_n=%d "
              "%s_checkpoint_pending_n=%d "
              "chunk_day_histogram=%s chunk_len=%d chunk_pad_n=%d"
              % (
                  _day_chunk_gate_prefix(),
                  _day_gate_tar_key(),
                  oldest_tar_for_chunk,
                  incomplete_n,
                  handoff_inflight_n,
                  handoff_priority_n,
                  handoff_cross_day_n,
                  _day_gate_tar_key(),
                  incomplete_n,
                  build_chunk_day_histogram(stats_files_chunk, tgz_archive_dir),
                  len(stats_files_chunk),
                  chunk_pad_n,
              ),
              flush=True,
          )
        if not stats_files_chunk:
          if handoff_priority_paths:
            now = time.time()
            if now - handoff_priority_stall_last_log >= 30.0:
              handoff_priority_stall_last_log = now
              log_print(
                  "sync_timedb: handoff_priority_stall handoff_n=%d chunk_len=0 "
                  "pending=%d oldest_tar=%s incomplete_n=%d"
                  % (
                      len(handoff_priority_paths),
                      len(pending_stats_files),
                      oldest_tar_for_chunk or "",
                      incomplete_n,
                  ),
                  flush=True,
              )
          if oldest_tar_for_chunk and (incomplete_n or handoff_inflight_n):
            if incomplete_n > 0:
              reclaimed = _reconcile_orphan_inflight_for_oldest_tar(
                  oldest_tar_for_chunk,
                  blocked_paths,
              )
              if reclaimed:
                reconcile_refs["oldest_day_gate_stall_blocked_n"] = None
                _invalidate_pending_reconcile_unprocessed_cache()
                _reconcile_pending_with_oldest_checkpoint_incomplete()
                _finalize_archive_slots_if_needed(
                    force=True,
                    allow_defer=True,
                    context="oldest_day_gate_wait",
                )
                continue
              reconcile_refs["oldest_day_gate_stall_blocked_n"] = incomplete_n
              now = time.time()
              if now - oldest_day_chunk_gate_stall_last_log >= 30.0:
                oldest_day_chunk_gate_stall_last_log = now
                checkpoint_incomplete_on_disk_stall = list(blocked_paths)
                fallback_n = sum(
                    1
                    for path in checkpoint_incomplete_on_disk_stall
                    if path not in inflight_archive_paths
                )
                pending_oldest_n = sum(
                    1
                    for path in pending_paths_before_chunk
                    if oldest_tar_for_chunk in daily_tar_paths_for_stats_paths(
                        [path],
                        tgz_archive_dir,
                    )
                )
                blocked_in_pending_n = sum(
                    1
                    for path in checkpoint_incomplete_on_disk_stall
                    if path in pending_paths_before_chunk
                )
                cross_day_mismatch_n = sum(
                    1
                    for path in checkpoint_incomplete_on_disk_stall
                    if oldest_tar_for_chunk not in daily_tar_paths_for_stats_paths(
                        [path],
                        tgz_archive_dir,
                    )
                )
                stall_detail = (
                    "blocked_not_in_pending"
                    if pending_oldest_n == 0
                    else "pending_filter_empty"
                )
                if cross_day_mismatch_n:
                  stall_detail = "cross_day_bucket"
                log_print(
                    "sync_timedb: %s_stall %s=%s "
                    "incomplete_n=%d blocked_in_pending_n=%d inflight_oldest_n=%d "
                    "pending_oldest_n=%d fallback_n=%d cross_day_mismatch_n=%d "
                    "calendar_tar_histogram=%s detail=%s"
                    % (
                        _day_chunk_gate_prefix(),
                        _day_gate_tar_key(),
                        oldest_tar_for_chunk,
                        incomplete_n,
                        blocked_in_pending_n,
                        handoff_inflight_n,
                        pending_oldest_n,
                        fallback_n,
                        cross_day_mismatch_n,
                        build_chunk_day_histogram(
                            checkpoint_incomplete_on_disk_stall,
                            tgz_archive_dir,
                        ),
                        stall_detail,
                    ),
                    flush=True,
                )
            _finalize_archive_slots_if_needed(
                force=True,
                allow_defer=True,
                context="oldest_day_gate_wait",
            )
            _maybe_log_ingest_stall_watchdog(
                incomplete_n=incomplete_n,
                oldest_tar=oldest_tar_for_chunk,
                in_flight_paths=inflight_archive_paths,
            )
            oldest_day_gate_empty_chunk_spins += 1
            if oldest_day_gate_empty_chunk_spins >= 32:
              sleep_until_shutdown(0.05)
              oldest_day_gate_empty_chunk_spins = 0
          continue

        reconcile_refs["oldest_day_gate_stall_blocked_n"] = None
        oldest_day_gate_empty_chunk_spins = 0
        if stats_files_chunk:
          dispatch_sample = stats_files_chunk[:5]
          log_print(
              "sync_timedb: chunk dispatch begin chunk_n=%d pending_n=%d "
              "paths_sample=%s epochs=%s"
              % (
                  chunk_counter,
                  len(pending_stats_files),
                  [os.path.basename(str(path)) for path in dispatch_sample],
                  [
                      stats_path_ingest_sort_epoch(path)
                      for path in dispatch_sample
                  ],
              ),
              flush=True,
          )
        stats_files_chunk, chunk_dup_n, _chunk_dup_sample = (
            dedupe_ingest_paths_preserve_order(stats_files_chunk)
        )
        if chunk_dup_n:
          log_print(
              "sync_timedb: chunk dispatch deduped duplicate_n=%d chunk_n=%d"
              % (chunk_dup_n, len(stats_files_chunk)),
              flush=True,
          )
        chunk_dispatch_paths = {
            os.path.normpath(p)
            for p in stats_files_chunk
            if p
        }
        chunk_in_progress = True
        reconcile_refs["chunk_day_tokens"] = set(
            build_chunk_day_histogram(stats_files_chunk, tgz_archive_dir).keys(),
        )
        pending_minus = pending_minus_chunk(
            pending_stats_files,
            stats_files_chunk,
            newest_first=newest_first,
        )
        if cfg.get_sync_ingest_giant_pool_supplement_enabled():
          supplement_queue = int(
              cfg.get_sync_ingest_giant_pool_supplement_queue_size(),
          )
          closed_for_tail = _resolve_closed_paths_for_cap()
          if closed_for_tail:
            giant_pending_tail = build_giant_supplement_pending_tail(
                pending_minus,
                closed_paths=closed_for_tail,
                supplement_queue=supplement_queue,
                log_fn=None,
                newest_first=newest_first,
            )
          else:
            giant_pending_tail = cap_pending_stats_file_list(
                pending_minus,
                supplement_queue,
                log_fn=None,
                newest_first=newest_first,
            )
        else:
          giant_pending_tail = pending_minus
        successful_paths, files_to_be_archived, active_workers, k = (
            _ingest_explicit_path_batch(
                stats_files_chunk,
                context_label="ingest chunk",
                pending_total=len(pending_stats_files),
                batch_chunk_counter=chunk_counter,
                pending_tail=giant_pending_tail,
                oldest_tar=(
                    oldest_tar_for_chunk
                    if (incomplete_n or handoff_inflight_n)
                    else None
                ),
                gated_tar_restore=bool(
                    oldest_tar_for_chunk and incomplete_n > 0,
                ),
            )
        )

        if chunk_counter == 0 and startup_gate_cleared_t0 is not None:
          log_print(
              "sync_timedb: startup_elapsed_s %.3f"
              % (startup_gate_cleared_t0 - ingest_t0),
              flush=True,
          )
        log_print(
            "sync_timedb: chunk_elapsed_s %.3f"
            % (time.time() - chunk_t0),
            flush=True,
        )
        log_print(
            "Throughput telemetry: active_workers=%d backlog=%d chunk_size=%d bulk_create_batch=%d"
            % (
                active_workers,
                len(pending_stats_files),
                chunk_size,
                bulk_create_batch_size(),
            ),
            flush=True,
        )
        if len(pending_stats_files) > thread_count and active_workers < max(1, thread_count // 2):
          log_print(
              "Idle-with-backlog detector: backlog=%d active_workers=%d pool=%d"
              % (len(pending_stats_files), active_workers, thread_count),
              flush=True,
          )
        log_print("Files marked for archival: %d" % len(files_to_be_archived))
        log_print(
            "sync_timedb: chunk ingest summary chunk=%d ingested_this_chunk=%d "
            "checkpoint_immediate_n=%d archive_deferred_n=%d"
            % (
                chunk_counter,
                len(successful_paths),
                len([
                    p for p in successful_paths
                    if p not in set(files_to_be_archived)
                ]),
                len([
                    p for p in successful_paths
                    if p in set(files_to_be_archived)
                ]),
            ),
            flush=True,
        )
        worker_memory_accumulator.maybe_flush(
            chunk_counter,
            ingest_pool=ingest_pool,
            archive_pool=archive_pool,
        )
        reconcile_refs["last_chunk_ingest_summary_mono"] = time.monotonic()
        reconcile_refs["last_chunk_archival_n"] = len(files_to_be_archived)
        reconcile_refs["suppress_idle_archive_retries"] = (
            len(files_to_be_archived) == 0
        )
        # Ingest progress can clear oldest-day incomplete — force next reconcile rescan.
        if successful_paths:
          _invalidate_pending_reconcile_unprocessed_cache()

        _maybe_handle_cross_day_db_complete_after_chunk(
            stats_files_chunk=stats_files_chunk,
            successful_paths=successful_paths,
            files_to_be_archived=files_to_be_archived,
            oldest_tar_for_chunk=oldest_tar_for_chunk,
            incomplete_n=incomplete_n,
        )

        if files_to_be_archived:
          _ensure_daily_archive_dir_exists()
        archive_t0 = time.time()
        ar_file_mapping = build_archive_mapping(
            files_to_be_archived,
            tgz_archive_dir,
        )
        total_in_mapping = sum(len(v) for v in ar_file_mapping.values())
        if ar_file_mapping:
          log_print(
              "Archive mapping: %d tar(s), %d file(s) to archive"
              % (len(ar_file_mapping), total_in_mapping))
        elif files_to_be_archived:
          log_print(
              "Archive mapping empty (all files skipped: no timestamp in head)")
          ar_file_mapping = _build_fallback_archive_mapping_by_mtime(
              files_to_be_archived,
              tgz_archive_dir,
          )
          if ar_file_mapping:
            log_print(
                "Fallback archive mapping by mtime: %d tar(s), %d file(s)"
                % (
                    len(ar_file_mapping),
                    sum(len(v) for v in ar_file_mapping.values()),
                ),
                flush=True,
            )

        _finalize_archive_slots_if_needed(force=False)

        archived_candidates = set(files_to_be_archived)
        immediate_paths = [
            p for p in successful_paths if p not in archived_candidates
        ]
        deferred_paths = [p for p in successful_paths if p in archived_candidates]
        chunk_state_paths = handoff_priority_paths | {
            os.path.normpath(p) for p in stats_files_chunk if p
        }
        for p in immediate_paths:
          _transition_file_state(
              file_states,
              p,
              SyncFileState.ARCHIVED,
              handoff_priority_paths=chunk_state_paths,
          )
          added = _add_processed_path(
              p, processed_files, processed_files_order, checkpoint_entries,
              checkpoint_path, file_states=file_states,
              handoff_priority_paths=chunk_state_paths)
          if added:
            checkpoint_dirty_count += 1
        if incomplete_n > 0 and immediate_paths:
          _flush_checkpoint_if_needed(force=True)
        else:
          _flush_checkpoint_if_needed()

        if ar_file_mapping:
          archive_items_all = _normalize_archive_groups_by_tgz(ar_file_mapping)
          with archive_state_lock:
            inflight_archive_paths.update(deferred_paths)
          _track_pending_append_groups(archive_items_all)
          for p in deferred_paths:
            _transition_file_state(file_states, p, SyncFileState.ARCHIVE_QUEUED)
          def _enqueue_overflow_item(item):
            _enqueue_archive_task({
                "task": ArchiveTask(archive_info=item, attempt=1),
                "paths": list(item[1]),
                "retry_at": time.time(),
            })
            for p in item[1]:
              _transition_file_state(file_states, p, SyncFileState.ARCHIVE_QUEUED)

          dispatch_stats = archive_dispatch.dispatch_disjoint_items(
              archive_items_all,
              archive_queue_max=archive_queue_max,
              build_deferred_paths_fn=_build_deferred_paths_for_items,
              track_pending_append_fn=_track_pending_append_groups,
              transition_queued_fn=lambda p: _transition_file_state(
                  file_states, p, SyncFileState.ARCHIVE_QUEUED),
              enqueue_overflow_fn=_enqueue_overflow_item,
          )
          perf_stats["archive_dispatch_s"] += dispatch_stats.get("dispatch_s", 0.0)
          perf_stats["archive_dispatch_count"] += 1
          perf_stats["archive_dispatch_items"] += dispatch_stats.get("submitted", 0)
          if dispatch_stats.get("queued", 0) > 0 or pending_archive_tasks:
            _log_pending_archive_heap(context="after_chunk_dispatch")
          _dispatch_due_archive_retries()
        elif deferred_paths:
          log_print(
              "Deferring processed marker for %d file(s): archival mapping missing"
              % len(deferred_paths),
              flush=True,
          )
        stall_diagnostics.chunk_archive_elapsed_s = time.time() - archive_t0
        log_print(
            "sync_timedb: chunk_prewarm_elapsed_s=%.3f chunk_ingest_elapsed_s=%.3f "
            "chunk_archive_elapsed_s=%.3f"
            % (
                stall_diagnostics.chunk_prewarm_elapsed_s,
                stall_diagnostics.chunk_ingest_elapsed_s,
                stall_diagnostics.chunk_archive_elapsed_s,
            ),
            flush=True,
        )
        _reap_supervisor_pool_children(
            ingest_pool, archive_pool, populate_pool_controller,
        )

        _record_ingest_sort_epochs_for_paths(
            [p for p in stats_files_chunk if p in set(successful_paths)])
        _advance_pending_after_chunk(stats_files_chunk, successful_paths)
        chunk_counter += 1
        _maybe_apply_tree_rss_governor(chunk_counter, ingest_pool, archive_pool)

        _dispatch_due_archive_retries()
        active_chunk_ingest_tracker = None

        if chunk_counter % rescan_every_chunks == 0:
          _finalize_archive_slots_if_needed(
              force=True,
              allow_defer=bool(pending_stats_files),
              context="rescan_every_chunks",
          )
          pending_stats_files = _cap_pending_after_rescan(
              merge_rescan_discovered_into_pending(
                  pending_stats_files,
                  rescan_pending_stats_files(
                      directory,
                      startdate,
                      enddate,
                      host_name_ext,
                      _rescan_processed_exclusions(),
                      host_scan_hints=host_scan_hints,
                      newest_first=newest_first,
                  ),
                  processed_exclude=_rescan_processed_exclusions(),
                  newest_first=newest_first,
              ),
          )
          log_print(
              "Rescanned after %d chunks; pending files (%s): %d"
              % (
                  rescan_every_chunks,
                  "newest first" if newest_first else "oldest first",
                  len(pending_stats_files),
              ))

        _finalize_archive_slots_if_needed(
            force=True,
            allow_defer=bool(pending_stats_files),
            context="end_of_batch",
        )
        archive_janitor.signal_work_available()
        _maybe_enqueue_immediate_day_close(context="chunk_end")

        _flush_deferred_archive_finalize_prewarm()
        chunk_in_progress = False
        chunk_dispatch_paths = set()
        reconcile_refs["chunk_day_tokens"] = set()

      _persist_dead_letters_if_needed(force=True)
      _flush_checkpoint_if_needed(force=True)
      janitor_stats = archive_janitor.stats()
      if (
          janitor_stats["janitor_debt_depth"] > 0
          and janitor_stats["janitor_ticks_completed"] == 0
          and janitor_stats.get("janitor_tick_defer_reason")
      ):
        log_print(
            "Archive janitor tick defer reason=%s debt=%d"
            % (
                janitor_stats["janitor_tick_defer_reason"],
                janitor_stats["janitor_debt_depth"],
            ),
            flush=True,
        )
      if (perf_stats["archive_finalize_calls"] or perf_stats["archive_dispatch_count"]
          or janitor_stats["janitor_ticks_completed"]):
        log_print(
            "Archive perf telemetry finalize_calls=%d finalize_wait_s=%.3f "
            "dispatch_calls=%d dispatch_items=%d dispatch_submit_s=%.3f "
            "stall_events=%d janitor_debt=%d janitor_ticks=%d janitor_throttled=%d"
            % (
                perf_stats["archive_finalize_calls"],
                perf_stats["archive_finalize_wait_s"],
                perf_stats["archive_dispatch_count"],
                perf_stats["archive_dispatch_items"],
                perf_stats["archive_dispatch_s"],
                perf_stats["archive_worker_stall_events"],
                janitor_stats["janitor_debt_depth"],
                janitor_stats["janitor_ticks_completed"],
                janitor_stats["janitor_budget_throttled"],
            ),
            flush=True,
        )
      close_old_connections()
      connections.close_all()
  finally:
    chunk_in_progress = False
    chunk_dispatch_paths = set()
    reconcile_refs["chunk_day_tokens"] = set()
    active_chunk_ingest_tracker = None
    preflight_shutdown_wait = not pool_worker_exit
    if day_close_manifest is not None:
      day_close_manifest.shutdown(wait=preflight_shutdown_wait)
    if day_raw_removal is not None:
      day_raw_removal.shutdown(wait=preflight_shutdown_wait)
    archive_janitor.shutdown(wait=not pool_worker_exit)
    if not pool_worker_exit:
      _finalize_archive_slots_if_needed(force=True)
    else:
      log_print(
          "Archive finalize skipped during pool_worker_exit teardown",
          flush=True,
      )
    _persist_dead_letters_if_needed(force=True)
    _flush_checkpoint_if_needed(force=True)
    _shutdown_ingest_pools(
        ingest_pool,
        force_terminate=pool_worker_exit,
    )
    from hpcperfstats.dbload.lib.sync_timedb_populate_pool import (
        reset_populate_pool_controller_for_tests,
        shutdown_populate_pool_controller,
    )

    shutdown_populate_pool_controller(force=pool_worker_exit)
    reset_populate_pool_controller_for_tests()


def parse_sync_timedb_argv(argv):
  """Parse CLI argv into ``(run_once, startdate, enddate)`` (same rules as ``sync_timedb``)."""
  argv_for_dates = list(argv)
  run_once = False
  if len(argv_for_dates) > 1 and argv_for_dates[1] == "once":
    run_once = True
    argv_for_dates = [argv_for_dates[0]] + argv_for_dates[2:]

  now_local = datetime.today()
  default_start = datetime.combine(
      now_local.date(), datetime.min.time()) - timedelta(days=days_to_process)
  default_end = now_local
  startdate, enddate = parse_start_end_dates(
      argv_for_dates, default_start, default_end)

  if (
      len(argv_for_dates) == 2
      and argv_for_dates[1] not in ("all", "current")
  ):
    try:
      single_day = datetime.strptime(argv_for_dates[1], "%Y-%m-%d")
    except ValueError:
      pass
    else:
      startdate = single_day
      enddate = datetime.combine(single_day.date(), datetime.max.time())

  if len(argv_for_dates) > 1 and argv_for_dates[1] in ('all', 'current'):
    startdate = argv_for_dates[1]
    enddate = None

  return run_once, startdate, enddate


def run_sync_timedb_supervisor_from_parsed(run_once, startdate, enddate):
  """Run one supervisor session after ``database_startup()`` (CLI or in-process tests)."""
  _reset_sync_runtime_caches()
  if startdate == 'all':
    log_print(
        "###Date Range of stats files to ingest: entire archive directory "
        "(no date filter)####")
  elif startdate == 'current':
    log_print(
        "###Date Range of stats files to ingest: entire archive directory "
        "(no date filter; newest-first / current mode)####")
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
    if cfg.get_sync_enable_cpuset_priority_budget():
      budget = cfg.derive_pipeline_cpuset_priority_budget()
      buckets = cfg.pipeline_cpu_process_buckets()
      log_print(
          "Pipeline cpuset budget effective_cores=%d sync_ingest=%d sync_archive=%d metrics=%d reserve=%d"
          % (
              budget["effective_cores"],
              budget["sync_ingest_cap"],
              budget["sync_archive_cap"],
              budget["metrics_cap"],
              budget["reserve_cap"],
          ),
          flush=True,
      )
      log_print(
          "Pipeline process buckets real_time=%s normal=%s best_effort=%s"
          % (
              ",".join(buckets["real_time"]),
              ",".join(buckets["normal"]),
              ",".join(buckets["best_effort"]),
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
        run_sync_timedb_supervisor_loop(
            directory,
            startdate,
            enddate,
            host_name_ext,
            manager_lock,
            archive_pool,
            run_once=run_once,
        )
      except MultiprocessingWorkerExitError as exc:
        hard_exit_pool_worker_error(exc)

    if DEBUG:
      log_print("sync_timedb finished")
  finally:
    manager.shutdown()


def run_ingest_entire_archive_once_for_tests():
  """In-process equivalent of ``python sync_timedb.py once all``.

  Uses the active Django database (e.g. pytest-django ``test_*``), unlike a
  subprocess which would connect to ``[DEFAULT] dbname`` from ini only. Forces
  single-process ingest so spawn workers do not open the non-test database.
  """
  old_inline = os.environ.get(_SYNC_TIMEDB_INGEST_INLINE_ENV)
  os.environ[_SYNC_TIMEDB_INGEST_INLINE_ENV] = "1"
  try:
    database_startup()
    run_sync_timedb_supervisor_from_parsed(True, "all", None)
  finally:
    if old_inline is None:
      os.environ.pop(_SYNC_TIMEDB_INGEST_INLINE_ENV, None)
    else:
      os.environ[_SYNC_TIMEDB_INGEST_INLINE_ENV] = old_inline


if __name__ == '__main__':
  # Use a mutable container so the SIGTERM handler can update state without
  # relying on `nonlocal` (which is only valid for enclosing function scopes).
  sigterm_received = {"value": False}

  def _sigterm_handler(signum, frame):
    sigterm_received["value"] = True
    shutdown_requested[0] = True
    raise SystemExit(143)

  previous_sigterm_handler = signal.getsignal(signal.SIGTERM)
  signal.signal(signal.SIGTERM, _sigterm_handler)
  try:
    set_daemon_process_title(name=SYNC_TIMEDB_PROCESS_TITLE, role="main")
    database_startup()
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
