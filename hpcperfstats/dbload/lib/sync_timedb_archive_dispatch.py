"""Archive append dispatch: multi-slot disjoint daily-tar jobs."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Set

import hpcperfstats.dbload.lib.conf_parser as cfg

# Short backoff when a heap day is blocked on daily_tar_restore (no new INI).
ARCHIVE_RESTORE_DISPATCH_BACKOFF_S = 15.0


def daily_tar_restore_in_progress_for_day(day_token: str) -> bool:
  """Indirection so unit tests can monkeypatch restore checks on this module."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      daily_tar_restore_in_progress_for_day as _impl,
  )

  return _impl(day_token)


def _day_token_for_archive_item(item) -> str:
  from hpcperfstats.dbload.lib.archive_compress import daily_tar_path_from_compressed
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      calendar_date_from_daily_tar_path,
  )

  day = calendar_date_from_daily_tar_path(daily_tar_path_from_compressed(item[0]))
  return day.isoformat() if day is not None else ""


def _enqueue_overflow_compat(enqueue_overflow_fn, item, *, retry_at=None):
  """Call overflow enqueue with optional retry_at; tolerate 1-arg test lambdas."""
  if retry_at is None:
    enqueue_overflow_fn(item)
    return
  try:
    enqueue_overflow_fn(item, retry_at=retry_at)
  except TypeError:
    enqueue_overflow_fn(item)


@dataclass
class ArchiveJobSlot:
  async_result: Any
  deferred_paths: list
  daily_tars: Set[str] = field(default_factory=set)
  submitted_at: float = field(default_factory=time.time)
  stall_logged: bool = False


