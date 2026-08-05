"""
Day-close manifest coordinator (historical module name: day_close_manifest).

On-disk artifact remains ``.sync_timedb_async_day_close.json``. Work runs on
janitor day-close worker threads, not an async worker pool owned by this module.

Attributes:
  MANIFEST_BASENAME: Attribute.
  MANIFEST_VERSION: Attribute.
  _DAY_CLOSE_PIPELINE_PENDING_STATUSES: Attribute.
  _DAY_CLOSE_WORKER_SLOT_STATUSES: Attribute.
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


def _is_day_close_pipeline_pending_entry(entry: Any) -> bool:
  """
  Internal helper to check if day close pipeline pending entry.
  
  Args:
    entry (Any): Entry passed to this helper.
  
  Returns:
    bool: True or False for this check.
  
  Examples:
    >>> _is_day_close_pipeline_pending_entry(None)  # doctest: +SKIP
  """
  if not isinstance(entry, dict):
    return False
  return str(entry.get("status") or "") in _DAY_CLOSE_PIPELINE_PENDING_STATUSES


def _is_worker_slot_pending_entry(entry: Any) -> bool:
  """
  Internal helper to check if worker slot pending entry.
  
  Args:
    entry (Any): Entry passed to this helper.
  
  Returns:
    bool: True or False for this check.
  
  Examples:
    >>> _is_worker_slot_pending_entry(None)  # doctest: +SKIP
  """
  if not isinstance(entry, dict):
    return False
  return str(entry.get("status") or "") in _DAY_CLOSE_WORKER_SLOT_STATUSES


def _is_deferred_waiting_on_ingest_entry(entry: Any) -> bool:
  """
  True for deferred handoff soft-state (waiting / empty / legacy detail).
  
  Empty and ``legacy_raw_delete_pending`` details are treated as waiting so they
  never fake-succeed enqueue (discover cap / immediate blacklist). Clear via
  ``clear_deferred_waiting_on_ingest`` when handoff for that day drains.
  
  Args:
    entry (Any): Entry passed to this helper.
  
  Returns:
    bool: True or False for this check.
  
  Examples:
    >>> _is_deferred_waiting_on_ingest_entry(None)  # doctest: +SKIP
  """
  if not isinstance(entry, dict):
    return False
  if str(entry.get("status") or "") != "deferred":
    return False
  detail = str(entry.get("detail") or "")
  return detail in ("waiting_on_ingest", "legacy_raw_delete_pending", "")


def manifest_path(archive_data_dir: str) -> str:
  """
  Manifest path.
  
  Args:
    archive_data_dir (str): String for archive data dir.
  
  Returns:
    str: str produced by this call.
  
  Examples:
    >>> manifest_path("x")  # doctest: +SKIP
  """
  return os.path.join(archive_data_dir, MANIFEST_BASENAME)


def _new_manifest() -> Dict[str, Any]:
  """
  Internal helper to handle new manifest.
  
  Returns:
    Dict[str, Any]: Dict[str, Any] produced by this call.
  
  Examples:
    >>> _new_manifest()  # doctest: +SKIP
  """
  return {
      "version": MANIFEST_VERSION,
      "entries": {},
      "last_progress": "",
      "last_progress_at": None,
  }


def _load_manifest(path: str) -> Dict[str, Any]:
  """
  Internal helper to load the manifest.
  
  Args:
    path (str): String for path.
  
  Returns:
    Dict[str, Any]: Dict[str, Any] produced by this call.
  
  Examples:
    >>> _load_manifest("x")  # doctest: +SKIP
  """
  payload = load_persistence_document(path, "day_close_manifest", default=None)
  if not isinstance(payload, dict):
    return _new_manifest()
  payload.setdefault("version", MANIFEST_VERSION)
  payload.setdefault("entries", {})
  return payload


def _save_manifest(path: str, payload: Dict[str, Any]) -> None:
  """
  Internal helper to save the manifest.
  
  Args:
    path (str): String for path.
    payload (Dict[str, Any]): Mapping for payload.
  
  Returns:
    None
  
  Examples:
    >>> _save_manifest("x", {})  # doctest: +SKIP
  """
  save_persistence_document(path, "day_close_manifest", payload)


class DayCloseManifestCoordinator:
  """
  Manifest + enqueue shim; ``DAY_CLOSE`` work runs on janitor worker threads.
  
  Attributes:
    _lock: Attribute.
    _manifest: Attribute.
    _manifest_path: Attribute.
    archive_data_dir: Attribute.
    day_raw_removal_coordinator: Attribute.
    enqueue_day_close_fn: Attribute.
    get_disqualified_daily_tars: Attribute.
    get_inflight_tar_paths_fn: Attribute.
    host_name_ext: Attribute.
    local_tz: Attribute.
    log_fn: Attribute.
    on_day_phase: Attribute.
    process_title: Attribute.
    submit_eligible_fn: Attribute.
    tgz_archive_dir: Attribute.
  """

  def __init__(
    self,
    *,
    archive_data_dir: str,
    host_name_ext: str,
    tgz_archive_dir: str,
    local_tz: Any,
    log_fn: Any,
    get_disqualified_daily_tars: Callable[[], Set[str]],
    day_raw_removal_coordinator: Any | None = None,
    on_day_phase: Optional[Callable[[str, str], None]] = None,
    submit_eligible_fn: Optional[Callable[[str], tuple]] = None,
    enqueue_day_close_fn: Optional[Callable[[str, str], bool]] = None,
    get_inflight_tar_paths_fn: Optional[Callable[[], Set[str]]] = None,
    process_title: str = "sync_timedb.py",
  ) -> None:
    """
    Initialize a new instance.
    
    Args:
      archive_data_dir (str): String for archive data dir.
      host_name_ext (str): String for host name ext.
      tgz_archive_dir (str): String for tgz archive dir.
      local_tz (Any): Local tz passed to this helper.
      log_fn (Any): Callable invoked by this helper.
      get_disqualified_daily_tars (Callable[[], Set[str]]): Get disqualified
      daily tars.
      day_raw_removal_coordinator (Any | None): One of ``Any``, ``None``.
      on_day_phase (Optional[Callable[[str, str], None]]): On day phase, or
      None when absent.
      submit_eligible_fn (Optional[Callable[[str], tuple]]): Submit eligible
      fn, or None when absent.
      enqueue_day_close_fn (Optional[Callable[[str, str], bool]]): Enqueue day
      close fn, or None when absent.
      get_inflight_tar_paths_fn (Optional[Callable[[], Set[str]]]): Get
      inflight tar paths fn, or None when absent.
      process_title (str): String for process title.
    
    Returns:
      None
    
    Examples:
      >>> __init__(0)  # doctest: +SKIP
    """
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
    """
    Recover stale manifest entries.
    
    Returns:
      None
    
    Examples:
      >>> DayCloseManifestCoordinator().recover_stale_manifest_entries()
    """
    self._recover_stale_manifest_entries()

  def _recover_stale_manifest_entries(
    self,
    *,
    live_worker_tars: Any | None = None,
  ) -> None:
    """
    Internal helper to handle recover stale manifest entries.
    
    Args:
      live_worker_tars (Any | None): One of ``Any``, ``None``.
    
    Returns:
      None
    
    Examples:
      >>> DayCloseManifestCoordinator()._recover_stale_manifest_entries(None)
    """
    stale_s = cfg.get_sync_day_close_manifest_stale_seconds()
    if stale_s <= 0:
      return
    live_workers = {
        os.path.normpath(t)
        for t in (live_worker_tars or ())
        if t
    }
    now = time.time()
    recovered: list[str] = []
    worker_slot_recovered: list[str] = []
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
        tar_norm = os.path.normpath(tar_norm)
        # Live day-close workers own the day — do not demote on stale clock.
        if tar_norm in live_workers:
          continue
        last_at = entry.get("last_progress_at") or entry.get("submitted_at")
        if last_at is None or now - float(last_at) < stale_s:
          continue
        entry["status"] = "deferred"
        entry["detail"] = "stale_manifest_recovery"
        entry["recovered_at"] = now
        recovered.append(tar_norm)
        worker_slot_recovered.append(tar_norm)
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
    # Worker-slot stale rows must re-enter debt as queued (not limbo deferred).
    for tar_norm in worker_slot_recovered:
      enqueued = False
      if self.enqueue_day_close_fn is not None:
        try:
          enqueued = bool(
              self.enqueue_day_close_fn(tar_norm, "stale_manifest_recovery")
          )
        except Exception:
          enqueued = False
      on_heap = False
      if self.get_inflight_tar_paths_fn is not None:
        try:
          on_heap = tar_norm in set(self.get_inflight_tar_paths_fn() or ())
        except Exception:
          on_heap = False
      # Already-on-heap returns False from debt push; still restore queued.
      if not enqueued and not on_heap:
        continue
      with self._lock:
        entry = self._manifest.get("entries", {}).get(tar_norm)
        if not isinstance(entry, dict):
          entry = {"tar_path": tar_norm}
          self._manifest.setdefault("entries", {})[tar_norm] = entry
        entry["status"] = "queued"
        entry["reason"] = "stale_manifest_recovery"
        entry.pop("detail", None)
        entry["submitted_at"] = time.time()
        self._touch_manifest_locked("queued", tar_norm=tar_norm)

  def reconcile_supervisor_raw_delete_pending(self, *, reason: str) -> int:
    """
    Legacy no-op; janitor owns delete on ``DAY_CLOSE`` debt.
    
    Args:
      reason (str): String for reason.
    
    Returns:
      int: int produced by this call.
    
    Examples:
      >>> reconcile_supervisor_raw_delete_pending(0)  # doctest: +SKIP
    """
    return 0

  def entry_progress_snapshot(self, tar_path: str) -> Dict[str, Any]:
    """
    Entry progress snapshot.
    
    Args:
      tar_path (str): String for tar path.
    
    Returns:
      Dict[str, Any]: Dict[str, Any] produced by this call.
    
    Examples:
      >>> DayCloseManifestCoordinator().entry_progress_snapshot("x")
    """
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
    """
    Legacy no-op; day-close workers drain via janitor pool shutdown.
    
    Args:
      wait (bool): Boolean flag for wait.
    
    Returns:
      None
    
    Examples:
      >>> DayCloseManifestCoordinator().shutdown(True)  # doctest: +SKIP
    """
    del wait
    return

  def _active_tar_paths_unlocked(self) -> Set[str]:
    """
    Caller must hold ``_lock`` when reading manifest entries.
    
    Returns:
      Set[str]: Set[str] produced by this call.
    
    Examples:
      >>> DayCloseManifestCoordinator()._active_tar_paths_unlocked()
    """
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
    """
    Manifest entries occupying a day-close worker slot (excludes deferred.
    
      handoff).
    
    Returns:
      Set[str]: Set[str] produced by this call.
    
    Examples:
      >>> DayCloseManifestCoordinator()._manifest_worker_slot_tar_paths_unlocked()
    """
    active: Set[str] = set()
    for tar_norm, entry in self._manifest.get("entries", {}).items():
      if _is_worker_slot_pending_entry(entry):
        active.add(os.path.normpath(tar_norm))
    return active

  def _deferred_waiting_on_ingest_tar_paths_unlocked(self) -> Set[str]:
    """
    Internal helper to handle deferred waiting on ingest tar paths unlocked.
    
    Returns:
      Set[str]: Set[str] produced by this call.
    
    Examples:
      >>> _deferred_waiting_on_ingest_tar_paths_unlocked(0)  # doctest: +SKIP
    """
    waiting: Set[str] = set()
    for tar_norm, entry in self._manifest.get("entries", {}).items():
      if _is_deferred_waiting_on_ingest_entry(entry):
        waiting.add(os.path.normpath(tar_norm))
    return waiting

  def active_or_submitted_tar_paths(self) -> Set[str]:
    """
    Pipeline-active tars for stall diagnostics (excludes deferred waiting).
    
    Returns:
      Set[str]: Set[str] produced by this call.
    
    Examples:
      >>> DayCloseManifestCoordinator().active_or_submitted_tar_paths()
    """
    with self._lock:
      active = set(self._active_tar_paths_unlocked())
      active -= self._deferred_waiting_on_ingest_tar_paths_unlocked()
      return active

  def manifest_worker_slot_tar_paths(self) -> Set[str]:
    """
    Manifest worker slot tar paths.
    
    Returns:
      Set[str]: Set[str] produced by this call.
    
    Examples:
      >>> DayCloseManifestCoordinator().manifest_worker_slot_tar_paths()
    """
    with self._lock:
      return set(self._manifest_worker_slot_tar_paths_unlocked())

  def deferred_waiting_on_ingest_tar_paths(self) -> Set[str]:
    """
    Deferred waiting on ingest tar paths.
    
    Returns:
      Set[str]: Set[str] produced by this call.
    
    Examples:
      >>> DayCloseManifestCoordinator().deferred_waiting_on_ingest_tar_paths()
    """
    with self._lock:
      return set(self._deferred_waiting_on_ingest_tar_paths_unlocked())

  def active_discover_cap_tar_paths(
    self,
    *,
    live_worker_tars: Any | None = None,
  ) -> Set[str]:
    """
    Discover enqueue cap: live day-close workers + manifest worker slots only.
    
    Args:
      live_worker_tars (Any | None): One of ``Any``, ``None``.
    
    Returns:
      Set[str]: Set[str] produced by this call.
    
    Examples:
      >>> DayCloseManifestCoordinator().active_discover_cap_tar_paths(None)
    """
    live_worker_tars = {
        os.path.normpath(t)
        for t in (live_worker_tars or ())
        if t
    }
    with self._lock:
      active = set(live_worker_tars)
      active |= self._manifest_worker_slot_tar_paths_unlocked()
    return active

  def active_worker_tar_paths(
    self,
    *,
    live_worker_tars: Any | None = None,
  ) -> Set[str]:
    """
    Legacy worker occupancy metric (includes debt heap; excludes deferred).
    
    Args:
      live_worker_tars (Any | None): One of ``Any``, ``None``.
    
    Returns:
      Set[str]: Set[str] produced by this call.
    
    Examples:
      >>> DayCloseManifestCoordinator().active_worker_tar_paths(None)
    """
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
    """
    Re-enqueue manifest worker slots with no heap debt and no live worker.
    
    Args:
      debt_tar_paths (Set[str]): Sequence for debt tar paths.
      live_worker_tars (Set[str]): Sequence for live worker tars.
    
    Returns:
      int: int produced by this call.
    
    Examples:
      >>> DayCloseManifestCoordinator().reconcile_manifest_with_debt_heap([], [])
    """
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
      enqueued = False
      if self.enqueue_day_close_fn is not None:
        try:
          enqueued = bool(
              self.enqueue_day_close_fn(tar_norm, "ghost_manifest_reconcile")
          )
        except Exception:
          enqueued = False
      if not enqueued:
        # F17: do not leave sticky ghost limbo — drop so classify can retry.
        with self._lock:
          entries = self._manifest.setdefault("entries", {})
          entries.pop(tar_norm, None)
          _save_manifest(self._manifest_path, self._manifest)
        continue
      # Debt push succeeded: restore worker-slot queued (not limbo deferred).
      with self._lock:
        entry = self._manifest.get("entries", {}).get(tar_norm)
        if not isinstance(entry, dict):
          entry = {"tar_path": tar_norm}
          self._manifest.setdefault("entries", {})[tar_norm] = entry
        entry["status"] = "queued"
        entry["reason"] = "ghost_manifest_reconcile"
        entry.pop("detail", None)
        entry["submitted_at"] = time.time()
        self._touch_manifest_locked("queued", tar_norm=tar_norm)
    return len(reenqueue)

  def clear_deferred_waiting_on_ingest(self, tar_path: str) -> bool:
    """
    Drop deferred/waiting_on_ingest so classify can mark ready_for_enqueue.
    
    Args:
      tar_path (str): String for tar path.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> DayCloseManifestCoordinator().clear_deferred_waiting_on_ingest("x")
    """
    tar_norm = os.path.normpath(tar_path or "")
    if not tar_norm:
      return False
    with self._lock:
      entry = self._manifest.get("entries", {}).get(tar_norm)
      if not _is_deferred_waiting_on_ingest_entry(entry):
        return False
      entries = self._manifest.setdefault("entries", {})
      entries.pop(tar_norm, None)
      _save_manifest(self._manifest_path, self._manifest)
    self.log_fn(
        "janitor: day_close deferred cleared tar=%s" % tar_norm,
        flush=True,
    )
    return True

  def discover_inflight_breakdown(
    self,
    *,
    live_worker_tars: Any | None = None,
  ) -> Dict[str, int]:
    """
    Counts for janitor discover logging (debt heap vs deferred vs worker slots).
    
    Args:
      live_worker_tars (Any | None): One of ``Any``, ``None``.
    
    Returns:
      Dict[str, int]: Dict[str, int] produced by this call.
    
    Examples:
      >>> DayCloseManifestCoordinator().discover_inflight_breakdown(None)
    """
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
    """
    Legacy no-op; janitor ``DayRawRemovalCoordinator`` owns delete pending.
    
      state.
    
    Returns:
      List[str]: List[str] produced by this call.
    
    Examples:
      >>> DayCloseManifestCoordinator().tar_paths_raw_delete_pending()
    """
    return []

  def _remaining_raw_for_tar_drop(self, tar_norm: str) -> Dict[str, List[str]]:
    """
    Internal helper to handle remaining raw for tar drop.
    
    Args:
      tar_norm (str): String for tar norm.
    
    Returns:
      Dict[str, List[str]]: Dict[str, List[str]] produced by this call.
    
    Examples:
      >>> DayCloseManifestCoordinator()._remaining_raw_for_tar_drop("x")
    """
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
    """
    Internal helper to handle day close filesystem complete.
    
    Args:
      tar_norm (str): String for tar norm.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> DayCloseManifestCoordinator()._day_close_filesystem_complete("x")
    """
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
    """
    Defer for ingest handoff.
    
    Args:
      tar_path (str): String for tar path.
    
    Returns:
      None
    
    Examples:
      >>> DayCloseManifestCoordinator().defer_for_ingest_handoff("x")
    """
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

  def notify_day_phase(self, tar_path: str, phase: str) -> None:
    """
    Public phase notify (e.g. sealed) for supervisor re-prewarm hooks.
    
    Args:
      tar_path (str): String for tar path.
      phase (str): String for phase.
    
    Returns:
      None
    
    Examples:
      >>> DayCloseManifestCoordinator().notify_day_phase("x", "x")
    """
    tar_norm = os.path.normpath(tar_path or "")
    if tar_norm and phase:
      self._notify_phase(tar_norm, phase)

  def finalize_complete_if_filesystem(self, tar_path: str) -> bool:
    """
    Finalize complete if filesystem.
    
    Args:
      tar_path (str): String for tar path.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> DayCloseManifestCoordinator().finalize_complete_if_filesystem("x")
    """
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
    """
    Return True if complete.
    
    Args:
      tar_path (str): String for tar path.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> DayCloseManifestCoordinator().is_complete("x")  # doctest: +SKIP
    """
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
    disqualified_daily_tars: Any | None = None,
  ) -> bool:
    """
    Enqueue janitor ``DAY_CLOSE`` debt for ``tar_path`` (single-flight per tar).
    
    Args:
      tar_path (str): String for tar path.
      reason (str): String for reason.
      disqualified_daily_tars (Any | None): One of ``Any``, ``None``.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> DayCloseManifestCoordinator().enqueue_day_close("x", "x", None)
    """
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
    disqualified_daily_tars: Any | None = None,
  ) -> tuple[bool, str]:
    """
    Like ``enqueue_day_close`` but returns ``(ok, reject_reason)``.
    
    Args:
      tar_path (str): String for tar path.
      reason (str): String for reason.
      disqualified_daily_tars (Any | None): One of ``Any``, ``None``.
    
    Returns:
      tuple[bool, str]: tuple[bool, str] produced by this call.
    
    Examples:
      >>> DayCloseManifestCoordinator().enqueue_day_close_result("x", "x", None)
    """
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
    disqualified_daily_tars: Any | None = None,
  ) -> tuple[bool, str]:
    """
    Internal helper to handle enqueue day close impl.
    
    Args:
      tar_path (str): String for tar path.
      reason (str): String for reason.
      disqualified_daily_tars (Any | None): One of ``Any``, ``None``.
    
    Returns:
      tuple[bool, str]: tuple[bool, str] produced by this call.
    
    Examples:
      >>> DayCloseManifestCoordinator()._enqueue_day_close_impl("x", "x", None)
    """
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
        # Non-ingest deferred (stale/ghost): promote when already on the debt
        # heap; otherwise fall through to debt push + queued. Waiting-on-ingest
        # deferred stays deferred. Other pending statuses are idempotent only
        # when already on the debt heap (ghost queued without debt must re-push).
        if (
            str(entry.get("status") or "") == "deferred"
            and not _is_deferred_waiting_on_ingest_entry(entry)
        ):
          if tar_norm in inflight:
            entry["status"] = "queued"
            entry["reason"] = reason or "promoted_from_deferred"
            entry.pop("detail", None)
            entry["submitted_at"] = time.time()
            self._touch_manifest_locked("queued", tar_norm=tar_norm)
            self.log_fn(
                "janitor: day_close enqueue tar=%s reason=%s"
                % (tar_norm, reason or "promoted_from_deferred"),
                flush=True,
            )
            return True, reason or "promoted_from_deferred"
          # Not on heap yet — fall through to enqueue_day_close_fn.
        elif _is_deferred_waiting_on_ingest_entry(entry):
          # Soft-state only: not queued work. Discover must not treat as success.
          return False, "deferred_waiting_on_ingest"
        elif tar_norm in inflight:
          return True, "already_inflight"
        # Pending worker-slot without heap debt — fall through to re-push.
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
    """
    Internal helper to handle touch manifest locked.
    
    Args:
      stage (str): String for stage.
      tar_norm (str): String for tar norm.
    
    Returns:
      None
    
    Examples:
      >>> DayCloseManifestCoordinator()._touch_manifest_locked("x", "x")
    """
    self._manifest["last_progress"] = stage
    self._manifest["last_progress_at"] = time.time()
    if tar_norm:
      entry = self._manifest.setdefault("entries", {}).setdefault(tar_norm, {})
      if isinstance(entry, dict):
        entry["last_progress"] = stage
        entry["last_progress_at"] = time.time()
    _save_manifest(self._manifest_path, self._manifest)

  def _touch_manifest(self, stage: str, *, tar_norm: str = "") -> None:
    """
    Internal helper to handle touch manifest.
    
    Args:
      stage (str): String for stage.
      tar_norm (str): String for tar norm.
    
    Returns:
      None
    
    Examples:
      >>> DayCloseManifestCoordinator()._touch_manifest("x", "x")
    """
    with self._lock:
      self._touch_manifest_locked(stage, tar_norm=tar_norm)

  def touch_progress(self, stage: str, *, tar_path: str = "") -> None:
    """
    Heartbeat last_progress during long seal/verify/delete (stale recovery).
    
    Args:
      stage (str): String for stage.
      tar_path (str): String for tar path.
    
    Returns:
      None
    
    Examples:
      >>> DayCloseManifestCoordinator().touch_progress("x", "x")  # doctest: +SKIP
    """
    self._touch_manifest(stage, tar_norm=os.path.normpath(tar_path or ""))

  def _set_entry_status(self, tar_norm: str, status: str, **extra: Any) -> None:
    """
    Internal helper to set the entry status.
    
    Args:
      tar_norm (str): String for tar norm.
      status (str): String for status.
      **extra (Any): Extra keyword arguments (``extra``); keys are ``str`` and
      value types match the wrapped protocol for this helper.
    
    Returns:
      None
    
    Examples:
      >>> DayCloseManifestCoordinator()._set_entry_status("x", "x")
    """
    with self._lock:
      entry = self._manifest.setdefault("entries", {}).setdefault(tar_norm, {})
      if not isinstance(entry, dict):
        entry = {}
        self._manifest["entries"][tar_norm] = entry
      entry["status"] = status
      entry.update(extra)
      _save_manifest(self._manifest_path, self._manifest)

  def _notify_phase(self, tar_norm: str, phase: str) -> None:
    """
    Internal helper to handle notify phase.
    
    Args:
      tar_norm (str): String for tar norm.
      phase (str): String for phase.
    
    Returns:
      None
    
    Examples:
      >>> DayCloseManifestCoordinator()._notify_phase("x", "x")  # doctest: +SKIP
    """
    if self.on_day_phase is not None:
      try:
        self.on_day_phase(tar_norm, phase)
      except Exception:
        pass

