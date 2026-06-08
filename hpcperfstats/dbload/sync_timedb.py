#!/usr/bin/env python3
"""Load raw stats files into TimescaleDB (host_data, proc_data). Parses stats, applies hardware counter maps, computes deltas/arc, bulk-inserts, and optionally archives processed files (append to daily ``.tar``; seal to ``.tar.zst`` and raw/``.tar`` cleanup via the background ``ArchiveJanitor``). Runs in parallel with configurable chunk size.

**Hot path (supervisor thread):** discover → ingest → checkpoint → dispatch append (up to ``sync_archive_max_inflight_jobs`` disjoint daily-tar slots). Ingest never blocks on seal, zstd, raw delete, or uncompressed ``.tar`` removal.

**Cold path (``ArchiveJanitor`` thread):** day-debt queue consumed in time-sliced micro-batches (``archive_janitor_budget_seconds`` / ``archive_janitor_days_per_tick``). Full accrual runs on ``archive_maintenance_interval_seconds`` (default 8h) when the ingest queue is empty; partial prior-day accrual and chunk-end ``enqueue_completed_prior_days_reclaim()`` run during ingest backlog. ``signal_work_available()`` schedules ticks without waiting. Seal → raw remove (``allow_auto_seal=False``) → ``.tar`` drop per day; DB head-ingest gate and disqualification union unchanged. Progress persists in ``.sync_archive_maint_hints.json`` v2 (``debt_queue``, ``day_phases``).

Append and raw delete stay DB-gated when ``sync_archive_require_db_head_ingest=yes``. Finalize uses soft defer (``allow_defer``) under ingest backlog instead of blocking the supervisor.

When the ingest queue is empty, rescans for new stats files. After a rescan still finds nothing pending, it sleeps ``EMPTY_QUEUE_RESCAN_SLEEP_SECONDS`` (default 30s) and exits the loop iteration (continuous mode repeats).

CLI: no args or ``YYYY-MM-DD`` range uses a sliding window (see ``days_to_process``). First arg ``all`` scans every host stats dir under ``archive_dir`` (subdirs whose names end with ``DEFAULT.host_name_ext`` from ini). Prefix ``once`` to exit after one idle rescan (no 300s sleep), e.g. ``once all``.

DB access is process-safe: pool workers use close_old_connections() at task start and connections.close_all() at task end so connections do not linger between files. Writes are serialized with a shared lock.

"""
import itertools
import heapq
import json
import math
import multiprocessing
from contextlib import contextmanager
import os
import shutil
import signal
import tempfile
import subprocess
import sys
import threading
import time
import warnings
from collections import deque
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from functools import partial
from hpcperfstats.django_bootstrap import ensure_django
from hpcperfstats.process_title import (
    apply_pool_worker_process_title,
    set_daemon_process_title,
    set_daemon_thread_title,
)

ensure_django()

SYNC_TIMEDB_PROCESS_TITLE = "sync_timedb.py"

from django.db import IntegrityError, close_old_connections, connections
from django.db.utils import DatabaseError, OperationalError

