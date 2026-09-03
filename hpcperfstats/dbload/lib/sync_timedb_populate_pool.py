"""
Dedicated populate-pool threads for in-process sealed/tar member streaming.

Attributes:
  _POPULATE_POOL_CONTROLLER: Attribute.
"""
from __future__ import annotations

import threading
import time
from typing import Any

import hpcperfstats.dbload.lib.conf_parser as cfg
from hpcperfstats.dbload.lib.print_utils import log_print
from hpcperfstats.dbload.lib.sync_timedb_ingest_worker_diagnostics import (
    apply_ingest_pool_worker_init,
    clear_worker_stage,
    record_worker_stage,
)
from hpcperfstats.dbload.lib.sync_timedb_session_executor import (
    create_sync_timedb_thread_pool,
)

_POPULATE_POOL_CONTROLLER = None


def get_populate_pool_controller() -> Any:
  """
  Return the populate pool controller.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> get_populate_pool_controller()  # doctest: +SKIP
  """
  return _POPULATE_POOL_CONTROLLER


def set_populate_pool_controller(controller: Any) -> None:
  """
  Set the populate pool controller.
  
  Args:
    controller (Any): Controller passed to this helper.
  
  Returns:
    None
  
  Examples:
    >>> set_populate_pool_controller(None)  # doctest: +SKIP
  """
  global _POPULATE_POOL_CONTROLLER
  _POPULATE_POOL_CONTROLLER = controller


def reset_populate_pool_controller_for_tests() -> None:
  """
  Reset populate pool controller for tests.
  
  Returns:
    None
  
  Examples:
    >>> reset_populate_pool_controller_for_tests()  # doctest: +SKIP
  """
  global _POPULATE_POOL_CONTROLLER
  _POPULATE_POOL_CONTROLLER = None


class _PopulateWorkerHandle:
  """
  Thread handle with the join/is_alive surface populate-pool tests use.

  Attributes:
    pid: Synthetic worker index used in restart logs.
    _thread: Worker thread.
  """

  def __init__(self, thread: threading.Thread, index: int) -> None:
    """
    Wrap one populate worker thread.

    Args:
      thread (threading.Thread): Worker thread.
      index (int): Worker index.

    Returns:
      None

    Examples:
      >>> _PopulateWorkerHandle(threading.Thread(target=lambda: None), 0).pid
      1
    """
    self._thread = thread
    self.pid = int(index) + 1

  def is_alive(self) -> bool:
    """
    Return True while the worker thread is running.

    Returns:
      bool: Thread liveness.

    Examples:
      >>> _PopulateWorkerHandle(threading.Thread(target=lambda: None), 0).is_alive()
      False
    """
    return bool(self._thread.is_alive())

  def join(self, timeout: float | None = None) -> None:
    """
    Join the worker thread.

    Args:
      timeout (float | None): Optional join timeout.

    Returns:
      None

    Examples:
      >>> _PopulateWorkerHandle(threading.Thread(target=lambda: None), 0).join(0)
    """
    self._thread.join(timeout)

  def terminate(self) -> None:
    """
    No-op: threads cannot be terminated.

    Returns:
      None

    Examples:
      >>> _PopulateWorkerHandle(threading.Thread(target=lambda: None), 0).terminate()
    """
    return


