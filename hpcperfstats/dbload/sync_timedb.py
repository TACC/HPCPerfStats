#!/usr/bin/env python3
"""Load raw stats files into TimescaleDB (host_data, proc_data). Parses stats, applies hardware counter maps, computes deltas/arc, bulk-inserts, and optionally archives processed files (append to daily ``.tar``; ``pigz`` seal and raw-file removal on ``archive_pigz_interval_seconds``, default 4h). Runs in parallel with configurable chunk size.

After each ingest wave completes, rescans the archive directory for new files. When none are pending, sleeps ``EMPTY_QUEUE_RESCAN_SLEEP_SECONDS`` (default 5 minutes) and scans again, until SIGTERM.

CLI: no args or ``YYYY-MM-DD`` range uses a sliding window (see ``days_to_process``). First arg ``all`` scans every host stats dir under ``archive_dir`` (subdirs whose names end with ``DEFAULT.host_name_ext`` from ini). Prefix ``once`` to exit after one idle rescan (no 300s sleep), e.g. ``once all``.

DB access is process-safe: add_stats_file_to_db runs in multiprocessing workers and calls close_old_connections() at entry so each worker uses a fresh connection. Writes are serialized with a shared lock.

"""
import itertools
import json
import multiprocessing
import os
import shutil
import signal
import tempfile
import subprocess
import sys
import time
import warnings
from collections import deque
from datetime import datetime, timedelta, timezone
from functools import partial
from hpcperfstats.django_bootstrap import ensure_django
ensure_django()

import hpcperfstats.dbload.django_timezone_utc_shim  # noqa: F401

from django.db import IntegrityError, close_old_connections, connections

import hpcperfstats.conf_parser as cfg
from hpcperfstats.dbload.date_utils import log_date_range, parse_start_end_dates
from hpcperfstats.dbload.io_helpers import host_data_instance_from_stats_row
from hpcperfstats.dbload.pigz_cli import pigz_decompress_verbose
from hpcperfstats.print_utils import log_print
from hpcperfstats.shutdown_utils import (
    shutdown_requested,
    send_sigchld_to_parent,
    sleep_until_shutdown,
)
from hpcperfstats.dbload.sync_timedb_archive_helpers import (
    build_archive_mapping,
    dedupe_tar_keep_largest_file_per_member,
    filter_files_to_add_to_archive,
    get_existing_archive_members,
    iter_daily_tar_paths,
    remove_verified_archived_raw_files,
    replace_corrupt_tar_from_gzip_backup,
    rescan_pending_stats_files,
    seal_dirty_daily_archives,
    stats_file_is_active_segment,
    tar_has_duplicate_file_members,
    verify_tar_archive_readable,
)
from hpcperfstats.file_locking import file_write_lock
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
# pigz thread cap: one quarter of total cores, clamped to at least one (CPU, not DB pool).
pigz_thread_count = max(1, cfg.get_worker_thread_count(4))

archive_thread_count = cfg.get_sync_archive_pool_processes()

# How many days to process if run without any arguments
days_to_process = 5

# How many files to proccess and archive at once
chunk_size = 100
# Max paths per ``tar -T`` batch (limits list-file size; argv stays tiny).
tar_append_batch_size = 32
# Rescan stats directory after this many processed chunks
rescan_every_chunks = 10
# Bound processed-file tracking to avoid unbounded set growth in long runs.
processed_files_max_size = 200000
SYNC_TIMEDB_CHECKPOINT_BASENAME = ".sync_timedb_state.json"
SYNC_TIMEDB_CHECKPOINT_FLUSH_EVERY_FILES = 100

# When no pending files remain after a directory rescan, sleep this long (seconds)
# before scanning again. Interruptible via shutdown_requested / SIGTERM path.
EMPTY_QUEUE_RESCAN_SLEEP_SECONDS = 300

