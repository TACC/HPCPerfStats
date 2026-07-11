"""Dedicated populate-pool workers for Redis L2 sealed/tar member streaming."""
from __future__ import annotations

import multiprocessing
import time

import hpcperfstats.dbload.lib.conf_parser as cfg
from hpcperfstats.dbload.lib.multiprocessing_pool_health import (
    _waitpid_pid_nonblocking,
)
from hpcperfstats.dbload.lib.print_utils import log_print
from hpcperfstats.dbload.lib.sync_timedb_ingest_worker_diagnostics import (
    apply_ingest_pool_worker_init,
    clear_worker_stage,
    record_worker_stage,
)

_POPULATE_POOL_CONTROLLER = None


def get_populate_pool_controller():
  return _POPULATE_POOL_CONTROLLER


def set_populate_pool_controller(controller):
  global _POPULATE_POOL_CONTROLLER
  _POPULATE_POOL_CONTROLLER = controller


def reset_populate_pool_controller_for_tests():
  global _POPULATE_POOL_CONTROLLER
  _POPULATE_POOL_CONTROLLER = None


class PopulatePoolController:
  """Spawn workers that BRPOP Redis populate jobs (separate from ingest-pool)."""

  def __init__(self):
    self._shutdown = None
    self._processes = []
    self._script_name = None
    self._registry = None
    self._ctx = None

  def is_running(self):
    """True when at least one populate-pool worker process is alive."""
    return any(proc.is_alive() for proc in self._processes)

  def start(self, *, script_name, registry):
    from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
        archive_members_redis_enabled,
    )

    n_workers = int(cfg.get_sync_archive_members_populate_pool_processes())
    if n_workers <= 0 or not archive_members_redis_enabled():
      return
    self._script_name = script_name
    self._registry = registry
    self._ctx = multiprocessing.get_context("spawn")
    self._shutdown = self._ctx.Event()
    for index in range(n_workers):
      self._spawn_one(index)
    log_print(
        "sync_timedb: populate-pool started workers=%d"
        % len(self._processes),
        flush=True,
    )

  def _spawn_one(self, index):
    proc = self._ctx.Process(
        target=_populate_pool_worker_entry,
        args=(self._script_name, self._registry, self._shutdown),
        name="populate-pool-%d" % index,
        daemon=True,
    )
    proc.start()
    self._processes.append(proc)
    return proc

  def stop(self, *, force=False):
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

  def reap_and_restart(self):
    """Join dead workers and replace them up to the configured pool size."""
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
      if old_pid is not None:
        _waitpid_pid_nonblocking(int(old_pid), timeout_s=0.5)
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


def _populate_pool_worker_entry(script_name, registry, shutdown):
  apply_ingest_pool_worker_init(script_name, "populate-pool", registry)
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      archive_members_populate_queue_brpop,
      reset_archive_members_redis_client_for_tests,
  )
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      execute_archive_members_populate_for_canonical,
  )

  del reset_archive_members_redis_client_for_tests
  while not shutdown.is_set():
    record_worker_stage("", "populate_queue_wait")
    job = archive_members_populate_queue_brpop(timeout_s=1.0)
    if job is None:
      continue
    canonical = str(job.get("canonical") or "")
    if not canonical:
      continue
    try:
      record_worker_stage(canonical, "populate_scan")
      execute_archive_members_populate_for_canonical(canonical)
    except Exception as exc:
      log_print(
          "ERROR: populate-pool scan failed canonical=%s: %s"
          % (canonical, exc),
          flush=True,
      )
    finally:
      clear_worker_stage()
      from hpcperfstats.dbload.lib.sync_timedb_worker_memory import (
          release_spawn_pool_worker_memory,
      )

      release_spawn_pool_worker_memory()
      time.sleep(0)


def shutdown_populate_pool_controller(*, force=False):
  controller = get_populate_pool_controller()
  if controller is not None:
    controller.stop(force=force)