import hpcperfstats.conf_parser as cfg
from hpcperfstats.dbload.date_utils import log_date_range, parse_start_end_dates
from hpcperfstats.dbload.db_unavailable import (
    DatabaseUnavailableExit,
    is_database_unavailable_error,
    log_and_raise_database_unavailable,
    reraise_database_unavailable_chain,
)
from hpcperfstats.dbload.io_helpers import host_data_instance_from_stats_row
from hpcperfstats.dbload.multiprocessing_pool_health import (
    MultiprocessingWorkerExitError,
    async_result_get_watch_pool,
    close_pool_bounded,
    imap_unordered_watch_pool,
    terminate_pool_bounded,
)
from hpcperfstats.process_memory import read_process_rss_bytes
from hpcperfstats.dbload.archive_compress import (
    compressed_sibling_paths,
    daily_compressed_path_for_date,
    daily_tar_path_from_compressed,
    detect_compressed_format,
)
from hpcperfstats.dbload.zstd_cli import decompress_compressed_to_tar
from hpcperfstats.print_utils import log_print
from hpcperfstats.shutdown_utils import (
    shutdown_requested,
    send_sigchld_to_parent,
    sleep_until_shutdown,
)
from hpcperfstats.dbload.sync_timedb_archive_helpers import (
    build_archive_mapping,
    build_seal_disqualified_daily_tars,
    collect_days_with_unmapped_closed_raw,
    daily_tar_path_from_compressed,
    get_unmapped_closed_raw_daily_tars_cached,
    daily_tar_paths_for_archive_job_tasks,
    daily_tar_paths_for_stats_paths,
    daily_tar_paths_from_pending_archive_tasks,
    filter_files_to_add_to_archive,
    get_existing_archive_members,
    iter_daily_tar_paths,
    replace_corrupt_tar_from_compressed_backup,
    cap_pending_stats_file_list,
    rescan_pending_stats_files,
    should_seal_daily_tar,
    stats_file_is_active_segment,
    verify_tar_archive_readable,
)
from hpcperfstats.file_locking import file_write_lock
from hpcperfstats.dbload.sync_timedb_archive_dispatch import ArchiveDispatchCoordinator
from hpcperfstats.dbload.sync_timedb_archive_janitor import ArchiveJanitor

# Supervisor tests monkeypatch these names; cold-path maintenance lives in ArchiveJanitor.
def seal_dirty_daily_archives(*args, **kwargs):
  raise RuntimeError("seal_dirty_daily_archives is janitor-only; supervisor must not call this")


def remove_verified_archived_raw_files(*args, **kwargs):
  raise RuntimeError(
      "remove_verified_archived_raw_files is janitor-only; supervisor must not call this")


def remove_verified_uncompressed_daily_tars(*args, **kwargs):
  raise RuntimeError(
      "remove_verified_uncompressed_daily_tars is janitor-only; supervisor must not call this")
from hpcperfstats.dbload.sync_timedb_ingest_readiness import (
    filter_paths_head_ingested,
    head_timestamp_present_in_db,
    reset_sync_ingest_readiness_caches,
    stats_file_head_ingested_in_db,
)
from hpcperfstats.dbload.sync_timedb_parsing import (
    EVENTMAPS_BY_TYPE,
    build_stats_dataframes,
    compute_deltas_and_arc,
    exclude_types,
    find_processing_start_index,
    load_stats_file_lines,
    parse_first_timestamp_line,
    parse_stats_file_path,
    parse_stats_lines,
)
from hpcperfstats.site.machine.models import host_data, proc_data


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

# Set to 1/yes/true so ingest runs in the parent process (no spawn pool). Required
# for pytest-django: pool workers would reconnect with default [DEFAULT] dbname instead
# of the test database created for the session.
_SYNC_TIMEDB_INGEST_INLINE_ENV = "HPCPERFSTATS_SYNC_TIMEDB_INGEST_INLINE"


def _sync_timedb_ingest_inline_requested():
  return os.environ.get(_SYNC_TIMEDB_INGEST_INLINE_ENV, "").strip().lower() in (
      "1", "yes", "true")

# Rows per bulk_create batch to limit peak memory per worker
bulk_create_batch_size = 10000
_HOST_ITIMES_CACHE = {}
_HOST_ITIMES_CACHE_REFRESH_SECONDS = 20
_HOST_ITIMES_CACHE_MAX_ENTRIES = 2000
_HOST_ITIMES_CACHE_MAX_TIMESTAMPS_PER_ENTRY = 100000

tgz_archive_dir = cfg.get_daily_archive_dir_path()


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


