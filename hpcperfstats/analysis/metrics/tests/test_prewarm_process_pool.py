"""
Regression tests for the update_metrics prewarm process pool.

The Bokeh prewarm does not scale across free-threaded threads (shared
``sys.modules`` / ``_abc`` cache locks, stop-the-world GC), so it runs in
forkserver worker processes that receive the job's decoded table. These tests
pin the contract without starting real workers: sizing, child result
translation (database errors keep the parent's retry), stall rebuild, the
scheduler's offload site, and the connection-budget term.
"""

from __future__ import annotations

import concurrent.futures
import re
from pathlib import Path

import pytest
from django.db import DatabaseError

from hpcperfstats.analysis.metrics.lib import prewarm_process_pool as ppp
from hpcperfstats.dbload.lib import conf_parser as cfg

REPO = Path(__file__).resolve().parents[4]
SCHEDULER_SRC = (
  REPO / "hpcperfstats" / "analysis" / "metrics" / "update_metrics.py"
)


class _FakeFuture:
  def __init__(self, result=None, exc=None):
    self._result = result
    self._exc = exc

  def result(self, timeout=None):
    if self._exc is not None:
      raise self._exc
    return self._result


class _FakeExecutor:
  def __init__(self, future):
    self.future = future
    self.submitted = []
    self.shutdown_calls = []
    self._processes = {}

  def submit(self, fn, *args):
    self.submitted.append((fn, args))
    return self.future

  def shutdown(self, wait=True, cancel_futures=False):
    self.shutdown_calls.append((wait, cancel_futures))


def _pool_with(future, monkeypatch, stall=0.0):
  pool = ppp.PrewarmProcessPool(
    2, max_tasks_per_child=None, stall_timeout_s=stall, log_fn=lambda s: None
  )
  fake = _FakeExecutor(future)
  monkeypatch.setattr(pool, "_ensure", lambda: fake)
  return pool, fake


def test_prewarm_processes_auto_tracks_metrics_pool(monkeypatch):
  monkeypatch.setattr(cfg, "_ensure_cfg_loaded", lambda: None)
  monkeypatch.setattr(cfg, "get_metrics_pool_processes", lambda: 24)
  monkeypatch.setattr(cfg, "_pipeline_get", lambda key: "auto")
  assert cfg.get_metrics_prewarm_processes() == 24
  monkeypatch.setattr(cfg, "_pipeline_get", lambda key: "0")
  assert cfg.get_metrics_prewarm_processes() == 0
  monkeypatch.setattr(cfg, "_pipeline_get", lambda key: "12")
  assert cfg.get_metrics_prewarm_processes() == 12
  monkeypatch.setattr(cfg, "_pipeline_get", lambda key: "garbage")
  assert cfg.get_metrics_prewarm_processes() == 24


def test_get_pool_is_none_when_disabled(monkeypatch):
  monkeypatch.setattr(cfg, "get_metrics_prewarm_processes", lambda: 0)
  monkeypatch.setattr(ppp, "_POOL", None)
  assert ppp.get_prewarm_process_pool() is None


def test_get_pool_is_singleton_sized_from_config(monkeypatch):
  monkeypatch.setattr(cfg, "get_metrics_prewarm_processes", lambda: 5)
  monkeypatch.setattr(cfg, "get_metrics_prewarm_max_tasks_per_child", lambda: 0)
  monkeypatch.setattr(cfg, "get_metrics_run_stall_timeout_s", lambda: 123.0)
  monkeypatch.setattr(ppp, "_POOL", None)
  pool = ppp.get_prewarm_process_pool()
  assert pool is ppp.get_prewarm_process_pool()
  assert (pool.processes, pool.max_tasks_per_child, pool.stall_timeout_s) == (
    5,
    None,
    123.0,
  )
  ppp.shutdown_prewarm_process_pool()
  assert ppp._POOL is None


def test_run_ships_table_and_returns_child_timings(monkeypatch):
  pool, fake = _pool_with(
    _FakeFuture({"ok": True, "detail_s": 1.5, "plots_s": 2.5}), monkeypatch
  )
  sentinel = object()
  out = pool.run("42", sentinel)
  assert out["detail_s"] == 1.5 and out["plots_s"] == 2.5
  assert fake.submitted == [(ppp.run_prewarm_in_child, ("42", sentinel))]


def test_child_db_error_is_raised_as_database_error_for_retry(monkeypatch):
  pool, _ = _pool_with(
    _FakeFuture(
      {"ok": False, "db_error": True, "error": "OperationalError: x"}
    ),
    monkeypatch,
  )
  with pytest.raises(DatabaseError, match="OperationalError"):
    pool.run("1", None)


def test_child_failure_is_prewarm_child_error(monkeypatch):
  pool, _ = _pool_with(
    _FakeFuture({"ok": False, "db_error": False, "error": "ValueError: bad"}),
    monkeypatch,
  )
  with pytest.raises(ppp.PrewarmChildError, match="ValueError"):
    pool.run("1", None)


def test_stall_rebuilds_pool_and_fails_job(monkeypatch):
  pool, fake = _pool_with(
    _FakeFuture(exc=concurrent.futures.TimeoutError()), monkeypatch, stall=7.0
  )
  pool._executor = fake  # the live executor the rebuild must drop
  with pytest.raises(ppp.PrewarmChildError, match="exceeded 7s"):
    pool.run("9", None)
  assert pool._executor is None
  assert pool.generation == 1
  assert fake.shutdown_calls == [(False, True)]


def test_broken_pool_rebuilds(monkeypatch):
  pool, fake = _pool_with(
    _FakeFuture(exc=concurrent.futures.process.BrokenProcessPool("dead")),
    monkeypatch,
  )
  pool._executor = fake
  with pytest.raises(ppp.PrewarmChildError, match="broken"):
    pool.run("9", None)
  assert pool.generation == 1


def test_scheduler_offloads_in_persist_detail_plot_elapsed():
  src = SCHEDULER_SRC.read_text(encoding="utf-8")
  body = src.split("def _persist_detail_plot_elapsed", 1)[1].split(
    "def _run_one", 1
  )[0]
  assert "get_prewarm_process_pool(" in body
  assert "HANDOFF.pop(jid)" in body
  assert re.search(r"pool\.run\(jid, table\)", body)
  # Shutdown path kills workers before the DB connections are closed.
  shutdown = src.split("def _shutdown_db_best_effort", 1)[1].split("\ndef ", 1)[
    0
  ]
  assert shutdown.find("shutdown_prewarm_process_pool()") < shutdown.find(
    "connections.close_all()"
  )


def test_child_initializer_installs_shim_before_django():
  import inspect

  body = inspect.getsource(ppp._child_initializer)
  assert body.find("bokeh_validation_patch") < body.find("ensure_django()")
  assert body.find("apply_pool_worker_process_title") < body.find(
    "bokeh_validation_patch"
  )
