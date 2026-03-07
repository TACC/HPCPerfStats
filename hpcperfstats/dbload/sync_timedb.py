#!/usr/bin/env python3
"""Load raw stats files into TimescaleDB (host_data, proc_data). Parses stats, applies hardware counter maps, computes deltas/arc, bulk-inserts, and optionally archives processed files. Runs in parallel with configurable chunk size.

DB access is process-safe: add_stats_file_to_db runs in multiprocessing workers and calls close_old_connections() at entry so each worker uses a fresh connection. Writes are serialized with a shared lock.

AI generated.
"""
import multiprocessing
import os
import subprocess
import sys
import tarfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from functools import partial

os.environ.setdefault("DJANGO_SETTINGS_MODULE",
                      "hpcperfstats.site.hpcperfstats_site.settings")

import django
django.setup()

# Django 5.0+ removed django.utils.timezone.utc; ensure it exists for ORM and any code that still references it
import django.utils.timezone as _django_tz
if not hasattr(_django_tz, "utc"):
  _django_tz.utc = timezone.utc

from django.db import IntegrityError, close_old_connections
import pandas as pd

import hpcperfstats.conf_parser as cfg
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
thread_count = int(int(cfg.get_total_cores()) / 4)
if thread_count < 1:
  thread_count = 1

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
  times_qs = host_data.objects.filter(
      host=hostname,
      time__gte=ts_low,
      time__lt=ts_high,
  ).values_list("time", flat=True).distinct().order_by("time")
  times = [float(tt.timestamp()) for tt in times_qs]
  itimes = [int(tt) for tt in times]

  start_idx, need_archival = find_processing_start_index(lines, times, itimes)
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


def archive_stats_files(archive_info):
  """Append stats files to a daily .tar, compress with pigz, and remove originals after verification.

    AI generated.
    """
  archive_fname, stats_files = archive_info
  archive_tar_fname = archive_fname[:-3]
  if not os.path.exists(archive_tar_fname):
    try:
      # If the file is missing, it will error, catch that error and continue
      print(
          subprocess.check_output(['/usr/bin/pigz', '-v', '-d', archive_fname]))
    except subprocess.CalledProcessError:
      pass

  existing_archive_file = {}
  if os.path.exists(archive_tar_fname):

    try:
      with tarfile.open(archive_tar_fname, 'r') as archive_tarfile:
        existing_archive_tarinfo = archive_tarfile.getmembers()

      for tar_member_data in existing_archive_tarinfo:
        existing_archive_file[tar_member_data.name] = tar_member_data.size

    except Exception:
      pass

  stats_files_to_tar = []
  for stats_fname_path in stats_files:
    fname_parts = stats_fname_path.split('/')

    if ((stats_fname_path[1:] in existing_archive_file.keys()) and
        (tarfile.open('/tmp/test.tar', 'w').gettarinfo(stats_fname_path).size
         == existing_archive_file[stats_fname_path[1:]])):

      print("file %s found in archive, skipping" % stats_fname_path)
      continue
    stats_files_to_tar.append(stats_fname_path)

  tar_output = subprocess.check_output(['/bin/tar', 'uvf', archive_tar_fname] +
                                       stats_files_to_tar)
  if DEBUG:
    print(tar_output, flush=True)
  print("Archived: " + str(stats_files_to_tar))

  ### VERIFY TAR AND DELETE DATA IF IT IS ARCHIVED AND HAS THE SAME FILE SIZE
  with tarfile.open(archive_tar_fname, 'r') as archive_tarfile:
    existing_archive_tarinfo = archive_tarfile.getmembers()
    for tar_member_data in existing_archive_tarinfo:
      existing_archive_file[tar_member_data.name] = tar_member_data.size

    for stats_fname_path in stats_files:

      if ((stats_fname_path[1:] in existing_archive_file.keys()) and
          (tarfile.open('/tmp/%s.tar' % uuid.uuid4(),
                        'w').gettarinfo(stats_fname_path).size
           == existing_archive_file[stats_fname_path[1:]])):
        print("removing stats file:" + stats_fname_path)
        os.remove(stats_fname_path)

  print(subprocess.check_output([
      '/usr/bin/pigz', '-f', '-8', '-v', '-p',
      str(thread_count * 2), archive_tar_fname
  ]),
        flush=True)


