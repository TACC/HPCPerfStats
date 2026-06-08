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
    build_remaining_raw_for_daily_tar,
    build_remaining_raw_stats_by_daily_gz,
    calendar_date_from_daily_tar_path,
    collect_days_with_unmapped_closed_raw,
    daily_gz_has_remaining_raw_stats,
    daily_tar_path_from_compressed,
    daily_tar_seal_calendar_eligible,
    dedupe_tar_keep_largest_file_per_member,
    drop_legacy_gz_if_equivalent_to_zst,
    effective_keep_uncompressed_tar,
    iter_daily_tar_paths,
    remove_verified_archived_raw_files,
    remove_verified_uncompressed_daily_tars,
    scan_and_quarantine_unparsable_closed_raw,
    should_seal_daily_tar,
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
    self.process_title = process_title

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
    self._last_accrual_at = 0.0
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

  def _run_unparsable_quarantine_scan(self):
    skip_paths = set(self.get_quarantine_skip_paths() or ())
    moved = scan_and_quarantine_unparsable_closed_raw(
        self.archive_data_dir,
        self.host_name_ext,
        skip_paths=skip_paths,
        log_fn=self.log_fn,
        max_per_pass=cfg.get_sync_unparsable_raw_quarantine_max_per_tick(),
    )
    if moved:
      self.log_fn(
          "Archive janitor quarantined unparsable_raw count=%d" % moved,
          flush=True,
      )
    return moved

  def enqueue_startup_debt(self):
    """Startup quarantine scan only; DAY_CLOSE scheduling is supervisor-driven."""
    self._run_unparsable_quarantine_scan()
    self.signal_work_available()

  def maybe_accrue_debt_if_due(self, interval_seconds: float) -> bool:
    elapsed = max(0.0, time.time() - float(self._last_accrual_at))
    if elapsed < float(interval_seconds):
      return False
    self._accrue_debt_full(reason="interval")
    self._last_accrual_at = time.time()
    return True

  def maybe_accrue_partial_debt_if_due(self, interval_seconds: float) -> bool:
    """Bounded prior-day RAW/TAR accrual while ingest backlog is non-empty."""
    elapsed = max(0.0, time.time() - float(self._last_accrual_at))
    if elapsed < float(interval_seconds):
      return False
    self._accrue_debt_partial_prior_days(reason="interval_partial")
    self._last_accrual_at = time.time()
    return True

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
    remaining = build_remaining_raw_stats_by_daily_gz(
        self.archive_data_dir,
        self.host_name_ext,
        self.tgz_archive_dir,
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
      return
    candidates.sort(key=lambda item: item[0])
    with self._debt_lock:
      for _, tar_norm in candidates:
        self._enqueue_day_close_locked(tar_norm, persist=False)
      self._trim_heap_to_max_entries_locked()
    self._persist_hints()
    self.log_fn(
        "Archive janitor scheduled day_close reason=%s days=%d debt_depth=%d"
        % (reason, len(candidates), self.debt_depth()),
        flush=True,
    )

  def _accrue_maintenance_extras(self):
    self._enqueue_debt_locked(
        DebtKind.LOCK_CLEANUP, _LOCK_CLEANUP_TAR_SENTINEL, persist=False)
    if self.tgz_archive_dir and os.path.isdir(self.tgz_archive_dir):
      for tar_path in iter_daily_tar_paths(self.tgz_archive_dir):
        if tar_has_duplicate_file_members(tar_path):
          self._enqueue_debt_locked(
              DebtKind.DEDUPE, os.path.normpath(tar_path), persist=False)

  def _accrue_debt_full(self, *, reason: str):
    """Full snapshot accrual at interval only (not per tick)."""
    if not self.tgz_archive_dir:
      return
    snapshot = build_archive_maintenance_snapshot(
        self.archive_data_dir,
        self.host_name_ext,
        self.tgz_archive_dir,
        log_fn=self.log_fn,
    )
    with self._accrual_snapshot_lock:
      self._accrual_snapshot = snapshot
    disqualified = set(self.get_disqualified_daily_tars())
    unmapped = collect_days_with_unmapped_closed_raw(
        snapshot.closed_paths, snapshot.mapping, self.tgz_archive_dir)
    disqualified |= set(unmapped or ())

    with self._debt_lock:
      self._accrue_maintenance_extras()
      self._trim_heap_to_max_entries_locked()

    self._persist_hints(
        paths_hint=snapshot_paths_hint_entries(
            snapshot.closed_paths,
            snapshot.first_timestamp_by_path,
            snapshot.head_identity_by_path,
        ),
        host_dirs_hint=snapshot_host_dirs_from_paths(snapshot.closed_paths),
    )
    self.log_fn(
        "Archive janitor accrue reason=%s debt_depth=%d (full snapshot)"
        % (reason, self.debt_depth()),
        flush=True,
    )

  def _accrue_debt_partial_prior_days(self, *, reason: str):
    """No-op: DAY_CLOSE is scheduled at startup and every N ingest chunks."""
    self.log_fn(
        "Archive janitor accrue reason=%s skipped (scheduled day_close only)"
        % reason,
        flush=True,
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
      self._run_unparsable_quarantine_scan()
      disqualified = set(self.get_disqualified_daily_tars())
      self._tick_remaining_raw_cache = {}
      with self._debt_lock:
        work_items = self._pop_eligible_debt_locked(disqualified, max_days)
      if not work_items:
        return

      validation_cache = {"hits": 0, "misses": 0}
      with self._accrual_snapshot_lock:
        snapshot = self._accrual_snapshot
      days_processed = 0
      tick_mutated = False

      for i, debt in enumerate(work_items):
        if time.time() - tick_t0 >= budget_s:
          self._budget_throttled_count += 1
          self._requeue_unprocessed_work(work_items, i)
          tick_mutated = True
          break

        try:
          disqualified = set(self.get_disqualified_daily_tars())
          if debt.tar_path in disqualified:
            self._enqueue_debt(debt.kind, debt.tar_path, persist=False)
            continue
          success = self._process_debt_item(
              debt,
              snapshot=snapshot,
              validation_cache=validation_cache,
              disqualified=disqualified,
          )
          processed_index = i + 1
          tick_mutated = True
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
          break

      if tick_mutated:
        self._persist_hints()

      self._ticks_completed += 1
      self.log_fn(
          "Archive janitor tick done days=%d debt_remaining=%d duration_s=%.3f"
          % (days_processed, self.debt_depth(), time.time() - tick_t0),
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
      if self._pending_signal and self._tick_depth == 1:
        self._pending_signal = False
        self.signal_work_available()
      self._tick_depth = max(0, self._tick_depth - 1)

  def _process_debt_item(
      self,
      debt: DayDebt,
      *,
      snapshot,
      validation_cache,
      disqualified: Set[str],
  ) -> bool:
    if debt.kind == DebtKind.DAY_CLOSE:
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
    if debt.kind == DebtKind.DEDUPE:
      self._dedupe_one_day(debt.tar_path)
      return True
    if debt.kind == DebtKind.LOCK_CLEANUP:
      cleanup_stale_fnctl_lock_sidecars(self.archive_data_dir)
      cleanup_stale_fnctl_lock_sidecars(self.tgz_archive_dir)
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
      if not self._seal_one_day(tar_norm, ignore_remaining_raw=True):
        return False
    if self._day_close_raw_removal_enabled():
      coord = self.day_raw_removal_coordinator
      coord.start_async_day_pipeline(tar_norm)
      if not coord.delete_phase_done(tar_norm):
        self._enqueue_day_close(tar_norm, persist=False)
        self._persist_hints()
        return False
      with self._hints_state_lock:
        self._day_phases[tar_norm] = day_phase_hint_entry(tar_norm, "tar_dropped")
      return True
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

  def _dedupe_one_day(self, tar_path: str):
    if not tar_has_duplicate_file_members(tar_path):
      return
    dedupe_tar_keep_largest_file_per_member(tar_path, log_fn=self.log_fn)

  def shutdown(self, wait: bool = True):
    self._executor.shutdown(wait=wait)

  def stats(self) -> Dict[str, Any]:
    return {
        "janitor_debt_depth": self.debt_depth(),
        "janitor_ticks_completed": self._ticks_completed,
        "janitor_budget_throttled": self._budget_throttled_count,
    }