# Set to 1/yes/true so ingest runs in the parent process (no spawn pool). Required
# for pytest-django: pool workers would reconnect with default PORTAL.dbname instead
# of the test database created for the session.
_SYNC_TIMEDB_INGEST_INLINE_ENV = "HPCPERFSTATS_SYNC_TIMEDB_INGEST_INLINE"


def _sync_timedb_ingest_inline_requested():
  return os.environ.get(_SYNC_TIMEDB_INGEST_INLINE_ENV, "").strip().lower() in (
      "1", "yes", "true")

# Rows per bulk_create batch to limit peak memory per worker
bulk_create_batch_size = 10000

tgz_archive_dir = cfg.get_daily_archive_dir_path()

# This routine will read the file until a timestamp is read that is not in the database. It then reads in the rest of the file.
def add_stats_file_to_db(lock, stats_file, stats_file_contents=None):
  """Parse a stats file, map hardware counters, compute deltas/arc, and bulk-insert into host_data and proc_data. Returns (stats_file, need_archival). Uses lock for DB writes.

    """
  close_old_connections()

  hostname, _ = parse_stats_file_path(stats_file)
  if hostname is None:
    log_print("Invalid stats file path: %s" % stats_file)
    return (stats_file, False, False)

  if stats_file_is_active_segment(stats_file):
    if DEBUG:
      log_print("Skipping active segment (still linked to current): %s" % stats_file)
    return (stats_file, False, False)

  lines, load_err = load_stats_file_lines(stats_file, stats_file_contents)
  if load_err is not None:
    log_print(load_err)
    return (stats_file, False, False)

  t, jid, host = parse_first_timestamp_line(lines)
  if t is None:
    log_print("initial timestamp not found")
    return (stats_file, False, False)

  timestamp_utc = datetime.fromtimestamp(int(float(t)), tz=timezone.utc)
  # Fast path: if head timestamp is not present, process from file head.
  head_present = host_data.objects.filter(
      host=hostname,
      time=timestamp_utc,
  ).exists()

  if not head_present:
    start_idx, need_archival = 0, False
  else:
    ts_low = timestamp_utc - timedelta(hours=48)
    ts_high = timestamp_utc + timedelta(hours=72)
    # Fallback: fetch distinct existing timestamps for deterministic resume.
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
      epoch = int(dt.timestamp())
      itimes_set.add(epoch)
    start_idx, need_archival = find_processing_start_index(lines, itimes_set)
  if start_idx == -1:
    log_print("No missing timestamps found for %s" % stats_file)
    return (stats_file, True, True)

  # Keep only lines from start_idx to avoid holding the full file in memory
  lines = lines[start_idx:]

  start = time.time()
  try:
    stats_list, proc_stats_list = parse_stats_lines(
        lines, 0,
        eventmaps_by_type=EVENTMAPS_BY_TYPE,
        exclude_types_list=exclude_types,
    )
  except Exception as e:
    log_print("error: process data failed: ", str(e))
    log_print("Possibly corrupt file: %s" % stats_file)
    return (stats_file, False, False)

  stats, proc_stats = build_stats_dataframes(stats_list, proc_stats_list)
  del stats_list
  del proc_stats_list
  if stats.empty and proc_stats.empty:
    if DEBUG:
      log_print("Unable to process stats file %s" % stats_file)
    return (stats_file, False, False)

  stats = compute_deltas_and_arc(stats)
  log_print("processing time for {0} {1:.1f}s".format(stats_file, time.time() - start))

  lock.acquire()
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
        proc_data.objects.bulk_create(proc_objs, ignore_conflicts=True)
    except Exception as e:
      if DEBUG:
        log_print("error in proc_data bulk_create: %s\nFile %s" % (e, stats_file))
      _insert_proc_data_individually(proc_stats)
  finally:
    lock.release()

  lock.acquire()
  need_archival = True
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
          host_data.objects.bulk_create(host_objs, ignore_conflicts=True)
    except Exception as e:
      if DEBUG:
        log_print("error in host_data bulk_create:", str(e))
      need_archival = _insert_host_data_individually(stats)
  finally:
    lock.release()

  try:
    from hpcperfstats.site.machine.cache_utils import (
        invalidate_jid_derived_cache_keys,
        invalidate_job_plot_cache_keys_for_jids,
    )

    jids = set()
    if not stats.empty:
      if "jid" in stats.columns:
        jids.update(str(x) for x in stats["jid"].dropna().unique())
    if not proc_stats.empty and "jid" in proc_stats.columns:
      jids.update(str(x) for x in proc_stats["jid"].dropna().unique())
    if jids:
      invalidate_jid_derived_cache_keys(jids)
      invalidate_job_plot_cache_keys_for_jids(jids)
  except Exception:
    pass

  if DEBUG:
    log_print("File successfully added to DB")
  return (stats_file, need_archival, True)


