"""Regression tests for multiprocessing pool worker-death detection."""

import multiprocessing
import time
from types import SimpleNamespace

import pytest

from hpcperfstats.dbload.lib import multiprocessing_pool_health as mph


class _DeadWorker:
  pid = 4242
  exitcode = -9

  def is_alive(self):
    return False


class _RecycledWorker:
  def __init__(self, pid=4242):
    self.pid = pid
    self.exitcode = 0
    self._joined = False

  def is_alive(self):
    return False

  def join(self, timeout=None):
    del timeout
    self._joined = True


class _RecycledWorkerNoneExit:
  def __init__(self, pid=4242):
    self.pid = pid
    self.exitcode = None
    self._joined = False

  def is_alive(self):
    return False

  def join(self, timeout=None):
    del timeout
    self._joined = True


class _AliveWorker:
  def __init__(self, pid=4243):
    self.pid = pid
    self._joined = False

  def is_alive(self):
    return not self._joined

  def join(self, timeout=None):
    del timeout
    self._joined = True


class _BlockingPool:
  def __init__(self):
    self._pool = [_AliveWorker()]

  def imap_unordered(self, fn, iterable, chunksize=1):
    del fn, iterable, chunksize

    class _IMap:
      def next(self, timeout=None):
        del timeout
        raise multiprocessing.TimeoutError()

    return _IMap()

  def kill_worker(self):
    self._pool = [_DeadWorker()]


def test_dead_pool_worker_pids_detects_exited_worker():
  pool = SimpleNamespace(_pool=[_DeadWorker(), _AliveWorker()])
  assert mph.dead_pool_worker_pids(pool) == [4242]


def test_alive_pool_worker_count():
  pool = SimpleNamespace(_pool=[_DeadWorker(), _AliveWorker()])
  assert mph.alive_pool_worker_count(pool) == 1


def test_abort_if_pool_workers_dead_raises():
  pool = SimpleNamespace(_pool=[_DeadWorker()])
  with pytest.raises(mph.MultiprocessingWorkerExitError) as excinfo:
    mph.abort_if_pool_workers_dead(pool, context="test")
  assert excinfo.value.exit_code == 137
  assert excinfo.value.dead_pids == (4242,)


def test_abort_if_pool_workers_dead_log_does_not_claim_oom():
  pool = SimpleNamespace(_pool=[_DeadWorker()])
  with pytest.raises(mph.MultiprocessingWorkerExitError) as excinfo:
    mph.abort_if_pool_workers_dead(pool, context="test")
  message = str(excinfo.value)
  assert "likely OOM" not in message
  assert "no longer alive" in message


def test_imap_unordered_watch_pool_aborts_when_worker_dies():
  pool = _BlockingPool()

  def kill_after_delay():
    time.sleep(0.05)
    pool.kill_worker()

  import threading

  threading.Thread(target=kill_after_delay, daemon=True).start()
  iterator = mph.imap_unordered_watch_pool(
      pool,
      lambda x: x,
      [1],
      poll_timeout_s=0.05,
      context="test_imap",
  )
  with pytest.raises(mph.MultiprocessingWorkerExitError):
    next(iterator)


class _BlockingAsyncResult:
  def get(self, timeout=None):
    del timeout
    raise multiprocessing.TimeoutError()


def test_async_result_get_watch_pool_aborts_when_worker_dies():
  pool = SimpleNamespace(_pool=[_AliveWorker()])

  def kill_after_delay():
    time.sleep(0.05)
    pool._pool = [_DeadWorker()]

  import threading

  threading.Thread(target=kill_after_delay, daemon=True).start()
  with pytest.raises(mph.MultiprocessingWorkerExitError):
    mph.async_result_get_watch_pool(
        _BlockingAsyncResult(),
        pool,
        poll_timeout_s=0.05,
        context="test_async",
    )


def test_async_result_get_watch_pool_returns_when_ready():
  pool = SimpleNamespace(_pool=[_AliveWorker()])

  class _ReadyAsyncResult:
    def get(self, timeout=None):
      del timeout
      return [True, False]

  assert mph.async_result_get_watch_pool(
      _ReadyAsyncResult(),
      pool,
      poll_timeout_s=0.05,
      context="test_ready",
  ) == [True, False]


class _CloseablePool:
  closed = False
  terminated = False

  def __init__(self, workers):
    self._pool = workers

  def close(self):
    self.closed = True

  def terminate(self):
    self.terminated = True


def test_close_pool_bounded_terminates_when_worker_dead():
  pool = _CloseablePool([_DeadWorker()])
  assert mph.close_pool_bounded(pool, timeout_s=0.1, force_terminate=False) is True
  assert pool.terminated is True


def test_imap_unordered_watch_pool_aborts_on_stuck_worker_stall(monkeypatch):
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_pool_stall_abort_after_timeouts",
      lambda: 2,
  )
  pool = _BlockingPool()
  iterator = mph.imap_unordered_watch_pool(
      pool,
      lambda x: x,
      [1],
      poll_timeout_s=0.01,
      context="test_stall",
  )
  with pytest.raises(mph.MultiprocessingPoolStallError) as excinfo:
    next(iterator)
  assert excinfo.value.exit_code == 124


def test_imap_stall_logs_before_raise(monkeypatch):
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_pool_stall_abort_after_timeouts",
      lambda: 2,
  )
  logs = []
  monkeypatch.setattr(mph, "log_print", lambda msg, **kwargs: logs.append(str(msg)))
  pool = _BlockingPool()
  iterator = mph.imap_unordered_watch_pool(
      pool,
      lambda x: x,
      [1],
      poll_timeout_s=0.01,
      context="test_stall_log",
  )
  with pytest.raises(mph.MultiprocessingPoolStallError):
    next(iterator)
  assert any("ERROR:" in line and "Pool imap stalled" in line for line in logs)
  assert any("test_stall_log" in line for line in logs)


def test_imap_stall_warning_callback(monkeypatch):
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_pool_stall_abort_after_timeouts",
      lambda: 4,
  )
  warnings = []

  def on_stall_warning(consecutive, abort_after, poll_timeout_s, context):
    warnings.append(
        (consecutive, abort_after, poll_timeout_s, context),
    )

  pool = _BlockingPool()
  iterator = mph.imap_unordered_watch_pool(
      pool,
      lambda x: x,
      [1],
      poll_timeout_s=0.01,
      context="test_warn",
      on_stall_warning=on_stall_warning,
  )
  with pytest.raises(mph.MultiprocessingPoolStallError):
    next(iterator)
  assert warnings[0][0] == 2
  assert warnings[1][0] == 3
  assert warnings[-1][0] == 4


def test_imap_unordered_watch_pool_honors_stall_abort_override(monkeypatch):
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_pool_stall_abort_after_timeouts",
      lambda: 100,
  )
  warnings = []

  def on_stall_warning(consecutive, abort_after, poll_timeout_s, context):
    warnings.append((consecutive, abort_after))

  pool = _BlockingPool()
  iterator = mph.imap_unordered_watch_pool(
      pool,
      lambda x: x,
      [1],
      poll_timeout_s=0.01,
      stall_abort_after_timeouts=2,
      context="test_override",
      on_stall_warning=on_stall_warning,
  )
  with pytest.raises(mph.MultiprocessingPoolStallError):
    next(iterator)
  assert warnings[-1] == (2, 2)


def test_imap_stall_fatal_summary_appended_to_error(monkeypatch):
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_pool_stall_abort_after_timeouts",
      lambda: 2,
  )
  logs = []
  monkeypatch.setattr(mph, "log_print", lambda msg, **kwargs: logs.append(str(msg)))

  def on_stall_fatal_summary(consecutive, abort_after, poll_timeout_s, context):
    del consecutive, abort_after, poll_timeout_s, context
    return " diagnostics_summary=worker_stages=-"

  pool = _BlockingPool()
  iterator = mph.imap_unordered_watch_pool(
      pool,
      lambda x: x,
      [1],
      poll_timeout_s=0.01,
      context="test_fatal_summary",
      on_stall_fatal_summary=on_stall_fatal_summary,
  )
  with pytest.raises(mph.MultiprocessingPoolStallError):
    next(iterator)
  error_lines = [line for line in logs if "ERROR:" in line and "Pool imap stalled" in line]
  assert error_lines
  assert "diagnostics_summary=worker_stages=-" in error_lines[-1]


class _DeferStallPool:
  """Pool whose imap iterator times out until ``release_after`` poll attempts."""

  def __init__(self, release_after=5):
    self.release_after = release_after

  def imap_unordered(self, fn, iterable, chunksize=1):
    del fn, chunksize
    items = iter(iterable)
    release_after = self.release_after

    class _Iterator:
      def __init__(self):
        self._timeouts = 0

      def next(self, timeout=None):
        del timeout
        self._timeouts += 1
        if self._timeouts >= release_after:
          return next(items)
        raise multiprocessing.TimeoutError

    return _Iterator()


