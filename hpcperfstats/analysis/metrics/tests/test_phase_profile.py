"""Unit tests for the opt-in metrics phase profiler."""

import threading
import time

import pytest

from hpcperfstats.analysis.metrics.lib import phase_profile as pp


@pytest.fixture(autouse=True)
def _reset_records():
  pp.drain_records()
  yield
  pp.drain_records()


def test_disabled_scope_is_noop(monkeypatch):
  monkeypatch.delenv(pp.ENV_FLAG, raising=False)
  with pp.job_scope("1", "metrics") as rec:
    with pp.phase("db_fetch"):
      pass
  assert rec is None
  assert pp.drain_records() == []


def test_nested_phases_charge_exclusive_time(monkeypatch):
  monkeypatch.setenv(pp.ENV_FLAG, "1")
  with pp.job_scope("42", "metrics") as rec:
    with pp.phase("metrics_compute"):
      time.sleep(0.02)
      with pp.phase("db_fetch"):
        time.sleep(0.03)
      with pp.phase("db_fetch"):
        time.sleep(0.01)
  records = pp.drain_records()
  assert len(records) == 1
  phases = records[0]["phases"]
  assert records[0]["jid"] == "42" and records[0]["stage"] == "metrics"
  assert phases["db_fetch"] >= 0.04
  assert 0.015 <= phases["metrics_compute"] < 0.045
  assert sum(phases.values()) <= records[0]["wall_s"] + 1e-6
  assert "stack" not in records[0]
  assert rec is records[0]


def test_inner_job_scope_keeps_outer_record(monkeypatch):
  monkeypatch.setenv(pp.ENV_FLAG, "1")
  with (
    pp.job_scope("a", "prewarm") as outer,
    pp.job_scope("a", "prewarm") as inner,
  ):
    assert inner is None
    with pp.phase("bokeh"):
      pass
  records = pp.drain_records()
  assert len(records) == 1 and records[0] is outer
  assert records[0]["phases"]["bokeh"] >= 0.0


def test_records_are_per_thread(monkeypatch):
  monkeypatch.setenv(pp.ENV_FLAG, "1")

  def worker(jid):
    with pp.job_scope(jid, "metrics"):
      with pp.phase("persist"):
        time.sleep(0.01)

  threads = [threading.Thread(target=worker, args=(str(i),)) for i in range(4)]
  for t in threads:
    t.start()
  for t in threads:
    t.join()
  records = pp.drain_records()
  assert sorted(r["jid"] for r in records) == ["0", "1", "2", "3"]
  assert all(r["phases"]["persist"] >= 0.009 for r in records)


def test_merge_and_replaceable_fraction():
  merged = pp.merge_records(
    [
      {
        "jid": "9",
        "stage": "metrics",
        "wall_s": 4.0,
        "phases": {"db_fetch": 1.0, "metrics_compute": 2.0, "persist": 1.0},
      },
      {
        "jid": "9",
        "stage": "prewarm",
        "wall_s": 6.0,
        "phases": {"plot_series": 2.0, "bokeh": 3.0, "persist": 1.0},
      },
    ]
  )
  row = merged["9"]
  assert row["stages"] == ["metrics", "prewarm"]
  assert row["wall_s"] == 10.0
  assert pp.replaceable_fraction(row) == pytest.approx(0.5)
  assert pp.replaceable_fraction({"wall_s": 0.0, "phases": {}}) == 0.0
