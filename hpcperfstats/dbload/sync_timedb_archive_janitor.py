"""Background archive janitor: day-debt queue and time-sliced micro-batches."""
from __future__ import annotations

import heapq
import os
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any, Callable, Dict, Optional, Set

import hpcperfstats.conf_parser as cfg
from django.db import close_old_connections

from hpcperfstats.dbload.archive_compress import compressed_sibling_paths
from hpcperfstats.file_locking import cleanup_stale_fnctl_lock_sidecars
from hpcperfstats.dbload.sync_timedb_archive_helpers import (
    atomic_seal_tar_to_zst,
    build_disqualification_reasons_by_tar,
    build_remaining_raw_for_daily_tar,
    build_remaining_raw_stats_by_daily_gz,
    calendar_date_from_daily_tar_path,
    classify_day_close_candidates,
    daily_tar_eligible_for_day_close_submit,
    day_close_queued_reason_for_report_reason,
    daily_gz_has_remaining_raw_stats,
    daily_tar_path_from_compressed,
    daily_tar_seal_calendar_eligible,
    dedupe_tar_keep_largest_file_per_member,
    drop_legacy_gz_if_equivalent_to_zst,
    effective_keep_uncompressed_tar,
    invalidate_unmapped_disqualify_cache,
    iter_daily_tar_paths,
    log_day_close_candidate_report,
    remove_verified_archived_raw_files,
    remove_verified_uncompressed_daily_tars,
    tar_day_dirty_by_mtime,
    tar_has_duplicate_file_members,
)
from hpcperfstats.dbload.sync_timedb_archive_maint import (
    build_archive_maintenance_snapshot,
    load_archive_maint_hints,
    day_phase_hint_entry,
    prune_day_phases_hints,
    prune_validated_days_hints,
    save_archive_maint_hints,
    snapshot_host_dirs_from_paths,
    snapshot_paths_hint_entries,
)
from hpcperfstats.process_memory import read_process_rss_bytes
from hpcperfstats.process_title import set_daemon_thread_title
from hpcperfstats.dbload.sync_timedb_startup_archive_scan import (
    copy_archive_maintenance_snapshot,
)

_LOCK_CLEANUP_TAR_SENTINEL = "__lock_cleanup__"


class DebtKind(str, Enum):
  DAY_CLOSE = "day_close"
  SEAL_PRIOR_DAY = "seal_prior_day"
  RAW_REMOVE = "raw_remove"
  TAR_DROP = "tar_drop"
  VALIDATE = "validate"
  DEDUPE = "dedupe"
  LOCK_CLEANUP = "lock_cleanup"


_DAY_PIPELINE_KINDS = frozenset({
    DebtKind.DAY_CLOSE,
    DebtKind.SEAL_PRIOR_DAY,
    DebtKind.RAW_REMOVE,
    DebtKind.TAR_DROP,
})

_LEGACY_DAY_PIPELINE_KINDS = frozenset({
    DebtKind.SEAL_PRIOR_DAY,
    DebtKind.RAW_REMOVE,
    DebtKind.TAR_DROP,
})

_SCHEDULED_DAY_CLOSE_SUBMIT_REASONS = frozenset({
    "startup",
    "every_n_chunks",
    "ingest_queue_empty",
})


def _is_heavy_maintenance_reason(reason: str) -> bool:
  if not reason:
    return False
  if reason == "startup":
    return True
  return str(reason).startswith("day_ingest_complete:")


def _scheduled_day_close_submit_allowed(reason: str) -> bool:
  if reason in _SCHEDULED_DAY_CLOSE_SUBMIT_REASONS:
    return True
  return str(reason).startswith("day_ingest_complete:")

_DEBT_PRIORITY = {
    DebtKind.DAY_CLOSE: 0,
    DebtKind.SEAL_PRIOR_DAY: 0,
    DebtKind.RAW_REMOVE: 1,
    DebtKind.VALIDATE: 2,
    DebtKind.TAR_DROP: 3,
    DebtKind.DEDUPE: 4,
    DebtKind.LOCK_CLEANUP: 5,
}


@dataclass(order=True)
class DayDebt:
  sort_index: tuple
  kind: DebtKind = field(compare=False)
  tar_path: str = field(compare=False)
  gz_path: str = field(compare=False, default="")


def _debt_sort_key(kind: DebtKind, tar_path: str) -> tuple:
  return (_DEBT_PRIORITY.get(kind, 99), tar_path)


def _tar_to_gz_path(tar_path: str) -> str:
  zst_path, gz_path = compressed_sibling_paths(tar_path)
  if os.path.isfile(zst_path):
    return zst_path
  if os.path.isfile(gz_path):
    return gz_path
  return zst_path


def _calendar_date_from_daily_tar(tar_path: str):
  return calendar_date_from_daily_tar_path(tar_path)


def _normalize_day_pipeline_debt(debt: DayDebt) -> DayDebt:
  if debt.kind not in _LEGACY_DAY_PIPELINE_KINDS:
    return debt
  tar_norm = os.path.normpath(debt.tar_path)
  return DayDebt(
      sort_index=_debt_sort_key(DebtKind.DAY_CLOSE, tar_norm),
      kind=DebtKind.DAY_CLOSE,
      tar_path=tar_norm,
      gz_path=debt.gz_path,
  )


