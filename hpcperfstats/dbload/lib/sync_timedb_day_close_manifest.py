"""Day-close manifest coordinator (historical module name: day_close_manifest).

On-disk artifact remains ``.sync_timedb_async_day_close.json``. Work runs on
janitor day-close worker threads, not an async worker pool owned by this module.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set

import hpcperfstats.dbload.lib.conf_parser as cfg
from hpcperfstats.dbload.lib.sync_timedb_persistence import (
    load_persistence_document,
    save_persistence_document,
)

MANIFEST_BASENAME = ".sync_timedb_async_day_close.json"
MANIFEST_VERSION = 1

_DAY_CLOSE_PIPELINE_PENDING_STATUSES = frozenset({
    "submitted",
    "queued",
    "sealing",
    "raw_removal",
    "deferred",
})
# Legacy statuses retained for stale-manifest recovery until operator on-disk
# sample confirms no remaining entries (plan P2 shrink — deferred).

_DAY_CLOSE_WORKER_SLOT_STATUSES = frozenset({
    "submitted",
    "queued",
    "sealing",
    "raw_removal",
})


def _is_day_close_pipeline_pending_entry(entry) -> bool:
  if not isinstance(entry, dict):
    return False
  return str(entry.get("status") or "") in _DAY_CLOSE_PIPELINE_PENDING_STATUSES


def _is_worker_slot_pending_entry(entry) -> bool:
  if not isinstance(entry, dict):
    return False
  return str(entry.get("status") or "") in _DAY_CLOSE_WORKER_SLOT_STATUSES


def _is_deferred_waiting_on_ingest_entry(entry) -> bool:
  if not isinstance(entry, dict):
    return False
  if str(entry.get("status") or "") != "deferred":
    return False
  detail = str(entry.get("detail") or "")
  return detail in ("waiting_on_ingest", "legacy_raw_delete_pending", "")


def manifest_path(archive_data_dir: str) -> str:
  return os.path.join(archive_data_dir, MANIFEST_BASENAME)


def _new_manifest() -> Dict[str, Any]:
  return {
      "version": MANIFEST_VERSION,
      "entries": {},
      "last_progress": "",
      "last_progress_at": None,
  }


def _load_manifest(path: str) -> Dict[str, Any]:
  payload = load_persistence_document(path, "day_close_manifest", default=None)
  if not isinstance(payload, dict):
    return _new_manifest()
  payload.setdefault("version", MANIFEST_VERSION)
  payload.setdefault("entries", {})
  return payload


def _save_manifest(path: str, payload: Dict[str, Any]) -> None:
  save_persistence_document(path, "day_close_manifest", payload)


class DayCloseManifestCoordinator:
  """Manifest + enqueue shim; ``DAY_CLOSE`` work runs on janitor worker threads."""

  def __init__(
      self,
      *,
      archive_data_dir: str,
      host_name_ext: str,
      tgz_archive_dir: str,
      local_tz,
      log_fn,
      get_disqualified_daily_tars: Callable[[], Set[str]],
      day_raw_removal_coordinator=None,
      on_day_phase: Optional[Callable[[str, str], None]] = None,
      submit_eligible_fn: Optional[Callable[[str], tuple]] = None,
      enqueue_day_close_fn: Optional[Callable[[str, str], bool]] = None,
      get_inflight_tar_paths_fn: Optional[Callable[[], Set[str]]] = None,
      process_title: str = "sync_timedb.py",
  ):
    self.archive_data_dir = archive_data_dir
    self.host_name_ext = host_name_ext
    self.tgz_archive_dir = tgz_archive_dir
    self.local_tz = local_tz
    self.log_fn = log_fn
    self.get_disqualified_daily_tars = get_disqualified_daily_tars
    self.day_raw_removal_coordinator = day_raw_removal_coordinator
    self.on_day_phase = on_day_phase
    self.submit_eligible_fn = submit_eligible_fn
    self.enqueue_day_close_fn = enqueue_day_close_fn
    self.get_inflight_tar_paths_fn = get_inflight_tar_paths_fn
    self.process_title = process_title
    self._manifest_path = manifest_path(archive_data_dir)
    self._lock = threading.Lock()
    self._manifest = _load_manifest(self._manifest_path)
    self._recover_stale_manifest_entries()

  def recover_stale_manifest_entries(self) -> None:
    self._recover_stale_manifest_entries()

  def _recover_stale_manifest_entries(self) -> None:
    stale_s = cfg.get_sync_day_close_manifest_stale_seconds()
    if stale_s <= 0:
      return
    now = time.time()
    recovered: list[str] = []
    downgraded: list[str] = []
    with self._lock:
      for tar_norm, entry in list(self._manifest.get("entries", {}).items()):
        if not isinstance(entry, dict):
          continue
        status = str(entry.get("status") or "")
        if status == "raw_delete_pending":
          entry["status"] = "deferred"
          entry["detail"] = "legacy_raw_delete_pending"
          entry["recovered_at"] = now
          recovered.append(os.path.normpath(tar_norm))
          continue
        if status not in ("submitted", "sealing", "raw_removal", "queued"):
          continue
        last_at = entry.get("last_progress_at") or entry.get("submitted_at")
        if last_at is None or now - float(last_at) < stale_s:
          continue
        tar_norm = os.path.normpath(tar_norm)
        entry["status"] = "deferred"
        entry["detail"] = "stale_manifest_recovery"
        entry["recovered_at"] = now
        recovered.append(tar_norm)
      for tar_norm, entry in list(self._manifest.get("entries", {}).items()):
        if not isinstance(entry, dict):
          continue
        if str(entry.get("status") or "") != "complete":
          continue
        tar_norm = os.path.normpath(tar_norm)
        if self._day_close_filesystem_complete(tar_norm):
          continue
        entry["status"] = "deferred"
        entry["detail"] = "stale_complete_filesystem_mismatch"
        entry["recovered_at"] = now
        downgraded.append(tar_norm)
      if recovered or downgraded:
        _save_manifest(self._manifest_path, self._manifest)
    for tar_norm in downgraded:
      self.log_fn(
          "janitor: day_close stale complete downgraded tar=%s" % tar_norm,
          flush=True,
      )
    for tar_norm in recovered:
      self.log_fn(
          "janitor: day_close stale manifest recovery tar=%s" % tar_norm,
          flush=True,
      )

  def reconcile_supervisor_raw_delete_pending(self, *, reason: str) -> int:
    """Legacy no-op; janitor owns delete on ``DAY_CLOSE`` debt."""
    return 0

  def entry_progress_snapshot(self, tar_path: str) -> Dict[str, Any]:
    tar_norm = os.path.normpath(tar_path or "")
    with self._lock:
      entry = self._manifest.get("entries", {}).get(tar_norm)
      if not isinstance(entry, dict):
        return {}
      last_at = entry.get("last_progress_at") or entry.get("submitted_at")
      age_s = None
      if last_at is not None:
        age_s = max(0.0, time.time() - float(last_at))
      return {
          "status": str(entry.get("status") or ""),
          "last_progress": str(entry.get("last_progress") or ""),
          "last_progress_age_s": age_s,
      }

  def shutdown(self, wait: bool = True) -> None:
    """Legacy no-op; day-close workers drain via janitor pool shutdown."""
    del wait
    return

  def _active_tar_paths_unlocked(self) -> Set[str]:
    """Caller must hold ``_lock`` when reading manifest entries."""
    active: Set[str] = set()
    if self.get_inflight_tar_paths_fn is not None:
      try:
        active |= set(self.get_inflight_tar_paths_fn() or ())
      except Exception:
        pass
    for tar_norm, entry in self._manifest.get("entries", {}).items():
      if _is_day_close_pipeline_pending_entry(entry):
        active.add(os.path.normpath(tar_norm))
    return active

  def _manifest_worker_slot_tar_paths_unlocked(self) -> Set[str]:
    """Manifest entries occupying a day-close worker slot (excludes deferred handoff)."""
    active: Set[str] = set()
    for tar_norm, entry in self._manifest.get("entries", {}).items():
      if _is_worker_slot_pending_entry(entry):
        active.add(os.path.normpath(tar_norm))
    return active

  def _deferred_waiting_on_ingest_tar_paths_unlocked(self) -> Set[str]:
    waiting: Set[str] = set()
    for tar_norm, entry in self._manifest.get("entries", {}).items():
      if _is_deferred_waiting_on_ingest_entry(entry):
        waiting.add(os.path.normpath(tar_norm))
    return waiting

  def active_or_submitted_tar_paths(self) -> Set[str]:
    with self._lock:
      return set(self._active_tar_paths_unlocked())

  def manifest_worker_slot_tar_paths(self) -> Set[str]:
    with self._lock:
      return set(self._manifest_worker_slot_tar_paths_unlocked())

  def deferred_waiting_on_ingest_tar_paths(self) -> Set[str]:
    with self._lock:
      return set(self._deferred_waiting_on_ingest_tar_paths_unlocked())

  def active_discover_cap_tar_paths(self, *, live_worker_tars=None) -> Set[str]:
    """Discover enqueue cap: live day-close workers + manifest worker slots only."""
    live_worker_tars = {
        os.path.normpath(t)
        for t in (live_worker_tars or ())
        if t
    }
    with self._lock:
      active = set(live_worker_tars)
      active |= self._manifest_worker_slot_tar_paths_unlocked()
    return active

  def active_worker_tar_paths(self, *, live_worker_tars=None) -> Set[str]:
    """Legacy worker occupancy metric (includes debt heap; excludes deferred)."""
    live_worker_tars = {
        os.path.normpath(t)
        for t in (live_worker_tars or ())
        if t
    }
    with self._lock:
      deferred = self._deferred_waiting_on_ingest_tar_paths_unlocked()
      active = set(live_worker_tars)
      if self.get_inflight_tar_paths_fn is not None:
        try:
          debt_heap = set(self.get_inflight_tar_paths_fn() or ())
        except Exception:
          debt_heap = set()
      else:
        debt_heap = set()
      active |= debt_heap - deferred
      active |= self._manifest_worker_slot_tar_paths_unlocked()
    return active

  def reconcile_manifest_with_debt_heap(
      self,
      *,
      debt_tar_paths: Set[str],
      live_worker_tars: Set[str],
  ) -> int:
    """Re-enqueue manifest worker slots with no heap debt and no live worker."""
    debt_tar_paths = {
        os.path.normpath(t) for t in (debt_tar_paths or ()) if t
    }
    live_worker_tars = {
        os.path.normpath(t) for t in (live_worker_tars or ()) if t
    }
    reenqueue: list[str] = []
    with self._lock:
      for tar_norm, entry in list(self._manifest.get("entries", {}).items()):
        if not isinstance(entry, dict):
          continue
        if not _is_worker_slot_pending_entry(entry):
          continue
        tar_norm = os.path.normpath(tar_norm)
        if tar_norm in debt_tar_paths or tar_norm in live_worker_tars:
          continue
        entry["status"] = "deferred"
        entry["detail"] = "ghost_manifest_reconcile"
        entry["recovered_at"] = time.time()
        reenqueue.append(tar_norm)
      if reenqueue:
        _save_manifest(self._manifest_path, self._manifest)
    for tar_norm in reenqueue:
      self.log_fn(
          "janitor: day_close ghost manifest reconcile tar=%s" % tar_norm,
          flush=True,
      )
      if self.enqueue_day_close_fn is not None:
        try:
          self.enqueue_day_close_fn(tar_norm, "ghost_manifest_reconcile")
        except Exception:
          pass
    return len(reenqueue)

  def discover_inflight_breakdown(self, *, live_worker_tars=None) -> Dict[str, int]:
    """Counts for janitor discover logging (debt heap vs deferred vs worker slots)."""
    live_worker_tars = {
        os.path.normpath(t)
        for t in (live_worker_tars or ())
        if t
    }
    with self._lock:
      deferred = self._deferred_waiting_on_ingest_tar_paths_unlocked()
      manifest_worker = self._manifest_worker_slot_tar_paths_unlocked()
      manifest_pending = sum(
          1
          for entry in self._manifest.get("entries", {}).values()
          if _is_day_close_pipeline_pending_entry(entry)
      )
      if self.get_inflight_tar_paths_fn is not None:
        try:
          debt_heap = set(self.get_inflight_tar_paths_fn() or ())
        except Exception:
          debt_heap = set()
      else:
        debt_heap = set()
    discover_cap = self.active_discover_cap_tar_paths(live_worker_tars=live_worker_tars)
    worker_occupancy = self.active_worker_tar_paths(live_worker_tars=live_worker_tars)
    return {
        "active_workers_n": len(live_worker_tars),
        "deferred_waiting_n": len(deferred),
        "debt_heap_n": len(debt_heap),
        "debt_heap_minus_deferred_n": len(debt_heap - deferred),
        "manifest_pending_n": manifest_pending,
        "manifest_worker_slot_n": len(manifest_worker),
        "discover_cap_n": len(discover_cap),
        "worker_occupancy_n": len(worker_occupancy),
    }

  def tar_paths_raw_delete_pending(self) -> List[str]:
    """Legacy no-op; janitor ``DayRawRemovalCoordinator`` owns delete pending state."""
    return []

  def _remaining_raw_for_tar_drop(self, tar_norm: str) -> Dict[str, List[str]]:
    coord = self.day_raw_removal_coordinator
    if coord is not None and bool(getattr(coord, "enabled", False)):
      return coord.remaining_raw_paths_blocking_tar_drop(tar_norm)
    from hpcperfstats.dbload.lib.sync_timedb_day_raw_removal import (
        remaining_raw_by_gz_blocking_tar_drop,
    )
    return remaining_raw_by_gz_blocking_tar_drop(
        tar_path=tar_norm,
        archive_data_dir=self.archive_data_dir,
        host_name_ext=self.host_name_ext,
        tgz_archive_dir=self.tgz_archive_dir,
        get_quarantine_skip_paths=lambda: set(),
        log_fn=None,
    )

  def _day_close_filesystem_complete(self, tar_norm: str) -> bool:
    from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
        day_close_filesystem_complete,
    )
    tar_norm = os.path.normpath(tar_norm or "")
    if not tar_norm:
      return False
    remaining = self._remaining_raw_for_tar_drop(tar_norm)
    return day_close_filesystem_complete(
        tar_norm,
        remaining_raw_by_gz=remaining,
        use_blocking_remaining=False,
    )

  def defer_for_ingest_handoff(self, tar_path: str) -> None:
    tar_norm = os.path.normpath(tar_path or "")
    if not tar_norm:
      return
    with self._lock:
      entry = self._manifest.get("entries", {}).get(tar_norm)
      if not isinstance(entry, dict):
        self._manifest.setdefault("entries", {})[tar_norm] = {
            "tar_path": tar_norm,
            "status": "deferred",
            "detail": "waiting_on_ingest",
            "submitted_at": time.time(),
        }
        _save_manifest(self._manifest_path, self._manifest)
        self._touch_manifest_locked("deferred_waiting_on_ingest", tar_norm=tar_norm)
        return
      status = str(entry.get("status") or "")
      if status not in _DAY_CLOSE_PIPELINE_PENDING_STATUSES:
        return
    self._set_entry_status(tar_norm, "deferred", detail="waiting_on_ingest")
    self._touch_manifest("deferred_waiting_on_ingest", tar_norm=tar_norm)

  def finalize_complete_if_filesystem(self, tar_path: str) -> bool:
    tar_norm = os.path.normpath(tar_path or "")
    if not tar_norm or not self._day_close_filesystem_complete(tar_norm):
      return False
    self._notify_phase(tar_norm, "tar_dropped")
    self._set_entry_status(
        tar_norm,
        "complete",
        completed_at=time.time(),
    )
    self._touch_manifest("complete", tar_norm=tar_norm)
    return True

  def is_complete(self, tar_path: str) -> bool:
    tar_norm = os.path.normpath(tar_path or "")
    with self._lock:
      entry = self._manifest.get("entries", {}).get(tar_norm)
      if not isinstance(entry, dict) or entry.get("status") != "complete":
        return False
    return self._day_close_filesystem_complete(tar_norm)

  def enqueue_day_close(
      self,
      tar_path: str,
      reason: str = "",
      *,
      disqualified_daily_tars=None,
  ) -> bool:
    """Enqueue janitor ``DAY_CLOSE`` debt for ``tar_path`` (single-flight per tar)."""
    ok, _reason = self.enqueue_day_close_result(
        tar_path,
        reason=reason,
        disqualified_daily_tars=disqualified_daily_tars,
    )
    return ok

  def enqueue_day_close_result(
      self,
      tar_path: str,
      reason: str = "",
      *,
      disqualified_daily_tars=None,
  ) -> tuple[bool, str]:
    """Like ``enqueue_day_close`` but returns ``(ok, reject_reason)``."""
    return self._enqueue_day_close_impl(
        tar_path,
        reason=reason,
        disqualified_daily_tars=disqualified_daily_tars,
    )

  def _enqueue_day_close_impl(
      self,
      tar_path: str,
      *,
      reason: str,
      disqualified_daily_tars=None,
  ) -> tuple[bool, str]:
    tar_norm = os.path.normpath(tar_path or "")
    if not tar_norm:
      return False, ""
    if self.is_complete(tar_norm):
      return True, "already_complete"
    if disqualified_daily_tars is None:
      disqualified = self.get_disqualified_daily_tars()
    else:
      disqualified = disqualified_daily_tars
    if tar_norm in disqualified:
      return False, "disqualified"
    if self.submit_eligible_fn is not None:
      try:
        eligible, skip_reason = self.submit_eligible_fn(tar_norm)
      except Exception:
        eligible, skip_reason = False, "enqueue_eligible_error"
      if not eligible:
        if skip_reason:
          self.log_fn(
              "janitor: day_close enqueue skip tar=%s reason=%s"
              % (tar_norm, skip_reason),
              flush=True,
          )
        return False, skip_reason or "submit_ineligible"
    inflight: Set[str] = set()
    if self.get_inflight_tar_paths_fn is not None:
      try:
        inflight = set(self.get_inflight_tar_paths_fn() or ())
      except Exception:
        inflight = set()
    with self._lock:
      entry = self._manifest.get("entries", {}).get(tar_norm)
      if _is_day_close_pipeline_pending_entry(entry):
        if tar_norm in inflight:
          return True, "already_inflight"
      elif tar_norm in inflight:
        return True, "already_inflight"
    enqueued = False
    if self.enqueue_day_close_fn is not None:
      try:
        enqueued = bool(self.enqueue_day_close_fn(tar_norm, reason))
      except Exception:
        enqueued = False
    if not enqueued:
      return False, "already_on_debt_heap"
    with self._lock:
      self._manifest.setdefault("entries", {})[tar_norm] = {
          "tar_path": tar_norm,
          "status": "queued",
          "reason": reason,
          "submitted_at": time.time(),
      }
      self._touch_manifest_locked("queued", tar_norm=tar_norm)
    self.log_fn(
        "janitor: day_close enqueue tar=%s reason=%s" % (tar_norm, reason),
        flush=True,
    )
    return True, reason

  def _touch_manifest_locked(self, stage: str, *, tar_norm: str = "") -> None:
    self._manifest["last_progress"] = stage
    self._manifest["last_progress_at"] = time.time()
    if tar_norm:
      entry = self._manifest.setdefault("entries", {}).setdefault(tar_norm, {})
      if isinstance(entry, dict):
        entry["last_progress"] = stage
        entry["last_progress_at"] = time.time()
    _save_manifest(self._manifest_path, self._manifest)

  def _touch_manifest(self, stage: str, *, tar_norm: str = "") -> None:
    with self._lock:
      self._touch_manifest_locked(stage, tar_norm=tar_norm)

  def _set_entry_status(self, tar_norm: str, status: str, **extra) -> None:
    with self._lock:
      entry = self._manifest.setdefault("entries", {}).setdefault(tar_norm, {})
      if not isinstance(entry, dict):
        entry = {}
        self._manifest["entries"][tar_norm] = entry
      entry["status"] = status
      entry.update(extra)
      _save_manifest(self._manifest_path, self._manifest)

  def _notify_phase(self, tar_norm: str, phase: str) -> None:
    if self.on_day_phase is not None:
      try:
        self.on_day_phase(tar_norm, phase)
      except Exception:
        pass

