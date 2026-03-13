#!/usr/bin/env python3
"""Sync Slurm accounting (sacct) data into job_data. Reads pipe-delimited files, filters restricted queues and existing jobs, and bulk-inserts or falls back to per-row insert.

"""
import io
import os
import signal
import sys
import time
from datetime import datetime, timedelta
from hpcperfstats.django_bootstrap import ensure_django
ensure_django()

import hostlist
import pandas as pd
from django.conf import settings
from django.db import IntegrityError, close_old_connections, connections
from pandas import read_csv, to_datetime, to_timedelta

import hpcperfstats.conf_parser as cfg
from hpcperfstats.dbload.date_utils import log_date_range, parse_start_end_dates
from hpcperfstats.print_utils import log_print
from hpcperfstats.site.machine.models import job_data

local_timezone = cfg.get_local_timezone()

_shutdown_requested = False


def _handle_sigterm(signum, frame):
  global _shutdown_requested
  _shutdown_requested = True
  log_print("Received SIGTERM, will exit after current work")


def _to_pydatetime_or_none(ts):
  """Convert pandas Timestamp/NaT to Python datetime or None."""
  if pd.isna(ts):
    return None
  return ts.to_pydatetime()


COLUMNS_TO_READ = [
    'JobID', 'User', 'Account', 'Start', 'End', 'Submit', 'Partition',
    'Timelimit', 'JobName', 'State', 'NNodes', 'ReqCPUS', 'NodeList'
]


def sync_acct_from_content(content, jobs_in_db):
  """Load accounting data from pipe-delimited string into job_data.

  Same logic as sync_acct but accepts raw sacct output (e.g. from API or
  subprocess). Returns the number of new job_data rows inserted.
  """
  if isinstance(content, bytes):
    content = content.decode("utf-8", errors="replace")
  if not content.strip():
    return 0
  df = read_csv(io.StringIO(content), sep='|', engine='python', on_bad_lines='skip')
  return _sync_acct_dataframe(df, jobs_in_db)


def sync_acct(acct_file, jobs_in_db):
  """Load accounting CSV from acct_file into job_data, skipping jobs already in jobs_in_db and those matching restricted_queue_keywords.

    """
  with open(acct_file, "r", encoding="utf-8", errors="replace") as f:
    return sync_acct_from_content(f.read(), jobs_in_db)


def _sync_acct_dataframe(df, jobs_in_db):
  """Apply column filter, renames, filters, and insert into job_data. Returns count of new entries."""
  columns_to_read = COLUMNS_TO_READ
  # cycle through collumns so we can remove those we don't want to import.
  for c in df:
    if c in columns_to_read:
      continue
    df = df.drop(columns=c)

  df = df.rename(
      columns={
          'JobID': 'jid',
          'User': 'username',
          'Account': 'account',
          'Start': 'start_time',
          'End': 'end_time',
          'Submit': 'submit_time',
          'Partition': 'queue',
          'Timelimit': 'timelimit',
          'JobName': 'jobname',
          'State': 'state',
          'NNodes': 'nhosts',
          'ReqCPUS': 'ncores',
          'NodeList': 'host_list'
      })

  df = df[~df["jid"].isin(jobs_in_db)]
  df["jid"] = df["jid"].apply(str)

  restricted_queue_keywords = cfg.get_restricted_queue_keywords()

  df_len = len(df)
  queue_col_index = df.columns.get_loc("queue")
  job_id_col_index = df.columns.get_loc("jid")

  restricted_job_ids = []

  restricted_df_indices = []

  for i in range(df_len):
    for q in restricted_queue_keywords:
      if q in df.iloc[i, queue_col_index]:
        if settings.DEBUG:
          restricted_job_ids.append(df.iloc[i, job_id_col_index])
          restricted_df_indices.append(i)

  if restricted_df_indices:
    # restricted_df_indices are positional; convert to index labels before dropping
    df = df.drop(index=df.index[restricted_df_indices])

  if len(restricted_job_ids) > 0:
    log_print("The following jobs are restricted and will be skipped: " +
          str(restricted_job_ids))

  # In case newer slurm gives "None" time for unstarted jobs.  Older slurm prints start_time=end_time=cancelled_time.
  df['start_time'] = df['start_time'].replace('^None$', pd.NA, regex=True)
  df['start_time'] = df['start_time'].replace('^Unknown$', pd.NA, regex=True)
  df['start_time'] = df['start_time'].fillna(df['end_time'])

  df["start_time"] = to_datetime(df["start_time"]).dt.tz_localize(
      local_timezone, ambiguous=False, nonexistent="shift_forward")
  df["end_time"] = to_datetime(df["end_time"]).dt.tz_localize(
      local_timezone, ambiguous=False, nonexistent="shift_forward")
  df["submit_time"] = to_datetime(df["submit_time"]).dt.tz_localize(
      local_timezone, ambiguous=False, nonexistent="shift_forward")

  df["runtime"] = to_timedelta(df["end_time"] -
                               df["start_time"]).dt.total_seconds()
  df["timelimit"] = df["timelimit"].str.replace('-', ' days ')
  df["timelimit"] = to_timedelta(df["timelimit"]).dt.total_seconds()

  df["host_list"] = df["host_list"].apply(hostlist.expand_hostlist)
  df["node_hrs"] = df["nhosts"] * df["runtime"] / 3600.

  n_new = df.shape[0]
  log_print("Total number of new entries:", n_new)

  objs = [
      job_data(
          jid=str(row.jid),
          username=row.username,
          account=row.account if pd.notna(row.account) else None,
          start_time=_to_pydatetime_or_none(row.start_time),
          end_time=_to_pydatetime_or_none(row.end_time),
          submit_time=_to_pydatetime_or_none(row.submit_time),
          queue=row.queue if pd.notna(row.queue) else None,
          timelimit=float(row.timelimit) if pd.notna(row.timelimit) else None,
          jobname=str(row.jobname) if pd.notna(row.jobname) else None,
          state=row.state if pd.notna(row.state) else None,
          nhosts=int(row.nhosts) if pd.notna(row.nhosts) else None,
          ncores=int(row.ncores) if pd.notna(row.ncores) else None,
          host_list=list(row.host_list) if row.host_list else [],
          runtime=float(row.runtime) if pd.notna(row.runtime) else None,
          node_hrs=float(row.node_hrs) if pd.notna(row.node_hrs) else None,
      ) for row in df.itertuples(index=False)
  ]
  try:
    job_data.objects.bulk_create(objs)
    return n_new
  except Exception as e:
    log_print("error in bulk_create:", str(e))
    _insert_job_data_individually(df)
    return n_new