class ArchiveJanitor:
  """Owns seal/raw/tar cleanup off the ingest supervisor thread."""

  def __init__(
      self,
      *,
      archive_data_dir: str,
      host_name_ext: str,
      tgz_archive_dir: str,
      local_tz,
      log_fn,
      get_disqualified_daily_tars: Callable[[], Set[str]],
      get_ingest_backlog_high: Callable[[], bool],
      get_pending_stats_count: Callable[[], int],
      get_idle_seconds: Callable[[], float],
      get_quarantine_skip_paths: Optional[Callable[[], Set[str]]] = None,
      ingest_ready_fn=None,
      archive_stats_files_fn=None,
      day_raw_removal_coordinator=None,
      async_day_close_coordinator=None,
      get_day_close_candidate_inputs=None,
      get_tree_rss_bytes=None,
      startup_snapshot_coordinator=None,
      process_title: str = "sync_timedb.py",
  ):
    self.archive_data_dir = archive_data_dir
    self.host_name_ext = host_name_ext
    self.tgz_archive_dir = tgz_archive_dir
    self.local_tz = local_tz
    self.log_fn = log_fn
    self.get_disqualified_daily_tars = get_disqualified_daily_tars
    self.get_ingest_backlog_high = get_ingest_backlog_high
    self.get_pending_stats_count = get_pending_stats_count
    self.get_idle_seconds = get_idle_seconds
    self.get_quarantine_skip_paths = get_quarantine_skip_paths or (lambda: set())
    self.ingest_ready_fn = ingest_ready_fn
    self.archive_stats_files_fn = archive_stats_files_fn
    self.day_raw_removal_coordinator = day_raw_removal_coordinator
    self.async_day_close_coordinator = async_day_close_coordinator
    self.get_day_close_candidate_inputs = get_day_close_candidate_inputs
    self.get_tree_rss_bytes = get_tree_rss_bytes
    self.startup_snapshot_coordinator = startup_snapshot_coordinator
    self.process_title = process_title
    self._allow_tick_chaining = True

    self._executor = ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="archive-janitor")
    self._future = None
    self._debt_heap: list = []
    self._debt_seen: Set[tuple] = set()
    self._debt_lock = threading.Lock()
    self._hints_state_lock = threading.Lock()
    self._validated_days: Dict[str, Dict[str, Any]] = {}
    self._day_phases: Dict[str, str] = {}
    self._accrual_snapshot = None
    self._accrual_snapshot_lock = threading.Lock()
    self._maintenance_pass_lock = threading.Lock()
    self._pending_maintenance_pass_reason: Optional[str] = None
    self._ticks_completed = 0
    self._budget_throttled_count = 0
    self._pending_signal = False
    self._tick_depth = 0
    self._tick_remaining_raw_cache: Dict[str, dict] = {}

    self._load_hints_state()

  def _load_hints_state(self):
    prior = load_archive_maint_hints(self.archive_data_dir) or {}
    with self._hints_state_lock:
      self._validated_days.update(
          prune_validated_days_hints(prior.get("validated_days") or {}))
      self._day_phases.update(
          prune_day_phases_hints(prior.get("day_phases") or {}))
    with self._debt_lock:
      day_close_loaded = set()
      for entry in prior.get("debt_queue") or []:
        if not isinstance(entry, dict):
          continue
        try:
          kind = DebtKind(str(entry.get("kind", "")))
        except ValueError:
          continue
        tar_path = str(entry.get("tar_path", "")).strip()
        if not tar_path:
          continue
        if kind in (DebtKind.LOCK_CLEANUP, DebtKind.DEDUPE):
          self.log_fn(
              "janitor: dropped legacy debt kind=%s tar=%s"
              % (kind.value, tar_path),
              flush=True,
          )
          continue
        if kind in _DAY_PIPELINE_KINDS:
          tar_norm = os.path.normpath(tar_path)
          if tar_norm in day_close_loaded:
            continue
          day_close_loaded.add(tar_norm)
          self._enqueue_day_close_locked(tar_norm, persist=False)
          continue
        self._enqueue_debt_locked(kind, tar_path, persist=False)
      self._trim_heap_to_max_entries_locked()

  def _persist_hints(self, *, paths_hint=None, host_dirs_hint=None):
    with self._debt_lock:
      self._trim_heap_to_max_entries_locked()
      debt_payload = self._debt_queue_payload_locked()
    with self._hints_state_lock:
      validated_days = prune_validated_days_hints(self._validated_days)
      day_phases = prune_day_phases_hints(self._day_phases)
      self._validated_days = dict(validated_days)
      self._day_phases = dict(day_phases)
    save_archive_maint_hints(
        self.archive_data_dir,
        host_dirs=host_dirs_hint or {},
        paths=paths_hint or {},
        validated_days=validated_days,
        day_phases=day_phases,
        debt_queue=debt_payload,
    )

  def _debt_queue_payload(self) -> list:
    with self._debt_lock:
      return self._debt_queue_payload_locked()

  def _debt_queue_payload_locked(self) -> list:
    items = []
    for debt in sorted(self._debt_heap):
      items.append({
          "kind": debt.kind.value,
          "tar_path": debt.tar_path,
      })
    max_entries = cfg.get_archive_janitor_debt_max_entries()
    return items[:max_entries]

  def _trim_heap_to_max_entries_locked(self):
    max_entries = cfg.get_archive_janitor_debt_max_entries()
    while len(self._debt_heap) > max_entries:
      evicted = heapq.heappop(self._debt_heap)
      self._debt_seen.discard((evicted.kind.value, evicted.tar_path))

  def _evict_lowest_priority_debt_if_full_locked(self):
    max_entries = cfg.get_archive_janitor_debt_max_entries()
    if len(self._debt_heap) < max_entries:
      return
    evicted = max(self._debt_heap, key=lambda d: d.sort_index)
    self._debt_heap.remove(evicted)
    heapq.heapify(self._debt_heap)
    self._debt_seen.discard((evicted.kind.value, evicted.tar_path))
    self.log_fn(
        "Archive janitor debt cap reached (%d); evicted lowest-priority debt "
        "kind=%s tar=%s"
        % (max_entries, evicted.kind.value, evicted.tar_path),
        flush=True,
    )

  def _enqueue_debt(self, kind: DebtKind, tar_path: str, *, persist: bool = True):
    with self._debt_lock:
      self._enqueue_debt_locked(kind, tar_path, persist=False)
      if persist:
        self._trim_heap_to_max_entries_locked()
    if persist:
      self._persist_hints()

  def _day_pipeline_queued_locked(self, tar_norm: str) -> bool:
    if (DebtKind.DAY_CLOSE.value, tar_norm) in self._debt_seen:
      return True
    for kind in _LEGACY_DAY_PIPELINE_KINDS:
      if (kind.value, tar_norm) in self._debt_seen:
        return True
    return False

  def _remove_day_pipeline_debts_for_tar_locked(self, tar_norm: str):
    if not self._debt_heap:
      return
    kept = []
    for debt in self._debt_heap:
      if debt.tar_path == tar_norm and debt.kind in _DAY_PIPELINE_KINDS:
        self._debt_seen.discard((debt.kind.value, tar_norm))
        continue
      kept.append(debt)
    if len(kept) != len(self._debt_heap):
      self._debt_heap = kept
      heapq.heapify(self._debt_heap)

  def _enqueue_day_close_locked(self, tar_path: str, *, persist: bool = True):
    tar_norm = os.path.normpath(tar_path) if tar_path else tar_path
    if not tar_norm or tar_norm == _LOCK_CLEANUP_TAR_SENTINEL:
      return
    if self._day_pipeline_queued_locked(tar_norm):
      return
    self._remove_day_pipeline_debts_for_tar_locked(tar_norm)
    key = (DebtKind.DAY_CLOSE.value, tar_norm)
    self._evict_lowest_priority_debt_if_full_locked()
    gz_path = _tar_to_gz_path(tar_norm)
    debt = DayDebt(
        sort_index=_debt_sort_key(DebtKind.DAY_CLOSE, tar_norm),
        kind=DebtKind.DAY_CLOSE,
        tar_path=tar_norm,
        gz_path=gz_path,
    )
    heapq.heappush(self._debt_heap, debt)
    self._debt_seen.add(key)
    if persist:
      self._trim_heap_to_max_entries_locked()

  def _enqueue_day_close(self, tar_path: str, *, persist: bool = True):
    with self._debt_lock:
      self._enqueue_day_close_locked(tar_path, persist=False)
      if persist:
        self._trim_heap_to_max_entries_locked()
    if persist:
      self._persist_hints()

  def _enqueue_debt_locked(self, kind: DebtKind, tar_path: str, *, persist: bool = True):
    if kind in _DAY_PIPELINE_KINDS:
      self._enqueue_day_close_locked(tar_path, persist=persist)
      return
    tar_norm = os.path.normpath(tar_path) if tar_path else tar_path
    key = (kind.value, tar_norm)
    if key in self._debt_seen:
      return
    self._evict_lowest_priority_debt_if_full_locked()
    gz_path = _tar_to_gz_path(tar_norm) if tar_norm and tar_norm != _LOCK_CLEANUP_TAR_SENTINEL else ""
    debt = DayDebt(
        sort_index=_debt_sort_key(kind, tar_norm),
        kind=kind,
        tar_path=tar_norm,
        gz_path=gz_path,
    )
    heapq.heappush(self._debt_heap, debt)
    self._debt_seen.add(key)
    if persist:
      self._trim_heap_to_max_entries_locked()

  def _requeue_unprocessed_work(self, work_items: list, start_index: int):
    if start_index >= len(work_items):
      return
    with self._debt_lock:
      for debt in work_items[start_index:]:
        key = (debt.kind.value, debt.tar_path)
        if key in self._debt_seen:
          continue
        heapq.heappush(self._debt_heap, debt)
        self._debt_seen.add(key)

  def debt_depth(self) -> int:
    with self._debt_lock:
      return len(self._debt_heap)

  def signal_work_available(self):
    """Schedule a single-flight janitor tick (non-blocking)."""
    if self._future is not None and not self._future.done():
      self._pending_signal = True
      return
    try:
      self._future = self._executor.submit(self._run_tick_body)
    except RuntimeError:
      self._future = None

  def enqueue_startup_debt(self):
    """Signal janitor work at startup; DAY_CLOSE scheduling is supervisor-driven."""
    self.signal_work_available()

  def signal_scheduled_maintenance_pass(self, *, reason: str):
    """Queue snapshot+hints+day_close scan on the janitor thread (non-blocking)."""
    with self._maintenance_pass_lock:
      pending = self._pending_maintenance_pass_reason
      if _is_heavy_maintenance_reason(reason):
        self._pending_maintenance_pass_reason = reason
      elif not _is_heavy_maintenance_reason(pending or ""):
        self._pending_maintenance_pass_reason = reason
    self.signal_work_available()

  def run_scheduled_maintenance_pass(self, *, reason: str):
    """Janitor-thread: light or heavy maintenance depending on ``reason``."""
    if _is_heavy_maintenance_reason(reason):
      self.run_heavy_maintenance_pass(reason=reason)
    else:
      self.run_light_maintenance_pass(reason=reason)

  def run_light_maintenance_pass(self, *, reason: str):
    """Refresh candidate report from accrual only; no full-tree collect."""
    if not self.tgz_archive_dir:
      return
    with self._accrual_snapshot_lock:
      accrual_snapshot = self._accrual_snapshot
    remaining = build_remaining_raw_stats_by_daily_gz(
        self.archive_data_dir,
        self.host_name_ext,
        self.tgz_archive_dir,
        maintenance_snapshot=accrual_snapshot,
    )
    self.log_fn(
        "janitor: light maintenance pass reason=%s accrual_closed_paths=%d"
        % (
            reason,
            len(accrual_snapshot.closed_paths) if accrual_snapshot else 0,
        ),
        flush=True,
    )
    self._log_day_close_candidate_report(
        reason=reason,
        remaining_raw_by_gz=remaining,
        newly_queued_tars=set(),
    )
    newly_queued = self._submit_scheduled_day_close_from_snapshot(
        reason=reason,
        remaining_raw_by_gz=remaining,
    )
    if newly_queued:
      self._log_day_close_candidate_report(
          reason=reason,
          remaining_raw_by_gz=remaining,
          newly_queued_tars=newly_queued,
      )

  def run_heavy_maintenance_pass(self, *, reason: str):
    """Janitor-thread: full snapshot refresh (startup adopt or collect)."""
    if not self.tgz_archive_dir:
      return
    snap_t0 = time.time()
    coord = self.startup_snapshot_coordinator
    snapshot = None
    adopted = False
    if reason == "startup" and coord is not None:
      existing = coord.get_snapshot()
      if existing is not None:
        snapshot = copy_archive_maintenance_snapshot(existing)
        adopted = True
        self.log_fn(
            "janitor: heavy startup adopted coordinator snapshot "
            "closed_paths=%d" % len(snapshot.closed_paths),
            flush=True,
        )
    if snapshot is None:
      if coord is not None:
        coord.begin_build()
      try:
        snapshot = build_archive_maintenance_snapshot(
            self.archive_data_dir,
            self.host_name_ext,
            self.tgz_archive_dir,
            log_fn=self.log_fn,
        )
      except Exception:
        if coord is not None:
          coord.abort_build()
        raise
      if coord is not None:
        coord.publish(snapshot, from_janitor=True)
        invalidate_unmapped_disqualify_cache()
    with self._accrual_snapshot_lock:
      self._accrual_snapshot = snapshot
    if not adopted:
      self._persist_hints(
          paths_hint=snapshot_paths_hint_entries(
              snapshot.closed_paths,
              snapshot.first_timestamp_by_path,
              snapshot.head_identity_by_path,
          ),
          host_dirs_hint=snapshot_host_dirs_from_paths(snapshot.closed_paths),
      )
    self.log_fn(
        "janitor: heavy snapshot refreshed reason=%s closed_paths=%d "
        "duration_s=%.3f adopted=%s"
        % (
            reason,
            len(snapshot.closed_paths),
            time.time() - snap_t0,
            "yes" if adopted else "no",
        ),
        flush=True,
    )
    with self._accrual_snapshot_lock:
      accrual_snapshot = self._accrual_snapshot
    remaining = build_remaining_raw_stats_by_daily_gz(
        self.archive_data_dir,
        self.host_name_ext,
        self.tgz_archive_dir,
        maintenance_snapshot=accrual_snapshot,
    )
    self._log_day_close_candidate_report(
        reason=reason,
        remaining_raw_by_gz=remaining,
        newly_queued_tars=set(),
    )
    newly_queued = self._submit_scheduled_day_close_from_snapshot(
        reason=reason,
        remaining_raw_by_gz=remaining,
    )
    if newly_queued:
      self._log_day_close_candidate_report(
          reason=reason,
          remaining_raw_by_gz=remaining,
          newly_queued_tars=newly_queued,
      )
    self._trim_accrual_snapshot_memory()

  def _trim_accrual_snapshot_memory(self):
    """Release large snapshot lists after hints are persisted on disk."""
    with self._accrual_snapshot_lock:
      snapshot = self._accrual_snapshot
      if snapshot is None:
        return
      closed_count = len(snapshot.closed_paths)
      snapshot.closed_paths = []
      snapshot.head_identity_by_path.clear()
      snapshot.head_read_stats.clear()
      snapshot.ready_paths.clear()
    if closed_count:
      self.log_fn(
          "janitor: accrual snapshot trimmed released_closed_paths=%d"
          % closed_count,
          flush=True,
      )

  def _calendar_today_local(self):
    return datetime.now(self.local_tz).date()

  def _day_needs_scheduled_close(
      self,
      tar_norm: str,
      *,
      remaining_raw_by_gz,
      disqualified: Set[str],
  ) -> bool:
    if tar_norm in disqualified:
      return False
    day_date = _calendar_date_from_daily_tar(tar_norm)
    if day_date is None:
      return False
    today_local = self._calendar_today_local()
    if day_date >= today_local and not daily_tar_seal_calendar_eligible(
        tar_norm, self.local_tz):
      return False
    if not self._day_phase_at_least(tar_norm, "tar_dropped"):
      return True
    if os.path.isfile(tar_norm) and tar_day_dirty_by_mtime(tar_norm):
      return True
    zst_path, _gz_path = compressed_sibling_paths(tar_norm)
    if daily_gz_has_remaining_raw_stats(zst_path, remaining_raw_by_gz):
      return True
    return False

  def enqueue_scheduled_day_close(self, *, reason: str):
    """Enqueue ``DAY_CLOSE`` for eligible calendar days (startup + every N chunks)."""
    if not self.tgz_archive_dir or not os.path.isdir(self.tgz_archive_dir):
      return
    disqualified = set(self.get_disqualified_daily_tars())
    with self._accrual_snapshot_lock:
      accrual_snapshot = self._accrual_snapshot
    remaining = build_remaining_raw_stats_by_daily_gz(
        self.archive_data_dir,
        self.host_name_ext,
        self.tgz_archive_dir,
        maintenance_snapshot=accrual_snapshot,
    )
    seen = set()
    candidates = []
    for tar_path in iter_daily_tar_paths(self.tgz_archive_dir):
      tar_norm = os.path.normpath(tar_path)
      if tar_norm in seen:
        continue
      seen.add(tar_norm)
      if not self._day_needs_scheduled_close(
          tar_norm,
          remaining_raw_by_gz=remaining,
          disqualified=disqualified,
      ):
        continue
      day_date = _calendar_date_from_daily_tar(tar_norm) or date.max
      candidates.append((day_date, tar_norm))
    for gz_key, raw_paths in (remaining or {}).items():
      if not raw_paths:
        continue
      tar_norm = os.path.normpath(daily_tar_path_from_compressed(gz_key))
      if tar_norm in seen:
        continue
      seen.add(tar_norm)
      if not self._day_needs_scheduled_close(
          tar_norm,
          remaining_raw_by_gz=remaining,
          disqualified=disqualified,
      ):
        continue
      day_date = _calendar_date_from_daily_tar(tar_norm) or date.max
      candidates.append((day_date, tar_norm))
    if not candidates:
      self._log_day_close_candidate_report(
          reason=reason,
          remaining_raw_by_gz=remaining,
          newly_queued_tars=set(),
      )
      return
    candidates.sort(key=lambda item: item[0])
    newly_queued = set()
    with self._debt_lock:
      for _, tar_norm in candidates:
        self._enqueue_day_close_locked(tar_norm, persist=False)
        newly_queued.add(tar_norm)
      self._trim_heap_to_max_entries_locked()
    self._persist_hints()
    self._log_day_close_candidate_report(
        reason=reason,
        remaining_raw_by_gz=remaining,
        newly_queued_tars=newly_queued,
    )
    self.log_fn(
        "janitor: day_close scheduled reason=%s days=%d debt_depth=%d"
        % (reason, len(candidates), self.debt_depth()),
        flush=True,
    )

  def _submit_eligible_async_day_close(
      self,
      tar_norm: str,
      *,
      reason: str,
      disqualified: Optional[Set[str]] = None,
  ) -> bool:
    """Submit async ``DAY_CLOSE`` when checkpoint-complete eligibility passes."""
    tar_norm = os.path.normpath(tar_norm or "")
    if not tar_norm:
      return False
    coord = self.async_day_close_coordinator
    if coord is None:
      return False
    inputs = {}
    if self.get_day_close_candidate_inputs is not None:
      try:
        inputs = self.get_day_close_candidate_inputs() or {}
      except Exception:
        inputs = {}
    if disqualified is None:
      disqualified = set(self.get_disqualified_daily_tars())
    with self._hints_state_lock:
      day_phases = dict(self._day_phases)
    with self._accrual_snapshot_lock:
      accrual_snapshot = self._accrual_snapshot
    remaining = build_remaining_raw_stats_by_daily_gz(
        self.archive_data_dir,
        self.host_name_ext,
        self.tgz_archive_dir,
        maintenance_snapshot=accrual_snapshot,
    )
    eligible, skip_reason = daily_tar_eligible_for_day_close_submit(
        tar_norm,
        unprocessed_by_tar=inputs.get("unprocessed_by_tar"),
        disqualified_daily_tars=disqualified,
        day_phases=day_phases,
        remaining_raw_by_gz=remaining,
        local_tz=self.local_tz,
    )
    if not eligible:
      if skip_reason and self.log_fn:
        self.log_fn(
            "janitor: day_close submit skip tar=%s reason=%s"
            % (tar_norm, skip_reason),
            flush=True,
        )
      return False
    submitted = coord.submit_day_close(
        tar_norm,
        reason=reason,
        disqualified_daily_tars=disqualified,
    )
    if submitted and self.log_fn:
      self.log_fn(
          "janitor: async day_close submit tar=%s reason=%s"
          % (tar_norm, reason),
          flush=True,
      )
    return submitted

  def enqueue_immediate_day_close(self, tar_path: str, *, reason: str) -> bool:
    """Submit async ``DAY_CLOSE`` when ingest checkpoint is complete for the day."""
    tar_norm = os.path.normpath(tar_path) if tar_path else ""
    if not tar_norm:
      return False
    submit_reason = "day_ingest_complete:%s" % reason
    submitted = self._submit_eligible_async_day_close(
        tar_norm,
        reason=submit_reason,
    )
    if submitted:
      self._log_day_close_candidate_report(
          reason=submit_reason,
          remaining_raw_by_gz=None,
          newly_queued_tars={tar_norm},
      )
    return submitted

  def enqueue_immediate_day_close_many(self, tar_paths, *, reason: str):
    """Bulk async ``DAY_CLOSE`` submit (oldest-first list); one wake signal."""
    if not tar_paths:
      return
    disqualified = set(self.get_disqualified_daily_tars())
    submit_reason = "day_ingest_complete:%s" % reason
    submitted = 0
    newly_queued = set()
    for tar_path in tar_paths:
      tar_norm = os.path.normpath(tar_path) if tar_path else ""
      if not tar_norm:
        continue
      if self._submit_eligible_async_day_close(
          tar_norm,
          reason=submit_reason,
          disqualified=disqualified,
      ):
        newly_queued.add(tar_norm)
        submitted += 1
    self._log_day_close_candidate_report(
        reason=submit_reason,
        remaining_raw_by_gz=None,
        newly_queued_tars=newly_queued,
    )
    if not submitted:
      return
    self.log_fn(
        "janitor: async day_close bulk submit reason=%s days=%d"
        % (submit_reason, submitted),
        flush=True,
    )
    self.signal_work_available()

  def _heap_has_day_close_work_locked(self) -> bool:
    for debt in self._debt_heap:
      if debt.kind in _DAY_PIPELINE_KINDS:
        return True
    return False

  def _run_tick_lock_cleanup(self) -> int:
    removed = cleanup_stale_fnctl_lock_sidecars(self.archive_data_dir)
    removed += cleanup_stale_fnctl_lock_sidecars(self.tgz_archive_dir)
    return removed

  def _consume_dedupe_hints(self, disqualified: Set[str]):
    """Enqueue ``DAY_CLOSE`` for days flagged by ingest member-cache populate."""
    try:
      from hpcperfstats.dbload.sync_timedb_archive_members_redis import (
          archive_members_redis_enabled,
          list_dedupe_hint_day_tokens,
      )
    except Exception:
      return
    if not archive_members_redis_enabled() or not self.tgz_archive_dir:
      return
    for day_token in list_dedupe_hint_day_tokens():
      tar_path = os.path.join(self.tgz_archive_dir, "%s.tar" % day_token)
      tar_norm = os.path.normpath(tar_path)
      if tar_norm in disqualified:
        continue
      self._submit_eligible_async_day_close(
          tar_norm,
          reason="dedupe_hint",
          disqualified=disqualified,
      )

  def _effective_tick_limits(self):
    budget = float(cfg.get_archive_janitor_budget_seconds())
    max_days = int(cfg.get_archive_janitor_days_per_tick())
    if self.get_ingest_backlog_high() or cfg.get_pipeline_overlap_mode() == "ingest_priority":
      backoff = float(cfg.get_sync_dispatch_archive_backoff_ratio())
      budget *= backoff
      max_days = max(1, int(max_days * backoff))
    if self.debt_depth() >= cfg.get_archive_janitor_debt_high_watermark():
      burst = float(cfg.get_archive_janitor_debt_burst_factor())
      budget *= burst
      max_days = max(max_days, int(cfg.get_archive_janitor_days_per_tick() * burst))
    idle_s = self.get_idle_seconds()
    if self.get_pending_stats_count() == 0 and idle_s >= cfg.get_archive_maintenance_idle_seconds():
      budget *= 2.0
    return budget, max_days

  def _rss_over_limit(self) -> bool:
    tree_limit_mb = cfg.get_sync_process_tree_rss_limit_mb()
    if tree_limit_mb > 0 and self.get_tree_rss_bytes is not None:
      rss_bytes = int(self.get_tree_rss_bytes())
      if rss_bytes > 0:
        return rss_bytes > int(tree_limit_mb) * 1024 * 1024
    limit_mb = cfg.get_sync_supervisor_rss_limit_mb()
    if limit_mb <= 0:
      return False
    rss_bytes = read_process_rss_bytes()
    if rss_bytes <= 0:
      return False
    return rss_bytes > int(limit_mb) * 1024 * 1024

  def _pop_eligible_debt_locked(self, disqualified: Set[str], max_days: int) -> list:
    selected = []
    selected_day_tars = set()
    deferred = []
    while self._debt_heap:
      debt = heapq.heappop(self._debt_heap)
      key = (debt.kind.value, debt.tar_path)
      self._debt_seen.discard(key)
      if debt.tar_path in disqualified:
        deferred.append(debt)
        continue
      if debt.kind in _DAY_PIPELINE_KINDS:
        if len(selected_day_tars) >= max_days:
          deferred.append(debt)
          continue
        norm_debt = _normalize_day_pipeline_debt(debt)
        if norm_debt.tar_path in selected_day_tars:
          continue
        selected.append(norm_debt)
        selected_day_tars.add(norm_debt.tar_path)
        continue
      selected.append(debt)
    for debt in deferred:
      key = (debt.kind.value, debt.tar_path)
      if key not in self._debt_seen:
        heapq.heappush(self._debt_heap, debt)
        self._debt_seen.add(key)
    return selected

  def _fresh_remaining_raw_by_gz_for_tar(self, tar_path: str) -> dict:
    tar_norm = os.path.normpath(tar_path)
    cached = self._tick_remaining_raw_cache.get(tar_norm)
    if cached is not None:
      return cached
    fresh = build_remaining_raw_for_daily_tar(
        self.archive_data_dir,
        self.host_name_ext,
        self.tgz_archive_dir,
        tar_norm,
    )
    self._tick_remaining_raw_cache[tar_norm] = fresh
    return fresh

  def _run_tick_body(self):
    set_daemon_thread_title(
        "", script_name=self.process_title, role="archive-janitor")
    self._tick_depth += 1
    work_items = []
    processed_index = 0
    tick_made_progress = False
    try:
      close_old_connections()
      if self._rss_over_limit():
        self._budget_throttled_count += 1
        self.log_fn(
            "Archive janitor tick deferred reason=rss_over_limit",
            flush=True,
        )
        self._pending_signal = True
        return

      budget_s, max_days = self._effective_tick_limits()
      tick_t0 = time.time()
      maintenance_pass_s = 0.0
      pass_reason = None
      with self._maintenance_pass_lock:
        pass_reason = self._pending_maintenance_pass_reason
        self._pending_maintenance_pass_reason = None
      if pass_reason:
        maint_t0 = time.time()
        self.run_scheduled_maintenance_pass(reason=pass_reason)
        maintenance_pass_s = time.time() - maint_t0
      debt_tick_t0 = time.time()
      disqualified = set(self.get_disqualified_daily_tars())
      self._consume_dedupe_hints(disqualified)
      self._tick_remaining_raw_cache = {}
      with self._debt_lock:
        has_day_work = self._heap_has_day_close_work_locked()
      if has_day_work:
        removed = self._run_tick_lock_cleanup()
        if removed:
          self.log_fn(
              "janitor: lock_cleanup removed=%d" % removed,
              flush=True,
          )
      with self._debt_lock:
        work_items = self._pop_eligible_debt_locked(disqualified, max_days)
      debt_popped = len(work_items)
      if not work_items:
        self._ticks_completed += 1
        self.log_fn(
            "Archive janitor tick done days=0 debt_remaining=%d duration_s=%.3f "
            "maintenance_pass_s=%.3f debt_popped=0 async_submitted=0 days_completed=0"
            % (
                self.debt_depth(),
                time.time() - tick_t0,
                maintenance_pass_s,
            ),
            flush=True,
        )
        return

      validation_cache = {"hits": 0, "misses": 0}
      with self._accrual_snapshot_lock:
        snapshot = self._accrual_snapshot
      days_processed = 0
      async_submitted = 0
      tick_mutated = False

      for i, debt in enumerate(work_items):
        if time.time() - debt_tick_t0 >= budget_s:
          self._budget_throttled_count += 1
          self._requeue_unprocessed_work(work_items, i)
          tick_mutated = True
          if processed_index > 0:
            tick_made_progress = True
          break

        try:
          disqualified = set(self.get_disqualified_daily_tars())
          if debt.tar_path in disqualified:
            self._enqueue_debt(debt.kind, debt.tar_path, persist=False)
            continue
          tick_stats = {"async_submitted": 0}
          success = self._process_debt_item(
              debt,
              snapshot=snapshot,
              validation_cache=validation_cache,
              disqualified=disqualified,
              tick_stats=tick_stats,
          )
          async_submitted += int(tick_stats.get("async_submitted") or 0)
          processed_index = i + 1
          tick_mutated = True
          tick_made_progress = True
          if success and debt.kind in (
              DebtKind.DAY_CLOSE,
              DebtKind.SEAL_PRIOR_DAY,
              DebtKind.RAW_REMOVE,
              DebtKind.VALIDATE,
              DebtKind.TAR_DROP,
          ):
            days_processed += 1
          disqualified = set(self.get_disqualified_daily_tars())
        except Exception as exc:
          self.log_fn(
              "Archive janitor debt error kind=%s tar=%s: %s\n%s"
              % (
                  debt.kind.value,
                  debt.tar_path,
                  exc,
                  traceback.format_exc(),
              ),
              flush=True,
          )
          self._requeue_unprocessed_work(work_items, i)
          tick_mutated = True
          if processed_index > 0:
            tick_made_progress = True
          break

      if tick_mutated:
        self._persist_hints()

      self._ticks_completed += 1
      self.log_fn(
          "Archive janitor tick done days=%d debt_remaining=%d duration_s=%.3f "
          "maintenance_pass_s=%.3f debt_popped=%d async_submitted=%d "
          "days_completed=%d"
          % (
              days_processed,
              self.debt_depth(),
              time.time() - tick_t0,
              maintenance_pass_s,
              debt_popped,
              async_submitted,
              days_processed,
          ),
          flush=True,
      )
    except Exception as exc:
      self.log_fn(
          "Archive janitor tick error: %s\n%s"
          % (exc, traceback.format_exc()),
          flush=True,
      )
      if work_items and processed_index < len(work_items):
        self._requeue_unprocessed_work(work_items, processed_index)
        self._persist_hints()
    finally:
      close_old_connections()
      if (
          self._allow_tick_chaining
          and self.debt_depth() > 0
          and tick_made_progress
      ):
        self._pending_signal = True
      if self._pending_signal and self._tick_depth == 1:
        self._pending_signal = False
        self.signal_work_available()
      self._tick_depth = max(0, self._tick_depth - 1)

  def _debt_heap_tar_paths(self) -> Set[str]:
    with self._debt_lock:
      return {
          os.path.normpath(debt.tar_path)
          for debt in self._debt_heap
          if debt.kind == DebtKind.DAY_CLOSE
      }

  def _log_day_close_candidate_report(
      self,
      *,
      reason: str,
      remaining_raw_by_gz=None,
      newly_queued_tars=None,
  ) -> None:
    if self.get_day_close_candidate_inputs is None:
      return
    try:
      inputs = self.get_day_close_candidate_inputs()
    except Exception:
      return
    if not isinstance(inputs, dict):
      return
    disq_reasons = build_disqualification_reasons_by_tar(
        tgz_archive_dir=self.tgz_archive_dir,
        inflight_paths=inputs.get("inflight_paths"),
        pending_append_by_daily_tar=inputs.get("pending_append_by_daily_tar"),
        in_flight_archive_tars=inputs.get("in_flight_archive_tars"),
        pending_archive_task_tars=inputs.get("pending_archive_task_tars"),
        unmapped_closed_raw_tars=inputs.get("unmapped_closed_raw_tars"),
        unprocessed_by_tar=inputs.get("unprocessed_by_tar"),
        local_tz=self.local_tz,
    )
    with self._hints_state_lock:
      day_phases = dict(self._day_phases)
    async_active = set()
    coord = self.async_day_close_coordinator
    if coord is not None:
      async_active = coord.active_or_submitted_tar_paths()
    entries = classify_day_close_candidates(
        tgz_archive_dir=self.tgz_archive_dir,
        remaining_raw_by_gz=remaining_raw_by_gz,
        unprocessed_by_tar=inputs.get("unprocessed_by_tar"),
        disqualification_reasons=disq_reasons,
        day_phases=day_phases,
        local_tz=self.local_tz,
        async_in_progress_tars=async_active,
        debt_heap_tars=self._debt_heap_tar_paths(),
        newly_queued_tars=newly_queued_tars or set(),
        queued_reason=day_close_queued_reason_for_report_reason(reason),
    )
    async_progress_fn = None
    if coord is not None:
      async_progress_fn = coord.entry_progress_snapshot
    log_day_close_candidate_report(
        entries,
        reason=reason,
        log_fn=self.log_fn,
        async_progress_fn=async_progress_fn,
    )

  def _submit_scheduled_day_close_from_snapshot(
      self,
      *,
      reason: str,
      remaining_raw_by_gz,
  ) -> Set[str]:
    """Submit async DAY_CLOSE for ``eligible_deferred`` days on scheduled passes."""
    if not _scheduled_day_close_submit_allowed(reason):
      return set()
    coord = self.async_day_close_coordinator
    if coord is None:
      return set()
    try:
      inputs = self.get_day_close_candidate_inputs() or {}
    except Exception:
      inputs = {}
    if not isinstance(inputs, dict):
      inputs = {}
    disq_reasons = build_disqualification_reasons_by_tar(
        tgz_archive_dir=self.tgz_archive_dir,
        inflight_paths=inputs.get("inflight_paths"),
        pending_append_by_daily_tar=inputs.get("pending_append_by_daily_tar"),
        in_flight_archive_tars=inputs.get("in_flight_archive_tars"),
        pending_archive_task_tars=inputs.get("pending_archive_task_tars"),
        unmapped_closed_raw_tars=inputs.get("unmapped_closed_raw_tars"),
        unprocessed_by_tar=inputs.get("unprocessed_by_tar"),
        local_tz=self.local_tz,
    )
    with self._hints_state_lock:
      day_phases = dict(self._day_phases)
    entries = classify_day_close_candidates(
        tgz_archive_dir=self.tgz_archive_dir,
        remaining_raw_by_gz=remaining_raw_by_gz,
        unprocessed_by_tar=inputs.get("unprocessed_by_tar"),
        disqualification_reasons=disq_reasons,
        day_phases=day_phases,
        local_tz=self.local_tz,
        async_in_progress_tars=coord.active_or_submitted_tar_paths(),
        debt_heap_tars=self._debt_heap_tar_paths(),
        newly_queued_tars=set(),
    )
    deferred = [
        entry for entry in entries
        if entry.get("status") == "eligible_deferred"
    ]
    deferred.sort(
        key=lambda entry: calendar_date_from_daily_tar_path(entry["tar_path"])
        or date.max,
    )
    newly_queued: Set[str] = set()
    disqualified = set(self.get_disqualified_daily_tars())
    if reason == "startup":
      max_inflight = cfg.get_sync_startup_day_close_max_inflight()
    else:
      max_inflight = cfg.get_sync_day_close_max_inflight()
    submit_reason = "scheduled_%s" % reason
    for entry in deferred:
      if len(coord.active_or_submitted_tar_paths()) >= max_inflight:
        break
      tar_norm = os.path.normpath(entry["tar_path"])
      if self._submit_eligible_async_day_close(
          tar_norm,
          reason=submit_reason,
          disqualified=disqualified,
      ):
        newly_queued.add(tar_norm)
    if newly_queued:
      self.log_fn(
          "janitor: scheduled day_close submit reason=%s days=%d"
          % (reason, len(newly_queued)),
          flush=True,
      )
    return newly_queued

  def _process_debt_item(
      self,
      debt: DayDebt,
      *,
      snapshot,
      validation_cache,
      disqualified: Set[str],
      tick_stats=None,
  ) -> bool:
    if debt.kind == DebtKind.DAY_CLOSE:
      coord = self.async_day_close_coordinator
      if coord is not None:
        tar_norm = os.path.normpath(debt.tar_path)
        if coord.is_complete(tar_norm):
          with self._hints_state_lock:
            self._day_phases[tar_norm] = day_phase_hint_entry(tar_norm, "tar_dropped")
          return True
        was_active = tar_norm in coord.active_or_submitted_tar_paths()
        self._submit_eligible_async_day_close(
            tar_norm,
            reason="janitor_debt",
            disqualified=disqualified,
        )
        if (
            tick_stats is not None
            and not was_active
            and tar_norm in coord.active_or_submitted_tar_paths()
        ):
          tick_stats["async_submitted"] = int(
              tick_stats.get("async_submitted") or 0
          ) + 1
        if coord.is_complete(tar_norm):
          with self._hints_state_lock:
            self._day_phases[tar_norm] = day_phase_hint_entry(tar_norm, "tar_dropped")
          return True
        self._enqueue_day_close(tar_norm, persist=False)
        return False
      return self._close_one_day(
          debt.tar_path,
          snapshot=snapshot,
          validation_cache=validation_cache,
          disqualified=disqualified,
      )
    if debt.kind == DebtKind.SEAL_PRIOR_DAY:
      return self._seal_one_day(debt.tar_path)
    if debt.kind in (DebtKind.RAW_REMOVE, DebtKind.VALIDATE):
      return self._raw_remove_one_day(
          debt.tar_path,
          snapshot,
          validation_cache,
          disqualified,
      )
    if debt.kind == DebtKind.TAR_DROP:
      return self._tar_drop_one_day(
          debt.tar_path,
          validation_cache,
          disqualified,
      )
    if debt.kind in (DebtKind.DEDUPE, DebtKind.LOCK_CLEANUP):
      self.log_fn(
          "janitor: ignored legacy debt kind=%s tar=%s"
          % (debt.kind.value, debt.tar_path),
          flush=True,
      )
      return True
    return False

  def _day_phase_name(self, tar_path: str):
    phase = self._day_phases.get(os.path.normpath(tar_path))
    if isinstance(phase, dict):
      return phase.get("phase")
    return phase

  def _day_phase_at_least(self, tar_path: str, target: str) -> bool:
    order = {"sealed": 1, "raw_removed": 2, "tar_dropped": 3}
    phase_name = self._day_phase_name(tar_path)
    if phase_name not in order:
      return False
    return order[phase_name] >= order[target]

  def _day_close_raw_removal_enabled(self) -> bool:
    coord = self.day_raw_removal_coordinator
    return coord is not None and bool(getattr(coord, "enabled", False))

  def _close_one_day(
      self,
      tar_path: str,
      *,
      snapshot,
      validation_cache,
      disqualified: Set[str],
  ) -> bool:
    tar_norm = os.path.normpath(tar_path)
    if not self._day_phase_at_least(tar_norm, "sealed"):
      needs_dedupe = False
      if os.path.isfile(tar_norm):
        needs_dedupe = tar_has_duplicate_file_members(tar_norm)
      else:
        try:
          from hpcperfstats.dbload.sync_timedb_archive_members_redis import (
              dedupe_hint_is_set,
          )
          from hpcperfstats.dbload.sync_timedb_archive_helpers import (
              calendar_date_from_daily_tar_path,
          )

          day = calendar_date_from_daily_tar_path(tar_norm)
          if day is not None and dedupe_hint_is_set(day.isoformat()):
            needs_dedupe = True
        except Exception:
          needs_dedupe = False
      if needs_dedupe:
        if os.path.isfile(tar_norm):
          self.log_fn(
              "janitor: day_close dedupe tar=%s" % tar_norm,
              flush=True,
          )
          dedupe_tar_keep_largest_file_per_member(tar_norm, log_fn=self.log_fn)
        else:
          zst_path, gz_path = compressed_sibling_paths(tar_norm)
          sealed_path = zst_path if os.path.isfile(zst_path) else gz_path
          if os.path.isfile(sealed_path):
            from hpcperfstats.dbload.sync_timedb_archive_helpers import (
                dedupe_sealed_daily_archive,
            )

            self.log_fn(
                "janitor: day_close sealed dedupe %s" % sealed_path,
                flush=True,
            )
            dedupe_sealed_daily_archive(sealed_path, log_fn=self.log_fn)
      if not self._seal_one_day(tar_norm, ignore_remaining_raw=True):
        return False
    if self._day_close_raw_removal_enabled():
      coord = self.day_raw_removal_coordinator
      self.log_fn(
          "janitor: day_close async_verify start tar=%s" % tar_norm,
          flush=True,
      )
      coord.start_async_verify(tar_norm)
      if not coord.verification_complete(tar_norm):
        self._enqueue_day_close(tar_norm, persist=False)
        self._persist_hints()
        return False
      if coord.delete_phase_done(tar_norm):
        with self._hints_state_lock:
          self._day_phases[tar_norm] = day_phase_hint_entry(tar_norm, "tar_dropped")
        return True
      self._enqueue_day_close(tar_norm, persist=False)
      self._persist_hints()
      return False
    if not self._day_phase_at_least(tar_norm, "raw_removed"):
      if not self._raw_remove_one_day(
          tar_norm,
          snapshot,
          validation_cache,
          disqualified,
      ):
        self._enqueue_day_close(tar_norm, persist=False)
        return False
    if not self._day_phase_at_least(tar_norm, "tar_dropped"):
      if not self._tar_drop_one_day(
          tar_norm,
          validation_cache,
          disqualified,
      ):
        self._enqueue_day_close(tar_norm, persist=False)
        return False
    return True

  def _seal_one_day(self, tar_path: str, *, ignore_remaining_raw: bool = False) -> bool:
    if not daily_tar_seal_calendar_eligible(tar_path, self.local_tz):
      self.log_fn(
          "Janitor seal deferred (calendar-today grace): %s" % tar_path,
          flush=True,
      )
      self._enqueue_day_close(tar_path, persist=False)
      self._persist_hints()
      return False
    remaining_raw_by_gz = self._fresh_remaining_raw_by_gz_for_tar(tar_path)
    zst_path, gz_path = compressed_sibling_paths(tar_path)
    if (
        not ignore_remaining_raw
        and daily_gz_has_remaining_raw_stats(zst_path, remaining_raw_by_gz)
    ):
      self.log_fn(
          "Janitor seal deferred (raw stats still present for day): %s"
          % tar_path,
          flush=True,
      )
      self._enqueue_day_close(tar_path, persist=False)
      self._persist_hints()
      return False
    keep_tar = effective_keep_uncompressed_tar(
        tar_path,
        local_tz=self.local_tz,
    )
    self.log_fn(
        "janitor: day_close seal start tar=%s" % tar_path,
        flush=True,
    )
    atomic_seal_tar_to_zst(
        tar_path,
        zst_path,
        cfg.get_archive_zstd_threads(),
        cfg.get_archive_zstd_level(),
        keep_tar,
        log_fn=self.log_fn,
        remaining_raw_by_gz=remaining_raw_by_gz,
        force_remove_uncompressed_tar=False,
    )
    drop_legacy_gz_if_equivalent_to_zst(gz_path, zst_path, log_fn=self.log_fn)
    if os.path.isfile(zst_path) or os.path.isfile(gz_path):
      self.log_fn(
          "janitor: day_close seal done tar=%s" % tar_path,
          flush=True,
      )
      with self._hints_state_lock:
        self._day_phases[tar_path] = day_phase_hint_entry(tar_path, "sealed")
      return True
    return False

  def _raw_remove_one_day(
      self,
      tar_path: str,
      snapshot,
      validation_cache,
      disqualified: Set[str],
  ) -> bool:
    max_paths = cfg.get_archive_janitor_raw_paths_per_tick()
    before_remaining = self._fresh_remaining_raw_by_gz_for_tar(tar_path)
    remove_verified_archived_raw_files(
        self.archive_data_dir,
        self.host_name_ext,
        self.tgz_archive_dir,
        log_fn=self.log_fn,
        archive_stats_files_fn=self.archive_stats_files_fn,
        ingest_ready_fn=self.ingest_ready_fn,
        maintenance_snapshot=None,
        validation_cache=validation_cache,
        validated_days_out=self._validated_days,
        skip_daily_tar_paths=disqualified,
        only_daily_tar_paths={tar_path},
        allow_auto_seal=False,
        max_deletes_per_pass=max_paths,
    )
    self._tick_remaining_raw_cache.pop(os.path.normpath(tar_path), None)
    after_remaining = self._fresh_remaining_raw_by_gz_for_tar(tar_path)
    zst_path, _gz_path = compressed_sibling_paths(tar_path)
    if daily_gz_has_remaining_raw_stats(zst_path, after_remaining):
      self._enqueue_day_close(tar_path, persist=False)
      self._persist_hints()
      if before_remaining != after_remaining:
        with self._hints_state_lock:
          self._day_phases.pop(tar_path, None)
      return False
    with self._hints_state_lock:
      self._day_phases[tar_path] = day_phase_hint_entry(tar_path, "raw_removed")
    return True

  def _tar_drop_one_day(
      self,
      tar_path: str,
      validation_cache,
      disqualified: Set[str],
  ) -> bool:
    remaining_raw_by_gz = self._fresh_remaining_raw_by_gz_for_tar(tar_path)
    zst_path, _gz_path = compressed_sibling_paths(tar_path)
    if daily_gz_has_remaining_raw_stats(zst_path, remaining_raw_by_gz):
      self.log_fn(
          "Janitor tar drop deferred (raw stats still present for day): %s"
          % tar_path,
          flush=True,
      )
      self._enqueue_day_close(tar_path, persist=False)
      self._persist_hints()
      return False
    tar_existed = os.path.isfile(tar_path)
    remove_verified_uncompressed_daily_tars(
        self.tgz_archive_dir,
        log_fn=self.log_fn,
        remaining_raw_by_gz=remaining_raw_by_gz,
        force_remove_uncompressed_tar=False,
        validation_cache=validation_cache,
        validated_days_out=self._validated_days,
        skip_daily_tar_paths=disqualified,
        only_daily_tar_paths={tar_path},
    )
    if tar_existed and os.path.isfile(tar_path):
      return False
    if not tar_existed or not os.path.isfile(tar_path):
      with self._hints_state_lock:
        self._day_phases[tar_path] = day_phase_hint_entry(tar_path, "tar_dropped")
      return True
    return False

  def shutdown(self, wait: bool = True):
    self._executor.shutdown(wait=wait)

  def stats(self) -> Dict[str, Any]:
    return {
        "janitor_debt_depth": self.debt_depth(),
        "janitor_ticks_completed": self._ticks_completed,
        "janitor_budget_throttled": self._budget_throttled_count,
    }