def database_startup():
  """Print DB version, database size, and optionally chunk compression stats for host_data.

    AI generated.
    """

  from django.db import connection
  with connection.cursor() as cur:
    if DEBUG:
      cur.execute("SELECT version();")
      row = cur.fetchone()
      print("Postgresql server version:", row[0] if row else "unknown")
    cur.execute("SELECT pg_size_pretty(pg_database_size(%s));",
                [cfg.get_db_name()])
    for x in cur.fetchall():
      print("Database Size:", x[0])
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

  try:
    startdate = datetime.strptime(sys.argv[1], "%Y-%m-%d")
  except:
    startdate = datetime.combine(
        datetime.today(), datetime.min.time()) - timedelta(days=days_to_process)
  try:
    enddate = datetime.strptime(sys.argv[2], "%Y-%m-%d")
  except:
    enddate = startdate + timedelta(days=days_to_process)

  if (len(sys.argv) > 1):
    if sys.argv[1] == 'all':
      startdate = 'all'
      enddate = datetime.combine(
          datetime.today(),
          datetime.min.time()) - timedelta(days=days_to_process + 1)

  print("###Date Range of stats files to ingest: {0} -> {1}####".format(
      startdate, enddate))
  #################################################################

  # Parse and convert raw stats files to pandas dataframe
  start = time.time()
  directory = cfg.get_archive_dir_path()

  stats_files = []
  for entry in os.scandir(directory):
    if entry.is_file() or not (entry.name.startswith("c") or
                               entry.name.startswith("v")):
      continue
    for stats_file in os.scandir(entry.path):
      if not stats_file.is_file() or stats_file.name.startswith('.'):
        continue
      if stats_file.name.startswith("current"):
        continue
      fdate = None
      try:
        ### different ways to define the date of the file: use timestamp or use the time of the last piece of data
        # based on filename
        name_fdate = datetime.fromtimestamp(int(stats_file.name))

        # timestamp of rabbitmq modify
        mtime_fdate = datetime.fromtimestamp(
            int(os.path.getmtime(stats_file.path)))

        fdate = mtime_fdate
      except Exception as e:
        print("error in obtaining timestamp of raw data files: ", str(e))
        continue
      if startdate == 'all':
        if fdate > enddate:
          continue
        stats_files += [stats_file.path]
        continue

      if fdate <= startdate - timedelta(days=1) or fdate > enddate:
        continue
      stats_files += [stats_file.path]

  # sort files by oldest first, not based on the node (default os.scandir)
  stats_files.sort(key=lambda x: x.split('/')[-1])
  print("Number of host stats files to process = ", len(stats_files))

  with multiprocessing.get_context('spawn').Pool(
      processes=archive_thread_count) as archive_pool:
    archive_job = None
    # Process and archive chunk_size files before continuing to process more
    for i in range(int(len(stats_files) / chunk_size) + 1):
      function_time = time.time()
      j = i + 1
      if DEBUG:
        print("Begining Chunk(%s) #%s Processing" % (chunk_size, i))

      ar_file_mapping = {}
      files_to_be_archived = []

      try:
        stats_files[j * chunk_size]
        stats_files_chunk = stats_files[i * chunk_size:j * chunk_size:]
      except IndexError:
        stats_files_chunk = stats_files[i * chunk_size:]

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

      for stats_fname in files_to_be_archived:
        stats_start = open(stats_fname,
                           'r').readlines(8192)  # grab first 8k bytes
        archive_fname = ''
        for line in stats_start:
          if line[0].isdigit():
            t, jid, host = line.split()
            file_date = datetime.fromtimestamp(float(t))
            archive_fname = os.path.join(tgz_archive_dir,
                                         file_date.strftime("%Y-%m-%d.tar.gz"))
            break

        if file_date.date == datetime.today().date:
          continue

        if not archive_fname:
          print("Unable to find first timestamp in %s, skipping archiving" %
                stats_fname)
          continue
        if archive_fname not in ar_file_mapping:
          ar_file_mapping[archive_fname] = []
        ar_file_mapping[archive_fname].append(stats_fname)

      # skip first iteration, on first there will be no archive_job
      if i:
        if DEBUG:
          print("Checking/waiting for background archival proccesses")

        # Wait until last archive_job is complete before starting another one
        for stats_files_archived in archive_job.get():
          print("[{0:.1f}%] completed".format(
              100 * stats_files.index(stats_fname) / len(stats_files)),
                end="\r",
                flush=True)

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