def _insert_job_data_individually(df):
  """Fallback: insert job_data rows one by one, skipping duplicates.

    """
  for row in df.itertuples(index=False):
    try:
      job_data(
          jid=str(row.jid),
          username=row.username,
          account=row.account if pd.notna(row.account) else None,
          start_time=_to_pydatetime_or_none(row.start_time),
          end_time=_to_pydatetime_or_none(row.end_time),
          submit_time=_to_pydatetime_or_none(row.submit_time),
          queue=row.queue if pd.notna(row.queue) else None,
          timelimit=float(row.timelimit) if pd.notna(row.timelimit) else None,
          jobname=str(row.jobname) if pd.notna(row.jobname) else None,
          state=row.state if pd.notna(row.state) else None,
          nhosts=int(row.nhosts) if pd.notna(row.nhosts) else None,
          ncores=int(row.ncores) if pd.notna(row.ncores) else None,
          host_list=list(row.host_list) if row.host_list else [],
          runtime=float(row.runtime) if pd.notna(row.runtime) else None,
          node_hrs=float(row.node_hrs) if pd.notna(row.node_hrs) else None,
      ).save()
    except IntegrityError:
      pass  # skip duplicate jid
    except Exception as e:
      log_print("error in single insert:", str(e), "for jid", row.jid)


def _sleep_until_shutdown(seconds):
  """Sleep for up to seconds, returning early if SIGTERM was received."""
  global _shutdown_requested
  interval = 5
  elapsed = 0
  while elapsed < seconds and not _shutdown_requested:
    time.sleep(min(interval, seconds - elapsed))
    elapsed += interval


if __name__ == "__main__":
  signal.signal(signal.SIGTERM, _handle_sigterm)
  #    while True:

  #################################################################
  default_start = datetime.combine(datetime.today(), datetime.min.time())
  default_end = default_start + timedelta(days=1)
  startdate, enddate = parse_start_end_dates(sys.argv, default_start, default_end)

  log_date_range("job files to ingest", startdate, enddate)
  #################################################################

  # Parse and convert raw stats files to pandas dataframe
  start = time.time()
  directory = cfg.get_accounting_path()

  searchdate = startdate - timedelta(days=2)
  jobs_in_db = set(
      job_data.objects.filter(end_time__date__gte=searchdate)
      .values_list("jid", flat=True)
      .iterator(chunk_size=10000)
  )
  log_print("Jobs found in DB in this date range: %s" % len(jobs_in_db))

  while startdate <= enddate and not _shutdown_requested:
    for entry in os.scandir(directory):
      if _shutdown_requested:
        break
      if not entry.is_file():
        continue
      if entry.name.startswith(str(startdate.date())):
        log_print(entry.path)
        try:
          sync_acct(entry.path, jobs_in_db)
        except Exception as e:
          if settings.DEBUG:
            raise e
          log_print("Unable to load file: %s" % entry.path)
    startdate += timedelta(days=1)
  log_print("loading time", time.time() - start)

  if _shutdown_requested:
    log_print("Exiting due to SIGTERM")
    sys.exit(143)

  # Close DB connections before long sleep to avoid idle connections.
  close_old_connections()
  connections.close_all()
  _sleep_until_shutdown(900)
  if _shutdown_requested:
    sys.exit(143)
