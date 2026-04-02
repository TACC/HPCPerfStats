"""Tests for dbload ORM row builders."""
from __future__ import annotations

import pandas as pd
import pytest
from types import SimpleNamespace

from hpcperfstats.dbload.io_helpers import (
    host_data_instance_from_stats_row,
    job_data_instance_from_acct_row,
)


def test_host_data_instance_from_stats_row_maps_fields():
  ts = pd.Timestamp("2020-06-01 12:00:00")
  row = SimpleNamespace(
      time=ts,
      host="n.example.com",
      type="cpu",
      event="cycles",
      unit="count",
      value=1.5,
      delta=0.25,
      arc=100.0,
  )
  h = host_data_instance_from_stats_row(row)
  assert h.time == ts.to_pydatetime()
  assert h.host == "n.example.com"
  assert h.type == "cpu"
  assert h.dev is None
  assert h.event == "cycles"
  assert h.unit == "count"
  assert h.value == 1.5
  assert h.delta == 0.25
  assert h.arc == 100.0


def test_job_data_instance_from_acct_row_maps_fields():
  row = SimpleNamespace(
      jid="42",
      username="alice",
      account=pd.NA,
      start_time=pd.Timestamp("2020-01-01 10:00:00"),
      end_time=pd.Timestamp("2020-01-01 11:00:00"),
      submit_time=pd.Timestamp("2020-01-01 09:00:00"),
      queue="batch",
      timelimit=3600.0,
      jobname="j1",
      state="COMPLETED",
      nhosts=2,
      ncores=64,
      host_list=["a", "b"],
      runtime=3600.0,
      node_hrs=2.0,
  )
  j = job_data_instance_from_acct_row(row)
  assert j.jid == "42"
  assert j.username == "alice"
  assert j.account is None
  assert j.queue == "batch"
  assert j.host_list == ["a", "b"]
  assert j.nhosts == 2
  assert j.ncores == 64