class PopulatePoolController:
  """
  Thread workers that dequeue in-process populate jobs.

  Attributes:
    _ctx: Unused leftover attribute kept for test patches.
    _pool: Titled thread pool (titles only; workers are raw threads).
    _processes: Worker handles.
    _registry: Worker registry.
    _script_name: Process title script name.
    _shutdown: Shutdown event.
  """

  def __init__(self) -> None:
    """
    Initialize a new instance.
    
    Returns:
      None
    
    Examples:
      >>> PopulatePoolController()  # doctest: +SKIP
    """
    self._shutdown = None
    self._processes = []
    self._script_name = None
    self._registry = None
    self._ctx = None
    self._pool = None

  def is_running(self) -> Any:
    """
    True when at least one populate-pool worker process is alive.
    
    Returns:
      Any: Open return polymorphism from ``is_running``: concrete type depends
      on inputs and branch (mapping, scalar, handle, or ``None``-like empty).
    
    Examples:
      >>> PopulatePoolController().is_running()  # doctest: +SKIP
    """
    return any(proc.is_alive() for proc in self._processes)

  def start(self, *, script_name: Any, registry: Any) -> None:
    """
    Start background work for this object.
    
    Args:
      script_name (Any): Script name passed to this helper.
      registry (Any): Registry passed to this helper.
    
    Returns:
      None
    
    Examples:
      >>> PopulatePoolController().start(None, None)  # doctest: +SKIP
    """
    n_workers = int(cfg.get_sync_archive_members_populate_pool_processes())
    if n_workers <= 0:
      return
    self._script_name = script_name
    self._registry = registry
    self._ctx = object()
    self._shutdown = threading.Event()
    self._pool = create_sync_timedb_thread_pool(
        max_workers=n_workers,
        thread_role="populate-pool",
        process_title=str(script_name or "sync_timedb.py"),
    )
    for index in range(n_workers):
      self._spawn_one(index)
    log_print(
        "populate-pool started workers=%d"
        % len(self._processes),
        flush=True,
    )

  def _spawn_one(self, index: int) -> Any:
    """
    Internal helper to handle spawn one.
    
    Args:
      index (int): Integer value for index.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> PopulatePoolController()._spawn_one(0)  # doctest: +SKIP
    """
    thread = threading.Thread(
        target=_populate_pool_worker_entry,
        args=(self._script_name, self._registry, self._shutdown),
        name="populate-pool-%d" % index,
        daemon=True,
    )
    thread.start()
    handle = _PopulateWorkerHandle(thread, index)
    self._processes.append(handle)
    return handle

  def stop(self, *, force: bool = False) -> None:
    """
    Stop background work for this object.
    
    Args:
      force (bool): Boolean flag for force.
    
    Returns:
      None
    
    Examples:
      >>> PopulatePoolController().stop(True)  # doctest: +SKIP
    """
    if self._shutdown is not None:
      self._shutdown.set()
    for proc in self._processes:
      # Always join — is_alive() is False for zombies but join still reaps.
      try:
        proc.join(timeout=5.0 if not force else 0.0)
      except Exception:
        pass
      if proc.is_alive() and force:
        try:
          proc.terminate()
        except Exception:
          pass
        try:
          proc.join(timeout=2.0)
        except Exception:
          pass
    self._processes = []
    self._shutdown = None
    self._script_name = None
    self._registry = None
    self._ctx = None
    if self._pool is not None:
      try:
        self._pool.terminate()
        self._pool.join()
      except Exception:
        pass
    self._pool = None

  def reap_and_restart(self) -> Any:
    """
    Join dead workers and replace them up to the configured pool size.
    
    Returns:
      Any: Open return polymorphism from ``reap_and_restart``: concrete type
      depends on inputs and branch (mapping, scalar, handle, or ``None``-like
      empty).
    
    Examples:
      >>> PopulatePoolController().reap_and_restart()  # doctest: +SKIP
    """
    if self._shutdown is None or self._ctx is None:
      return
    if self._shutdown.is_set():
      return
    n_workers = int(cfg.get_sync_archive_members_populate_pool_processes())
    if n_workers <= 0:
      return
    kept = []
    restarted = 0
    for proc in self._processes:
      if proc.is_alive():
        kept.append(proc)
        continue
      old_pid = getattr(proc, "pid", None)
      try:
        proc.join(timeout=0)
      except Exception:
        pass
      log_print(
          "WARN: populate-pool worker restarted pid=%s"
          % (old_pid if old_pid is not None else "-"),
          flush=True,
      )
      restarted += 1
    self._processes = kept
    while len(self._processes) < n_workers:
      self._spawn_one(len(self._processes))
    return restarted


def _populate_pool_worker_entry(
  script_name: Any,
  registry: Any,
  shutdown: Any,
) -> None:
  """
  Internal helper to populate the pool worker entry.
  
  Args:
    script_name (Any): Script name passed to this helper.
    registry (Any): Registry passed to this helper.
    shutdown (Any): Shutdown passed to this helper.
  
  Returns:
    None
  
  Examples:
    >>> _populate_pool_worker_entry(None, None, None)  # doctest: +SKIP
  """
  apply_ingest_pool_worker_init(script_name, "populate-pool", registry)
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_coord import (
      archive_members_populate_queue_claim,
      complete_populate_queue_job,
      requeue_populate_queue_job,
  )
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      execute_archive_members_populate_for_canonical,
  )

  while not shutdown.is_set():
    record_worker_stage("", "populate_queue_wait")
    job = archive_members_populate_queue_claim(timeout_s=1.0)
    if job is None:
      continue
    canonical = str(job.get("canonical") or "")
    day_token = str(job.get("day_token") or "")
    if not canonical:
      requeue_populate_queue_job(job)
      continue
    try:
      record_worker_stage(canonical, "populate_scan")
      execute_archive_members_populate_for_canonical(canonical)
      complete_populate_queue_job(job)
    except Exception as exc:
      log_print(
          "ERROR: populate-pool scan failed canonical=%s day=%s: %s"
          % (canonical, day_token or "?", exc),
          flush=True,
      )
      requeue_populate_queue_job(job)
    finally:
      clear_worker_stage()
      from hpcperfstats.dbload.lib.sync_timedb_worker_memory import (
          release_spawn_pool_worker_memory,
      )

      release_spawn_pool_worker_memory()
      time.sleep(0)


def shutdown_populate_pool_controller(*, force: bool = False) -> None:
  """
  Shutdown populate pool controller.
  
  Args:
    force (bool): Boolean flag for force.
  
  Returns:
    None
  
  Examples:
    >>> shutdown_populate_pool_controller(True)  # doctest: +SKIP
  """
  controller = get_populate_pool_controller()
  if controller is not None:
    controller.stop(force=force)