def _load_sync_checkpoint(state_path):
  """Load checkpoint entries from JSON list, returning [] on invalid content."""
  try:
    with open(state_path, "r", encoding="utf-8") as fh:
      raw = json.load(fh)
  except (OSError, ValueError, TypeError):
    return []
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
  """Atomically save checkpoint entries."""
  parent = os.path.dirname(str(state_path))
  if parent:
    os.makedirs(parent, exist_ok=True)
  tmp_path = "%s.tmp" % state_path
  with open(tmp_path, "w", encoding="utf-8") as fh:
    json.dump(list(completed_entries), fh)
  os.replace(tmp_path, state_path)


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
  while len(checkpoint_entries) > processed_files_max_size:
    checkpoint_entries.popleft()
  return True


def _insert_proc_data_individually(proc_stats_df):
  """Fallback: insert proc_data rows one by one, skipping duplicates.

    """
  unique_violations = 0
  for row in proc_stats_df.itertuples(index=False):
    try:
      proc_data(jid=row.jid, host=row.host, proc=row.proc).save()
    except IntegrityError:
      unique_violations += 1
    except Exception as e:
      log_print("error in single proc_data insert:", str(e), "row:", row)
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
    for row in stats_df.itertuples(index=False):
      try:
        host_data_instance_from_stats_row(row).save()
      except IntegrityError:
        unique_violations += 1
      except Exception as e:
        log_print("error in single host_data insert:", str(e), "row:", row)
        need_archival = False
  if DEBUG:
    log_print("Existing Rows Found in DB: %s" % unique_violations)
  return need_archival




def _decompress_gz(gz_path):
  """Decompress .tar.gz with pigz. No-op if path missing or on error."""
  if not os.path.exists(gz_path):
    return
  try:
    with file_write_lock(gz_path):
      pigz_decompress_verbose(gz_path, pigz_thread_count)
  except subprocess.CalledProcessError:
    pass


