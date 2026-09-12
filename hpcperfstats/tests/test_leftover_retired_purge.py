"""Absence tests for leftover retired no-ops deleted in leftover-retired-code-purge.

Names kept only so older tests could import 0/[] stubs must be gone. Live
H17/H23 kick helpers and listend present-tense defaults stay.
"""
from __future__ import annotations

import inspect

from hpcperfstats.dbload.lib import conf_parser as cfg
from hpcperfstats.dbload.lib import sync_timedb_archive_members_coord as coord
from hpcperfstats.dbload.lib import sync_timedb_ingest_timeout as ingest_timeout
from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo
from hpcperfstats.dbload.lib.sync_timedb_day_close_manifest import (
    DayCloseManifestCoordinator,
)
from hpcperfstats.dbload.lib.sync_timedb_day_raw_removal import (
    DayRawRemovalCoordinator,
)
from hpcperfstats.dbload.lib.sync_timedb_startup_archive_scan import (
    StartupArchiveScanCoordinator,
)
from hpcperfstats.dbload import sync_timedb as st
from hpcperfstats.dbload import sync_timedb_archive as sta
import hpcperfstats.listend as listend
from hpcperfstats.lib.rmq_quorum_queue import LISTEND_AMQP_FRAME_MAX


def test_listend_split_clamp_and_8mib_frame_leftovers_gone():
  src = inspect.getsource(listend)
  assert "def split_listend_amqp_prefetch" not in src
  consumer_src = inspect.getsource(cfg.get_listend_amqp_consumer_count)
  assert "min(16" not in consumer_src
  assert LISTEND_AMQP_FRAME_MAX == 131072
  assert "listend_archive_worker_threads" not in cfg.INI_OPTION_DEFAULTS
  assert cfg.get_listend_archive_worker_threads() == (
      2 * cfg.get_listend_amqp_consumer_count()
  )


def test_orchestrator_watchdog_names_gone():
  assert not hasattr(qo, "_ingest_watchdog_budget_s")
  assert not hasattr(qo, "_abandon_timed_out_ingest")
  assert not hasattr(qo, "INGEST_WATCHDOG_GRACE_S")
  loop_src = inspect.getsource(qo._ingest_coordinator_loop)
  assert "_abandon_timed_out_ingest" not in loop_src
  handoff_src = inspect.getsource(qo._day_close_complete_wait_on_ingest_handoff)
  assert "requeue_closed_raw_paths_for_ingest" not in handoff_src
  assert "kick_closed_raw_paths_to_ingest" in handoff_src
  assert "kick_closed_raw_unblock" in handoff_src


def test_ingest_deadline_contextvar_noops_gone():
  for name in (
      "set_ingest_task_deadline_monotonic",
      "reset_ingest_task_deadline_monotonic",
      "get_ingest_task_deadline_monotonic",
      "extend_ingest_task_deadline_monotonic",
      "set_ingest_task_effective_timeout_s",
      "reset_ingest_task_effective_timeout_s",
      "get_ingest_task_effective_timeout_s",
      "_raise_if_ingest_deadline_exceeded",
      "_raise_if_ingest_deadline_exceeded_when_enabled",
      "_ingest_task_deadline_monotonic",
      "_ingest_task_effective_timeout_s",
  ):
    assert not hasattr(coord, name), name
  assert hasattr(coord, "IngestArchiveLookupBudgetExceededError")


def test_always_zero_wall_timeout_getters_gone():
  for name in (
      "get_sync_pool_stall_abort_after_timeouts",
      "get_sync_ingest_per_file_timeout_s",
      "get_sync_ingest_per_file_timeout_s_per_mib",
      "get_sync_ingest_chunk_size",
  ):
    assert not hasattr(cfg, name), name
  registry_opts = {opt for _sec, opt, *_rest in cfg.INI_OPTION_REGISTRY}
  assert "sync_pool_stall_abort_after_timeouts" not in registry_opts
  assert "sync_ingest_per_file_timeout_s" not in registry_opts
  assert "sync_ingest_per_file_timeout_s_per_mib" not in registry_opts
  assert hasattr(cfg, "get_sync_ingest_per_file_timeout_max_s")
  assert hasattr(cfg, "get_sync_ingest_stall_idle_s")


def test_ingest_timeout_resolve_and_stall_abort_helpers_gone():
  for name in (
      "resolve_ingest_per_file_timeout_s",
      "resolve_ingest_per_file_timeout_for_size_bytes",
      "max_ingest_per_file_timeout_for_paths",
      "stall_abort_polls_for_paths",
      "STALL_ABORT_GRACE_S",
      "stall_abort_polls_for_sealed_archives",
      "default_giant_supplement_trigger_budget_s",
  ):
    assert not hasattr(ingest_timeout, name), name


def test_compat_aliases_gone():
  timed_src = inspect.getsource(st._run_ingest_timed)
  assert "enable_sigalrm" not in timed_src
  assert not hasattr(DayCloseManifestCoordinator, "reconcile_supervisor_raw_delete_pending")
  assert not hasattr(DayCloseManifestCoordinator, "tar_paths_raw_delete_pending")
  assert not hasattr(DayCloseManifestCoordinator, "shutdown")
  assert not hasattr(DayRawRemovalCoordinator, "start_async_day_pipeline")
  assert not hasattr(StartupArchiveScanCoordinator, "wait_or_build_snapshot")
  assert not hasattr(sta, "_iter_stream_tasks_chunked")
  assert not hasattr(sta, "_process_task_chunk_interruptibly")