def test_imap_stall_counter_resets_during_redis_populate_progress(monkeypatch):
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_pool_stall_abort_after_timeouts",
      lambda: 2,
  )
  pool = _DeferStallPool(release_after=5)
  iterator = mph.imap_unordered_watch_pool(
      pool,
      lambda x: x,
      [42],
      poll_timeout_s=0.01,
      on_stall_poll=lambda *_a, **_k: True,
  )
  assert next(iterator) == 42


def test_close_pool_bounded_closes_alive_workers():
  pool = _CloseablePool([_AliveWorker()])
  assert mph.close_pool_bounded(pool, timeout_s=0.1) is True
  assert pool.closed is True
  assert pool.terminated is False


def test_terminate_pool_bounded_logs_context(monkeypatch):
  logs = []
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.multiprocessing_pool_health.log_print",
      lambda msg, flush=False: logs.append(msg),
  )

  class _TermPool:
    _pool = [_AliveWorker()]

    def terminate(self):
      for worker in self._pool:
        worker.join()

  mph.terminate_pool_bounded(_TermPool(), context="ingest_pool")
  assert any("Pool workers terminated" in line and "ingest_pool" in line for line in logs)


class _StubbornWorker:
  pid = 5555

  def is_alive(self):
    return True

  def join(self, timeout=None):
    del timeout


def test_terminate_pool_bounded_sigkill_after_timeout(monkeypatch):
  logs = []
  killed = []
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.multiprocessing_pool_health.log_print",
      lambda msg, flush=False: logs.append(msg),
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.multiprocessing_pool_health.os.kill",
      lambda pid, sig: killed.append((pid, sig)),
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.multiprocessing_pool_health.os.waitpid",
      lambda pid, flags: (pid, 0),
  )

  class _StubbornPool:
    _pool = [_StubbornWorker()]

    def terminate(self):
      pass

  mph.terminate_pool_bounded(_StubbornPool(), timeout_s=0.01, context="ingest_pool")
  assert any("Pool terminate SIGKILL" in line and "5555" in line for line in logs)
  assert (5555, mph.signal.SIGKILL) in killed


def test_hard_exit_pool_worker_error_uses_os_exit(monkeypatch):
  exit_codes = []
  logs = []
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.multiprocessing_pool_health.log_print",
      lambda msg, flush=False: logs.append(msg),
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.multiprocessing_pool_health.os._exit",
      lambda code: exit_codes.append(code),
  )
  exc = mph.MultiprocessingPoolStallError(
      "pool imap stalled",
      dead_pids=(),
      context="sync_timedb ingest pool",
      exit_code=124,
  )
  mph.hard_exit_pool_worker_error(exc)
  assert exit_codes == [124]
  assert any("hard exit code=124" in line for line in logs)


def test_handle_pool_worker_exit_fatal_hard_exits_without_terminate(monkeypatch):
  import hpcperfstats.dbload.sync_timedb as st

  exit_codes = []
  terminate_calls = []

  monkeypatch.setattr(
      st,
      "terminate_pool_bounded",
      lambda *_a, **_k: terminate_calls.append(True) or False,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.multiprocessing_pool_health.log_print",
      lambda msg, flush=False: None,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.multiprocessing_pool_health.os._exit",
      lambda code: exit_codes.append(code),
  )
  exc = mph.MultiprocessingPoolStallError(
      "pool imap stalled",
      dead_pids=(),
      context="sync_timedb ingest pool",
      exit_code=124,
  )
  st._handle_pool_worker_exit_fatal(exc, ingest_pool=object())
  assert exit_codes == [124]
  assert terminate_calls == []


def test_handle_pool_worker_exit_fatal_hard_exits_when_terminate_would_block(monkeypatch):
  """Regression: production limbo when terminate ran before os._exit(124)."""
  import hpcperfstats.dbload.sync_timedb as st

  exit_codes = []
  terminate_started = []

  def blocking_terminate(*_a, **_k):
    terminate_started.append(True)
    time.sleep(3600)
    return False

  monkeypatch.setattr(st, "terminate_pool_bounded", blocking_terminate)
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.multiprocessing_pool_health.os._exit",
      lambda code: exit_codes.append(code),
  )
  exc = mph.MultiprocessingPoolStallError(
      "pool imap stalled",
      dead_pids=(),
      context="sync_timedb ingest pool",
      exit_code=124,
  )
  st._handle_pool_worker_exit_fatal(exc, ingest_pool=object())
  assert exit_codes == [124]
  assert terminate_started == []


def test_abort_recycle_grace_tolerates_many_checks(monkeypatch):
  monkeypatch.setattr(
      mph, "get_sync_pool_worker_recycle_grace_seconds", lambda: 60.0,
  )
  pool = SimpleNamespace(_pool=[_RecycledWorker(), _AliveWorker()])
  for _ in range(20):
    mph.abort_if_pool_workers_dead(pool, context="recycle_test")


def _recycle_test_pool(*, dead_count=1, alive_count=20, pool_pad=0):
  workers = [_RecycledWorker(pid=1000 + i) for i in range(dead_count)]
  workers.extend(_AliveWorker(pid=5000 + i) for i in range(alive_count))
  workers.extend([None] * pool_pad)
  return SimpleNamespace(_pool=workers)


def test_abort_recycle_20_of_21_alive_no_fatal(monkeypatch):
  """July-08 display signature: 20 alive, 1 dead recycle, 21 materialized."""
  monkeypatch.setattr(
      mph, "get_sync_pool_worker_recycle_grace_seconds", lambda: 60.0,
  )
  logs = []
  monkeypatch.setattr(mph, "log_print", lambda msg, **kwargs: logs.append(str(msg)))
  pool = _recycle_test_pool(dead_count=1, alive_count=20)
  ctx = {"expected_pool_workers": 24}
  mph.abort_if_pool_workers_dead(
      pool,
      context="sync_timedb ingest chunk",
      pool_health_context=ctx,
  )
  assert not any("ERROR: pool worker death diagnostics" in line for line in logs)
  assert any("pool worker recycle in progress" in line for line in logs)


def test_abort_recycle_24_cap_20_alive_one_dead_no_fatal(monkeypatch):
  """Reproduces July-08 fatal: process_cap 24, 20 alive, 1 dead, pool below cap."""
  monkeypatch.setattr(
      mph, "get_sync_pool_worker_recycle_grace_seconds", lambda: 60.0,
  )
  logs = []
  monkeypatch.setattr(mph, "log_print", lambda msg, **kwargs: logs.append(str(msg)))
  pool = _recycle_test_pool(dead_count=1, alive_count=20)
  ctx = {"expected_pool_workers": 24}
  mph.abort_if_pool_workers_dead(
      pool,
      context="sync_timedb ingest chunk",
      pool_health_context=ctx,
  )
  assert not any("ERROR: pool worker death diagnostics" in line for line in logs)


def test_abort_recycle_22_pool_19_alive_three_dead_no_fatal(monkeypatch):
  """July-08 tolerated poll: 19 alive, 3 dead recycle, 22 materialized."""
  monkeypatch.setattr(
      mph, "get_sync_pool_worker_recycle_grace_seconds", lambda: 60.0,
  )
  logs = []
  monkeypatch.setattr(mph, "log_print", lambda msg, **kwargs: logs.append(str(msg)))
  pool = _recycle_test_pool(dead_count=3, alive_count=19)
  ctx = {"expected_pool_workers": 24}
  mph.abort_if_pool_workers_dead(
      pool,
      context="sync_timedb ingest chunk",
      pool_health_context=ctx,
  )
  assert not any("ERROR: pool worker death diagnostics" in line for line in logs)


def test_abort_recycle_spawn_gap_raw_pool_len_no_fatal(monkeypatch):
  """Raw _pool longer than materialized workers during replacement spawn."""
  monkeypatch.setattr(
      mph, "get_sync_pool_worker_recycle_grace_seconds", lambda: 60.0,
  )
  pool = _recycle_test_pool(dead_count=1, alive_count=20, pool_pad=1)
  mph.abort_if_pool_workers_dead(pool, context="spawn_gap")


