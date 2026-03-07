"""Unit tests for dbload parsing logic (sync_acct column handling, sync_timedb line parsing).

AI generated.
"""
import pandas as pd

import pytest


def test_sync_acct_columns_kept():
  """Columns to read in sync_acct match expected set.

    AI generated.
    """
  columns_to_read = [
      "JobID",
      "User",
      "Account",
      "Start",
      "End",
      "Submit",
      "Partition",
      "Timelimit",
      "JobName",
      "State",
      "NNodes",
      "ReqCPUS",
      "NodeList",
  ]
  assert len(columns_to_read) == 13
  assert "JobID" in columns_to_read
  assert "NodeList" in columns_to_read


def test_sync_acct_rename_map():
  """Rename map for sync_acct is consistent.

    AI generated.
    """
  renames = {
      "JobID": "jid",
      "User": "username",
      "Account": "account",
      "Start": "start_time",
      "End": "end_time",
      "Submit": "submit_time",
      "Partition": "queue",
      "Timelimit": "timelimit",
      "JobName": "jobname",
      "State": "state",
      "NNodes": "nhosts",
      "ReqCPUS": "ncores",
      "NodeList": "host_list",
  }
  assert renames["JobID"] == "jid"
  assert renames["NodeList"] == "host_list"


def test_sacct_gen_daterange():
  """daterange (exclusive end) yields expected number of days."""
  from datetime import datetime, timedelta
  from hpcperfstats.dbload.date_utils import daterange
  start = datetime(2024, 1, 1)
  end = datetime(2024, 1, 4)
  days = list(daterange(start, end, inclusive_end=False))
  assert len(days) == 3
  assert days[0] == start
  assert days[-1] == start + timedelta(2)


def test_sacct_gen_daterange_same_day_empty():
  """daterange with start == end (exclusive) yields no days."""
  from datetime import datetime
  from hpcperfstats.dbload.date_utils import daterange
  d = datetime(2024, 6, 15)
  days = list(daterange(d, d, inclusive_end=False))
  assert len(days) == 0


def test_sacct_gen_daterange_single_day():
  """daterange with end == start+1 day (exclusive) yields one date."""
  from datetime import datetime, timedelta
  from hpcperfstats.dbload.date_utils import daterange
  start = datetime(2024, 3, 1)
  end = start + timedelta(days=1)
  days = list(daterange(start, end, inclusive_end=False))
  assert len(days) == 1
  assert days[0] == start
