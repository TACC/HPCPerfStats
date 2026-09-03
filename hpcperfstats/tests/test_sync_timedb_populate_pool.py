"""Unit tests for PopulatePoolController thread-pool join/restart behavior."""

from types import SimpleNamespace

from hpcperfstats.dbload.lib.sync_timedb_populate_pool import PopulatePoolController


class _ReadyResult:
  def ready(self):
    return True


class _LiveResult:
  def ready(self):
    return False


class _FakePool:
  def __init__(self):
    self.terminated = False
    self.joined = False
    self.submitted = []

  def apply_async(self, fn, args=(), kwds=None):
    del kwds
    self.submitted.append((fn, args))
    return _LiveResult()

  def terminate(self):
    self.terminated = True

  def join(self):
    self.joined = True


def test_populate_pool_stop_joins_dead_processes():
  controller = PopulatePoolController()
  pool = _FakePool()
  controller._pool = pool
  controller._shutdown = SimpleNamespace(set=lambda: None, is_set=lambda: False)
  controller._results = [_LiveResult()]
  controller.stop(force=False)
  assert pool.terminated is True
  assert pool.joined is True
  assert controller._results == []
  assert controller._pool is None


def test_populate_pool_reap_and_restart_replaces_dead_worker(monkeypatch):
  controller = PopulatePoolController()
  pool = _FakePool()
  controller._pool = pool
  controller._shutdown = SimpleNamespace(is_set=lambda: False)
  controller._script_name = "sync_timedb.py"
  controller._registry = {}
  controller._results = [_ReadyResult(), _LiveResult()]
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser"
      ".get_sync_archive_members_populate_pool_processes",
      lambda: 2,
  )
  restarted = controller.reap_and_restart()
  assert restarted == 1
  assert len(controller._results) == 2
  assert pool.submitted


def test_populate_pool_reap_joins_dead_thread_without_waitpid(monkeypatch):
  controller = PopulatePoolController()
  pool = _FakePool()
  controller._pool = pool
  controller._shutdown = SimpleNamespace(is_set=lambda: False)
  controller._script_name = "sync_timedb.py"
  controller._registry = {}
  controller._results = [_ReadyResult(), _LiveResult()]
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser"
      ".get_sync_archive_members_populate_pool_processes",
      lambda: 2,
  )
  controller.reap_and_restart()
  assert pool.submitted
  assert any(not result.ready() for result in controller._results)


def test_populate_pool_has_no_spawn_one():
  """Hard cutover: populate workers are ThreadPool apply_async jobs."""
  import inspect

  from hpcperfstats.dbload.lib import sync_timedb_populate_pool as pp

  src = inspect.getsource(pp)
  assert "def _spawn_one" not in src
  assert 'get_context("spawn")' not in src
  assert "apply_async" in src
  assert "create_sync_timedb_thread_pool" in src


def test_populate_pool_started():
  """T6: the orchestrator must start and reap the populate-pool controller."""
  import inspect

  from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo

  source = inspect.getsource(qo.run_sync_timedb_queue_orchestrator)
  assert "PopulatePoolController" in source
  assert "reap_and_restart" in source
  assert "set_populate_pool_controller" in source
