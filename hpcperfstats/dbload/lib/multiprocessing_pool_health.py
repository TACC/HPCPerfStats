"""
Detect dead multiprocessing pool workers and fail fast instead of hanging.

Linux OOM can kill either the supervisor or a pool worker. When a **worker**
dies first, the parent must poll liveness (``abort_if_pool_workers_dead``) or
block forever on ``imap_unordered`` / ``AsyncResult.get()``. When the
**supervisor** is SIGKILL'd first, spawn workers without parent-death handling
become orphans; ``apply_pool_worker_process_title`` sets ``PR_SET_PDEATHSIG``
(SIGKILL) on Linux so the full ``sync_timedb`` tree exits and supervisord can
restart cleanly.

Spawned workers should use distinct ``setproctitle`` names such as
``sync_timedb.py [worker:ingest-pool]`` so ``top``/``ps`` and kernel OOM logs
can be matched to the pool kind, not confused with the ``[main]`` supervisor.

Attributes:
  IDLE_POOL_RECOVER_MAX: Attribute.
  IDLE_POOL_RECOVER_WALL_S: Attribute.
  IDLE_POOL_UNHEALED_RECOVER_MAX: Attribute.
  _COLD_SYNC_TIMEDB_POOL_MAXTASKSPERCHILD: Attribute.
  _COLLECT_PENDING: Attribute.
  _IDLE_POOL_GHOST_CONTEXT: Attribute.
  _IDLE_POOL_TASKQUEUE_DEAD_CAUSE: Attribute.
  _IDLE_WORKER_WCHAN_EXACT: Attribute.
  _INGEST_POOL_KIND_LOG_LABEL: Attribute.
  _INGEST_POOL_WORKER_CMDLINE_MARK: Attribute.
  _LOGGED_RECYCLE_INFO_PIDS_BY_POOL: Attribute.
  _POST_RETIRE_MAINTAIN_COALESCE_S: Attribute.
  _RECYCLE_PID_FIRST_SEEN_BY_POOL: Attribute.
  _RECYCLE_TRACKING_MAX_PIDS: Attribute.
  _STALL_POLL_FAIL_LOG_INTERVAL_S: Attribute.
  _SUPERVISOR_RETIRE_PIDS_BY_POOL: Attribute.
  _WAITPID_OSERROR_LOGGED_MAX: Attribute.
  _WAITPID_OSERROR_LOGGED_PIDS: Attribute.
  _WARNED_SLOW_RECYCLE_PIDS_BY_POOL: Attribute.
  _ZOMBIE_AGE_ERROR_THRESHOLD_S: Attribute.
  _ZOMBIE_FIRST_SEEN_MONO: Attribute.
  _last_post_retire_maintain_monotonic: Attribute.
  _last_stall_poll_fail_log_mono: Attribute.
"""

from __future__ import annotations

from typing import Any, Iterator

import multiprocessing
import os
import signal
import threading
import time

from hpcperfstats.dbload.lib.print_utils import log_print

# Per-pool recycle tracking (keyed by ``id(pool)``).
_RECYCLE_PID_FIRST_SEEN_BY_POOL = {}
_LOGGED_RECYCLE_INFO_PIDS_BY_POOL = {}
_WARNED_SLOW_RECYCLE_PIDS_BY_POOL = {}
_SUPERVISOR_RETIRE_PIDS_BY_POOL = {}
_RECYCLE_TRACKING_MAX_PIDS = 256

# Coalesce busy post_retire maintain calls so timeout waves do not thrash
# reclaim/probe every ~10s (production exit-124 cascade).
_POST_RETIRE_MAINTAIN_COALESCE_S = 5.0
_last_post_retire_maintain_monotonic = 0.0


def reset_post_retire_maintain_coalesce_for_tests() -> None:
  """
  Reset coalesce wall clock (unit tests only).
  
  Returns:
    None
  
  Examples:
    >>> reset_post_retire_maintain_coalesce_for_tests()  # doctest: +SKIP
  """
  global _last_post_retire_maintain_monotonic
  _last_post_retire_maintain_monotonic = 0.0


class MultiprocessingWorkerExitError(RuntimeError):
  """
  Raised when a pool worker process is no longer alive.
  
  Attributes:
    context: Attribute.
    dead_pids: Attribute.
    diagnostics: Attribute.
    exit_code: Attribute.
    likely_cause: Attribute.
  """

  def __init__(
    self,
    message: Any,
    *,
    dead_pids: Any,
    context: str = "",
    exit_code: int = 137,
    likely_cause: str = "",
    diagnostics: Any | None = None,
  ) -> None:
    """
    Initialize a new instance.
    
    Args:
      message (Any): Message passed to this helper.
      dead_pids (Any): Dead pids passed to this helper.
      context (str): String for context.
      exit_code (int): Integer value for exit code.
      likely_cause (str): String for likely cause.
      diagnostics (Any | None): One of ``Any``, ``None``.
    
    Returns:
      None
    
    Examples:
      >>> MultiprocessingWorkerExitError(None, None, "x", 0, "x", None)
    """
    super().__init__(message)
    self.dead_pids = tuple(int(p) for p in dead_pids if p is not None)
    self.context = str(context or "")
    self.exit_code = int(exit_code)
    self.likely_cause = str(likely_cause or "")
    self.diagnostics = dict(diagnostics or {})

  def __str__(self) -> Any:
    """
    Return the informal string representation.
    
    Returns:
      Any: Open return polymorphism from ``__str__``: concrete type depends on
      inputs and branch (mapping, scalar, handle, or ``None``-like empty).
    
    Examples:
      >>> __str__()  # doctest: +SKIP
    """
    base = super().__str__()
    if self.likely_cause and self.likely_cause not in base:
      return "%s likely_cause=%s" % (base, self.likely_cause)
    return base


class MultiprocessingPoolStallError(MultiprocessingWorkerExitError):
  """
  Raised when a pool worker is alive but imap progress stalls too long.
  """


def ingest_path_normpath(path: str) -> Any:
  """
  Canonical normpath key for ingest sliding-window / recover dedupe.
  
  Args:
    path (str): String for path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> ingest_path_normpath("x")  # doctest: +SKIP
  """
  if not path:
    return ""
  return os.path.normpath(str(path))


def ingest_path_dispatch_label(path: str) -> Any:
  """
  Operator-facing path label: ``host/basename`` (not basename-only).
  
  Many hosts share the same timestamp basename; basename-only WARNs collapse
  distinct full paths into one misleading token.
  
  Args:
    path (str): String for path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> ingest_path_dispatch_label("x")  # doctest: +SKIP
  """
  norm = ingest_path_normpath(path)
  if not norm:
    return ""
  parent = os.path.basename(os.path.dirname(norm))
  base = os.path.basename(norm)
  if parent and parent not in (".", "/"):
    return "%s/%s" % (parent, base)
  return base


def dedupe_ingest_paths_preserve_order(paths: Any) -> Any:
  """
  First occurrence wins; return (unique_paths, duplicate_n, duplicate_sample).
  
  ``duplicate_sample`` entries are ``basename:count`` strings (capped by
    caller).
  
  Args:
    paths (Any): Iterable of filesystem paths as strings.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> dedupe_ingest_paths_preserve_order(None)  # doctest: +SKIP
  """
  norm_counts = {}
  first_path_by_norm = {}
  order = []
  for path in paths or ():
    if not path:
      continue
    norm = ingest_path_normpath(path)
    norm_counts[norm] = norm_counts.get(norm, 0) + 1
    if norm not in first_path_by_norm:
      first_path_by_norm[norm] = path
      order.append(norm)
  unique = [first_path_by_norm[norm] for norm in order]
  total = sum(norm_counts.values())
  duplicate_n = max(0, total - len(unique))
  duplicate_sample = []
  for norm in order:
    count = norm_counts[norm]
    if count > 1:
      duplicate_sample.append("%s:%d" % (os.path.basename(norm), count))
  return unique, duplicate_n, duplicate_sample


def pending_ingest_normpaths(pending_async: Any) -> Any:
  """
  Normpath keys for paths currently in a sliding-window ``pending_async`` map.
  
  Args:
    pending_async (Any): Pending async passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> pending_ingest_normpaths(None)  # doctest: +SKIP
  """
  return {
      ingest_path_normpath(path)
      for path in (pending_async or {}).values()
      if path
  }


def get_sync_pool_poll_timeout_s() -> Any:
  """
  Seconds between ``AsyncResult.get`` / ``imap`` progress polls.
  
  Returns:
    Any: Open return polymorphism from ``get_sync_pool_poll_timeout_s``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_sync_pool_poll_timeout_s()  # doctest: +SKIP
  """
  import hpcperfstats.dbload.lib.conf_parser as cfg

  return cfg.get_sync_pool_poll_timeout_s()


def get_sync_pool_worker_recycle_grace_polls() -> Any:
  """
  Deprecated poll-count grace; prefer.
  
    ``get_sync_pool_worker_recycle_grace_seconds``.
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_pool_worker_recycle_grace_polls``: concrete type depends on
    inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_sync_pool_worker_recycle_grace_polls()  # doctest: +SKIP
  """
  import hpcperfstats.dbload.lib.conf_parser as cfg

  return cfg.get_sync_pool_worker_recycle_grace_polls()


def get_sync_pool_worker_recycle_grace_seconds() -> Any:
  """
  Wall-clock seconds before WARN on slow ``maxtasksperchild`` replacement per.
  
    dead PID.
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_pool_worker_recycle_grace_seconds``: concrete type depends on
    inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_sync_pool_worker_recycle_grace_seconds()  # doctest: +SKIP
  """
  import hpcperfstats.dbload.lib.conf_parser as cfg

  return cfg.get_sync_pool_worker_recycle_grace_seconds()


def get_sync_pool_idle_reconcile_max_rounds() -> Any:
  """
  Redispatch rounds before idle-pool ghost fail-fast.
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_pool_idle_reconcile_max_rounds``: concrete type depends on
    inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_sync_pool_idle_reconcile_max_rounds()  # doctest: +SKIP
  """
  import hpcperfstats.dbload.lib.conf_parser as cfg

  return cfg.get_sync_pool_idle_reconcile_max_rounds()


def get_sync_pool_idle_reconcile_polls_per_round() -> Any:
  """
  Idle polls between orphan-async reconcile redispatch rounds.
  
  Returns:
    Any: Open return polymorphism from
    ``get_sync_pool_idle_reconcile_polls_per_round``: concrete type depends on
    inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> get_sync_pool_idle_reconcile_polls_per_round()  # doctest: +SKIP
  """
  import hpcperfstats.dbload.lib.conf_parser as cfg

  return cfg.get_sync_pool_idle_reconcile_polls_per_round()


_COLLECT_PENDING = object()


