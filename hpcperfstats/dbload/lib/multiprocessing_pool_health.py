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
import signal
import time

from hpcperfstats.dbload.lib.print_utils import log_print

# Consecutive recycle-grace polls per pool (keyed by ``id(pool)``).
_RECYCLE_GRACE_POLLS_BY_POOL = {}


class MultiprocessingWorkerExitError(RuntimeError):
  """Raised when a pool worker process is no longer alive."""

  def __init__(
      self,
      message,
      *,
      dead_pids,
      context="",
      exit_code=137,
      likely_cause="",
      diagnostics=None,
  ):
    super().__init__(message)
    self.dead_pids = tuple(int(p) for p in dead_pids if p is not None)
    self.context = str(context or "")
    self.exit_code = int(exit_code)
    self.likely_cause = str(likely_cause or "")
    self.diagnostics = dict(diagnostics or {})


class MultiprocessingPoolStallError(MultiprocessingWorkerExitError):
  """Raised when a pool worker is alive but imap progress stalls too long."""


def get_sync_pool_poll_timeout_s():
  """Seconds between ``AsyncResult.get`` / ``imap`` progress polls."""
  import hpcperfstats.dbload.lib.conf_parser as cfg

  return cfg.get_sync_pool_poll_timeout_s()


def get_sync_pool_worker_recycle_grace_polls():
  """Consecutive dead-worker polls to tolerate ``maxtasksperchild`` recycle."""
  import hpcperfstats.dbload.lib.conf_parser as cfg

  return cfg.get_sync_pool_worker_recycle_grace_polls()


def get_sync_pool_idle_reconcile_max_rounds():
  """Redispatch rounds before idle-pool ghost fail-fast."""
  import hpcperfstats.dbload.lib.conf_parser as cfg

  return cfg.get_sync_pool_idle_reconcile_max_rounds()


def get_sync_pool_idle_reconcile_polls_per_round():
  """Idle polls between orphan-async reconcile redispatch rounds."""
  import hpcperfstats.dbload.lib.conf_parser as cfg

  return cfg.get_sync_pool_idle_reconcile_polls_per_round()


_COLLECT_PENDING = object()


def try_collect_async_result(async_result):
  """Collect a finished task even when ``ready()`` is false (orphan async / H1)."""
  get_fn = getattr(async_result, "get", None)
  if not callable(get_fn):
    return _COLLECT_PENDING
  try:
    return get_fn(timeout=0)
  except multiprocessing.TimeoutError:
    return _COLLECT_PENDING
  except TypeError:
    pass
  ready_fn = getattr(async_result, "ready", None)
  if callable(ready_fn) and ready_fn():
    try:
      return get_fn()
    except Exception:
      return _COLLECT_PENDING
  return _COLLECT_PENDING


def reconcile_idle_pending_async(
    pool,
    pending_async,
    fn,
    *,
    apply_async=None,
    resolve_skip_result=None,
    on_redispatch=None,
    allow_redispatch=True,
):
  """Collect orphan async results or redispatch stale entries (H1/H2 recovery).

  Mutates ``pending_async`` in place. Returns ``(collected, redispatched_n)``
  where ``collected`` is a list of ``(path, item)`` tuples.
  """
  apply_async_fn = apply_async or getattr(pool, "apply_async", None)
  if not callable(apply_async_fn):
    return [], 0
  collected = []
  redispatched = 0
  for async_result, path in list(pending_async.items()):
    item = try_collect_async_result(async_result)
    if item is not _COLLECT_PENDING:
      pending_async.pop(async_result, None)
      collected.append((path, item))
      continue
    skip_item = None
    if callable(resolve_skip_result):
      try:
        skip_item = resolve_skip_result(path)
      except Exception:
        skip_item = None
    if skip_item is not None:
      pending_async.pop(async_result, None)
      collected.append((path, skip_item))
      continue
    if not allow_redispatch:
      continue
    pending_async.pop(async_result, None)
    new_async = apply_async_fn(fn, (path,))
    pending_async[new_async] = path
    redispatched += 1
    if callable(on_redispatch):
      try:
        on_redispatch(path)
      except Exception:
        pass
  return collected, redispatched


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


def _iter_dead_pool_worker_processes(pool):
  for proc in iter_pool_worker_processes(pool):
    is_alive_fn = getattr(proc, "is_alive", None)
    if callable(is_alive_fn) and not is_alive_fn():
      yield proc


def alive_pool_worker_count(pool):
  """Return count of pool worker processes still alive."""
  alive = 0
  for proc in iter_pool_worker_processes(pool):
    is_alive_fn = getattr(proc, "is_alive", None)
    if callable(is_alive_fn) and is_alive_fn():
      alive += 1
  return alive


