"""Per-day post-seal raw removal: async verify manifest, gated batch delete."""
from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Any, Callable, Dict, List, Optional, Set

import hpcperfstats.conf_parser as cfg
from django.db import close_old_connections

from hpcperfstats.dbload.archive_compress import compressed_sibling_paths
from hpcperfstats.dbload.sync_timedb_archive_helpers import (
    build_remaining_raw_for_daily_tar,
    calendar_date_from_daily_tar_path,
    classify_removable_raw_paths_for_daily_gz,
    remove_verified_uncompressed_daily_tars,
    stats_file_is_active_segment,
)
from hpcperfstats.file_locking import file_write_lock
from hpcperfstats.process_title import set_daemon_thread_title
from hpcperfstats.shutdown_utils import shutdown_requested

MANIFEST_VERSION = 1
MANIFEST_SUBDIR = ".sync_timedb_day_raw_removal"

PHASE_VERIFYING = "verifying"
PHASE_VERIFICATION_COMPLETE = "verification_complete"
PHASE_DELETING = "deleting"
PHASE_DONE = "done"


def day_removal_manifest_dir(archive_data_dir: str) -> str:
  return os.path.join(archive_data_dir, MANIFEST_SUBDIR)


def day_removal_manifest_path(archive_data_dir: str, day_date: date) -> str:
  return os.path.join(
      day_removal_manifest_dir(archive_data_dir),
      "%s.json" % day_date.isoformat(),
  )


def _path_fingerprint(path: str) -> Optional[Dict[str, int]]:
  try:
    st = os.stat(path)
    return {"mtime": int(st.st_mtime_ns), "size": int(st.st_size)}
  except OSError:
    return None


def _new_manifest(tar_path: str) -> Dict[str, Any]:
  return {
      "version": MANIFEST_VERSION,
      "tar_path": os.path.normpath(tar_path),
      "phase": PHASE_VERIFYING,
      "started_at": time.time(),
      "completed_at": None,
      "verified_count": 0,
      "skipped_count": 0,
      "deleted_count": 0,
      "entries": {},
  }


def _load_manifest(path: str, tar_path: str) -> Dict[str, Any]:
  try:
    with open(path, encoding="utf-8") as handle:
      payload = json.load(handle)
  except (OSError, json.JSONDecodeError, TypeError, ValueError):
    return _new_manifest(tar_path)
  if not isinstance(payload, dict):
    return _new_manifest(tar_path)
  payload.setdefault("version", MANIFEST_VERSION)
  payload.setdefault("entries", {})
  payload.setdefault("tar_path", os.path.normpath(tar_path))
  return payload


def _save_manifest(path: str, payload: Dict[str, Any]) -> None:
  parent = os.path.dirname(path)
  try:
    os.makedirs(parent, exist_ok=True)
  except OSError:
    pass
  tmp_path = "%s.tmp" % path
  try:
    with open(tmp_path, "w", encoding="utf-8") as handle:
      json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
    os.replace(tmp_path, path)
  except OSError:
    try:
      if os.path.exists(tmp_path):
        os.remove(tmp_path)
    except OSError:
      pass


def _entry_fingerprint(entry: Dict[str, Any]) -> Optional[Dict[str, int]]:
  if "mtime" not in entry or "size" not in entry:
    return None
  return {"mtime": int(entry["mtime"]), "size": int(entry["size"])}


