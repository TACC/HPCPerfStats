"""Background archive janitor: day-debt queue and parallel DAY_CLOSE workers."""
from __future__ import annotations

import heapq
import os
import threading
import time
import traceback
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any, Callable, Dict, Optional, Set, Tuple

import hpcperfstats.dbload.lib.conf_parser as cfg
from django.db import close_old_connections

from hpcperfstats.dbload.lib.archive_compress import compressed_sibling_paths
from hpcperfstats.dbload.lib.file_locking import (
    cleanup_orphan_fnctl_lock_sidecars,
    cleanup_stale_fnctl_lock_sidecars,
)
from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
    atomic_seal_tar_to_zst,
    build_disqualification_reasons_by_tar,
    build_remaining_raw_for_daily_tar,
    build_remaining_raw_stats_by_daily_gz,
    calendar_date_from_daily_tar_path,
    classify_day_close_candidates,
    daily_tar_eligible_for_day_close_submit,
    day_close_queued_reason_for_report_reason,
    remaining_raw_by_gz_has_paths_on_disk,
    daily_tar_path_from_compressed,
    daily_tar_seal_calendar_eligible,
    dedupe_tar_keep_largest_file_per_member,
    drop_legacy_gz_if_equivalent_to_zst,
    effective_keep_uncompressed_tar,
    invalidate_unmapped_disqualify_cache,
    iter_daily_tar_paths,
    log_day_close_candidate_report,
    remove_verified_uncompressed_daily_tars,
    tar_day_dirty_by_mtime,
    tar_has_duplicate_file_members,
)
from hpcperfstats.dbload.lib.sync_timedb_archive_maint import (
    build_archive_maintenance_snapshot,
    load_archive_maint_hints,
    day_phase_hint_entry,
    prune_day_phases_hints,
    prune_validated_days_hints,
    save_archive_maint_hints,
    snapshot_host_dirs_from_paths,
    snapshot_paths_hint_entries,
)
from hpcperfstats.dbload.lib.process_memory import read_process_rss_bytes
from hpcperfstats.dbload.lib.process_title import set_daemon_thread_title
from hpcperfstats.dbload.lib.sync_timedb_session_executor import (
    SessionSingleFlightExecutor,
)
from hpcperfstats.dbload.lib.sync_timedb_day_close_cooperation import (
    DayCloseYieldError,
    JanitorDeferTracker,
    clear_day_close_yield,
    daily_tar_janitor_mutation_should_defer,
    log_janitor_day_close_defer,
    log_janitor_day_close_yield,
    signal_day_close_yield,
)
from hpcperfstats.dbload.lib.sync_timedb_startup_archive_scan import (
    copy_archive_maintenance_snapshot,
)

_LOCK_CLEANUP_TAR_SENTINEL = "__lock_cleanup__"
_TICK_WAIT_HEARTBEAT_SECONDS = 300.0


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
    DebtKind.VALIDATE,
})

_LEGACY_DAY_PIPELINE_KINDS = frozenset({
    DebtKind.SEAL_PRIOR_DAY,
    DebtKind.RAW_REMOVE,
    DebtKind.TAR_DROP,
    DebtKind.VALIDATE,
})