def test_abort_recycle_consecutive_different_pids_no_fatal(monkeypatch):
  """Reproduces hpcperfstats03: grace 1/2, 2/2, then third PID must not fatal."""
  monkeypatch.setattr(
      mph, "get_sync_pool_worker_recycle_grace_seconds", lambda: 60.0,
  )
  logs = []
  monkeypatch.setattr(mph, "log_print", lambda msg, **kwargs: logs.append(str(msg)))

  def pool_with_one_dead(dead_pid, alive_count=15):
    workers = [_RecycledWorker(pid=dead_pid)]
    workers.extend(_AliveWorker(pid=6000 + i) for i in range(alive_count))
    return SimpleNamespace(_pool=workers)

  mph.abort_if_pool_workers_dead(pool_with_one_dead(1173), context="sync_timedb ingest chunk")
  mph.abort_if_pool_workers_dead(pool_with_one_dead(1500), context="sync_timedb ingest chunk")
  mph.abort_if_pool_workers_dead(pool_with_one_dead(1765), context="sync_timedb ingest chunk")
  assert not any("ERROR: pool worker death diagnostics" in line for line in logs)
  assert any("dead_pid=1765" in line for line in logs)


def test_abort_recycle_many_rapid_checks_no_fatal(monkeypatch):
  monkeypatch.setattr(
      mph, "get_sync_pool_worker_recycle_grace_seconds", lambda: 60.0,
  )
  pool = SimpleNamespace(_pool=[_RecycledWorker(), _AliveWorker()])
  for _ in range(25):
    mph.abort_if_pool_workers_dead(pool, context="rapid")


def test_abort_recycle_stuck_replacements_fatal(monkeypatch):
  monkeypatch.setattr(
      mph, "get_sync_pool_worker_recycle_grace_seconds", lambda: 60.0,
  )
  dead_workers = [_RecycledWorker(pid=100 + i) for i in range(4)]
  pool = SimpleNamespace(_pool=dead_workers)
  with pytest.raises(mph.MultiprocessingWorkerExitError) as excinfo:
    mph.abort_if_pool_workers_dead(pool, context="stuck")
  assert excinfo.value.likely_cause == "recycle_stuck"
  assert excinfo.value.exit_code == 137


def test_abort_recycle_slow_spawn_warn_not_fatal(monkeypatch):
  mono = [1000.0]

  def fake_monotonic():
    return mono[0]

  monkeypatch.setattr(mph.time, "monotonic", fake_monotonic)
  monkeypatch.setattr(
      mph, "get_sync_pool_worker_recycle_grace_seconds", lambda: 10.0,
  )
  logs = []
  monkeypatch.setattr(mph, "log_print", lambda msg, **kwargs: logs.append(str(msg)))
  pool = SimpleNamespace(_pool=[_RecycledWorker(), _AliveWorker()])
  mph.abort_if_pool_workers_dead(pool, context="slow")
  mono[0] += 15.0
  mph.abort_if_pool_workers_dead(pool, context="slow")
  assert any("WARN: pool worker recycle slow" in line for line in logs)
  assert not any("ERROR: pool worker death diagnostics" in line for line in logs)


def test_abort_recycle_exitcode_none_grace(monkeypatch):
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_ingest_pool_maxtasksperchild",
      lambda: 1,
  )
  monkeypatch.setattr(
      mph, "get_sync_pool_worker_recycle_grace_seconds", lambda: 60.0,
  )
  pool = SimpleNamespace(_pool=[_RecycledWorkerNoneExit(), _AliveWorker()])
  mph.abort_if_pool_workers_dead(pool, context="none_exit")
  mph.abort_if_pool_workers_dead(pool, context="none_exit")


def test_abort_recycle_grace_reaps_dead_worker_pids(monkeypatch):
  monkeypatch.setattr(
      mph, "get_sync_pool_worker_recycle_grace_seconds", lambda: 60.0,
  )
  waitpids = []

  def _waitpid(pid, flags):
    waitpids.append((pid, flags))
    return (pid, 0)

  monkeypatch.setattr(mph.os, "waitpid", _waitpid)
  recycled = _RecycledWorker()
  pool = SimpleNamespace(_pool=[recycled, _AliveWorker()])
  mph.abort_if_pool_workers_dead(pool, context="recycle_reap")
  assert recycled._joined is True
  assert any(pid == 4242 for pid, _flags in waitpids)


def test_abort_recycle_grace_reaps_zombie_children(monkeypatch):
  monkeypatch.setattr(
      mph, "get_sync_pool_worker_recycle_grace_seconds", lambda: 60.0,
  )
  zombie_calls = []
  warn_calls = []

  def _reap_zombies(*, context=""):
    zombie_calls.append(context)
    return []

  def _warn(*, context=""):
    warn_calls.append(context)

  monkeypatch.setattr(mph, "reap_zombie_children_of_self", _reap_zombies)
  monkeypatch.setattr(mph, "warn_unreaped_zombie_children", _warn)
  pool = SimpleNamespace(_pool=[_RecycledWorker(), _AliveWorker()])
  mph.abort_if_pool_workers_dead(pool, context="recycle_zombie")
  assert zombie_calls == ["recycle_zombie"]
  assert warn_calls == ["recycle_zombie"]


def test_warn_unreaped_zombie_children_logs_when_zombies_remain(monkeypatch):
  logs = []
  monkeypatch.setattr(mph, "log_print", lambda msg, **kwargs: logs.append(str(msg)))
  monkeypatch.setattr(mph, "_iter_zombie_child_pids", lambda: iter([111, 222, 333]))
  mph.warn_unreaped_zombie_children(context="unit_warn")
  assert any("WARN: unreaped zombie children context=unit_warn count=3" in line for line in logs)


def test_reap_pool_worker_pids_logs_reaped(monkeypatch):
  logs = []
  monkeypatch.setattr(mph, "log_print", lambda msg, **kwargs: logs.append(str(msg)))
  waitpids = []

  def _waitpid(pid, flags):
    waitpids.append(pid)
    return (pid, 0)

  monkeypatch.setattr(mph.os, "waitpid", _waitpid)
  pool = SimpleNamespace(_pool=[_RecycledWorker()])
  reaped = mph.reap_pool_worker_pids(pool, context="unit")
  assert reaped == [4242]
  assert any("Pool worker reap context=unit" in line for line in logs)


def test_create_sync_timedb_spawn_pool_requires_pool_kind():
  with pytest.raises(ValueError, match="pool_kind_log_label"):
    mph.create_sync_timedb_spawn_pool(
        processes=1,
        initializer=lambda: None,
        initargs=(),
        pool_kind_log_label="",
    )


def test_abort_archive_recycle_healthy_when_ingest_maxtasks_zero(monkeypatch):
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_ingest_pool_maxtasksperchild",
      lambda: 0,
  )
  monkeypatch.setattr(
      mph, "get_sync_pool_worker_recycle_grace_seconds", lambda: 60.0,
  )
  ingest_pool = SimpleNamespace(_pool=[_AliveWorker()])
  archive_pool = SimpleNamespace(_pool=[_RecycledWorker(), _AliveWorker()])
  mph.abort_if_pool_workers_dead(
      archive_pool,
      context="archive_recycle",
      pool_health_context={
          "ingest_pool": ingest_pool,
          "archive_pool": archive_pool,
      },
  )


def test_reap_zombie_children_of_self_waitpids_state_z(monkeypatch):
  logs = []
  monkeypatch.setattr(mph, "log_print", lambda msg, **kwargs: logs.append(str(msg)))
  monkeypatch.setattr(mph, "_iter_zombie_child_pids", lambda: iter([99901, 99902]))
  waitpids = []

  def _waitpid(pid, flags):
    waitpids.append(pid)
    return (pid, 0)

  monkeypatch.setattr(mph.os, "waitpid", _waitpid)
  reaped = mph.reap_zombie_children_of_self(context="unit_z")
  assert reaped == [99901, 99902]
  assert waitpids == [99901, 99902]
  assert any("Zombie child reap context=unit_z" in line for line in logs)


def test_abort_recycle_grace_logs_info_not_error(monkeypatch):
  monkeypatch.setattr(
      mph, "get_sync_pool_worker_recycle_grace_seconds", lambda: 60.0,
  )
  logs = []
  monkeypatch.setattr(mph, "log_print", lambda msg, **kwargs: logs.append(str(msg)))
  pool = SimpleNamespace(_pool=[_RecycledWorker(), _AliveWorker()])
  mph.abort_if_pool_workers_dead(pool, context="recycle_log")
  assert any("INFO: pool worker recycle in progress" in line for line in logs)
  assert any("grace_deadline_s=" in line for line in logs)
  assert not any("ERROR: pool worker death diagnostics" in line for line in logs)


