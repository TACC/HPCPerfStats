"""Detect dead multiprocessing pool workers and fail fast instead of hanging.

Linux OOM kills workers with SIGKILL; the parent can block forever on
``imap_unordered`` or ``AsyncResult.get()`` unless it polls worker liveness.
Spawned workers inherit the same ``setproctitle`` name as the supervisor, so
dmesg ``sync_timedb.py`` lines often refer to a worker, not the parent PID.
"""

from __future__ import annotations

import multiprocessing
import os
import time

from hpcperfstats.print_utils import log_print


class MultiprocessingWorkerExitError(RuntimeError):
  """Raised when a pool worker process is no longer alive."""

  def __init__(self, message, *, dead_pids, context="", exit_code=137):
    super().__init__(message)
    self.dead_pids = tuple(int(p) for p in dead_pids if p is not None)
    self.context = str(context or "")
    self.exit_code = int(exit_code)


def get_sync_pool_poll_timeout_s():
  """Seconds between ``AsyncResult.get`` / ``imap`` progress polls."""
  import hpcperfstats.conf_parser as cfg

  return cfg.get_sync_pool_poll_timeout_s()


def iter_pool_worker_processes(pool):
  """Yield worker ``Process`` objects from a ``multiprocessing.Pool``."""
  if pool is None:
    return
  for proc in list(getattr(pool, "_pool", []) or []):
    if proc is not None:
      yield proc


def dead_pool_worker_pids(pool):
  """Return PIDs of pool workers that are no longer alive."""
  dead = []
  for proc in iter_pool_worker_processes(pool):
    is_alive_fn = getattr(proc, "is_alive", None)
    if callable(is_alive_fn) and not is_alive_fn():
      dead.append(getattr(proc, "pid", None))
  return [pid for pid in dead if pid is not None]


def abort_if_pool_workers_dead(pool, *, context=""):
  """Raise ``MultiprocessingWorkerExitError`` when any pool worker has exited."""
  dead = dead_pool_worker_pids(pool)
  if not dead:
    return
  message = (
      "Multiprocessing pool worker exited unexpectedly (likely OOM/SIGKILL); "
      "dead_pids=%s context=%s"
      % (dead, context or "unknown")
  )
  log_print("ERROR: %s" % message, flush=True)
  raise MultiprocessingWorkerExitError(
      message,
      dead_pids=dead,
      context=context,
      exit_code=137,
  )


def _wait_pool_processes_bounded(active_pool, timeout_s):
  workers = list(iter_pool_worker_processes(active_pool))
  deadline = time.monotonic() + max(0.1, float(timeout_s))
  for proc in workers:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
      break
    try:
      proc.join(timeout=remaining)
    except Exception:
      continue
  alive = [
      getattr(p, "pid", None)
      for p in workers
      if getattr(p, "is_alive", lambda: False)()
  ]
  return len(alive) == 0, alive


def terminate_pool_bounded(active_pool, timeout_s=30.0):
  """Terminate a pool and wait briefly so shutdown does not hang after worker death."""
  if active_pool is None:
    return True
  try:
    active_pool.terminate()
  except Exception:
    pass
  all_done, alive = _wait_pool_processes_bounded(active_pool, timeout_s)
  if not all_done:
    log_print(
        "Pool terminate timeout; lingering_workers=%s" % alive,
        flush=True,
    )
  return all_done


def imap_unordered_watch_pool(
    pool,
    fn,
    iterable,
    *,
    poll_timeout_s=None,
    context="",
):
  """Like ``pool.imap_unordered`` but abort when a worker dies (OOM-safe)."""
  if pool is None:
    return iter(())
  poll_timeout_s = (
      get_sync_pool_poll_timeout_s()
      if poll_timeout_s is None
      else max(0.05, float(poll_timeout_s))
  )
  # Timeout polling on IMapIterator is reliable only with chunksize=1.
  try:
    iterator = pool.imap_unordered(fn, iterable, chunksize=1)
  except TypeError:
    iterator = pool.imap_unordered(fn, iterable)
  iterator_next = getattr(iterator, "next", None)
  if not callable(iterator_next):
    for item in iterator:
      abort_if_pool_workers_dead(pool, context=context)
      yield item
    return
  while True:
    abort_if_pool_workers_dead(pool, context=context)
    try:
      yield iterator_next(timeout=poll_timeout_s)
    except StopIteration:
      break
    except multiprocessing.TimeoutError:
      continue


def async_result_get_watch_pool(
    async_result,
    pool,
    *,
    poll_timeout_s=None,
    context="",
):
  """Like ``AsyncResult.get()`` but abort when a pool worker dies."""
  if async_result is None:
    return None
  poll_timeout_s = (
      get_sync_pool_poll_timeout_s()
      if poll_timeout_s is None
      else max(0.05, float(poll_timeout_s))
  )
  get_fn = getattr(async_result, "get", None)
  if not callable(get_fn):
    return None
  while True:
    abort_if_pool_workers_dead(pool, context=context)
    try:
      try:
        return get_fn(timeout=poll_timeout_s)
      except TypeError:
        # Test doubles and some pool adapters omit timeout= on get().
        return get_fn()
    except multiprocessing.TimeoutError:
      continue