class ArchiveDispatchCoordinator:
  """Manage up to N concurrent map_async jobs on disjoint daily tars."""

  def __init__(
      self,
      *,
      archive_pool,
      max_inflight: int,
      archive_stats_files_fn,
      log_fn,
      pending_stats_count_fn: Callable[[], int],
  ):
    self.archive_pool = archive_pool
    self.max_inflight = max(1, int(max_inflight))
    self.archive_stats_files_fn = archive_stats_files_fn
    self.log_fn = log_fn
    self.pending_stats_count_fn = pending_stats_count_fn
    self.slots: List[ArchiveJobSlot] = []

  def _occupied_daily_tars(self) -> Set[str]:
    occupied = set()
    for slot in self.slots:
      occupied.update(slot.daily_tars)
    return occupied

  def _daily_tar_for_item(self, item) -> str:
    from hpcperfstats.dbload.lib.archive_compress import daily_tar_path_from_compressed

    compressed_path = item[0]
    return daily_tar_path_from_compressed(compressed_path)

  def log_stalled_slots(self) -> int:
    """Emit one stall log per in-flight slot past the configured threshold."""
    stall_s = float(cfg.get_sync_archive_worker_stall_seconds())
    now = time.time()
    newly_logged = 0
    for slot in self.slots:
      elapsed = max(0.0, now - float(slot.submitted_at))
      ready_fn = getattr(slot.async_result, "ready", None)
      is_ready = False
      if callable(ready_fn):
        try:
          is_ready = ready_fn()
        except Exception:
          is_ready = False
      if is_ready or elapsed < stall_s or slot.stall_logged:
        continue
      slot.stall_logged = True
      newly_logged += 1
      self.log_fn(
          "Archive worker stall detected inflight_slots=%d elapsed_s=%.0f "
          "threshold_s=%.0f pending_stats=%d daily_tars=%s"
          % (
              len(self.slots),
              elapsed,
              stall_s,
              self.pending_stats_count_fn(),
              sorted(slot.daily_tars),
          ),
          flush=True,
      )
    return newly_logged

  def prune_finished_slots(self, finalize_slot_fn) -> int:
    """Finalize ready slots; return count finalized.

    Free slot capacity *before* calling ``finalize_slot_fn`` so overflow heap
    drain during finalize can see ``has_capacity()``.
    """
    finalized_slots = []
    remaining = []
    for slot in self.slots:
      ready_fn = getattr(slot.async_result, "ready", None)
      is_ready = False
      if callable(ready_fn):
        try:
          is_ready = ready_fn()
        except Exception:
          is_ready = False
      if is_ready:
        finalized_slots.append(slot)
      else:
        remaining.append(slot)
    self.slots = remaining
    for slot in finalized_slots:
      finalize_slot_fn(slot)
    return len(finalized_slots)

  def has_capacity(self) -> bool:
    return len(self.slots) < self.max_inflight

  def dispatch_disjoint_items(
      self,
      archive_items_all: list,
      *,
      archive_queue_max: int,
      build_deferred_paths_fn,
      track_pending_append_fn,
      transition_queued_fn,
      enqueue_overflow_fn,
  ) -> Dict[str, Any]:
    """Dispatch items whose daily tar is not already in-flight.

    Items are ordered oldest calendar day first so the ingest chunk gate day
    claims archive slots before newer days. Each in-flight slot carries
    **one** daily tar (``map_async`` of a single group) so concurrent day
    count tracks ``max_inflight`` (wired to archive pool size).
    """
    stats = {"submitted": 0, "queued": 0, "deferred_groups": 0, "pending_stats": 0}
    if not archive_items_all:
      return stats

    from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
        sort_archive_items_oldest_day_first,
    )

    archive_items_all = sort_archive_items_oldest_day_first(archive_items_all)

    if not self.has_capacity():
      for item in archive_items_all:
        enqueue_overflow_fn(item)
      stats["queued"] = len(archive_items_all)
      return stats

    occupied = self._occupied_daily_tars()
    free_slots = self.max_inflight - len(self.slots)
    max_submit = min(free_slots, max(1, int(archive_queue_max)))
    to_dispatch = []
    overflow = []
    restore_backoff_items = []
    restore_skip_days = []
    now = time.time()
    restore_retry_at = now + ARCHIVE_RESTORE_DISPATCH_BACKOFF_S
    for item in archive_items_all:
      tar = self._daily_tar_for_item(item)
      day_token = _day_token_for_archive_item(item)
      if day_token and daily_tar_restore_in_progress_for_day(day_token):
        restore_backoff_items.append(item)
        restore_skip_days.append(day_token)
        continue
      if tar in occupied:
        overflow.append(item)
        continue
      if len(to_dispatch) >= max_submit:
        overflow.append(item)
        continue
      to_dispatch.append(item)
      occupied.add(tar)

    if not to_dispatch:
      for item in restore_backoff_items:
        _enqueue_overflow_compat(
            enqueue_overflow_fn, item, retry_at=restore_retry_at,
        )
        stats["queued"] += 1
      for item in archive_items_all:
        if item in restore_backoff_items:
          continue
        _enqueue_overflow_compat(enqueue_overflow_fn, item)
        stats["queued"] += 1
      for day_token in sorted(set(restore_skip_days)):
        self.log_fn(
            "Archive dispatch skip day=%s reason=daily_tar_restore"
            % day_token,
            flush=True,
        )
      return stats

    dispatch_t0 = time.time()
    for item in to_dispatch:
      batch = [item]
      async_result = self.archive_pool.map_async(
          self.archive_stats_files_fn, batch)
      deferred_paths = build_deferred_paths_fn(batch)
      daily_tars = {self._daily_tar_for_item(item)}
      self.slots.append(ArchiveJobSlot(
          async_result=async_result,
          deferred_paths=deferred_paths,
          daily_tars=daily_tars,
      ))
      track_pending_append_fn(batch)
      for p in item[1]:
        transition_queued_fn(p)
      stats["submitted"] += 1

    stats["dispatch_s"] = max(0.0, time.time() - dispatch_t0)
    stats["deferred_groups"] = len(overflow) + len(restore_backoff_items)

    for item in restore_backoff_items:
      _enqueue_overflow_compat(
          enqueue_overflow_fn, item, retry_at=restore_retry_at,
      )
      stats["queued"] += 1
    for item in overflow:
      _enqueue_overflow_compat(enqueue_overflow_fn, item)
      stats["queued"] += 1

    for day_token in sorted(set(restore_skip_days)):
      self.log_fn(
          "Archive dispatch skip day=%s reason=daily_tar_restore"
          % day_token,
          flush=True,
      )

    stats["pending_stats"] = self.pending_stats_count_fn()
    self.log_fn(
        "Archive dispatch submitted=%d queued=%d inflight_slots=%d pending_stats=%d"
        % (
            stats["submitted"],
            stats["queued"],
            len(self.slots),
            stats["pending_stats"],
        ),
        flush=True,
    )
    return stats

  @property
  def any_inflight(self) -> bool:
    return bool(self.slots)