def test_abort_sigkill_logs_diagnostics_with_non_cgroup_hint(monkeypatch):
  logs = []
  monkeypatch.setattr(mph, "log_print", lambda msg, **kwargs: logs.append(str(msg)))
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.process_memory.read_cgroup_memory_events",
      lambda: {"oom_kill": 0},
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.process_memory.read_cgroup_memory_current_bytes",
      lambda: 33205403648,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.process_memory.read_cgroup_memory_max_bytes",
      lambda: 137438953472,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.process_memory.format_tree_rss_breakdown_mb",
      lambda *a, **k: {"tree_total_mb": 31.0, "supervisor_mb": 1.0,
                       "ingest_pool_mb": 20.0, "archive_pool_mb": 5.0},
  )
  pool = SimpleNamespace(_pool=[_DeadWorker()])
  with pytest.raises(mph.MultiprocessingWorkerExitError) as excinfo:
    mph.abort_if_pool_workers_dead(pool, context="sigkill_test")
  assert excinfo.value.likely_cause == "sigkill_non_cgroup"
  assert any("ERROR: pool worker death diagnostics" in line for line in logs)
  assert any("likely_cause=sigkill_non_cgroup" in line for line in logs)


def test_abort_sigkill_with_cgroup_oom_reports_sigkill(monkeypatch):
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.process_memory.read_cgroup_memory_events",
      lambda: {"oom_kill": 3},
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.process_memory.read_cgroup_memory_current_bytes",
      lambda: 100,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.process_memory.read_cgroup_memory_max_bytes",
      lambda: 1000,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.process_memory.format_tree_rss_breakdown_mb",
      lambda *a, **k: {"tree_total_mb": 1.0, "supervisor_mb": 1.0,
                       "ingest_pool_mb": 0.0, "archive_pool_mb": 0.0},
  )
  pool = SimpleNamespace(_pool=[_DeadWorker()])
  with pytest.raises(mph.MultiprocessingWorkerExitError) as excinfo:
    mph.abort_if_pool_workers_dead(pool, context="cgroup_oom")
  assert excinfo.value.likely_cause == "sigkill"


def test_describe_dead_pool_workers_includes_in_flight_sample(monkeypatch):
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.process_memory.read_cgroup_memory_events",
      lambda: {"oom_kill": 0},
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.process_memory.read_cgroup_memory_current_bytes",
      lambda: 0,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.process_memory.read_cgroup_memory_max_bytes",
      lambda: None,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.process_memory.format_tree_rss_breakdown_mb",
      lambda *a, **k: {"tree_total_mb": 0.0, "supervisor_mb": 0.0,
                       "ingest_pool_mb": 0.0, "archive_pool_mb": 0.0},
  )
  pool = SimpleNamespace(_pool=[_RecycledWorker(), _AliveWorker()])
  diag = mph.describe_dead_pool_workers(
      pool,
      pool_health_context={"in_flight_sample": ["/pending/a"]},
  )
  assert diag["in_flight_sample"] == ["/pending/a"]
  assert diag["likely_cause"] == "recycle"


class _ManualAsyncResult:
  def __init__(self, pool, fn, path):
    self._pool = pool
    self._fn = fn
    self._path = path
    self._ready = False
    self._result = None
    pool.inflight[self] = path
    pool.peak = max(pool.peak, len(pool.inflight))
    pool.submit_count += 1

  def ready(self):
    return self._ready

  def get(self, timeout=None):
    del timeout
    if not self._ready:
      raise multiprocessing.TimeoutError()
    return self._result

  def finish(self):
    self._result = self._fn(self._path)
    self._ready = True
    self._pool.inflight.pop(self, None)


class _ManualPool:
  def __init__(self):
    self.inflight = {}
    self.peak = 0
    self.submit_count = 0
    self._pool = [_AliveWorker()]

  def apply_async(self, fn, args=()):
    return _ManualAsyncResult(self, fn, args[0])


def test_imap_sliding_window_watch_pool_refills_before_prior_batch_drains():
  import threading

  pool = _ManualPool()
  paths = [
      "slow0",
      "fast1",
      "fast2",
      "fast3",
      "slow4",
      "fast5",
      "fast6",
      "fast7",
  ]
  gen = mph.imap_sliding_window_watch_pool(
      pool,
      lambda path: path,
      paths,
      max_inflight=4,
      poll_timeout_s=0.01,
      stall_abort_polls_fn=lambda in_flight: 10000,
      context="test_sliding_refill",
  )
  results = []
  errors = []

  def consumer():
    try:
      for item in gen:
        results.append(item)
    except Exception as exc:
      errors.append(exc)

  thread = threading.Thread(target=consumer, daemon=True)
  thread.start()
  deadline = time.monotonic() + 2.0
  while pool.submit_count < 4 and time.monotonic() < deadline:
    time.sleep(0.005)
  assert pool.submit_count == 4
  fast_first_batch = [
      ar for ar, path in pool.inflight.items() if path.startswith("fast")
  ]
  assert len(fast_first_batch) == 3
  for ar in fast_first_batch:
    ar.finish()
  deadline = time.monotonic() + 2.0
  while pool.submit_count < 7 and time.monotonic() < deadline:
    time.sleep(0.005)
  assert pool.submit_count >= 7
  assert pool.peak == 4
  deadline = time.monotonic() + 5.0
  while len(results) < len(paths) and time.monotonic() < deadline:
    for ar in list(pool.inflight):
      ar.finish()
    time.sleep(0.01)
  thread.join(timeout=2.0)
  assert not errors
  assert sorted(results) == sorted(paths)


def test_imap_sliding_window_watch_pool_peak_concurrency():
  pool = _ManualPool()
  paths = [f"path{i}" for i in range(20)]
  gen = mph.imap_sliding_window_watch_pool(
      pool,
      lambda path: path,
      paths,
      max_inflight=4,
      poll_timeout_s=0.01,
      stall_abort_polls_fn=lambda in_flight: 10000,
  )
  results = []
  import threading

  def consumer():
    for item in gen:
      results.append(item)

  thread = threading.Thread(target=consumer, daemon=True)
  thread.start()
  time.sleep(0.02)
  assert pool.peak == 4
  deadline = time.monotonic() + 2.0
  while (pool.inflight or len(results) < len(paths)) and time.monotonic() < deadline:
    for ar in list(pool.inflight):
      ar.finish()
    time.sleep(0.005)
  thread.join(timeout=2.0)
  assert len(results) == len(paths)
  assert pool.peak == 4


def test_imap_sliding_window_recomputes_stall_abort_for_in_flight(monkeypatch):
  from hpcperfstats.dbload.lib import sync_timedb_ingest_timeout as timeout_mod

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_pool_poll_timeout_s",
      lambda: 5.0,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_pool_stall_abort_after_timeouts",
      lambda: 2881,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_ingest_per_file_timeout_s",
      lambda: 900.0,
  )
  monkeypatch.setattr(
      timeout_mod,
      "resolve_ingest_per_file_timeout_s",
      lambda path: 900.0 if "small" in path else 7200.0,
  )

  recorded = []

  def _polls_fn(in_flight):
    value = timeout_mod.stall_abort_polls_for_paths(in_flight)
    recorded.append((list(in_flight), value))
    return value

  pool = _ManualPool()
  paths = ["large0", "small1", "small2", "small3"]
  gen = mph.imap_sliding_window_watch_pool(
      pool,
      lambda path: path,
      paths,
      max_inflight=2,
      poll_timeout_s=0.01,
      stall_abort_polls_fn=_polls_fn,
  )
  import threading

  thread = threading.Thread(target=lambda: list(gen), daemon=True)
  thread.start()
  time.sleep(0.02)
  assert recorded
  large_polls = timeout_mod.stall_abort_polls_for_paths(["large0"])
  small_polls = timeout_mod.stall_abort_polls_for_paths(["small1"])
  assert large_polls > small_polls
  assert any(polls >= large_polls for _paths, polls in recorded if "large0" in _paths)
  for ar in list(pool.inflight):
    ar.finish()
  thread.join(timeout=2.0)


def test_sliding_window_supplements_sub_1g_when_giants_in_flight():
  pool = _ManualPool()
  chunk_paths = ["giant0", "giant1"]
  supplement_calls = []

  def _supplement(slots_needed, in_flight):
    supplement_calls.append((slots_needed, list(in_flight)))
    if not any(path.startswith("giant") for path in in_flight):
      return []
    return [f"tail{i}" for i in range(slots_needed)]

  gen = mph.imap_sliding_window_watch_pool(
      pool,
      lambda path: path,
      chunk_paths,
      max_inflight=4,
      poll_timeout_s=0.01,
      stall_abort_polls_fn=lambda in_flight: 10000,
      supplement_paths_fn=_supplement,
  )
  import threading

  thread = threading.Thread(target=lambda: list(gen), daemon=True)
  thread.start()
  deadline = time.monotonic() + 2.0
  while pool.submit_count < 4 and time.monotonic() < deadline:
    time.sleep(0.005)
  assert pool.submit_count == 4
  assert supplement_calls
  dispatched = {path for path in pool.inflight.values()}
  assert "tail0" in dispatched
  assert "tail1" in dispatched
  for ar in list(pool.inflight):
    ar.finish()
  deadline = time.monotonic() + 2.0
  while pool.submit_count < 4 and time.monotonic() < deadline:
    time.sleep(0.005)
  thread.join(timeout=2.0)


