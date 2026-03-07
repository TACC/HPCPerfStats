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
from pandas import DataFrame, to_datetime

import hpcperfstats.conf_parser as cfg
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

amd64_pmc_eventmap = {
    0x43ff03: "FLOPS,W=48",
    0x4300c2: "BRANCH_INST_RETIRED,W=48",
    0x4300c3: "BRANCH_INST_RETIRED_MISS,W=48",
    0x4308af: "DISPATCH_STALL_CYCLES1,W=48",
    0x43ffae: "DISPATCH_STALL_CYCLES0,W=48"
}

amd64_df_eventmap = {
    0x403807: "MBW_CHANNEL_0,W=48,U=64B",
    0x403847: "MBW_CHANNEL_1,W=48,U=64B",
    0x403887: "MBW_CHANNEL_2,W=48,U=64B",
    0x4038c7: "MBW_CHANNEL_3,W=48,U=64B",
    0x433907: "MBW_CHANNEL_4,W=48,U=64B",
    0x433947: "MBW_CHANNEL_5,W=48,U=64B",
    0x433987: "MBW_CHANNEL_6,W=48,U=64B",
    0x4339c7: "MBW_CHANNEL_7,W=48,U=64B"
}

intel_8pmc3_eventmap = {
    0x4301c7: 'FP_ARITH_INST_RETIRED_SCALAR_DOUBLE,W=48,U=1',
    0x4302c7: 'FP_ARITH_INST_RETIRED_SCALAR_SINGLE,W=48,U=1',
    0x4304c7: 'FP_ARITH_INST_RETIRED_128B_PACKED_DOUBLE,W=48,U=2',
    0x4308c7: 'FP_ARITH_INST_RETIRED_128B_PACKED_SINGLE,W=48,U=4',
    0x4310c7: 'FP_ARITH_INST_RETIRED_256B_PACKED_DOUBLE,W=48,U=4',
    0x4320c7: 'FP_ARITH_INST_RETIRED_256B_PACKED_SINGLE,W=48,U=8',
    0x4340c7: 'FP_ARITH_INST_RETIRED_512B_PACKED_DOUBLE,W=48,U=8',
    0x4380c7: 'FP_ARITH_INST_RETIRED_512B_PACKED_SINGLE,W=48,U=16',
    "FIXED_CTR0": 'INST_RETIRED,W=48',
    "FIXED_CTR1": 'APERF,W=48',
    "FIXED_CTR2": 'MPERF,W=48'
}

intel_skx_imc_eventmap = {
    0x400304: "CAS_READS,W=48",
    0x400c04: "CAS_WRITES,W=48",
    0x400b01: "ACT_COUNT,W=48",
    0x400102: "PRE_COUNT_MISS,W=48"
}

exclude_types = [
    "ib", "ib_sw", "intel_skx_cha", "ps", "sysv_shm", "tmpfs", "vfs"
]

#exclude_types = ["ib", "ib_sw", "intel_skx_cha", "proc", "ps", "sysv_shm", "tmpfs", "vfs"]