class _DayRawRemovalState:
  """Per-calendar-day verify/delete state backed by a JSON manifest."""

  def __init__(
      self,
      *,
      tar_path: str,
      archive_data_dir: str,
      host_name_ext: str,
      tgz_archive_dir: str,
      log_fn,
      get_quarantine_skip_paths: Callable[[], Set[str]],
      ingest_ready_fn: Optional[Callable[[str], bool]] = None,
  ):
    self.tar_path = os.path.normpath(tar_path)
    self.archive_data_dir = archive_data_dir
    self.host_name_ext = host_name_ext
    self.tgz_archive_dir = tgz_archive_dir
    self.log_fn = log_fn
    self.get_quarantine_skip_paths = get_quarantine_skip_paths
    self.ingest_ready_fn = ingest_ready_fn
    day_date = calendar_date_from_daily_tar_path(self.tar_path)
    if day_date is None:
      raise ValueError("invalid daily tar path: %s" % tar_path)
    self.day_date = day_date
    self._manifest_path = day_removal_manifest_path(archive_data_dir, day_date)
    self._lock = threading.Lock()
    self._manifest = _load_manifest(self._manifest_path, self.tar_path)
    self._validation_cache = {"hits": 0, "misses": 0}
    self._verify_future = None

  def phase(self) -> str:
    with self._lock:
      return str(self._manifest.get("phase") or PHASE_VERIFYING)

  def verification_complete(self) -> bool:
    return self.phase() in (
        PHASE_VERIFICATION_COMPLETE,
        PHASE_DELETING,
        PHASE_DONE,
    )

  def needs_delete_phase(self) -> bool:
    return self.phase() in (PHASE_VERIFICATION_COMPLETE, PHASE_DELETING)

  def delete_phase_done(self) -> bool:
    return self.phase() == PHASE_DONE

  def paths_pending_delete(self) -> Set[str]:
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

  def begin_deleting(self) -> None:
    with self._lock:
      if self._manifest.get("phase") == PHASE_VERIFICATION_COMPLETE:
        self._manifest["phase"] = PHASE_DELETING
        _save_manifest(self._manifest_path, self._manifest)

  def _record_entry(self, path: str, daily_gz: str, status: str, reason: str) -> None:
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

  def _verify_body(self) -> None:
    close_old_connections()
    with self._lock:
      phase = str(self._manifest.get("phase") or "")
      if phase in (PHASE_DONE, PHASE_VERIFICATION_COMPLETE, PHASE_DELETING):
        return
      self._manifest["phase"] = PHASE_VERIFYING
      if not self._manifest.get("started_at"):
        self._manifest["started_at"] = time.time()
    zst_path, _gz_path = compressed_sibling_paths(self.tar_path)
    remaining = build_remaining_raw_for_daily_tar(
        self.archive_data_dir,
        self.host_name_ext,
        self.tgz_archive_dir,
        self.tar_path,
    )
    raw_paths: List[str] = []
    for paths in (remaining or {}).values():
      raw_paths.extend(paths or [])
    skip_paths = set(self.get_quarantine_skip_paths() or ())
    filtered = []
    for path in raw_paths:
      if stats_file_is_active_segment(path):
        self._record_entry(path, zst_path, "skipped_active_segment", "active_segment")
        continue
      if path in skip_paths:
        self._record_entry(path, zst_path, "skipped_quarantine", "quarantine")
        continue
      filtered.append(path)
    gate_fn = (
        self.ingest_ready_fn
        if cfg.get_sync_archive_require_db_head_ingest()
        else None
    )
    if filtered:
      for path, status, reason in classify_removable_raw_paths_for_daily_gz(
          zst_path,
          filtered,
          ingest_ready_fn=gate_fn,
          allow_auto_seal=False,
          log_fn=self.log_fn,
          validation_cache=self._validation_cache,
      ):
        self._record_entry(path, zst_path, status, reason)
    with self._lock:
      self._manifest["phase"] = PHASE_VERIFICATION_COMPLETE
      _save_manifest(self._manifest_path, self._manifest)
    if self.log_fn:
      self.log_fn(
          "Day raw removal verify complete day=%s verified=%d skipped=%d"
          % (
              self.day_date.isoformat(),
              int(self._manifest.get("verified_count", 0)),
              int(self._manifest.get("skipped_count", 0)),
          ),
          flush=True,
      )

  def apply_batch_delete(self) -> int:
    max_deletes = cfg.get_sync_day_close_raw_removal_max_deletes_per_pass()
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
            "removing stats file (day raw removal preflight): " + path,
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
      raw_delete_complete = not remaining
    if raw_delete_complete:
      remaining_raw = build_remaining_raw_for_daily_tar(
          self.archive_data_dir,
          self.host_name_ext,
          self.tgz_archive_dir,
          self.tar_path,
      )
      remove_verified_uncompressed_daily_tars(
          self.tgz_archive_dir,
          log_fn=self.log_fn,
          remaining_raw_by_gz=remaining_raw,
          force_remove_uncompressed_tar=False,
          only_daily_tar_paths={self.tar_path},
      )
      with self._lock:
        self._manifest["phase"] = PHASE_DONE
        self._manifest["completed_at"] = time.time()
        _save_manifest(self._manifest_path, self._manifest)
      if self.log_fn:
        self.log_fn(
            "Day raw removal delete complete day=%s deleted=%d"
            % (self.day_date.isoformat(), int(self._manifest.get("deleted_count", 0))),
            flush=True,
        )
    else:
      with self._lock:
        _save_manifest(self._manifest_path, self._manifest)
    return deleted