def _reraise_or_handle_pool_worker_exit(
    exc,
    *,
    ingest_pool,
    db_writer_pool,
    archive_pool=None,
):
  """Terminate ingest/archive pools and re-raise worker death."""
  if isinstance(exc, MultiprocessingWorkerExitError):
    terminate_pool_bounded(ingest_pool)
    terminate_pool_bounded(db_writer_pool)
    terminate_pool_bounded(archive_pool)
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
):
  """Write queued parse payloads and return updated finished count."""
  if not parse_tasks:
    return chunk_ingest_finished
  writer_fn = partial(_db_writer_worker, manager_lock)
  task_batch = list(parse_tasks)
  parse_tasks.clear()
  if _sync_timedb_ingest_inline_requested() or db_writer_pool is None:
    write_results_iter = (writer_fn(_db_write_task_tuple(task)) for task in task_batch)
  else:
    write_results_iter = imap_unordered_watch_pool(
        db_writer_pool,
        writer_fn,
        [_db_write_task_tuple(task) for task in task_batch],
        context="sync_timedb ingest db_writer pool",
    )
  try:
    for result in write_results_iter:
      stats_fname, need_archival, ingest_ok, elapsed_s = result
      if ingest_ok:
        _transition_file_state(file_states, stats_fname, SyncFileState.WRITTEN)
        successful_paths.append(stats_fname)
        if should_archive and need_archival:
          files_to_be_archived.append(stats_fname)
        remaining = pending_total - chunk_ingest_finished - 1
        chunk_ingest_finished += 1
        _log_sync_timedb_ingest_completed(stats_fname, elapsed_s, remaining)
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
  raw = _load_json_list(path)
  if raw is None:
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
  _save_json_atomic(path, payload)


def _host_recent_timestamps_cached(hostname, ts_low, ts_high):
  """Return cached host timestamp set for duplicate detection window."""
  key = (hostname, int(ts_low.timestamp()), int(ts_high.timestamp()))
  now = time.time()
  cached = _HOST_ITIMES_CACHE.get(key)
  if cached and (now - cached["checked_at"] <= _HOST_ITIMES_CACHE_REFRESH_SECONDS):
    return set(cached["times"])
  itimes_set = set()
  qs_times = (
      host_data.objects.filter(
          host=hostname,
          time__gte=ts_low,
          time__lt=ts_high,
      )
      .values_list("time", flat=True)
      .distinct()
  )
  for dt in qs_times.iterator():
    if dt is None:
      continue
    if dt.tzinfo is None:
      dt = dt.replace(tzinfo=timezone.utc)
    itimes_set.add(int(dt.timestamp()))
  max_timestamps = cfg.get_sync_host_itimes_cache_max_timestamps_per_entry()
  if len(itimes_set) <= max_timestamps:
    _HOST_ITIMES_CACHE[key] = {"times": tuple(itimes_set), "checked_at": now}
  if len(_HOST_ITIMES_CACHE) > _HOST_ITIMES_CACHE_MAX_ENTRIES:
    oldest_keys = sorted(
        _HOST_ITIMES_CACHE.keys(),
        key=lambda k: _HOST_ITIMES_CACHE[k]["checked_at"],
    )[:100]
    for drop_key in oldest_keys:
      _HOST_ITIMES_CACHE.pop(drop_key, None)
  return itimes_set


def _pick_write_lock_for_path(lock_or_locks, stats_file):
  if isinstance(lock_or_locks, list) and lock_or_locks:
    idx = abs(hash(stats_file)) % len(lock_or_locks)
    return lock_or_locks[idx]
  return lock_or_locks


def _reset_sync_runtime_caches():
  """Clear per-process ingest caches between sync_timedb sessions."""
  reset_sync_ingest_readiness_caches()
  _HOST_ITIMES_CACHE.clear()