def test_supplement_not_used_while_chunk_paths_remain():
  pool = _ManualPool()
  paths = ["chunk0", "chunk1", "chunk2", "chunk3"]
  supplement_calls = []

  def _supplement(slots_needed, in_flight):
    supplement_calls.append((slots_needed, list(in_flight)))
    return ["tail0"]

  gen = mph.imap_sliding_window_watch_pool(
      pool,
      lambda path: path,
      paths,
      max_inflight=2,
      poll_timeout_s=0.01,
      stall_abort_polls_fn=lambda in_flight: 10000,
      supplement_paths_fn=_supplement,
  )
  import threading

  results = []

  def consumer():
    for item in gen:
      results.append(item)

  thread = threading.Thread(target=consumer, daemon=True)
  thread.start()
  time.sleep(0.02)
  assert pool.submit_count == 2
  assert supplement_calls == []
  deadline = time.monotonic() + 2.0
  while len(results) < len(paths) and time.monotonic() < deadline:
    for ar in list(pool.inflight):
      ar.finish()
    time.sleep(0.01)
  thread.join(timeout=2.0)
  assert len(results) == len(paths)


def test_supplement_duplicate_only_does_not_busy_spin():
  """Duplicate-suppressed supplement paths must exit refill, not spin forever."""
  pool = _ManualPool()
  paths = ["chunk0"]
  calls = {"n": 0}

  def _supplement(slots_needed, in_flight):
    del slots_needed
    calls["n"] += 1
    # Always offer a path already in flight → dispatch suppressed.
    return list(in_flight)[:1] or ["chunk0"]

  gen = mph.imap_sliding_window_watch_pool(
      pool,
      lambda path: path,
      paths,
      max_inflight=2,
      poll_timeout_s=0.01,
      stall_abort_polls_fn=lambda in_flight: 10000,
      supplement_paths_fn=_supplement,
  )
  import threading

  results = []

  def consumer():
    for item in gen:
      results.append(item)

  thread = threading.Thread(target=consumer, daemon=True)
  thread.start()
  deadline = time.monotonic() + 2.0
  while not results and time.monotonic() < deadline:
    for ar in list(pool.inflight):
      ar.finish()
    time.sleep(0.01)
  thread.join(timeout=2.0)
  assert results == ["chunk0"]
  assert calls["n"] < 50


def test_supplement_requires_giant_in_flight():
  pool = _ManualPool()
  chunk_paths = ["small0", "small1"]
  supplement_calls = []

  def _supplement(slots_needed, in_flight):
    supplement_calls.append((slots_needed, list(in_flight)))
    return ["tail0"]

  gen = mph.imap_sliding_window_watch_pool(
      pool,
      lambda path: path,
      chunk_paths,
      max_inflight=4,
      poll_timeout_s=0.01,
      stall_abort_polls_fn=lambda in_flight: 10000,
      supplement_paths_fn=lambda slots, in_flight: (
          _supplement(slots, in_flight)
          if any(path.startswith("giant") for path in in_flight)
          else []
      ),
  )
  import threading

  thread = threading.Thread(target=lambda: list(gen), daemon=True)
  thread.start()
  deadline = time.monotonic() + 2.0
  while pool.submit_count < 2 and time.monotonic() < deadline:
    time.sleep(0.005)
  assert supplement_calls == []
  for ar in list(pool.inflight):
    ar.finish()
  thread.join(timeout=2.0)


def test_imap_sliding_window_waits_for_slow_in_flight():
  import threading

  pool = _ManualPool()
  paths = ["giant0"]
  finished = {"done": False}

  gen = mph.imap_sliding_window_watch_pool(
      pool,
      lambda path: path,
      paths,
      max_inflight=2,
      poll_timeout_s=0.01,
      stall_abort_polls_fn=lambda in_flight: 100000,
  )

  def consumer():
    list(gen)
    finished["done"] = True

  thread = threading.Thread(target=consumer, daemon=True)
  thread.start()
  time.sleep(0.05)
  assert finished["done"] is False
  assert len(pool.inflight) == 1
  for ar in list(pool.inflight):
    ar.finish()
  thread.join(timeout=2.0)
  assert finished["done"] is True


def test_supplement_dedupe_skips_batch_completed_paths(tmp_path, monkeypatch):
  from hpcperfstats.dbload.lib import sync_timedb_ingest_timeout as ingest_timeout_mod

  monkeypatch.setattr(
      ingest_timeout_mod.cfg, "get_sync_ingest_giant_pool_supplement_max_bytes", lambda: 10**9,
  )
  tail0 = str(tmp_path / "tail0")
  tail1 = str(tmp_path / "tail1")
  (tmp_path / "tail0").write_bytes(b"x" * 100)
  (tmp_path / "tail1").write_bytes(b"x" * 100)
  batch_seen = {tail0}
  pending_tail = [tail0, tail1]
  picked = list(
      ingest_timeout_mod.iter_giant_supplement_paths(
          pending_tail,
          limit=2,
          exclude=batch_seen,
      ),
  )
  assert picked == [tail1]


def test_supplement_dedupe_same_path_not_redispatched_in_batch():
  import threading
  from hpcperfstats.dbload.lib import sync_timedb_ingest_timeout as ingest_timeout_mod

  pool = _ManualPool()
  chunk_paths = ["giant0"]
  batch_seen = set(chunk_paths)
  dispatch_counts = {"tail0": 0}

  def _supplement(slots_needed, in_flight):
    exclude = set(in_flight) | batch_seen
    picked = list(
        ingest_timeout_mod.iter_giant_supplement_paths(
            ["tail0", "tail1"],
            limit=slots_needed,
            exclude=exclude,
        ),
    )
    for path in picked:
      batch_seen.add(path)
      dispatch_counts[path] = dispatch_counts.get(path, 0) + 1
    return picked

  gen = mph.imap_sliding_window_watch_pool(
      pool,
      lambda path: path,
      chunk_paths,
      max_inflight=2,
      poll_timeout_s=0.01,
      stall_abort_polls_fn=lambda in_flight: 100000,
      supplement_paths_fn=_supplement,
  )

  def consumer():
    for item in gen:
      if item == "tail0":
        batch_seen.add("tail0")

  thread = threading.Thread(target=consumer, daemon=True)
  thread.start()
  deadline = time.monotonic() + 2.0
  while pool.submit_count < 2 and time.monotonic() < deadline:
    time.sleep(0.005)
  for ar in list(pool.inflight):
    if pool.inflight.get(ar) != "giant0":
      ar.finish()
  deadline = time.monotonic() + 2.0
  while pool.inflight and time.monotonic() < deadline:
    for ar in list(pool.inflight):
      ar.finish()
    time.sleep(0.01)
  thread.join(timeout=2.0)
  assert dispatch_counts.get("tail0", 0) <= 1


def test_pool_workers_all_idle_false_when_wchan_unavailable(monkeypatch):
  pool = SimpleNamespace(_pool=[_AliveWorker()])
  monkeypatch.setattr(mph, "read_process_wchan", lambda _pid: None)
  assert mph.pool_workers_all_idle(pool) is False


def test_pool_workers_all_idle_true_for_futex_wchan(monkeypatch):
  pool = SimpleNamespace(_pool=[_AliveWorker()])
  monkeypatch.setattr(mph, "read_process_wchan", lambda _pid: "futex_wait_queue")
  assert mph.pool_workers_all_idle(pool) is True


def test_pool_workers_all_idle_false_when_worker_running(monkeypatch):
  pool = SimpleNamespace(_pool=[_AliveWorker()])
  monkeypatch.setattr(mph, "read_process_wchan", lambda _pid: "0")
  assert mph.pool_workers_all_idle(pool) is False


def test_idle_pool_ghost_abort_polls_clamped():
  assert mph.idle_pool_ghost_abort_polls(10000) == 120
  assert mph.idle_pool_ghost_abort_polls(100) == 12
  assert mph.idle_pool_ghost_abort_polls(200) == 12


