"""Unit tests for PopulatePoolController join/restart behavior."""

from types import SimpleNamespace

from hpcperfstats.dbload.lib.sync_timedb_populate_pool import PopulatePoolController


class _DeadProc:
  pid = 7001

  def __init__(self):
    self.joined = False

  def is_alive(self):
    return False

  def join(self, timeout=None):
    del timeout
    self.joined = True


class _AliveProc:
  pid = 7002

  def is_alive(self):
    return True

  def join(self, timeout=None):
    del timeout


def test_populate_pool_stop_joins_dead_processes():
  controller = PopulatePoolController()
  dead = _DeadProc()
  alive = _AliveProc()
  controller._processes = [dead, alive]
  controller._shutdown = SimpleNamespace(set=lambda: None, is_set=lambda: False)
  controller.stop(force=False)
  assert dead.joined is True
  assert controller._processes == []


def test_populate_pool_reap_and_restart_replaces_dead_worker(monkeypatch):
  controller = PopulatePoolController()
  dead = _DeadProc()
  alive = _AliveProc()
  controller._processes = [dead, alive]
  controller._shutdown = SimpleNamespace(is_set=lambda: False)
  controller._ctx = object()
  controller._script_name = "sync_timedb.py"
  controller._registry = {}
  spawned = []

  def _spawn(index):
    proc = _AliveProc()
    proc.pid = 8000 + index
    spawned.append(proc)
    controller._processes.append(proc)
    return proc

  monkeypatch.setattr(controller, "_spawn_one", _spawn)
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser"
      ".get_sync_archive_members_populate_pool_processes",
      lambda: 2,
  )
  restarted = controller.reap_and_restart()
  assert dead.joined is True
  assert restarted == 1
  assert alive in controller._processes
  assert len(controller._processes) == 2
  assert spawned