def _is_heavy_maintenance_reason(reason: str) -> bool:
  if not reason:
    return False
  if reason == "startup":
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
      get_pending_stats_count: Callable[[], int],
      get_idle_seconds: Callable[[], float],
      get_delete_disqualified_daily_tars: Optional[Callable[[], Set[str]]] = None,
      get_quarantine_skip_paths: Optional[Callable[[], Set[str]]] = None,
      ingest_ready_fn=None,
      archive_stats_files_fn=None,
      day_raw_removal_coordinator=None,
      day_close_manifest_coordinator=None,
      get_day_close_candidate_inputs=None,
      get_tree_rss_bytes=None,
      startup_snapshot_coordinator=None,
      get_ingest_pool_in_flight_count=None,
      get_chunk_in_progress=None,
      get_chunk_day_tokens=None,
      get_startup_ingest_gate_cleared=None,
      process_title: str = "sync_timedb.py",
      day_close_enabled: bool = True,
  ):
    self.archive_data_dir = archive_data_dir
    self.host_name_ext = host_name_ext
    self.tgz_archive_dir = tgz_archive_dir
    self.local_tz = local_tz
    self.log_fn = log_fn
    self.get_disqualified_daily_tars = get_disqualified_daily_tars
    self.get_delete_disqualified_daily_tars = (
        get_delete_disqualified_daily_tars or get_disqualified_daily_tars
    )
    self.get_pending_stats_count = get_pending_stats_count
    self.get_idle_seconds = get_idle_seconds
    self.get_quarantine_skip_paths = get_quarantine_skip_paths or (lambda: set())
    self.ingest_ready_fn = ingest_ready_fn
    self.archive_stats_files_fn = archive_stats_files_fn
    self.day_raw_removal_coordinator = day_raw_removal_coordinator
    self.day_close_manifest_coordinator = day_close_manifest_coordinator
    self.get_day_close_candidate_inputs = get_day_close_candidate_inputs
    self.get_tree_rss_bytes = get_tree_rss_bytes
    self.startup_snapshot_coordinator = startup_snapshot_coordinator
    self.get_ingest_pool_in_flight_count = (
        get_ingest_pool_in_flight_count or (lambda: 0)
    )
    self.get_chunk_in_progress = get_chunk_in_progress or (lambda: False)
    self.get_chunk_day_tokens = get_chunk_day_tokens or (lambda: set())
    self.get_startup_ingest_gate_cleared = (
        get_startup_ingest_gate_cleared or (lambda: True)
    )
    self.process_title = process_title
    # CLI ``backlog`` dual-mode: ingest only; ``current``/date-range own day-close.
    self.day_close_enabled = bool(day_close_enabled)
    self._defer_tracker = JanitorDeferTracker()
    self._allow_tick_chaining = True
    self._maintenance_pass_cached_inputs = None

    self._session_executor = SessionSingleFlightExecutor(
        thread_name_prefix="archive-janitor",
        process_title=self.process_title,
        thread_role="archive-janitor",
        enabled=True,
    )
    self._future = None
    self._debt_heap: list = []
    self._debt_seen: Set[tuple] = set()
    self._debt_lock = threading.Lock()
    self._hints_state_lock = threading.Lock()
    self._persist_hints_lock = threading.Lock()
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
    self._day_close_pool: Optional[ThreadPoolExecutor] = None
    self._day_close_pool_size = 0
    self._day_close_in_flight: Dict[Any, DayDebt] = {}
    self._day_close_in_flight_lock = threading.Lock()
    self._day_close_role_seq = 0

    self._load_hints_state()

  def get_accrual_snapshot_for_reconcile(self):
    """Return accrual snapshot when mapping is present (steady-state reconcile)."""
    with self._accrual_snapshot_lock:
      snap = self._accrual_snapshot
    if snap is None or not snap.mapping:
      return None
    return snap

  def _load_hints_state(self):
    prior = load_archive_maint_hints(self.archive_data_dir) or {}
    with self._hints_state_lock:
      self._validated_days.update(
          prune_validated_days_hints(prior.get("validated_days") or {}))
      self._day_phases.update(
          prune_day_phases_hints(prior.get("day_phases") or {}))
    with self._debt_lock:
      day_close_loaded = set()
      debt_migrated = False
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
          debt_migrated = True
          continue
        if kind in _DAY_PIPELINE_KINDS:
          if not self.day_close_enabled:
            continue
          tar_norm = os.path.normpath(tar_path)
          if kind != DebtKind.DAY_CLOSE:
            debt_migrated = True
          if tar_norm in day_close_loaded:
            continue
          day_close_loaded.add(tar_norm)
          self._enqueue_day_close_locked(tar_norm, persist=False)
          continue
        self._enqueue_debt_locked(kind, tar_path, persist=False)
      self._trim_heap_to_max_entries_locked()
    if debt_migrated:
      self._persist_hints()

  def _persist_hints(self, *, paths_hint=None, host_dirs_hint=None):
    with self._persist_hints_lock:
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

  def _enqueue_day_close_locked(self, tar_path: str, *, persist: bool = True) -> bool:
    tar_norm = os.path.normpath(tar_path) if tar_path else tar_path
    if not tar_norm or tar_norm == _LOCK_CLEANUP_TAR_SENTINEL:
      return False
    if self._day_pipeline_queued_locked(tar_norm):
      return False
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
    return True

  def _enqueue_day_close(self, tar_path: str, *, persist: bool = True) -> bool:
    if not self.day_close_enabled:
      return False
    with self._debt_lock:
      added = self._enqueue_day_close_locked(tar_path, persist=False)
      if persist:
        self._trim_heap_to_max_entries_locked()
    if persist:
      self._persist_hints()
    return added

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
      self._future = self._session_executor.submit(self._run_tick_body)
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
    coord = self.day_close_manifest_coordinator
    if coord is not None:
      recover = getattr(coord, "recover_stale_manifest_entries", None)
      if callable(recover):
        recover()
    if _is_heavy_maintenance_reason(reason):
      self.run_heavy_maintenance_pass(reason=reason)
    else:
      self.run_light_maintenance_pass(reason=reason)

  def _remaining_raw_from_accrual_only(self) -> dict:
    """Accrual remaining map only — never builds a full-tree snapshot."""
    with self._accrual_snapshot_lock:
      accrual_snapshot = self._accrual_snapshot
    if accrual_snapshot is None:
      return {}
    return build_remaining_raw_stats_by_daily_gz(
        self.archive_data_dir,
        self.host_name_ext,
        self.tgz_archive_dir,
        maintenance_snapshot=accrual_snapshot,
        allow_full_snapshot=False,
    )

  def run_light_maintenance_pass(self, *, reason: str):
    """Refresh candidate report from accrual only; no full-tree collect."""
    if not self.tgz_archive_dir:
      return
    with self._accrual_snapshot_lock:
      accrual_snapshot = self._accrual_snapshot
    remaining = self._remaining_raw_from_accrual_only()
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
    newly_queued = self._discover_and_enqueue_ready_day_close(reason=reason)
    if newly_queued:
      self._log_day_close_candidate_report(
          reason=reason,
          remaining_raw_by_gz=remaining,
          newly_queued_tars=newly_queued,
      )

  def run_heavy_maintenance_pass(self, *, reason: str):
    """Janitor-thread: full snapshot refresh (startup adopt or collect)."""
    coord = self.startup_snapshot_coordinator
    if not self.tgz_archive_dir:
      if reason == "startup" and coord is not None:
        coord.mark_startup_heavy_maintenance_finished()
      return
    in_flight = int(self.get_ingest_pool_in_flight_count() or 0)
    chunk_active = bool(self.get_chunk_in_progress())
    if chunk_active or in_flight > 0:
      defer_reason = (
          "chunk_in_progress" if chunk_active else "ingest_in_flight"
      )
      self.log_fn(
          "janitor: heavy maintenance deferred reason=%s n=%d "
          "maintenance_reason=%s"
          % (
              defer_reason,
              in_flight if defer_reason == "ingest_in_flight" else 1,
              reason,
          ),
          flush=True,
      )
      self.signal_scheduled_maintenance_pass(reason=reason)
      return
    startup_pass = reason == "startup" and coord is not None
    day_ingest_complete_pass = str(reason).startswith("day_ingest_complete:")
    pass_t0 = time.time()
    self.log_fn(
        "janitor: heavy maintenance begin reason=%s "
        "(classify/report; not an ingest gate)"
        % reason,
        flush=True,
    )
    sub_remaining_map_s = 0.0
    sub_candidate_report_s = 0.0
    sub_scheduled_submit_s = 0.0
    sub_trim_s = 0.0
    sub_lock_cleanup_s = 0.0
    self._maintenance_pass_cached_inputs = None
    if self.get_day_close_candidate_inputs is not None:
      try:
        cached_inputs = self.get_day_close_candidate_inputs() or {}
        if isinstance(cached_inputs, dict):
          self._maintenance_pass_cached_inputs = cached_inputs
      except Exception:
        self._maintenance_pass_cached_inputs = {}
    if startup_pass:
      coord.mark_startup_heavy_maintenance_started()
    snap_t0 = time.time()
    snapshot = None
    adopted = False
    try:
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
      if snapshot is None and day_ingest_complete_pass:
        with self._accrual_snapshot_lock:
          existing_accrual = self._accrual_snapshot
        if existing_accrual is not None and (
            existing_accrual.closed_paths or existing_accrual.mapping
        ):
          snapshot = copy_archive_maintenance_snapshot(existing_accrual)
          adopted = True
          self.log_fn(
              "janitor: heavy day_ingest_complete adopted accrual snapshot "
              "closed_paths=%d mapping_groups=%d"
              % (
                  len(snapshot.closed_paths),
                  len(snapshot.mapping or {}),
              ),
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
              build_ready_set=False,
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
      remaining_t0 = time.time()
      remaining = self._remaining_raw_from_accrual_only()
      sub_remaining_map_s = time.time() - remaining_t0
      report_t0 = time.time()
      # One classify for candidate report + discover (startup heavy pass was ~2×).
      shared_entries = self._build_day_close_candidate_entries(
          remaining_raw_by_gz=remaining,
          newly_queued_tars=set(),
          reason=reason,
      )
      self._log_day_close_candidate_entries(
          shared_entries,
          reason=reason,
      )
      sub_candidate_report_s = time.time() - report_t0
      submit_t0 = time.time()
      newly_queued = self._discover_and_enqueue_ready_day_close(
          reason=reason,
          entries=shared_entries,
      )
      sub_scheduled_submit_s = time.time() - submit_t0
      if newly_queued:
        report_t0 = time.time()
        # Re-classify only when enqueue changed queued status for the report.
        self._log_day_close_candidate_report(
            reason=reason,
            remaining_raw_by_gz=remaining,
            newly_queued_tars=newly_queued,
        )
        sub_candidate_report_s += time.time() - report_t0
      trim_t0 = time.time()
      self._trim_accrual_snapshot_memory()
      sub_trim_s = time.time() - trim_t0
      lock_t0 = time.time()
      removed_locks = self._run_scheduled_archive_lock_cleanup(reason=reason)
      sub_lock_cleanup_s = time.time() - lock_t0
      if removed_locks:
        self.log_fn(
            "janitor: scheduled lock_cleanup removed=%d reason=%s"
            % (removed_locks, reason),
            flush=True,
        )
      self.log_fn(
          "janitor: heavy maintenance sub_phases reason=%s "
          "remaining_map_s=%.3f candidate_report_s=%.3f "
          "scheduled_submit_s=%.3f trim_s=%.3f lock_cleanup_s=%.3f "
          "maintenance_pass_s=%.3f"
          % (
              reason,
              sub_remaining_map_s,
              sub_candidate_report_s,
              sub_scheduled_submit_s,
              sub_trim_s,
              sub_lock_cleanup_s,
              time.time() - pass_t0,
          ),
          flush=True,
      )
      self.log_fn(
          "janitor: heavy maintenance finished reason=%s duration_s=%.3f"
          % (reason, time.time() - pass_t0),
          flush=True,
      )
    finally:
      self._maintenance_pass_cached_inputs = None
      if startup_pass and coord is not None:
        coord.mark_startup_heavy_maintenance_finished()

  def _trim_accrual_snapshot_memory(self):
    """Release large snapshot lists after hints are persisted on disk."""
    with self._accrual_snapshot_lock:
      snapshot = self._accrual_snapshot
      if snapshot is None:
        return
      closed_count = len(snapshot.closed_paths)
      snapshot.closed_paths = []
      snapshot.head_identity_by_path.clear()
      snapshot.gate_identities_by_path.clear()
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
    if remaining_raw_by_gz_has_paths_on_disk(
        self._blocking_remaining_raw_for_tar(tar_norm),
        zst_path,
    ):
      return True
    return False

  def enqueue_scheduled_day_close(self, *, reason: str):
    """Enqueue ``DAY_CLOSE`` for eligible calendar days (startup + every N chunks)."""
    if not self.tgz_archive_dir or not os.path.isdir(self.tgz_archive_dir):
      return
    disqualified = set(self.get_disqualified_daily_tars())
    remaining = self._remaining_raw_from_accrual_only()
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

  def _enqueue_eligible_day_close(
      self,
      tar_norm: str,
      *,
      reason: str,
      disqualified: Optional[Set[str]] = None,
  ) -> Tuple[bool, str]:
    """Enqueue manifest + ``DAY_CLOSE`` debt when checkpoint-complete eligibility passes."""
    if not self.day_close_enabled:
      return False, "day_close_disabled"
    coord = self.day_close_manifest_coordinator
    if coord is not None:
      if hasattr(coord, "enqueue_day_close_result"):
        return coord.enqueue_day_close_result(
            tar_norm,
            reason,
            disqualified_daily_tars=disqualified,
        )
      ok = coord.enqueue_day_close(
          tar_norm,
          reason,
          disqualified_daily_tars=disqualified,
      )
      return ok, ("" if ok else "enqueue_rejected")
    return self._enqueue_day_close_debt_if_eligible(
        tar_norm,
        reason=reason,
        disqualified=disqualified,
    )

  def _enqueue_day_close_debt_if_eligible(
      self,
      tar_norm: str,
      *,
      reason: str,
      disqualified: Optional[Set[str]] = None,
  ) -> Tuple[bool, str]:
    tar_norm = os.path.normpath(tar_norm or "")
    if not tar_norm:
      return False, ""
    inputs = self._get_maintenance_pass_candidate_inputs()
    if disqualified is None:
      disqualified = set(self.get_disqualified_daily_tars())
    with self._hints_state_lock:
      day_phases = dict(self._day_phases)
    remaining = self._remaining_raw_from_accrual_only()
    eligible, skip_reason = daily_tar_eligible_for_day_close_submit(
        tar_norm,
        unprocessed_by_tar=inputs.get("unprocessed_by_tar"),
        disqualified_daily_tars=disqualified,
        day_phases=day_phases,
        remaining_raw_by_gz=remaining,
        local_tz=self.local_tz,
        day_raw_removal=self.day_raw_removal_coordinator,
        tgz_archive_dir=self.tgz_archive_dir,
    )
    if not eligible:
      if skip_reason and self.log_fn:
        self.log_fn(
            "janitor: day_close enqueue skip tar=%s reason=%s"
            % (tar_norm, skip_reason),
            flush=True,
        )
      return False, skip_reason or "submit_ineligible"
    with self._debt_lock:
      if self._day_pipeline_queued_locked(tar_norm):
        return False, "already_on_debt_heap"
      self._enqueue_day_close_locked(tar_norm, persist=False)
      self._trim_heap_to_max_entries_locked()
    self._persist_hints()
    if self.log_fn:
      self.log_fn(
          "janitor: day_close enqueue tar=%s reason=%s"
          % (tar_norm, reason),
          flush=True,
      )
    return True, reason

  def get_day_phases_snapshot(self) -> Dict[str, Any]:
    with self._hints_state_lock:
      return dict(self._day_phases)

  def _day_close_active_tar_paths(self) -> Set[str]:
    """Tars that are truly in-progress for classify ``day_close_in_progress``.

    Includes live workers, debt-heap ``DAY_CLOSE``, and manifest *worker-slot*
    statuses (queued/submitted/sealing/raw_removal). Excludes ``deferred`` /
    ``waiting_on_ingest`` so handoff-deferred days can classify as
    ``ready_for_enqueue`` and discover can resume after ingest advances.
    """
    active = set(self._day_close_live_worker_tar_paths())
    active |= self._debt_heap_tar_paths()
    coord = self.day_close_manifest_coordinator
    if coord is not None and hasattr(coord, "manifest_worker_slot_tar_paths"):
      active |= set(coord.manifest_worker_slot_tar_paths() or ())
    return active

  def _day_close_live_worker_tar_paths(self) -> Set[str]:
    with self._day_close_in_flight_lock:
      return {
          os.path.normpath(debt.tar_path)
          for debt in self._day_close_in_flight.values()
          if debt and debt.tar_path
      }

  def _day_close_worker_occupancy_tar_paths(self) -> Set[str]:
    live_workers = self._day_close_live_worker_tar_paths()
    coord = self.day_close_manifest_coordinator
    if coord is not None and hasattr(coord, "active_worker_tar_paths"):
      return coord.active_worker_tar_paths(live_worker_tars=live_workers)
    active = set(live_workers)
    active |= self._debt_heap_tar_paths()
    return active

  def _format_tick_waiting_tar_paths(self, in_flight: Dict[Any, DayDebt]) -> str:
    paths = sorted(
        {
            os.path.normpath(debt.tar_path)
            for debt in in_flight.values()
            if debt and debt.tar_path
        }
    )
    return ",".join(paths) if paths else "-"

  def _log_tick_waiting_heartbeat(
      self,
      in_flight: Dict[Any, DayDebt],
      last_heartbeat: list,
  ) -> None:
    if not in_flight or not self.log_fn:
      return
    now = time.monotonic()
    if (
        last_heartbeat
        and last_heartbeat[0] is not None
        and (now - last_heartbeat[0]) < _TICK_WAIT_HEARTBEAT_SECONDS
    ):
      return
    self.log_fn(
        "janitor: tick waiting in_flight=%d debt_remaining=%d tars=%s"
        % (
            len(in_flight),
            self.debt_depth(),
            self._format_tick_waiting_tar_paths(in_flight),
        ),
        flush=True,
    )
    if last_heartbeat:
      last_heartbeat[0] = now

  def _finalize_day_close_manifest(self, tar_norm: str) -> None:
    coord = self.day_close_manifest_coordinator
    if coord is None:
      return
    try:
      coord.finalize_complete_if_filesystem(tar_norm)
    except Exception:
      pass

  def _discover_and_enqueue_ready_day_close(
      self,
      *,
      reason: str,
      slot_budget: Optional[int] = None,
      entries=None,
  ) -> Set[str]:
    if not self.day_close_enabled:
      return set()
    if not self.tgz_archive_dir:
      return set()
    if entries is None:
      inputs = self._get_maintenance_pass_candidate_inputs()
      if not isinstance(inputs, dict):
        inputs = {}
      remaining = self._remaining_raw_from_accrual_only()
      entries = self._build_day_close_candidate_entries(
          remaining_raw_by_gz=remaining,
          newly_queued_tars=set(),
          reason=reason,
          inputs=inputs,
      )
    max_inflight = cfg.get_sync_day_close_max_inflight()
    live_workers = self._day_close_live_worker_tar_paths()
    coord = self.day_close_manifest_coordinator
    if coord is not None and hasattr(coord, "active_discover_cap_tar_paths"):
      active = coord.active_discover_cap_tar_paths(live_worker_tars=live_workers)
    else:
      active = set(live_workers)
    disqualified = set(self.get_disqualified_daily_tars())
    newly_queued: Set[str] = set()
    skipped_inflight = 0
    skipped_eligible = 0
    ready_for_enqueue_n = 0
    discover_reason = "discover_ready_%s" % reason
    slot_budget_n = None if slot_budget is None else max(0, int(slot_budget))
    for entry in entries:
      if entry.get("status") != "ready_for_enqueue":
        continue
      if "awaiting_janitor_discover" not in (entry.get("reasons") or ()):
        continue
      ready_for_enqueue_n += 1
      if len(active) >= max_inflight:
        skipped_inflight += 1
        continue
      if slot_budget_n is not None and len(newly_queued) >= slot_budget_n:
        continue
      tar_norm = os.path.normpath(entry["tar_path"])
      ok, reject_reason = self._enqueue_eligible_day_close(
          tar_norm,
          reason=discover_reason,
          disqualified=disqualified,
      )
      if ok:
        newly_queued.add(tar_norm)
        active.add(tar_norm)
      else:
        skipped_eligible += 1
        if reject_reason and self.log_fn:
          self.log_fn(
              "janitor: discover_enqueue_reject tar=%s reason=%s"
              % (tar_norm, reject_reason),
              flush=True,
          )
    if newly_queued or skipped_inflight or ready_for_enqueue_n or skipped_eligible:
      breakdown: Dict[str, int] = {}
      if coord is not None and hasattr(coord, "discover_inflight_breakdown"):
        try:
          breakdown = coord.discover_inflight_breakdown(
              live_worker_tars=live_workers,
          )
        except Exception:
          breakdown = {}
      free_slots = self._day_close_free_slots()
      log_suffix = ""
      if reason == "tick_slot_free":
        log_suffix = " free_slots=%d" % free_slots
      self.log_fn(
          "janitor: discover_ready_day_close enqueued=%d skipped_inflight=%d "
          "skipped_eligible=%d ready_for_enqueue_n=%d max_inflight=%d active_workers=%d "
          "deferred_waiting=%d debt_heap=%d manifest_pending=%d "
          "discover_cap=%d worker_occupancy=%d reason=%s%s"
          % (
              len(newly_queued),
              skipped_inflight,
              skipped_eligible,
              ready_for_enqueue_n,
              max_inflight,
              breakdown.get("active_workers_n", len(live_workers)),
              breakdown.get("deferred_waiting_n", 0),
              breakdown.get("debt_heap_n", 0),
              breakdown.get("manifest_pending_n", 0),
              breakdown.get("discover_cap_n", len(active)),
              breakdown.get("worker_occupancy_n", len(active)),
              reason,
              log_suffix,
          ),
          flush=True,
      )
    return newly_queued

  def enqueue_immediate_day_close(self, tar_path: str, *, reason: str) -> bool:
    """Enqueue ``DAY_CLOSE`` when ingest checkpoint is complete for the day."""
    tar_norm = os.path.normpath(tar_path) if tar_path else ""
    if not tar_norm:
      return False
    submit_reason = "day_ingest_complete:%s" % reason
    submitted, _reject = self._enqueue_eligible_day_close(
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
    """Bulk ``DAY_CLOSE`` enqueue (oldest-first list); one wake signal."""
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
      if self._enqueue_eligible_day_close(
          tar_norm,
          reason=submit_reason,
          disqualified=disqualified,
      )[0]:
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
        "janitor: day_close bulk enqueue reason=%s days=%d"
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
    from hpcperfstats.dbload.lib.sync_timedb_day_raw_removal import (
        day_removal_manifest_dir,
    )

    manifest_dir = day_removal_manifest_dir(self.archive_data_dir)
    return cleanup_orphan_fnctl_lock_sidecars(manifest_dir)

  def _run_scheduled_archive_lock_cleanup(self, *, reason: str = "") -> int:
    if reason == "startup":
      return self._run_tick_lock_cleanup()
    removed = cleanup_stale_fnctl_lock_sidecars(self.archive_data_dir)
    removed += cleanup_stale_fnctl_lock_sidecars(self.tgz_archive_dir)
    return removed

  def _get_maintenance_pass_candidate_inputs(self) -> Dict[str, Any]:
    cached = self._maintenance_pass_cached_inputs
    if isinstance(cached, dict):
      return cached
    if self.get_day_close_candidate_inputs is None:
      return {}
    try:
      inputs = self.get_day_close_candidate_inputs() or {}
    except Exception:
      inputs = {}
    if not isinstance(inputs, dict):
      inputs = {}
    return inputs

  def _consume_dedupe_hints(self, disqualified: Set[str]):
    """Enqueue ``DAY_CLOSE`` for days flagged by ingest member-cache populate."""
    try:
      from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
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
      self._enqueue_eligible_day_close(
          tar_norm,
          reason="dedupe_hint",
          disqualified=disqualified,
      )

  def _effective_tick_budget(self) -> float:
    """Wall-clock budget for one janitor wake (debt burst / idle scale only)."""
    budget = float(cfg.get_archive_janitor_budget_seconds())
    if self.debt_depth() >= cfg.get_archive_janitor_debt_high_watermark():
      burst = float(cfg.get_archive_janitor_debt_burst_factor())
      budget *= burst
    idle_s = self.get_idle_seconds()
    if self.get_pending_stats_count() == 0 and idle_s >= cfg.get_archive_maintenance_idle_seconds():
      budget *= 2.0
    return budget

  def _ensure_day_close_pool(self) -> ThreadPoolExecutor:
    size = max(1, int(cfg.get_sync_day_close_max_inflight()))
    if self._day_close_pool is not None and self._day_close_pool_size == size:
      return self._day_close_pool
    if self._day_close_pool is not None:
      self._day_close_pool.shutdown(wait=False)
    self._day_close_pool = ThreadPoolExecutor(
        max_workers=size,
        thread_name_prefix="day-close",
    )
    self._day_close_pool_size = size
    return self._day_close_pool

  def _day_close_free_slots(self) -> int:
    with self._day_close_in_flight_lock:
      in_flight = len(self._day_close_in_flight)
    return max(0, max(1, int(cfg.get_sync_day_close_max_inflight())) - in_flight)

  def _reap_completed_day_close_futures(self) -> tuple:
    """Reap done cross-tick day-close futures. Return ``(ok_count, mutated)``."""
    with self._day_close_in_flight_lock:
      done_items = [
          (future, debt)
          for future, debt in list(self._day_close_in_flight.items())
          if future.done()
      ]
    ok_count = 0
    mutated = False
    for future, debt in done_items:
      with self._day_close_in_flight_lock:
        self._day_close_in_flight.pop(future, None)
      try:
        success = future.result()
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
        self._enqueue_debt(debt.kind, debt.tar_path, persist=False)
        mutated = True
        continue
      if success:
        mutated = True
        if debt.kind == DebtKind.DAY_CLOSE:
          ok_count += 1
      else:
        self._enqueue_debt(debt.kind, debt.tar_path, persist=False)
        mutated = True
    return ok_count, mutated

  def _pop_one_day_close_debt(
      self,
      disqualified: Set[str],
      *,
      skip_tars: Optional[Set[str]] = None,
  ) -> Optional[DayDebt]:
    blocked = set(disqualified)
    if skip_tars:
      blocked |= skip_tars
    with self._debt_lock:
      items = self._pop_eligible_debt_locked(blocked, max_days=1)
    for debt in items:
      if debt.kind == DebtKind.DAY_CLOSE:
        return debt
      # Legacy non-DAY_CLOSE debts: process inline on coordinator (rare).
      return debt
    return None

  def _submit_day_close_debt(
      self,
      debt: DayDebt,
      *,
      snapshot,
      validation_cache,
      disqualified: Set[str],
  ):
    pool = self._ensure_day_close_pool()
    with self._day_close_in_flight_lock:
      self._day_close_role_seq += 1
      role_id = self._day_close_role_seq
    role = "day-close-%d" % (role_id % max(1, self._day_close_pool_size))

    def _run():
      set_daemon_thread_title(
          "", script_name=self.process_title, role=role,
      )
      close_old_connections()
      try:
        tick_stats = {"days_started": 0}
        return self._process_debt_item(
            debt,
            snapshot=snapshot,
            validation_cache=validation_cache,
            disqualified=disqualified,
            tick_stats=tick_stats,
        )
      finally:
        close_old_connections()

    future = pool.submit(_run)
    with self._day_close_in_flight_lock:
      self._day_close_in_flight[future] = debt

    def _on_day_close_done(_fut):
      try:
        self.signal_work_available()
      except Exception:
        pass

    future.add_done_callback(_on_day_close_done)
    return future

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
    with self._accrual_snapshot_lock:
      accrual_snapshot = self._accrual_snapshot
    # Prefer accrual; otherwise day-scoped collect — never full-tree snapshot
    # on the janitor tick hot path (head-lock contention with MainThread).
    fresh = build_remaining_raw_for_daily_tar(
        self.archive_data_dir,
        self.host_name_ext,
        self.tgz_archive_dir,
        tar_norm,
        maintenance_snapshot=accrual_snapshot,
        allow_full_snapshot=False,
        log_fn=self.log_fn,
    )
    self._tick_remaining_raw_cache[tar_norm] = fresh
    return fresh

  def _blocking_remaining_raw_for_tar(self, tar_path: str) -> dict:
    """Quarantine-aware remaining-raw map for DECISION gates (not census)."""
    tar_norm = os.path.normpath(tar_path)
    cached = self._tick_remaining_raw_cache.get(("blocking", tar_norm))
    if cached is not None:
      return cached
    from hpcperfstats.dbload.lib.sync_timedb_day_raw_removal import (
        remaining_raw_blocking_day_incomplete,
    )
    with self._accrual_snapshot_lock:
      accrual_snapshot = self._accrual_snapshot

    def _snapshot():
      return accrual_snapshot

    quarantine_fn = None
    coord = self.day_raw_removal_coordinator
    if coord is not None:
      quarantine_fn = getattr(coord, "get_quarantine_skip_paths", None)
    blocking = remaining_raw_blocking_day_incomplete(
        tar_norm,
        archive_data_dir=self.archive_data_dir,
        host_name_ext=self.host_name_ext,
        tgz_archive_dir=self.tgz_archive_dir,
        get_quarantine_skip_paths=quarantine_fn,
        get_maintenance_snapshot=_snapshot if accrual_snapshot is not None else None,
        log_fn=self.log_fn,
    )
    self._tick_remaining_raw_cache[("blocking", tar_norm)] = blocking
    return blocking

  def _run_tick_body(self):
    set_daemon_thread_title(
        "", script_name=self.process_title, role="archive-janitor")
    self._tick_depth += 1
    tick_made_progress = False
    budget_throttled_this_tick = False
    tick_mutated = False
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

      if not self.day_close_enabled:
        # CLI ``backlog`` dual-mode: ingest-only for day-close, but still run
        # pending scheduled maintenance so startup snapshot publishes
        # (supervisor blocks in wait_for_snapshot until it lands).
        with self._maintenance_pass_lock:
          pass_reason = self._pending_maintenance_pass_reason
          self._pending_maintenance_pass_reason = None
        if pass_reason:
          self.run_scheduled_maintenance_pass(reason=pass_reason)
        self._ticks_completed += 1
        return

      tick_t0 = time.time()
      maintenance_pass_s = 0.0
      days_processed = 0
      debt_popped = 0
      days_started = 0
      # Reap completed cross-tick day-close futures before discover/fill so
      # free_slots and ghost-manifest reconcile see live truth.
      reaped_ok, reaped_mutated = self._reap_completed_day_close_futures()
      if reaped_ok:
        days_processed += reaped_ok
        tick_made_progress = True
      if reaped_mutated:
        tick_mutated = True
      # Reconcile ghost manifest slots before discover so discover_cap is not
      # filled by stale pending rows that have no heap debt / live worker.
      coord = self.day_close_manifest_coordinator
      if coord is not None:
        coord.recover_stale_manifest_entries()
        if hasattr(coord, "reconcile_manifest_with_debt_heap"):
          coord.reconcile_manifest_with_debt_heap(
              debt_tar_paths=self._debt_heap_tar_paths(),
              live_worker_tars=self._day_close_live_worker_tar_paths(),
          )
      self._discover_and_enqueue_ready_day_close(reason="tick")
      with self._maintenance_pass_lock:
        pass_reason = self._pending_maintenance_pass_reason
        self._pending_maintenance_pass_reason = None
      if pass_reason:
        maint_t0 = time.time()
        self.run_scheduled_maintenance_pass(reason=pass_reason)
        maintenance_pass_s += time.time() - maint_t0
      if not self.get_startup_ingest_gate_cleared():
        with self._debt_lock:
          debt_depth = len(self._debt_heap)
        self.log_fn(
            "janitor: tick deferred day_close_execution reason=startup_prep "
            "debt_depth=%d maintenance_pass_s=%.3f"
            % (debt_depth, maintenance_pass_s),
            flush=True,
        )
        self._ticks_completed += 1
        return
      # Prefill bookkeeping (disqualified scan + lock cleanup) is outside the
      # debt-drain budget — arming early let slow get_disqualified burn
      # archive_janitor_budget_seconds so _fill_free_slots never entered
      # (zero_pop with free_slots>0 / sample_tars=- forever).
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
      validation_cache = {"hits": 0, "misses": 0}
      with self._accrual_snapshot_lock:
        snapshot = self._accrual_snapshot
      in_flight: Dict[Any, DayDebt] = {}
      # Do not re-pop the same calendar day within one tick after a partial/failed
      # attempt (avoids busy-spinning the pool until budget expires).
      attempted_tars: Set[str] = set()

      # Debt-drain budget starts at fill (after maintenance + prefill), not
      # wall-clock for the whole tick.
      drain_budget_s = self._effective_tick_budget()
      budget_deadline = time.time() + drain_budget_s
      self.log_fn(
          "janitor: tick debt_drain_begin budget_s=%.3f debt_remaining=%d "
          "free_slots=%d"
          % (
              drain_budget_s,
              self.debt_depth(),
              self._day_close_free_slots(),
          ),
          flush=True,
      )

      def _budget_ok() -> bool:
        return time.time() < budget_deadline

      def _fill_free_slots() -> int:
        nonlocal debt_popped, days_started, tick_mutated, disqualified
        nonlocal tick_made_progress, budget_throttled_this_tick
        started = 0
        while _budget_ok() and self._day_close_free_slots() > 0:
          disqualified = set(self.get_disqualified_daily_tars())
          debt = self._pop_one_day_close_debt(
              disqualified, skip_tars=attempted_tars,
          )
          if debt is None:
            free_slots = self._day_close_free_slots()
            if free_slots > 0:
              self._discover_and_enqueue_ready_day_close(
                  reason="tick_slot_free",
                  slot_budget=free_slots,
              )
              debt = self._pop_one_day_close_debt(
                  disqualified, skip_tars=attempted_tars,
              )
            if debt is None:
              break
          debt_popped += 1
          attempted_tars.add(os.path.normpath(debt.tar_path))
          if debt.tar_path in disqualified:
            self._enqueue_debt(debt.kind, debt.tar_path, persist=False)
            tick_mutated = True
            continue
          if debt.kind != DebtKind.DAY_CLOSE:
            # Rare legacy debt: run on coordinator thread.
            try:
              success = self._process_debt_item(
                  debt,
                  snapshot=snapshot,
                  validation_cache=validation_cache,
                  disqualified=disqualified,
                  tick_stats={"days_started": 0},
              )
              if success:
                tick_mutated = True
                tick_made_progress = True
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
              self._enqueue_debt(debt.kind, debt.tar_path, persist=False)
              tick_mutated = True
            continue
          future = self._submit_day_close_debt(
              debt,
              snapshot=snapshot,
              validation_cache=validation_cache,
              disqualified=disqualified,
          )
          in_flight[future] = debt
          days_started += 1
          started += 1
        return started

      _fill_free_slots()
      if not in_flight and debt_popped == 0:
        free_slots = self._day_close_free_slots()
        remaining = self.debt_depth()
        if remaining > 0 and free_slots > 0:
          heap_tars = self._debt_heap_tar_paths()
          blocked = sorted(heap_tars & set(disqualified))
          sample = ",".join(blocked[:8]) if blocked else ""
          budget_remaining_s = budget_deadline - time.time()
          self.log_fn(
              "janitor: tick zero_pop debt_remaining=%d free_slots=%d "
              "disqualified_on_heap=%d sample_tars=%s budget_remaining_s=%.3f"
              % (
                  remaining,
                  free_slots,
                  len(blocked),
                  sample or "-",
                  budget_remaining_s,
              ),
              flush=True,
          )
          self.signal_work_available()
        self._ticks_completed += 1
        self.log_fn(
            "Archive janitor tick done days=%d debt_remaining=%d duration_s=%.3f "
            "maintenance_pass_s=%.3f debt_popped=0 days_started=0 days_completed=%d"
            % (
                days_processed,
                self.debt_depth(),
                time.time() - tick_t0,
                maintenance_pass_s,
                days_processed,
            ),
            flush=True,
        )
      else:
        tick_wait_heartbeat: list = [None]
        while in_flight:
          timeout = max(0.05, budget_deadline - time.time())
          wait_timeout = min(timeout, _TICK_WAIT_HEARTBEAT_SECONDS)
          done, _pending = wait(
              list(in_flight.keys()),
              timeout=wait_timeout,
              return_when=FIRST_COMPLETED,
          )
          if not done:
            self._log_tick_waiting_heartbeat(in_flight, tick_wait_heartbeat)
            if not _budget_ok():
              self._budget_throttled_count += 1
              budget_throttled_this_tick = True
              self.log_fn(
                  "janitor: tick budget_exit debt_remaining=%d "
                  "scheduling_followup=yes"
                  % self.debt_depth(),
                  flush=True,
              )
              break
            continue
          for future in done:
            debt = in_flight.pop(future)
            with self._day_close_in_flight_lock:
              self._day_close_in_flight.pop(future, None)
            try:
              success = future.result()
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
              self._enqueue_debt(debt.kind, debt.tar_path, persist=False)
              tick_mutated = True
              continue
            if success:
              tick_mutated = True
              tick_made_progress = True
              if debt.kind == DebtKind.DAY_CLOSE:
                days_processed += 1
            else:
              self._enqueue_debt(debt.kind, debt.tar_path, persist=False)
              tick_mutated = True
          if _budget_ok():
            _fill_free_slots()
          else:
            self._budget_throttled_count += 1
            budget_throttled_this_tick = True
            self.log_fn(
                "janitor: tick budget_exit debt_remaining=%d "
                "scheduling_followup=yes"
                % self.debt_depth(),
                flush=True,
            )
            break

        # Non-blocking budget_exit: leave remaining futures in
        # ``_day_close_in_flight`` for cross-tick reap/fill. Do not drain-wait
        # (operator: duration_s=9217 class failure after budget_exit).
        if in_flight and budget_throttled_this_tick:
          self.log_fn(
              "janitor: tick budget_exit leave_in_flight=%d "
              "(nonblocking; follow-up will reap/fill)"
              % len(in_flight),
              flush=True,
          )
          in_flight.clear()
        elif in_flight:
          # Idle heap with workers still running: also non-blocking — done
          # callbacks + follow-up tick reap completions.
          self.log_fn(
              "janitor: tick leave_in_flight=%d "
              "(nonblocking; follow-up will reap/fill)"
              % len(in_flight),
              flush=True,
          )
          in_flight.clear()

        if tick_mutated:
          self._persist_hints()

        self._ticks_completed += 1
        self.log_fn(
            "Archive janitor tick done days=%d debt_remaining=%d duration_s=%.3f "
            "maintenance_pass_s=%.3f debt_popped=%d days_started=%d "
            "days_completed=%d"
            % (
                days_processed,
                self.debt_depth(),
                time.time() - tick_t0,
                maintenance_pass_s,
                debt_popped,
                days_started,
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
      with self._day_close_in_flight_lock:
        stuck = list(self._day_close_in_flight.items())
        self._day_close_in_flight.clear()
      for future, debt in stuck:
        try:
          future.result(timeout=0.1)
        except Exception:
          pass
        self._enqueue_debt(debt.kind, debt.tar_path, persist=False)
      self._persist_hints()
    finally:
      close_old_connections()
      with self._day_close_in_flight_lock:
        has_in_flight = bool(self._day_close_in_flight)
      if self._allow_tick_chaining and (
          (
              self.debt_depth() > 0
              and (
                  tick_made_progress
                  or tick_mutated
                  or budget_throttled_this_tick
              )
          )
          or has_in_flight
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

  def _build_day_close_candidate_entries(
      self,
      *,
      remaining_raw_by_gz=None,
      newly_queued_tars=None,
      reason: str = "",
      inputs=None,
  ):
    """Classify day-close candidates once for report and/or discover."""
    if self.get_day_close_candidate_inputs is None and inputs is None:
      return []
    if inputs is None:
      inputs = self._get_maintenance_pass_candidate_inputs()
    if not isinstance(inputs, dict):
      return []
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
    return classify_day_close_candidates(
        tgz_archive_dir=self.tgz_archive_dir,
        remaining_raw_by_gz=remaining_raw_by_gz,
        unprocessed_by_tar=inputs.get("unprocessed_by_tar"),
        disqualification_reasons=disq_reasons,
        day_phases=day_phases,
        local_tz=self.local_tz,
        day_close_in_progress_tars=self._day_close_active_tar_paths(),
        debt_heap_tars=self._debt_heap_tar_paths(),
        newly_queued_tars=newly_queued_tars or set(),
        queued_reason=day_close_queued_reason_for_report_reason(reason),
        day_raw_removal=self.day_raw_removal_coordinator,
    )

  def _log_day_close_candidate_entries(self, entries, *, reason: str) -> None:
    log_day_close_candidate_report(
        entries,
        reason=reason,
        log_fn=self.log_fn,
        async_progress_fn=None,
    )

  def _log_day_close_candidate_report(
      self,
      *,
      reason: str,
      remaining_raw_by_gz=None,
      newly_queued_tars=None,
  ) -> None:
    if self.get_day_close_candidate_inputs is None:
      return
    entries = self._build_day_close_candidate_entries(
        remaining_raw_by_gz=remaining_raw_by_gz,
        newly_queued_tars=newly_queued_tars,
        reason=reason,
    )
    self._log_day_close_candidate_entries(entries, reason=reason)

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
      coord = self.day_close_manifest_coordinator
      if coord is not None and coord.is_complete(os.path.normpath(debt.tar_path)):
        with self._hints_state_lock:
          self._day_phases[os.path.normpath(debt.tar_path)] = day_phase_hint_entry(
              debt.tar_path, "tar_dropped")
        return True
      return self._close_one_day(
          debt.tar_path,
          snapshot=snapshot,
          validation_cache=validation_cache,
          disqualified=disqualified,
      )
    if debt.kind in (DebtKind.DEDUPE, DebtKind.LOCK_CLEANUP):
      self.log_fn(
          "janitor: ignored legacy debt kind=%s tar=%s"
          % (debt.kind.value, debt.tar_path),
          flush=True,
      )
      return True
    return False

  def signal_day_close_yield(self, tar_path: str, *, reason: str) -> None:
    signal_day_close_yield(tar_path, reason=reason, log_fn=self.log_fn)

  def _chunk_day_tokens(self) -> Set[str]:
    try:
      tokens = self.get_chunk_day_tokens() or set()
      return set(tokens)
    except Exception:
      return set()

  def _check_day_close_defer(
      self,
      tar_path: str,
      *,
      phase: str,
      disqualified: Set[str],
      delete_disqualified: Optional[Set[str]] = None,
  ) -> tuple[bool, str]:
    cap_exceeded = self._defer_tracker.defer_cap_exceeded(tar_path)
    defer, reason = daily_tar_janitor_mutation_should_defer(
        tar_path,
        tgz_archive_dir=self.tgz_archive_dir,
        disqualified_daily_tars=disqualified,
        delete_disqualified_daily_tars=delete_disqualified,
        phase=phase,
        defer_cap_exceeded=cap_exceeded,
        chunk_in_progress=bool(self.get_chunk_in_progress()),
        chunk_day_tokens=self._chunk_day_tokens(),
    )
    if defer:
      self._defer_tracker.record_defer(tar_path)
      log_janitor_day_close_defer(
          tar_path,
          phase=phase,
          reason=reason,
          log_fn=self.log_fn,
      )
      return True, reason
    if cap_exceeded and self.log_fn:
      self.log_fn(
          "janitor: mutation proceed reason=defer_cap_exceeded tar=%s phase=%s"
          % (os.path.normpath(tar_path), phase or ""),
          flush=True,
      )
    self._defer_tracker.clear_tar(tar_path)
    return False, ""

  def _handle_day_close_yield(self, tar_path: str, exc: DayCloseYieldError) -> bool:
    log_janitor_day_close_yield(
        tar_path,
        phase=exc.phase or "",
        reason=exc.reason or "",
        log_fn=self.log_fn,
    )
    clear_day_close_yield(tar_path)
    self._enqueue_day_close(tar_path, persist=False)
    self._persist_hints()
    return False

  def _day_phase_name(self, tar_path: str):
    phase = self._day_phases.get(os.path.normpath(tar_path))
    if isinstance(phase, dict):
      return phase.get("phase")
    return phase

  def _day_phase_at_least(self, tar_path: str, target: str) -> bool:
    from hpcperfstats.dbload.lib.sync_timedb_manifest_contract import (
        day_phase_at_least,
    )
    return day_phase_at_least(self._day_phases, tar_path, target)

  def _close_one_day(
      self,
      tar_path: str,
      *,
      snapshot,
      validation_cache,
      disqualified: Set[str],
  ) -> bool:
    if not self.day_close_enabled:
      return False
    tar_norm = os.path.normpath(tar_path)
    coord = self.day_raw_removal_coordinator
    if not self._day_phase_at_least(tar_norm, "sealed"):
      if coord is not None and not coord.pre_seal_verification_complete(tar_norm):
        self.log_fn(
            "janitor: day_close pre_seal_verify start tar=%s" % tar_norm,
            flush=True,
        )
        if not coord.run_pre_seal_verify_sync(tar_norm):
          self._enqueue_day_close(tar_norm, persist=False)
          self._persist_hints()
          return False
        if coord.should_handoff_before_seal(tar_norm):
          coord.complete_handoff_to_ingest(
              tar_norm,
              reason="pre_seal_verify",
          )
          self._enqueue_day_close(tar_norm, persist=False)
          self._persist_hints()
          return False
      needs_dedupe = False
      if os.path.isfile(tar_norm):
        needs_dedupe = tar_has_duplicate_file_members(tar_norm)
      else:
        try:
          from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
              dedupe_hint_is_set,
          )
          from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
              calendar_date_from_daily_tar_path,
          )

          day = calendar_date_from_daily_tar_path(tar_norm)
          if day is not None and dedupe_hint_is_set(day.isoformat()):
            needs_dedupe = True
        except Exception:
          needs_dedupe = False
      if needs_dedupe:
        defer, _reason = self._check_day_close_defer(
            tar_norm,
            phase="dedupe",
            disqualified=disqualified,
        )
        if defer:
          self._enqueue_day_close(tar_norm, persist=False)
          self._persist_hints()
          return False
        if os.path.isfile(tar_norm):
          self.log_fn(
              "janitor: day_close dedupe tar=%s" % tar_norm,
              flush=True,
          )
          try:
            dedupe_tar_keep_largest_file_per_member(
                tar_norm,
                log_fn=self.log_fn,
                tgz_archive_dir=self.tgz_archive_dir,
                yield_phase="dedupe",
            )
          except DayCloseYieldError as exc:
            return self._handle_day_close_yield(tar_norm, exc)
        else:
          zst_path, gz_path = compressed_sibling_paths(tar_norm)
          sealed_path = zst_path if os.path.isfile(zst_path) else gz_path
          if os.path.isfile(sealed_path):
            from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
                dedupe_sealed_daily_archive,
            )

            self.log_fn(
                "janitor: day_close sealed dedupe %s" % sealed_path,
                flush=True,
            )
            try:
              dedupe_sealed_daily_archive(
                  sealed_path,
                  log_fn=self.log_fn,
                  tgz_archive_dir=self.tgz_archive_dir,
              )
            except DayCloseYieldError as exc:
              return self._handle_day_close_yield(tar_norm, exc)
      if not self._seal_one_day(tar_norm, disqualified=disqualified):
        return False
    if coord is not None:
      if not coord.post_seal_verification_complete(tar_norm):
        self.log_fn(
            "janitor: day_close post_seal_verify start tar=%s" % tar_norm,
            flush=True,
        )
        if not coord.run_post_seal_verify_sync(tar_norm):
          self._enqueue_day_close(tar_norm, persist=False)
          self._persist_hints()
          return False
      # Recover VERIFYING+POST_SEAL inconsistency (silent re-enqueue trap).
      promote = getattr(coord, "promote_phase_if_verify_stage_ahead", None)
      if callable(promote):
        promote(tar_norm)
      if not coord.verification_complete(tar_norm):
        self._enqueue_day_close(tar_norm, persist=False)
        self._persist_hints()
        return False
      delete_disqualified = set(self.get_delete_disqualified_daily_tars())
      if tar_norm in delete_disqualified:
        self.log_fn(
            "janitor: day_close delete deferred tar=%s reason=delete_disqualified"
            % tar_norm,
            flush=True,
        )
        self._enqueue_day_close(tar_norm, persist=False)
        self._persist_hints()
        return False
      delete_defer, _delete_reason = self._check_day_close_defer(
          tar_norm,
          phase="delete",
          disqualified=disqualified,
          delete_disqualified=delete_disqualified,
      )
      if delete_defer:
        self._enqueue_day_close(tar_norm, persist=False)
        self._persist_hints()
        return False
      if coord.delete_phase_done(tar_norm):
        upgraded = coord.reclassify_retryable_skips_after_handoff_sync(tar_norm)
        if upgraded:
          self.log_fn(
              "janitor: day_close reclassify upgraded=%d tar=%s"
              % (upgraded, tar_norm),
              flush=True,
          )
          coord.begin_deleting(tar_norm)
          coord.apply_batch_delete(tar_norm)
      coord.reopen_done_days_with_verified_on_disk()
      if not coord.delete_phase_done(tar_norm):
        self.log_fn(
            "janitor: day_close delete start tar=%s" % tar_norm,
            flush=True,
        )
        coord.begin_deleting(tar_norm)
        coord.apply_batch_delete(tar_norm)
      if coord.delete_phase_done(tar_norm):
        if not self._day_phase_at_least(tar_norm, "tar_dropped"):
          if not self._tar_drop_one_day(
              tar_norm,
              validation_cache,
              disqualified,
          ):
            self._enqueue_day_close(tar_norm, persist=False)
            self._persist_hints()
            return False
        self._finalize_day_close_manifest(tar_norm)
        return True
      self._enqueue_day_close(tar_norm, persist=False)
      self._persist_hints()
      return False
    if not self._day_phase_at_least(tar_norm, "tar_dropped"):
      if not self._tar_drop_one_day(
          tar_norm,
          validation_cache,
          disqualified,
      ):
        self._enqueue_day_close(tar_norm, persist=False)
        self._persist_hints()
        return False
    self._finalize_day_close_manifest(tar_norm)
    return True

  def _seal_one_day(self, tar_path: str, *, disqualified: Optional[Set[str]] = None) -> bool:
    """Seal daily ``.tar`` to ``.tar.zst``.

    Closed-raw on disk is expected after pre-seal verify (delete runs post-seal).
    Do not gate seal on remaining closed-raw; pre-seal/handoff and tar-drop own
    that safety. Calendar-today grace still applies.
    """
    disqualified = disqualified if disqualified is not None else set()
    defer, _reason = self._check_day_close_defer(
        tar_path,
        phase="seal",
        disqualified=disqualified,
    )
    if defer:
      self._enqueue_day_close(tar_path, persist=False)
      self._persist_hints()
      return False
    if not daily_tar_seal_calendar_eligible(tar_path, self.local_tz):
      self.log_fn(
          "Janitor seal deferred (calendar-today grace): %s" % tar_path,
          flush=True,
      )
      self._enqueue_day_close(tar_path, persist=False)
      self._persist_hints()
      return False
    remaining_raw_by_gz = self._blocking_remaining_raw_for_tar(tar_path)
    zst_path, gz_path = compressed_sibling_paths(tar_path)
    keep_tar = effective_keep_uncompressed_tar(
        tar_path,
        local_tz=self.local_tz,
    )
    self.log_fn(
        "janitor: day_close seal start tar=%s" % tar_path,
        flush=True,
    )
    try:
      zst_members = atomic_seal_tar_to_zst(
          tar_path,
          zst_path,
          cfg.get_archive_zstd_threads(),
          cfg.get_archive_zstd_level(),
          keep_tar,
          log_fn=self.log_fn,
          remaining_raw_by_gz=remaining_raw_by_gz,
          force_remove_uncompressed_tar=False,
          tgz_archive_dir=self.tgz_archive_dir,
      )
    except DayCloseYieldError as exc:
      return self._handle_day_close_yield(tar_path, exc)
    drop_legacy_gz_if_equivalent_to_zst(
        gz_path, zst_path, log_fn=self.log_fn, zst_members=zst_members,
    )
    if os.path.isfile(zst_path) or os.path.isfile(gz_path):
      self.log_fn(
          "janitor: day_close seal done tar=%s" % tar_path,
          flush=True,
      )
      with self._hints_state_lock:
        self._day_phases[tar_path] = day_phase_hint_entry(tar_path, "sealed")
      return True
    self._enqueue_day_close(tar_path, persist=False)
    self._persist_hints()
    return False

  def _tar_drop_one_day(
      self,
      tar_path: str,
      validation_cache,
      disqualified: Set[str],
  ) -> bool:
    zst_path, gz_path = compressed_sibling_paths(tar_path)
    sealed_ok = os.path.isfile(zst_path) or os.path.isfile(gz_path)
    # Delete-path may already have unlinked .tar; sealed-only → complete.
    if not os.path.isfile(tar_path) and sealed_ok:
      with self._hints_state_lock:
        self._day_phases[tar_path] = day_phase_hint_entry(tar_path, "tar_dropped")
      return True
    remaining_raw_by_gz = self._blocking_remaining_raw_for_tar(tar_path)
    if remaining_raw_by_gz_has_paths_on_disk(remaining_raw_by_gz, zst_path):
      handoff_paths_n = 0
      defer_reason = "remaining_raw_on_disk"
      coord = self.day_raw_removal_coordinator
      if coord is not None:
        try:
          handoff_paths = coord.paths_for_closed_raw_handoff_requeue(tar_path) or ()
          handoff_paths_n = len(handoff_paths)
          delete_done_fn = getattr(coord, "delete_phase_done", None)
          phase_done = (
              bool(delete_done_fn(tar_path))
              if callable(delete_done_fn)
              else False
          )
          if handoff_paths_n > 0 and phase_done:
            defer_reason = "stale_manifest_retryable"
          elif handoff_paths_n > 0:
            defer_reason = "waiting_on_ingest"
        except Exception:
          handoff_paths_n = 0
      self.log_fn(
          "Janitor tar drop deferred (raw stats still present for day): %s "
          "handoff_paths=%d reason=%s"
          % (tar_path, handoff_paths_n, defer_reason),
          flush=True,
      )
      self._enqueue_day_close(tar_path, persist=False)
      self._persist_hints()
      return False
    defer, _reason = self._check_day_close_defer(
        tar_path,
        phase="tar_drop",
        disqualified=disqualified,
    )
    if defer:
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
      self._enqueue_day_close(tar_path, persist=False)
      self._persist_hints()
      return False
    if not tar_existed or not os.path.isfile(tar_path):
      with self._hints_state_lock:
        self._day_phases[tar_path] = day_phase_hint_entry(tar_path, "tar_dropped")
      return True
    return False

  def shutdown(self, wait: bool = True):
    self._session_executor.shutdown(wait=wait)
    if self._day_close_pool is not None:
      self._day_close_pool.shutdown(wait=wait)
      self._day_close_pool = None
      self._day_close_pool_size = 0
    with self._day_close_in_flight_lock:
      self._day_close_in_flight.clear()

  def tick_defer_reason(self) -> Optional[str]:
    """Best-effort reason when debt exists but no tick has completed yet."""
    if self._tick_depth > 0:
      return "tick_in_progress"
    with self._maintenance_pass_lock:
      if self._pending_maintenance_pass_reason:
        return "startup_heavy_maintenance"
    if self.debt_depth() <= 0:
      return None
    coord = self.day_raw_removal_coordinator
    if coord is not None:
      for tar_norm in self._debt_heap_tar_paths():
        if not coord.pre_seal_verification_complete(tar_norm):
          return "pre_seal_verify_in_progress"
    return "debt_pending"

  def stats(self) -> Dict[str, Any]:
    defer_reason = self.tick_defer_reason()
    return {
        "janitor_debt_depth": self.debt_depth(),
        "janitor_ticks_completed": self._ticks_completed,
        "janitor_budget_throttled": self._budget_throttled_count,
        "janitor_tick_defer_reason": defer_reason,
    }