def test_imap_sliding_window_idle_pool_ghost_fatal(monkeypatch):
  monkeypatch.setattr(mph, "idle_pool_ghost_abort_polls", lambda _n: 3)
  monkeypatch.setattr(mph, "pool_workers_all_idle", lambda _p: True)
  monkeypatch.setattr(mph, "get_sync_pool_idle_reconcile_max_rounds", lambda: 0)
  monkeypatch.setattr(mph, "get_sync_pool_idle_reconcile_polls_per_round", lambda: 1)
  pool = _ManualPool()
  paths = ["ghost_path"]
  ghost_fatal = {"called": False, "paths": None}

  def on_fatal(pending_paths):
    ghost_fatal["called"] = True
    ghost_fatal["paths"] = list(pending_paths)

  gen = mph.imap_sliding_window_watch_pool(
      pool,
      lambda path: path,
      paths,
      max_inflight=1,
      poll_timeout_s=0.01,
      stall_abort_polls_fn=lambda in_flight: 100000,
      on_idle_pool_ghost_fatal=on_fatal,
  )
  with pytest.raises(mph.MultiprocessingPoolStallError) as excinfo:
    list(gen)
  assert excinfo.value.context == mph._IDLE_POOL_GHOST_CONTEXT
  assert excinfo.value.exit_code == 124
  assert "idle workers with pending async" in str(excinfo.value)
  assert ghost_fatal["called"] is True
  assert ghost_fatal["paths"] == ["ghost_path"]


class _OrphanAsyncResult:
  """Simulates worker success lost from the result queue (ready() false, get(0) works)."""

  def __init__(self, pool, fn, path):
    self._pool = pool
    self._fn = fn
    self._path = path
    self._result = fn(path)
    pool.inflight[self] = path
    pool.peak = max(pool.peak, len(pool.inflight))
    pool.submit_count += 1

  def ready(self):
    return False

  def get(self, timeout=None):
    if timeout == 0:
      return self._result
    raise multiprocessing.TimeoutError()


def test_try_collect_async_result_without_ready():
  pool = _ManualPool()
  ar = _OrphanAsyncResult(pool, lambda p: p, "orphan_path")
  assert ar.ready() is False
  assert mph.try_collect_async_result(ar) == "orphan_path"


def test_reconcile_idle_pending_async_collects_orphan():
  pool = _ManualPool()
  pending = {_OrphanAsyncResult(pool, lambda p: p, "p1"): "p1"}
  collected, redispatched = mph.reconcile_idle_pending_async(
      pool,
      pending,
      lambda path: path,
  )
  assert redispatched == 0
  assert collected == [("p1", "p1")]
  assert not pending


def test_reconcile_idle_pending_async_redispatches_stale():
  pool = _ManualPool()
  stale = _ManualAsyncResult(pool, lambda p: p, "stale_path")
  pending = {stale: "stale_path"}
  redispatch_paths = []

  collected, redispatched = mph.reconcile_idle_pending_async(
      pool,
      pending,
      lambda path: path,
      on_redispatch=lambda path: redispatch_paths.append(path),
  )
  assert collected == []
  assert redispatched == 1
  assert redispatch_paths == ["stale_path"]
  assert len(pending) == 1
  assert list(pending.values()) == ["stale_path"]
  assert pool.submit_count == 2


def test_reconcile_idle_pending_async_skip_without_redispatch():
  pool = _ManualPool()
  stale = _ManualAsyncResult(pool, lambda p: p, "skip_path")
  pending = {stale: "skip_path"}
  collected, redispatched = mph.reconcile_idle_pending_async(
      pool,
      pending,
      lambda path: path,
      resolve_skip_result=lambda path: (path, False, True, 0.0),
  )
  assert redispatched == 0
  assert collected == [("skip_path", ("skip_path", False, True, 0.0))]
  assert not pending
  assert pool.submit_count == 1


def test_imap_sliding_window_orphan_collect_avoids_ghost_fatal(monkeypatch):
  monkeypatch.setattr(mph, "idle_pool_ghost_abort_polls", lambda _n: 3)
  monkeypatch.setattr(mph, "pool_workers_all_idle", lambda _p: True)
  monkeypatch.setattr(mph, "get_sync_pool_idle_reconcile_max_rounds", lambda: 3)
  monkeypatch.setattr(mph, "get_sync_pool_idle_reconcile_polls_per_round", lambda: 100)
  pool = _ManualPool()

  class _OrphanPool(_ManualPool):
    def apply_async(self, fn, args=()):
      return _OrphanAsyncResult(self, fn, args[0])

  pool = _OrphanPool()
  gen = mph.imap_sliding_window_watch_pool(
      pool,
      lambda path: path,
      ["orphan_path"],
      max_inflight=1,
      poll_timeout_s=0.01,
      stall_abort_polls_fn=lambda in_flight: 100000,
  )
  results = list(gen)
  assert results == ["orphan_path"]


def test_imap_sliding_window_reconcile_redispatch_within_budget(monkeypatch):
  monkeypatch.setattr(mph, "idle_pool_ghost_abort_polls", lambda _n: 1000)
  monkeypatch.setattr(mph, "pool_workers_all_idle", lambda _p: True)
  monkeypatch.setattr(mph, "get_sync_pool_idle_reconcile_max_rounds", lambda: 3)
  monkeypatch.setattr(mph, "get_sync_pool_idle_reconcile_polls_per_round", lambda: 1)
  pool = _ManualPool()
  redispatch_count = {"n": 0}

  def on_redispatch(path):
    del path
    redispatch_count["n"] += 1

  gen = mph.imap_sliding_window_watch_pool(
      pool,
      lambda path: path,
      ["stale_path"],
      max_inflight=1,
      poll_timeout_s=0.01,
      stall_abort_polls_fn=lambda in_flight: 100000,
      on_reconcile_redispatch=on_redispatch,
  )

  import threading

  def finish_after_redispatch():
    deadline = time.monotonic() + 2.0
    while redispatch_count["n"] < 1 and time.monotonic() < deadline:
      time.sleep(0.005)
    for ar in list(pool.inflight):
      ar.finish()

  threading.Thread(target=finish_after_redispatch, daemon=True).start()
  results = list(gen)
  assert results == ["stale_path"]
  assert redispatch_count["n"] >= 1


def test_imap_sliding_window_ghost_fatal_after_reconcile_exhausted(monkeypatch):
  monkeypatch.setattr(mph, "idle_pool_ghost_abort_polls", lambda _n: 3)
  monkeypatch.setattr(mph, "pool_workers_all_idle", lambda _p: True)
  monkeypatch.setattr(mph, "get_sync_pool_idle_reconcile_max_rounds", lambda: 1)
  monkeypatch.setattr(mph, "get_sync_pool_idle_reconcile_polls_per_round", lambda: 1)
  pool = _ManualPool()
  gen = mph.imap_sliding_window_watch_pool(
      pool,
      lambda path: path,
      ["ghost_path"],
      max_inflight=1,
      poll_timeout_s=0.01,
      stall_abort_polls_fn=lambda in_flight: 100000,
  )
  with pytest.raises(mph.MultiprocessingPoolStallError) as excinfo:
    list(gen)
  assert excinfo.value.context == mph._IDLE_POOL_GHOST_CONTEXT
  assert excinfo.value.exit_code == 124
  assert excinfo.value.likely_cause == mph._IDLE_POOL_TASKQUEUE_DEAD_CAUSE


def test_dedupe_ingest_paths_preserve_order_counts_duplicates():
  paths = ["/archive/host/1781085150"] * 8 + ["/archive/host/1781081790"]
  unique, duplicate_n, sample = mph.dedupe_ingest_paths_preserve_order(paths)
  assert len(unique) == 2
  assert duplicate_n == 7
  assert "1781085150:8" in sample


def test_sliding_window_suppresses_duplicate_normpath_dispatch(capsys):
  class _AutoFinishPool(_ManualPool):
    def apply_async(self, fn, args=()):
      ar = super().apply_async(fn, args)
      ar.finish()
      return ar

  pool = _AutoFinishPool()
  paths = [
      "/archive/c637-051/1780788583",
      "/archive/c637-051/1780788583",
      "/archive/c637-062/1780788583",
  ]
  gen = mph.imap_sliding_window_watch_pool(
      pool,
      lambda path: path,
      paths,
      max_inflight=3,
      poll_timeout_s=0.01,
      stall_abort_polls_fn=lambda in_flight: 100000,
      context="test_duplicate_dispatch",
  )
  results = list(gen)
  assert pool.submit_count == 2
  assert sorted(results) == [
      "/archive/c637-051/1780788583",
      "/archive/c637-062/1780788583",
  ]
  out = capsys.readouterr().out
  assert "duplicate dispatch suppressed" in out
  assert "path=c637-051/1780788583" in out
  assert "path=1780788583 " not in out.replace("path=c637-051/1780788583", "")


def test_ingest_path_dispatch_label_host_basename():
  assert (
      mph.ingest_path_dispatch_label("/archive/c637-051/1780788583")
      == "c637-051/1780788583"
  )
  assert mph.ingest_path_dispatch_label("dup_path") == "dup_path"


def test_pool_recover_dedupes_duplicate_pending_paths():
  pending = ["/archive/a/1781085150"] * 8 + ["/archive/a/1781081790"]
  unique, duplicate_n, sample = mph.dedupe_ingest_paths_preserve_order(pending)
  assert duplicate_n == 7
  assert len(unique) == 2
  assert unique[0].endswith("1781085150")
  assert unique[1].endswith("1781081790")
  assert len(unique) == 2


