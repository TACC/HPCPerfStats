#!/usr/bin/env python3
"""Load raw stats files into TimescaleDB (host_data, proc_data). Parses stats, applies hardware counter maps, computes deltas/arc, bulk-inserts, and optionally archives processed files. Runs in parallel with configurable chunk size.

CLI: no args or ``YYYY-MM-DD`` range uses a sliding window (see ``days_to_process``). First arg ``all`` scans every host stats dir under ``archive_dir`` (subdirs whose names end with ``DEFAULT.host_name_ext`` from ini).

DB access is process-safe: add_stats_file_to_db runs in multiprocessing workers and calls close_old_connections() at entry so each worker uses a fresh connection. Writes are serialized with a shared lock.

"""
import itertools
import multiprocessing
import os
import signal
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
import pandas as pd

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
    collect_stats_files_in_range,
    filter_files_to_add_to_archive,
    get_existing_archive_members,
    get_stats_chunk,
    rescan_pending_stats_files,
    get_tar_member_name,
    get_verified_files_to_remove,
    stats_file_is_active_segment,
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

# Thread count for database loading and archival
thread_count = cfg.get_worker_thread_count(4)
# pigz thread cap: one quarter of total cores, clamped to at least one.
pigz_thread_count = max(1, cfg.get_worker_thread_count(4))

# amount of concurrent pigz using thread_count*2 cores
archive_thread_count = int(thread_count / 2)
if archive_thread_count < 1:
  archive_thread_count = 1

# How many days to process if run without any arguments
days_to_process = 5

# How many files to proccess and archive at once
chunk_size = 100
# Rescan stats directory after this many processed chunks
rescan_every_chunks = 10
# Bound processed-file tracking to avoid unbounded set growth in long runs.
processed_files_max_size = 200000

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
    return (stats_file, False)

  if stats_file_is_active_segment(stats_file):
    if DEBUG:
      log_print("Skipping active segment (still linked to current): %s" % stats_file)
    return (stats_file, False)

  lines, load_err = load_stats_file_lines(stats_file, stats_file_contents)
  if load_err is not None:
    log_print(load_err)
    return (stats_file, False)

  t, jid, host = parse_first_timestamp_line(lines)
  if t is None:
    log_print("initial timestamp not found")
    return (stats_file, False)

  timestamp_utc = datetime.fromtimestamp(int(float(t)), tz=timezone.utc)
  ts_low = timestamp_utc - timedelta(hours=48)
  ts_high = timestamp_utc + timedelta(hours=72)
  # Use Django ORM to fetch distinct existing timestamps for this host in range.
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
    # Normalise to UTC then convert to integer epoch seconds.
    if dt.tzinfo is None:
      dt = dt.replace(tzinfo=timezone.utc)
    epoch = int(dt.timestamp())
    itimes_set.add(epoch)

  start_idx, need_archival = find_processing_start_index(lines, itimes_set)
  if start_idx == -1:
    log_print("No missing timestamps found for %s" % stats_file)
    return (stats_file, True)

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
    return (stats_file, False)

  stats, proc_stats = build_stats_dataframes(stats_list, proc_stats_list)
  del stats_list
  del proc_stats_list
  if stats.empty and proc_stats.empty:
    if DEBUG:
      log_print("Unable to process stats file %s" % stats_file)
    return (stats_file, False)

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

  if DEBUG:
    log_print("File successfully added to DB")
  return (stats_file, need_archival)


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
  """Append file_paths to tar at tar_path. Does nothing if file_paths is empty."""
  if not file_paths:
    return
  with file_write_lock(tar_path):
    result = subprocess.run(
        ['/bin/tar', 'uvf', tar_path] + file_paths,
        capture_output=True,
        text=True,
        check=False,
    )
  if result.stdout:
    log_print(result.stdout, flush=True)
  if result.stderr:
    log_print(result.stderr, flush=True)
  if result.returncode != 0:
    raise subprocess.CalledProcessError(
        result.returncode, result.args, output=result.stdout, stderr=result.stderr)
  log_print("Archived: " + str(file_paths))


