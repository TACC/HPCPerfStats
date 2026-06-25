"""Startup-only raw removal preflight: async verify, gated delete."""
from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Set

import hpcperfstats.dbload.lib.conf_parser as cfg
from django.db import close_old_connections

from hpcperfstats.dbload.lib.archive_compress import detect_compressed_format
from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
    build_archive_mapping,
    classify_removable_raw_paths_for_daily_gz,
    collect_stats_files_in_range,
    daily_tar_path_from_compressed,
    stats_file_is_active_segment,
)
from hpcperfstats.dbload.lib.file_locking import file_write_lock
from hpcperfstats.dbload.lib.process_title import set_daemon_thread_title
from hpcperfstats.dbload.lib.sync_timedb_persistence import (
    load_persistence_document,
    save_persistence_document,
)
from hpcperfstats.dbload.lib.shutdown_utils import shutdown_requested

from hpcperfstats.dbload.lib.sync_timedb_manifest_contract import (
    PHASE_DELETING,
    PHASE_DONE,
    PHASE_VERIFICATION_COMPLETE,
    PHASE_VERIFYING,
)

MANIFEST_BASENAME = ".sync_timedb_startup_raw_removal.json"
MANIFEST_VERSION = 1


def manifest_path(archive_data_dir: str) -> str:
  return os.path.join(archive_data_dir, MANIFEST_BASENAME)


def _path_fingerprint(path: str) -> Optional[Dict[str, int]]:
  try:
    st = os.stat(path)
    return {"mtime": int(st.st_mtime_ns), "size": int(st.st_size)}
  except OSError:
    return None


def _new_manifest() -> Dict[str, Any]:
  return {
      "version": MANIFEST_VERSION,
      "phase": PHASE_VERIFYING,
      "started_at": time.time(),
      "completed_at": None,
      "verified_count": 0,
      "skipped_count": 0,
      "deleted_count": 0,
      "pending_gz_paths": [],
      "entries": {},
  }


def _load_manifest(path: str) -> Dict[str, Any]:
  payload = load_persistence_document(path, "startup_raw_removal", default=None)
  if not isinstance(payload, dict):
    return _new_manifest()
  payload.setdefault("version", MANIFEST_VERSION)
  payload.setdefault("entries", {})
  payload.setdefault("pending_gz_paths", [])
  return payload


def _save_manifest(path: str, payload: Dict[str, Any]) -> None:
  save_persistence_document(path, "startup_raw_removal", payload)


def _entry_fingerprint(entry: Dict[str, Any]) -> Optional[Dict[str, int]]:
  if "mtime" not in entry or "size" not in entry:
    return None
  return {"mtime": int(entry["mtime"]), "size": int(entry["size"])}