def try_collect_async_result(async_result: Any) -> Any:
  """
  Collect a finished task even when ``ready()`` is false (orphan async / H1).
  
  Args:
    async_result (Any): Async result passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> try_collect_async_result(None)  # doctest: +SKIP
  """
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
  pool: Any,
  pending_async: Any,
  fn: Any,
  *,
  apply_async: Any | None = None,
  resolve_skip_result: Any | None = None,
  on_redispatch: Any | None = None,
  allow_redispatch: bool = True,
) -> Any:
  """
  Collect orphan async results or redispatch stale entries (H1/H2 recovery).
  
  Mutates ``pending_async`` in place. Returns ``(collected, redispatched_n)``
  where ``collected`` is a list of ``(path, item)`` tuples.
  
  Args:
    pool (Any): Live handle (pool, client, or connection).
    pending_async (Any): Pending async passed to this helper.
    fn (Any): Callable invoked by this helper.
    apply_async (Any | None): One of ``Any``, ``None``.
    resolve_skip_result (Any | None): One of ``Any``, ``None``.
    on_redispatch (Any | None): One of ``Any``, ``None``.
    allow_redispatch (bool): Boolean flag for allow redispatch.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> reconcile_idle_pending_async(None, None, None, None, None, None, True)
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


def iter_pool_worker_processes(pool: Any) -> Iterator[Any]:
  """
  Yield worker ``Process`` objects from a ``multiprocessing.Pool``.
  
  Args:
    pool (Any): Live handle (pool, client, or connection).
  
  Yields:
    Iterator[Any]: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> iter_pool_worker_processes(None)  # doctest: +SKIP
  """
  if pool is None:
    return
  for proc in list(getattr(pool, "_pool", []) or []):
    if proc is not None:
      yield proc


def dead_pool_worker_pids(pool: Any) -> Any:
  """
  Return PIDs of pool workers that are no longer alive.
  
  Args:
    pool (Any): Live handle (pool, client, or connection).
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> dead_pool_worker_pids(None)  # doctest: +SKIP
  """
  dead = []
  for proc in iter_pool_worker_processes(pool):
    is_alive_fn = getattr(proc, "is_alive", None)
    if not callable(is_alive_fn):
      continue
    try:
      alive = bool(is_alive_fn())
    except (ValueError, AssertionError, OSError):
      alive = False
    except Exception:
      continue
    if not alive:
      dead.append(getattr(proc, "pid", None))
  return [pid for pid in dead if pid is not None]


def _iter_dead_pool_worker_processes(pool: Any) -> Iterator[Any]:
  """
  Internal helper to iterate over the dead pool worker processes.
  
  Args:
    pool (Any): Live handle (pool, client, or connection).
  
  Yields:
    Iterator[Any]: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _iter_dead_pool_worker_processes(None)  # doctest: +SKIP
  """
  for proc in iter_pool_worker_processes(pool):
    is_alive_fn = getattr(proc, "is_alive", None)
    if not callable(is_alive_fn):
      continue
    try:
      alive = bool(is_alive_fn())
    except (ValueError, AssertionError, OSError):
      # Closed or foreign Process objects raise; treat as dead for reap.
      alive = False
    except Exception:
      continue
    if not alive:
      yield proc


def alive_pool_worker_count(pool: Any) -> Any:
  """
  Return count of pool worker processes still alive.
  
  Args:
    pool (Any): Live handle (pool, client, or connection).
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> alive_pool_worker_count(None)  # doctest: +SKIP
  """
  alive = 0
  for proc in iter_pool_worker_processes(pool):
    is_alive_fn = getattr(proc, "is_alive", None)
    if not callable(is_alive_fn):
      continue
    try:
      if is_alive_fn():
        alive += 1
    except (ValueError, AssertionError, OSError):
      continue
    except Exception:
      continue
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


def read_process_wchan(pid: int) -> Any:
  """
  Return kernel wait channel for ``pid``, or None when unavailable.
  
  Args:
    pid (int): Integer value for pid.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> read_process_wchan(0)  # doctest: +SKIP
  """
  try:
    with open("/proc/%d/wchan" % int(pid), encoding="ascii") as proc_wchan:
      return proc_wchan.read().strip()
  except OSError:
    return None


def worker_wchan_looks_idle(wchan: Any) -> Any:
  """
  True when ``wchan`` indicates a blocked/idle pool worker (Linux).
  
  Args:
    wchan (Any): Wchan passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> worker_wchan_looks_idle(None)  # doctest: +SKIP
  """
  if not wchan or wchan == "0":
    return False
  if wchan in _IDLE_WORKER_WCHAN_EXACT:
    return True
  if wchan.startswith("futex"):
    return True
  if "pipe_read" in wchan:
    return True
  return False


def pool_workers_all_idle(pool: Any) -> Any:
  """
  True when every alive pool worker's wchan looks idle (Linux ``/proc``).
  
  Args:
    pool (Any): Live handle (pool, client, or connection).
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> pool_workers_all_idle(None)  # doctest: +SKIP
  """
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


def format_pool_worker_wchan_sample(pool: Any, *, limit: int = 5) -> Any:
  """
  Return ``pid:wchan`` strings for up to ``limit`` alive pool workers.
  
  Args:
    pool (Any): Live handle (pool, client, or connection).
    limit (int): Integer value for limit.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> format_pool_worker_wchan_sample(None, 0)  # doctest: +SKIP
  """
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


def idle_pool_ghost_abort_polls(stall_abort_after: Any) -> Any:
  """
  Poll count before idle-pool ghost fail-fast (much shorter than giant defer).
  
  Args:
    stall_abort_after (Any): Stall abort after passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> idle_pool_ghost_abort_polls(None)  # doctest: +SKIP
  """
  return max(12, min(120, max(1, int(stall_abort_after)) // 20))


def _process_exitcode_signal_name(exitcode: Any) -> Any:
  """
  Internal helper to process the exitcode signal name.
  
  Args:
    exitcode (Any): Exitcode passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _process_exitcode_signal_name(None)  # doctest: +SKIP
  """
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


def _infer_likely_cause(dead_workers: Any, cgroup_events: Any) -> Any:
  """
  Internal helper to handle infer likely cause.
  
  Args:
    dead_workers (Any): Dead workers passed to this helper.
    cgroup_events (Any): Cgroup events passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _infer_likely_cause(None, None)  # doctest: +SKIP
  """
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


def _cold_sync_timedb_pool_recycles_after_every_task(
  pool: Any,
  *,
  pool_health_context: Any | None = None,
) -> Any:
  """
  True when ``pool`` is not the supervised ingest pool (hardcoded.
  
    maxtasksperchild=1).
  
  Args:
    pool (Any): Live handle (pool, client, or connection).
    pool_health_context (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _cold_sync_timedb_pool_recycles_after_every_task(None, None)
  """
  ctx = pool_health_context or {}
  ingest_pool = ctx.get("ingest_pool")
  if ingest_pool is None or pool is None:
    return False
  return pool is not ingest_pool


def _dead_worker_exitcode_is_recycle(
  proc: Any,
  *,
  pool: Any | None = None,
  pool_health_context: Any | None = None,
) -> Any:
  """
  True when a dead worker exitcode looks like healthy pool recycle.
  
  Args:
    proc (Any): Proc passed to this helper.
    pool (Any | None): One of ``Any``, ``None``.
    pool_health_context (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _dead_worker_exitcode_is_recycle(None, None, None)  # doctest: +SKIP
  """
  exitcode = getattr(proc, "exitcode", None)
  if exitcode == 0:
    return True
  if exitcode == -signal.SIGTERM and pool is not None:
    pid = getattr(proc, "pid", None)
    if pid is not None:
      retired = _SUPERVISOR_RETIRE_PIDS_BY_POOL.get(id(pool), set())
      if pid in retired:
        return True
  if exitcode is None:
    if _cold_sync_timedb_pool_recycles_after_every_task(
        pool,
        pool_health_context=pool_health_context,
    ):
      return True
    ctx = pool_health_context if isinstance(pool_health_context, dict) else {}
    if "maxtasksperchild" in ctx:
      try:
        return int(ctx.get("maxtasksperchild") or 0) > 0
      except (TypeError, ValueError):
        return False
    import hpcperfstats.dbload.lib.conf_parser as cfg

    return cfg.get_sync_ingest_pool_maxtasksperchild() > 0
  return False


def _pool_recycle_gate_metrics(
  pool: Any,
  dead_procs: Any,
  *,
  pool_health_context: Any | None = None,
) -> Any:
  """
  Snapshot counts for healthy-recycle gate logging and decisions.
  
  Args:
    pool (Any): Live handle (pool, client, or connection).
    dead_procs (Any): Dead procs passed to this helper.
    pool_health_context (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _pool_recycle_gate_metrics(None, None, None)  # doctest: +SKIP
  """
  workers = list(iter_pool_worker_processes(pool))
  proc_count = len(workers)
  alive = alive_pool_worker_count(pool)
  dead_n = len(dead_procs)
  materialized = alive + dead_n
  raw_pool_len = len(getattr(pool, "_pool", []) or [])
  ctx = pool_health_context or {}
  configured = ctx.get("expected_pool_workers")
  if configured is not None:
    try:
      configured = int(configured)
    except (TypeError, ValueError):
      configured = None
  expected_total = proc_count
  if configured is not None and configured > expected_total:
    expected_total = configured
  if raw_pool_len > expected_total:
    expected_total = raw_pool_len
  gap = max(0, expected_total - materialized)
  dead_exitcodes = [
      (getattr(proc, "pid", None), getattr(proc, "exitcode", None))
      for proc in dead_procs
  ]
  return {
      "alive": alive,
      "len_workers": proc_count,
      "expected_total": expected_total,
      "materialized": materialized,
      "dead_n": dead_n,
      "gap": gap,
      "dead_exitcodes": dead_exitcodes,
  }


def _recycle_replacements_keeping_pace(
  pool: Any,
  dead_procs: Any,
  *,
  pool_health_context: Any | None = None,
) -> Any:
  """
  True when alive workers cover dead slots, including spawn-gap tolerance.
  
  Args:
    pool (Any): Live handle (pool, client, or connection).
    dead_procs (Any): Dead procs passed to this helper.
    pool_health_context (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _recycle_replacements_keeping_pace(None, None, None)  # doctest: +SKIP
  """
  metrics = _pool_recycle_gate_metrics(
      pool,
      dead_procs,
      pool_health_context=pool_health_context,
  )
  alive = metrics["alive"]
  proc_count = metrics["len_workers"]
  expected_total = metrics["expected_total"]
  dead_n = metrics["dead_n"]
  gap = metrics["gap"]

  if alive <= 0:
    return False
  if alive >= expected_total - dead_n:
    return True
  # Pool sized below process_cap during recycle (July-08: 20/21 materialized vs cap 24).
  cap_shrink = max(0, expected_total - proc_count)
  if (
      cap_shrink > 0
      and alive >= dead_n
      and alive >= expected_total - dead_n - cap_shrink
  ):
    return True
  # Replacement slot not yet materialized in pool._pool.
  if (
      gap > 0
      and gap <= dead_n
      and alive >= dead_n
      and alive >= expected_total - dead_n - gap
  ):
    return True
  return False


def _is_maxtasksperchild_recycle_in_progress(
  pool: Any,
  dead_procs: Any,
  *,
  pool_health_context: Any | None = None,
) -> Any:
  """
  True when dead workers look like normal pool worker replacement.
  
  Args:
    pool (Any): Live handle (pool, client, or connection).
    dead_procs (Any): Dead procs passed to this helper.
    pool_health_context (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _is_maxtasksperchild_recycle_in_progress(None, None, None)
  """
  if not dead_procs:
    return False
  if not _recycle_replacements_keeping_pace(
      pool,
      dead_procs,
      pool_health_context=pool_health_context,
  ):
    return False
  for proc in dead_procs:
    if not _dead_worker_exitcode_is_recycle(
        proc,
        pool=pool,
        pool_health_context=pool_health_context,
    ):
      return False
  return True


def _format_recycle_gate_reject_reason(
  pool: Any,
  dead_procs: Any,
  *,
  pool_health_context: Any | None = None,
) -> Any:
  """
  Internal helper to format the recycle gate reject reason.
  
  Args:
    pool (Any): Live handle (pool, client, or connection).
    dead_procs (Any): Dead procs passed to this helper.
    pool_health_context (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _format_recycle_gate_reject_reason(None, None, None)  # doctest: +SKIP
  """
  metrics = _pool_recycle_gate_metrics(
      pool,
      dead_procs,
      pool_health_context=pool_health_context,
  )
  return (
      "alive=%s len_workers=%s expected_total=%s materialized=%s dead_n=%s "
      "gap=%s dead_exitcodes=%s"
      % (
          metrics["alive"],
          metrics["len_workers"],
          metrics["expected_total"],
          metrics["materialized"],
          metrics["dead_n"],
          metrics["gap"],
          metrics["dead_exitcodes"],
      )
  )


def _is_recycle_stuck_replacements_lagging(
  pool: Any,
  dead_procs: Any,
  *,
  pool_health_context: Any | None = None,
) -> Any:
  """
  True when recycle-shaped exits occur but replacements are not keeping pace.
  
  Args:
    pool (Any): Live handle (pool, client, or connection).
    dead_procs (Any): Dead procs passed to this helper.
    pool_health_context (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _is_recycle_stuck_replacements_lagging(None, None, None)
  """
  if not dead_procs:
    return False
  if _is_maxtasksperchild_recycle_in_progress(
      pool,
      dead_procs,
      pool_health_context=pool_health_context,
  ):
    return False
  for proc in dead_procs:
    if not _dead_worker_exitcode_is_recycle(
        proc,
        pool=pool,
        pool_health_context=pool_health_context,
    ):
      return False
  return True


def describe_dead_pool_workers(
  pool: Any,
  *,
  pool_health_context: Any | None = None,
) -> Any:
  """
  Build operator-facing diagnostics for dead pool workers.
  
  Args:
    pool (Any): Live handle (pool, client, or connection).
    pool_health_context (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> describe_dead_pool_workers(None, None)  # doctest: +SKIP
  """
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


def _format_pool_worker_death_diagnostics(
  context: Any,
  diagnostics: Any,
) -> Any:
  """
  Internal helper to format the pool worker death diagnostics.
  
  Args:
    context (Any): Context passed to this helper.
    diagnostics (Any): Diagnostics passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _format_pool_worker_death_diagnostics(None, None)  # doctest: +SKIP
  """
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


def _reset_recycle_tracking(pool: Any) -> None:
  """
  Internal helper to handle reset recycle tracking.
  
  Args:
    pool (Any): Live handle (pool, client, or connection).
  
  Returns:
    None
  
  Examples:
    >>> _reset_recycle_tracking(None)  # doctest: +SKIP
  """
  pool_key = id(pool)
  _RECYCLE_PID_FIRST_SEEN_BY_POOL.pop(pool_key, None)
  _LOGGED_RECYCLE_INFO_PIDS_BY_POOL.pop(pool_key, None)
  _WARNED_SLOW_RECYCLE_PIDS_BY_POOL.pop(pool_key, None)


def _prune_recycle_tracking(pool: Any, dead_pids: Any) -> None:
  """
  Internal helper to handle prune recycle tracking.
  
  Args:
    pool (Any): Live handle (pool, client, or connection).
    dead_pids (Any): Dead pids passed to this helper.
  
  Returns:
    None
  
  Examples:
    >>> _prune_recycle_tracking(None, None)  # doctest: +SKIP
  """
  pool_key = id(pool)
  first_seen = _RECYCLE_PID_FIRST_SEEN_BY_POOL.get(pool_key)
  if not first_seen:
    return
  dead_set = set(dead_pids)
  for pid in list(first_seen):
    if pid not in dead_set:
      first_seen.pop(pid, None)
      logged = _LOGGED_RECYCLE_INFO_PIDS_BY_POOL.get(pool_key)
      if logged is not None:
        logged.discard(pid)
      warned = _WARNED_SLOW_RECYCLE_PIDS_BY_POOL.get(pool_key)
      if warned is not None:
        warned.discard(pid)


def _handle_healthy_maxtasksperchild_recycle(
  pool: Any,
  dead_procs: Any,
  *,
  context: Any,
  diagnostics: Any,
  pool_health_context: Any,
) -> None:
  """
  Reap and log healthy recycle; never fatal while replacements keep pace.
  
  Args:
    pool (Any): Live handle (pool, client, or connection).
    dead_procs (Any): Dead procs passed to this helper.
    context (Any): Context passed to this helper.
    diagnostics (Any): Diagnostics passed to this helper.
    pool_health_context (Any): Pool health context passed to this helper.
  
  Returns:
    None
  
  Examples:
    >>> _handle_healthy_maxtasksperchild_recycle(None, None, None, None, None)
  """
  reconcile_fn = (pool_health_context or {}).get("idle_reconcile_fn")
  if callable(reconcile_fn):
    try:
      reconcile_fn()
    except Exception:
      pass
  grace_ctx = context or "recycle_grace"
  reap_pool_worker_pids(pool, context=grace_ctx)
  reap_zombie_children_of_self(context=grace_ctx)
  warn_unreaped_zombie_children(context=grace_ctx)

  now_mono = time.monotonic()
  grace_seconds = float(get_sync_pool_worker_recycle_grace_seconds())
  pool_key = id(pool)
  first_seen = _RECYCLE_PID_FIRST_SEEN_BY_POOL.setdefault(pool_key, {})
  logged_info = _LOGGED_RECYCLE_INFO_PIDS_BY_POOL.setdefault(pool_key, set())
  warned_slow = _WARNED_SLOW_RECYCLE_PIDS_BY_POOL.setdefault(pool_key, set())
  dead_pids = [
      getattr(proc, "pid", None)
      for proc in dead_procs
      if getattr(proc, "pid", None) is not None
  ]
  _prune_recycle_tracking(pool, dead_pids)
  diag_suffix = _format_pool_worker_death_diagnostics(context, diagnostics)
  for pid in dead_pids:
    if pid not in first_seen:
      first_seen[pid] = now_mono
    age_s = now_mono - first_seen[pid]
    if pid not in logged_info:
      if len(logged_info) >= _RECYCLE_TRACKING_MAX_PIDS:
        logged_info.clear()
        warned_slow.clear()
      logged_info.add(pid)
      log_print(
          "INFO: pool worker recycle in progress %s dead_pid=%s "
          "dead_pid_age_s=%.1f grace_deadline_s=%.0f"
          % (diag_suffix, pid, age_s, grace_seconds),
          flush=True,
      )
    elif age_s >= grace_seconds and pid not in warned_slow:
      if len(warned_slow) >= _RECYCLE_TRACKING_MAX_PIDS:
        warned_slow.clear()
      warned_slow.add(pid)
      log_print(
          "WARN: pool worker recycle slow %s dead_pid=%s "
          "dead_pid_age_s=%.1f grace_deadline_s=%.0f"
          % (diag_suffix, pid, age_s, grace_seconds),
          flush=True,
      )


def abort_if_pool_workers_dead(
  pool: Any,
  *,
  context: str = "",
  pool_health_context: Any | None = None,
) -> None:
  """
  Raise ``MultiprocessingWorkerExitError`` when any pool worker has exited.
  
  Args:
    pool (Any): Live handle (pool, client, or connection).
    context (str): String for context.
    pool_health_context (Any | None): One of ``Any``, ``None``.
  
  Returns:
    None
  
  Raises:
    MultiprocessingWorkerExitError: Raised when ``abort_if_pool_workers_dead``
    hits a ``MultiprocessingWorkerExitError`` failure path.
  
  Examples:
    >>> abort_if_pool_workers_dead(None, "x", None)  # doctest: +SKIP
  """
  dead_procs = list(_iter_dead_pool_worker_processes(pool))
  if not dead_procs:
    _reset_recycle_tracking(pool)
    return

  diagnostics = describe_dead_pool_workers(
      pool,
      pool_health_context=pool_health_context,
  )
  dead = [w.get("pid") for w in diagnostics.get("dead_workers") or () if w.get("pid")]

  if _is_maxtasksperchild_recycle_in_progress(
      pool,
      dead_procs,
      pool_health_context=pool_health_context,
  ):
    _handle_healthy_maxtasksperchild_recycle(
        pool,
        dead_procs,
        context=context,
        diagnostics=diagnostics,
        pool_health_context=pool_health_context or {},
    )
    return

  likely_cause = diagnostics.get("likely_cause") or "unknown"
  if _is_recycle_stuck_replacements_lagging(
      pool,
      dead_procs,
      pool_health_context=pool_health_context,
  ):
    likely_cause = "recycle_stuck"
    diagnostics = dict(diagnostics)
    diagnostics["likely_cause"] = likely_cause
  elif likely_cause == "recycle":
    likely_cause = "recycle_stuck"
    diagnostics = dict(diagnostics)
    diagnostics["likely_cause"] = likely_cause

  log_print(
      "ERROR: pool worker recycle gate rejected: %s"
      % _format_recycle_gate_reject_reason(
          pool,
          dead_procs,
          pool_health_context=pool_health_context,
      ),
      flush=True,
  )

  _reset_recycle_tracking(pool)
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
      likely_cause=likely_cause,
      diagnostics=diagnostics,
  )


def _wait_pool_processes_bounded(active_pool: Any, timeout_s: Any) -> Any:
  """
  Internal helper to wait for the pool processes bounded.
  
  Args:
    active_pool (Any): Active pool passed to this helper.
    timeout_s (Any): Timeout s passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _wait_pool_processes_bounded(None, None)  # doctest: +SKIP
  """
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


_WAITPID_OSERROR_LOGGED_PIDS = set()
_WAITPID_OSERROR_LOGGED_MAX = 64
_STALL_POLL_FAIL_LOG_INTERVAL_S = 30.0
_last_stall_poll_fail_log_mono = 0.0
_ZOMBIE_FIRST_SEEN_MONO = {}
_ZOMBIE_AGE_ERROR_THRESHOLD_S = 60.0


def reset_zombie_reap_observability_for_tests() -> None:
  """
  Clear waitpid / stall-poll / zombie-age tracking (unit tests only).
  
  Returns:
    None
  
  Examples:
    >>> reset_zombie_reap_observability_for_tests()  # doctest: +SKIP
  """
  global _last_stall_poll_fail_log_mono
  _WAITPID_OSERROR_LOGGED_PIDS.clear()
  _ZOMBIE_FIRST_SEEN_MONO.clear()
  _last_stall_poll_fail_log_mono = 0.0


def _log_waitpid_oserror(pid: int, exc: Any) -> None:
  """
  Log waitpid OSError once per pid so systematic failures are visible.
  
  Args:
    pid (int): Integer value for pid.
    exc (Any): Exception instance being classified or logged.
  
  Returns:
    None
  
  Examples:
    >>> _log_waitpid_oserror(0, None)  # doctest: +SKIP
  """
  try:
    pid_int = int(pid)
  except (TypeError, ValueError):
    return
  if pid_int in _WAITPID_OSERROR_LOGGED_PIDS:
    return
  if len(_WAITPID_OSERROR_LOGGED_PIDS) >= _WAITPID_OSERROR_LOGGED_MAX:
    _WAITPID_OSERROR_LOGGED_PIDS.clear()
  _WAITPID_OSERROR_LOGGED_PIDS.add(pid_int)
  log_print(
      "WARN: waitpid failed pid=%s errno=%s err=%s"
      % (pid_int, getattr(exc, "errno", None), type(exc).__name__),
      flush=True,
  )


def _log_on_stall_poll_failure(exc: Any, *, context: str = "") -> None:
  """
  Throttled WARN when an on_stall_poll callback raises (never silent).
  
  Args:
    exc (Any): Exception instance being classified or logged.
    context (str): String for context.
  
  Returns:
    None
  
  Examples:
    >>> _log_on_stall_poll_failure(None, "x")  # doctest: +SKIP
  """
  global _last_stall_poll_fail_log_mono
  now_mono = time.monotonic()
  if now_mono - _last_stall_poll_fail_log_mono < _STALL_POLL_FAIL_LOG_INTERVAL_S:
    return
  _last_stall_poll_fail_log_mono = now_mono
  log_print(
      "WARN: on_stall_poll failed context=%s err=%s: %s"
      % (context or "unknown", type(exc).__name__, exc),
      flush=True,
  )


def _waitpid_pid_nonblocking(pid: int, *, timeout_s: float = 0.5) -> Any:
  """
  Return True when ``pid`` was reaped (or already gone).
  
  Args:
    pid (int): Integer value for pid.
    timeout_s (float): Floating-point value for timeout s.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _waitpid_pid_nonblocking(0, 0)  # doctest: +SKIP
  """
  try:
    waited_pid, _status = os.waitpid(int(pid), os.WNOHANG)
    if waited_pid == int(pid):
      return True
  except ChildProcessError:
    return True
  except OSError as exc:
    _log_waitpid_oserror(pid, exc)
    return False
  deadline = time.monotonic() + max(0.0, float(timeout_s))
  while time.monotonic() < deadline:
    try:
      waited_pid, _status = os.waitpid(int(pid), os.WNOHANG)
      if waited_pid == int(pid):
        return True
    except ChildProcessError:
      return True
    except OSError as exc:
      _log_waitpid_oserror(pid, exc)
      return False
    time.sleep(0.05)
  return False


def _safe_proc_is_alive(proc: Any, *, default: bool = True) -> Any:
  """
  Return process liveness; closed/foreign Process objects do not raise.
  
  Args:
    proc (Any): Proc passed to this helper.
    default (bool): Boolean flag for default.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _safe_proc_is_alive(None, True)  # doctest: +SKIP
  """
  is_alive_fn = getattr(proc, "is_alive", None)
  if not callable(is_alive_fn):
    return bool(default)
  try:
    return bool(is_alive_fn())
  except (ValueError, AssertionError, OSError):
    return False
  except Exception:
    return bool(default)


def _reap_pool_worker_pids(
  pool: Any,
  *,
  timeout_s: float = 5.0,
  context: str = "",
) -> Any:
  """
  Reap terminated pool workers so zombies do not accumulate under the.
  
    supervisor.
  
  Args:
    pool (Any): Live handle (pool, client, or connection).
    timeout_s (float): Floating-point value for timeout s.
    context (str): String for context.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _reap_pool_worker_pids(None, 0, "x")  # doctest: +SKIP
  """
  if pool is None:
    return []
  # Prefer Process.join so multiprocessing updates internal state first.
  for proc in list(_iter_dead_pool_worker_processes(pool)):
    try:
      proc.join(timeout=0)
    except Exception:
      pass
  pids = []
  for proc in iter_pool_worker_processes(pool):
    pid = getattr(proc, "pid", None)
    if pid is None:
      continue
    if not _safe_proc_is_alive(proc, default=True):
      pids.append(pid)
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


def reap_pool_worker_pids(
  pool: Any,
  *,
  timeout_s: float = 5.0,
  context: str = "",
) -> Any:
  """
  Public wrapper: reap dead workers still listed on ``pool._pool``.
  
  Args:
    pool (Any): Live handle (pool, client, or connection).
    timeout_s (float): Floating-point value for timeout s.
    context (str): String for context.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> reap_pool_worker_pids(None, 0, "x")  # doctest: +SKIP
  """
  reaped = _reap_pool_worker_pids(pool, timeout_s=timeout_s, context=context)
  if reaped:
    pool_key = id(pool)
    retired = _SUPERVISOR_RETIRE_PIDS_BY_POOL.get(pool_key)
    if retired:
      for pid in reaped:
        retired.discard(int(pid))
  return reaped


def _find_pool_worker_process(pool: Any, pid: int) -> Any:
  """
  Internal helper to find the pool worker process.
  
  Args:
    pool (Any): Live handle (pool, client, or connection).
    pid (int): Integer value for pid.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _find_pool_worker_process(None, 0)  # doctest: +SKIP
  """
  try:
    pid_int = int(pid)
  except (TypeError, ValueError):
    return None
  for proc in iter_pool_worker_processes(pool):
    if getattr(proc, "pid", None) == pid_int:
      return proc
  return None


def retire_pool_worker_pid(pool: Any, pid: int, *, context: str = "") -> Any:
  """
  Supervisor-initiated cooperative worker retire (SIGTERM, exitcode -15).
  
  Args:
    pool (Any): Live handle (pool, client, or connection).
    pid (int): Integer value for pid.
    context (str): String for context.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> retire_pool_worker_pid(None, 0, "x")  # doctest: +SKIP
  """
  if pool is None or pid is None:
    return False
  proc = _find_pool_worker_process(pool, pid)
  if proc is None:
    return False
  pool_key = id(pool)
  retired = _SUPERVISOR_RETIRE_PIDS_BY_POOL.setdefault(pool_key, set())
  pid_int = int(getattr(proc, "pid", pid))
  retired.add(pid_int)
  if _safe_proc_is_alive(proc, default=False):
    terminate_fn = getattr(proc, "terminate", None)
    if callable(terminate_fn):
      terminate_fn()
  reap_pool_worker_pids(pool, context=context or "supervisor_retire")
  return True


def reset_supervisor_retire_tracking_for_tests() -> None:
  """
  Reset supervisor retire tracking for tests.
  
  Returns:
    None
  
  Examples:
    >>> reset_supervisor_retire_tracking_for_tests()  # doctest: +SKIP
  """
  _SUPERVISOR_RETIRE_PIDS_BY_POOL.clear()


def _read_proc_stat_fields(pid: int) -> Any:
  """
  Return ``(state, ppid)`` from ``/proc/<pid>/stat``, or ``None``.
  
  Reads bytes and decodes with replace so a non-ASCII ``comm`` cannot abort
  a full ``/proc`` census with ``UnicodeDecodeError``.
  
  Args:
    pid (int): Integer value for pid.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _read_proc_stat_fields(0)  # doctest: +SKIP
  """
  try:
    with open("/proc/%d/stat" % int(pid), "rb") as proc_stat:
      raw = proc_stat.read()
  except OSError:
    return None
  try:
    stat_line = raw.decode("utf-8", errors="replace")
  except (TypeError, ValueError, UnicodeError):
    return None
  rparen = stat_line.rfind(")")
  if rparen < 0 or rparen + 2 >= len(stat_line):
    return None
  # fields after ") ": state ppid ...
  rest = stat_line[rparen + 2 :].split()
  if len(rest) < 2:
    return None
  state = rest[0]
  try:
    ppid = int(rest[1])
  except (TypeError, ValueError):
    return None
  return state, ppid


def _process_stat_is_zombie(pid: int) -> Any:
  """
  Return True when ``/proc/<pid>/stat`` reports state ``Z``.
  
  Args:
    pid (int): Integer value for pid.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _process_stat_is_zombie(0)  # doctest: +SKIP
  """
  fields = _read_proc_stat_fields(pid)
  if fields is None:
    return False
  return fields[0] == "Z"


def _iter_zombie_child_pids() -> Iterator[Any]:
  """
  Yield PIDs of direct children of this process that are zombies.
  
  Yields:
    Iterator[Any]: Open return polymorphism from ``_iter_zombie_child_pids``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> _iter_zombie_child_pids()  # doctest: +SKIP
  """
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
    fields = _read_proc_stat_fields(pid)
    if fields is None:
      continue
    state, ppid = fields
    if state == "Z" and ppid == self_pid:
      yield pid


def reap_zombie_children_of_self(*, context: str = "") -> Any:
  """
  PID-specific waitpid for zombie children (not in pool._pool).
  
  Prefer this over ``waitpid(-1)`` so live Pool/Manager waits are not stolen.
  
  Args:
    context (str): String for context.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> reap_zombie_children_of_self("x")  # doctest: +SKIP
  """
  reaped = []
  for pid in _iter_zombie_child_pids():
    if _waitpid_pid_nonblocking(pid, timeout_s=0.5):
      reaped.append(int(pid))
      _ZOMBIE_FIRST_SEEN_MONO.pop(int(pid), None)
  if reaped:
    log_print(
        "Zombie child reap context=%s pids=%s"
        % (context or "supervisor", reaped),
        flush=True,
    )
  return reaped


_INGEST_POOL_WORKER_CMDLINE_MARK = "[worker:ingest-pool]"


def pool_worker_cmdline_mark_for_kind(pool_kind: str) -> str:
  """
  Build the ``ps``/cmdline mark for a pool worker title.

  Matches ``apply_pool_worker_process_title`` output
  ``{script} [worker:{pool_kind}]``.

  Args:
    pool_kind (str): Stable pool label (e.g. ``ingest-pool``,
      ``metrics-pool``).

  Returns:
    str: Substring used for PPID census matching.

  Examples:
    >>> pool_worker_cmdline_mark_for_kind("metrics-pool")
    '[worker:metrics-pool]'
  """
  return "[worker:%s]" % str(pool_kind or "").strip()


def _read_proc_cmdline(pid: int) -> Any:
  """
  Return decoded ``/proc/<pid>/cmdline`` (nulls → spaces) or ``""``.
  
  Args:
    pid (int): Integer value for pid.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _read_proc_cmdline(0)  # doctest: +SKIP
  """
  try:
    with open("/proc/%d/cmdline" % int(pid), "rb") as handle:
      raw = handle.read()
  except OSError:
    return ""
  if not raw:
    return ""
  return raw.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()


def _iter_direct_child_pids(*, self_pid: Any | None = None) -> Iterator[Any]:
  """
  Yield PIDs of live (non-zombie) direct children of this process.
  
  Args:
    self_pid (Any | None): One of ``Any``, ``None``.
  
  Yields:
    Iterator[Any]: Open return polymorphism from ``_iter_direct_child_pids``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> _iter_direct_child_pids(None)  # doctest: +SKIP
  """
  parent = int(self_pid) if self_pid is not None else os.getpid()
  try:
    entries = os.listdir("/proc")
  except OSError:
    return
  for name in entries:
    if not name.isdigit():
      continue
    pid = int(name)
    if pid == parent:
      continue
    fields = _read_proc_stat_fields(pid)
    if fields is None:
      continue
    state, ppid = fields
    if state == "Z":
      continue
    if ppid == parent:
      yield pid


def list_pool_child_pids_of_self(
  *,
  cmdline_mark: str,
  self_pid: Any | None = None,
) -> list[int]:
  """
  Direct children whose cmdline contains ``cmdline_mark``.

  Args:
    cmdline_mark (str): Substring to match (e.g.
      ``[worker:ingest-pool]`` or ``[worker:metrics-pool]``).
    self_pid (Any | None): Optional parent PID override (tests).

  Returns:
    list[int]: Matching live (non-zombie) child PIDs.

  Examples:
    >>> list_pool_child_pids_of_self(
    ...     cmdline_mark="[worker:metrics-pool]"
    ... )  # doctest: +SKIP
  """
  mark = str(cmdline_mark or "")
  if not mark:
    return []
  out: list[int] = []
  for pid in _iter_direct_child_pids(self_pid=self_pid):
    cmdline = _read_proc_cmdline(pid)
    if mark in cmdline:
      out.append(int(pid))
  return out


def list_ingest_pool_child_pids_of_self(*, self_pid: Any | None = None) -> Any:
  """
  Direct children whose cmdline matches ``[worker:ingest-pool]``.

  Thin wrapper over ``list_pool_child_pids_of_self`` preserving the
  ingest-only census used by ``sync_timedb`` reclaim/abandon paths.

  Args:
    self_pid (Any | None): Optional parent PID override (tests).

  Returns:
    Any: List of matching child PIDs.

  Examples:
    >>> list_ingest_pool_child_pids_of_self(None)  # doctest: +SKIP
  """
  return list_pool_child_pids_of_self(
      cmdline_mark=_INGEST_POOL_WORKER_CMDLINE_MARK,
      self_pid=self_pid,
  )


def kill_pool_children_by_ppid_census(
  *,
  cmdline_mark: str,
  context: str = "",
  keep_pids: Any | None = None,
) -> list[int]:
  """
  SIGKILL children of main matching ``cmdline_mark`` except ``keep_pids``.

  Used on abandon/recreate so orphans left out of ``pool._pool`` cannot
  survive a proactive swap. Pool-kind agnostic; ingest and metrics pass
  their respective ``[worker:…]`` marks.

  Args:
    cmdline_mark (str): Cmdline substring for the pool kind.
    context (str): Operator-facing context string.
    keep_pids (Any | None): PIDs to leave alive.

  Returns:
    list[int]: PIDs that were targeted for SIGKILL.

  Examples:
    >>> kill_pool_children_by_ppid_census(
    ...     cmdline_mark="[worker:metrics-pool]",
    ...     context="metrics_pool",
    ... )  # doctest: +SKIP
  """
  keep = {int(p) for p in (keep_pids or ()) if p is not None}
  targets = [
      pid
      for pid in list_pool_child_pids_of_self(cmdline_mark=cmdline_mark)
      if pid not in keep
  ]
  if not targets:
    return []
  log_print(
      "INFO: pool_recover ppid_census kill context=%s n=%d pids=%s"
      % (context or "ppid_census", len(targets), targets[:24]),
      flush=True,
  )
  _sigkill_pool_worker_pids(
      targets,
      context=context or "ppid_census",
      blocking_reap_s=0.2,
  )
  return targets


def kill_ingest_pool_children_by_ppid_census(
  *,
  context: str = "",
  keep_pids: Any | None = None,
) -> Any:
  """
  SIGKILL ingest-pool children of main except optional ``keep_pids``.

  Thin wrapper preserving ``sync_timedb`` call sites; delegates to
  ``kill_pool_children_by_ppid_census`` with the ingest cmdline mark.

  Args:
    context (str): Operator-facing context string.
    keep_pids (Any | None): PIDs to leave alive.

  Returns:
    Any: List of targeted PIDs.

  Examples:
    >>> kill_ingest_pool_children_by_ppid_census(
    ...     context="x", keep_pids=None
    ... )  # doctest: +SKIP
  """
  return kill_pool_children_by_ppid_census(
      cmdline_mark=_INGEST_POOL_WORKER_CMDLINE_MARK,
      context=context,
      keep_pids=keep_pids,
  )


def reclaim_excess_ingest_pool_children(
  pool: Any | None = None,
  *,
  expected: Any | None = None,
  context: str = "",
) -> Any:
  """
  Cull ingest-pool children of main when count exceeds configured processes.
  
  Keeps alive PIDs from ``pool._pool`` when possible; orphans not registered on
  the live Pool are SIGKILL'd first. Registered workers are **never** culled
  even when ``len(keep) > expected`` (retire/replacement races must not
  SIGKILL the live cohort).
  
  Args:
    pool (Any | None): One of ``Any``, ``None``.
    expected (Any | None): One of ``Any``, ``None``.
    context (str): String for context.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> reclaim_excess_ingest_pool_children(None, None, "x")  # doctest: +SKIP
  """
  import hpcperfstats.dbload.lib.conf_parser as cfg

  if expected is None:
    expected = int(cfg.get_sync_ingest_pool_processes())
  expected = max(0, int(expected))
  children = list_ingest_pool_child_pids_of_self()
  if len(children) <= expected:
    return []
  keep = set(_alive_pool_worker_pids(pool)) if pool is not None else set()
  keep = {pid for pid in keep if pid in children}
  # Never truncate keep — registered workers stay alive; only orphans cull.
  extras = [pid for pid in children if pid not in keep]
  if not extras:
    return []
  log_print(
      "ERROR: ingest pool child_ingest over cap alive=%d expected=%d "
      "culling_n=%d context=%s"
      % (
          len(children),
          expected,
          len(extras),
          context or "reclaim",
      ),
      flush=True,
  )
  _sigkill_pool_worker_pids(
      extras,
      context=context or "reclaim_excess",
      blocking_reap_s=0.2,
  )
  return extras


def warn_unreaped_zombie_children(*, context: str = "") -> None:
  """
  Log WARN/ERROR when direct zombie children remain after a reap attempt.
  
  Tracks first-seen monotonic age so a 200ms recycle transient and a multi-hour
  leak are distinguishable; escalates to ERROR past the operator 60s bar.
  
  Args:
    context (str): String for context.
  
  Returns:
    None
  
  Examples:
    >>> warn_unreaped_zombie_children("x")  # doctest: +SKIP
  """
  zombies = list(_iter_zombie_child_pids())
  now_mono = time.monotonic()
  alive_set = {int(pid) for pid in zombies}
  for tracked in list(_ZOMBIE_FIRST_SEEN_MONO):
    if tracked not in alive_set:
      _ZOMBIE_FIRST_SEEN_MONO.pop(tracked, None)
  if not zombies:
    return
  ages = []
  for pid in zombies:
    pid_int = int(pid)
    first = _ZOMBIE_FIRST_SEEN_MONO.setdefault(pid_int, now_mono)
    ages.append(now_mono - first)
  max_age = max(ages) if ages else 0.0
  sample = zombies[:8]
  sample_ages = [
      "%.1f" % (now_mono - _ZOMBIE_FIRST_SEEN_MONO[int(pid)]) for pid in sample
  ]
  level = "ERROR" if max_age >= _ZOMBIE_AGE_ERROR_THRESHOLD_S else "WARN"
  log_print(
      "%s: unreaped zombie children context=%s count=%d "
      "max_age_s=%.1f sample_pids=%s sample_age_s=%s"
      % (
          level,
          context or "supervisor",
          len(zombies),
          max_age,
          sample,
          sample_ages,
      ),
      flush=True,
  )


def _pid_is_alive(pid: int) -> Any:
  """
  Internal helper to handle pid is alive.
  
  Args:
    pid (int): Integer value for pid.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _pid_is_alive(0)  # doctest: +SKIP
  """
  try:
    os.kill(int(pid), 0)
    return True
  except OSError:
    return False


def _alive_pool_worker_pids(pool: Any) -> Any:
  """
  Internal helper to handle alive pool worker pids.
  
  Args:
    pool (Any): Live handle (pool, client, or connection).
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _alive_pool_worker_pids(None)  # doctest: +SKIP
  """
  pids = []
  for proc in iter_pool_worker_processes(pool):
    if not _safe_proc_is_alive(proc, default=False):
      continue
    pid = getattr(proc, "pid", None)
    if pid is not None:
      pids.append(int(pid))
  return pids


def _aggressive_terminate_pool_workers(
  pool: Any,
  *,
  context: str = "",
  sigterm_grace_s: float = 2.0,
  sigkill_first: bool = False,
) -> None:
  """
  SIGTERM then SIGKILL known pool worker PIDs (or SIGKILL-first for abandon).
  
  Args:
    pool (Any): Live handle (pool, client, or connection).
    context (str): String for context.
    sigterm_grace_s (float): Floating-point value for sigterm grace s.
    sigkill_first (bool): Boolean flag for sigkill first.
  
  Returns:
    None
  
  Examples:
    >>> _aggressive_terminate_pool_workers(None, "x", 0, True)  # doctest: +SKIP
  """
  alive_pids = _alive_pool_worker_pids(pool)
  if not alive_pids:
    return
  if sigkill_first:
    _sigkill_pool_worker_pids(
        alive_pids,
        context=context,
        blocking_reap_s=0.2,
    )
    return
  for pid in alive_pids:
    try:
      os.kill(pid, signal.SIGTERM)
    except OSError:
      continue
  deadline = time.monotonic() + max(0.05, float(sigterm_grace_s))
  lingering = []
  while time.monotonic() < deadline:
    lingering = [pid for pid in alive_pids if _pid_is_alive(pid)]
    if not lingering:
      return
    time.sleep(0.05)
  if lingering:
    _sigkill_pool_worker_pids(lingering, context=context, blocking_reap_s=0.2)


def _sigkill_pool_worker_pids(
  pids: Any,
  *,
  context: str = "",
  blocking_reap_s: float = 2.0,
) -> None:
  """
  Internal helper to handle sigkill pool worker pids.
  
  Args:
    pids (Any): Pids passed to this helper.
    context (str): String for context.
    blocking_reap_s (float): Floating-point value for blocking reap s.
  
  Returns:
    None
  
  Examples:
    >>> _sigkill_pool_worker_pids(None, "x", 0)  # doctest: +SKIP
  """
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
  # Bounded non-blocking reap; never O(workers × 2s).
  reap_budget = max(0.0, float(blocking_reap_s))
  if reap_budget <= 0.0 or not killed:
    return
  deadline = time.monotonic() + reap_budget
  remaining = set(killed)
  while remaining and time.monotonic() < deadline:
    done = set()
    for pid in remaining:
      try:
        waited_pid, _status = os.waitpid(int(pid), os.WNOHANG)
        if waited_pid == int(pid):
          done.add(pid)
      except ChildProcessError:
        done.add(pid)
      except OSError:
        done.add(pid)
    remaining -= done
    if remaining:
      time.sleep(0.05)


def _stop_abandoned_pool_repopulate(pool: Any) -> None:
  """
  Stop Pool ``_worker_handler`` and cancel Finalize before abandon SIGKILL.

  CPython ``_handle_workers`` keeps calling ``_maintain_pool`` while
  ``thread._state != TERMINATE``. Leaving the handler in RUN after SIGKILL
  repopulates replacement workers that become unreaped ``STAT=Z`` under
  ``[main]`` when the Pool object is dropped without Process.join (hs04
  2026-08-13: zombie PIDs disjoint from the SIGKILL list after ``/pub``
  recycle). Cancelling ``pool._terminate`` prevents a later Finalize from
  entering hangable ``_terminate_pool`` / ``p.join``.

  Args:
    pool (Any): Abandoned ``multiprocessing.Pool`` (or test double).

  Returns:
    None

  Examples:
    >>> _stop_abandoned_pool_repopulate(object())  # doctest: +SKIP
  """
  try:
    from multiprocessing.pool import TERMINATE as _POOL_TERMINATE
  except ImportError:  # pragma: no cover - stdlib always present
    _POOL_TERMINATE = "TERMINATE"
  try:
    pool._state = _POOL_TERMINATE
  except Exception:
    pass
  handler = getattr(pool, "_worker_handler", None)
  if handler is not None:
    try:
      handler._state = _POOL_TERMINATE
    except Exception:
      pass
  finalize = getattr(pool, "_terminate", None)
  cancel = getattr(finalize, "cancel", None)
  if callable(cancel):
    try:
      cancel()
    except Exception:
      pass


def _abandon_pool_reap_until_clear(
  pool: Any,
  *,
  timeout_s: float,
  context: str,
) -> None:
  """
  Process.join tracked workers, then retry ``/proc`` zombie reap to empty.

  Args:
    pool (Any): Abandoned pool whose ``_pool`` workers may still need join.
    timeout_s (float): Caller join/abandon budget; clamped to ~2–10s wall.
    context (str): Operator-facing reap/warn context string.

  Returns:
    None

  Examples:
    >>> _abandon_pool_reap_until_clear(None, timeout_s=2.0, context="x")  # doctest: +SKIP
  """
  reap_ctx = context or "abandon_pool"
  join_budget = min(5.0, max(0.1, float(timeout_s)))
  try:
    _reap_pool_worker_pids(pool, timeout_s=join_budget, context=reap_ctx)
  except Exception:
    pass
  # Bounded wall: enough for post-kill waitpid storms, not a hang.
  wall_s = min(10.0, max(2.0, float(timeout_s)))
  deadline = time.monotonic() + wall_s
  while True:
    try:
      reap_zombie_children_of_self(context=reap_ctx)
    except Exception:
      pass
    try:
      remaining = list(_iter_zombie_child_pids())
    except Exception:
      remaining = []
    if not remaining:
      break
    if time.monotonic() >= deadline:
      break
    time.sleep(0.05)
  try:
    warn_unreaped_zombie_children(context=reap_ctx)
  except Exception:
    pass


def terminate_pool_bounded(
  active_pool: Any,
  timeout_s: float = 30.0,
  *,
  context: str = "",
  kill_workers_first: bool = False,
  abandon_after_kill: bool = False,
  pool_worker_cmdline_mark: str | None = None,
) -> Any:
  """
  Terminate a pool and wait briefly so shutdown does not hang after worker
  death.

  When ``abandon_after_kill=True`` (idle-pool recover / proactive swap /
  metrics teardown), **stop ``_worker_handler`` and cancel Finalize first**,
  then SIGKILL known worker PIDs and **do not** call stdlib
  ``Pool.terminate()`` / join — that path can hang forever in
  ``_help_stuff_finish`` or at ``p.join()`` when workers swallow SIGTERM
  (RC-C; hs04 2026-08-13). Also SIGKILL every direct child whose cmdline
  matches ``pool_worker_cmdline_mark`` (default ``[worker:ingest-pool]``)
  so orphans left out of ``pool._pool`` cannot double the live cohort on
  recreate. After kill, Process.join + retry ``/proc`` reap until empty
  (or a bounded wall) and warn unreaped zombies — a single-pass reap left
  the ``/pub`` recycle 24Z cohort under metrics ``[main]``.

  Args:
    active_pool (Any): Live ``multiprocessing.Pool``.
    timeout_s (float): Join budget for the non-abandon path; abandon reap
      wall clamps to about 2–10s from this value.
    context (str): Operator-facing context string.
    kill_workers_first (bool): SIGTERM/SIGKILL workers before terminate.
    abandon_after_kill (bool): Skip stdlib ``Pool.terminate()`` after kill.
    pool_worker_cmdline_mark (str | None): PPID census cmdline mark; when
      ``None`` and abandoning, defaults to the ingest-pool mark.

  Returns:
    Any: ``True`` when workers exited (or abandon completed).

  Examples:
    >>> terminate_pool_bounded(
    ...     None, 0, context="x", kill_workers_first=True,
    ...     abandon_after_kill=True,
    ...     pool_worker_cmdline_mark="[worker:metrics-pool]",
    ... )  # doctest: +SKIP
  """
  if active_pool is None:
    return True
  alive_before = alive_pool_worker_count(active_pool)
  if abandon_after_kill:
    # Primary: stop repopulator before SIGKILL (hs04 PID gap 830–853).
    _stop_abandoned_pool_repopulate(active_pool)
  if kill_workers_first or abandon_after_kill:
    wchan_sample = format_pool_worker_wchan_sample(active_pool)
    log_print(
        "INFO: pool_recover terminate workers_before=%d wchan_sample=%s "
        "context=%s"
        % (alive_before, wchan_sample, context or "pool"),
        flush=True,
    )
    _aggressive_terminate_pool_workers(
        active_pool,
        context=context,
        sigterm_grace_s=0.2 if abandon_after_kill else 2.0,
        sigkill_first=bool(abandon_after_kill),
    )
  if abandon_after_kill:
    # Orphans may sit under main outside pool._pool after retire/swap.
    mark = (
        str(pool_worker_cmdline_mark)
        if pool_worker_cmdline_mark
        else _INGEST_POOL_WORKER_CMDLINE_MARK
    )
    try:
      kill_pool_children_by_ppid_census(
          cmdline_mark=mark,
          context=context or "abandon_pool",
          keep_pids=(),
      )
    except Exception:
      pass
    log_print(
        "INFO: pool_recover terminate outcome=abandoned context=%s "
        "workers_before=%d"
        % (context or "pool", alive_before),
        flush=True,
    )
    _abandon_pool_reap_until_clear(
        active_pool,
        timeout_s=float(timeout_s),
        context=context or "abandon_pool",
    )
    # RC-8: drop retire tracking for abandoned pool identity so a recycled
    # ``id(pool)`` cannot inherit a dead cohort's SIGTERM set.
    _SUPERVISOR_RETIRE_PIDS_BY_POOL.pop(id(active_pool), None)
    return True
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
  outcome = "all_done"
  if not all_done:
    log_print(
        "Pool terminate timeout; lingering_workers=%s" % alive,
        flush=True,
    )
    _sigkill_pool_worker_pids(alive, context=context, blocking_reap_s=0.5)
    outcome = "sigkill"
    all_done, alive = _wait_pool_processes_bounded(active_pool, timeout_s)
    if not all_done and alive:
      log_print(
          "Pool terminate still lingering after SIGKILL context=%s workers=%s"
          % (context or "pool", alive),
          flush=True,
      )
      outcome = "timeout"
  if kill_workers_first or "recover" in str(context or ""):
    log_print(
        "INFO: pool_recover terminate outcome=%s context=%s"
        % (outcome, context or "pool"),
        flush=True,
    )
  _reap_pool_worker_pids(active_pool, timeout_s=min(5.0, float(timeout_s)), context=context)
  # RC-JT: orphans outside pool._pool (recycle races, census kills, abandoned
  # cohorts) stay STAT=Z under [main] if we only waitpid tracked workers.
  # Mirror the abandon branch — PID-specific /proc reap, never waitpid(-1).
  try:
    reap_zombie_children_of_self(context=context or "pool")
  except Exception:
    pass
  return all_done


def _ingest_pool_dispatch_probe_worker(_sentinel: Any) -> Any:
  """
  Picklable no-op task proving the pool taskqueue can dequeue work.
  
  Args:
    _sentinel (Any):  sentinel passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _ingest_pool_dispatch_probe_worker(None)  # doctest: +SKIP
  """
  del _sentinel
  return True


def probe_ingest_pool_dispatch(
  pool: Any,
  timeout_s: float = 10.0,
  *,
  context: str = "",
) -> Any:
  """
  Return True when a trivial ``apply_async`` completes within ``timeout_s``.
  
  Args:
    pool (Any): Live handle (pool, client, or connection).
    timeout_s (float): Floating-point value for timeout s.
    context (str): String for context.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Raises:
    RuntimeError: Raised when ``probe_ingest_pool_dispatch`` hits a
    ``RuntimeError`` failure path.
  
  Examples:
    >>> probe_ingest_pool_dispatch(None, 0, "x")  # doctest: +SKIP
  """
  apply_async = getattr(pool, "apply_async", None)
  if not callable(apply_async):
    log_print(
        "ERROR: pool_recover respawn dispatch_probe failed missing apply_async "
        "context=%s"
        % (context or "pool"),
        flush=True,
    )
    return False
  try:
    async_result = apply_async(_ingest_pool_dispatch_probe_worker, (None,))
    get_fn = getattr(async_result, "get", None)
    if not callable(get_fn):
      raise RuntimeError("async result missing get")
    get_fn(timeout=max(0.5, float(timeout_s)))
    log_print(
        "INFO: pool_recover respawn dispatch_probe ok context=%s"
        % (context or "pool"),
        flush=True,
    )
    return True
  except Exception as exc:
    err_s = str(exc).strip() or type(exc).__name__
    log_print(
        "ERROR: pool_recover respawn dispatch_probe failed context=%s err=%s"
        % (context or "pool", err_s),
        flush=True,
    )
    return False


def maintain_ingest_pool_after_supervisor_retire(
  pool: Any,
  *,
  pool_health_context: Any | None = None,
  recreate_pool_fn: Any | None = None,
) -> Any:
  """
  Post-retire health check when ``maxtasksperchild=0`` (supervisor SIGTERM.
  
    retire).
  
  Args:
    pool (Any): Live handle (pool, client, or connection).
    pool_health_context (Any | None): One of ``Any``, ``None``.
    recreate_pool_fn (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> maintain_ingest_pool_after_supervisor_retire(None, None, None)
  """
  import hpcperfstats.dbload.lib.conf_parser as cfg

  global _last_post_retire_maintain_monotonic

  if pool is None:
    return pool
  if cfg.get_sync_ingest_pool_maxtasksperchild() > 0:
    return pool
  now = time.monotonic()
  workers_busy = not pool_workers_all_idle(pool)
  if (
      workers_busy
      and _last_post_retire_maintain_monotonic > 0.0
      and (now - _last_post_retire_maintain_monotonic)
      < _POST_RETIRE_MAINTAIN_COALESCE_S
  ):
    log_print(
        "INFO: post_retire_maintenance coalesced reason=workers_busy "
        "window_s=%.1f"
        % _POST_RETIRE_MAINTAIN_COALESCE_S,
        flush=True,
    )
    return pool
  _last_post_retire_maintain_monotonic = now
  reclaim_excess_ingest_pool_children(
      pool,
      context="post_retire_maintenance",
  )
  reap_pool_worker_pids(pool, context="post_retire_maintenance")
  reap_zombie_children_of_self(context="post_retire_maintenance")
  dead_procs = list(_iter_dead_pool_worker_processes(pool))
  metrics = _pool_recycle_gate_metrics(
      pool,
      dead_procs,
      pool_health_context=pool_health_context,
  )
  if (
      metrics["gap"] > 0
      or metrics["alive"] < metrics["expected_total"] - metrics["dead_n"]
  ):
    log_print(
        "WARN: ingest pool replacement lagging alive=%d expected_total=%d "
        "materialized=%d gap=%d dead_n=%d context=post_retire_maintenance"
        % (
            metrics["alive"],
            metrics["expected_total"],
            metrics["materialized"],
            metrics["gap"],
            metrics["dead_n"],
        ),
        flush=True,
    )
    # RC-M: refuse further retire/swap while replacement is still lagging.
    return pool
  # Busy pool: skip dispatch_probe — a 10s apply_async behind long in-flight
  # tasks raises TimeoutError and was mis-read as a dead taskqueue, driving
  # reclaim/SIGKILL thrash toward exit 124.
  if not pool_workers_all_idle(pool):
    log_print(
        "INFO: post_retire_maintenance skip_probe reason=workers_busy",
        flush=True,
    )
    return pool
  if not probe_ingest_pool_dispatch(pool, context="post_retire_maintenance"):
    if pool_workers_all_idle(pool) and callable(recreate_pool_fn):
      log_print(
          "WARN: ingest pool dispatch_probe failed after retire; proactive swap",
          flush=True,
      )
      try:
        # RC-N: abandon+kill old workers (pool._pool + PPID census) before recreate.
        terminate_pool_bounded(
            pool,
            timeout_s=5.0,
            context="proactive_swap",
            kill_workers_first=True,
            abandon_after_kill=True,
        )
        new_pool = recreate_pool_fn()
        reclaim_excess_ingest_pool_children(
            new_pool,
            context="post_proactive_swap",
        )
        return new_pool
      except Exception as exc:
        log_print(
            "ERROR: ingest pool proactive swap failed err=%s"
            % (str(exc).strip() or type(exc).__name__),
            flush=True,
        )
  return pool


def close_pool_bounded(
  active_pool: Any,
  timeout_s: float = 30.0,
  *,
  force_terminate: bool = False,
  pool_worker_cmdline_mark: str | None = None,
) -> Any:
  """
  Close a pool with a bounded join; terminate when workers already exited.

  When ``force_terminate=True``, routes through ``terminate_pool_bounded``
  with ``abandon_after_kill=True`` so metrics/teardown never enters stdlib
  ``Pool.terminate()``.

  Args:
    active_pool (Any): Live ``multiprocessing.Pool``.
    timeout_s (float): Join budget for the graceful close path.
    force_terminate (bool): Skip close/join; abandon-kill instead.
    pool_worker_cmdline_mark (str | None): PPID census mark forwarded on
      force/abandon terminate.

  Returns:
    Any: ``True`` when workers exited (or abandon completed).

  Examples:
    >>> close_pool_bounded(
    ...     None, 0, force_terminate=True,
    ...     pool_worker_cmdline_mark="[worker:metrics-pool]",
    ... )  # doctest: +SKIP
  """
  if active_pool is None:
    return True
  if force_terminate or dead_pool_worker_pids(active_pool):
    return terminate_pool_bounded(
        active_pool,
        timeout_s,
        abandon_after_kill=bool(force_terminate),
        kill_workers_first=bool(force_terminate),
        context="close_force_terminate" if force_terminate else "",
        pool_worker_cmdline_mark=pool_worker_cmdline_mark,
    )
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
    return terminate_pool_bounded(
        active_pool,
        timeout_s,
        pool_worker_cmdline_mark=pool_worker_cmdline_mark,
    )
  return all_done


def _stall_warning_thresholds(stall_abort_after: Any) -> Any:
  """
  50% and 75% poll-timeout counts for one-shot stall warnings.
  
  Args:
    stall_abort_after (Any): Stall abort after passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _stall_warning_thresholds(None)  # doctest: +SKIP
  """
  abort_after = max(1, int(stall_abort_after))
  return (
      max(1, abort_after // 2),
      max(1, (abort_after * 3) // 4),
  )


def imap_unordered_watch_pool(
  pool: Any,
  fn: Any,
  iterable: Any,
  *,
  poll_timeout_s: Any | None = None,
  stall_abort_after_timeouts: Any | None = None,
  context: str = "",
  on_stall_warning: Any | None = None,
  on_stall_poll: Any | None = None,
  on_stall_fatal_summary: Any | None = None,
  pool_health_context: Any | None = None,
) -> Iterator[Any]:
  """
  Like ``pool.imap_unordered`` but abort when a worker dies (OOM-safe).
  
  Args:
    pool (Any): Live handle (pool, client, or connection).
    fn (Any): Callable invoked by this helper.
    iterable (Any): Iterable passed to this helper.
    poll_timeout_s (Any | None): One of ``Any``, ``None``.
    stall_abort_after_timeouts (Any | None): One of ``Any``, ``None``.
    context (str): String for context.
    on_stall_warning (Any | None): One of ``Any``, ``None``.
    on_stall_poll (Any | None): One of ``Any``, ``None``.
    on_stall_fatal_summary (Any | None): One of ``Any``, ``None``.
    pool_health_context (Any | None): One of ``Any``, ``None``.
  
  Yields:
    Iterator[Any]: Value produced by this call (type depends on inputs).
  
  Raises:
    MultiprocessingPoolStallError: Raised when ``imap_unordered_watch_pool``
    hits a ``MultiprocessingPoolStallError`` failure path.
  
  Examples:
    >>> imap_unordered_watch_pool(0)  # doctest: +SKIP
  """
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

  def _abort_pool_health() -> None:
    """
    Internal helper to handle abort pool health.
    
    Returns:
      None
    
    Examples:
      >>> _abort_pool_health()  # doctest: +SKIP
    """
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
        except Exception as exc:
          _log_on_stall_poll_failure(exc, context=context)
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


_IDLE_POOL_TASKQUEUE_DEAD_CAUSE = "idle_pool_taskqueue_dead"
# Wall-clock budget for sync one-shot recover (RC-F/G/H); never soft-hang forever.
IDLE_POOL_RECOVER_WALL_S = 30.0
# Max successful idle-pool recovers per sliding-window imap session.
IDLE_POOL_RECOVER_MAX = 3
# Identical skip_no pending after this many probe-ok recovers → path soft-fail
# (not process exit 124). Aligns with recover cap by default.
IDLE_POOL_UNHEALED_RECOVER_MAX = 3


def imap_sliding_window_watch_pool(
  pool: Any,
  fn: Any,
  paths: Any,
  *,
  max_inflight: Any,
  poll_timeout_s: Any | None = None,
  stall_abort_polls_fn: Any | None = None,
  context: str = "",
  on_stall_warning: Any | None = None,
  on_stall_poll: Any | None = None,
  on_stall_fatal_summary: Any | None = None,
  pool_health_context: Any | None = None,
  on_in_flight_change: Any | None = None,
  supplement_paths_fn: Any | None = None,
  on_idle_pool_ghost_fatal: Any | None = None,
  on_reconcile_redispatch: Any | None = None,
  resolve_reconcile_skip_result: Any | None = None,
  on_idle_pool_stuck_after_redispatch: Any | None = None,
  skip_idle_pool_recover_fn: Any | None = None,
  soft_fail_unhealed_paths_fn: Any | None = None,
) -> Iterator[Any]:
  """
  Dispatch pool work with at most ``max_inflight`` concurrent ``apply_async``.
  
    tasks.
  
  Refills idle worker slots from ``paths`` in FIFO order (sliding window). When
    the
  primary ``paths`` iterator is exhausted, optional ``supplement_paths_fn`` may
    return
  additional paths (giant pool supplement). Stall abort threshold is recomputed
    from
  the current in-flight path set on each poll when ``stall_abort_polls_fn`` is
    provided.
  
  When a full-redispatch reconcile thrash leaves workers idle with the same
    pending
  set, optional ``on_idle_pool_stuck_after_redispatch`` may recreate the Pool
    and
  rebuild ``pending_async`` (up to ``IDLE_POOL_RECOVER_MAX`` successful recovers
  per sliding-window session; each success resets ``pool_recover_attempted``).
  
  Optional ``skip_idle_pool_recover_fn(pending_paths)`` returning a non-empty
    reason
  skips recover / ghost fatal while workers are blocked in populate_wait with
    live
  pending work (prevents false-positive exit 124).
  
  When the same pending normpaths remain after
    ``IDLE_POOL_UNHEALED_RECOVER_MAX``
  probe-ok recovers (or recover cap is hit with non-empty pending), those paths
    are
  soft-failed via ``soft_fail_unhealed_paths_fn`` (or a path-as-item default)
    and the
  imap session continues — exit **124** is reserved for recover wall / probe
    fail /
  empty soft-fail at cap (true taskqueue death).
  
  Args:
    pool (Any): Live handle (pool, client, or connection).
    fn (Any): Callable invoked by this helper.
    paths (Any): Iterable of filesystem paths as strings.
    max_inflight (Any): Max inflight passed to this helper.
    poll_timeout_s (Any | None): One of ``Any``, ``None``.
    stall_abort_polls_fn (Any | None): One of ``Any``, ``None``.
    context (str): String for context.
    on_stall_warning (Any | None): One of ``Any``, ``None``.
    on_stall_poll (Any | None): One of ``Any``, ``None``.
    on_stall_fatal_summary (Any | None): One of ``Any``, ``None``.
    pool_health_context (Any | None): One of ``Any``, ``None``.
    on_in_flight_change (Any | None): One of ``Any``, ``None``.
    supplement_paths_fn (Any | None): One of ``Any``, ``None``.
    on_idle_pool_ghost_fatal (Any | None): One of ``Any``, ``None``.
    on_reconcile_redispatch (Any | None): One of ``Any``, ``None``.
    resolve_reconcile_skip_result (Any | None): One of ``Any``, ``None``.
    on_idle_pool_stuck_after_redispatch (Any | None): One of ``Any``,
    ``None``.
    skip_idle_pool_recover_fn (Any | None): One of ``Any``, ``None``.
    soft_fail_unhealed_paths_fn (Any | None): One of ``Any``, ``None``.
  
  Yields:
    Iterator[Any]: Value produced by this call (type depends on inputs).
  
  Raises:
    RuntimeError: Raised when ``imap_sliding_window_watch_pool`` hits a
    ``RuntimeError`` failure path.
  
  Examples:
    >>> imap_sliding_window_watch_pool(0)  # doctest: +SKIP
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
  full_redispatch_thrash_seen = False
  pool_recover_attempted = False
  pool_recover_count = 0
  unhealed_recover_streak = 0
  idle_recover_skip_logged = False
  idle_redispatch_skip_logged = False
  duplicate_dispatch_warned = set()
  duplicate_dispatch_suppressed_n = {}
  active_pool = pool

  def _pending_norm_fingerprint(paths: Any | None = None) -> Any:
    """
    Internal helper to handle pending norm fingerprint.
    
    Args:
      paths (Any | None): One of ``Any``, ``None``.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _pending_norm_fingerprint(None)  # doctest: +SKIP
    """
    if paths is None:
      paths = list(pending_async.values())
    return frozenset(
        ingest_path_normpath(path)
        for path in (paths or ())
        if path
    )

  def _soft_fail_unhealed_paths(paths: Any, *, escalate_reason: Any) -> Any:
    """
    Drop stuck pending paths from the session; return (path, item) yields.
    
    Args:
      paths (Any): Iterable of filesystem paths as strings.
      escalate_reason (Any): Escalate reason passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _soft_fail_unhealed_paths(None, None)  # doctest: +SKIP
    """
    nonlocal unhealed_recover_streak, pool_recover_attempted, pool_recover_count
    nonlocal full_redispatch_thrash_seen, idle_reconcile_rounds
    nonlocal idle_polls_since_reconcile, consecutive_timeouts
    nonlocal polls_since_last_yield, idle_pool_warned
    path_list = [path for path in (paths or ()) if path]
    if not path_list:
      return []
    if callable(soft_fail_unhealed_paths_fn):
      try:
        packed = list(soft_fail_unhealed_paths_fn(path_list) or ())
      except Exception:
        packed = []
    else:
      packed = [(path, path) for path in path_list]
    if not packed:
      return []
    drop_norms = {
        ingest_path_normpath(path)
        for path, _item in packed
        if path
    }
    for async_result, path in list(pending_async.items()):
      if ingest_path_normpath(path) in drop_norms:
        pending_async.pop(async_result, None)
    sample = [
        os.path.basename(str(path))
        for path, _item in packed[:5]
        if path
    ]
    log_print(
        "ERROR: pool imap idle reconcile path soft-fail "
        "reason=idle_pool_unhealed_after_recover escalate=%s "
        "path_n=%d recover_count=%d/%d unhealed_streak=%d "
        "pending_sample=%s context=%s"
        % (
            escalate_reason,
            len(packed),
            int(pool_recover_count),
            int(IDLE_POOL_RECOVER_MAX),
            int(unhealed_recover_streak),
            sample,
            context or "pool",
        ),
        flush=True,
    )
    unhealed_recover_streak = 0
    pool_recover_attempted = False
    pool_recover_count = 0
    full_redispatch_thrash_seen = False
    idle_reconcile_rounds = 0
    idle_polls_since_reconcile = 0
    consecutive_timeouts = 0
    polls_since_last_yield = 0
    idle_pool_warned = False
    return packed

  def _idle_recover_skip_reason(pending_paths: Any) -> Any:
    """
    Internal helper to handle idle recover skip reason.
    
    Args:
      pending_paths (Any): Iterable of filesystem paths as strings.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _idle_recover_skip_reason(None)  # doctest: +SKIP
    """
    nonlocal idle_recover_skip_logged
    if not callable(skip_idle_pool_recover_fn):
      return ""
    try:
      reason = skip_idle_pool_recover_fn(pending_paths) or ""
    except Exception:
      return ""
    reason = str(reason).strip()
    if not reason:
      return ""
    if not idle_recover_skip_logged:
      idle_recover_skip_logged = True
      log_print(
          "INFO: pool imap idle reconcile pool_recover skipped reason=%s "
          "pending_async_n=%d context=%s"
          % (reason, len(pending_paths or ()), context or "pool"),
          flush=True,
      )
    return reason

  def _abort_pool_health() -> None:
    """
    Internal helper to handle abort pool health.
    
    Returns:
      None
    
    Examples:
      >>> _abort_pool_health()  # doctest: +SKIP
    """
    ctx = dict(health_ctx)
    ctx["active_pool"] = active_pool
    if ctx.get("in_flight_sample") is None:
      sample_fn = ctx.get("in_flight_sample_fn")
      if callable(sample_fn):
        ctx["in_flight_sample"] = sample_fn()
    abort_if_pool_workers_dead(
        active_pool,
        context=context,
        pool_health_context=ctx,
    )

  def _in_flight_paths() -> Any:
    """
    Internal helper to handle in flight paths.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _in_flight_paths()  # doctest: +SKIP
    """
    return list(pending_async.values())

  def _update_stall_abort_from_in_flight() -> None:
    """
    Internal helper to update the stall abort from in flight.
    
    Returns:
      None
    
    Examples:
      >>> _update_stall_abort_from_in_flight()  # doctest: +SKIP
    """
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

  def _sync_active_pool_from_health_ctx() -> None:
    """
    Adopt proactive-swap / recover pool rewired into health_ctx (RC-N).
    
    Returns:
      None
    
    Examples:
      >>> _sync_active_pool_from_health_ctx()  # doctest: +SKIP
    """
    nonlocal active_pool
    candidate = health_ctx.get("active_pool")
    if candidate is None:
      candidate = health_ctx.get("ingest_pool")
    if candidate is not None and candidate is not active_pool:
      active_pool = candidate
      health_ctx["active_pool"] = candidate
      if "ingest_pool" in health_ctx:
        health_ctx["ingest_pool"] = candidate

  def _dispatch_path(path: str) -> Any:
    """
    Submit one path unless the same normpath is already in ``pending_async``.
    
    Args:
      path (str): String for path.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Raises:
      RuntimeError: Raised when ``_dispatch_path`` hits a ``RuntimeError``
      failure path.
    
    Examples:
      >>> _dispatch_path("x")  # doctest: +SKIP
    """
    if not path:
      return False
    _sync_active_pool_from_health_ctx()
    apply_async = getattr(active_pool, "apply_async", None)
    if not callable(apply_async):
      raise RuntimeError("pool missing apply_async for sliding window dispatch")
    norm = ingest_path_normpath(path)
    pending_normpaths = pending_ingest_normpaths(pending_async)
    if norm in pending_normpaths:
      suppressed_n = duplicate_dispatch_suppressed_n.get(norm, 0) + 1
      duplicate_dispatch_suppressed_n[norm] = suppressed_n
      if norm not in duplicate_dispatch_warned:
        duplicate_dispatch_warned.add(norm)
        log_print(
            "WARN: pool imap duplicate dispatch suppressed path=%s "
            "suppressed_n=%d context=%s"
            % (
                ingest_path_dispatch_label(norm),
                suppressed_n,
                context or "pool",
            ),
            flush=True,
        )
      return False
    async_result = apply_async(fn, (path,))
    pending_async[async_result] = path
    return True

  def _submit_until_cap() -> None:
    """
    Internal helper to handle submit until cap.
    
    Returns:
      None
    
    Raises:
      RuntimeError: Raised when ``_submit_until_cap`` hits a ``RuntimeError``
      failure path.
    
    Examples:
      >>> _submit_until_cap()  # doctest: +SKIP
    """
    _sync_active_pool_from_health_ctx()
    apply_async = getattr(active_pool, "apply_async", None)
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
        dispatched_any = False
        for supp_path in supplement_paths:
          if len(pending_async) >= max_inflight:
            break
          if _dispatch_path(supp_path):
            dispatched_any = True
        # Duplicate-only supplement must not busy-spin the refill loop.
        if not dispatched_any:
          break
        continue
      else:
        if _dispatch_path(path):
          continue
        # Primary path suppressed as duplicate — advance to next path.
        continue

  def _handle_stall_poll() -> None:
    """
    Internal helper to handle the stall poll.
    
    Returns:
      None
    
    Raises:
      MultiprocessingPoolStallError: Raised when ``_handle_stall_poll`` hits a
      ``MultiprocessingPoolStallError`` failure path.
    
    Examples:
      >>> _handle_stall_poll()  # doctest: +SKIP
    """
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
    except Exception as exc:
      _log_on_stall_poll_failure(exc, context=context)
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

  def _try_pool_recover_after_thrash() -> Any:
    """
    Pool recreate after full-redispatch thrash (capped per imap session).
    
    Returns ``None`` on skip / already-in-flight, or a (possibly empty) list of
    ``(path, item)`` collected via skip / unhealed soft-fail on success. Raises
    stall exit **124** only for recover wall / probe fail / empty soft-fail at
    recover cap (true taskqueue death). Identical pending after
    ``IDLE_POOL_UNHEALED_RECOVER_MAX`` probe-ok recovers soft-fails those paths
    instead of burning process exit.
    
    Returns:
      Any: Open return polymorphism from ``_try_pool_recover_after_thrash``:
      concrete type depends on inputs and branch (mapping, scalar, handle, or
      ``None``-like empty).
    
    Raises:
      MultiprocessingPoolStallError: Raised when
      ``_try_pool_recover_after_thrash`` hits a
      ``MultiprocessingPoolStallError`` failure path.
      exc: Raised when ``_try_pool_recover_after_thrash`` hits a ``exc``
      failure path.
    
    Examples:
      >>> _try_pool_recover_after_thrash()  # doctest: +SKIP
    """
    nonlocal active_pool, pool_recover_attempted, pool_recover_count
    nonlocal full_redispatch_thrash_seen
    nonlocal consecutive_timeouts, polls_since_last_yield, idle_pool_warned
    nonlocal idle_reconcile_rounds, idle_polls_since_reconcile
    nonlocal unhealed_recover_streak
    if not callable(on_idle_pool_stuck_after_redispatch):
      return None
    if pool_recover_count >= int(IDLE_POOL_RECOVER_MAX):
      pending_cap_paths = list(_in_flight_paths())
      cap_count = int(pool_recover_count)
      soft_failed = _soft_fail_unhealed_paths(
          pending_cap_paths,
          escalate_reason="recover_cap",
      )
      if soft_failed:
        log_print(
            "ERROR: pool imap idle reconcile pool_recover cap exceeded "
            "recover_count=%d max=%d pending_async_n=%d "
            "action=path_soft_fail context=%s"
            % (
                cap_count,
                int(IDLE_POOL_RECOVER_MAX),
                len(pending_async),
                context or "pool",
            ),
            flush=True,
        )
        return soft_failed
      log_print(
          "ERROR: pool imap idle reconcile pool_recover cap exceeded "
          "recover_count=%d max=%d pending_async_n=%d context=%s"
          % (
              cap_count,
              int(IDLE_POOL_RECOVER_MAX),
              len(pending_async),
              context or "pool",
          ),
          flush=True,
      )
      raise MultiprocessingPoolStallError(
          "idle pool recover cap exceeded recover_count=%d max=%d"
          % (cap_count, int(IDLE_POOL_RECOVER_MAX)),
          dead_pids=[],
          context=context,
          exit_code=124,
          likely_cause=_IDLE_POOL_TASKQUEUE_DEAD_CAUSE,
      )
    if pool_recover_attempted:
      return None
    pending_before = len(pending_async)
    pending_paths = list(_in_flight_paths())
    pending_norms_before = _pending_norm_fingerprint(pending_paths)
    if _idle_recover_skip_reason(pending_paths):
      return None
    pool_recover_attempted = True
    pending_sample = [
        os.path.basename(str(path))
        for path in pending_paths[:5]
        if path
    ]
    log_print(
        "INFO: pool imap idle reconcile pool_recover pending_async_n=%d "
        "recover_count=%d/%d pending_sample=%s context=%s"
        % (
            pending_before,
            int(pool_recover_count) + 1,
            int(IDLE_POOL_RECOVER_MAX),
            pending_sample,
            context or "pool",
        ),
        flush=True,
    )
    # RC-F/G/H: wall-clock abort — never soft-hang MainThread on recover.
    recover_box = {}
    recover_error = {}

    def _run_recover_callback() -> None:
      """
      Internal helper to run the recover callback.
      
      Returns:
        None
      
      Examples:
        >>> _run_recover_callback()  # doctest: +SKIP
      """
      try:
        recover_box["value"] = on_idle_pool_stuck_after_redispatch(
            active_pool,
            pending_paths,
            pending_async,
            fn,
        )
      except BaseException as exc:
        recover_error["exc"] = exc

    recover_thread = threading.Thread(
        target=_run_recover_callback,
        name="idle-pool-recover",
        daemon=True,
    )
    recover_thread.start()
    recover_thread.join(timeout=float(IDLE_POOL_RECOVER_WALL_S))
    if recover_thread.is_alive():
      log_print(
          "ERROR: pool imap idle reconcile pool_recover exceeded wall_s=%.1f "
          "context=%s"
          % (IDLE_POOL_RECOVER_WALL_S, context or "pool"),
          flush=True,
      )
      raise MultiprocessingPoolStallError(
          "idle pool recover exceeded wall_s=%.1f" % IDLE_POOL_RECOVER_WALL_S,
          dead_pids=[],
          context=context,
          exit_code=124,
          likely_cause=_IDLE_POOL_TASKQUEUE_DEAD_CAUSE,
      )
    if "exc" in recover_error:
      exc = recover_error["exc"]
      if isinstance(exc, MultiprocessingPoolStallError):
        raise exc
      log_print(
          "ERROR: pool imap idle reconcile pool_recover failed: %s context=%s"
          % (exc, context or "pool"),
          flush=True,
      )
      raise MultiprocessingPoolStallError(
          "idle pool recover failed: %s" % exc,
          dead_pids=[],
          context=context,
          exit_code=124,
          likely_cause=_IDLE_POOL_TASKQUEUE_DEAD_CAUSE,
      )
    recovered = recover_box.get("value")
    if not isinstance(recovered, dict):
      raise MultiprocessingPoolStallError(
          "idle pool recover invalid return type",
          dead_pids=[],
          context=context,
          exit_code=124,
          likely_cause=_IDLE_POOL_TASKQUEUE_DEAD_CAUSE,
      )
    new_pool = recovered.get("pool")
    collected = list(recovered.get("collected") or ())
    if new_pool is None:
      raise MultiprocessingPoolStallError(
          "idle pool recover returned no replacement pool",
          dead_pids=[],
          context=context,
          exit_code=124,
          likely_cause=_IDLE_POOL_TASKQUEUE_DEAD_CAUSE,
      )
    active_pool = new_pool
    health_ctx["active_pool"] = new_pool
    if "ingest_pool" in health_ctx:
      health_ctx["ingest_pool"] = new_pool
    full_redispatch_thrash_seen = False
    idle_reconcile_rounds = 0
    idle_polls_since_reconcile = 0
    consecutive_timeouts = 0
    polls_since_last_yield = 0
    idle_pool_warned = False
    # Allow a later thrash to recover again (sticky-attempted was exit-124 RC).
    pool_recover_attempted = False
    pool_recover_count += 1
    pending_norms_after = _pending_norm_fingerprint()
    if (
        pending_norms_before
        and pending_norms_after
        and pending_norms_after == pending_norms_before
    ):
      unhealed_recover_streak += 1
    else:
      unhealed_recover_streak = 0
    log_print(
        "INFO: pool imap idle reconcile pool_recover done "
        "collected_n=%d pending_async_n=%d recover_count=%d/%d "
        "unhealed_streak=%d/%d context=%s"
        % (
            len(collected),
            len(pending_async),
            int(pool_recover_count),
            int(IDLE_POOL_RECOVER_MAX),
            int(unhealed_recover_streak),
            int(IDLE_POOL_UNHEALED_RECOVER_MAX),
            context or "pool",
        ),
        flush=True,
    )
    if (
        unhealed_recover_streak >= int(IDLE_POOL_UNHEALED_RECOVER_MAX)
        and pending_async
    ):
      soft_failed = _soft_fail_unhealed_paths(
          list(pending_async.values()),
          escalate_reason="unhealed_streak",
      )
      if soft_failed:
        collected.extend(soft_failed)
      elif not soft_failed and pending_async:
        # Caller refused soft-fail while pending remains — treat as taskqueue death.
        raise MultiprocessingPoolStallError(
            "idle pool unhealed recover soft-fail empty recover_count=%d"
            % int(pool_recover_count),
            dead_pids=[],
            context=context,
            exit_code=124,
            likely_cause=_IDLE_POOL_TASKQUEUE_DEAD_CAUSE,
        )
    _submit_until_cap()
    _update_stall_abort_from_in_flight()
    return collected

  def _attempt_idle_reconcile(*, queue_yields: bool = False) -> Any:
    """
    Internal helper to handle attempt idle reconcile.
    
    Args:
      queue_yields (bool): Boolean flag for queue yields.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _attempt_idle_reconcile(True)  # doctest: +SKIP
    """
    nonlocal idle_reconcile_rounds, idle_polls_since_reconcile
    nonlocal consecutive_timeouts, polls_since_last_yield, idle_pool_warned
    nonlocal full_redispatch_thrash_seen, idle_redispatch_skip_logged
    if not pending_async or not pool_workers_all_idle(active_pool):
      idle_polls_since_reconcile = 0
      return []
    pending_before = len(pending_async)
    pending_paths = _in_flight_paths()
    # populate_wait / populate_enqueue: workers look wchan-idle but are live.
    # Skip pool_recover AND idle redispatch (same skip_fn); orphan collect OK.
    skip_reason = _idle_recover_skip_reason(pending_paths)
    # After a full-redispatch thrash with still-idle workers, recover once
    # instead of burning remaining redispatch rounds into a dead taskqueue.
    if (
        not skip_reason
        and full_redispatch_thrash_seen
        and not pool_recover_attempted
        and callable(on_idle_pool_stuck_after_redispatch)
    ):
      recovered = _try_pool_recover_after_thrash()
      if recovered is not None:
        if recovered and queue_yields:
          reconcile_pending_yields.extend(recovered)
        return recovered
      # Recover failed — still allow one more redispatch cycle below.
    allow_redispatch = idle_reconcile_rounds < max_reconcile_rounds
    if full_redispatch_thrash_seen and pool_recover_attempted:
      allow_redispatch = False
    if skip_reason:
      if not idle_redispatch_skip_logged:
        idle_redispatch_skip_logged = True
        log_print(
            "INFO: pool imap idle reconcile redispatch skipped reason=%s "
            "pending_async_n=%d context=%s"
            % (skip_reason, len(pending_async), context or "pool"),
            flush=True,
        )
      allow_redispatch = False
    collected, redispatched = reconcile_idle_pending_async(
        active_pool,
        pending_async,
        fn,
        resolve_skip_result=resolve_reconcile_skip_result,
        on_redispatch=on_reconcile_redispatch,
        allow_redispatch=allow_redispatch,
    )
    idle_polls_since_reconcile = 0
    if collected:
      consecutive_timeouts = 0
      polls_since_last_yield = 0
      idle_pool_warned = False
      idle_reconcile_rounds = 0
      full_redispatch_thrash_seen = False
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
      if (
          redispatched >= pending_before
          and pending_before > 0
          and len(pending_async) == pending_before
      ):
        full_redispatch_thrash_seen = True
        if (
            callable(on_idle_pool_stuck_after_redispatch)
            and not pool_recover_attempted
        ):
          recovered = _try_pool_recover_after_thrash()
          if recovered is not None:
            if recovered and queue_yields:
              reconcile_pending_yields.extend(recovered)
            return recovered
      consecutive_timeouts = 0
      polls_since_last_yield = 0
      _submit_until_cap()
      _update_stall_abort_from_in_flight()
    return []

  def _idle_reconcile_for_recycle() -> None:
    """
    Internal helper to handle idle reconcile for recycle.
    
    Returns:
      None
    
    Examples:
      >>> _idle_reconcile_for_recycle()  # doctest: +SKIP
    """
    _attempt_idle_reconcile(queue_yields=True)

  health_ctx["idle_reconcile_fn"] = _idle_reconcile_for_recycle

  def _check_idle_pool_ghost() -> None:
    """
    Internal helper to check the idle pool ghost.
    
    Returns:
      None
    
    Raises:
      MultiprocessingPoolStallError: Raised when ``_check_idle_pool_ghost``
      hits a ``MultiprocessingPoolStallError`` failure path.
    
    Examples:
      >>> _check_idle_pool_ghost()  # doctest: +SKIP
    """
    nonlocal idle_pool_warned, polls_since_last_yield
    if not pending_async:
      return
    if not pool_workers_all_idle(active_pool):
      return
    ghost_abort_polls = idle_pool_ghost_abort_polls(stall_abort_after)
    pending_paths = _in_flight_paths()
    if _idle_recover_skip_reason(pending_paths):
      return
    pending_sample = [
        os.path.basename(str(path))
        for path in pending_paths[:5]
        if path
    ]
    wchan_sample = format_pool_worker_wchan_sample(active_pool)
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
    # Do not fatal on thrash+stale-attempted after a successful recover reset
    # rounds to 0/1 — wait for more rounds or recover-cap exhaustion.
    recover_cap_exceeded = pool_recover_count >= int(IDLE_POOL_RECOVER_MAX)
    if recover_cap_exceeded and pending_async:
      soft_failed = _soft_fail_unhealed_paths(
          list(pending_async.values()),
          escalate_reason="ghost_recover_cap",
      )
      if soft_failed:
        reconcile_pending_yields.extend(soft_failed)
        polls_since_last_yield = 0
        idle_pool_warned = False
        return
    if (
        idle_reconcile_rounds < max_reconcile_rounds
        and not recover_cap_exceeded
    ):
      return
    taskqueue_dead = bool(recover_cap_exceeded) or (
        idle_reconcile_rounds >= max_reconcile_rounds
        and full_redispatch_thrash_seen
    )
    likely_cause = (
        _IDLE_POOL_TASKQUEUE_DEAD_CAUSE if taskqueue_dead else ""
    )
    message = (
        "pool imap idle workers with pending async "
        "(context=%s pending_async_n=%d polls_since_yield=%d "
        "pending_sample=%s worker_wchan_sample=%s likely_cause=%s)"
        % (
            _IDLE_POOL_GHOST_CONTEXT,
            len(pending_async),
            int(polls_since_last_yield),
            pending_sample,
            wchan_sample,
            likely_cause or "unknown",
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
        likely_cause=likely_cause,
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
    if pool_workers_all_idle(active_pool):
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
    if pool_workers_all_idle(active_pool) and pending_async:
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
  async_result: Any,
  pool: Any,
  *,
  poll_timeout_s: Any | None = None,
  context: str = "",
  on_stall_poll: Any | None = None,
  pool_health_context: Any | None = None,
) -> Any:
  """
  Like ``AsyncResult.get()`` but abort when a pool worker dies.
  
  Args:
    async_result (Any): Async result passed to this helper.
    pool (Any): Live handle (pool, client, or connection).
    poll_timeout_s (Any | None): One of ``Any``, ``None``.
    context (str): String for context.
    on_stall_poll (Any | None): One of ``Any``, ``None``.
    pool_health_context (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> async_result_get_watch_pool(None, None, None, "x", None, None)
  """
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
  consecutive_timeouts = 0
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
      if on_stall_poll is not None:
        try:
          on_stall_poll(
              consecutive_timeouts,
              context,
              dict(pool_health_context or {}),
          )
        except Exception as exc:
          _log_on_stall_poll_failure(exc, context=context)
      consecutive_timeouts += 1
      continue


def hard_exit_pool_worker_error(exc: MultiprocessingWorkerExitError) -> None:
  """
  Exit immediately after pool worker failure (do not wait on helper threads).
  
  ``sys.exit`` can block while non-daemon threads (for example async DAY_CLOSE
  seal) finish; stall/OOM exit handlers must use ``os._exit`` instead.
  
  Args:
    exc (MultiprocessingWorkerExitError): exc as
    ``MultiprocessingWorkerExitError``.
  
  Returns:
    None
  
  Examples:
    >>> hard_exit_pool_worker_error(None)  # doctest: +SKIP
  """
  likely_cause = getattr(exc, "likely_cause", "") or ""
  diagnostics = dict(getattr(exc, "diagnostics", None) or {})
  # Ghost/stall fatals often have empty dead_workers; prefer exc.likely_cause
  # over inferred ``unknown`` in the diagnostics suffix.
  diag_cause = str(diagnostics.get("likely_cause") or "").strip()
  if likely_cause and diag_cause in ("", "unknown"):
    diagnostics["likely_cause"] = likely_cause
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


_COLD_SYNC_TIMEDB_POOL_MAXTASKSPERCHILD = 1
_INGEST_POOL_KIND_LOG_LABEL = "ingest-pool"


def sync_timedb_spawn_pool_recycle_kwargs(*, pool_kind_log_label: Any) -> Any:
  """
  Return ``maxtasksperchild`` kwargs for a sync_timedb spawn pool kind.
  
  Args:
    pool_kind_log_label (Any): Pool kind log label passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> sync_timedb_spawn_pool_recycle_kwargs(None)  # doctest: +SKIP
  """
  import hpcperfstats.dbload.lib.conf_parser as cfg

  if pool_kind_log_label == _INGEST_POOL_KIND_LOG_LABEL:
    maxtasks = cfg.get_sync_ingest_pool_maxtasksperchild()
    if maxtasks > 0:
      return {"maxtasksperchild": int(maxtasks)}
    return {}
  return {"maxtasksperchild": _COLD_SYNC_TIMEDB_POOL_MAXTASKSPERCHILD}


def create_sync_timedb_spawn_pool(
  *,
  processes: Any,
  initializer: Any,
  initargs: Any,
  pool_kind_log_label: Any | None = None,
) -> Any:
  """
  Create a spawn-context ``Pool`` with pool-kind recycle kwargs.
  
  Args:
    processes (Any): Processes passed to this helper.
    initializer (Any): Initializer passed to this helper.
    initargs (Any): Initargs passed to this helper.
    pool_kind_log_label (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Raises:
    ValueError: Raised when ``create_sync_timedb_spawn_pool`` hits a
    ``ValueError`` failure path.
  
  Examples:
    >>> create_sync_timedb_spawn_pool(None, None, None, None)  # doctest: +SKIP
  """
  label = str(pool_kind_log_label or "").strip()
  if not label:
    raise ValueError("create_sync_timedb_spawn_pool requires pool_kind_log_label")
  return multiprocessing.get_context("spawn").Pool(
      processes=processes,
      initializer=initializer,
      initargs=initargs,
      **sync_timedb_spawn_pool_recycle_kwargs(pool_kind_log_label=label),
  )