def _append_to_tar(tar_path, file_paths):
  """Append file_paths to tar at tar_path. Does nothing if file_paths is empty.

  Uses GNU/BSD ``tar -r -f`` with ``--null -T`` so argv stays tiny, names may
  contain spaces, and we do not rely on ``-u`` (mtime) vs. Python-side filters.
  Skips paths that disappeared before append (race). Batches via
  ``tar_append_batch_size``.
  """
  if not file_paths:
    return
  batch = max(1, int(tar_append_batch_size))
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
        result = subprocess.run(
            [
                tar_bin,
                "-r",
                "-f",
                tar_path,
                "--null",
                "-T",
                list_path,
            ],
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


def archive_stats_files(archive_info):
  """Append stats files to a daily ``.tar`` (verify, recover, dedupe).

  ``pigz`` sealing and removal of raw stats run on ``archive_pigz_interval_seconds``
  (see main loop), not after each append.
  """
  archive_fname, stats_files = archive_info
  archive_tar_fname = archive_fname[:-3]

  if not os.path.exists(archive_tar_fname):
    if os.path.exists(archive_fname):
      _decompress_gz(archive_fname)
  existing_members = get_existing_archive_members(archive_tar_fname)

  # Corrupt/truncated .tar can make Python's tarfile reader return {} while GNU
  # tar still refuses append (exit 2). Recover before append so we never raise
  # without trying restore-from-.gz (same as post-append path).
  if os.path.isfile(archive_tar_fname) and not verify_tar_archive_readable(
      archive_tar_fname):
    log_print(
        "Daily tar unreadable before append; recovering from .tar.gz or "
        "clearing: %s" % archive_tar_fname,
        flush=True,
    )
    if not replace_corrupt_tar_from_gzip_backup(
        archive_tar_fname, archive_fname, pigz_thread_count):
      log_print(
          "ERROR: could not restore daily tar before append; leaving raw stats "
          "files in place: %s" % archive_fname,
          flush=True,
      )
      return
    existing_members = get_existing_archive_members(archive_tar_fname)

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
    return

  if stats_files_to_tar:
    if not verify_tar_archive_readable(archive_tar_fname):
      log_print(
          "Daily tar failed integrity check after append; recovering from "
          ".tar.gz or clearing for rebuild: %s" % archive_tar_fname,
          flush=True,
      )
      if not replace_corrupt_tar_from_gzip_backup(
          archive_tar_fname, archive_fname, pigz_thread_count):
        log_print(
            "ERROR: could not restore daily tar from %s; leaving raw stats "
            "files in place" % archive_fname,
            flush=True,
        )
        return
      existing_after = get_existing_archive_members(archive_tar_fname)
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
          return
      if not verify_tar_archive_readable(archive_tar_fname):
        log_print(
            "ERROR: daily tar still unreadable after recovery append; leaving "
            "raw stats files in place: %s" % archive_tar_fname,
            flush=True,
        )
        return

def database_startup():
  """Print DB version, database size, and optionally chunk compression stats for host_data."""
  from django.db import connection
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


def run_sync_timedb_supervisor_loop(
    directory,
    startdate,
    enddate,
    host_name_ext,
    manager_lock,
    archive_pool,
    run_once=False,
):
  """Rescan archive, ingest pending files in chunks, run pigz/removal on interval; loop until shutdown.

  If ``run_once`` is True, exit after the first rescan that finds no pending
  files (no ``EMPTY_QUEUE_RESCAN_SLEEP_SECONDS`` idle wait). Used by pipeline
  E2E tests.
  """
  pigz_interval = cfg.get_archive_pigz_interval_seconds()
  last_archive_maint = time.time()
  ingest_t0 = time.time()

  def _run_scheduled_archive_maintenance():
    for tar_path in sorted(iter_daily_tar_paths(tgz_archive_dir)):
      if not tar_has_duplicate_file_members(tar_path):
        continue
      log_print(
          "Duplicate member paths in daily tar; rewriting (keep largest per path): "
          "%s" % tar_path,
          flush=True,
      )
      if not dedupe_tar_keep_largest_file_per_member(tar_path, log_fn=log_print):
        log_print(
            "ERROR: tar dedupe failed during scheduled maintenance: %s" % tar_path,
            flush=True,
        )
    seal_dirty_daily_archives(
        tgz_archive_dir,
        local_tz=local_timezone,
        pigz_threads=pigz_thread_count,
        compress_level=cfg.get_archive_pigz_level(),
        keep_uncompressed_tar=cfg.get_archive_keep_uncompressed_tar(),
        idle_seconds=cfg.get_archive_seal_idle_seconds(),
        seal_immediately_if_dirty=True,
        log_fn=log_print,
    )
    remove_verified_archived_raw_files(
        directory, host_name_ext, tgz_archive_dir, log_fn=log_print)

  def _finalize_archive_job_if_needed():
    nonlocal archive_job
    nonlocal archive_job_deferred_paths
    nonlocal checkpoint_dirty_count
    if archive_job is None:
      return
    archive_job.get()
    for p in archive_job_deferred_paths:
      added = _add_processed_path(
          p, processed_files, processed_files_order, checkpoint_entries,
          checkpoint_path)
      if added:
        checkpoint_dirty_count += 1
      inflight_archive_paths.discard(p)
    _flush_checkpoint_if_needed()
    archive_job = None
    archive_job_deferred_paths = []

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

  log_print(
      "sync_timedb continuous mode: pigz interval %s s; idle rescan sleep %s s"
      % (int(pigz_interval), int(EMPTY_QUEUE_RESCAN_SLEEP_SECONDS)),
      flush=True,
  )

  archive_job = None
  archive_job_deferred_paths = []
  ingest_pool = None
  processed_files = set()
  processed_files_order = deque()
  checkpoint_entries = deque()
  checkpoint_dirty_count = 0
  inflight_archive_paths = set()
  pending_stats_files = []
  chunk_counter = 0
  checkpoint_path = os.path.join(directory, SYNC_TIMEDB_CHECKPOINT_BASENAME)

  for entry in _load_sync_checkpoint(checkpoint_path):
    fp = _path_fingerprint(entry["path"])
    if fp is None:
      continue
    if fp["size"] != entry["size"] or fp["mtime"] != entry["mtime"]:
      continue
    processed_files.add(entry["path"])
    processed_files_order.append(entry["path"])
    checkpoint_entries.append(entry)

  if not _sync_timedb_ingest_inline_requested():
    ingest_pool = multiprocessing.get_context('spawn').Pool(
        processes=thread_count)

  try:
    while not shutdown_requested[0]:
      if time.time() - last_archive_maint >= pigz_interval:
        _finalize_archive_job_if_needed()
        log_print(
            "Running scheduled daily-archive pigz and raw file removal",
            flush=True,
        )
        try:
          os.makedirs(tgz_archive_dir, exist_ok=True)
        except OSError:
          if not os.path.isdir(tgz_archive_dir):
            raise
        _run_scheduled_archive_maintenance()
        last_archive_maint = time.time()
        close_old_connections()
        connections.close_all()

      if not pending_stats_files:
        pending_stats_files = rescan_pending_stats_files(
            directory,
            startdate,
            enddate,
            host_name_ext,
            processed_files | inflight_archive_paths,
        )
        if pending_stats_files:
          log_print(
              "Number of host stats files to process = ",
              len(pending_stats_files),
              flush=True,
          )
          chunk_counter = 0
        else:
          log_print(
              "No pending stats files; sleeping %s s before next directory scan"
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
          continue

      while pending_stats_files:
        if shutdown_requested[0]:
          log_print("Exiting due to SIGTERM")
          break
        if DEBUG:
          log_print(
              "Begining Chunk(%s) #%s Processing" % (chunk_size, chunk_counter))

        stats_files_chunk = pending_stats_files[:chunk_size]
        if not stats_files_chunk:
          continue

        files_to_be_archived = []
        successful_paths = []
        log_print("%s files per chunk" % chunk_size)

        add_stats_file = partial(add_stats_file_to_db, manager_lock)
        if _sync_timedb_ingest_inline_requested():
          results_iter = (add_stats_file(path) for path in stats_files_chunk)
        else:
          if ingest_pool is None:
            results_iter = iter(())
          else:
            results_iter = ingest_pool.imap_unordered(
                add_stats_file, stats_files_chunk)

        k = 0
        for result in results_iter:
          ingest_ok = True
          if len(result) >= 3:
            stats_fname, need_archival, ingest_ok = result
          else:
            stats_fname, need_archival = result
          k += 1
          if ingest_ok:
            successful_paths.append(stats_fname)
          if ingest_ok and should_archive and need_archival:
            files_to_be_archived.append(stats_fname)
          log_print(
              "chunk %s: completed file %s out of %s\n" % (
                  chunk_counter, k, chunk_size),
              flush=True)

        log_print("loading time", time.time() - ingest_t0)
        log_print("Files marked for archival: %d" % len(files_to_be_archived))

        if files_to_be_archived:
          os.makedirs(tgz_archive_dir, exist_ok=True)
        ar_file_mapping = build_archive_mapping(
            files_to_be_archived, tgz_archive_dir)
        total_in_mapping = sum(len(v) for v in ar_file_mapping.values())
        if ar_file_mapping:
          log_print(
              "Archive mapping: %d tar(s), %d file(s) to archive"
              % (len(ar_file_mapping), total_in_mapping))
        elif files_to_be_archived:
          log_print(
              "Archive mapping empty (all files skipped: no timestamp in head)")

        _finalize_archive_job_if_needed()

        archived_candidates = set(files_to_be_archived)
        immediate_paths = [
            p for p in successful_paths if p not in archived_candidates
        ]
        deferred_paths = [p for p in successful_paths if p in archived_candidates]
        for p in immediate_paths:
          added = _add_processed_path(
              p, processed_files, processed_files_order, checkpoint_entries,
              checkpoint_path)
          if added:
            checkpoint_dirty_count += 1
        _flush_checkpoint_if_needed()

        if ar_file_mapping:
          archive_job = archive_pool.map_async(
              archive_stats_files, list(ar_file_mapping.items()))
          archive_job_deferred_paths = deferred_paths
          inflight_archive_paths.update(deferred_paths)
          log_print("Archival running in the background")
        elif deferred_paths:
          log_print(
              "Deferring processed marker for %d file(s): archival mapping missing"
              % len(deferred_paths),
              flush=True,
          )

        pending_stats_files = pending_stats_files[chunk_size:]
        chunk_counter += 1

        if chunk_counter % rescan_every_chunks == 0:
          _finalize_archive_job_if_needed()
          pending_stats_files = rescan_pending_stats_files(
              directory,
              startdate,
              enddate,
              host_name_ext,
              processed_files | inflight_archive_paths,
          )
          log_print(
              "Rescanned after %d chunks; pending files (newest first): %d"
              % (rescan_every_chunks, len(pending_stats_files)))

      _finalize_archive_job_if_needed()
      _flush_checkpoint_if_needed(force=True)
      close_old_connections()
      connections.close_all()
  finally:
    if archive_job is not None:
      archive_job.get()
    _flush_checkpoint_if_needed(force=True)
    if ingest_pool is not None:
      if hasattr(ingest_pool, "close"):
        ingest_pool.close()
      if hasattr(ingest_pool, "join"):
        ingest_pool.join()


def parse_sync_timedb_argv(argv):
  """Parse CLI argv into ``(run_once, startdate, enddate)`` (same rules as ``sync_timedb``)."""
  argv_for_dates = list(argv)
  run_once = False
  if len(argv_for_dates) > 1 and argv_for_dates[1] == "once":
    run_once = True
    argv_for_dates = [argv_for_dates[0]] + argv_for_dates[2:]

  default_start = datetime.combine(
      datetime.today(), datetime.min.time()) - timedelta(days=days_to_process)
  default_end = default_start + timedelta(days=days_to_process)
  startdate, enddate = parse_start_end_dates(
      argv_for_dates, default_start, default_end)

  if len(argv_for_dates) > 1 and argv_for_dates[1] == 'all':
    startdate = 'all'
    enddate = None

  return run_once, startdate, enddate


def run_sync_timedb_supervisor_from_parsed(run_once, startdate, enddate):
  """Run one supervisor session after ``database_startup()`` (CLI or in-process tests)."""
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
    manager_lock = manager.Lock()
    with multiprocessing.get_context('spawn').Pool(
        processes=archive_thread_count) as archive_pool:
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
  subprocess which would connect to ``PORTAL.dbname`` from ini only. Forces
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
    database_startup()
    run_once, startdate, enddate = parse_sync_timedb_argv(sys.argv)
    run_sync_timedb_supervisor_from_parsed(run_once, startdate, enddate)
    if shutdown_requested[0]:
      sys.exit(143)
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
