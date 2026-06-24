#!/usr/bin/env python3
"""Load raw stats files into TimescaleDB (host_data, proc_data). Parses stats, applies hardware counter maps, computes deltas/arc, bulk-inserts, and optionally archives processed files (append to daily ``.tar``; seal to ``.tar.zst`` and raw/``.tar`` cleanup via the background ``ArchiveJanitor``). Runs in parallel with configurable chunk size.

**Hot path (supervisor thread):** discover → ingest → checkpoint → dispatch append (up to ``sync_archive_max_inflight_jobs`` disjoint daily-tar slots). Ingest never blocks on seal, zstd, raw delete, or uncompressed ``.tar`` removal.

**Cold path (``ArchiveJanitor`` thread):** day-debt queue consumed in time-sliced micro-batches (``archive_janitor_budget_seconds`` / ``archive_janitor_days_per_tick``). Snapshot/hints refresh and ``DAY_CLOSE`` scheduling run at supervisor startup (CLI ``all`` only), every ``rescan_every_chunks`` ingest chunks (default 10), and when the ingest queue drains to zero after chunk processing—all on the janitor thread. Per-day lock cleanup (once per tick), dedupe-before-seal, seal → async verify (``DayRawRemovalCoordinator``) with ingest-thread batched delete, or incremental raw/tar when preflight is off; DB head-ingest gate and disqualification union unchanged. Progress persists in ``.sync_archive_maint_hints.json`` v2 (``debt_queue``, ``day_phases``).

**Startup prefights (``all`` only):** when ``startdate == 'all'``, ``StartupRawRemovalPreflight`` and ``StartupDayClosePreflight`` (checkpoint-complete + filesystem-quiescent ``startup_quiescent_tar`` submit) run on background threads; ``StartupTailIngestCoordinator`` consumes discover-slice tail enqueue on another thread. Default ``sync_startup_drain_day_close_before_ingest=yes`` blocks first ingest until discover + startup tail ingest + deletion prefights complete (not until async DAY_CLOSE idles). Date-range runs skip startup maintenance and begin ingest immediately.

Append and raw delete stay DB-gated when ``sync_archive_require_db_head_ingest=yes``. Finalize uses soft defer (``allow_defer``) under ingest backlog instead of blocking the supervisor.

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
import math
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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date as date_cls
from datetime import datetime, timedelta, timezone
from enum import Enum
from functools import partial
from hpcperfstats.dbload.lib.django_bootstrap import ensure_django
from hpcperfstats.dbload.lib.process_title import (
    apply_pool_worker_process_title,
    set_daemon_process_title,
    set_daemon_thread_title,
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
    MultiprocessingWorkerExitError,
    alive_pool_worker_count,
    async_result_get_watch_pool,
    close_pool_bounded,
    hard_exit_pool_worker_error,
    imap_sliding_window_watch_pool,
    imap_unordered_watch_pool,
    pool_workers_all_idle,
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
    build_live_unprocessed_by_tar_for_reconcile,
    build_seal_disqualified_daily_tars,
    build_remaining_raw_stats_by_daily_gz,
    build_unprocessed_raw_by_daily_tar,
    calendar_days_checkpoint_ingest_complete,
    daily_tar_eligible_for_day_close_submit,
    resolve_unmapped_closed_raw_daily_tars,
    daily_tar_path_for_stats_path,
    daily_tar_path_from_compressed,
    calendar_date_from_daily_tar_path,
    days_ingest_complete_by_checkpoint,
    oldest_checkpoint_blocked_tar,
    on_disk_unprocessed_paths_for_tar,
    prepend_checkpoint_blocked_paths_to_pending,
    unprocessed_tar_paths_still_on_disk,
    daily_tar_paths_for_archive_job_tasks,
    daily_tar_paths_for_stats_paths,
    daily_tar_paths_from_pending_archive_tasks,
    filter_files_to_add_to_archive,
    get_existing_archive_members,
    get_existing_archive_members_for_daily_archive,
    invalidate_after_daily_tar_mutation,
    invalidate_daily_archive_members_cache,
    iter_daily_tar_paths,
    replace_corrupt_tar_from_compressed_backup,
    ensure_daily_tar_restored_for_append,
    cap_pending_stats_file_list,
    INGEST_PARSE_FAILED_QUARANTINE_REASON,
    quarantine_ingest_failed_raw_path,
    raw_stats_path_needs_tar_append,
    rescan_pending_stats_files,
    set_archive_members_invalidation_hook,
    should_seal_daily_tar,
    stats_file_is_active_segment,
    stats_path_ingest_sort_epoch,
    verify_tar_archive_readable,
    _derive_stats_path_date,
    normalize_daily_compressed_path,
)
from hpcperfstats.dbload.lib.sync_timedb_ingest_worker_diagnostics import (
    apply_ingest_pool_worker_init,
    clear_dispatch_worker_stages,
    clear_worker_stage,
    count_worker_registry_entries,
    format_worker_stages_snapshot,
    prune_stale_worker_stages,
    record_worker_stage,
    seed_dispatch_worker_stages,
    update_worker_substage,
    worker_registry_shows_recent_progress,
)
from hpcperfstats.dbload.lib.sync_timedb_async_day_close import AsyncDayCloseCoordinator
from hpcperfstats.dbload.lib.file_locking import file_write_lock
from hpcperfstats.dbload.lib.sync_timedb_archive_dispatch import ArchiveDispatchCoordinator
from hpcperfstats.dbload.lib.sync_timedb_archive_janitor import ArchiveJanitor
from hpcperfstats.dbload.lib.sync_timedb_day_raw_removal import DayRawRemovalCoordinator
from hpcperfstats.dbload.lib.sync_timedb_startup_raw_removal import (
    PHASE_VERIFICATION_COMPLETE,
    StartupRawRemovalPreflight,
)
from hpcperfstats.dbload.lib.sync_timedb_startup_day_close import (
    StartupDayClosePreflight,
)
from hpcperfstats.dbload.lib.sync_timedb_startup_tail_ingest import (
    StartupTailIngestCoordinator,
)
from hpcperfstats.dbload.lib.sync_timedb_startup_archive_scan import (
    StartupArchiveScanCoordinator,
)
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
    redis_lookup_full_members,
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

# How many files to proccess and archive at once
chunk_size = 1000
# Max paths per ``tar -T`` batch (limits list-file size; argv stays tiny).
tar_append_batch_size = 256
# Rescan stats directory after this many processed chunks
rescan_every_chunks = 10
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
    clear_worker_stage()


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


def _prewarm_archive_members_redis_for_days(day_items):
  """Single-flight populate on supervisor before imap when Redis L2 is cold."""
  summary_parts = []
  if not archive_members_redis_enabled():
    return "-"
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      _daily_archive_members_cache_key,
      _resolve_sealed_daily_archive_path,
  )
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      ArchiveDayIngestSkipError,
  )

  for compressed, day_token in day_items or ():
    canonical = normalize_daily_compressed_path(compressed)
    cache_key = _daily_archive_members_cache_key(canonical)
    keys = build_archive_members_redis_keys(cache_key)
    if redis_members_cache_is_fully_warm(keys):
      summary_parts.append("%s:redis_warm" % day_token)
      continue
    sealed_path = _resolve_sealed_daily_archive_path(canonical)
    tar_path = daily_tar_path_from_compressed(canonical)
    if sealed_path is None and not os.path.isfile(tar_path):
      summary_parts.append("%s:no_daily_archive" % day_token)
      continue
    log_print(
        "Prewarming archive members Redis for day=%s sealed=%s"
        % (day_token, sealed_path or tar_path),
        flush=True,
    )
    try:
      get_existing_archive_members_for_daily_archive(canonical)
      summary_parts.append("%s:prewarmed" % day_token)
    except ArchiveDayIngestSkipError:
      summary_parts.append("%s:day_ingest_skip" % day_token)
    except ArchiveMembersRedisUnavailableError as exc:
      _exit_on_archive_members_redis_unavailable(exc)
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


def _prewarm_archive_members_redis_for_chunk(paths):
  """Single-flight populate on supervisor before imap when Redis L2 is cold."""
  summary = _prewarm_archive_members_redis_for_days(
      list(_unique_daily_compressed_archives_for_paths(paths, tgz_archive_dir).items()),
  )
  log_print("INFO: chunk prewarm days=%s" % summary, flush=True)
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
  summary = _prewarm_archive_members_redis_for_days(day_items)
  log_print("INFO: archive chunk prewarm days=%s" % summary, flush=True)
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
    self.async_day_close = None
    self.worker_registry = None
    self.chunk_prewarm_summary = "-"

  def note_imap_completion(self):
    self.last_imap_completion_monotonic = time.monotonic()

  def seconds_since_last_imap_completion(self):
    last = self.last_imap_completion_monotonic
    if last is None:
      return -1.0
    return max(0.0, time.monotonic() - float(last))

  def format_async_day_close_detail(self):
    coord = self.async_day_close
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
  if (
      active_pool is not None
      and sample_list
      and pool_workers_all_idle(active_pool)
      and not worker_registry_shows_recent_progress(registry, pool=active_pool)
  ):
    return False, "idle_pool_ghost_inflight"
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
  if not day_hint:
    if callable(day_hint_from_sample_fn) and sample:
      day_hint = day_hint_from_sample_fn(sample)
    elif sample:
      day_hint = _calendar_day_hint_from_sealed_paths(sample)
  if not day_hint:
    return False, "no_day_hint"
  if archive_members_populate_shows_progress_for_day(
      day_hint,
      tgz_archive_dir,
      progress_state=progress_state,
  ):
    return True, "redis_populate_active"
  if archive_members_redis_enabled():
    try:
      day_date = date_cls.fromisoformat(day_hint)
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
      " pipeline_overlap_mode=%s async_day_close=%s chunk_prewarm=%s"
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
          diag.format_async_day_close_detail(),
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
    alive_workers = alive_pool_worker_count(pool)
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


def _make_ingest_stall_poll_fn(
    tracker,
    progress_state,
    stall_diagnostics=None,
    *,
    day_hint_from_sample_fn=None,
):
  """Defer pool imap stall abort while Redis populate shows progress."""

  def on_stall_poll(consecutive, context, pool_health_context):
    del context
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
    if defer_on:
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
    return False

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
    db_writer_pool=None,
    archive_pool=None,
    stall_poll_state=None,
    stall_diagnostics=None,
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
      "db_writer_pool": db_writer_pool,
      "archive_pool": archive_pool,
      "in_flight_sample_fn": (
          tracker.sample_in_flight if tracker is not None else None
      ),
  }
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
          tracker, stall_poll_state, stall_diagnostics=stall_diagnostics,
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
  cap = cfg.get_sync_ingest_imap_inflight_cap()
  if cap <= 0:
    cap = thread_count
  return max(1, min(int(path_count), int(thread_count), int(cap)))


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
    db_writer_pool=None,
    archive_pool=None,
    stall_diagnostics=None,
    pending_tail=None,
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
  pool_health_context = {
      "ingest_pool": ingest_pool if ingest_pool is not None else pool,
      "db_writer_pool": db_writer_pool,
      "archive_pool": archive_pool,
      "in_flight_sample_fn": (
          tracker.sample_in_flight if tracker is not None else None
      ),
  }
  supplement_log_state = {"logged": False}

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

  def _giant_pool_supplement_paths_fn(slots_needed, in_flight_paths):
    if not cfg.get_sync_ingest_giant_pool_supplement_enabled():
      return []
    if slots_needed <= 0 or not pending_tail:
      return []
    if not any_giant_ingest_budget_in_flight(in_flight_paths):
      return []
    exclude = set(in_flight_paths or ())
    if tracker is not None:
      exclude.update(tracker.batch_seen_paths())
    selected = list(
        iter_giant_supplement_paths(
            pending_tail,
            limit=int(slots_needed),
            exclude=exclude,
        ),
    )
    if not selected:
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
          for path in (pending_tail or ())[:5]
          if path
      ]
      log_print(
          "INFO: sync_timedb: giant pool supplement begin pending_tail_n=%d "
          "pending_tail_sample=%s in_flight_giants=%s selected=%s "
          "idle_slots=%d trigger_budget_s=%.0f"
          % (
              len(pending_tail or ()),
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
          tracker, stall_poll_state, stall_diagnostics=stall_diagnostics,
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
    completed_path = _imap_ingest_result_path(item)
    if dispatch_registry is not None and completed_path:
      clear_dispatch_worker_stages(dispatch_registry, [completed_path])
    yield item


def _clear_ingest_worker_file_caches():
  """Drop per-process ingest caches after a file (safe when worker recycles)."""
  sync_timedb_host_itimes.reset_host_itimes_caches()


def _release_ingest_worker_heap():
  """Return parse heap to the OS on Linux when ``sync_ingest_malloc_trim_after_file``."""
  _clear_ingest_worker_file_caches()
  if not cfg.get_sync_ingest_malloc_trim_after_file():
    return
  gc.collect()
  try:
    libc = ctypes.CDLL("libc.so.6")
    libc.malloc_trim(0)
  except (OSError, AttributeError):
    pass


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


def _log_ingest_worker_file_completion(
    stats_file,
    *,
    elapsed_s,
    parse_elapsed_s=None,
    stats_rows=None,
    proc_rows=None,
    stage=None,
):
  """Worker-side per-file completion log (size, rows, RSS)."""
  size_bytes = stats_file_size_bytes(stats_file)
  parts = [
      "ingest file completed path=%s" % stats_file,
      "size_bytes=%d" % size_bytes,
      "elapsed_s=%.1f" % float(elapsed_s),
      "worker_rss_mib=%.1f" % _worker_rss_mib(),
  ]
  if parse_elapsed_s is not None:
    parts.append("parse_elapsed_s=%.1f" % float(parse_elapsed_s))
  if stats_rows is not None:
    parts.append("stats_rows=%d" % int(stats_rows))
  if proc_rows is not None:
    parts.append("proc_rows=%d" % int(proc_rows))
  if stage:
    parts.append("stage=%s" % stage)
  remaining_pair = _sealed_archive_ingest_remaining_pair()
  if remaining_pair is not None:
    remaining, total = remaining_pair
    parts.append("remaining=%d/%d" % (remaining, total))
  log_print(" ".join(parts), flush=True)


def _spawn_pool_recycle_kwargs():
  maxtasks = cfg.get_sync_ingest_pool_maxtasksperchild()
  if maxtasks > 0:
    return {"maxtasksperchild": int(maxtasks)}
  return {}

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


def _exit_on_archive_members_redis_unavailable(exc):
  """Fatal exit when Redis L2 contract fails during ingest or startup."""
  log_print("ERROR: %s" % exc, flush=True)
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
class ParseTask:
  path: str


@dataclass(frozen=True)
class DBWriteTask:
  path: str
  payload: object
  need_archival: bool
  parse_elapsed_s: float = 0.0


@dataclass(frozen=True)
class ArchiveTask:
  archive_info: tuple
  attempt: int = 1


def _db_write_task_tuple(task):
  return (
      task.path,
      task.payload,
      task.need_archival,
      task.parse_elapsed_s,
  )


def _db_writer_stage_batch_size(target_chunk_size, ingest_queue_high):
  """Bound parse->writer payload staging to limit peak memory."""
  queue_cap = max(1, int(ingest_queue_high) // 8)
  stage_max = cfg.get_sync_db_writer_stage_max_batch()
  return max(1, min(int(target_chunk_size), stage_max, queue_cap))


def _shutdown_ingest_pools(ingest_pool, db_writer_pool, *, force_terminate=False):
  """Bounded shutdown for ingest pools (terminate after worker OOM/SIGKILL)."""
  close_pool_bounded(ingest_pool, force_terminate=force_terminate)
  close_pool_bounded(db_writer_pool, force_terminate=force_terminate)


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


def _maybe_wait_tree_rss_before_chunk(ingest_pool, db_writer_pool, archive_pool):
  """Defer starting a new chunk while the process tree is over the RSS cap."""
  limit_mb = cfg.get_sync_process_tree_rss_limit_mb()
  if limit_mb <= 0:
    return
  limit_bytes = int(limit_mb) * 1024 * 1024
  for attempt in range(60):
    tree_bytes = read_sync_timedb_tree_rss_bytes(
        ingest_pool, db_writer_pool, archive_pool)
    if tree_bytes <= 0 or tree_bytes <= limit_bytes:
      return
    if attempt == 0:
      breakdown = format_tree_rss_breakdown_mb(
          ingest_pool, db_writer_pool, archive_pool)
      log_print(
          "sync_timedb tree RSS %.1f MiB exceeds limit %d MiB "
          "(supervisor=%.1f ingest=%.1f db_writer=%.1f archive=%.1f); "
          "deferring chunk dispatch"
          % (
              breakdown["tree_total_mb"],
              limit_mb,
              breakdown["supervisor_mb"],
              breakdown["ingest_pool_mb"],
              breakdown["db_writer_pool_mb"],
              breakdown["archive_pool_mb"],
          ),
          flush=True,
      )
    time.sleep(_TREE_RSS_DEFER_SLEEP_SECONDS)


def _maybe_apply_tree_rss_governor(
    chunk_counter,
    ingest_pool,
    db_writer_pool,
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
  tree_bytes = read_sync_timedb_tree_rss_bytes(
      ingest_pool, db_writer_pool, archive_pool)
  if exit_mb > 0 and tree_bytes > int(exit_mb) * 1024 * 1024:
    breakdown = format_tree_rss_breakdown_mb(
        ingest_pool, db_writer_pool, archive_pool)
    log_print(
        "ERROR: sync_timedb process tree RSS %.1f MiB exceeds exit cap %d MiB "
        "(supervisor=%.1f ingest=%.1f db_writer=%.1f archive=%.1f); exiting"
        % (
            breakdown["tree_total_mb"],
            exit_mb,
            breakdown["supervisor_mb"],
            breakdown["ingest_pool_mb"],
            breakdown["db_writer_pool_mb"],
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
    db_writer_pool=None,
    archive_pool=None,
):
  """``os._exit`` immediately — do not wait on pool terminate or context managers."""
  del ingest_pool, db_writer_pool, archive_pool
  hard_exit_pool_worker_error(exc)


def _reraise_or_handle_pool_worker_exit(
    exc,
    *,
    ingest_pool,
    db_writer_pool,
    archive_pool=None,
):
  """Terminate ingest/archive pools and re-raise worker death."""
  if isinstance(exc, MultiprocessingWorkerExitError):
    ctx = getattr(exc, "context", "") or "pool_worker_exit"
    terminate_pool_bounded(ingest_pool, context=ctx)
    terminate_pool_bounded(db_writer_pool, context=ctx)
    terminate_pool_bounded(archive_pool, context=ctx)
    raise
  raise exc


def _drain_db_write_tasks(
    *,
    parse_tasks,
    manager_lock,
    db_writer_pool,
    file_states,
    successful_paths,
    files_to_be_archived,
    chunk_ingest_finished,
    pending_total,
    chunk_counter=0,
    ingest_pool=None,
    archive_pool=None,
    stall_diagnostics=None,
    chunk_paths_norm=None,
):
  """Write queued parse payloads and return updated finished count."""
  if not parse_tasks:
    return chunk_ingest_finished
  chunk_paths_norm = chunk_paths_norm or set()
  writer_fn = partial(_db_writer_worker, manager_lock)
  task_batch = list(parse_tasks)
  parse_tasks.clear()
  task_paths = [task.path for task in task_batch]
  write_tracker = (
      _IngestPoolInFlightTracker(task_paths)
      if db_writer_pool is not None
      and not _sync_timedb_ingest_inline_requested()
      else None
  )
  if _sync_timedb_ingest_inline_requested() or db_writer_pool is None:
    write_results_iter = (writer_fn(_db_write_task_tuple(task)) for task in task_batch)
  else:
    write_results_iter = _imap_ingest_pool(
        db_writer_pool,
        writer_fn,
        [_db_write_task_tuple(task) for task in task_batch],
        context="sync_timedb ingest db_writer pool",
        tracker=write_tracker,
        chunk_counter=chunk_counter,
        pending_count=pending_total,
        ingest_pool=ingest_pool,
        db_writer_pool=db_writer_pool,
        archive_pool=archive_pool,
        stall_diagnostics=stall_diagnostics,
    )
  try:
    for result in write_results_iter:
      stats_fname, need_archival, ingest_ok, elapsed_s = result
      if write_tracker is not None:
        write_tracker.complete(stats_fname)
      if ingest_ok:
        _transition_file_state(file_states, stats_fname, SyncFileState.WRITTEN)
        successful_paths.append(stats_fname)
        if should_archive and need_archival:
          files_to_be_archived.append(stats_fname)
        remaining = _ingest_remaining_count(pending_total, chunk_ingest_finished)
        chunk_ingest_finished += 1
        _log_sync_timedb_ingest_completed(
            stats_fname,
            elapsed_s,
            remaining,
            stage="write",
            supplement=_ingest_path_is_supplement(stats_fname, chunk_paths_norm),
        )
  except MultiprocessingWorkerExitError:
    raise
  return chunk_ingest_finished


class SyncFileState(str, Enum):
  DISCOVERED = "discovered"
  PARSED = "parsed"
  WRITTEN = "written"
  ARCHIVE_QUEUED = "archive_queued"
  ARCHIVE_FAILED_RETRYABLE = "archive_failed_retryable"
  ARCHIVED = "archived"


_SYNC_STATE_TRANSITIONS = {
    SyncFileState.DISCOVERED: {
        SyncFileState.PARSED,
        SyncFileState.WRITTEN,
        SyncFileState.ARCHIVE_QUEUED,
    },
    SyncFileState.PARSED: {
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


def _transition_file_state(file_states, path, new_state):
  """Best-effort state transition validator for per-file supervisor state."""
  current = file_states.get(path)
  if current is None:
    file_states[path] = new_state
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


def _calendar_days_ingest_complete_for_heavy_pass(
    *,
    chunk_paths,
    pending_before,
    pending_after,
    archive_data_dir,
    host_name_ext,
    tgz_archive_dir,
    checkpoint_path,
    maintenance_snapshot=None,
):
  """Calendar days eligible for ``day_ingest_complete`` heavy maintenance."""
  pending_drained = _completed_ingest_calendar_days(
      chunk_paths=chunk_paths,
      pending_before=pending_before,
      pending_after=pending_after,
  )
  if not pending_drained:
    return []
  return calendar_days_checkpoint_ingest_complete(
      pending_drained,
      archive_data_dir=archive_data_dir,
      host_name_ext=host_name_ext,
      tgz_archive_dir=tgz_archive_dir,
      checkpoint_path=checkpoint_path,
      pending_stats_paths=pending_after,
      maintenance_snapshot=maintenance_snapshot,
  )


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


def _log_sync_timedb_ingest_completed(
    stats_fname,
    elapsed_s,
    remaining,
    *,
    stage=None,
    supplement=False,
):
  stage_suffix = " stage=%s" % stage if stage else ""
  supplement_suffix = " supplement=yes" if supplement else ""
  size_bytes = stats_file_size_bytes(stats_fname)
  remaining_count = max(0, int(remaining))
  log_print(
      "Completed file %s - processed in %.1fs - %d remaining to process "
      "size_bytes=%d%s%s"
      % (
          stats_fname,
          float(elapsed_s),
          remaining_count,
          size_bytes,
          stage_suffix,
          supplement_suffix,
      ),
      flush=True,
  )


def _log_db_complete_skip(stats_file, reason, *, elapsed_s=None):
  size_bytes = stats_file_size_bytes(stats_file)
  elapsed_suffix = (
      " elapsed_s=%.1f" % float(elapsed_s) if elapsed_s is not None else ""
  )
  log_print(
      "No missing timestamps found for %s reason=%s size_bytes=%d%s"
      % (stats_file, reason, size_bytes, elapsed_suffix),
      flush=True,
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
    return (stats_file, None, False, True, parse_elapsed)
  return (stats_file, None, False, False, parse_elapsed)


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
      return (stats_file, None, False, False, 0.0)
  try:
    return _run_ingest_timed(
        stats_file,
        "parse",
        impl,
    )
  except IngestPerFileTimeoutError as exc:
    _log_ingest_per_file_timeout(exc)
    return (stats_file, None, False, False, exc.elapsed_s)
  except IngestArchiveLookupBudgetExceededError as exc:
    _log_ingest_archive_lookup_budget_exceeded(exc)
    return (stats_file, None, False, False, 0.0)
  finally:
    _release_ingest_worker_heap()


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
    log_print("initial timestamp not found")
    return (
        True,
        _parse_failure_after_quarantine(
            stats_file, parse_elapsed_fn(), error_detail="initial timestamp not found",
        ),
    )
  if not host:
    log_print("initial host not found in %s" % stats_file)
    return (
        True,
        _parse_failure_after_quarantine(
            stats_file, parse_elapsed_fn(), error_detail="initial host not found",
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
    _log_db_complete_skip(
        stats_file,
        db_complete_reason or "db_complete_full_scan",
        elapsed_s=parse_elapsed_fn(),
    )
    need_archival = raw_stats_path_needs_tar_append(
        stats_file,
        tgz_archive_dir,
        first_ts=t,
    )
    return True, (stats_file, None, need_archival, True, parse_elapsed_fn())
  return False, (int(start_idx), need_archival)


def _ingest_reconcile_skip_result(stats_file):
  """Return an ingest result tuple when DB idempotency says re-dispatch is unnecessary."""
  done, result = _resolve_streaming_ingest_start(stats_file, lambda: 0.0)
  if done:
    return result
  return None


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
        log_print("error: process data failed: ", str(e))
        log_print("Possibly corrupt file: %s" % stats_file)
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
      return (stats_file, (stats, proc_stats), need_archival, True, _parse_elapsed())
    except FileNotFoundError:
      load_err = "Stats file disappeared: %s" % stats_file
      log_print(load_err)
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
        _stats_file, _payload, need_archival, early_ok, _early_parse_elapsed = result
        if not early_ok:
          return (_stats_file, need_archival, False, time.time() - t0)
        if _payload is None:
          return (_stats_file, need_archival, True, time.time() - t0)
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
          log_print("error: process data failed: ", str(e))
          log_print("Possibly corrupt file: %s" % stats_file)
          _stats_file, _payload, _need, early_ok, _ = _parse_failure_after_quarantine(
              stats_file, _parse_elapsed(), error_detail=str(e),
          )
          return (_stats_file, _need, False, time.time() - t0)
        if total_stats_rows == 0 and total_proc_rows == 0:
          if DEBUG:
            log_print("Unable to process stats file %s" % stats_file)
          _stats_file, _payload, _need, early_ok, _ = _parse_failure_after_quarantine(
              stats_file, _parse_elapsed(), error_detail="empty stats and proc_stats",
          )
          return (_stats_file, _need, False, time.time() - t0)
      elapsed = time.time() - t0
      if ingest_ok:
        _log_ingest_worker_file_completion(
            stats_file,
            elapsed_s=elapsed,
            parse_elapsed_s=_parse_elapsed(),
            stats_rows=total_stats_rows,
            proc_rows=total_proc_rows,
            stage="ingest",
        )
      return (stats_file, need_archival, ingest_ok, elapsed)
    except FileNotFoundError:
      load_err = "Stats file disappeared: %s" % stats_file
      log_print(load_err)
      _stats_file, _payload, _need, early_ok, _ = _parse_failure_after_quarantine(
          stats_file, _parse_elapsed(), error_detail=load_err,
      )
      return (_stats_file, _need, False, time.time() - t0)


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
        log_print("Invalid stats file path: %s" % stats_file)
        return (stats_file, None, False, False, _parse_elapsed())
      if stats_file_is_active_segment(stats_file):
        if DEBUG:
          log_print("Skipping active segment (still linked to current): %s" % stats_file)
        return (stats_file, None, False, False, _parse_elapsed())
      if _should_stream_stats_file(stats_file, stats_file_contents):
        return _parse_stats_file_payload_impl_streaming(stats_file)
      lines, load_err = load_stats_file_lines(stats_file, stats_file_contents)
      if load_err is not None:
        log_print(load_err)
        return _parse_failure_after_quarantine(
            stats_file, _parse_elapsed(), error_detail=load_err,
        )
      t, _jid, host = parse_first_timestamp_line(lines)
      if t is None:
        log_print("initial timestamp not found")
        return _parse_failure_after_quarantine(
            stats_file, _parse_elapsed(), error_detail="initial timestamp not found",
        )
      if not host:
        log_print("initial host not found in %s" % stats_file)
        return _parse_failure_after_quarantine(
            stats_file, _parse_elapsed(), error_detail="initial host not found",
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
        _log_db_complete_skip(
            stats_file,
            db_complete_reason or "db_complete_full_scan",
            elapsed_s=_parse_elapsed(),
        )
        need_archival = raw_stats_path_needs_tar_append(
            stats_file,
            tgz_archive_dir,
            first_ts=t,
        )
        return (stats_file, None, need_archival, True, _parse_elapsed())
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
        log_print("error: process data failed: ", str(e))
        log_print("Possibly corrupt file: %s" % stats_file)
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
      return (stats_file, (stats, proc_stats), need_archival, True, _parse_elapsed())
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
      _release_ingest_worker_heap()


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
  record_worker_stage(stats_file, "ingest", substage="worker_entry")
  try:
    return _run_ingest_timed(
        stats_file,
        "ingest",
        lambda: _add_stats_file_to_db_impl(
            lock, stats_file, stats_file_contents=stats_file_contents
        ),
    )
  except IngestPerFileTimeoutError as exc:
    _log_ingest_per_file_timeout(exc)
    return (stats_file, False, False, exc.elapsed_s)
  except IngestArchiveLookupBudgetExceededError as exc:
    _log_ingest_archive_lookup_budget_exceeded(exc)
    return (stats_file, False, False, 0.0)
  finally:
    _release_ingest_worker_heap()


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
      stats_file, payload, need_archival, ingest_ok, _parse_elapsed = _parse_stats_file_payload(
          stats_file,
          stats_file_contents=stats_file_contents,
          use_ingest_timer=False,
      )
      if not ingest_ok:
        return (stats_file, need_archival, False, time.time() - t0)
      if payload is None:
        return (stats_file, need_archival, True, time.time() - t0)
      stats, proc_stats = payload
      stats_rows = len(stats)
      proc_rows = len(proc_stats)
      stats_file, need_archival, ingest_ok = _write_stats_payload_to_db(
          lock, stats_file, stats, proc_stats, need_archival=need_archival
      )
      elapsed = time.time() - t0
      if ingest_ok:
        _log_ingest_worker_file_completion(
            stats_file,
            elapsed_s=elapsed,
            parse_elapsed_s=_parse_elapsed,
            stats_rows=stats_rows,
            proc_rows=proc_rows,
            stage="ingest",
        )
      return (stats_file, need_archival, ingest_ok, elapsed)
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


def _append_to_tar(tar_path, file_paths):
  """Append file_paths to tar at tar_path. Does nothing if file_paths is empty.

  Uses GNU/BSD ``tar -r -f`` with ``--null -T`` so argv stays tiny, names may
  contain spaces, and we do not rely on ``-u`` (mtime) vs. Python-side filters.
  Skips paths that disappeared before append (race). Batches via
  ``tar_append_batch_size``.
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
          lf.write(os.fsencode(p) + b"\0")
      tar_bin = shutil.which("tar") or "/bin/tar"
      with file_write_lock(tar_path):
        tar_args = [
            tar_bin,
            "-r" if tar_exists else "-c",
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
  with _sync_worker_db_task():
    return _archive_stats_files_body(archive_info)


def _archive_stats_files_body(archive_info):
  archive_fname, stats_files = archive_info
  stats_files, _skipped = filter_paths_head_ingested(stats_files, log_fn=log_print)
  if not stats_files:
    return True
  archive_tar_fname = daily_tar_path_from_compressed(archive_fname)
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
    existing_members = get_existing_archive_members(archive_tar_fname)

  # Corrupt/truncated .tar can make Python's tarfile reader return {} while GNU
  # tar still refuses append (exit 2). Recover before append so we never raise
  # without trying restore-from-.gz (same as post-append path).
  if os.path.isfile(archive_tar_fname) and not verify_tar_archive_readable(
      archive_tar_fname):
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
      existing_members = get_existing_archive_members(archive_tar_fname)
    else:
      existing_members = {}

  stats_files_to_tar = filter_files_to_add_to_archive(
      stats_files, existing_members, debug=DEBUG)
  if stats_files_to_tar:
    if not _restore_daily_tar_or_log_failure(
        archive_tar_fname, context="before append"):
      return False
  try:
    _append_to_tar(archive_tar_fname, stats_files_to_tar)
  except (subprocess.CalledProcessError, RuntimeError) as exc:
    log_print(
        "ERROR: tar append failed for %s (%s); leaving raw stats files in place"
        % (archive_tar_fname, exc),
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
      existing_after = (
          get_existing_archive_members(archive_tar_fname)
          if os.path.exists(archive_tar_fname)
          else {}
      )
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
              "ERROR: retry tar append failed for %s (%s); leaving raw stats "
              "files in place" % (archive_tar_fname, exc),
              flush=True,
          )
          return False
      if not verify_tar_archive_readable(archive_tar_fname):
        log_print(
            "ERROR: daily tar still unreadable after recovery append; leaving "
            "raw stats files in place: %s" % archive_tar_fname,
            flush=True,
        )
        return False
  if stats_files_to_tar:
    invalidate_after_daily_tar_mutation(
        archive_fname,
        reason="tar_append",
        log_fn=log_print,
    )
  return True


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


def _resolve_archive_maintenance_interval_seconds(raw_value):
  """Normalize archive maintenance interval to a safe finite positive float."""
  default_interval = float(8 * 3600)
  try:
    interval = float(raw_value)
  except (TypeError, ValueError):
    return default_interval, "invalid"
  if (not math.isfinite(interval)) or interval <= 0:
    return default_interval, "non_finite_or_non_positive"
  return interval, None


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
  ingest_queue_high = ingest_queue_max
  ingest_queue_low = max(1, int(ingest_queue_max * 0.5))
  archive_queue_high = archive_queue_max
  archive_queue_low = max(1, int(archive_queue_max * 0.5))
  archive_retry_max_attempts = max(1, int(cfg.get_sync_archive_retry_max_attempts()))
  archive_retry_backoff_base = max(0.0, float(cfg.get_sync_archive_retry_backoff_base_seconds()))
  archive_retry_backoff_max = max(0.0, float(cfg.get_sync_archive_retry_backoff_max_seconds()))
  ingest_first_durability = bool(cfg.get_sync_enable_ingest_first_durability_mode())
  ingest_t0 = time.time()
  run_startup_maintenance = startdate == "all"

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

  def _dispatch_due_archive_retries():
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

  startup_preflight = None
  startup_day_close = None
  startup_tail_ingest = None
  day_raw_removal = None
  async_day_close = None
  delete_phase_active = False
  day_close_rescan_pending = False
  chunk_in_progress = False
  active_chunk_ingest_tracker = None
  max_ingest_sort_epoch_by_tar: dict[str, int] = {}

  def _get_quarantine_skip_paths():
    captured = _capture_disqualification_inputs()
    skip_paths = set(captured["pending_stats_paths"])
    skip_paths |= set(captured["inflight_paths"])
    for paths in captured["pending_append_by_daily_tar"].values():
      skip_paths |= set(paths)
    if startup_preflight is not None:
      skip_paths |= startup_preflight.paths_pending_startup_delete()
    if day_raw_removal is not None:
      skip_paths |= day_raw_removal.paths_pending_delete()
    return skip_paths

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
    return set(build_seal_disqualified_daily_tars(
        tgz_archive_dir=tgz_archive_dir,
        remaining_raw_by_gz=None,
        inflight_paths=captured["inflight_paths"],
        pending_append_by_daily_tar=captured["pending_append_by_daily_tar"],
        in_flight_archive_tars=captured["in_flight_archive_tars"],
        pending_archive_task_tars=captured["pending_archive_task_tars"],
        unmapped_closed_raw_tars=set(unmapped or ()),
    ))

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

  def _maybe_enqueue_immediate_day_close(*, context: str):
    if not tgz_archive_dir:
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
    for tar_path in candidates:
      if async_day_close is not None:
        if len(async_day_close.active_or_submitted_tar_paths()) >= max_inflight:
          break
      if async_day_close.submit_day_close(
          tar_path,
          reason="day_ingest_complete:%s" % context,
          disqualified_daily_tars=disqualified,
      ):
        submitted_any = True
    if submitted_any:
      archive_janitor.signal_work_available()

  def _apply_archive_finalize_results(deferred_paths, results):
    nonlocal checkpoint_dirty_count
    nonlocal dead_letter_dirty
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
      if result:
        invalidate_after_daily_tar_mutation(
            archive_task.archive_info[0],
            reason="archive_finalize",
            log_fn=log_print,
        )
        for p in archive_paths:
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
    finalized_paths = []
    for task_payload, result in zip(deferred_paths, results):
      if result:
        finalized_paths.extend(task_payload.get("paths") or ())
    if finalized_paths:
      _record_ingest_sort_epochs_for_paths(finalized_paths)
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
      archive_dispatch.prune_finished_slots(
          lambda slot: _finalize_archive_slot(
              slot, force=True, context=context or "prune_ready"))
      return bool(archive_dispatch.slots)

    finalized_any = False
    for slot in list(archive_dispatch.slots):
      if _finalize_archive_slot(
          slot, force=True, allow_defer=allow_defer, context=context):
        finalized_any = True
        archive_dispatch.slots = [
            s for s in archive_dispatch.slots if s is not slot
        ]
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
      "ingest_gate_cleared": (
          not run_startup_maintenance
          or not cfg.get_sync_startup_drain_day_close_before_ingest()
      ),
      "get_startup_snapshot": lambda: None,
      "get_accrual_snapshot": lambda: None,
      "warned_live_reconcile_fallback": False,
  }

  def _resolve_reconcile_maintenance_snapshot():
    if not reconcile_refs["ingest_gate_cleared"]:
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

  def _live_unprocessed_by_tar_for_reconcile():
    snapshot, _source = _resolve_reconcile_maintenance_snapshot()
    return build_live_unprocessed_by_tar_for_reconcile(
        directory,
        host_name_ext,
        tgz_archive_dir,
        checkpoint_path=checkpoint_path,
        pending_stats_paths=list(pending_stats_files),
        maintenance_snapshot=snapshot,
    )

  def _apply_handoff_priority_to_pending(pending):
    if not handoff_priority_paths:
      return list(pending or ())
    blocked = sorted(handoff_priority_paths)
    return prepend_checkpoint_blocked_paths_to_pending(
        pending,
        blocked,
        exclude=inflight_archive_paths,
    )

  def _cap_pending_stats_with_handoff_priority(paths):
    if not handoff_priority_paths:
      return _cap_pending_stats_files_list(paths, ingest_queue_max)
    merged = _apply_handoff_priority_to_pending(paths)
    priority_n = len(handoff_priority_paths)
    tail_budget = max(0, ingest_queue_max - priority_n)
    if len(merged) <= ingest_queue_max:
      return merged
    head = merged[:priority_n]
    tail = merged[priority_n:]
    if len(tail) > tail_budget:
      log_print(
          "sync_timedb: handoff priority cap handoff_priority_n=%d capped_tail=%d "
          "pending=%d max=%d"
          % (priority_n, len(tail) - tail_budget, len(merged), ingest_queue_max),
          flush=True,
      )
      tail = tail[:tail_budget]
    return head + tail

  def _cap_pending_after_rescan(paths):
    cap_t0 = time.time()
    _snapshot, source = _resolve_reconcile_maintenance_snapshot()
    log_print(
        "sync_timedb: pending reconcile cap begin source=%s" % source,
        flush=True,
    )
    unprocessed = _live_unprocessed_by_tar_for_reconcile()
    tar_norm = oldest_checkpoint_blocked_tar(
        unprocessed, tgz_archive_dir=tgz_archive_dir)
    blocked = (
        on_disk_unprocessed_paths_for_tar(unprocessed, tar_norm)
        if tar_norm else []
    )
    capped = _cap_pending_stats_with_handoff_priority(
        prepend_checkpoint_blocked_paths_to_pending(
            paths,
            blocked,
            exclude=processed_files | inflight_archive_paths,
        ),
    )
    log_print(
        "sync_timedb: pending reconcile cap done elapsed_s=%.3f "
        "oldest_tar=%s blocked_n=%d capped_pending=%d"
        % (
            time.time() - cap_t0,
            tar_norm or "",
            len(blocked),
            len(capped),
        ),
        flush=True,
    )
    return capped

  def _reconcile_pending_with_oldest_checkpoint_blocked():
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
    tail = pending_stats_files[len(stats_files_chunk):]
    successful_set = set(successful_paths)
    tail = [path for path in tail if path not in successful_set]
    if failed_chunk_paths:
      pending_stats_files = _apply_handoff_priority_to_pending(
          prepend_checkpoint_blocked_paths_to_pending(
              tail,
              failed_chunk_paths,
              exclude=processed_files | inflight_archive_paths,
          ),
      )
    else:
      pending_stats_files = _apply_handoff_priority_to_pending(tail)

  def _ingest_paths_on_supervisor_thread(paths):
    """Bounded short ingest for startup tail (runs on supervisor thread)."""
    successful_paths = []
    files_to_be_archived = []
    for path in paths:
      if use_split_db_writer_pipeline:
        stats_fname, payload, need_archival, ingest_ok, _parse_elapsed = (
            _parse_stats_file_payload(path)
        )
        if not ingest_ok:
          continue
        if payload is None:
          _transition_file_state(file_states, stats_fname, SyncFileState.WRITTEN)
          successful_paths.append(stats_fname)
          if should_archive and need_archival:
            files_to_be_archived.append(stats_fname)
          continue
        stats, proc_stats = payload
        stats_fname, need_archival, ingest_ok = _write_stats_payload_to_db(
            manager_lock,
            stats_fname,
            stats,
            proc_stats,
            need_archival=need_archival,
        )
        if not ingest_ok:
          continue
        _transition_file_state(file_states, stats_fname, SyncFileState.WRITTEN)
        successful_paths.append(stats_fname)
        if should_archive and need_archival:
          files_to_be_archived.append(stats_fname)
      elif db_writer_combined_task:
        stats_fname, need_archival, ingest_ok, _elapsed = (
            _ingest_parse_and_write_file(manager_lock, path)
        )
        if not ingest_ok:
          continue
        _transition_file_state(file_states, stats_fname, SyncFileState.WRITTEN)
        successful_paths.append(stats_fname)
        if should_archive and need_archival:
          files_to_be_archived.append(stats_fname)
      else:
        stats_fname, need_archival, ingest_ok, _elapsed = add_stats_file_to_db(
            manager_lock, path)
        if not ingest_ok:
          continue
        _transition_file_state(file_states, stats_fname, SyncFileState.WRITTEN)
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
  ):
    """Ingest an explicit path list via pool imap or supervisor-thread fallback."""
    nonlocal pool_worker_exit, active_chunk_ingest_tracker

    successful_paths = []
    files_to_be_archived = []
    chunk_ingest_finished = 0
    active_chunk_ingest_tracker = None
    chunk_paths_norm = {
        os.path.normpath(path)
        for path in (paths or ())
        if path
    }

    stall_diagnostics.chunk_batch_size = len(paths)
    stall_diagnostics.chunk_prewarm_summary = (
        _prewarm_archive_members_redis_for_chunk(paths)
    )

    k = 0
    active_workers = 0
    imap_context = "sync_timedb %s" % context_label

    if _sync_timedb_ingest_inline_requested() or ingest_pool is None:
      try:
        inline_successful, inline_archived = _ingest_paths_on_supervisor_thread(
            paths)
        return inline_successful, inline_archived, active_workers, k
      finally:
        active_chunk_ingest_tracker = None

    try:
      if use_split_db_writer_pipeline:
        parse_tasks = deque()
        writer_stage_batch_size = _db_writer_stage_batch_size(
            len(paths),
            ingest_queue_high,
        )
        parse_envelopes = [ParseTask(path=path) for path in paths]
        parse_tracker = None
        parse_paths = [task.path for task in parse_envelopes]
        parse_tracker = _IngestPoolInFlightTracker(parse_paths)
        active_chunk_ingest_tracker = parse_tracker
        parse_results_iter = _imap_ingest_paths_batched(
            ingest_pool,
            _parse_stats_file_payload,
            parse_paths,
            thread_count=thread_count,
            context=imap_context,
            tracker=parse_tracker,
            chunk_counter=batch_chunk_counter,
            pending_count=pending_total,
            ingest_pool=ingest_pool,
            db_writer_pool=db_writer_pool,
            archive_pool=archive_pool,
            stall_diagnostics=stall_diagnostics,
            pending_tail=pending_tail,
        )
        for parsed in parse_results_iter:
          stats_fname, payload, need_archival, ingest_ok, parse_elapsed_s = parsed
          if parse_tracker is not None:
            parse_tracker.complete(stats_fname)
          k += 1
          active_workers = max(active_workers, min(thread_count, k))
          if not ingest_ok:
            continue
          if payload is None:
            _transition_file_state(file_states, stats_fname, SyncFileState.WRITTEN)
            successful_paths.append(stats_fname)
            if should_archive and need_archival:
              files_to_be_archived.append(stats_fname)
            remaining = _ingest_remaining_count(pending_total, chunk_ingest_finished)
            chunk_ingest_finished += 1
            _log_sync_timedb_ingest_completed(
                stats_fname,
                parse_elapsed_s,
                remaining,
                stage="parse",
                supplement=_ingest_path_is_supplement(stats_fname, chunk_paths_norm),
            )
            continue
          parse_tasks.append(
              DBWriteTask(
                  path=stats_fname,
                  payload=payload,
                  need_archival=need_archival,
                  parse_elapsed_s=parse_elapsed_s,
              ))
          _transition_file_state(file_states, stats_fname, SyncFileState.PARSED)
          if len(parse_tasks) >= writer_stage_batch_size:
            chunk_ingest_finished = _drain_db_write_tasks(
                parse_tasks=parse_tasks,
                manager_lock=manager_lock,
                db_writer_pool=db_writer_pool,
                file_states=file_states,
                successful_paths=successful_paths,
                files_to_be_archived=files_to_be_archived,
                chunk_ingest_finished=chunk_ingest_finished,
                pending_total=pending_total,
                chunk_counter=batch_chunk_counter,
                ingest_pool=ingest_pool,
                archive_pool=archive_pool,
                stall_diagnostics=stall_diagnostics,
                chunk_paths_norm=chunk_paths_norm,
            )
        if parse_tasks:
          chunk_ingest_finished = _drain_db_write_tasks(
              parse_tasks=parse_tasks,
              manager_lock=manager_lock,
              db_writer_pool=db_writer_pool,
              file_states=file_states,
              successful_paths=successful_paths,
              files_to_be_archived=files_to_be_archived,
              chunk_ingest_finished=chunk_ingest_finished,
              pending_total=pending_total,
              chunk_counter=batch_chunk_counter,
              ingest_pool=ingest_pool,
              archive_pool=archive_pool,
              stall_diagnostics=stall_diagnostics,
              chunk_paths_norm=chunk_paths_norm,
          )
      elif db_writer_combined_task:
        add_combined = partial(_ingest_parse_and_write_file, manager_lock)
        combined_tracker = _IngestPoolInFlightTracker(paths)
        active_chunk_ingest_tracker = combined_tracker
        results_iter = _imap_ingest_paths_batched(
            ingest_pool,
            add_combined,
            paths,
            thread_count=thread_count,
            context=imap_context,
            tracker=combined_tracker,
            chunk_counter=batch_chunk_counter,
            pending_count=pending_total,
            ingest_pool=ingest_pool,
            db_writer_pool=db_writer_pool,
            archive_pool=archive_pool,
            stall_diagnostics=stall_diagnostics,
            pending_tail=pending_tail,
        )
        for result in results_iter:
          ingest_ok = True
          elapsed_s = 0.0
          if len(result) >= 4:
            stats_fname, need_archival, ingest_ok, elapsed_s = result[:4]
          elif len(result) >= 3:
            stats_fname, need_archival, ingest_ok = result[:3]
          else:
            stats_fname, need_archival = result
          if combined_tracker is not None:
            combined_tracker.complete(stats_fname)
          k += 1
          active_workers = max(active_workers, min(thread_count, k))
          if ingest_ok:
            _transition_file_state(file_states, stats_fname, SyncFileState.WRITTEN)
            successful_paths.append(stats_fname)
            if should_archive and need_archival:
              files_to_be_archived.append(stats_fname)
            remaining = _ingest_remaining_count(pending_total, chunk_ingest_finished)
            chunk_ingest_finished += 1
            _log_sync_timedb_ingest_completed(
                stats_fname,
                elapsed_s,
                remaining,
                stage="ingest",
                supplement=_ingest_path_is_supplement(stats_fname, chunk_paths_norm),
            )
      else:
        add_stats_file = partial(add_stats_file_to_db, manager_lock)
        ingest_tracker = _IngestPoolInFlightTracker(paths)
        active_chunk_ingest_tracker = ingest_tracker
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
            db_writer_pool=db_writer_pool,
            archive_pool=archive_pool,
            stall_diagnostics=stall_diagnostics,
            pending_tail=pending_tail,
        )
        for result in results_iter:
          ingest_ok = True
          elapsed_s = 0.0
          if len(result) >= 4:
            stats_fname, need_archival, ingest_ok, elapsed_s = result[:4]
          elif len(result) >= 3:
            stats_fname, need_archival, ingest_ok = result[:3]
          else:
            stats_fname, need_archival = result
          if ingest_tracker is not None:
            ingest_tracker.complete(stats_fname)
          k += 1
          active_workers = max(active_workers, min(thread_count, k))
          if ingest_ok:
            _transition_file_state(file_states, stats_fname, SyncFileState.WRITTEN)
            successful_paths.append(stats_fname)
            if should_archive and need_archival:
              files_to_be_archived.append(stats_fname)
            remaining = _ingest_remaining_count(pending_total, chunk_ingest_finished)
            chunk_ingest_finished += 1
            _log_sync_timedb_ingest_completed(
                stats_fname,
                elapsed_s,
                remaining,
                stage="ingest",
                supplement=_ingest_path_is_supplement(stats_fname, chunk_paths_norm),
            )
    except MultiprocessingWorkerExitError as exc:
      pool_worker_exit = True
      _handle_pool_worker_exit_fatal(
          exc,
          ingest_pool=ingest_pool,
          db_writer_pool=db_writer_pool,
          archive_pool=archive_pool,
      )
    except DatabaseUnavailableExit:
      raise
    except ArchiveMembersRedisUnavailableError as exc:
      _exit_on_archive_members_redis_unavailable(exc)
    except Exception as exc:
      if use_split_db_writer_pipeline:
        error_context = "sync_timedb ingest parse pool"
      elif db_writer_combined_task:
        error_context = "sync_timedb ingest pool"
      else:
        error_context = "sync_timedb ingest pool"
      reraise_database_unavailable_chain(exc, context=error_context)
      raise
    finally:
      active_chunk_ingest_tracker = None

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

      archive_dispatch.dispatch_disjoint_items(
          archive_items_all,
          archive_queue_max=archive_queue_max,
          build_deferred_paths_fn=_build_deferred_paths_for_items,
          track_pending_append_fn=_track_pending_append_groups,
          transition_queued_fn=lambda p: _transition_file_state(
              file_states, p, SyncFileState.ARCHIVE_QUEUED),
          enqueue_overflow_fn=_enqueue_overflow_item,
      )
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

  def _run_startup_tail_ingest_batch(paths, context_label):
    nonlocal chunk_in_progress
    chunk_in_progress = True
    try:
      return _run_ingest_archive_paths_batch(paths, context_label)
    finally:
      chunk_in_progress = False

  log_print(
      "sync_timedb: day_close schedule startup + every %d ingest chunks + "
      "on ingest queue drain + each ingest batch (calendar-day drain); "
      "idle rescan sleep %s s"
      % (
          int(rescan_every_chunks),
          int(EMPTY_QUEUE_RESCAN_SLEEP_SECONDS),
      ),
      flush=True,
  )
  log_print(
      "sync_timedb: archive_maintenance_interval_seconds is deprecated and ignored",
      flush=True,
  )
  log_print(
      "sync_timedb: sync_unparsable_raw_quarantine_max_per_tick is deprecated; "
      "unparseable closed raw is quarantined at ingest parse failure",
      flush=True,
  )
  log_print(
      "Queue watermarks ingest=(low=%d high=%d) archive=(low=%d high=%d)"
      % (ingest_queue_low, ingest_queue_high, archive_queue_low, archive_queue_high),
      flush=True,
  )

  host_scan_hints = {}
  idle_since_empty_queue = None
  worker_idle_loops = 0
  ingest_pool = None
  db_writer_pool = None
  pool_worker_exit = False
  db_writer_enabled = cfg.get_sync_enable_db_writer_pipeline()
  db_writer_combined_task = (
      db_writer_enabled and cfg.get_sync_db_writer_combined_task()
  )
  use_split_db_writer_pipeline = (
      db_writer_enabled and not db_writer_combined_task
  )
  stall_diagnostics = IngestStallDiagnostics()
  if use_split_db_writer_pipeline:
    stall_diagnostics.ingest_pipeline = "split_parse_write"
  else:
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
  chunk_counter = 0
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
    if use_split_db_writer_pipeline:
      ingest_pool_kind = "ingest-parse-pool"
    else:
      ingest_pool_kind = "ingest-pool"
    ingest_pool = multiprocessing.get_context('spawn').Pool(
        processes=thread_count,
        initializer=apply_ingest_pool_worker_init,
        initargs=(
            SYNC_TIMEDB_PROCESS_TITLE,
            ingest_pool_kind,
            worker_diagnostics_registry,
        ),
        **_spawn_pool_recycle_kwargs(),
    )
    if use_split_db_writer_pipeline:
      db_writer_processes = cfg.get_sync_db_writer_pool_processes(
          ingest_processes=thread_count)
      db_writer_pool = multiprocessing.get_context('spawn').Pool(
          processes=db_writer_processes,
          initializer=apply_ingest_pool_worker_init,
          initargs=(
              SYNC_TIMEDB_PROCESS_TITLE,
              "db-writer-pool",
              worker_diagnostics_registry,
          ),
          **_spawn_pool_recycle_kwargs(),
      )
    elif db_writer_combined_task:
      log_print(
          "sync_db_writer_combined_task is enabled; parse and DB write run in "
          "one ingest worker per file (no split staging in supervisor).",
          flush=True,
      )

  archive_dispatch = ArchiveDispatchCoordinator(
      archive_pool=archive_pool,
      max_inflight=cfg.get_sync_archive_max_inflight_jobs(),
      archive_stats_files_fn=archive_stats_files,
      log_fn=log_print,
      get_ingest_backlog_high=lambda: len(pending_stats_files) > ingest_queue_low,
      ingest_queue_low=ingest_queue_low,
      pending_stats_count_fn=lambda: len(pending_stats_files),
  )
  day_raw_removal = DayRawRemovalCoordinator(
      archive_data_dir=directory,
      host_name_ext=host_name_ext,
      tgz_archive_dir=tgz_archive_dir,
      log_fn=log_print,
      get_quarantine_skip_paths=_get_quarantine_skip_paths,
      ingest_ready_fn=stats_file_head_ingested_in_db,
      process_title=SYNC_TIMEDB_PROCESS_TITLE,
  )
  async_day_close = AsyncDayCloseCoordinator(
      archive_data_dir=directory,
      host_name_ext=host_name_ext,
      tgz_archive_dir=tgz_archive_dir,
      local_tz=local_timezone,
      log_fn=log_print,
      get_disqualified_daily_tars=_janitor_disqualified_daily_tars,
      day_raw_removal_coordinator=day_raw_removal,
      process_title=SYNC_TIMEDB_PROCESS_TITLE,
  )
  stall_diagnostics.async_day_close = async_day_close
  startup_archive_scan = StartupArchiveScanCoordinator(
      archive_data_dir=directory,
      host_name_ext=host_name_ext,
      tgz_archive_dir=tgz_archive_dir,
      log_fn=log_print,
  )

  def _wait_startup_snapshot_for_preflight():
    """Preflights wait on janitor publish only; never trigger fallback collect."""
    snap = startup_archive_scan.get_snapshot()
    if snap is not None:
      return snap
    return startup_archive_scan.wait_for_snapshot(allow_build=False)

  def _get_startup_snapshot_for_rescan():
    """Supervisor rescan may single-flight fallback-build after wait timeout."""
    snap = startup_archive_scan.get_snapshot()
    if snap is not None:
      return snap
    return startup_archive_scan.wait_for_snapshot(allow_build=True)

  def _startup_closed_paths_for_rescan():
    snap = startup_archive_scan.get_snapshot()
    if snap is None or not snap.closed_paths:
      return None
    return list(snap.closed_paths)

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

  def _rescan_pending_with_progress():
    rescan_t0 = time.time()
    log_print("sync_timedb: pending rescan begin", flush=True)
    if run_startup_maintenance:
      _get_startup_snapshot_for_rescan()
    paths = rescan_pending_stats_files(
        directory,
        startdate,
        enddate,
        host_name_ext,
        processed_files | inflight_archive_paths,
        host_scan_hints=host_scan_hints,
        startup_closed_paths=_startup_closed_paths_for_rescan(),
    )
    log_print(
        "sync_timedb: pending rescan done pending=%d elapsed_s=%.3f"
        % (len(paths), time.time() - rescan_t0),
        flush=True,
    )
    return paths

  def _get_ingest_pool_in_flight_count():
    if active_chunk_ingest_tracker is None:
      return 0
    return active_chunk_ingest_tracker.in_flight_count()

  def _get_chunk_in_progress():
    return bool(chunk_in_progress)

  archive_janitor = ArchiveJanitor(
      archive_data_dir=directory,
      host_name_ext=host_name_ext,
      tgz_archive_dir=tgz_archive_dir,
      local_tz=local_timezone,
      log_fn=log_print,
      get_disqualified_daily_tars=_janitor_disqualified_daily_tars,
      get_ingest_backlog_high=lambda: len(pending_stats_files) > ingest_queue_low,
      get_pending_stats_count=lambda: len(pending_stats_files),
      get_idle_seconds=lambda: (
          max(0.0, time.time() - float(idle_since_empty_queue))
          if idle_since_empty_queue is not None else 0.0
      ),
      get_quarantine_skip_paths=_get_quarantine_skip_paths,
      ingest_ready_fn=stats_file_head_ingested_in_db,
      archive_stats_files_fn=archive_stats_files,
      day_raw_removal_coordinator=day_raw_removal,
      async_day_close_coordinator=async_day_close,
      get_day_close_candidate_inputs=_build_day_close_candidate_inputs,
      get_tree_rss_bytes=lambda: read_sync_timedb_tree_rss_bytes(
          ingest_pool, db_writer_pool, archive_pool),
      startup_snapshot_coordinator=startup_archive_scan,
      get_ingest_pool_in_flight_count=_get_ingest_pool_in_flight_count,
      get_chunk_in_progress=_get_chunk_in_progress,
      process_title=SYNC_TIMEDB_PROCESS_TITLE,
  )
  reconcile_refs["get_startup_snapshot"] = startup_archive_scan.get_snapshot
  reconcile_refs["get_accrual_snapshot"] = (
      archive_janitor.get_accrual_snapshot_for_reconcile
  )

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

  def _async_day_close_submit_eligible(tar_norm):
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
    )

  async_day_close.submit_eligible_fn = _async_day_close_submit_eligible
  async_day_close.on_day_phase = _on_async_day_phase

  def _archive_members_invalidation_hook(_canonical, day_token):
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

  def _on_day_close_pipeline_complete(_tar_path):
    nonlocal day_close_rescan_pending
    day_close_rescan_pending = True
    archive_janitor.signal_work_available()

  def _requeue_day_close_handoff_paths(tar_norm, paths, reason):
    nonlocal pending_stats_files
    tar_norm = os.path.normpath(str(tar_norm or ""))
    requeued = []
    for path in paths or ():
      if not path or not os.path.isfile(path):
        continue
      _remove_processed_path(
          path,
          processed_files,
          processed_files_order,
          checkpoint_entries,
          checkpoint_path,
          file_states=file_states,
          host_scan_hints=host_scan_hints,
          persist=False,
      )
      handoff_priority_paths.add(path)
      requeued.append(path)
    if not requeued:
      return
    _flush_checkpoint_if_needed(force=True)
    if async_day_close is not None:
      async_day_close.defer_for_ingest_handoff(tar_norm)
    pending_stats_files = _cap_pending_after_rescan(
        _apply_handoff_priority_to_pending(pending_stats_files),
    )
    log_print(
        "sync_timedb: day_close handoff requeue day=%s paths=%d reason=%s "
        "checkpoint_cleared=yes queue_head=yes"
        % (
            calendar_date_from_daily_tar_path(tar_norm).isoformat()
            if calendar_date_from_daily_tar_path(tar_norm) is not None
            else tar_norm,
            len(requeued),
            reason or "",
        ),
        flush=True,
    )
    archive_janitor.signal_work_available()

  def _recover_startup_day_close_handoffs():
    nonlocal pending_stats_files
    if day_raw_removal is None or not day_raw_removal.enabled:
      return
    for tar_norm, paths in day_raw_removal.discover_manifest_handoffs():
      _requeue_day_close_handoff_paths(
          tar_norm,
          paths,
          reason="startup_manifest_handoff",
      )

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
        rescan_pending_stats_files(
            directory,
            startdate,
            enddate,
            host_name_ext,
            processed_files | inflight_archive_paths,
            host_scan_hints=host_scan_hints,
        ),
    )
    day_close_rescan_pending = False
    log_print(
        "Day raw removal pipeline complete; rescanned pending=%d"
        % len(pending_stats_files),
        flush=True,
    )

  def _signal_maintenance_pass_if_queue_drained(*, had_pending_before_chunk: bool):
    if not had_pending_before_chunk or pending_stats_files:
      return
    log_print(
        "sync_timedb: maintenance pass reason=ingest_queue_empty",
        flush=True,
    )
    archive_janitor.signal_scheduled_maintenance_pass(reason="ingest_queue_empty")

  startup_preflight = None
  startup_day_close = None
  startup_tail_ingest = None

  try:
    _ensure_daily_archive_dir_exists()
    if run_startup_maintenance:
      log_print(cfg.format_sync_timedb_non_default_settings_line(), flush=True)
      log_print("sync_timedb: maintenance pass reason=startup", flush=True)
      startup_archive_scan.note_startup_maintenance_pending()
      archive_janitor.signal_scheduled_maintenance_pass(reason="startup")
      archive_janitor.enqueue_startup_debt()
      startup_preflight = StartupRawRemovalPreflight(
          archive_data_dir=directory,
          host_name_ext=host_name_ext,
          tgz_archive_dir=tgz_archive_dir,
          log_fn=log_print,
          get_disqualified_daily_tars=_janitor_disqualified_daily_tars,
          get_quarantine_skip_paths=_get_quarantine_skip_paths,
          ingest_ready_fn=stats_file_head_ingested_in_db,
          get_startup_snapshot=_wait_startup_snapshot_for_preflight,
          process_title=SYNC_TIMEDB_PROCESS_TITLE,
      )
      startup_preflight.start_async_verify()
      startup_day_close_holder = {"preflight": None}
      startup_tail_ingest = StartupTailIngestCoordinator(
          log_fn=log_print,
          run_ingest_batch=_run_startup_tail_ingest_batch,
          submit_day_close=lambda tar_norm, reason: (
              async_day_close.submit_day_close(
                  tar_norm,
                  reason=reason,
                  disqualified_daily_tars=_janitor_disqualified_daily_tars(),
              )
              if async_day_close is not None
              else False
          ),
          signal_janitor=lambda: archive_janitor.signal_work_available(),
          get_startup_snapshot=_get_startup_snapshot_for_rescan,
          live_unprocessed_by_tar=_live_unprocessed_by_tar_for_reconcile,
          discover_done_fn=lambda: (
              startup_day_close_holder["preflight"].discover_done()
              if startup_day_close_holder["preflight"] is not None
              and startup_day_close_holder["preflight"].enabled
              else True
          ),
          process_title=SYNC_TIMEDB_PROCESS_TITLE,
      )
      startup_day_close = StartupDayClosePreflight(
          archive_data_dir=directory,
          host_name_ext=host_name_ext,
          tgz_archive_dir=tgz_archive_dir,
          local_tz=local_timezone,
          log_fn=log_print,
          async_day_close=async_day_close,
          get_disqualification_inputs=_capture_disqualification_inputs,
          get_unmapped_closed_raw_tars=_get_unmapped_closed_raw_tars,
          day_phases=lambda: dict(archive_janitor._day_phases),
          get_startup_snapshot=_wait_startup_snapshot_for_preflight,
          get_accrual_remaining_raw_by_gz=_get_accrual_remaining_raw_by_gz,
          tail_ingest_coordinator=(
              startup_tail_ingest if startup_tail_ingest.enabled else None
          ),
          process_title=SYNC_TIMEDB_PROCESS_TITLE,
      )
      startup_day_close_holder["preflight"] = startup_day_close
      startup_day_close.start_async_discover_and_close()
      startup_tail_ingest.start_async_tail_ingest()
      _recover_startup_day_close_handoffs()
      if async_day_close is not None:
        async_day_close.reconcile_supervisor_raw_delete_pending(
            reason="startup",
        )

      startup_ingest_gate_cleared = (
          not cfg.get_sync_startup_drain_day_close_before_ingest()
      )
      startup_day_close_drain_complete = startup_ingest_gate_cleared
    else:
      log_print(
          "sync_timedb: startup maintenance skipped "
          "(pass 'all' for full-archive startup pass)",
          flush=True,
      )
      startup_archive_scan.mark_startup_heavy_maintenance_finished()
      startup_ingest_gate_cleared = True
      startup_day_close_drain_complete = True
      reconcile_refs["ingest_gate_cleared"] = True
    startup_drain_last_log = 0.0
    startup_drain_blocked_last_log = 0.0

    def _startup_ingest_gate_pending():
      if startup_tail_ingest is not None and startup_tail_ingest.enabled:
        if not startup_tail_ingest.tail_ingest_done():
          return True
      if startup_day_close is not None and startup_day_close.enabled:
        if not startup_day_close.discover_done():
          return True
      if startup_preflight is not None and startup_preflight.enabled:
        if not startup_preflight.delete_phase_done():
          return True
      if day_raw_removal is not None and day_raw_removal.enabled:
        if day_raw_removal.any_blocks_startup_drain():
          return True
      return False

    def _maybe_log_startup_drain_wait():
      nonlocal startup_drain_last_log
      now = time.time()
      if now - startup_drain_last_log < 30.0:
        return
      startup_drain_last_log = now
      tail_pending = (
          startup_tail_ingest is not None
          and startup_tail_ingest.enabled
          and not startup_tail_ingest.tail_ingest_done()
      )
      discover_pending = (
          startup_day_close is not None
          and startup_day_close.enabled
          and not startup_day_close.discover_done()
      )
      pending_eligible = (
          startup_day_close.pending_eligible_count()
          if startup_day_close is not None
          and startup_day_close.enabled
          and hasattr(startup_day_close, "pending_eligible_count")
          else (
              startup_day_close.pending_deferral_count()
              if startup_day_close is not None and startup_day_close.enabled
              else 0
          )
      )
      pending_retry = (
          startup_day_close.pending_retry_count()
          if startup_day_close is not None
          and startup_day_close.enabled
          and hasattr(startup_day_close, "pending_retry_count")
          else 0
      )
      tail_queue = (
          startup_tail_ingest.pending_count()
          if startup_tail_ingest is not None and startup_tail_ingest.enabled
          else 0
      )
      async_active = (
          len(async_day_close.active_or_submitted_tar_paths())
          if async_day_close is not None
          else 0
      )
      raw_pending = (
          startup_preflight is not None
          and startup_preflight.enabled
          and not startup_preflight.delete_phase_done()
      )
      day_delete_pending = (
          day_raw_removal is not None
          and day_raw_removal.enabled
          and day_raw_removal.any_blocks_startup_drain()
      )
      day_raw_waiting_on_ingest = (
          day_raw_removal.count_days_waiting_on_ingest()
          if day_raw_removal is not None and day_raw_removal.enabled
          else 0
      )
      log_print(
          "sync_timedb: startup ingest maintenance waiting tail_pending=%s "
          "discover=%s pending_eligible=%d pending_retry=%d tail_queue=%d "
          "async_active=%d startup_raw=%s day_raw_delete=%s "
          "day_raw_waiting_on_ingest=%d"
          % (
              tail_pending,
              discover_pending,
              pending_eligible,
              pending_retry,
              tail_queue,
              async_active,
              raw_pending,
              day_delete_pending,
              day_raw_waiting_on_ingest,
          ),
          flush=True,
      )

    def _startup_drain_block_snapshot(*, delete_driver: bool = False):
      async_raw = (
          async_day_close.tar_paths_raw_delete_pending()
          if async_day_close is not None
          else []
      )
      return {
          "delete_driver": delete_driver,
          "async_raw_delete_pending": len(async_raw),
          "chunk_in_progress": chunk_in_progress,
          "tail_pending": (
              startup_tail_ingest is not None
              and startup_tail_ingest.enabled
              and not startup_tail_ingest.tail_ingest_done()
          ),
          "discover_pending": (
              startup_day_close is not None
              and startup_day_close.enabled
              and not startup_day_close.discover_done()
          ),
          "raw_pending": (
              startup_preflight is not None
              and startup_preflight.enabled
              and not startup_preflight.delete_phase_done()
          ),
          "day_raw_delete": (
              day_raw_removal is not None
              and day_raw_removal.enabled
              and day_raw_removal.any_blocks_startup_drain()
          ),
          "heavy_not_idle": (
              not startup_archive_scan.is_startup_heavy_maintenance_idle()
          ),
          "gate_pending": _startup_ingest_gate_pending(),
      }

    def _maybe_log_startup_drain_blocked(*, trigger: str, delete_driver: bool = False):
      nonlocal startup_drain_blocked_last_log
      now = time.time()
      if now - startup_drain_blocked_last_log < 30.0:
        return
      startup_drain_blocked_last_log = now
      snap = _startup_drain_block_snapshot(delete_driver=delete_driver)
      log_print(
          "sync_timedb: startup drain blocked reason=trigger:%s "
          "delete_driver=%s async_raw_delete_pending=%d chunk_in_progress=%s "
          "tail_pending=%s discover_pending=%s raw_pending=%s "
          "day_raw_delete=%s heavy_not_idle=%s gate_pending=%s"
          % (
              trigger,
              snap["delete_driver"],
              snap["async_raw_delete_pending"],
              snap["chunk_in_progress"],
              snap["tail_pending"],
              snap["discover_pending"],
              snap["raw_pending"],
              snap["day_raw_delete"],
              snap["heavy_not_idle"],
              snap["gate_pending"],
          ),
          flush=True,
      )

    def _drain_startup_day_close_and_deletion_if_needed():
      nonlocal startup_ingest_gate_cleared, startup_day_close_drain_complete
      if startup_ingest_gate_cleared:
        return False
      if not startup_day_close_drain_complete:
        if _maybe_handle_raw_removal_delete_phase():
          _maybe_log_startup_drain_blocked(
              trigger="delete_driver",
              delete_driver=True,
          )
          return True
        if not _startup_ingest_gate_pending():
          if (
              startup_preflight is not None
              and startup_preflight.enabled
              and startup_preflight.delete_phase_done()
          ):
            _post_startup_raw_removal_rescan()
          log_print(
              "sync_timedb: startup ingest maintenance complete",
              flush=True,
          )
          startup_day_close_drain_complete = True
        else:
          _maybe_log_startup_drain_wait()
          _maybe_log_startup_drain_blocked(trigger="gate_pending")
          sleep_until_shutdown(0.25)
          return True
      if not startup_archive_scan.is_startup_heavy_maintenance_idle():
        _maybe_log_startup_drain_blocked(trigger="heavy_not_idle")
        if not startup_archive_scan.wait_for_startup_maintenance_idle():
          if startup_archive_scan.get_snapshot() is None:
            log_print(
                "WARN: startup maintenance idle wait timed out with no "
                "coordinator snapshot; delaying ingest",
                flush=True,
            )
            _maybe_log_startup_drain_blocked(trigger="heavy_not_idle_no_snapshot")
            sleep_until_shutdown(1.0)
            return True
          log_print(
              "WARN: startup maintenance idle wait timed out; proceeding "
              "with coordinator snapshot only",
              flush=True,
          )
      log_print(
          "sync_timedb: startup maintenance idle; ingest may begin",
          flush=True,
      )
      startup_ingest_gate_cleared = True
      reconcile_refs["ingest_gate_cleared"] = True
      return False

    def _post_startup_raw_removal_rescan():
      nonlocal pending_stats_files
      if startup_preflight is None:
        return
      removed = startup_preflight.consumed_paths()
      for path in removed:
        file_states.pop(path, None)
        if isinstance(host_scan_hints, dict):
          host_scan_hints.pop(path, None)
      pending_stats_files = _cap_pending_after_rescan(
          _rescan_pending_with_progress(),
      )
      log_print(
          "Startup raw removal preflight done; rescanned pending=%d"
          % len(pending_stats_files),
          flush=True,
      )

    def _finalize_day_close_raw_removal_delete(tar_norm):
      if async_day_close is not None:
        async_day_close.finalize_complete_if_filesystem(os.path.normpath(tar_norm))
      archive_janitor.signal_work_available()

    def _apply_day_close_raw_removal_deletes():
      """One ingest-gated pass over pending days (oldest-first; skip stuck per batch)."""
      nonlocal delete_phase_active
      if day_raw_removal is None or not day_raw_removal.enabled:
        return False
      if async_day_close is not None:
        async_day_close.reconcile_supervisor_raw_delete_pending(
            reason="delete_pass",
        )
      needs_delete = day_raw_removal.any_needs_delete_phase()
      needs_tar_drop = day_raw_removal.any_needs_tar_drop_finish()
      async_tar_drop = (
          async_day_close.tar_paths_raw_delete_pending()
          if async_day_close is not None
          else []
      )
      if not needs_delete and not needs_tar_drop and not async_tar_drop:
        return False
      if needs_delete:
        if chunk_in_progress:
          sleep_until_shutdown(0.1)
          _maybe_log_startup_drain_blocked(
              trigger="chunk_wait_day_raw_delete",
              delete_driver=True,
          )
          return True
        delete_phase_active = True
        for tar_norm in day_raw_removal.days_needing_delete_oldest_first():
          if day_raw_removal.phase(tar_norm) == PHASE_VERIFICATION_COMPLETE:
            day_raw_removal.begin_deleting(tar_norm)
          deleted = day_raw_removal.apply_batch_delete(tar_norm)
          if day_raw_removal.delete_phase_done(tar_norm):
            _finalize_day_close_raw_removal_delete(tar_norm)
            continue
          if (
              deleted == 0
              and day_raw_removal.needs_delete_phase(tar_norm)
              and not day_raw_removal.delete_phase_done(tar_norm)
          ):
            continue
        delete_phase_active = False
      tar_drop_targets: list[str] = []
      if needs_tar_drop:
        tar_drop_targets.extend(day_raw_removal.days_needing_tar_drop_oldest_first())
      for tar_norm in async_tar_drop:
        if tar_norm not in tar_drop_targets:
          tar_drop_targets.append(tar_norm)
      for tar_norm in tar_drop_targets:
        if day_raw_removal.try_finish_tar_drop_if_ready(tar_norm):
          _finalize_day_close_raw_removal_delete(tar_norm)
      _maybe_apply_day_close_rescan()
      return False

    def _maybe_handle_raw_removal_delete_phase():
      nonlocal delete_phase_active, pending_stats_files
      if startup_preflight is not None and startup_preflight.enabled:
        if not startup_preflight.delete_phase_done():
          if startup_preflight.needs_delete_phase():
            delete_phase_active = True
          if delete_phase_active:
            if chunk_in_progress:
              sleep_until_shutdown(0.1)
              _maybe_log_startup_drain_blocked(
                  trigger="chunk_wait_startup_raw_delete",
                  delete_driver=True,
              )
              return True
            if startup_preflight.phase() == PHASE_VERIFICATION_COMPLETE:
              startup_preflight.begin_deleting()
            startup_preflight.apply_deletes_from_manifest()
            if startup_preflight.delete_phase_done():
              delete_phase_active = False
              _post_startup_raw_removal_rescan()
            else:
              _maybe_log_startup_drain_blocked(
                  trigger="startup_raw_delete_in_progress",
                  delete_driver=True,
              )
              return True
      return _apply_day_close_raw_removal_deletes()

    while not shutdown_requested[0]:
      if _drain_startup_day_close_and_deletion_if_needed():
        continue
      if _maybe_handle_raw_removal_delete_phase():
        continue
      _maybe_apply_day_close_rescan()

      if not pending_stats_files:
        if idle_since_empty_queue is None:
          idle_since_empty_queue = time.time()
        _dispatch_due_archive_retries()
        if len(pending_archive_tasks) > archive_queue_high:
          log_print(
              "Archive backlog above high watermark pending=%d high=%d"
              % (len(pending_archive_tasks), archive_queue_high),
              flush=True,
          )
        _finalize_archive_slots_if_needed(force=True, context="pre_rescan")
        _maybe_enqueue_immediate_day_close(context="idle_finalize")
        pending_stats_files = _cap_pending_after_rescan(
            _rescan_pending_with_progress(),
        )
        if pending_stats_files:
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
        pending_stats_files = _cap_pending_after_rescan(
            _rescan_pending_with_progress(),
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
        if _maybe_handle_raw_removal_delete_phase():
          continue
        _maybe_wait_tree_rss_before_chunk(
            ingest_pool, db_writer_pool, archive_pool)
        _maybe_apply_day_close_rescan()
        idle_since_empty_queue = None
        _finalize_archive_slots_if_needed(
            force=True,
            allow_defer=True,
            context="chunk_boundary",
        )
        if shutdown_requested[0]:
          log_print("Exiting due to SIGTERM")
          break
        if DEBUG:
          log_print(
              "Begining Chunk(%s) #%s Processing" % (chunk_size, chunk_counter))

        if len(pending_stats_files) >= ingest_queue_high:
          log_print(
              "Ingest pending above high watermark pending=%d high=%d"
              % (len(pending_stats_files), ingest_queue_high),
              flush=True,
          )
        _reconcile_pending_with_oldest_checkpoint_blocked()
        target_chunk_size = min(chunk_size, ingest_queue_high)
        had_pending_before_chunk = bool(pending_stats_files)
        pending_paths_before_chunk = list(pending_stats_files)
        stats_files_chunk = pending_stats_files[:target_chunk_size]
        if not stats_files_chunk:
          continue

        chunk_in_progress = True
        successful_paths, files_to_be_archived, active_workers, k = (
            _ingest_explicit_path_batch(
                stats_files_chunk,
                context_label="ingest chunk",
                pending_total=len(pending_stats_files),
                batch_chunk_counter=chunk_counter,
                pending_tail=pending_stats_files[len(stats_files_chunk):],
            )
        )

        log_print("loading time", time.time() - ingest_t0)
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

        if files_to_be_archived:
          _ensure_daily_archive_dir_exists()
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
          if len(ar_file_mapping) >= archive_queue_high:
            log_print(
                "Archive mapping above high watermark groups=%d high=%d"
                % (len(ar_file_mapping), archive_queue_high),
                flush=True,
            )
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
          _dispatch_due_archive_retries()
        elif deferred_paths:
          log_print(
              "Deferring processed marker for %d file(s): archival mapping missing"
              % len(deferred_paths),
              flush=True,
          )

        _record_ingest_sort_epochs_for_paths(
            [p for p in stats_files_chunk if p in set(successful_paths)])
        _advance_pending_after_chunk(stats_files_chunk, successful_paths)
        if len(pending_stats_files) <= ingest_queue_low:
          log_print(
              "Ingest pending at/below low watermark pending=%d low=%d"
              % (len(pending_stats_files), ingest_queue_low),
              flush=True,
          )
        chunk_counter += 1
        _maybe_apply_tree_rss_governor(
            chunk_counter, ingest_pool, db_writer_pool, archive_pool)

        _dispatch_due_archive_retries()
        active_chunk_ingest_tracker = None

        if chunk_counter % rescan_every_chunks == 0:
          _finalize_archive_slots_if_needed(
              force=True,
              allow_defer=bool(pending_stats_files),
              context="rescan_every_chunks",
          )
          log_print(
              "sync_timedb: maintenance pass reason=every_n_chunks",
              flush=True,
          )
          archive_janitor.signal_scheduled_maintenance_pass(
              reason="every_n_chunks",
          )
          archive_janitor.signal_work_available()
          pending_stats_files = _cap_pending_after_rescan(
              rescan_pending_stats_files(
                  directory,
                  startdate,
                  enddate,
                  host_name_ext,
                  processed_files | inflight_archive_paths,
                  host_scan_hints=host_scan_hints,
              ),
          )
          log_print(
              "Rescanned after %d chunks; pending files (oldest first): %d"
              % (rescan_every_chunks, len(pending_stats_files)))

        _signal_maintenance_pass_if_queue_drained(
            had_pending_before_chunk=had_pending_before_chunk,
        )
        _finalize_archive_slots_if_needed(
            force=True,
            allow_defer=bool(pending_stats_files),
            context="end_of_batch",
        )
        _maybe_enqueue_immediate_day_close(context="chunk_end")
        unprocessed_for_progress = _build_unprocessed_by_tar_for_day_close(
            pending_stats_paths=list(pending_stats_files),
        )
        _log_checkpoint_day_close_chunk_progress(
            unprocessed_by_tar=unprocessed_for_progress,
            checkpoint_deferred_archive=len(deferred_paths),
        )
        with archive_janitor._accrual_snapshot_lock:
          accrual_for_day_complete = archive_janitor._accrual_snapshot
        pending_drained_days = _completed_ingest_calendar_days(
            chunk_paths=stats_files_chunk,
            pending_before=pending_paths_before_chunk,
            pending_after=pending_stats_files,
        )
        completed_ingest_days = _calendar_days_ingest_complete_for_heavy_pass(
            chunk_paths=stats_files_chunk,
            pending_before=pending_paths_before_chunk,
            pending_after=pending_stats_files,
            archive_data_dir=directory,
            host_name_ext=host_name_ext,
            tgz_archive_dir=tgz_archive_dir,
            checkpoint_path=checkpoint_path,
            maintenance_snapshot=accrual_for_day_complete,
        )
        if pending_drained_days and not completed_ingest_days:
          log_print(
              "sync_timedb: day ingest complete heavy pass skipped "
              "(checkpoint incomplete for touched days)",
              flush=True,
          )
        if completed_ingest_days:
          heavy_reason = "day_ingest_complete:" + ",".join(completed_ingest_days)
          log_print(
              "sync_timedb: heavy maintenance pass reason=%s"
              % heavy_reason,
              flush=True,
          )
          archive_janitor.signal_scheduled_maintenance_pass(reason=heavy_reason)
          archive_janitor.signal_work_available()

        chunk_in_progress = False

      _persist_dead_letters_if_needed(force=True)
      _flush_checkpoint_if_needed(force=True)
      janitor_stats = archive_janitor.stats()
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
    active_chunk_ingest_tracker = None
    preflight_shutdown_wait = not pool_worker_exit
    if startup_preflight is not None:
      startup_preflight.shutdown(wait=preflight_shutdown_wait)
    if startup_day_close is not None:
      startup_day_close.shutdown(wait=preflight_shutdown_wait)
    if startup_tail_ingest is not None:
      startup_tail_ingest.shutdown(wait=preflight_shutdown_wait)
    if async_day_close is not None:
      async_day_close.shutdown(wait=preflight_shutdown_wait)
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
        db_writer_pool,
        force_terminate=pool_worker_exit,
    )


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
      and argv_for_dates[1] != "all"
  ):
    try:
      single_day = datetime.strptime(argv_for_dates[1], "%Y-%m-%d")
    except ValueError:
      pass
    else:
      startdate = single_day
      enddate = datetime.combine(single_day.date(), datetime.max.time())

  if len(argv_for_dates) > 1 and argv_for_dates[1] == 'all':
    startdate = 'all'
    enddate = None

  return run_once, startdate, enddate