def test_reconcile_full_redispatch_then_recovery_callback(monkeypatch):
  monkeypatch.setattr(mph, "idle_pool_ghost_abort_polls", lambda _n: 1000)
  monkeypatch.setattr(mph, "pool_workers_all_idle", lambda _p: True)
  monkeypatch.setattr(mph, "get_sync_pool_idle_reconcile_max_rounds", lambda: 3)
  monkeypatch.setattr(mph, "get_sync_pool_idle_reconcile_polls_per_round", lambda: 1)
  stuck_pool = _ManualPool()
  recover_calls = {"n": 0}

  def on_recover(pool, pending_paths, pending_async, fn):
    recover_calls["n"] += 1
    assert list(pending_paths) == ["stuck_path"]
    pending_async.clear()
    new_pool = _ManualPool()
    ar = new_pool.apply_async(fn, ("stuck_path",))
    ar.finish()
    pending_async[ar] = "stuck_path"
    return {"pool": new_pool, "collected": []}

  gen = mph.imap_sliding_window_watch_pool(
      stuck_pool,
      lambda path: path,
      ["stuck_path"],
      max_inflight=1,
      poll_timeout_s=0.01,
      stall_abort_polls_fn=lambda in_flight: 100000,
      on_idle_pool_stuck_after_redispatch=on_recover,
  )
  results = list(gen)
  assert results == ["stuck_path"]
  assert recover_calls["n"] == 1


def test_idle_pool_ghost_fatal_sets_taskqueue_dead_cause(monkeypatch):
  monkeypatch.setattr(mph, "idle_pool_ghost_abort_polls", lambda _n: 3)
  monkeypatch.setattr(mph, "pool_workers_all_idle", lambda _p: True)
  monkeypatch.setattr(mph, "get_sync_pool_idle_reconcile_max_rounds", lambda: 1)
  monkeypatch.setattr(mph, "get_sync_pool_idle_reconcile_polls_per_round", lambda: 1)
  pool = _ManualPool()

  def on_recover_fail(pool, pending_paths, pending_async, fn):
    del pool, pending_paths, pending_async, fn
    return {"pool": None, "collected": []}

  gen = mph.imap_sliding_window_watch_pool(
      pool,
      lambda path: path,
      ["ghost_path"],
      max_inflight=1,
      poll_timeout_s=0.01,
      stall_abort_polls_fn=lambda in_flight: 100000,
      on_idle_pool_stuck_after_redispatch=on_recover_fail,
  )
  with pytest.raises(mph.MultiprocessingPoolStallError) as excinfo:
    list(gen)
  assert excinfo.value.exit_code == 124
  assert excinfo.value.likely_cause == mph._IDLE_POOL_TASKQUEUE_DEAD_CAUSE
  assert "idle_pool_taskqueue_dead" in str(excinfo.value)


def test_abort_if_pool_workers_dead_recycle_invokes_idle_reconcile(monkeypatch):
  monkeypatch.setattr(mph, "get_sync_pool_worker_recycle_grace_seconds", lambda: 60.0)
  reconcile_calls = {"n": 0}

  def reconcile_fn():
    reconcile_calls["n"] += 1

  pool = SimpleNamespace(_pool=[_RecycledWorker(), _AliveWorker()])
  ctx = {"idle_reconcile_fn": reconcile_fn}
  mph.abort_if_pool_workers_dead(pool, context="recycle_reconcile_test", pool_health_context=ctx)
  assert reconcile_calls["n"] == 1
  mph.abort_if_pool_workers_dead(pool, context="recycle_reconcile_test", pool_health_context=ctx)
  assert reconcile_calls["n"] == 2
  mph.abort_if_pool_workers_dead(
      pool,
      context="recycle_reconcile_test",
      pool_health_context=ctx,
  )
  assert reconcile_calls["n"] == 3


def test_terminate_pool_bounded_kill_workers_first_before_terminate(monkeypatch):
  """Non-abandon path still calls stdlib terminate after aggressive kill."""
  logs = []
  aggressive_calls = []
  terminate_calls = []

  monkeypatch.setattr(
      mph,
      "log_print",
      lambda msg, flush=False: logs.append(msg),
  )
  monkeypatch.setattr(
      mph,
      "_aggressive_terminate_pool_workers",
      lambda pool, **kwargs: aggressive_calls.append(pool),
  )
  monkeypatch.setattr(
      mph,
      "_wait_pool_processes_bounded",
      lambda pool, timeout_s: (True, []),
  )
  monkeypatch.setattr(
      mph,
      "_reap_pool_worker_pids",
      lambda pool, **kwargs: [],
  )

  class _TermPool:
    _pool = [_AliveWorker()]

    def terminate(self):
      terminate_calls.append(True)

  mph.terminate_pool_bounded(
      _TermPool(),
      context="idle_pool_recover",
      kill_workers_first=True,
      abandon_after_kill=False,
  )
  assert aggressive_calls
  assert terminate_calls
  assert any("pool_recover terminate outcome=all_done" in line for line in logs)


def test_terminate_pool_bounded_abandon_skips_blocking_terminate(monkeypatch):
  """RC-C/W: blocking Pool.terminate() must not hang abandon-pool recover."""
  logs = []
  aggressive_kwargs = []
  terminate_calls = []

  monkeypatch.setattr(
      mph,
      "log_print",
      lambda msg, flush=False: logs.append(msg),
  )
  monkeypatch.setattr(
      mph,
      "_aggressive_terminate_pool_workers",
      lambda pool, **kwargs: aggressive_kwargs.append(dict(kwargs)),
  )
  monkeypatch.setattr(
      mph,
      "reap_zombie_children_of_self",
      lambda **kwargs: None,
  )

  class _HangTerminatePool:
    _pool = [_AliveWorker()]

    def terminate(self):
      terminate_calls.append(True)
      time.sleep(3600)

  started = time.monotonic()
  ok = mph.terminate_pool_bounded(
      _HangTerminatePool(),
      context="idle_pool_recover",
      kill_workers_first=True,
      abandon_after_kill=True,
  )
  elapsed = time.monotonic() - started
  assert ok is True
  assert elapsed < 3.0
  assert terminate_calls == []
  assert aggressive_kwargs
  assert aggressive_kwargs[0].get("sigkill_first") is True
  assert any("outcome=abandoned" in line for line in logs)


def test_recover_wall_raises_stall_not_soft_hang(monkeypatch):
  """RC-F/G/H: recover callback that never returns → exit 124 within wall."""
  monkeypatch.setattr(mph, "IDLE_POOL_RECOVER_WALL_S", 0.3)
  monkeypatch.setattr(mph, "idle_pool_ghost_abort_polls", lambda _n: 1000)
  monkeypatch.setattr(mph, "pool_workers_all_idle", lambda _p: True)
  monkeypatch.setattr(mph, "get_sync_pool_idle_reconcile_max_rounds", lambda: 3)
  monkeypatch.setattr(mph, "get_sync_pool_idle_reconcile_polls_per_round", lambda: 1)
  stuck_pool = _ManualPool()

  def on_recover_hang(pool, pending_paths, pending_async, fn):
    del pool, pending_paths, pending_async, fn
    time.sleep(30)

  gen = mph.imap_sliding_window_watch_pool(
      stuck_pool,
      lambda path: path,
      ["stuck_path"],
      max_inflight=1,
      poll_timeout_s=0.01,
      stall_abort_polls_fn=lambda in_flight: 100000,
      on_idle_pool_stuck_after_redispatch=on_recover_hang,
  )
  started = time.monotonic()
  with pytest.raises(mph.MultiprocessingPoolStallError) as excinfo:
    list(gen)
  elapsed = time.monotonic() - started
  assert elapsed < 5.0
  assert excinfo.value.exit_code == 124
  assert excinfo.value.likely_cause == mph._IDLE_POOL_TASKQUEUE_DEAD_CAUSE
  assert "exceeded wall" in str(excinfo.value)


