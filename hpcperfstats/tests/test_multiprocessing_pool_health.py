"""Regression tests for multiprocessing pool worker-death detection."""

import multiprocessing
import time
from types import SimpleNamespace

import pytest

from hpcperfstats.dbload import multiprocessing_pool_health as mph


class _DeadWorker:
  pid = 4242

  def is_alive(self):
    return False


class _AliveWorker:
  pid = 4243

  def __init__(self):
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
      "hpcperfstats.conf_parser.get_sync_pool_stall_abort_after_timeouts",
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
      "hpcperfstats.conf_parser.get_sync_pool_stall_abort_after_timeouts",
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
      "hpcperfstats.conf_parser.get_sync_pool_stall_abort_after_timeouts",
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


def test_close_pool_bounded_closes_alive_workers():
  pool = _CloseablePool([_AliveWorker()])
  assert mph.close_pool_bounded(pool, timeout_s=0.1) is True
  assert pool.closed is True
  assert pool.terminated is False