def _invalidate_jid_caches(stats, proc_stats):
  try:
    from hpcperfstats.site.machine.cache_utils import (
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
  write_lock = _pick_write_lock_for_path(lock, stats_file)
  try:
    try:
      proc_it = proc_stats.itertuples(index=False)
      while True:
        batch = list(itertools.islice(proc_it, bulk_create_batch_size))
        if not batch:
          break
        proc_objs = [
            proc_data(jid=row.jid, host=row.host, proc=row.proc) for row in batch
        ]
        lock_wait_t0 = time.time()
        write_lock.acquire()
        lock_wait = time.time() - lock_wait_t0
        _log_db_lock_wait("proc", stats_file, lock_wait)
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
          batch = list(itertools.islice(stats_it, bulk_create_batch_size))
          if not batch:
            break
          host_objs = [host_data_instance_from_stats_row(row) for row in batch]
          lock_wait_t0 = time.time()
          write_lock.acquire()
          lock_wait = time.time() - lock_wait_t0
          _log_db_lock_wait("host", stats_file, lock_wait)
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


def _log_sync_timedb_ingest_completed(stats_fname, elapsed_s, remaining):
  log_print(
      "Completed file %s - processed in %.1fs - %d remaining to process."
      % (stats_fname, float(elapsed_s), remaining),
      flush=True,
  )


def _parse_stats_file_payload(stats_file, stats_file_contents=None):
  """Parse stats file into payload for deferred DB writer stage.

  Returns (stats_file, payload, need_archival, ingest_ok, parse_elapsed_s).
  """
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
      lines, load_err = load_stats_file_lines(stats_file, stats_file_contents)
      if load_err is not None:
        log_print(load_err)
        return (stats_file, None, False, False, _parse_elapsed())
      t, _jid, host = parse_first_timestamp_line(lines)
      if t is None:
        log_print("initial timestamp not found")
        return (stats_file, None, False, False, _parse_elapsed())
      if not host:
        log_print("initial host not found in %s" % stats_file)
        return (stats_file, None, False, False, _parse_elapsed())
      host = str(host).strip()
      timestamp_utc = datetime.fromtimestamp(int(float(t)), tz=timezone.utc)
      head_present = head_timestamp_present_in_db(host, timestamp_utc)
      if not head_present:
        # New file head is not in DB yet; it still needs archival post-ingest.
        start_idx, need_archival = 0, True
      else:
        ts_low = timestamp_utc - timedelta(hours=48)
        ts_high = timestamp_utc + timedelta(hours=72)
        itimes_set = _host_recent_timestamps_cached(host, ts_low, ts_high)
        start_idx, need_archival = find_processing_start_index(lines, itimes_set)
      if start_idx == -1:
        log_print("No missing timestamps found for %s" % stats_file)
        return (stats_file, None, True, True, _parse_elapsed())
      lines = lines[start_idx:]
      try:
        stats_list, proc_stats_list = parse_stats_lines(
            lines,
            0,
            eventmaps_by_type=EVENTMAPS_BY_TYPE,
            exclude_types_list=exclude_types,
        )
      except Exception as e:
        log_print("error: process data failed: ", str(e))
        log_print("Possibly corrupt file: %s" % stats_file)
        return (stats_file, None, False, False, _parse_elapsed())
      stats, proc_stats = build_stats_dataframes(stats_list, proc_stats_list)
      del stats_list
      del proc_stats_list
      if stats.empty and proc_stats.empty:
        if DEBUG:
          log_print("Unable to process stats file %s" % stats_file)
        return (stats_file, None, False, False, _parse_elapsed())
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
  stats = None
  proc_stats = None
  payload = None
  t0 = time.time()
  with _sync_worker_db_task():
    try:
      stats_file, payload, need_archival, ingest_ok, _parse_elapsed = _parse_stats_file_payload(
          stats_file, stats_file_contents=stats_file_contents
      )
      if not ingest_ok:
        return (stats_file, need_archival, False, time.time() - t0)
      if payload is None:
        return (stats_file, need_archival, True, time.time() - t0)
      stats, proc_stats = payload
      stats_file, need_archival, ingest_ok = _write_stats_payload_to_db(
          lock, stats_file, stats, proc_stats, need_archival=need_archival
      )
      return (stats_file, need_archival, ingest_ok, time.time() - t0)
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
  """Load checkpoint entries from JSON list, returning [] on invalid content."""
  raw = _load_json_list(state_path)
  if raw is None:
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
  """Atomically save checkpoint entries."""
  _save_json_atomic(state_path, list(completed_entries))


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
):
  """Record processed path in memory and checkpoint buffer."""
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
  if os.path.isfile(tar_path):
    return True
  fmt = detect_compressed_format(archive_compressed_path)
  if fmt not in ("zst", "gz"):
    return False
  return decompress_compressed_to_tar(
      archive_compressed_path,
      tar_path,
      cfg.get_archive_zstd_threads(),
  )


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

  zstd sealing and removal of raw stats run on ``archive_maintenance_interval_seconds``
  (see main loop), not after each append.
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
  try:
    _append_to_tar(archive_tar_fname, stats_files_to_tar)
  except subprocess.CalledProcessError as exc:
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
        try:
          _append_to_tar(archive_tar_fname, to_retry)
        except subprocess.CalledProcessError as exc:
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
  """Rescan archive, ingest pending files in chunks, run zstd seal/removal on interval.

  Whenever the ingest queue is empty, runs full archive maintenance before
  ``rescan_pending_stats_files``. If ``run_once`` is True, exit after the first
  rescan that finds no pending files (no ``EMPTY_QUEUE_RESCAN_SLEEP_SECONDS``
  idle wait). Used by pipeline E2E tests.
  """
  maint_interval_raw = cfg.get_archive_maintenance_interval_seconds()
  maint_interval, maint_interval_warning = (
      _resolve_archive_maintenance_interval_seconds(maint_interval_raw)
  )
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
  last_archive_maint = time.time()
  ingest_t0 = time.time()

  def _maintenance_elapsed_s():
    return max(0.0, time.time() - last_archive_maint)

  def _is_maintenance_due():
    return _maintenance_elapsed_s() >= maint_interval

  def _complete_maintenance_timer_reset():
    nonlocal last_archive_maint
    last_archive_maint = time.time()
    close_old_connections()
    connections.close_all()

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

  def _get_quarantine_skip_paths():
    captured = _capture_disqualification_inputs()
    skip_paths = set(captured["pending_stats_paths"])
    skip_paths |= set(captured["inflight_paths"])
    for paths in captured["pending_append_by_daily_tar"].values():
      skip_paths |= set(paths)
    return skip_paths

  def _janitor_disqualified_daily_tars():
    captured = _capture_disqualification_inputs()
    unmapped = set()
    with archive_janitor._accrual_snapshot_lock:
      snap = archive_janitor._accrual_snapshot
    if snap is not None:
      unmapped = collect_days_with_unmapped_closed_raw(
          snap.closed_paths, snap.mapping, tgz_archive_dir)
    else:
      unmapped = get_unmapped_closed_raw_daily_tars_cached(
          directory,
          host_name_ext,
          tgz_archive_dir,
          log_fn=log_print,
      )
    return set(build_seal_disqualified_daily_tars(
        tgz_archive_dir=tgz_archive_dir,
        remaining_raw_by_gz=None,
        pending_stats_paths=captured["pending_stats_paths"],
        inflight_paths=captured["inflight_paths"],
        pending_append_by_daily_tar=captured["pending_append_by_daily_tar"],
        in_flight_archive_tars=captured["in_flight_archive_tars"],
        pending_archive_task_tars=captured["pending_archive_task_tars"],
        unmapped_closed_raw_tars=set(unmapped or ()),
    ))

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
        for p in archive_paths:
          _transition_file_state(file_states, p, SyncFileState.ARCHIVED)
          added = _add_processed_path(
              p, processed_files, processed_files_order, checkpoint_entries,
              checkpoint_path, file_states=file_states)
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
                  checkpoint_path, file_states=file_states)
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
    except OSError:
      pass

  if maint_interval_warning is not None:
    log_print(
        "Invalid archive_maintenance_interval_seconds=%r reason=%s; using default %.1fs"
        % (maint_interval_raw, maint_interval_warning, float(maint_interval)),
        flush=True,
    )
  log_print(
      "sync_timedb continuous mode: archive janitor accrual interval %.1f s; "
      "idle rescan sleep %s s"
      % (
          float(maint_interval),
          int(EMPTY_QUEUE_RESCAN_SLEEP_SECONDS),
      ),
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
  chunk_size = cfg.get_sync_ingest_chunk_size()
  processed_files = set()
  file_states = {}
  processed_files_order = deque()
  checkpoint_entries = deque()
  checkpoint_dirty_count = 0
  inflight_archive_paths = set()
  pending_stats_files = []
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
    if use_split_db_writer_pipeline:
      ingest_pool_kind = "ingest-parse-pool"
    else:
      ingest_pool_kind = "ingest-pool"
    ingest_pool = multiprocessing.get_context('spawn').Pool(
        processes=thread_count,
        initializer=apply_pool_worker_process_title,
        initargs=(SYNC_TIMEDB_PROCESS_TITLE, ingest_pool_kind),
    )
    if use_split_db_writer_pipeline:
      db_writer_processes = cfg.get_sync_db_writer_pool_processes(
          ingest_processes=thread_count)
      db_writer_pool = multiprocessing.get_context('spawn').Pool(
          processes=db_writer_processes,
          initializer=apply_pool_worker_process_title,
          initargs=(SYNC_TIMEDB_PROCESS_TITLE, "db-writer-pool"),
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
      process_title=SYNC_TIMEDB_PROCESS_TITLE,
  )

  try:
    _ensure_daily_archive_dir_exists()
    archive_janitor.enqueue_startup_debt()

    def _run_debt_accrual_if_due(reason_label):
      if not _is_maintenance_due():
        return False
      if len(pending_stats_files) > 0:
        if archive_janitor.maybe_accrue_partial_debt_if_due(maint_interval):
          _complete_maintenance_timer_reset()
          archive_janitor.signal_work_available()
          return True
        if _is_maintenance_due():
          log_print(
              "Archive debt accrual deferred reason=ingest_backlog context=%s "
              "pending=%d"
              % (reason_label, len(pending_stats_files)),
              flush=True,
          )
        return False
      if archive_janitor.maybe_accrue_debt_if_due(maint_interval):
        _complete_maintenance_timer_reset()
        archive_janitor.signal_work_available()
        return True
      return False

    while not shutdown_requested[0]:
      _run_debt_accrual_if_due("outer_loop")

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
        pending_stats_files = _cap_pending_stats_files_list(
            rescan_pending_stats_files(
                directory,
                startdate,
                enddate,
                host_name_ext,
                processed_files | inflight_archive_paths,
                host_scan_hints=host_scan_hints,
            ),
            ingest_queue_max,
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
        _run_debt_accrual_if_due("pre_rescan")
        archive_janitor.signal_work_available()
        pending_stats_files = _cap_pending_stats_files_list(
            rescan_pending_stats_files(
                directory,
                startdate,
                enddate,
                host_name_ext,
                processed_files | inflight_archive_paths,
                host_scan_hints=host_scan_hints,
            ),
            ingest_queue_max,
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
        idle_since_empty_queue = None
        prior_pending_daily_tars = daily_tar_paths_for_stats_paths(
            pending_stats_files,
            tgz_archive_dir,
        )
        _run_debt_accrual_if_due("chunk_boundary")
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
        target_chunk_size = min(chunk_size, ingest_queue_high)
        stats_files_chunk = pending_stats_files[:target_chunk_size]
        if not stats_files_chunk:
          continue

        files_to_be_archived = []
        successful_paths = []
        chunk_ingest_finished = 0

        k = 0
        active_workers = 0
        if use_split_db_writer_pipeline:
          parse_tasks = deque()
          writer_stage_batch_size = _db_writer_stage_batch_size(
              target_chunk_size,
              ingest_queue_high,
          )
          parse_envelopes = [ParseTask(path=path) for path in stats_files_chunk]
          try:
            if _sync_timedb_ingest_inline_requested():
              parse_results_iter = (
                  _parse_stats_file_payload(task.path) for task in parse_envelopes
              )
            elif ingest_pool is None:
              parse_results_iter = iter(())
            else:
              parse_results_iter = imap_unordered_watch_pool(
                  ingest_pool,
                  _parse_stats_file_payload,
                  [task.path for task in parse_envelopes],
                  context="sync_timedb ingest parse pool",
              )
            for parsed in parse_results_iter:
              stats_fname, payload, need_archival, ingest_ok, parse_elapsed_s = parsed
              k += 1
              active_workers = max(active_workers, min(thread_count, k))
              if not ingest_ok:
                continue
              if payload is None:
                _transition_file_state(file_states, stats_fname, SyncFileState.WRITTEN)
                successful_paths.append(stats_fname)
                if should_archive and need_archival:
                  files_to_be_archived.append(stats_fname)
                remaining = len(pending_stats_files) - chunk_ingest_finished - 1
                chunk_ingest_finished += 1
                _log_sync_timedb_ingest_completed(stats_fname, parse_elapsed_s, remaining)
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
                    pending_total=len(pending_stats_files),
                )
          except MultiprocessingWorkerExitError:
            pool_worker_exit = True
            terminate_pool_bounded(ingest_pool)
            terminate_pool_bounded(db_writer_pool)
            terminate_pool_bounded(archive_pool)
            raise
          except DatabaseUnavailableExit:
            raise
          except Exception as exc:
            reraise_database_unavailable_chain(
                exc, context="sync_timedb ingest parse pool"
            )
            raise

          if parse_tasks:
            try:
              chunk_ingest_finished = _drain_db_write_tasks(
                  parse_tasks=parse_tasks,
                  manager_lock=manager_lock,
                  db_writer_pool=db_writer_pool,
                  file_states=file_states,
                  successful_paths=successful_paths,
                  files_to_be_archived=files_to_be_archived,
                  chunk_ingest_finished=chunk_ingest_finished,
                  pending_total=len(pending_stats_files),
              )
            except MultiprocessingWorkerExitError:
              pool_worker_exit = True
              terminate_pool_bounded(ingest_pool)
              terminate_pool_bounded(db_writer_pool)
              terminate_pool_bounded(archive_pool)
              raise
            except DatabaseUnavailableExit:
              raise
            except Exception as exc:
              reraise_database_unavailable_chain(
                  exc, context="sync_timedb ingest db_writer pool"
              )
              raise
        elif db_writer_combined_task:
          add_combined = partial(_ingest_parse_and_write_file, manager_lock)
          try:
            if _sync_timedb_ingest_inline_requested():
              results_iter = (
                  add_combined(path) for path in stats_files_chunk
              )
            elif ingest_pool is None:
              results_iter = iter(())
            else:
              results_iter = imap_unordered_watch_pool(
                  ingest_pool,
                  add_combined,
                  stats_files_chunk,
                  context="sync_timedb ingest pool",
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
              k += 1
              active_workers = max(active_workers, min(thread_count, k))
              if ingest_ok:
                _transition_file_state(file_states, stats_fname, SyncFileState.WRITTEN)
                successful_paths.append(stats_fname)
                if should_archive and need_archival:
                  files_to_be_archived.append(stats_fname)
                remaining = len(pending_stats_files) - chunk_ingest_finished - 1
                chunk_ingest_finished += 1
                _log_sync_timedb_ingest_completed(stats_fname, elapsed_s, remaining)
          except MultiprocessingWorkerExitError:
            pool_worker_exit = True
            terminate_pool_bounded(ingest_pool)
            terminate_pool_bounded(archive_pool)
            raise
          except DatabaseUnavailableExit:
            raise
          except Exception as exc:
            reraise_database_unavailable_chain(
                exc, context="sync_timedb ingest pool"
            )
            raise
        else:
          add_stats_file = partial(add_stats_file_to_db, manager_lock)
          try:
            if _sync_timedb_ingest_inline_requested():
              results_iter = (add_stats_file(path) for path in stats_files_chunk)
            elif ingest_pool is None:
              results_iter = iter(())
            else:
              results_iter = imap_unordered_watch_pool(
                  ingest_pool,
                  add_stats_file,
                  stats_files_chunk,
                  context="sync_timedb ingest pool",
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
              k += 1
              active_workers = max(active_workers, min(thread_count, k))
              if ingest_ok:
                _transition_file_state(file_states, stats_fname, SyncFileState.WRITTEN)
                successful_paths.append(stats_fname)
                if should_archive and need_archival:
                  files_to_be_archived.append(stats_fname)
                remaining = len(pending_stats_files) - chunk_ingest_finished - 1
                chunk_ingest_finished += 1
                _log_sync_timedb_ingest_completed(stats_fname, elapsed_s, remaining)
          except MultiprocessingWorkerExitError:
            pool_worker_exit = True
            terminate_pool_bounded(ingest_pool)
            terminate_pool_bounded(archive_pool)
            raise
          except DatabaseUnavailableExit:
            raise
          except Exception as exc:
            reraise_database_unavailable_chain(
                exc, context="sync_timedb ingest pool"
            )
            raise

        log_print("loading time", time.time() - ingest_t0)
        log_print(
            "Throughput telemetry: active_workers=%d backlog=%d chunk_size=%d bulk_create_batch=%d"
            % (
                active_workers,
                len(pending_stats_files),
                chunk_size,
                bulk_create_batch_size,
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
              checkpoint_path, file_states=file_states)
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

        pending_stats_files = pending_stats_files[len(stats_files_chunk):]
        if len(pending_stats_files) <= ingest_queue_low:
          log_print(
              "Ingest pending at/below low watermark pending=%d low=%d"
              % (len(pending_stats_files), ingest_queue_low),
              flush=True,
          )
        chunk_counter += 1
        _maybe_exit_on_supervisor_rss_limit(chunk_counter)

        # Non-blocking: after files were added to their tars this chunk, kick a
        # single-flight background pass to seal completed days and remove their
        # raw stats / uncompressed .tar. Ingest advances to the next chunk
        # immediately; days with raw still to ingest/append are disqualified.
        current_pending_daily_tars = daily_tar_paths_for_stats_paths(
            pending_stats_files,
            tgz_archive_dir,
        )
        archive_janitor.enqueue_day_close_for_drained_days(
            prior_pending_daily_tars,
            current_pending_daily_tars,
        )
        if ar_file_mapping:
          archive_janitor.enqueue_completed_prior_days_reclaim(
              chunk_daily_tars=_prior_day_tars_from_archive_mapping(
                  ar_file_mapping,
                  local_tz=local_timezone,
              ),
          )
          archive_janitor.signal_work_available()
        elif prior_pending_daily_tars != current_pending_daily_tars:
          archive_janitor.signal_work_available()

        _dispatch_due_archive_retries()

        if chunk_counter % rescan_every_chunks == 0:
          _finalize_archive_slots_if_needed(
              force=True,
              allow_defer=bool(pending_stats_files),
              context="rescan_every_chunks",
          )
          pending_stats_files = _cap_pending_stats_files_list(
              rescan_pending_stats_files(
                  directory,
                  startdate,
                  enddate,
                  host_name_ext,
                  processed_files | inflight_archive_paths,
                  host_scan_hints=host_scan_hints,
              ),
              ingest_queue_max,
          )
          log_print(
              "Rescanned after %d chunks; pending files (oldest first): %d"
              % (rescan_every_chunks, len(pending_stats_files)))

      _finalize_archive_slots_if_needed(
          force=True,
          allow_defer=bool(pending_stats_files),
          context="end_of_batch",
      )
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
    archive_janitor.shutdown(wait=True)
    _finalize_archive_slots_if_needed(force=True)
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
    ) as archive_pool:
      run_sync_timedb_supervisor_loop(
          directory,
          startdate,
          enddate,
          host_name_ext,
          manager_lock,
          archive_pool,
          run_once=run_once,
      )

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
    sys.exit(exc.exit_code)
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
