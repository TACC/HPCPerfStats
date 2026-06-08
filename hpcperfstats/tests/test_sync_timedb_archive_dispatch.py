"""Unit tests for ArchiveDispatchCoordinator."""

import time
from unittest.mock import MagicMock

import hpcperfstats.conf_parser as cfg
from hpcperfstats.dbload.sync_timedb_archive_dispatch import (
    ArchiveDispatchCoordinator,
    ArchiveJobSlot,
)


def test_log_stalled_slots_emits_once_per_slot(monkeypatch):
  monkeypatch.setattr(cfg, "get_sync_archive_worker_stall_seconds", lambda: 10.0)
  logs = []
  coordinator = ArchiveDispatchCoordinator(
      archive_pool=MagicMock(),
      max_inflight=2,
      archive_stats_files_fn=MagicMock(),
      log_fn=lambda *args, **kwargs: logs.append(" ".join(str(a) for a in args)),
      get_ingest_backlog_high=lambda: False,
      ingest_queue_low=1,
      pending_stats_count_fn=lambda: 0,
  )

  class _NeverReady:
    def ready(self):
      return False

  coordinator.slots.append(ArchiveJobSlot(
      async_result=_NeverReady(),
      deferred_paths=[],
      daily_tars={"/tmp/2026-01-01.tar"},
      submitted_at=time.time() - 30.0,
  ))
  assert coordinator.log_stalled_slots() == 1
  assert coordinator.log_stalled_slots() == 0
  assert any("Archive worker stall detected" in line for line in logs)


def test_dispatch_at_capacity_reports_queued_count_once(monkeypatch):
  monkeypatch.setattr(cfg, "get_sync_dispatch_archive_backoff_ratio", lambda: 1.0)
  queued = []

  class _Pool:
    def map_async(self, fn, items):
      del fn, items
      return MagicMock(ready=lambda: False)

  coordinator = ArchiveDispatchCoordinator(
      archive_pool=_Pool(),
      max_inflight=1,
      archive_stats_files_fn=MagicMock(),
      log_fn=MagicMock(),
      get_ingest_backlog_high=lambda: False,
      ingest_queue_low=1,
      pending_stats_count_fn=lambda: 0,
  )
  coordinator.slots.append(ArchiveJobSlot(
      async_result=MagicMock(ready=lambda: False),
      deferred_paths=[],
      daily_tars={"/tmp/2026-01-01.tar"},
  ))
  items = [
      ("/tmp/2026-01-02.tar.gz", ["a"]),
      ("/tmp/2026-01-03.tar.gz", ["b"]),
      ("/tmp/2026-01-04.tar.gz", ["c"]),
  ]
  stats = coordinator.dispatch_disjoint_items(
      items,
      archive_queue_max=10,
      build_deferred_paths_fn=lambda x: x,
      track_pending_append_fn=lambda x: None,
      transition_queued_fn=lambda p: None,
      enqueue_overflow_fn=lambda item: queued.append(item),
  )
  assert stats["queued"] == len(items)
  assert len(queued) == len(items)


def test_dispatch_disjoint_items_respects_max_inflight_daily_tars(monkeypatch):
  monkeypatch.setattr(cfg, "get_sync_dispatch_archive_backoff_ratio", lambda: 1.0)
  submitted = []

  class _Pool:
    def map_async(self, fn, items):
      submitted.append(items)
      return MagicMock(ready=lambda: False)

  coordinator = ArchiveDispatchCoordinator(
      archive_pool=_Pool(),
      max_inflight=2,
      archive_stats_files_fn=MagicMock(),
      log_fn=MagicMock(),
      get_ingest_backlog_high=lambda: False,
      ingest_queue_low=1,
      pending_stats_count_fn=lambda: 0,
  )
  items = [
      ("/tmp/2026-01-01.tar.gz", ["a"]),
      ("/tmp/2026-01-02.tar.gz", ["b"]),
      ("/tmp/2026-01-01.tar.gz", ["c"]),
  ]
  stats = coordinator.dispatch_disjoint_items(
      items,
      archive_queue_max=10,
      build_deferred_paths_fn=lambda x: x,
      track_pending_append_fn=lambda x: None,
      transition_queued_fn=lambda p: None,
      enqueue_overflow_fn=lambda item: None,
  )
  assert stats["submitted"] == 2
  assert len(submitted) == 1
  assert len(submitted[0]) == 2
  daily_tars = {coordinator._daily_tar_for_item(item) for item in submitted[0]}
  assert len(daily_tars) == 2


def test_dispatch_log_includes_pending_stats_count(monkeypatch):
  monkeypatch.setattr(cfg, "get_sync_dispatch_archive_backoff_ratio", lambda: 1.0)
  logs = []

  class _Pool:
    def map_async(self, fn, items):
      del fn, items
      return MagicMock(ready=lambda: False)

  coordinator = ArchiveDispatchCoordinator(
      archive_pool=_Pool(),
      max_inflight=2,
      archive_stats_files_fn=MagicMock(),
      log_fn=lambda *args, **kwargs: logs.append(" ".join(str(a) for a in args)),
      get_ingest_backlog_high=lambda: False,
      ingest_queue_low=1,
      pending_stats_count_fn=lambda: 42,
  )
  stats = coordinator.dispatch_disjoint_items(
      [("/tmp/2026-01-01.tar.gz", ["a"])],
      archive_queue_max=10,
      build_deferred_paths_fn=lambda x: x,
      track_pending_append_fn=lambda x: None,
      transition_queued_fn=lambda p: None,
      enqueue_overflow_fn=lambda item: None,
  )
  assert stats["pending_stats"] == 42
  assert any("pending_stats=42" in line for line in logs)
