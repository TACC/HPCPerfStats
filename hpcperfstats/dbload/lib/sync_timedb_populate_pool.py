"""Dedicated populate-pool workers for Redis L2 sealed/tar member streaming."""
from __future__ import annotations

import multiprocessing
import time

import hpcperfstats.dbload.lib.conf_parser as cfg
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

  def is_running(self):
    return bool(self._processes)

  def start(self, *, script_name, registry):
    from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
        archive_members_redis_enabled,
    )

    n_workers = int(cfg.get_sync_archive_members_populate_pool_processes())
    if n_workers <= 0 or not archive_members_redis_enabled():
      return
    ctx = multiprocessing.get_context("spawn")
    self._shutdown = ctx.Event()
    for index in range(n_workers):
      proc = ctx.Process(
          target=_populate_pool_worker_entry,
          args=(script_name, registry, self._shutdown),
          name="populate-pool-%d" % index,
          daemon=True,
      )
      proc.start()
      self._processes.append(proc)
    log_print(
        "sync_timedb: populate-pool started workers=%d"
        % len(self._processes),
        flush=True,
    )

  def stop(self, *, force=False):
    if self._shutdown is not None:
      self._shutdown.set()
    for proc in self._processes:
      if not proc.is_alive():
        continue
      proc.join(timeout=5.0 if not force else 0.0)
      if proc.is_alive() and force:
        proc.terminate()
        proc.join(timeout=2.0)
    self._processes = []
    self._shutdown = None


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
      time.sleep(0)


def shutdown_populate_pool_controller(*, force=False):
  controller = get_populate_pool_controller()
  if controller is not None:
    controller.stop(force=force)