def run_sync_timedb_supervisor_from_parsed(run_once, startdate, enddate):
  """Run one supervisor session after ``database_startup()`` (CLI or in-process tests)."""
  _reset_sync_runtime_caches()
  if startdate == 'all':
    log_print(
        "###Date Range of stats files to ingest: entire archive directory "
        "(no date filter)####")
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
    if cfg.get_sync_enable_db_writer_pipeline():
      if cfg.get_sync_db_writer_combined_task():
        log_print(
            "sync_enable_db_writer_pipeline with sync_db_writer_combined_task: "
            "parse and DB write in one ingest worker per file.",
            flush=True,
        )
      else:
        log_print(
            "sync_enable_db_writer_pipeline is enabled; using separated parse "
            "workers and DB-writer workers.",
            flush=True,
        )
    lock_shards = max(1, int(cfg.get_sync_write_lock_shards()))
    if lock_shards == 1:
      manager_lock = manager.Lock()
    else:
      manager_lock = [manager.Lock() for _ in range(lock_shards)]
      log_print("Using %d sync_timedb write-lock shards" % lock_shards, flush=True)
    with multiprocessing.get_context('spawn').Pool(
        processes=archive_thread_count,
        initializer=apply_pool_worker_process_title,
        initargs=(SYNC_TIMEDB_PROCESS_TITLE, "archive-pool"),
        **_spawn_pool_recycle_kwargs(),
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
