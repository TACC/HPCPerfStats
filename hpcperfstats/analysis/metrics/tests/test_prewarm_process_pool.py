"""
Regression tests for the update_metrics prewarm process pool.

The Bokeh prewarm does not scale across free-threaded threads (shared
``sys.modules`` / ``_abc`` cache locks, stop-the-world GC), so it runs in
dedicated forkserver worker processes that receive the job's decoded table.
These tests pin the contract without starting real workers: sizing, child
result translation (database errors keep the parent's retry), per-worker
stall and death handling (no neighbour is touched), the free-list bound, the
scheduler's offload site, and the connection-budget term.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path

import pytest
from django.db import DatabaseError

from hpcperfstats.analysis.metrics.lib import prewarm_process_pool as ppp
from hpcperfstats.dbload.lib import conf_parser as cfg

REPO = Path(__file__).resolve().parents[4]
SCHEDULER_SRC = (
  REPO / "hpcperfstats" / "analysis" / "metrics" / "update_metrics.py"
)


class _FakeSlot:
  """Stands in for ``PrewarmWorker``: scripted results, records calls."""

  def __init__(self, script):
    self.script = list(script)
    self.calls = []
    self.stopped = 0

  def run(self, jid, table, timeout_s, max_tasks):
    self.calls.append((jid, table, timeout_s, max_tasks))
    item = self.script.pop(0)
    if isinstance(item, Exception):
      raise item
    return item

  def stop(self):
    self.stopped += 1


def _pool(slots, stall=0.0, recycle=None):
  it = iter(slots)
  return ppp.PrewarmProcessPool(
    len(slots),
    max_tasks_per_child=recycle,
    stall_timeout_s=stall,
    log_fn=lambda s: None,
    worker_factory=lambda: next(it),
  )


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
  # Slots are created eagerly (processes only on first use); keep them fake.
  monkeypatch.setattr(ppp, "PrewarmWorker", lambda ctx: _FakeSlot([]))
  pool = ppp.get_prewarm_process_pool()
  assert pool is ppp.get_prewarm_process_pool()
  assert (pool.processes, pool.max_tasks_per_child, pool.stall_timeout_s) == (
    5,
    None,
    123.0,
  )
  ppp.shutdown_prewarm_process_pool()
  assert ppp._POOL is None
  assert all(slot.stopped == 1 for slot in pool._slots)


def test_run_ships_table_and_returns_child_timings():
  slot = _FakeSlot([{"ok": True, "detail_s": 1.5, "plots_s": 2.5}])
  pool = _pool([slot], stall=900.0, recycle=100)
  sentinel = object()
  out = pool.run("42", sentinel)
  assert out["detail_s"] == 1.5 and out["plots_s"] == 2.5
  assert slot.calls == [("42", sentinel, 900.0, 100)]
  assert pool._free.qsize() == 1  # slot returned to the free list


def test_child_db_error_is_raised_as_database_error_for_retry():
  pool = _pool(
    [
      _FakeSlot(
        [{"ok": False, "db_error": True, "error": "OperationalError: x"}]
      )
    ]
  )
  with pytest.raises(DatabaseError, match="OperationalError"):
    pool.run("1", None)


def test_child_failure_is_prewarm_child_error():
  pool = _pool(
    [_FakeSlot([{"ok": False, "db_error": False, "error": "ValueError: bad"}])]
  )
  with pytest.raises(ppp.PrewarmChildError, match="ValueError"):
    pool.run("1", None)


def test_stall_replaces_only_the_stalled_worker():
  stalled = _FakeSlot([ppp.PrewarmChildError("prewarm worker exceeded 7s")])
  healthy = _FakeSlot([{"ok": True, "detail_s": 1.0, "plots_s": 1.0}])
  pool = _pool([stalled, healthy], stall=7.0)
  # Deterministic checkout order: the stalled slot is first in the free list.
  with pytest.raises(ppp.PrewarmChildError, match="exceeded 7s"):
    pool.run("9", None)
  assert pool.rebuilds == 1
  assert healthy.calls == []  # neighbour untouched
  assert pool._free.qsize() == 2  # both slots available again
  out = pool.run("10", None)
  assert out["ok"] is True
  assert healthy.calls or stalled.calls  # a slot served the next job


def test_free_list_bounds_concurrency():
  release = threading.Event()

  class _Blocking(_FakeSlot):
    def run(self, jid, table, timeout_s, max_tasks):
      release.wait(timeout=10)
      return {"ok": True, "detail_s": 0.0, "plots_s": 0.0}

  pool = _pool([_Blocking([])])
  first = threading.Thread(target=lambda: pool.run("a", None))
  first.start()
  waited = threading.Event()

  def second():
    waited.set()
    pool.run("b", None)

  t2 = threading.Thread(target=second)
  t2.start()
  waited.wait(timeout=5)
  assert pool._free.qsize() == 0  # second caller is blocked on the free list
  release.set()
  first.join(timeout=10)
  t2.join(timeout=10)
  assert pool._free.qsize() == 1


def test_worker_slot_kill_and_recycle_logic(monkeypatch):
  """``PrewarmWorker.run`` restarts dead processes and recycles at max_tasks."""

  class _Proc:
    def __init__(self):
      self.alive = True
      self.killed = 0

    def is_alive(self):
      return self.alive

    def kill(self):
      self.killed += 1
      self.alive = False

    def join(self, timeout=None):
      pass

    def start(self):
      pass

  class _Conn:
    def __init__(self, replies):
      self.sent = []
      self.replies = list(replies)
      self.closed = False

    def send(self, msg):
      self.sent.append(msg)

    def poll(self, timeout=None):
      return bool(self.replies)

    def recv(self):
      return self.replies.pop(0)

    def close(self):
      self.closed = True

  class _Ctx:
    def __init__(self):
      self.procs = []
      self.conns = []

    def Pipe(self, **kwargs):
      del kwargs  # duplex flag irrelevant to the fake
      conn = _Conn([{"ok": True, "detail_s": 0.1, "plots_s": 0.2}])
      self.conns.append(conn)
      return conn, _Conn([])

    def Process(self, **kwargs):
      del kwargs  # target/args/daemon irrelevant to the fake
      proc = _Proc()
      self.procs.append(proc)
      return proc

  ctx = _Ctx()
  slot = ppp.PrewarmWorker(ctx)
  assert slot.run("1", None, 900.0, 1)["ok"] is True
  assert slot.generation == 1 and slot.tasks == 1
  # max_tasks=1 reached: the next run stops the old process and starts a new one.
  assert slot.run("2", None, 900.0, 1)["ok"] is True
  assert slot.generation == 2 and ctx.procs[0].killed == 1
  # A stall kills the current process and raises; the slot restarts next time.
  ctx.conns[-1].replies.clear()
  with pytest.raises(ppp.PrewarmChildError, match="exceeded"):
    slot.run("3", None, 0.01, None)
  assert ctx.procs[1].killed == 1 and slot._proc is None
  assert slot.run("4", None, 900.0, None)["ok"] is True
  assert slot.generation == 3


def test_scheduler_offloads_in_persist_detail_plot_elapsed():
  src = SCHEDULER_SRC.read_text(encoding="utf-8")
  body = src.split("def _persist_detail_plot_elapsed", 1)[1].split(
    "def _run_one", 1
  )[0]
  assert "get_prewarm_process_pool(" in body
  assert "HANDOFF.pop(jid)" in body
  assert re.search(r"pool\.run\(jid, table\)", body)
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
