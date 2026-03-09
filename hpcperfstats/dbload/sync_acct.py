#!/usr/bin/env python3
"""Sync Slurm accounting (sacct) data into job_data. Reads pipe-delimited files, filters restricted queues and existing jobs, and bulk-inserts or falls back to per-row insert.

"""
import os
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
from hpcperfstats.site.machine.models import job_data

local_timezone = cfg.get_local_timezone()


def sync_acct(acct_file, jobs_in_db):
  """Load accounting CSV from acct_file into job_data, skipping jobs already in jobs_in_db and those matching restricted_queue_keywords.

    """
  # Junjie: ensure job name is treated as str.
  data_types = {8: str}

  columns_to_read = [
      'JobID', 'User', 'Account', 'Start', 'End', 'Submit', 'Partition',
      'Timelimit', 'JobName', 'State', 'NNodes', 'ReqCPUS', 'NodeList'
  ]
  # Be tolerant of malformed lines (extra separators etc.); skip them.
  df = read_csv(acct_file, sep='|', engine='python', on_bad_lines='skip')

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
    print("The following jobs are restricted and will be skipped: " +
          str(restricted_job_ids))

  # In case newer slurm gives "None" time for unstarted jobs.  Older slurm prints start_time=end_time=cancelled_time.
  df['start_time'] = df['start_time'].replace('^None$', pd.NA, regex=True)
  df['start_time'] = df['start_time'].replace('^Unknown$', pd.NA, regex=True)
  df['start_time'] = df['start_time'].fillna(df['end_time'])

  df["start_time"] = to_datetime(df["start_time"]).dt.tz_localize(
      local_timezone, ambiguous="NaT", nonexistent="NaT")
  df["end_time"] = to_datetime(df["end_time"]).dt.tz_localize(
      local_timezone, ambiguous="NaT", nonexistent="NaT")
  df["submit_time"] = to_datetime(df["submit_time"]).dt.tz_localize(
      local_timezone, ambiguous="NaT", nonexistent="NaT")

  df["runtime"] = to_timedelta(df["end_time"] -
                               df["start_time"]).dt.total_seconds()
  df["timelimit"] = df["timelimit"].str.replace('-', ' days ')
  df["timelimit"] = to_timedelta(df["timelimit"]).dt.total_seconds()

  df["host_list"] = df["host_list"].apply(hostlist.expand_hostlist)
  df["node_hrs"] = df["nhosts"] * df["runtime"] / 3600.

  print("Total number of new entries:", df.shape[0])

  objs = [
      job_data(
          jid=str(row.jid),
          username=row.username,
          account=row.account if pd.notna(row.account) else None,
          start_time=row.start_time.to_pydatetime(),
          end_time=row.end_time.to_pydatetime(),
          submit_time=row.submit_time.to_pydatetime(),
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
  except Exception as e:
    print("error in bulk_create:", str(e))
    _insert_job_data_individually(df)


def _insert_job_data_individually(df):
  """Fallback: insert job_data rows one by one, skipping duplicates.

    """
  for row in df.itertuples(index=False):
    try:
      job_data(
          jid=str(row.jid),
          username=row.username,
          account=row.account if pd.notna(row.account) else None,
          start_time=row.start_time.to_pydatetime(),
          end_time=row.end_time.to_pydatetime(),
          submit_time=row.submit_time.to_pydatetime(),
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
      print("error in single insert:", str(e), "for jid", row.jid)


if __name__ == "__main__":
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
  print("Jobs found in DB in this date range: %s" % len(jobs_in_db))

  while startdate <= enddate:
    for entry in os.scandir(directory):
      if not entry.is_file():
        continue
      if entry.name.startswith(str(startdate.date())):
        print(entry.path)
        try:
          sync_acct(entry.path, jobs_in_db)
        except Exception as e:
          if settings.DEBUG:
            raise e
          print("Unable to load file: %s" % entry.path)
    startdate += timedelta(days=1)
  print("loading time", time.time() - start)

  # Close DB connections before long sleep to avoid idle connections.
  close_old_connections()
  connections.close_all()
  time.sleep(900)
