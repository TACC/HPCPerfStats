"""host_sentinel_summary merge of ``kind=sentinel`` run marks (Postgres).

Run building is covered without a database in
``hpcperfstats/tests/test_host_sentinel_summary.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from hpcperfstats.dbload.lib.monitor_mark_side_ingest import (
  persist_monitor_marks,
)
from hpcperfstats.site.lib.machine.models import host_sentinel_summary


def _mark(start, end, *, code=-2, n=1, total=2, prev=None, host="hostA"):
  payload = ["cpu", "", "user", str(code), repr(float(end)), str(n), str(total)]
  payload.append("" if prev is None else repr(float(prev)))
  return {
    "kind": "sentinel",
    "jids": "\t".join(payload),
    "time": float(start),
    "host": host,
  }


def _rows():
  return [
    (
      r.code,
      r.start_time.timestamp(),
      r.end_time.timestamp(),
      r.device_count,
      r.total_devices,
    )
    for r in host_sentinel_summary.objects.order_by("start_time", "code")
  ]


def test_sentinel_runs_replay_is_idempotent():
  marks = [_mark(100, 110)]
  persist_monitor_marks(marks)
  persist_monitor_marks(marks)
  assert _rows() == [(-2, 100.0, 110.0, 1, 2)]


def test_sentinel_runs_split_across_flushes_merge_into_one_row():
  persist_monitor_marks([_mark(100, 110)])
  persist_monitor_marks([_mark(120, 130, prev=110)])
  assert _rows() == [(-2, 100.0, 130.0, 1, 2)]


def test_sentinel_runs_with_a_sample_between_stay_separate():
  persist_monitor_marks([_mark(100, 110)])
  # The series had a sample at 115 without the code (recovery).
  persist_monitor_marks([_mark(120, 130, prev=115)])
  assert _rows() == [(-2, 100.0, 110.0, 1, 2), (-2, 120.0, 130.0, 1, 2)]


def test_sentinel_runs_device_count_change_is_a_new_row():
  persist_monitor_marks([_mark(100, 110)])
  persist_monitor_marks([_mark(120, 130, n=2, prev=110)])
  assert _rows() == [(-2, 100.0, 110.0, 1, 2), (-2, 120.0, 130.0, 2, 2)]


def test_sentinel_runs_duplicate_start_merges_on_unique_constraint():
  host_sentinel_summary.objects.create(
    host="hostA",
    type="cpu",
    dev="",
    event="user",
    code=-2,
    start_time=datetime.fromtimestamp(100, tz=UTC),
    end_time=datetime.fromtimestamp(100, tz=UTC),
    device_count=1,
    total_devices=2,
  )
  persist_monitor_marks([_mark(100, 150)])
  assert _rows() == [(-2, 100.0, 150.0, 1, 2)]


def test_sentinel_runs_ignore_other_mark_kinds():
  persist_monitor_marks(
    [{"kind": "jobs", "jids": ["1"], "time": 1.0, "host": "h"}]
  )
  assert _rows() == []


def test_sentinel_runs_of_one_call_share_one_bounded_transaction():
  """N runs in one flush cost a fixed number of queries (one locking read,
  one insert, one update), not a transaction and lock per run."""
  from django.db import connection
  from django.test.utils import CaptureQueriesContext

  persist_monitor_marks([_mark(100, 110, host=f"h{i}") for i in range(10)])
  marks = [_mark(120, 130, prev=110, host=f"h{i}") for i in range(10)]
  marks += [_mark(100, 110, host=f"n{i}") for i in range(30)]
  with CaptureQueriesContext(connection) as ctx:
    persist_monitor_marks(marks)
  sql = [q["sql"].lstrip().upper() for q in ctx.captured_queries]
  assert sum(s.startswith("SELECT") for s in sql) == 1, sql
  assert sum(s.startswith("INSERT") for s in sql) == 1, sql
  assert sum(s.startswith("UPDATE") for s in sql) == 1, sql
  assert len(sql) <= 6, sql
  rows = sorted(
    (r.host, r.start_time.timestamp(), r.end_time.timestamp())
    for r in host_sentinel_summary.objects.all()
  )
  assert rows == sorted(
    [(f"h{i}", 100.0, 130.0) for i in range(10)]
    + [(f"n{i}", 100.0, 110.0) for i in range(30)]
  )


def test_sentinel_runs_chained_within_one_call_merge_into_one_row():
  persist_monitor_marks([_mark(100, 110), _mark(120, 130, prev=110)])
  assert _rows() == [(-2, 100.0, 130.0, 1, 2)]


def test_sentinel_run_already_covered_issues_no_write():
  """A replayed or re-delivered run inside the stored one writes nothing."""
  from django.db import connection
  from django.test.utils import CaptureQueriesContext

  persist_monitor_marks([_mark(100, 130)])
  with CaptureQueriesContext(connection) as ctx:
    persist_monitor_marks([_mark(110, 120, prev=100), _mark(100, 130)])
  sql = [q["sql"].lstrip().upper() for q in ctx.captured_queries]
  assert not any(s.startswith(("INSERT", "UPDATE")) for s in sql), sql
  assert _rows() == [(-2, 100.0, 130.0, 1, 2)]
