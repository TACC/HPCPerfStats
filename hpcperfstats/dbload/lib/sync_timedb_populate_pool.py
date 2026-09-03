"""
Dedicated populate-pool threads for in-process sealed/tar member streaming.

Attributes:
  _POPULATE_POOL_CONTROLLER: Process-wide populate controller, or None.
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
  Return the process-wide populate pool controller.

  Returns:
    Any: Current controller, or None before start.

  Examples:
    >>> get_populate_pool_controller() is None or True
    True
  """
  return _POPULATE_POOL_CONTROLLER


def set_populate_pool_controller(controller: Any) -> None:
  """
  Install the process-wide populate pool controller.

  Args:
    controller (Any): Controller instance, or None to clear.

  Returns:
    None

  Examples:
    >>> set_populate_pool_controller(None)
  """
  global _POPULATE_POOL_CONTROLLER
  _POPULATE_POOL_CONTROLLER = controller


def reset_populate_pool_controller_for_tests() -> None:
  """
  Clear the process-wide populate controller for unit tests.

  Returns:
    None

  Examples:
    >>> reset_populate_pool_controller_for_tests()
  """
  global _POPULATE_POOL_CONTROLLER
  _POPULATE_POOL_CONTROLLER = None


class PopulatePoolController:
  """
  Long-lived populate workers submitted on SyncTimedbThreadPool.

  Each worker dequeues in-process populate jobs. The titled pool is the
  only worker set — there is no leftover raw-thread dual path.

  Attributes:
    _pool: Titled SyncTimedbThreadPool that runs populate workers.
    _registry: Worker registry passed into each worker.
    _results: apply_async handles for the long-lived workers.
    _script_name: Process title script name.
    _shutdown: Shutdown event shared with worker loops.
  """

  def __init__(self) -> None:
    """
    Create an idle populate-pool controller.

    Returns:
      None

    Examples:
      >>> PopulatePoolController().is_running()
      False
    """
    self._shutdown = None
    self._results: list[Any] = []
    self._script_name = None
    self._registry = None
    self._pool = None

  def is_running(self) -> bool:
    """
    Return True when at least one submitted populate worker is unfinished.

    Returns:
      bool: True when a pool worker future is still running.

    Examples:
      >>> PopulatePoolController().is_running()
      False
    """
    return self._pool is not None and any(
        not result.ready() for result in self._results
    )

  def start(self, *, script_name: Any, registry: Any) -> None:
    """
    Submit populate workers onto the titled thread pool.

    Args:
      script_name (Any): Script name used for thread titles.
      registry (Any): Worker diagnostics registry.

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
    self._shutdown = threading.Event()
    self._pool = create_sync_timedb_thread_pool(
        max_workers=n_workers,
        thread_role="populate-pool",
        process_title=str(script_name or "sync_timedb.py"),
    )
    self._results = []
    for _index in range(n_workers):
      self._results.append(
          self._pool.apply_async(
              _populate_pool_worker_entry,
              (self._script_name, self._registry, self._shutdown),
          )
      )
    log_print(
        "populate-pool started workers=%d"
        % len(self._results),
        flush=True,
    )

  def stop(self, *, force: bool = False) -> None:
    """
    Signal workers to exit and shut the titled pool down.

    Args:
      force (bool): When True, cancel pending pool futures immediately.

    Returns:
      None

    Examples:
      >>> PopulatePoolController().stop(True)  # doctest: +SKIP
    """
    del force
    if self._shutdown is not None:
      self._shutdown.set()
    if self._pool is not None:
      try:
        self._pool.terminate()
        self._pool.join()
      except Exception:
        pass
    self._results = []
    self._shutdown = None
    self._script_name = None
    self._registry = None
    self._pool = None

  def reap_and_restart(self) -> Any:
    """
    Resubmit finished populate workers up to the configured pool size.

    Returns:
      Any: Number of workers resubmitted, or None when the pool is idle.

    Examples:
      >>> PopulatePoolController().reap_and_restart() is None
      True
    """
    if self._shutdown is None or self._pool is None:
      return
    if self._shutdown.is_set():
      return
    n_workers = int(cfg.get_sync_archive_members_populate_pool_processes())
    if n_workers <= 0:
      return
    kept = [result for result in self._results if not result.ready()]
    restarted = 0
    while len(kept) < n_workers:
      kept.append(
          self._pool.apply_async(
              _populate_pool_worker_entry,
              (self._script_name, self._registry, self._shutdown),
          )
      )
      restarted += 1
      log_print(
          "WARN: populate-pool worker restarted index=%d"
          % restarted,
          flush=True,
      )
    self._results = kept
    return restarted


def _populate_pool_worker_entry(
  script_name: Any,
  registry: Any,
  shutdown: Any,
) -> None:
  """
  Claim and scan populate-queue jobs until shutdown is set.

  Args:
    script_name (Any): Script name for worker titles and init.
    registry (Any): Worker diagnostics registry.
    shutdown (Any): Event that stops the worker loop.

  Returns:
    None

  Examples:
    >>> callable(_populate_pool_worker_entry)
    True
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
  Stop the process-wide populate controller when one is installed.

  Args:
    force (bool): Forwarded to ``PopulatePoolController.stop``.

  Returns:
    None

  Examples:
    >>> shutdown_populate_pool_controller(True)
  """
  controller = get_populate_pool_controller()
  if controller is not None:
    controller.stop(force=force)
