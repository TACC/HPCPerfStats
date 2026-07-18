"""Unit tests for ArchiveDispatchCoordinator."""

import time
from unittest.mock import MagicMock

import hpcperfstats.dbload.lib.conf_parser as cfg
from hpcperfstats.dbload.lib.sync_timedb_archive_dispatch import (
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
  """One calendar day per slot; fill free slots up to max_inflight."""
  submitted = []

  class _Pool:
    def map_async(self, fn, items):
      submitted.append(list(items))
      return MagicMock(ready=lambda: False)

  coordinator = ArchiveDispatchCoordinator(
      archive_pool=_Pool(),
      max_inflight=2,
      archive_stats_files_fn=MagicMock(),
      log_fn=MagicMock(),
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
  assert len(submitted) == 2
  assert all(len(batch) == 1 for batch in submitted)
  assert len(coordinator.slots) == 2
  assert stats["queued"] == 1


def test_dispatch_fills_pool_sized_capacity_one_day_per_slot(monkeypatch):
  """Pool-sized max_inflight must submit all days when mapping ≤ capacity."""
  submitted = []

  class _Pool:
    def map_async(self, fn, items):
      submitted.append(list(items))
      return MagicMock(ready=lambda: False)

  coordinator = ArchiveDispatchCoordinator(
      archive_pool=_Pool(),
      max_inflight=6,
      archive_stats_files_fn=MagicMock(),
      log_fn=MagicMock(),
      pending_stats_count_fn=lambda: 0,
  )
  items = [
      ("/tmp/2026-06-%02d.tar.gz" % day, ["p%d" % day])
      for day in range(7, 13)
  ]
  stats = coordinator.dispatch_disjoint_items(
      items,
      archive_queue_max=1000,
      build_deferred_paths_fn=lambda x: x,
      track_pending_append_fn=lambda x: None,
      transition_queued_fn=lambda p: None,
      enqueue_overflow_fn=lambda item: None,
  )
  assert stats["submitted"] == 6
  assert stats["queued"] == 0
  assert len(submitted) == 6
  assert all(len(batch) == 1 for batch in submitted)
  assert len(coordinator.slots) == 6


def test_dispatch_overflow_when_mapping_exceeds_capacity(monkeypatch):
  queued = []
  submitted = []

  class _Pool:
    def map_async(self, fn, items):
      submitted.append(list(items))
      return MagicMock(ready=lambda: False)

  coordinator = ArchiveDispatchCoordinator(
      archive_pool=_Pool(),
      max_inflight=6,
      archive_stats_files_fn=MagicMock(),
      log_fn=MagicMock(),
      pending_stats_count_fn=lambda: 0,
  )
  items = [
      ("/tmp/2026-06-%02d.tar.gz" % day, ["p%d" % day])
      for day in range(7, 14)
  ]
  stats = coordinator.dispatch_disjoint_items(
      items,
      archive_queue_max=1000,
      build_deferred_paths_fn=lambda x: x,
      track_pending_append_fn=lambda x: None,
      transition_queued_fn=lambda p: None,
      enqueue_overflow_fn=lambda item: queued.append(item),
  )
  assert stats["submitted"] == 6
  assert stats["queued"] == 1
  assert len(queued) == 1
  assert queued[0][0].endswith("2026-06-13.tar.gz")


def test_prune_finished_slots_frees_capacity_before_finalize(monkeypatch):
  """Overflow drain needs has_capacity True during finalize callbacks."""
  capacity_during_finalize = []

  class _Ready:
    def ready(self):
      return True

  coordinator = ArchiveDispatchCoordinator(
      archive_pool=MagicMock(),
      max_inflight=1,
      archive_stats_files_fn=MagicMock(),
      log_fn=MagicMock(),
      pending_stats_count_fn=lambda: 0,
  )
  coordinator.slots.append(ArchiveJobSlot(
      async_result=_Ready(),
      deferred_paths=[],
      daily_tars={"/tmp/2026-01-01.tar"},
  ))

  def _finalize(_slot):
    capacity_during_finalize.append(coordinator.has_capacity())

  assert coordinator.prune_finished_slots(_finalize) == 1
  assert capacity_during_finalize == [True]
  assert coordinator.slots == []


def test_dispatch_disjoint_items_oldest_calendar_day_first(monkeypatch):
  """Ingest-path archive dispatch must claim the oldest day slot first."""
  submitted = []

  class _Pool:
    def map_async(self, fn, items):
      submitted.append(list(items))
      return MagicMock(ready=lambda: False)

  coordinator = ArchiveDispatchCoordinator(
      archive_pool=_Pool(),
      max_inflight=1,
      archive_stats_files_fn=MagicMock(),
      log_fn=MagicMock(),
      pending_stats_count_fn=lambda: 0,
  )
  # Newer day listed first — after sort, 2026-01-01 must win the only slot.
  items = [
      ("/tmp/2026-01-03.tar.gz", ["new"]),
      ("/tmp/2026-01-01.tar.gz", ["old"]),
      ("/tmp/2026-01-02.tar.gz", ["mid"]),
  ]
  coordinator.dispatch_disjoint_items(
      items,
      archive_queue_max=10,
      build_deferred_paths_fn=lambda x: x,
      track_pending_append_fn=lambda x: None,
      transition_queued_fn=lambda p: None,
      enqueue_overflow_fn=lambda item: None,
  )
  assert len(submitted) == 1
  assert len(submitted[0]) == 1
  assert submitted[0][0][0].endswith("2026-01-01.tar.gz")


def test_dispatch_log_includes_pending_stats_count(monkeypatch):
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


def test_dispatch_skips_restore_blocked_day_submits_unblocked(monkeypatch):
  """Oldest restore-blocked day must not occupy a slot; newer free day submits."""
  from hpcperfstats.dbload.lib import sync_timedb_archive_dispatch as dispatch_mod

  submitted = []
  overflow = []
  logs = []
  now = 1_700_000_000.0
  monkeypatch.setattr(dispatch_mod.time, "time", lambda: now)
  monkeypatch.setattr(
      dispatch_mod,
      "daily_tar_restore_in_progress_for_day",
      lambda day: day == "2026-01-01",
  )

  class _Pool:
    def map_async(self, fn, items):
      submitted.append(list(items))
      return MagicMock(ready=lambda: False)

  coordinator = ArchiveDispatchCoordinator(
      archive_pool=_Pool(),
      max_inflight=1,
      archive_stats_files_fn=MagicMock(),
      log_fn=lambda *args, **kwargs: logs.append(" ".join(str(a) for a in args)),
      pending_stats_count_fn=lambda: 0,
  )
  items = [
      ("/tmp/2026-01-01.tar.gz", ["blocked"]),
      ("/tmp/2026-01-02.tar.gz", ["free"]),
  ]

  def _enqueue(item, retry_at=None):
    overflow.append((item, retry_at))

  stats = coordinator.dispatch_disjoint_items(
      items,
      archive_queue_max=10,
      build_deferred_paths_fn=lambda x: x,
      track_pending_append_fn=lambda x: None,
      transition_queued_fn=lambda p: None,
      enqueue_overflow_fn=_enqueue,
  )
  assert stats["submitted"] == 1
  assert len(submitted) == 1
  assert submitted[0][0][0].endswith("2026-01-02.tar.gz")
  assert len(overflow) == 1
  assert overflow[0][0][0].endswith("2026-01-01.tar.gz")
  assert overflow[0][1] == now + dispatch_mod.ARCHIVE_RESTORE_DISPATCH_BACKOFF_S
  assert any(
      "Archive dispatch skip day=2026-01-01 reason=daily_tar_restore" in line
      for line in logs
  )
