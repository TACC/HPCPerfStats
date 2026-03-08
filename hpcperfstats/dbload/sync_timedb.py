#!/usr/bin/env python3
"""Load raw stats files into TimescaleDB (host_data, proc_data). Parses stats, applies hardware counter maps, computes deltas/arc, bulk-inserts, and optionally archives processed files. Runs in parallel with configurable chunk size.

DB access is process-safe: add_stats_file_to_db runs in multiprocessing workers and calls close_old_connections() at entry so each worker uses a fresh connection. Writes are serialized with a shared lock.

AI generated.
"""
import multiprocessing
import os
import subprocess
import sys
import time
import warnings
from datetime import datetime, timedelta, timezone
from functools import partial

os.environ.setdefault("DJANGO_SETTINGS_MODULE",
                      "hpcperfstats.site.hpcperfstats_site.settings")

import django
django.setup()

# Django 5.0+ removed django.utils.timezone.utc; ensure it exists for ORM/code that references it (Django 6 still does not provide it).
import django.utils.timezone as _django_tz
if not hasattr(_django_tz, "utc"):
  _django_tz.utc = timezone.utc

from django.db import IntegrityError, close_old_connections
import pandas as pd

import hpcperfstats.conf_parser as cfg
from hpcperfstats.dbload.date_utils import parse_start_end_dates
from hpcperfstats.dbload.sync_timedb_archive_helpers import (
    build_archive_mapping,
    collect_stats_files_in_range,
    filter_files_to_add_to_archive,
    get_existing_archive_members,
    get_stats_chunk,
    get_tar_member_name,
    get_verified_files_to_remove,
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

local_timezone = cfg.get_timezone()

# Thread count for database loading and archival
thread_count = cfg.get_worker_thread_count(4)

# amount of concurrent pigz using thread_count*2 cores
archive_thread_count = int(thread_count / 2)
if archive_thread_count < 1:
  archive_thread_count = 1

# How many days to process if run without any arguments
days_to_process = 5

# How many files to proccess and archive at once
chunk_size = 100

tgz_archive_dir = cfg.get_daily_archive_dir_path()


# This routine will read the file until a timestamp is read that is not in the database. It then reads in the rest of the file.
def add_stats_file_to_db(lock, stats_file, stats_file_contents=None):
  """Parse a stats file, map hardware counters, compute deltas/arc, and bulk-insert into host_data and proc_data. Returns (stats_file, need_archival). Uses lock for DB writes.

    AI generated.
    """
  close_old_connections()

  hostname, _ = parse_stats_file_path(stats_file)
  if hostname is None:
    print("Invalid stats file path: %s" % stats_file)
    return (stats_file, False)

  lines, load_err = load_stats_file_lines(stats_file, stats_file_contents)
  if load_err is not None:
    print(load_err)
    return (stats_file, False)

  t, jid, host = parse_first_timestamp_line(lines)
  if t is None:
    print("initial timestamp not found")
    return (stats_file, False)

  timestamp_utc = datetime.fromtimestamp(int(float(t)), tz=timezone.utc)
  ts_low = timestamp_utc - timedelta(hours=48)
  ts_high = timestamp_utc + timedelta(hours=72)
  # Single round-trip: fetch distinct epoch seconds via raw SQL (index-friendly)
  from django.db import connection
  with connection.cursor() as cur:
    cur.execute(
        """
        SELECT DISTINCT EXTRACT(EPOCH FROM time)::bigint
        FROM host_data
        WHERE host = %s AND time >= %s AND time < %s
        """,
        [hostname, ts_low, ts_high],
    )
    itimes_set = set(row[0] for row in cur.fetchall())

  start_idx, need_archival = find_processing_start_index(lines, itimes_set)
  if start_idx == -1:
    print("No missing timestamps found for %s" % stats_file)
    return (stats_file, True)

  start = time.time()
  try:
    stats_list, proc_stats_list = parse_stats_lines(
        lines, start_idx,
        eventmaps_by_type=EVENTMAPS_BY_TYPE,
        exclude_types_list=exclude_types,
    )
  except Exception as e:
    print("error: process data failed: ", str(e))
    print("Possibly corrupt file: %s" % stats_file)
    return (stats_file, False)

  stats, proc_stats = build_stats_dataframes(stats_list, proc_stats_list)
  if stats.empty and proc_stats.empty:
    if DEBUG:
      print("Unable to process stats file %s" % stats_file)
    return (stats_file, False)

  stats = compute_deltas_and_arc(stats)
  print("processing time for {0} {1:.1f}s".format(stats_file, time.time() - start))

  lock.acquire()
  try:
    try:
      proc_objs = [
          proc_data(jid=row.jid, host=row.host, proc=row.proc)
          for row in proc_stats.itertuples(index=False)
      ]
      proc_data.objects.bulk_create(proc_objs, ignore_conflicts=True)
    except Exception as e:
      if DEBUG:
        print("error in proc_data bulk_create: %s\nFile %s" % (e, stats_file))
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
        host_objs = [
            host_data(
                time=row.time.to_pydatetime(),
                host=row.host,
                jid=row.jid,
                type=row.type,
                dev=None,
                event=row.event,
                unit=row.unit,
                value=float(row.value) if pd.notna(row.value) else None,
                delta=float(row.delta) if pd.notna(row.delta) else None,
                arc=float(row.arc) if pd.notna(row.arc) else None,
            )
            for row in stats.itertuples(index=False)
        ]
      host_data.objects.bulk_create(host_objs, ignore_conflicts=True)
    except Exception as e:
      if DEBUG:
        print("error in host_data bulk_create:", str(e))
      need_archival = _insert_host_data_individually(stats)
  finally:
    lock.release()

  if DEBUG:
    print("File successfully added to DB")
  return (stats_file, need_archival)


def _insert_proc_data_individually(proc_stats_df):
  """Fallback: insert proc_data rows one by one, skipping duplicates.

    AI generated.
    """
  unique_violations = 0
  for row in proc_stats_df.itertuples(index=False):
    try:
      proc_data(jid=row.jid, host=row.host, proc=row.proc).save()
    except IntegrityError:
      unique_violations += 1
    except Exception as e:
      print("error in single proc_data insert:", str(e), "row:", row)
  if DEBUG:
    print("Existing Rows Found in DB: %s" % unique_violations)


def _insert_host_data_individually(stats_df):
  """Fallback: insert host_data rows one by one, skipping duplicates. Returns need_archival.

    AI generated.
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
        host_data(
            time=row.time.to_pydatetime(),
            host=row.host,
            jid=row.jid,
            type=row.type,
            dev=None,
            event=row.event,
            unit=row.unit,
            value=float(row.value) if pd.notna(row.value) else None,
            delta=float(row.delta) if pd.notna(row.delta) else None,
            arc=float(row.arc) if pd.notna(row.arc) else None,
        ).save()
      except IntegrityError:
        unique_violations += 1
      except Exception as e:
        print("error in single host_data insert:", str(e), "row:", row)
        need_archival = False
  if DEBUG:
    print("Existing Rows Found in DB: %s" % unique_violations)
  return need_archival




def _decompress_gz(gz_path):
  """Decompress .tar.gz with pigz. No-op if path missing or on error."""
  if not os.path.exists(gz_path):
    return
  try:
    subprocess.check_output(['/usr/bin/pigz', '-v', '-d', gz_path])
  except subprocess.CalledProcessError:
    pass


def _append_to_tar(tar_path, file_paths):
  """Append file_paths to tar at tar_path. Does nothing if file_paths is empty."""
  if not file_paths:
    return
  out = subprocess.check_output(['/bin/tar', 'uvf', tar_path] + file_paths)
  if DEBUG:
    print(out, flush=True)
  print("Archived: " + str(file_paths))


def _compress_tar_gz(tar_path, num_threads=None):
  """Compress .tar with pigz. num_threads defaults to thread_count * 2."""
  if num_threads is None:
    num_threads = thread_count * 2
  if not os.path.exists(tar_path):
    return
  print(
      subprocess.check_output([
          '/usr/bin/pigz', '-f', '-8', '-v', '-p', str(num_threads), tar_path
      ]),
      flush=True)


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
    print("removing stats file:" + path)
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
        print("Postgresql server version:", row[0])
      print("Database Size:", row[1])
    if DEBUG:
      try:
        cur.execute(
            "SELECT chunk_name,before_compression_total_bytes/(1024*1024*1024),after_compression_total_bytes/(1024*1024*1024) FROM chunk_compression_stats('host_data');"
        )
        for x in cur.fetchall():
          try:
            print("{0} Size: {1:8.1f} {2:8.1f}".format(*x))
          except Exception:
            pass
      except Exception:
        pass
    else:
      print("Reading Chunk Data")


if __name__ == '__main__':

  database_startup()
  #################################################################

  default_start = datetime.combine(
      datetime.today(), datetime.min.time()) - timedelta(days=days_to_process)
  default_end = default_start + timedelta(days=days_to_process)
  startdate, enddate = parse_start_end_dates(
      sys.argv, default_start, default_end)

  if len(sys.argv) > 1 and sys.argv[1] == 'all':
      startdate = 'all'
      enddate = datetime.combine(
          datetime.today(),
          datetime.min.time()) - timedelta(days=days_to_process + 1)

  print("###Date Range of stats files to ingest: {0} -> {1}####".format(
      startdate, enddate))
  #################################################################

  start = time.time()
  directory = cfg.get_archive_dir_path()
  stats_files = collect_stats_files_in_range(directory, startdate, enddate)
  print("Number of host stats files to process = ", len(stats_files))

  with multiprocessing.get_context('spawn').Pool(
      processes=archive_thread_count) as archive_pool:
    archive_job = None
    # Process and archive chunk_size files before continuing to process more
    num_chunks = (len(stats_files) + chunk_size - 1) // chunk_size if stats_files else 1
    for i in range(num_chunks):
      if DEBUG:
        print("Begining Chunk(%s) #%s Processing" % (chunk_size, i))

      stats_files_chunk = get_stats_chunk(stats_files, i, chunk_size)
      if not stats_files_chunk:
        continue

      ar_file_mapping = {}
      files_to_be_archived = []
      print("%s files per chunk" % chunk_size)

      with multiprocessing.get_context('spawn').Pool(
          processes=thread_count) as pool:
        manager = multiprocessing.Manager()
        manager_lock = manager.Lock()
        add_stats_file = partial(add_stats_file_to_db, manager_lock)
        k = 0
        for stats_fname, need_archival in pool.imap_unordered(
            add_stats_file, stats_files_chunk):
          k += 1
          if should_archive and need_archival:
            files_to_be_archived.append(stats_fname)
          print("chunk %s: completed file %s out of %s\n" % (i, k, chunk_size),
                flush=True)

      print("loading time", time.time() - start)

      ar_file_mapping = build_archive_mapping(
          files_to_be_archived, tgz_archive_dir)

      # skip first iteration, on first there will be no archive_job
      if i:
        if DEBUG:
          print("Checking/waiting for background archival proccesses")

        # Wait until last archive_job is complete before starting another one
        archive_job.get()
        print("[{0:.1f}%] completed".format(
            100 * (i + 1) / num_chunks), end="\r", flush=True)

      if DEBUG:
        print("files to be archived: %s" % ar_file_mapping)

      archive_job = archive_pool.map_async(archive_stats_files,
                                           list(ar_file_mapping.items()))

      print("Archival running in the background")

    archive_job.get()

    print("sync_timedb sleeping")

    time.sleep(900)

    if DEBUG:
      print("sync_timedb finished")
