"""Architecture tests for sync_timedb session executors and spawn pool factory."""

from __future__ import annotations

import multiprocessing
import os
from datetime import timezone
from unittest.mock import MagicMock

from hpcperfstats.dbload.lib.multiprocessing_pool_health import (
    create_sync_timedb_spawn_pool,
    sync_timedb_spawn_pool_recycle_kwargs,
)
from hpcperfstats.dbload.lib.sync_timedb_archive_janitor import ArchiveJanitor
from hpcperfstats.dbload.lib.sync_timedb_day_raw_removal import DayRawRemovalCoordinator
from hpcperfstats.dbload.lib.sync_timedb_session_executor import (
    SessionSingleFlightExecutor,
)
from hpcperfstats.dbload.lib.sync_timedb_startup_raw_removal import (
    StartupRawRemovalPreflight,
)
from hpcperfstats.dbload.lib.sync_timedb_startup_tail_ingest import (
    StartupTailIngestCoordinator,
)


def test_session_role_executors_created_at_init(monkeypatch, tmp_path):
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_startup_raw_removal.cfg."
      "get_sync_startup_raw_removal_preflight",
      lambda: True,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_startup_tail_ingest.cfg."
      "get_sync_startup_tail_ingest_enabled",
      lambda: True,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_day_raw_removal.cfg."
      "get_sync_day_close_raw_removal_preflight",
      lambda: True,
  )

  archive_dir = str(tmp_path / "archive")
  tgz_dir = str(tmp_path / "daily")
  os.makedirs(archive_dir, exist_ok=True)
  os.makedirs(tgz_dir, exist_ok=True)

  janitor = ArchiveJanitor(
      archive_data_dir=archive_dir,
      host_name_ext=".hpc",
      tgz_archive_dir=tgz_dir,
      local_tz=timezone.utc,
      log_fn=MagicMock(),
      get_disqualified_daily_tars=lambda: set(),
      get_ingest_backlog_high=lambda: False,
      get_pending_stats_count=lambda: 0,
      get_idle_seconds=lambda: 0.0,
  )
  assert janitor._session_executor.is_active

  tail = StartupTailIngestCoordinator(
      log_fn=lambda *_a, **_k: None,
      run_ingest_batch=lambda *_a, **_k: ([], []),
      submit_day_close=lambda *_a, **_k: False,
      signal_janitor=lambda: None,
      get_startup_snapshot=lambda: None,
      live_unprocessed_by_tar=lambda: {},
      discover_done_fn=lambda: True,
  )
  assert tail._session_executor.is_active

  raw = StartupRawRemovalPreflight(
      archive_data_dir=archive_dir,
      host_name_ext="",
      tgz_archive_dir=tgz_dir,
      log_fn=lambda *_a, **_k: None,
      get_disqualified_daily_tars=lambda: set(),
      get_quarantine_skip_paths=lambda: set(),
  )
  assert raw._session_executor.is_active

  day_raw = DayRawRemovalCoordinator(
      archive_data_dir=archive_dir,
      host_name_ext="",
      tgz_archive_dir=tgz_dir,
      log_fn=lambda *_a, **_k: None,
      get_quarantine_skip_paths=lambda: set(),
  )
  assert day_raw._session_executor.is_active

  disabled = SessionSingleFlightExecutor(
      thread_name_prefix="disabled",
      process_title="sync_timedb.py",
      thread_role="disabled",
      enabled=False,
  )
  assert not disabled.is_active


def test_session_role_executors_shutdown_without_async_start():
  executor = SessionSingleFlightExecutor(
      thread_name_prefix="test-role",
      process_title="sync_timedb.py",
      thread_role="test-role",
      enabled=True,
  )
  executor.shutdown(wait=False)
  executor.shutdown(wait=False)


def test_spawn_pool_factory_preserves_recycle_kwargs(monkeypatch):
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_ingest_pool_maxtasksperchild",
      lambda: 25,
  )
  assert sync_timedb_spawn_pool_recycle_kwargs() == {"maxtasksperchild": 25}

  created = {}

  class _FakePool:
    def __init__(self, *args, **kwargs):
      created["args"] = args
      created["kwargs"] = kwargs

  fake_ctx = type("Ctx", (), {"Pool": _FakePool})()
  monkeypatch.setattr(
      multiprocessing,
      "get_context",
      lambda _name: fake_ctx,
  )
  pool = create_sync_timedb_spawn_pool(
      processes=2,
      initializer=lambda: None,
      initargs=(1, 2),
      pool_kind_log_label="test-pool",
  )
  assert isinstance(pool, _FakePool)
  assert created["kwargs"]["maxtasksperchild"] == 25
  assert created["kwargs"]["processes"] == 2