_IDLE_POOL_GHOST_CONTEXT = "idle_pool_ghost_inflight"

_IDLE_WORKER_WCHAN_EXACT = frozenset({
    "futex_wait_queue",
    "futex_wait",
    "pipe_read",
    "do_wait",
    "hrtimer_nanosleep",
    "wait_woken",
})


def read_process_wchan(pid):
  """Return kernel wait channel for ``pid``, or None when unavailable."""
  try:
    with open("/proc/%d/wchan" % int(pid), encoding="ascii") as proc_wchan:
      return proc_wchan.read().strip()
  except OSError:
    return None


def worker_wchan_looks_idle(wchan):
  """True when ``wchan`` indicates a blocked/idle pool worker (Linux)."""
  if not wchan or wchan == "0":
    return False
  if wchan in _IDLE_WORKER_WCHAN_EXACT:
    return True
  if wchan.startswith("futex"):
    return True
  if "pipe_read" in wchan:
    return True
  return False


def pool_workers_all_idle(pool):
  """True when every alive pool worker's wchan looks idle (Linux ``/proc``)."""
  alive = 0
  for proc in iter_pool_worker_processes(pool):
    is_alive_fn = getattr(proc, "is_alive", None)
    if not callable(is_alive_fn) or not is_alive_fn():
      continue
    pid = getattr(proc, "pid", None)
    if pid is None:
      return False
    wchan = read_process_wchan(pid)
    if wchan is None:
      return False
    if not worker_wchan_looks_idle(wchan):
      return False
    alive += 1
  return alive > 0


def format_pool_worker_wchan_sample(pool, *, limit=5):
  """Return ``pid:wchan`` strings for up to ``limit`` alive pool workers."""
  entries = []
  for proc in iter_pool_worker_processes(pool):
    if len(entries) >= max(1, int(limit)):
      break
    is_alive_fn = getattr(proc, "is_alive", None)
    if not callable(is_alive_fn) or not is_alive_fn():
      continue
    pid = getattr(proc, "pid", None)
    if pid is None:
      continue
    wchan = read_process_wchan(pid)
    entries.append("%s:%s" % (pid, wchan if wchan is not None else "?"))
  return entries