# This routine will read the file until a timestamp is read that is not in the database. It then reads in the rest of the file.
def add_stats_file_to_db(lock, stats_file, stats_file_contents=None):
  """Parse a stats file, map hardware counters, compute deltas/arc, and bulk-insert into host_data and proc_data. Returns (stats_file, need_archival). Uses lock for DB writes.

    AI generated.
    """
  # Ensure this process/thread uses a fresh DB connection (thread-safe for multiprocessing/threaded workers).
  close_old_connections()

  hostname, create_time = stats_file.split('/')[-2:]

  if stats_file_contents is not None:
    lines = stats_file_contents
  else:
    try:
      with open(stats_file, 'r') as fd:
        lines = fd.readlines()
    except FileNotFoundError:
      print("Stats file disappeared: %s" % stats_file)
      return ((stats_file, False))

  for l in lines:
    if not l:
      continue
    try:
      if l[0].isdigit():
        t, jid, host = l.split()
        break
    except:
      print("Error on this line: %s" % l)
  else:
    print("initial timestamp not found")

  timestamp_utc = datetime.fromtimestamp(int(float(t)), tz=timezone.utc)
  ts_low = timestamp_utc - timedelta(hours=48)
  ts_high = timestamp_utc + timedelta(hours=72)
  times_qs = host_data.objects.filter(host=hostname,
                                      time__gte=ts_low,
                                      time__lt=ts_high).values_list(
                                          "time",
                                          flat=True).distinct().order_by("time")
  times = [float(t.timestamp()) for t in times_qs]
  itimes = [int(t) for t in times]

  # start reading stats data from file at first - 1 missing time
  start_idx = -1
  last_idx = 0
  need_archival = True
  for i, line in enumerate(lines):
    if not line or not line[0]:
      continue
    if line[0].isdigit():
      t, jid, host = line.split()
      if jid == '-':
        continue

      if (float(t) not in times) and (int(float(t)) not in itimes):
        start_idx = last_idx
        need_archival = False
        break
      last_idx = i

  if start_idx == -1:
    print("No missing timestamps found for %s" % stats_file)
    return ((stats_file, True))

  # instrument the code to see what is actually proccessing in each file
  timestamps_found = 0
  counters_found = 0
  labels_found = 0
  unprocessable_lines = 0
  jobs_missing_found = 0

  schema = {}
  stats = []
  proc_stats = []  # process stats
  insert = False
  timestamp_job_missing = False
  start = time.time()
  try:
    for i, line in enumerate(lines):
      if not line or not line[0]:
        continue

      if line[0].isalpha() and insert:
        # Skip any data from a time stamp that doesn't have a jid associated
        if timestamp_job_missing:
          continue
        typ, dev, vals = line.split(maxsplit=2)
        counters_found += 1
        vals = vals.split()
        if typ in exclude_types:
          continue

        # Mapping hardware counters to events
        if typ == "amd64_pmc" or typ == "amd64_df" or typ == "intel_8pmc3" or typ == "intel_skx_imc":
          if typ == "amd64_pmc":
            eventmap = amd64_pmc_eventmap
          if typ == "amd64_df":
            eventmap = amd64_df_eventmap
          if typ == "intel_8pmc3":
            eventmap = intel_8pmc3_eventmap
          if typ == "intel_skx_imc":
            eventmap = intel_skx_imc_eventmap
          n = {}
          rm_idx = []
          schema_mod = [] * len(schema[typ])

          for idx, eve in enumerate(schema[typ]):
            eve = eve.split(',')[0]
            if "CTL" in eve:
              try:
                n[eve.lstrip("CTL")] = eventmap[int(vals[idx])]
              except Exception:
                n[eve.lstrip("CTL")] = "OTHER"
              rm_idx += [idx]

            elif "FIXED_CTR" in eve:
              schema_mod += [eventmap[eve]]

            elif "CTR" in eve:
              schema_mod += [n[eve.lstrip("CTR")]]
            else:
              schema_mod += [eve]

          for idx in sorted(rm_idx, reverse=True):
            del vals[idx]
          vals = dict(zip(schema_mod, vals))
        elif typ == "proc":
          proc_name = (line.split()[1]).split('/')[0]
          proc_stats += [{**tags2, "proc": proc_name}]
          continue
        else:
          # Software counters are not programmable and do not require mapping
          vals = dict(zip(schema[typ], vals))

        rec = {**tags, "type": typ, "dev": dev}

        for eve, val in vals.items():
          eve = eve.split(',')
          width = 64
          mult = 1
          unit = "#"

          for ele in eve[1:]:
            if "W=" in ele:
              width = int(ele.lstrip("W="))
            if "U=" in ele:
              ele = ele.lstrip("U=")
              try:
                mult = float(''.join(filter(str.isdigit, ele)))
              except Exception:
                pass
              try:
                unit = ''.join(filter(str.isalpha, ele))
              except Exception:
                pass

          stats += [{
              **rec, "event": eve[0],
              "value": float(val),
              "wid": width,
              "mult": mult,
              "unit": unit
          }]

      elif i >= start_idx and line[0].isdigit():
        t, jid, host = line.split()
        if jid == '-':
          timestamp_job_missing = True
          jobs_missing_found += 1
          continue
        timestamp_job_missing = False
        timestamps_found += 1
        insert = True
        tags = {"time": float(t), "host": host, "jid": jid}
        tags2 = {"jid": jid, "host": host}
      elif line[0] == '!':
        label, events = line.split(maxsplit=1)
        labels_found += 1
        typ, events = label[1:], events.split()
        schema[typ] = events
      else:
        unprocessable_lines += 1

  except Exception as e:
    print("error: process data failed: ", str(e))
    print("Possibly corrupt file: %s" % stats_file)
    return ((stats_file, False))

  unique_entries = set(tuple(d.items()) for d in proc_stats)

  # Convert set of tuples back to a list of dictionaries
  proc_stats = [dict(entry) for entry in unique_entries]
  proc_stats = DataFrame.from_records(proc_stats)

  stats = DataFrame.from_records(stats)

  if DEBUG:
    print(
        "File Stats for %s:\n %s labels found, %s timestamps found,  %s counters found, %s unprocessable lines, %s timestamps missing jids"
        % (stats_file, labels_found, timestamps_found, counters_found,
           unprocessable_lines, jobs_missing_found))

  if stats.empty and proc_stats.empty:
    if DEBUG:
      print("Unable to process stats file %s" % stats_file)
    return ((stats_file, False))

  # Always drop the first timestamp. For new file this is just first timestamp (at random rotate time).
  # For update from existing file this is timestamp already in database.

  # compute difference between time adjacent stats. if new file first na time diff is backfilled by second time diff
  stats["delta"] = (stats.groupby(["host", "type", "dev",
                                   "event"])["value"].diff())

  # correct stats for rollover and units (must be done before aggregation over devices)
  stats["delta"] = stats["delta"].mask(stats["delta"] < 0,
                                       2**stats["wid"] + stats["delta"])
  stats["delta"] = stats["delta"] * stats["mult"]
  del stats["wid"], stats["mult"]

  # aggregate over devices
  stats = stats.groupby(["host", "jid", "type", "event", "unit",
                         "time"]).sum().reset_index()
  stats = stats.sort_values(by=["host", "type", "event", "time"])

  # compute average rate of change.
  deltat = stats.groupby(["host", "type", "event"])["time"].diff()
  stats["arc"] = stats["delta"] / deltat
  stats["time"] = to_datetime(stats["time"], unit='s').dt.tz_localize('UTC')

  # drop rows from first timestamp
  stats = stats.dropna()
  print("processing time for {0} {1:.1f}s".format(stats_file,
                                                  time.time() - start))

  # bulk insertion using Django ORM
  lock.acquire()
  try:
    proc_objs = [
        proc_data(jid=row.jid, host=row.host, proc=row.proc)
        for row in proc_stats.itertuples(index=False)
    ]
    proc_data.objects.bulk_create(proc_objs, ignore_conflicts=True)
  except Exception as e:
    if DEBUG:
      print("error in proc_data bulk_create: %s\nFile %s" % (e, stats_file))
    lock.release()
    _insert_proc_data_individually(proc_stats)
  else:
    lock.release()

  lock.acquire()
  need_archival = True
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
        ) for row in stats.itertuples(index=False)
    ]
    host_data.objects.bulk_create(host_objs, ignore_conflicts=True)
  except Exception as e:
    if DEBUG:
      print("error in host_data bulk_create:", str(e))
    lock.release()
    need_archival = _insert_host_data_individually(stats)
  else:
    lock.release()

  need_archival = True
  if DEBUG:
    print("File successfully added to DB")
  return ((stats_file, need_archival))


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
