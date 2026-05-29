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

  def is_alive(self):
    return True


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