class StartupRawRemovalPreflight:
  """Async verify-all-then-delete startup pass for sealed archived raw."""

  def __init__(
      self,
      *,
      archive_data_dir: str,
      host_name_ext: str,
      tgz_archive_dir: str,
      log_fn,
      get_disqualified_daily_tars: Callable[[], Set[str]],
      get_quarantine_skip_paths: Callable[[], Set[str]],
      ingest_ready_fn: Optional[Callable[[str], bool]] = None,
      get_startup_snapshot: Optional[Callable[[], Any]] = None,
      process_title: str = "sync_timedb.py",
  ):
    self.archive_data_dir = archive_data_dir
    self.host_name_ext = host_name_ext
    self.tgz_archive_dir = tgz_archive_dir
    self.log_fn = log_fn
    self.get_disqualified_daily_tars = get_disqualified_daily_tars
    self.get_quarantine_skip_paths = get_quarantine_skip_paths
    self.ingest_ready_fn = ingest_ready_fn
    self.get_startup_snapshot = get_startup_snapshot
    self.process_title = process_title
    self._manifest_path = manifest_path(archive_data_dir)
    self._lock = threading.Lock()
    self._manifest = _load_manifest(self._manifest_path)
    self._executor: Optional[ThreadPoolExecutor] = None
    self._verify_future = None
    self._validation_cache = {"hits": 0, "misses": 0}
    self.enabled = cfg.get_sync_startup_raw_removal_preflight()

  def phase(self) -> str:
    with self._lock:
      return str(self._manifest.get("phase") or PHASE_VERIFYING)

  def verification_complete(self) -> bool:
    phase = self.phase()
    return phase in (PHASE_VERIFICATION_COMPLETE, PHASE_DELETING, PHASE_DONE)

  def needs_delete_phase(self) -> bool:
    phase = self.phase()
    return phase in (PHASE_VERIFICATION_COMPLETE, PHASE_DELETING)

  def delete_phase_done(self) -> bool:
    return self.phase() == PHASE_DONE

  def paths_pending_startup_delete(self) -> Set[str]:
    """Verified paths not yet deleted; janitor/supervisor skip during delete window."""
    with self._lock:
      pending = set()
      for entry in self._manifest.get("entries", {}).values():
        if not isinstance(entry, dict):
          continue
        if entry.get("status") != "verified":
          continue
        if entry.get("deleted"):
          continue
        path = entry.get("path")
        if path:
          pending.add(path)
      return pending

  def consumed_paths(self) -> Set[str]:
    """Paths deleted by this preflight (for supervisor state pruning)."""
    with self._lock:
      removed = set()
      for entry in self._manifest.get("entries", {}).values():
        if not isinstance(entry, dict):
          continue
        if not entry.get("deleted"):
          continue
        path = entry.get("path")
        if path:
          removed.add(path)
      return removed

  def start_async_verify(self) -> None:
    if not self.enabled:
      return
    with self._lock:
      phase = str(self._manifest.get("phase") or "")
      if phase == PHASE_DONE:
        return
      if phase in ("", PHASE_VERIFYING):
        if not self._manifest.get("started_at"):
          self._manifest["started_at"] = time.time()
        self._manifest["phase"] = PHASE_VERIFYING
        _save_manifest(self._manifest_path, self._manifest)
      if self._executor is not None:
        return
      self._executor = ThreadPoolExecutor(max_workers=1)
      self._verify_future = self._executor.submit(self._verify_loop)

  def shutdown(self, wait: bool = True) -> None:
    executor = self._executor
    if executor is None:
      return
    executor.shutdown(wait=wait)

  def begin_deleting(self) -> None:
    with self._lock:
      if self._manifest.get("phase") == PHASE_VERIFICATION_COMPLETE:
        self._manifest["phase"] = PHASE_DELETING
        _save_manifest(self._manifest_path, self._manifest)

  def apply_deletes_from_manifest(self) -> int:
    """Delete verified manifest entries; return count deleted this call."""
    if not self.enabled:
      return 0
    max_deletes = cfg.get_sync_startup_raw_removal_max_deletes_per_pass()
    deleted = 0
    with self._lock:
      entries = self._manifest.get("entries", {})
      phase = str(self._manifest.get("phase") or "")
      if phase not in (PHASE_VERIFICATION_COMPLETE, PHASE_DELETING):
        return 0
      self._manifest["phase"] = PHASE_DELETING
    for path in sorted(entries.keys()):
      if max_deletes and deleted >= max_deletes:
        break
      with self._lock:
        entry = entries.get(path)
        if not isinstance(entry, dict):
          continue
        if entry.get("status") != "verified" or entry.get("deleted"):
          continue
      if not os.path.isfile(path):
        with self._lock:
          entry = entries.get(path)
          if isinstance(entry, dict):
            entry["deleted"] = True
            entry["delete_reason"] = "already_absent"
        deleted += 1
        continue
      fp = _path_fingerprint(path)
      with self._lock:
        entry = entries.get(path)
        if not isinstance(entry, dict):
          continue
        if _entry_fingerprint(entry) != fp:
          entry["status"] = "skipped_fingerprint_changed"
          entry["reason"] = "fingerprint_changed_before_delete"
          continue
      if self.log_fn:
        self.log_fn(
            "removing stats file (startup raw removal preflight): " + path,
            flush=True,
        )
      try:
        with file_write_lock(path):
          os.remove(path)
        with self._lock:
          entry = entries.get(path)
          if isinstance(entry, dict):
            entry["deleted"] = True
          self._manifest["deleted_count"] = int(
              self._manifest.get("deleted_count", 0)) + 1
        deleted += 1
      except OSError as exc:
        if self.log_fn:
          self.log_fn("Could not remove %s: %s" % (path, exc), flush=True)
        with self._lock:
          entry = entries.get(path)
          if isinstance(entry, dict):
            entry["delete_failed"] = str(exc)
    with self._lock:
      remaining = [
          path for path, entry in entries.items()
          if isinstance(entry, dict)
          and entry.get("status") == "verified"
          and not entry.get("deleted")
      ]
      if not remaining:
        self._manifest["phase"] = PHASE_DONE
        self._manifest["completed_at"] = time.time()
      _save_manifest(self._manifest_path, self._manifest)
    return deleted

  def _resolve_snapshot_mapping(self):
    getter = self.get_startup_snapshot
    if getter is not None:
      try:
        snapshot = getter()
      except Exception:
        snapshot = None
      if snapshot is not None:
        return snapshot.mapping, list(snapshot.closed_paths or ())
    closed_paths = collect_stats_files_in_range(
        self.archive_data_dir,
        "all",
        None,
        self.host_name_ext,
    )
    return build_archive_mapping(closed_paths, self.tgz_archive_dir), closed_paths

  def _discover_pending_gz_paths(self) -> List[str]:
    mapping, closed_paths = self._resolve_snapshot_mapping()
    if not closed_paths and not mapping:
      return []
    skip_paths = set(self.get_quarantine_skip_paths() or ())
    disqualified_tars = {
        os.path.normpath(p) for p in (self.get_disqualified_daily_tars() or ())
    }
    eligible_paths = []
    for path in closed_paths:
      if stats_file_is_active_segment(path):
        continue
      if path in skip_paths:
        continue
      eligible_paths.append(path)
    if not mapping:
      mapping = build_archive_mapping(eligible_paths, self.tgz_archive_dir)
    pending = []
    for gz_path, stats_paths in sorted(mapping.items()):
      if detect_compressed_format(gz_path) is None:
        continue
      tar_path = os.path.normpath(daily_tar_path_from_compressed(gz_path))
      if tar_path in disqualified_tars:
        for stats_path in stats_paths:
          self._record_entry(
              stats_path,
              gz_path,
              "skipped_disqualified_day",
              "disqualified_day",
          )
        continue
      if not stats_paths:
        continue
      pending.append(gz_path)
    return pending

  def _record_entry(
      self,
      path: str,
      daily_gz: str,
      status: str,
      reason: str,
  ) -> None:
    fp = _path_fingerprint(path)
    entry = {
        "path": path,
        "daily_gz": daily_gz,
        "status": status,
        "reason": reason,
        "deleted": False,
    }
    if fp is not None:
      entry.update(fp)
    with self._lock:
      entries = self._manifest.setdefault("entries", {})
      prior = entries.get(path)
      if isinstance(prior, dict):
        if prior.get("status") == "verified":
          return
        if prior.get("status") == status:
          return
      else:
        if status == "verified":
          self._manifest["verified_count"] = int(
              self._manifest.get("verified_count", 0)) + 1
        else:
          self._manifest["skipped_count"] = int(
              self._manifest.get("skipped_count", 0)) + 1
      entries[path] = entry

  def _verify_one_gz(self, gz_path: str, stats_paths: List[str]) -> None:
    skip_paths = set(self.get_quarantine_skip_paths() or ())
    filtered = []
    for path in stats_paths:
      if stats_file_is_active_segment(path):
        self._record_entry(path, gz_path, "skipped_active_segment", "active_segment")
        continue
      if path in skip_paths:
        self._record_entry(path, gz_path, "skipped_quarantine", "quarantine")
        continue
      filtered.append(path)
    if not filtered:
      return
    gate_fn = (
        self.ingest_ready_fn
        if cfg.get_sync_archive_require_db_head_ingest()
        else None
    )
    for path, status, reason in classify_removable_raw_paths_for_daily_gz(
        gz_path,
        filtered,
        ingest_ready_fn=gate_fn,
        allow_auto_seal=False,
        log_fn=self.log_fn,
        validation_cache=self._validation_cache,
    ):
      self._record_entry(path, gz_path, status, reason)

  def _verify_slice(self) -> bool:
    """Return True when verification is complete."""
    with self._lock:
      phase = str(self._manifest.get("phase") or "")
      if phase == PHASE_DONE:
        return True
      if phase in (PHASE_VERIFICATION_COMPLETE, PHASE_DELETING):
        return True
      pending = list(self._manifest.get("pending_gz_paths") or [])
    if not pending:
      pending = self._discover_pending_gz_paths()
      with self._lock:
        self._manifest["pending_gz_paths"] = pending
        _save_manifest(self._manifest_path, self._manifest)
      if not pending:
        with self._lock:
          self._manifest["phase"] = PHASE_VERIFICATION_COMPLETE
          _save_manifest(self._manifest_path, self._manifest)
        if self.log_fn:
          self.log_fn(
              "Startup raw removal preflight: no closed raw to verify",
              flush=True,
          )
        return True
    budget = cfg.get_sync_startup_raw_removal_verify_budget_seconds()
    days_per_slice = cfg.get_sync_startup_raw_removal_verify_days_per_slice()
    slice_started = time.time()
    processed_days = 0
    mapping, _closed_paths = self._resolve_snapshot_mapping()
    while pending and processed_days < days_per_slice:
      if shutdown_requested[0]:
        return False
      if time.time() - slice_started >= budget:
        break
      gz_path = pending.pop(0)
      self._verify_one_gz(gz_path, list(mapping.get(gz_path, [])))
      processed_days += 1
      with self._lock:
        self._manifest["pending_gz_paths"] = list(pending)
        _save_manifest(self._manifest_path, self._manifest)
    with self._lock:
      self._manifest["pending_gz_paths"] = pending
      if not pending:
        self._manifest["phase"] = PHASE_VERIFICATION_COMPLETE
        if self.log_fn:
          self.log_fn(
              "Startup raw removal preflight verification complete "
              "verified=%d skipped=%d"
              % (
                  int(self._manifest.get("verified_count", 0)),
                  int(self._manifest.get("skipped_count", 0)),
              ),
              flush=True,
          )
      _save_manifest(self._manifest_path, self._manifest)
    return not pending

  def _verify_loop(self) -> None:
    set_daemon_thread_title(
        "",
        script_name=self.process_title,
        role="startup-raw-removal-preflight",
    )
    close_old_connections()
    try:
      with self._lock:
        phase = str(self._manifest.get("phase") or "")
      if phase == PHASE_DONE:
        return
      if phase in (PHASE_VERIFICATION_COMPLETE, PHASE_DELETING):
        return
      while not shutdown_requested[0]:
        if self._verify_slice():
          break
        time.sleep(0.25)
    finally:
      close_old_connections()