class DayRawRemovalCoordinator:
  """Registry of per-day verify/delete state machines."""

  def __init__(
      self,
      *,
      archive_data_dir: str,
      host_name_ext: str,
      tgz_archive_dir: str,
      log_fn,
      get_quarantine_skip_paths: Callable[[], Set[str]],
      ingest_ready_fn: Optional[Callable[[str], bool]] = None,
      process_title: str = "sync_timedb.py",
  ):
    self.archive_data_dir = archive_data_dir
    self.host_name_ext = host_name_ext
    self.tgz_archive_dir = tgz_archive_dir
    self.log_fn = log_fn
    self.get_quarantine_skip_paths = get_quarantine_skip_paths
    self.ingest_ready_fn = ingest_ready_fn
    self.process_title = process_title
    self.enabled = cfg.get_sync_day_close_raw_removal_preflight()
    self._days: Dict[str, _DayRawRemovalState] = {}
    self._days_lock = threading.Lock()
    self._executor: Optional[ThreadPoolExecutor] = None

  def _get_or_create_day(self, tar_path: str) -> _DayRawRemovalState:
    tar_norm = os.path.normpath(tar_path)
    with self._days_lock:
      state = self._days.get(tar_norm)
      if state is not None:
        return state
      state = _DayRawRemovalState(
          tar_path=tar_norm,
          archive_data_dir=self.archive_data_dir,
          host_name_ext=self.host_name_ext,
          tgz_archive_dir=self.tgz_archive_dir,
          log_fn=self.log_fn,
          get_quarantine_skip_paths=self.get_quarantine_skip_paths,
          ingest_ready_fn=self.ingest_ready_fn,
      )
      self._days[tar_norm] = state
      return state

  def phase(self, tar_path: str) -> str:
    return self._get_or_create_day(tar_path).phase()

  def verification_complete(self, tar_path: str) -> bool:
    return self._get_or_create_day(tar_path).verification_complete()

  def needs_delete_phase(self, tar_path: str) -> bool:
    return self._get_or_create_day(tar_path).needs_delete_phase()

  def delete_phase_done(self, tar_path: str) -> bool:
    return self._get_or_create_day(tar_path).delete_phase_done()

  def paths_pending_delete(self) -> Set[str]:
    pending: Set[str] = set()
    with self._days_lock:
      states = list(self._days.values())
    for state in states:
      pending |= state.paths_pending_delete()
    return pending

  def consumed_paths(self) -> Set[str]:
    removed: Set[str] = set()
    with self._days_lock:
      states = list(self._days.values())
    for state in states:
      removed |= state.consumed_paths()
    return removed

  def any_needs_delete_phase(self) -> bool:
    with self._days_lock:
      states = list(self._days.values())
    return any(s.needs_delete_phase() and not s.delete_phase_done() for s in states)

  def oldest_day_needing_delete(self) -> Optional[str]:
    candidates = []
    with self._days_lock:
      states = list(self._days.values())
    for state in states:
      if state.needs_delete_phase() and not state.delete_phase_done():
        candidates.append((state.day_date, state.tar_path))
    if not candidates:
      return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]

  def start_async_verify(self, tar_path: str) -> None:
    if not self.enabled:
      return
    state = self._get_or_create_day(tar_path)
    if state.verification_complete():
      return
    if self._executor is None:
      self._executor = ThreadPoolExecutor(max_workers=1)
    if state._verify_future is not None and not state._verify_future.done():
      return

    def _run():
      set_daemon_thread_title(
          "",
          script_name=self.process_title,
          role="day-raw-removal-preflight",
      )
      try:
        state._verify_body()
      finally:
        close_old_connections()

    state._verify_future = self._executor.submit(_run)

  def begin_deleting(self, tar_path: str) -> None:
    self._get_or_create_day(tar_path).begin_deleting()

  def apply_batch_delete(self, tar_path: str) -> int:
    return self._get_or_create_day(tar_path).apply_batch_delete()

  def shutdown(self, wait: bool = True) -> None:
    executor = self._executor
    if executor is None:
      return
    executor.shutdown(wait=wait)
