"""Detect dead multiprocessing pool workers and fail fast instead of hanging.

Linux OOM can kill either the supervisor or a pool worker. When a **worker** dies
first, the parent must poll liveness (``abort_if_pool_workers_dead``) or block
forever on ``imap_unordered`` / ``AsyncResult.get()``. When the **supervisor**
is SIGKILL'd first, spawn workers without parent-death handling become orphans;
``apply_pool_worker_process_title`` sets ``PR_SET_PDEATHSIG`` (SIGKILL) on Linux
so the full ``sync_timedb`` tree exits and supervisord can restart cleanly.

Spawned workers should use distinct ``setproctitle`` names such as
``sync_timedb.py [worker:ingest-pool]`` so ``top``/``ps`` and kernel OOM logs
can be matched to the pool kind, not confused with the ``[main]`` supervisor.
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


class MultiprocessingPoolStallError(MultiprocessingWorkerExitError):
  """Raised when a pool worker is alive but imap progress stalls too long."""


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
      "Multiprocessing pool worker no longer alive; "
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


def close_pool_bounded(active_pool, timeout_s=30.0, *, force_terminate=False):
  """Close a pool with a bounded join; terminate when workers already exited."""
  if active_pool is None:
    return True
  if force_terminate or dead_pool_worker_pids(active_pool):
    return terminate_pool_bounded(active_pool, timeout_s)
  try:
    active_pool.close()
  except Exception:
    pass
  all_done, alive = _wait_pool_processes_bounded(active_pool, timeout_s)
  if not all_done:
    log_print(
        "Pool close join timeout; terminating lingering_workers=%s" % alive,
        flush=True,
    )
    return terminate_pool_bounded(active_pool, timeout_s)
  return all_done


def _stall_warning_thresholds(stall_abort_after):
  """50% and 75% poll-timeout counts for one-shot stall warnings."""
  abort_after = max(1, int(stall_abort_after))
  return (
      max(1, abort_after // 2),
      max(1, (abort_after * 3) // 4),
  )


def imap_unordered_watch_pool(
    pool,
    fn,
    iterable,
    *,
    poll_timeout_s=None,
    context="",
    on_stall_warning=None,
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
  import hpcperfstats.conf_parser as cfg

  stall_abort_after = cfg.get_sync_pool_stall_abort_after_timeouts()
  warn_thresholds = _stall_warning_thresholds(stall_abort_after)
  warned_thresholds = set()
  consecutive_timeouts = 0
  while True:
    abort_if_pool_workers_dead(pool, context=context)
    try:
      item = iterator_next(timeout=poll_timeout_s)
    except StopIteration:
      break
    except multiprocessing.TimeoutError:
      consecutive_timeouts += 1
      for threshold in warn_thresholds:
        if (
            consecutive_timeouts >= threshold
            and threshold not in warned_thresholds
            and on_stall_warning is not None
        ):
          warned_thresholds.add(threshold)
          on_stall_warning(
              consecutive_timeouts,
              stall_abort_after,
              poll_timeout_s,
              context,
          )
      if consecutive_timeouts >= stall_abort_after:
        estimated_stall_s = consecutive_timeouts * poll_timeout_s
        message = (
            "Pool imap stalled after %d consecutive poll timeouts "
            "(context=%s poll_timeout_s=%.3f estimated_stall_s=%.1f)"
            % (
                consecutive_timeouts,
                context or "pool",
                poll_timeout_s,
                estimated_stall_s,
            )
        )
        log_print("ERROR: %s" % message, flush=True)
        if on_stall_warning is not None:
          on_stall_warning(
              consecutive_timeouts,
              stall_abort_after,
              poll_timeout_s,
              context,
          )
        raise MultiprocessingPoolStallError(
            message,
            dead_pids=[],
            context=context,
            exit_code=124,
        )
      continue
    consecutive_timeouts = 0
    yield item


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