def test_recover_does_not_clear_pending_before_new_pool_ready(monkeypatch):
  """RC-F: pending_async must remain until recover callback proves new pool."""
  monkeypatch.setattr(mph, "IDLE_POOL_RECOVER_WALL_S", 0.3)
  monkeypatch.setattr(mph, "idle_pool_ghost_abort_polls", lambda _n: 1000)
  monkeypatch.setattr(mph, "pool_workers_all_idle", lambda _p: True)
  monkeypatch.setattr(mph, "get_sync_pool_idle_reconcile_max_rounds", lambda: 3)
  monkeypatch.setattr(mph, "get_sync_pool_idle_reconcile_polls_per_round", lambda: 1)
  stuck_pool = _ManualPool()
  seen = {}

  def on_recover_fail_after_inspect(pool, pending_paths, pending_async, fn):
    del pool, pending_paths, fn
    seen["pending_n"] = len(pending_async)
    seen["paths"] = list(pending_async.values())
    # Simulate probe/respawn failure without clearing pending.
    raise mph.MultiprocessingPoolStallError(
        "replacement ingest pool dispatch_probe failed",
        dead_pids=[],
        context="idle_pool_recover",
        exit_code=124,
        likely_cause=mph._IDLE_POOL_TASKQUEUE_DEAD_CAUSE,
    )

  gen = mph.imap_sliding_window_watch_pool(
      stuck_pool,
      lambda path: path,
      ["stuck_path"],
      max_inflight=1,
      poll_timeout_s=0.01,
      stall_abort_polls_fn=lambda in_flight: 100000,
      on_idle_pool_stuck_after_redispatch=on_recover_fail_after_inspect,
  )
  with pytest.raises(mph.MultiprocessingPoolStallError) as excinfo:
    list(gen)
  assert seen["pending_n"] == 1
  assert seen["paths"] == ["stuck_path"]
  assert excinfo.value.likely_cause == mph._IDLE_POOL_TASKQUEUE_DEAD_CAUSE


def test_maintain_ingest_pool_refuses_swap_while_replacement_lagging(monkeypatch):
  """RC-M: gap>0 must not proactive-swap."""
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_ingest_pool_maxtasksperchild",
      lambda: 0,
  )
  monkeypatch.setattr(mph, "reap_pool_worker_pids", lambda *a, **k: [])
  monkeypatch.setattr(mph, "reap_zombie_children_of_self", lambda **k: None)
  monkeypatch.setattr(mph, "_iter_dead_pool_worker_processes", lambda pool: [])
  monkeypatch.setattr(
      mph,
      "_pool_recycle_gate_metrics",
      lambda *a, **k: {
          "alive": 23,
          "expected_total": 24,
          "materialized": 23,
          "gap": 1,
          "dead_n": 0,
      },
  )
  probe_calls = []
  monkeypatch.setattr(
      mph,
      "probe_ingest_pool_dispatch",
      lambda *a, **k: probe_calls.append(True) or False,
  )
  recreate_calls = []
  pool = SimpleNamespace(_pool=[_AliveWorker()])
  out = mph.maintain_ingest_pool_after_supervisor_retire(
      pool,
      recreate_pool_fn=lambda: recreate_calls.append(True) or object(),
  )
  assert out is pool
  assert probe_calls == []
  assert recreate_calls == []


def test_maintain_ingest_pool_proactive_swap_abandons_old_pool(monkeypatch):
  """RC-N: proactive swap must abandon+kill old pool before recreate."""
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_ingest_pool_maxtasksperchild",
      lambda: 0,
  )
  monkeypatch.setattr(mph, "reap_pool_worker_pids", lambda *a, **k: [])
  monkeypatch.setattr(mph, "reap_zombie_children_of_self", lambda **k: None)
  monkeypatch.setattr(mph, "_iter_dead_pool_worker_processes", lambda pool: [])
  monkeypatch.setattr(
      mph,
      "_pool_recycle_gate_metrics",
      lambda *a, **k: {
          "alive": 24,
          "expected_total": 24,
          "materialized": 24,
          "gap": 0,
          "dead_n": 0,
      },
  )
  monkeypatch.setattr(mph, "probe_ingest_pool_dispatch", lambda *a, **k: False)
  monkeypatch.setattr(mph, "pool_workers_all_idle", lambda _p: True)
  terminate_calls = []

  def fake_terminate(pool, **kwargs):
    terminate_calls.append(dict(kwargs))
    return True

  monkeypatch.setattr(mph, "terminate_pool_bounded", fake_terminate)
  new_pool = object()
  old_pool = SimpleNamespace(_pool=[_AliveWorker()])
  out = mph.maintain_ingest_pool_after_supervisor_retire(
      old_pool,
      recreate_pool_fn=lambda: new_pool,
  )
  assert out is new_pool
  assert terminate_calls
  assert terminate_calls[0].get("abandon_after_kill") is True
  assert terminate_calls[0].get("kill_workers_first") is True
  assert terminate_calls[0].get("context") == "proactive_swap"


def test_probe_ingest_pool_dispatch_success_and_failure():
  class _OkAsync:
    def get(self, timeout=None):
      del timeout
      return True

  class _OkPool:
    def apply_async(self, fn, args):
      del fn, args
      return _OkAsync()

  assert mph.probe_ingest_pool_dispatch(_OkPool(), context="test") is True

  class _FailPool:
    def apply_async(self, fn, args):
      del fn, args
      raise RuntimeError("dead taskqueue")

  assert mph.probe_ingest_pool_dispatch(_FailPool(), context="test") is False


def test_maintain_ingest_pool_after_supervisor_retire_noop_when_maxtasks_positive(
    monkeypatch,
):
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_ingest_pool_maxtasksperchild",
      lambda: 1,
  )
  probe_calls = []
  monkeypatch.setattr(
      mph,
      "probe_ingest_pool_dispatch",
      lambda *a, **k: probe_calls.append(True) or True,
  )
  pool = object()
  assert mph.maintain_ingest_pool_after_supervisor_retire(pool) is pool
  assert probe_calls == []


def test_full_redispatch_thrash_triggers_immediate_recover_same_round(monkeypatch):
  monkeypatch.setattr(mph, "idle_pool_ghost_abort_polls", lambda _n: 1000)
  monkeypatch.setattr(mph, "pool_workers_all_idle", lambda _p: True)
  monkeypatch.setattr(mph, "get_sync_pool_idle_reconcile_max_rounds", lambda: 3)
  monkeypatch.setattr(mph, "get_sync_pool_idle_reconcile_polls_per_round", lambda: 1)
  stuck_pool = _ManualPool()
  recover_calls = {"n": 0}

  def on_recover(pool, pending_paths, pending_async, fn):
    recover_calls["n"] += 1
    pending_async.clear()
    new_pool = _ManualPool()
    ar = new_pool.apply_async(fn, ("stuck_path",))
    ar.finish()
    pending_async[ar] = "stuck_path"
    return {"pool": new_pool, "collected": []}

  gen = mph.imap_sliding_window_watch_pool(
      stuck_pool,
      lambda path: path,
      ["stuck_path"],
      max_inflight=1,
      poll_timeout_s=0.01,
      stall_abort_polls_fn=lambda in_flight: 100000,
      on_idle_pool_stuck_after_redispatch=on_recover,
  )
  results = list(gen)
  assert results == ["stuck_path"]
  assert recover_calls["n"] == 1


def test_idle_pool_recover_skipped_when_skip_fn_returns_reason(monkeypatch):
  """populate_wait skip must prevent recover wall / exit 124."""
  monkeypatch.setattr(mph, "IDLE_POOL_RECOVER_WALL_S", 0.3)
  monkeypatch.setattr(mph, "idle_pool_ghost_abort_polls", lambda _n: 1000)
  monkeypatch.setattr(mph, "pool_workers_all_idle", lambda _p: True)
  monkeypatch.setattr(mph, "get_sync_pool_idle_reconcile_max_rounds", lambda: 3)
  monkeypatch.setattr(mph, "get_sync_pool_idle_reconcile_polls_per_round", lambda: 1)
  recover_calls = {"n": 0}
  logs = []

  def on_recover(pool, pending_paths, pending_async, fn):
    recover_calls["n"] += 1
    del pool, pending_paths, pending_async, fn
    time.sleep(30)

  monkeypatch.setattr(
      mph, "log_print", lambda msg, flush=False: logs.append(msg),
  )

  class _FinishablePool(_ManualPool):
    def apply_async(self, fn, args=()):
      ar = super().apply_async(fn, args)
      self._last_ar = ar
      return ar

  stuck_pool = _FinishablePool()
  polls = {"n": 0}

  def skip_fn(pending_paths):
    del pending_paths
    polls["n"] += 1
    if polls["n"] >= 6 and hasattr(stuck_pool, "_last_ar"):
      stuck_pool._last_ar.finish()
    return "populate_wait day=2026-06-07 reason=populate_wait"

  gen = mph.imap_sliding_window_watch_pool(
      stuck_pool,
      lambda path: path,
      ["stuck_path"],
      max_inflight=1,
      poll_timeout_s=0.01,
      stall_abort_polls_fn=lambda in_flight: 100000,
      on_idle_pool_stuck_after_redispatch=on_recover,
      skip_idle_pool_recover_fn=skip_fn,
  )
  results = list(gen)
  assert results == ["stuck_path"]
  assert recover_calls["n"] == 0
  assert any("pool_recover skipped" in line for line in logs)