def _compress_tar_gz(tar_path, num_threads=None):
  """Compress .tar with pigz. num_threads defaults to one quarter total cores."""
  if num_threads is None:
    num_threads = pigz_thread_count
  if not os.path.exists(tar_path):
    return
  gz_path = "%s.gz" % tar_path
  with file_write_lock(tar_path):
    result = subprocess.run(
        ['/usr/bin/pigz', '-f', '-8', '-v', '-p', str(num_threads), tar_path],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.stdout:
      log_print(result.stdout, flush=True)
    if result.stderr:
      log_print(result.stderr, flush=True)
    if result.returncode != 0:
      raise subprocess.CalledProcessError(
          result.returncode, result.args, output=result.stdout, stderr=result.stderr)
  # pigz rewrites tar_path to tar_path.gz, so synchronize on the resulting file too.
  if os.path.exists(gz_path):
    with file_write_lock(gz_path):
      pass


def archive_stats_files(archive_info):
  """Append stats files to a daily .tar, compress with pigz, and remove originals after verification."""
  archive_fname, stats_files = archive_info
  archive_tar_fname = archive_fname[:-3]

  _decompress_gz(archive_fname)
  existing_members = get_existing_archive_members(archive_tar_fname)

  stats_files_to_tar = filter_files_to_add_to_archive(
      stats_files, existing_members, debug=DEBUG)
  _append_to_tar(archive_tar_fname, stats_files_to_tar)

  existing_members = get_existing_archive_members(archive_tar_fname)
  for path in get_verified_files_to_remove(stats_files, existing_members):
    log_print("removing stats file:" + path)
    with file_write_lock(path):
      os.remove(path)

  _compress_tar_gz(archive_tar_fname)


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
    #################################################################

    default_start = datetime.combine(
        datetime.today(), datetime.min.time()) - timedelta(days=days_to_process)
    default_end = default_start + timedelta(days=days_to_process)
    startdate, enddate = parse_start_end_dates(
        sys.argv, default_start, default_end)

    if len(sys.argv) > 1 and sys.argv[1] == 'all':
      startdate = 'all'
      enddate = None

    if startdate == 'all':
      log_print(
          "###Date Range of stats files to ingest: entire archive directory "
          "(no date filter)####")
    else:
      log_date_range("stats files to ingest", startdate, enddate)
    #################################################################

    host_name_ext = cfg.get_host_name_ext().strip()
    if not host_name_ext:
      log_print(
          "ERROR: DEFAULT.host_name_ext must be set; sync_timedb uses archive "
          "subdirectories whose names end with this suffix.")
      sys.exit(1)

    start = time.time()
    directory = cfg.get_archive_dir_path()
    stats_files = collect_stats_files_in_range(
        directory, startdate, enddate, host_name_ext)
    log_print("Number of host stats files to process = ", len(stats_files))

    manager = multiprocessing.Manager()
    try:
      manager_lock = manager.Lock()
      with multiprocessing.get_context('spawn').Pool(
          processes=archive_thread_count) as archive_pool:
        archive_job = None
        processed_files = set()
        processed_files_order = deque()
        pending_stats_files = list(stats_files)
        chunk_counter = 0

        # Process chunk_size files at a time; every rescan_every_chunks chunks,
        # rescan and reprioritize by newest-first (including newly arrived files).
        while pending_stats_files:
          if shutdown_requested[0]:
            log_print("Exiting due to SIGTERM")
            break
          if DEBUG:
            log_print("Begining Chunk(%s) #%s Processing" % (chunk_size, chunk_counter))

          stats_files_chunk = pending_stats_files[:chunk_size]
          if not stats_files_chunk:
            continue

          ar_file_mapping = {}
          files_to_be_archived = []
          log_print("%s files per chunk" % chunk_size)

          with multiprocessing.get_context('spawn').Pool(
              processes=thread_count) as pool:
            add_stats_file = partial(add_stats_file_to_db, manager_lock)
            k = 0
            for stats_fname, need_archival in pool.imap_unordered(
                add_stats_file, stats_files_chunk):
              k += 1
              if should_archive and need_archival:
                files_to_be_archived.append(stats_fname)
              log_print(
                  "chunk %s: completed file %s out of %s\n" % (
                      chunk_counter, k, chunk_size),
                  flush=True)

          log_print("loading time", time.time() - start)
          log_print(
              "Files marked for archival: %d" % len(files_to_be_archived))

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

          # skip first iteration, on first there will be no archive_job
          if archive_job is not None:
            if DEBUG:
              log_print("Checking/waiting for background archival proccesses")

            # Wait until last archive_job is complete before starting another one
            archive_job.get()

          if DEBUG:
            log_print("files to be archived: %s" % ar_file_mapping)

          archive_job = archive_pool.map_async(
              archive_stats_files, list(ar_file_mapping.items()))

          log_print("Archival running in the background")
          for stats_path in stats_files_chunk:
            if stats_path in processed_files:
              continue
            processed_files.add(stats_path)
            processed_files_order.append(stats_path)
          while len(processed_files_order) > processed_files_max_size:
            old_path = processed_files_order.popleft()
            processed_files.discard(old_path)
          pending_stats_files = pending_stats_files[chunk_size:]
          chunk_counter += 1

          if chunk_counter % rescan_every_chunks == 0:
            if archive_job is not None:
              if DEBUG:
                log_print(
                    "Waiting for background archival proccesses before rescan")
              archive_job.get()
              archive_job = None
            pending_stats_files = rescan_pending_stats_files(
                directory, startdate, enddate, host_name_ext, processed_files)
            log_print(
                "Rescanned after %d chunks; pending files (newest first): %d"
                % (rescan_every_chunks, len(pending_stats_files)))

        if archive_job is not None:
          archive_job.get()

      log_print("sync_timedb sleeping")

      # Close DB connections before long sleep to avoid idle connections.
      close_old_connections()
      connections.close_all()
      sleep_until_shutdown(600)

      if DEBUG:
        log_print("sync_timedb finished")
    finally:
      manager.shutdown()
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