def idle_pool_ghost_abort_polls(stall_abort_after):
  """Poll count before idle-pool ghost fail-fast (much shorter than giant defer)."""
  return max(12, min(120, max(1, int(stall_abort_after)) // 20))


def _process_exitcode_signal_name(exitcode):
  if exitcode is None:
    return "unknown"
  if exitcode == 0:
    return "none"
  if exitcode < 0:
    try:
      return signal.Signals(-exitcode).name
    except (ValueError, AttributeError):
      return "SIG%d" % (-exitcode)
  return "exit_%d" % exitcode


def _infer_likely_cause(dead_workers, cgroup_events):
  if not dead_workers:
    return "unknown"
  oom_kill = int(cgroup_events.get("oom_kill", 0) or 0)
  exitcodes = [worker.get("exitcode") for worker in dead_workers]
  if all(code == 0 for code in exitcodes):
    return "recycle"
  if any(code == -9 for code in exitcodes):
    return "sigkill" if oom_kill > 0 else "sigkill_non_cgroup"
  if any(code is not None and code > 0 for code in exitcodes):
    return "worker_exception"
  return "unknown"


def _is_maxtasksperchild_recycle_in_progress(pool, dead_procs):
  """True when dead workers look like normal ``maxtasksperchild`` replacement."""
  if not dead_procs:
    return False
  workers = list(iter_pool_worker_processes(pool))
  total = len(workers)
  alive = alive_pool_worker_count(pool)
  if alive <= 0 or alive < total - len(dead_procs):
    return False
  for proc in dead_procs:
    if getattr(proc, "exitcode", None) != 0:
      return False
  return True


def describe_dead_pool_workers(pool, *, pool_health_context=None):
  """Build operator-facing diagnostics for dead pool workers."""
  from hpcperfstats.dbload.lib.process_memory import (
      format_tree_rss_breakdown_mb,
      read_cgroup_memory_current_bytes,
      read_cgroup_memory_events,
      read_cgroup_memory_max_bytes,
  )

  ctx = pool_health_context or {}
  ingest_pool = ctx.get("ingest_pool")
  archive_pool = ctx.get("archive_pool")
  in_flight_sample = list(ctx.get("in_flight_sample") or ())

  dead_workers = []
  for proc in _iter_dead_pool_worker_processes(pool):
    pid = getattr(proc, "pid", None)
    exitcode = getattr(proc, "exitcode", None)
    dead_workers.append({
        "pid": pid,
        "exitcode": exitcode,
        "signal": _process_exitcode_signal_name(exitcode),
    })

  alive = alive_pool_worker_count(pool)
  total = alive + len(dead_workers)
  cgroup_events = read_cgroup_memory_events()
  cgroup_current = read_cgroup_memory_current_bytes()
  cgroup_max = read_cgroup_memory_max_bytes()
  tree = format_tree_rss_breakdown_mb(ingest_pool, archive_pool)
  likely_cause = _infer_likely_cause(dead_workers, cgroup_events)

  diagnostics = {
      "dead_workers": dead_workers,
      "alive_workers": alive,
      "total_workers": total,
      "cgroup_oom_kill": int(cgroup_events.get("oom_kill", 0) or 0),
      "cgroup_memory_current_mb": cgroup_current / (1024.0 * 1024.0),
      "cgroup_memory_max_mb": (
          None if cgroup_max is None else cgroup_max / (1024.0 * 1024.0)
      ),
      "tree_total_mb": tree.get("tree_total_mb"),
      "supervisor_mb": tree.get("supervisor_mb"),
      "ingest_pool_mb": tree.get("ingest_pool_mb"),
      "archive_pool_mb": tree.get("archive_pool_mb"),
      "in_flight_sample": in_flight_sample,
      "likely_cause": likely_cause,
  }
  return diagnostics


def _format_pool_worker_death_diagnostics(context, diagnostics):
  dead_workers = diagnostics.get("dead_workers") or []
  dead_pids = [w.get("pid") for w in dead_workers if w.get("pid") is not None]
  return (
      "context=%s likely_cause=%s dead_pids=%s dead_workers=%s "
      "alive_workers=%s/%s cgroup_oom_kill=%s "
      "cgroup_memory_current_mb=%.1f cgroup_memory_max_mb=%s "
      "tree_total_mb=%.1f in_flight_sample=%s"
      % (
          context or "unknown",
          diagnostics.get("likely_cause") or "unknown",
          dead_pids,
          dead_workers,
          diagnostics.get("alive_workers"),
          diagnostics.get("total_workers"),
          diagnostics.get("cgroup_oom_kill"),
          float(diagnostics.get("cgroup_memory_current_mb") or 0.0),
          diagnostics.get("cgroup_memory_max_mb"),
          float(diagnostics.get("tree_total_mb") or 0.0),
          diagnostics.get("in_flight_sample") or [],
      )
  )


def _reset_recycle_grace(pool):
  _RECYCLE_GRACE_POLLS_BY_POOL.pop(id(pool), None)


def abort_if_pool_workers_dead(pool, *, context="", pool_health_context=None):
  """Raise ``MultiprocessingWorkerExitError`` when any pool worker has exited."""
  dead_procs = list(_iter_dead_pool_worker_processes(pool))
  if not dead_procs:
    _reset_recycle_grace(pool)
    return

  diagnostics = describe_dead_pool_workers(
      pool,
      pool_health_context=pool_health_context,
  )
  dead = [w.get("pid") for w in diagnostics.get("dead_workers") or () if w.get("pid")]

  if _is_maxtasksperchild_recycle_in_progress(pool, dead_procs):
    reconcile_fn = (pool_health_context or {}).get("idle_reconcile_fn")
    if callable(reconcile_fn):
      try:
        reconcile_fn()
      except Exception:
        pass
    # Reap exitcode-0 recycled workers every grace poll so zombies do not
    # accumulate under the supervisor while replacements start.
    grace_ctx = context or "recycle_grace"
    reap_pool_worker_pids(pool, context=grace_ctx)
    reap_zombie_children_of_self(context=grace_ctx)
    warn_unreaped_zombie_children(context=grace_ctx)
    grace_limit = get_sync_pool_worker_recycle_grace_polls()
    pool_key = id(pool)
    grace_used = _RECYCLE_GRACE_POLLS_BY_POOL.get(pool_key, 0) + 1
    if grace_used <= grace_limit:
      _RECYCLE_GRACE_POLLS_BY_POOL[pool_key] = grace_used
      log_print(
          "INFO: pool worker recycle in progress %s grace_poll=%d/%d"
          % (
              _format_pool_worker_death_diagnostics(context, diagnostics),
              grace_used,
              grace_limit,
          ),
          flush=True,
      )
      return

  _reset_recycle_grace(pool)
  message = (
      "Multiprocessing pool worker no longer alive; "
      "dead_pids=%s context=%s"
      % (dead, context or "unknown")
  )
  log_print(
      "ERROR: pool worker death diagnostics: %s"
      % _format_pool_worker_death_diagnostics(context, diagnostics),
      flush=True,
  )
  log_print("ERROR: %s" % message, flush=True)
  raise MultiprocessingWorkerExitError(
      message,
      dead_pids=dead,
      context=context,
      exit_code=137,
      likely_cause=diagnostics.get("likely_cause") or "unknown",
      diagnostics=diagnostics,
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


def _waitpid_pid_nonblocking(pid, *, timeout_s=0.5):
  """Return True when ``pid`` was reaped (or already gone)."""
  try:
    waited_pid, _status = os.waitpid(int(pid), os.WNOHANG)
    if waited_pid == int(pid):
      return True
  except ChildProcessError:
    return True
  except OSError:
    return False
  deadline = time.monotonic() + max(0.0, float(timeout_s))
  while time.monotonic() < deadline:
    try:
      waited_pid, _status = os.waitpid(int(pid), os.WNOHANG)
      if waited_pid == int(pid):
        return True
    except ChildProcessError:
      return True
    except OSError:
      return False
    time.sleep(0.05)
  return False


def _reap_pool_worker_pids(pool, *, timeout_s=5.0, context=""):
  """Reap terminated pool workers so zombies do not accumulate under the supervisor."""
  if pool is None:
    return []
  # Prefer Process.join so multiprocessing updates internal state first.
  for proc in list(_iter_dead_pool_worker_processes(pool)):
    try:
      proc.join(timeout=0)
    except Exception:
      pass
  pids = [
      getattr(proc, "pid", None)
      for proc in iter_pool_worker_processes(pool)
      if getattr(proc, "pid", None) is not None
      and not getattr(proc, "is_alive", lambda: True)()
  ]
  if not pids:
    return []
  deadline = time.monotonic() + max(0.1, float(timeout_s))
  reaped = []
  for pid in pids:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
      break
    if _waitpid_pid_nonblocking(pid, timeout_s=min(remaining, 0.5)):
      reaped.append(int(pid))
  if reaped:
    log_print(
        "Pool worker reap context=%s pids=%s"
        % (context or "pool", reaped),
        flush=True,
    )
  return reaped


def reap_pool_worker_pids(pool, *, timeout_s=5.0, context=""):
  """Public wrapper: reap dead workers still listed on ``pool._pool``."""
  return _reap_pool_worker_pids(pool, timeout_s=timeout_s, context=context)


def _process_stat_is_zombie(pid):
  """Return True when ``/proc/<pid>/stat`` reports state ``Z``."""
  try:
    with open("/proc/%d/stat" % int(pid), "r", encoding="ascii") as proc_stat:
      stat_line = proc_stat.read()
  except OSError:
    return False
  rparen = stat_line.rfind(")")
  if rparen < 0 or rparen + 2 >= len(stat_line):
    return False
  return stat_line[rparen + 2] == "Z"


def _iter_zombie_child_pids():
  """Yield PIDs of direct children of this process that are zombies."""
  self_pid = os.getpid()
  try:
    entries = os.listdir("/proc")
  except OSError:
    return
  for name in entries:
    if not name.isdigit():
      continue
    pid = int(name)
    if pid == self_pid:
      continue
    try:
      with open("/proc/%d/stat" % pid, "r", encoding="ascii") as proc_stat:
        stat_line = proc_stat.read()
    except OSError:
      continue
    rparen = stat_line.rfind(")")
    if rparen < 0 or rparen + 2 >= len(stat_line):
      continue
    # fields after ") ": state ppid ...
    rest = stat_line[rparen + 2 :].split()
    if len(rest) < 2:
      continue
    if rest[0] != "Z":
      continue
    try:
      ppid = int(rest[1])
    except (TypeError, ValueError):
      continue
    if ppid == self_pid:
      yield pid


def reap_zombie_children_of_self(*, context=""):
  """PID-specific waitpid for zombie children (not in pool._pool).

  Prefer this over ``waitpid(-1)`` so live Pool/Manager waits are not stolen.
  """
  reaped = []
  for pid in _iter_zombie_child_pids():
    if _waitpid_pid_nonblocking(pid, timeout_s=0.5):
      reaped.append(int(pid))
  if reaped:
    log_print(
        "Zombie child reap context=%s pids=%s"
        % (context or "supervisor", reaped),
        flush=True,
    )
  return reaped


def warn_unreaped_zombie_children(*, context=""):
  """Log WARN when direct zombie children remain after a reap attempt."""
  zombies = list(_iter_zombie_child_pids())
  if not zombies:
    return
  sample = zombies[:8]
  log_print(
      "WARN: unreaped zombie children context=%s count=%d sample_pids=%s"
      % (context or "supervisor", len(zombies), sample),
      flush=True,
  )


def _sigkill_pool_worker_pids(pids, *, context=""):
  killed = []
  for pid in pids or ():
    if pid is None:
      continue
    try:
      os.kill(int(pid), signal.SIGKILL)
      killed.append(int(pid))
    except OSError:
      continue
  if killed:
    log_print(
        "Pool terminate SIGKILL context=%s pids=%s"
        % (context or "pool", killed),
        flush=True,
    )
  for pid in killed:
    try:
      os.waitpid(int(pid), os.WNOHANG)
    except (ChildProcessError, OSError):
      pass
  # Blocking reap for defunct children SIGKILL may leave until waitpid runs.
  deadline = time.monotonic() + 2.0
  for pid in killed:
    while time.monotonic() < deadline:
      try:
        waited_pid, _status = os.waitpid(int(pid), os.WNOHANG)
        if waited_pid == int(pid):
          break
      except ChildProcessError:
        break
      except OSError:
        break
      time.sleep(0.05)


def terminate_pool_bounded(active_pool, timeout_s=30.0, *, context=""):
  """Terminate a pool and wait briefly so shutdown does not hang after worker death."""
  if active_pool is None:
    return True
  alive_before = alive_pool_worker_count(active_pool)
  try:
    active_pool.terminate()
  except Exception:
    pass
  log_print(
      "Pool workers terminated context=%s workers_before=%d"
      % (context or "pool", alive_before),
      flush=True,
  )
  all_done, alive = _wait_pool_processes_bounded(active_pool, timeout_s)
  if not all_done:
    log_print(
        "Pool terminate timeout; lingering_workers=%s" % alive,
        flush=True,
    )
    _sigkill_pool_worker_pids(alive, context=context)
    all_done, alive = _wait_pool_processes_bounded(active_pool, timeout_s)
    if not all_done and alive:
      log_print(
          "Pool terminate still lingering after SIGKILL context=%s workers=%s"
          % (context or "pool", alive),
          flush=True,
      )
  _reap_pool_worker_pids(active_pool, timeout_s=min(5.0, float(timeout_s)), context=context)
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
    stall_abort_after_timeouts=None,
    context="",
    on_stall_warning=None,
    on_stall_poll=None,
    on_stall_fatal_summary=None,
    pool_health_context=None,
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
  health_ctx = dict(pool_health_context or ())
  if health_ctx.get("active_pool") is None:
    health_ctx["active_pool"] = pool

  def _abort_pool_health():
    ctx = dict(health_ctx)
    if ctx.get("in_flight_sample") is None:
      sample_fn = ctx.get("in_flight_sample_fn")
      if callable(sample_fn):
        ctx["in_flight_sample"] = sample_fn()
    abort_if_pool_workers_dead(
        pool,
        context=context,
        pool_health_context=ctx,
    )

  if not callable(iterator_next):
    for item in iterator:
      _abort_pool_health()
      yield item
    return
  import hpcperfstats.dbload.lib.conf_parser as cfg

  if stall_abort_after_timeouts is None:
    stall_abort_after = cfg.get_sync_pool_stall_abort_after_timeouts()
  else:
    stall_abort_after = max(1, int(stall_abort_after_timeouts))
  warn_thresholds = _stall_warning_thresholds(stall_abort_after)
  warned_thresholds = set()
  consecutive_timeouts = 0
  while True:
    _abort_pool_health()
    try:
      item = iterator_next(timeout=poll_timeout_s)
    except StopIteration:
      break
    except multiprocessing.TimeoutError:
      deferred = False
      if on_stall_poll is not None:
        ctx = dict(health_ctx)
        if ctx.get("in_flight_sample") is None:
          sample_fn = ctx.get("in_flight_sample_fn")
          if callable(sample_fn):
            ctx["in_flight_sample"] = sample_fn()
        try:
          deferred = bool(on_stall_poll(consecutive_timeouts, context, ctx))
        except Exception:
          deferred = False
      if deferred:
        consecutive_timeouts = 0
      else:
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
        fatal_extra = ""
        if on_stall_fatal_summary is not None:
          try:
            fatal_extra = on_stall_fatal_summary(
                consecutive_timeouts,
                stall_abort_after,
                poll_timeout_s,
                context,
            ) or ""
          except Exception:
            fatal_extra = ""
        log_print("ERROR: %s%s" % (message, fatal_extra), flush=True)
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


def imap_sliding_window_watch_pool(
    pool,
    fn,
    paths,
    *,
    max_inflight,
    poll_timeout_s=None,
    stall_abort_polls_fn=None,
    context="",
    on_stall_warning=None,
    on_stall_poll=None,
    on_stall_fatal_summary=None,
    pool_health_context=None,
    on_in_flight_change=None,
    supplement_paths_fn=None,
    on_idle_pool_ghost_fatal=None,
    on_reconcile_redispatch=None,
    resolve_reconcile_skip_result=None,
):
  """Dispatch pool work with at most ``max_inflight`` concurrent ``apply_async`` tasks.

  Refills idle worker slots from ``paths`` in FIFO order (sliding window). When the
  primary ``paths`` iterator is exhausted, optional ``supplement_paths_fn`` may return
  additional paths (giant pool supplement). Stall abort threshold is recomputed from
  the current in-flight path set on each poll when ``stall_abort_polls_fn`` is provided.
  """
  if pool is None:
    return iter(())
  path_list = list(paths or ())
  if not path_list:
    return iter(())
  poll_timeout_s = (
      get_sync_pool_poll_timeout_s()
      if poll_timeout_s is None
      else max(0.05, float(poll_timeout_s))
  )
  max_inflight = max(1, int(max_inflight))
  health_ctx = dict(pool_health_context or ())
  if health_ctx.get("active_pool") is None:
    health_ctx["active_pool"] = pool

  def _abort_pool_health():
    ctx = dict(health_ctx)
    if ctx.get("in_flight_sample") is None:
      sample_fn = ctx.get("in_flight_sample_fn")
      if callable(sample_fn):
        ctx["in_flight_sample"] = sample_fn()
    abort_if_pool_workers_dead(
        pool,
        context=context,
        pool_health_context=ctx,
    )

  import hpcperfstats.dbload.lib.conf_parser as cfg

  default_stall_abort = cfg.get_sync_pool_stall_abort_after_timeouts()
  path_iter = iter(path_list)
  pending_async = {}
  consecutive_timeouts = 0
  polls_since_last_yield = 0
  idle_pool_warned = False
  idle_reconcile_rounds = 0
  idle_polls_since_reconcile = 0
  reconcile_pending_yields = []
  max_reconcile_rounds = get_sync_pool_idle_reconcile_max_rounds()
  polls_per_reconcile_round = get_sync_pool_idle_reconcile_polls_per_round()
  warned_thresholds = set()
  stall_abort_after = max(1, int(default_stall_abort))
  warn_thresholds = _stall_warning_thresholds(stall_abort_after)

  def _in_flight_paths():
    return list(pending_async.values())

  def _update_stall_abort_from_in_flight():
    nonlocal stall_abort_after, warn_thresholds
    in_flight = _in_flight_paths()
    if callable(stall_abort_polls_fn):
      if in_flight:
        stall_abort_after = max(1, int(stall_abort_polls_fn(in_flight)))
      else:
        stall_abort_after = max(1, int(default_stall_abort))
    warn_thresholds = _stall_warning_thresholds(stall_abort_after)
    if callable(on_in_flight_change):
      on_in_flight_change(in_flight)

  def _submit_until_cap():
    apply_async = getattr(pool, "apply_async", None)
    if not callable(apply_async):
      raise RuntimeError("pool missing apply_async for sliding window dispatch")
    while len(pending_async) < max_inflight:
      try:
        path = next(path_iter)
      except StopIteration:
        if not callable(supplement_paths_fn):
          break
        slots_needed = max_inflight - len(pending_async)
        if slots_needed <= 0:
          break
        in_flight = _in_flight_paths()
        try:
          supplement_paths = list(
              supplement_paths_fn(slots_needed, in_flight) or (),
          )
        except Exception:
          supplement_paths = []
        if not supplement_paths:
          break
        path = supplement_paths.pop(0)
        for extra_path in supplement_paths:
          if len(pending_async) >= max_inflight:
            break
          if not extra_path:
            continue
          extra_async = apply_async(fn, (extra_path,))
          pending_async[extra_async] = extra_path
      else:
        async_result = apply_async(fn, (path,))
        pending_async[async_result] = path
        continue
      async_result = apply_async(fn, (path,))
      pending_async[async_result] = path

  def _handle_stall_poll():
    nonlocal consecutive_timeouts
    deferred = False
    if on_stall_poll is not None:
      ctx = dict(health_ctx)
      if ctx.get("in_flight_sample") is None:
        sample_fn = ctx.get("in_flight_sample_fn")
        if callable(sample_fn):
          ctx["in_flight_sample"] = sample_fn()
      try:
        deferred = bool(on_stall_poll(consecutive_timeouts, context, ctx))
      except Exception:
        deferred = False
    if deferred:
      consecutive_timeouts = 0
    else:
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
      fatal_extra = ""
      if on_stall_fatal_summary is not None:
        try:
          fatal_extra = on_stall_fatal_summary(
              consecutive_timeouts,
              stall_abort_after,
              poll_timeout_s,
              context,
          ) or ""
        except Exception:
          fatal_extra = ""
      log_print("ERROR: %s%s" % (message, fatal_extra), flush=True)
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

  def _attempt_idle_reconcile(*, queue_yields=False):
    nonlocal idle_reconcile_rounds, idle_polls_since_reconcile
    nonlocal consecutive_timeouts, polls_since_last_yield, idle_pool_warned
    if not pending_async or not pool_workers_all_idle(pool):
      idle_polls_since_reconcile = 0
      return []
    collected, redispatched = reconcile_idle_pending_async(
        pool,
        pending_async,
        fn,
        resolve_skip_result=resolve_reconcile_skip_result,
        on_redispatch=on_reconcile_redispatch,
        allow_redispatch=idle_reconcile_rounds < max_reconcile_rounds,
    )
    idle_polls_since_reconcile = 0
    if collected:
      consecutive_timeouts = 0
      polls_since_last_yield = 0
      idle_pool_warned = False
      idle_reconcile_rounds = 0
      if queue_yields:
        reconcile_pending_yields.extend(collected)
      _submit_until_cap()
      _update_stall_abort_from_in_flight()
      return collected
    if redispatched > 0:
      idle_reconcile_rounds += 1
      pending_sample = [
          os.path.basename(str(path))
          for path in _in_flight_paths()[:5]
          if path
      ]
      log_print(
          "INFO: pool imap idle reconcile redispatch round=%d/%d "
          "redispatched_n=%d pending_async_n=%d pending_sample=%s context=%s"
          % (
              int(idle_reconcile_rounds),
              int(max_reconcile_rounds),
              int(redispatched),
              len(pending_async),
              pending_sample,
              context or "pool",
          ),
          flush=True,
      )
      consecutive_timeouts = 0
      polls_since_last_yield = 0
      _submit_until_cap()
      _update_stall_abort_from_in_flight()
    return []

  def _idle_reconcile_for_recycle():
    _attempt_idle_reconcile(queue_yields=True)

  health_ctx["idle_reconcile_fn"] = _idle_reconcile_for_recycle

  def _check_idle_pool_ghost():
    nonlocal idle_pool_warned
    if not pending_async:
      return
    if not pool_workers_all_idle(pool):
      return
    ghost_abort_polls = idle_pool_ghost_abort_polls(stall_abort_after)
    pending_paths = _in_flight_paths()
    pending_sample = [
        os.path.basename(str(path))
        for path in pending_paths[:5]
        if path
    ]
    wchan_sample = format_pool_worker_wchan_sample(pool)
    warn_after = max(3, ghost_abort_polls // 4)
    if (
        polls_since_last_yield >= warn_after
        and not idle_pool_warned
    ):
      idle_pool_warned = True
      log_print(
          "WARN: pool imap waiting pending_async_n=%d workers_idle=yes "
          "polls_since_yield=%d pending_sample=%s worker_wchan_sample=%s "
          "context=%s"
          % (
              len(pending_async),
              int(polls_since_last_yield),
              pending_sample,
              wchan_sample,
              context or "pool",
          ),
          flush=True,
      )
    if polls_since_last_yield < ghost_abort_polls:
      return
    if idle_reconcile_rounds < max_reconcile_rounds:
      return
    message = (
        "pool imap idle workers with pending async "
        "(context=%s pending_async_n=%d polls_since_yield=%d "
        "pending_sample=%s worker_wchan_sample=%s)"
        % (
            _IDLE_POOL_GHOST_CONTEXT,
            len(pending_async),
            int(polls_since_last_yield),
            pending_sample,
            wchan_sample,
        )
    )
    log_print("ERROR: %s" % message, flush=True)
    if callable(on_idle_pool_ghost_fatal):
      try:
        on_idle_pool_ghost_fatal(list(pending_paths))
      except Exception:
        pass
    raise MultiprocessingPoolStallError(
        message,
        dead_pids=[],
        context=_IDLE_POOL_GHOST_CONTEXT,
        exit_code=124,
    )

  _submit_until_cap()
  _update_stall_abort_from_in_flight()

  while pending_async:
    if reconcile_pending_yields:
      path, item = reconcile_pending_yields.pop(0)
      del path
      consecutive_timeouts = 0
      polls_since_last_yield = 0
      idle_pool_warned = False
      idle_reconcile_rounds = 0
      idle_polls_since_reconcile = 0
      yield item
      continue
    _abort_pool_health()
    _update_stall_abort_from_in_flight()
    orphan_collected = []
    if pool_workers_all_idle(pool):
      for async_result in list(pending_async):
        item = try_collect_async_result(async_result)
        if item is _COLLECT_PENDING:
          continue
        path = pending_async.pop(async_result)
        orphan_collected.append((path, item))
    if orphan_collected:
      for _path, item in orphan_collected:
        consecutive_timeouts = 0
        polls_since_last_yield = 0
        idle_pool_warned = False
        idle_reconcile_rounds = 0
        idle_polls_since_reconcile = 0
        _submit_until_cap()
        _update_stall_abort_from_in_flight()
        yield item
      continue
    completed = [
        async_result
        for async_result in list(pending_async)
        if getattr(async_result, "ready", lambda: False)()
    ]
    if completed:
      for async_result in completed:
        path = pending_async.pop(async_result)
        get_fn = getattr(async_result, "get", None)
        if not callable(get_fn):
          raise RuntimeError("async result missing get()")
        try:
          item = get_fn(timeout=0)
        except TypeError:
          item = get_fn()
        consecutive_timeouts = 0
        polls_since_last_yield = 0
        idle_pool_warned = False
        _submit_until_cap()
        _update_stall_abort_from_in_flight()
        yield item
      continue
    if pool_workers_all_idle(pool) and pending_async:
      idle_polls_since_reconcile += 1
      if idle_polls_since_reconcile >= polls_per_reconcile_round:
        reconciled = _attempt_idle_reconcile(queue_yields=False)
        if reconciled:
          consecutive_timeouts = 0
          polls_since_last_yield = 0
          idle_pool_warned = False
          idle_reconcile_rounds = 0
          idle_polls_since_reconcile = 0
          _submit_until_cap()
          _update_stall_abort_from_in_flight()
          for _path, item in reconciled:
            yield item
          continue
    _handle_stall_poll()
    polls_since_last_yield += 1
    _check_idle_pool_ghost()
    time.sleep(poll_timeout_s)


def async_result_get_watch_pool(
    async_result,
    pool,
    *,
    poll_timeout_s=None,
    context="",
    pool_health_context=None,
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
    abort_if_pool_workers_dead(
        pool,
        context=context,
        pool_health_context=pool_health_context,
    )
    try:
      try:
        return get_fn(timeout=poll_timeout_s)
      except TypeError:
        # Test doubles and some pool adapters omit timeout= on get().
        return get_fn()
    except multiprocessing.TimeoutError:
      continue


def hard_exit_pool_worker_error(exc: MultiprocessingWorkerExitError) -> None:
  """Exit immediately after pool worker failure (do not wait on helper threads).

  ``sys.exit`` can block while non-daemon threads (for example async DAY_CLOSE
  seal) finish; stall/OOM exit handlers must use ``os._exit`` instead.
  """
  likely_cause = getattr(exc, "likely_cause", "") or ""
  diagnostics = getattr(exc, "diagnostics", None) or {}
  extra = ""
  if likely_cause or diagnostics:
    extra = " " + _format_pool_worker_death_diagnostics(
        getattr(exc, "context", "") or "unknown",
        diagnostics,
    )
  log_print(
      "Pool worker exit: hard exit code=%d context=%s likely_cause=%s%s"
      % (
          exc.exit_code,
          exc.context or "unknown",
          likely_cause or "unknown",
          extra,
      ),
      flush=True,
  )
  os._exit(int(exc.exit_code))


def sync_timedb_spawn_pool_recycle_kwargs() -> dict:
  """Return ``maxtasksperchild`` kwargs for sync_timedb spawn pools when configured."""
  import hpcperfstats.dbload.lib.conf_parser as cfg

  maxtasks = cfg.get_sync_ingest_pool_maxtasksperchild()
  if maxtasks > 0:
    return {"maxtasksperchild": int(maxtasks)}
  return {}


def create_sync_timedb_spawn_pool(
    *,
    processes,
    initializer,
    initargs,
    pool_kind_log_label=None,
):
  """Create a spawn-context ``Pool`` with shared sync_timedb recycle kwargs."""
  del pool_kind_log_label
  return multiprocessing.get_context("spawn").Pool(
      processes=processes,
      initializer=initializer,
      initargs=initargs,
      **sync_timedb_spawn_pool_recycle_kwargs(),
  )
