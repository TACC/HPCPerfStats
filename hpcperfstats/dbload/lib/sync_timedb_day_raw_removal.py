"""Per-day post-seal raw removal: async verify; ingest-thread batched delete."""
from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import hpcperfstats.dbload.lib.conf_parser as cfg
from django.db import close_old_connections

from hpcperfstats.dbload.lib.archive_compress import compressed_sibling_paths
from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
    build_remaining_raw_for_daily_tar,
    calendar_date_from_daily_tar_path,
    classify_removable_raw_paths_for_daily_gz,
    quarantine_dir_for_archive,
    remaining_raw_by_gz_has_paths_on_disk,
    remove_verified_uncompressed_daily_tars,
    stats_file_is_active_segment,
)
from hpcperfstats.dbload.lib.sync_timedb_persistence import (
    load_persistence_document,
    save_persistence_document,
)
from hpcperfstats.dbload.lib.file_locking import cleanup_orphan_fnctl_lock_sidecars, file_write_lock
from hpcperfstats.dbload.lib.process_title import set_daemon_thread_title
from hpcperfstats.dbload.lib.shutdown_utils import shutdown_requested

MANIFEST_VERSION = 1
MANIFEST_SUBDIR = ".sync_timedb_day_raw_removal"

from hpcperfstats.dbload.lib.sync_timedb_manifest_contract import (
    PHASE_DELETING,
    PHASE_DONE,
    PHASE_VERIFICATION_COMPLETE,
    PHASE_VERIFYING,
)

RETRYABLE_SKIP_REASONS = frozenset({
    "not_sample_ingested",
    "not_head_ingested",
    "not_in_sealed_archive",
    "size_mismatch",
})
RETRYABLE_SKIP_STATUSES = frozenset({
    "skipped_not_sample_ingested",
    "skipped_not_head_ingested",
    "skipped_not_in_archive",
    "skipped_size_mismatch",
})
QUARANTINE_SKIP_REASONS = frozenset({"quarantine"})
QUARANTINE_SKIP_STATUSES = frozenset({"skipped_quarantine"})
KICK_NO_HANDOFF_PROGRESS = frozenset({"noop", "quarantine_terminal"})


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
  payload = load_persistence_document(path, "day_raw_removal", default=None)
  if not isinstance(payload, dict):
    return _new_manifest(tar_path)
  payload.setdefault("version", MANIFEST_VERSION)
  payload.setdefault("entries", {})
  payload.setdefault("tar_path", os.path.normpath(tar_path))
  return payload


def _save_manifest(path: str, payload: Dict[str, Any]) -> None:
  save_persistence_document(path, "day_raw_removal", payload)


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
      get_maintenance_snapshot: Optional[Callable[[], Any]] = None,
  ):
    self.tar_path = os.path.normpath(tar_path)
    self.archive_data_dir = archive_data_dir
    self.host_name_ext = host_name_ext
    self.tgz_archive_dir = tgz_archive_dir
    self.log_fn = log_fn
    self.get_quarantine_skip_paths = get_quarantine_skip_paths
    self.ingest_ready_fn = ingest_ready_fn
    self.get_maintenance_snapshot = get_maintenance_snapshot
    day_date = calendar_date_from_daily_tar_path(self.tar_path)
    if day_date is None:
      raise ValueError("invalid daily tar path: %s" % tar_path)
    self.day_date = day_date
    self._manifest_path = day_removal_manifest_path(archive_data_dir, day_date)
    self._lock = threading.Lock()
    self._manifest = _load_manifest(self._manifest_path, self.tar_path)
    self._validation_cache = {"hits": 0, "misses": 0}
    self._pipeline_future = None
    self._verify_sealed_members = None

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

  def _resolve_maintenance_snapshot(self) -> Any:
    if self.get_maintenance_snapshot is None:
      return None
    try:
      return self.get_maintenance_snapshot()
    except Exception:
      return None

  def _build_remaining_raw_for_daily_tar(self) -> Dict[str, List[str]]:
    return build_remaining_raw_for_daily_tar(
        self.archive_data_dir,
        self.host_name_ext,
        self.tgz_archive_dir,
        self.tar_path,
        maintenance_snapshot=self._resolve_maintenance_snapshot(),
    )

  def _manifest_paths_on_disk(self) -> List[str]:
    return [path for path, entry in self._manifest_entries_on_disk()]

  def _closed_raw_paths_on_disk(self) -> List[str]:
    if self.delete_phase_done():
      blocking = self._blocking_manifest_paths_on_disk()
      if blocking:
        return blocking
      return []
    remaining = self._build_remaining_raw_for_daily_tar()
    paths: List[str] = []
    for raw_list in (remaining or {}).values():
      paths.extend(raw_list or [])
    return paths

  def _has_closed_raw_existing_on_disk(self) -> bool:
    if self._only_quarantine_terminal_on_disk():
      self._finalize_quarantine_terminal_done()
      return False
    if self.delete_phase_done():
      if self._blocking_manifest_paths_on_disk():
        return True
      if self._unmanifested_closed_raw_paths():
        return True
      if self._ghost_deleted_paths_on_disk():
        return False
      return False
    remaining = self._build_remaining_raw_for_daily_tar()
    zst_path, _gz_path = compressed_sibling_paths(self.tar_path)
    return remaining_raw_by_gz_has_paths_on_disk(remaining, zst_path)

  def _filter_accrual_paths_blocking_tar_drop(
      self, remaining: Dict[str, List[str]],
  ) -> Dict[str, List[str]]:
    if not remaining:
      return {}
    skip_paths = set(self.get_quarantine_skip_paths() or ())
    with self._lock:
      entries = dict(self._manifest.get("entries", {}))
    filtered: Dict[str, List[str]] = {}
    for gz_path, raw_list in remaining.items():
      blockers: List[str] = []
      for path in raw_list or []:
        if not os.path.isfile(path):
          continue
        if path in skip_paths:
          continue
        entry = entries.get(path)
        if entry is not None and self._entry_is_quarantine_terminal_skip(entry):
          continue
        blockers.append(path)
      if blockers:
        filtered[gz_path] = blockers
    return filtered

  def _remaining_raw_paths_blocking_tar_drop(self) -> Dict[str, List[str]]:
    """Accrual map whose on-disk paths block ``.tar`` unlink (manifest/quarantine-aware)."""
    if self._only_quarantine_terminal_on_disk():
      self._finalize_quarantine_terminal_done()
      return {}
    if self.delete_phase_done():
      blocking = self._blocking_manifest_paths_on_disk()
      if blocking:
        zst_path, _gz_path = compressed_sibling_paths(self.tar_path)
        return {zst_path: blocking}
      return {}
    return self._filter_accrual_paths_blocking_tar_drop(
        self._build_remaining_raw_for_daily_tar(),
    )

  def _count_quarantine_accrual_paths_on_disk(self) -> int:
    remaining = self._build_remaining_raw_for_daily_tar()
    skip_paths = set(self.get_quarantine_skip_paths() or ())
    with self._lock:
      entries = dict(self._manifest.get("entries", {}))
    count = 0
    for paths in (remaining or {}).values():
      for path in paths or []:
        if not os.path.isfile(path):
          continue
        if path in skip_paths:
          count += 1
          continue
        entry = entries.get(path)
        if entry is not None and self._entry_is_quarantine_terminal_skip(entry):
          count += 1
    return count

  def _log_tar_drop_skip(self, reason: str, *, sealed_ok: bool = True) -> None:
    if not self.log_fn:
      return
    blocking = self._remaining_raw_paths_blocking_tar_drop()
    remaining_n = sum(
        1
        for paths in (blocking or {}).values()
        for path in (paths or [])
        if os.path.isfile(path)
    )
    quarantine_n = self._count_quarantine_accrual_paths_on_disk()
    sealed = "ok" if sealed_ok else "missing"
    self.log_fn(
        "sync_timedb: tar_drop_skip day=%s reason=%s remaining_n=%d "
        "quarantine_n=%d sealed=%s validation=ok"
        % (
            self.day_date.isoformat(),
            reason,
            remaining_n,
            quarantine_n,
            sealed,
        ),
        flush=True,
    )

  def try_finish_tar_drop_if_ready(self) -> bool:
    """Drop ``.tar`` when sealed and no closed raw files remain on disk."""
    if not os.path.isfile(self.tar_path):
      return True
    zst_path, gz_path = compressed_sibling_paths(self.tar_path)
    if not (os.path.isfile(zst_path) or os.path.isfile(gz_path)):
      self._log_tar_drop_skip("sealed_missing", sealed_ok=False)
      return False
    remaining_raw = self._remaining_raw_paths_blocking_tar_drop()
    if remaining_raw_by_gz_has_paths_on_disk(remaining_raw, zst_path):
      self._log_tar_drop_skip("remaining_raw_on_disk")
      return False
    remove_verified_uncompressed_daily_tars(
        self.tgz_archive_dir,
        log_fn=self.log_fn,
        remaining_raw_by_gz=remaining_raw,
        force_remove_uncompressed_tar=False,
        only_daily_tar_paths={self.tar_path},
    )
    if os.path.isfile(self.tar_path):
      return False
    with self._lock:
      if self._manifest.get("phase") != PHASE_DONE:
        self._manifest["phase"] = PHASE_DONE
        self._manifest["completed_at"] = time.time()
        _save_manifest(self._manifest_path, self._manifest)
    if self.log_fn:
      self.log_fn(
          "Day raw removal tar drop complete day=%s"
          % self.day_date.isoformat(),
          flush=True,
      )
    return True

  def _entry_is_retryable_skip(self, entry: Dict[str, Any]) -> bool:
    if not isinstance(entry, dict):
      return False
    reason = str(entry.get("reason") or "")
    status = str(entry.get("status") or "")
    return (
        reason in RETRYABLE_SKIP_REASONS
        or status in RETRYABLE_SKIP_STATUSES
    )

  def _entry_is_quarantine_terminal_skip(self, entry: Dict[str, Any]) -> bool:
    if not isinstance(entry, dict):
      return False
    reason = str(entry.get("reason") or "")
    status = str(entry.get("status") or "")
    return (
        reason in QUARANTINE_SKIP_REASONS
        or status in QUARANTINE_SKIP_STATUSES
    )

  def _needs_retry_after_ingest(self) -> bool:
    if self.phase() != PHASE_DONE:
      return False
    with self._lock:
      entries = dict(self._manifest.get("entries", {}))
    for path in self._closed_raw_paths_on_disk():
      if not os.path.isfile(path):
        continue
      entry = entries.get(path)
      if entry is None or self._entry_is_retryable_skip(entry):
        return True
    return False

  def _reset_for_reverify(self) -> None:
    with self._lock:
      self._manifest = _new_manifest(self.tar_path)
      _save_manifest(self._manifest_path, self._manifest)

  def _all_closed_raw_terminal_or_gone(self) -> bool:
    with self._lock:
      entries = dict(self._manifest.get("entries", {}))
    for path in self._closed_raw_paths_on_disk():
      if not os.path.isfile(path):
        continue
      entry = entries.get(path)
      if entry is None:
        return False
      if self._entry_is_quarantine_terminal_skip(entry):
        continue
      if self._entry_is_retryable_skip(entry):
        return False
    return True

  def _unmanifested_closed_raw_paths(self) -> List[str]:
    with self._lock:
      entries = dict(self._manifest.get("entries", {}))
    if self.delete_phase_done():
      return []
    unmanifested: List[str] = []
    for path in self._closed_raw_paths_on_disk():
      if not os.path.isfile(path):
        continue
      if path not in entries:
        unmanifested.append(path)
    return unmanifested

  def _manifest_entries_on_disk(self) -> List[Tuple[str, Dict[str, Any]]]:
    with self._lock:
      entries = dict(self._manifest.get("entries", {}))
    on_disk: List[Tuple[str, Dict[str, Any]]] = []
    for path, entry in entries.items():
      if os.path.isfile(path) and isinstance(entry, dict):
        on_disk.append((path, entry))
    return on_disk

  def _entry_is_verified_ghost_on_disk(self, entry: Dict[str, Any]) -> bool:
    if not isinstance(entry, dict):
      return False
    return (
        entry.get("deleted") is True
        and str(entry.get("status") or "") == "verified"
    )

  def _manifest_verified_pending_count(self) -> int:
    """Manifest-only count of verified entries not yet marked deleted."""
    with self._lock:
      entries = self._manifest.get("entries", {})
    count = 0
    for entry in (entries or {}).values():
      if not isinstance(entry, dict):
        continue
      if str(entry.get("status") or "") != "verified":
        continue
      if entry.get("deleted"):
        continue
      count += 1
    return count

  def _manifest_has_ghost_markers(self) -> bool:
    """True when manifest marks verified paths deleted (ghost retry candidates)."""
    with self._lock:
      entries = self._manifest.get("entries", {})
    for entry in (entries or {}).values():
      if self._entry_is_verified_ghost_on_disk(entry):
        return True
    return False

  def _verified_pending_paths_on_disk(self) -> List[str]:
    """On-disk paths whose manifest entry is verified and not yet deleted."""
    with self._lock:
      entries = dict(self._manifest.get("entries", {}))
    pending: List[str] = []
    for path, entry in entries.items():
      if not isinstance(entry, dict):
        continue
      if str(entry.get("status") or "") != "verified":
        continue
      if entry.get("deleted"):
        continue
      if os.path.isfile(path):
        pending.append(path)
    return pending

  def _ghost_deleted_paths_on_disk(self) -> List[str]:
    if not self._manifest_has_ghost_markers():
      return []
    return [
        path
        for path, entry in self._manifest_entries_on_disk()
        if self._entry_is_verified_ghost_on_disk(entry)
    ]

  def needs_ghost_delete_retry(self) -> bool:
    if not self.delete_phase_done():
      return False
    if not self._manifest_has_ghost_markers():
      return False
    return bool(self._ghost_deleted_paths_on_disk())

  def needs_reopen_for_verified_pending(self) -> bool:
    """True when ``phase=done`` but verified manifest entries remain undeleted."""
    if self.phase() != PHASE_DONE:
      return False
    return self._manifest_verified_pending_count() > 0

  def _blocking_manifest_paths_on_disk(self) -> List[str]:
    return [
        path
        for path, entry in self._manifest_entries_on_disk()
        if not self._entry_is_verified_ghost_on_disk(entry)
        and not self._entry_is_quarantine_terminal_skip(entry)
    ]

  def _only_quarantine_terminal_on_disk(self) -> bool:
    on_disk = self._manifest_entries_on_disk()
    if not on_disk:
      return False
    for _path, entry in on_disk:
      if not self._entry_is_quarantine_terminal_skip(entry):
        return False
    return True

  def _finalize_quarantine_terminal_done(self) -> None:
    if self.delete_phase_done():
      return
    with self._lock:
      if self._manifest.get("phase") == PHASE_DONE:
        return
      self._manifest["phase"] = PHASE_DONE
      self._manifest["completed_at"] = time.time()
      _save_manifest(self._manifest_path, self._manifest)
    if self.log_fn:
      self.log_fn(
          "Day raw removal quarantine-terminal done day=%s on_disk=%d"
          % (
              self.day_date.isoformat(),
              len(self._manifest_entries_on_disk()),
          ),
          flush=True,
      )

  def _prepare_ghost_delete_retry(self) -> bool:
    ghosts = self._ghost_deleted_paths_on_disk()
    if not ghosts:
      return False
    with self._lock:
      for path in ghosts:
        entry = self._manifest.get("entries", {}).get(path)
        if isinstance(entry, dict):
          entry.pop("deleted", None)
          entry.pop("delete_failed", None)
          entry.pop("delete_reason", None)
      self._manifest["phase"] = PHASE_DELETING
      _save_manifest(self._manifest_path, self._manifest)
    if self.log_fn:
      self.log_fn(
          "Day raw removal ghost delete retry day=%s paths=%d"
          % (self.day_date.isoformat(), len(ghosts)),
          flush=True,
      )
    return True

  def _manifest_retryable_paths_on_disk(self) -> List[str]:
    return [
        path
        for path, entry in self._manifest_entries_on_disk()
        if self._entry_is_retryable_skip(entry)
    ]

  def _manifest_only_waiting_on_ingest(self) -> bool:
    on_disk = self._manifest_entries_on_disk()
    if not on_disk:
      return False
    for _path, entry in on_disk:
      if not self._entry_is_retryable_skip(entry):
        return False
    return True

  def _only_waiting_on_ingest_blocks_completion(self) -> bool:
    if self.delete_phase_done():
      return self._manifest_only_waiting_on_ingest()
    if self._unmanifested_closed_raw_paths():
      return False
    with self._lock:
      entries = dict(self._manifest.get("entries", {}))
    for path in self._closed_raw_paths_on_disk():
      if not os.path.isfile(path):
        continue
      entry = entries.get(path)
      if entry is None or not self._entry_is_retryable_skip(entry):
        return False
    return True

  def _async_verify_in_flight(self) -> bool:
    future = self._pipeline_future
    return future is not None and not future.done()

  def blocks_startup_drain(self) -> bool:
    if self.phase() == PHASE_DONE:
      if self.needs_reopen_for_verified_pending():
        return True
      if self._only_quarantine_terminal_on_disk():
        self._finalize_quarantine_terminal_done()
      return False
    if self._async_verify_in_flight():
      return True
    if self.phase() == PHASE_VERIFYING:
      return True
    if self.paths_pending_delete():
      if (
          self.phase() == PHASE_VERIFICATION_COMPLETE
          and not self._async_verify_in_flight()
      ):
        return False
      return True
    if self.phase() == PHASE_DELETING:
      return True
    return False

  def waiting_on_ingest_at_startup(self) -> bool:
    return (
        not self.delete_phase_done()
        and not self.blocks_startup_drain()
        and self.needs_delete_phase()
    )

  def handoff_paths_for_ingest(self) -> List[str]:
    """Closed raw on disk whose manifest entry is missing or retryable."""
    if self.delete_phase_done():
      return self._manifest_retryable_paths_on_disk()
    with self._lock:
      entries = dict(self._manifest.get("entries", {}))
    paths: List[str] = []
    for path in self._closed_raw_paths_on_disk():
      if not os.path.isfile(path):
        continue
      entry = entries.get(path)
      if entry is None or self._entry_is_retryable_skip(entry):
        paths.append(path)
    return paths

  def should_handoff_day_close_to_ingest(self) -> bool:
    if not self.verification_complete():
      return False
    if not self._only_waiting_on_ingest_blocks_completion():
      return False
    return bool(self.handoff_paths_for_ingest())

  def complete_handoff_to_ingest(self) -> List[str]:
    """Mark waiting-on-ingest done when needed; return paths for ingest requeue."""
    paths = self.handoff_paths_for_ingest()
    if not paths:
      return []
    if not self.delete_phase_done():
      self._mark_done_waiting_on_ingest()
    return list(paths)

  def _mark_done_waiting_on_ingest(self) -> None:
    retryable_count = 0
    with self._lock:
      entries = dict(self._manifest.get("entries", {}))
    for path in self._closed_raw_paths_on_disk():
      if not os.path.isfile(path):
        continue
      entry = entries.get(path)
      if isinstance(entry, dict) and self._entry_is_retryable_skip(entry):
        retryable_count += 1
    with self._lock:
      self._manifest["phase"] = PHASE_DONE
      self._manifest["completed_at"] = time.time()
      _save_manifest(self._manifest_path, self._manifest)
    if self.log_fn:
      self.log_fn(
          "Day raw removal deferring to done (waiting_on_ingest) day=%s retryable=%d"
          % (self.day_date.isoformat(), retryable_count),
          flush=True,
      )

  def progress_summary(self) -> Dict[str, Any]:
    with self._lock:
      entries = self._manifest.get("entries", {})
      pending_delete = 0
      for entry in entries.values():
        if not isinstance(entry, dict):
          continue
        if entry.get("status") == "verified" and not entry.get("deleted"):
          pending_delete += 1
      return {
          "phase": str(self._manifest.get("phase") or ""),
          "verified_count": int(self._manifest.get("verified_count", 0)),
          "pending_delete": pending_delete,
          "deleted_count": int(self._manifest.get("deleted_count", 0)),
      }

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

  def reopen_delete_phase_if_verified_on_disk(self) -> bool:
    """Reopen delete when ``phase=done`` but verified paths remain on disk."""
    if self.phase() != PHASE_DONE:
      return False
    pending = self._verified_pending_paths_on_disk()
    if not pending:
      return False
    with self._lock:
      self._manifest["phase"] = PHASE_DELETING
      _save_manifest(self._manifest_path, self._manifest)
    if self.log_fn:
      self.log_fn(
          "Day raw removal pending delete reopen day=%s paths=%d"
          % (self.day_date.isoformat(), len(pending)),
          flush=True,
      )
    return True

  def begin_deleting(self) -> None:
    pending_delete = self.paths_pending_delete()
    blocking = self._blocking_manifest_paths_on_disk()
    with self._lock:
      phase = self._manifest.get("phase")
      if phase == PHASE_VERIFICATION_COMPLETE:
        self._manifest["phase"] = PHASE_DELETING
        _save_manifest(self._manifest_path, self._manifest)
      elif phase == PHASE_DONE and (pending_delete or blocking):
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
    remaining = self._build_remaining_raw_for_daily_tar()
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
          sealed_members=self._verify_sealed_members,
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

  def _batch_delete_completion_context(self, entries):
    """Single completion snapshot after the delete loop (one pass per helper)."""
    remaining_verified = [
        path for path, entry in entries.items()
        if isinstance(entry, dict)
        and entry.get("status") == "verified"
        and not entry.get("deleted")
    ]
    raw_delete_complete = not remaining_verified
    all_terminal = False
    only_waiting = False
    has_closed_raw = False
    unmanifested: List[str] = []
    remaining_raw = None
    if raw_delete_complete:
      all_terminal = self._all_closed_raw_terminal_or_gone()
      if not all_terminal:
        only_waiting = self._only_waiting_on_ingest_blocks_completion()
        unmanifested = self._unmanifested_closed_raw_paths()
        if only_waiting:
          has_closed_raw = self._has_closed_raw_existing_on_disk()
      else:
        remaining_raw = self._remaining_raw_paths_blocking_tar_drop()
    return {
        "raw_delete_complete": raw_delete_complete,
        "all_closed_raw_terminal_or_gone": all_terminal,
        "only_waiting_on_ingest": only_waiting,
        "has_closed_raw_on_disk": has_closed_raw,
        "unmanifested_paths": unmanifested,
        "remaining_raw_by_gz": remaining_raw,
    }

  def apply_batch_delete(self) -> int:
    if self.needs_ghost_delete_retry():
      self._prepare_ghost_delete_retry()
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
      entries = self._manifest.get("entries", {})
    completion = self._batch_delete_completion_context(entries)
    if completion["raw_delete_complete"]:
      if not completion["all_closed_raw_terminal_or_gone"]:
        if (
            completion["only_waiting_on_ingest"]
            and completion["has_closed_raw_on_disk"]
        ):
          self._mark_done_waiting_on_ingest()
          return deleted
        if completion["unmanifested_paths"]:
          with self._lock:
            self._manifest["phase"] = PHASE_VERIFYING
            _save_manifest(self._manifest_path, self._manifest)
          return deleted
        with self._lock:
          self._manifest["phase"] = PHASE_VERIFICATION_COMPLETE
          _save_manifest(self._manifest_path, self._manifest)
        return deleted
      remove_verified_uncompressed_daily_tars(
          self.tgz_archive_dir,
          log_fn=self.log_fn,
          remaining_raw_by_gz=self._remaining_raw_paths_blocking_tar_drop(),
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


def day_raw_delete_safe_during_chunk(
    day_raw_removal,
    chunk_calendar_day_hint: Optional[str],
) -> bool:
  """True when oldest pending delete day is calendar-disjoint from in-flight ingest."""
  if not chunk_calendar_day_hint:
    return False
  if day_raw_removal is None or not day_raw_removal.enabled:
    return False
  blocking_tar = day_raw_removal.oldest_day_needing_delete()
  if not blocking_tar:
    return False
  delete_day = calendar_date_from_daily_tar_path(blocking_tar)
  if delete_day is None:
    return False
  return delete_day.isoformat() != chunk_calendar_day_hint


def run_supervisor_day_raw_removal_delete_pass(
    day_raw_removal,
    async_day_close,
    *,
    chunk_in_progress: bool,
    chunk_calendar_day_hint: Optional[str] = None,
    finalize_day_close_delete: Callable[[str], None],
    sleep_fn: Callable[[float], None],
    log_chunk_wait: Optional[Callable[[Optional[str], int], None]] = None,
    on_delete_batch_begin: Optional[Callable[[], None]] = None,
    on_delete_batch_end: Optional[Callable[[], None]] = None,
) -> bool:
  """One supervisor delete-driver pass; tar-drop runs before batch-delete chunk wait.

  Returns True when the caller should keep spinning the delete driver.
  """
  if day_raw_removal is None or not day_raw_removal.enabled:
    return False
  if async_day_close is not None:
    async_day_close.reconcile_supervisor_raw_delete_pending(reason="delete_pass")
  reopen_fn = getattr(
      day_raw_removal, "reopen_done_days_with_verified_on_disk", lambda: 0,
  )
  made_progress = reopen_fn() > 0
  needs_delete = day_raw_removal.any_needs_delete_phase()
  needs_tar_drop = day_raw_removal.any_needs_tar_drop_finish()
  async_tar_drop = (
      async_day_close.tar_paths_raw_delete_pending()
      if async_day_close is not None
      else []
  )
  if not needs_delete and not needs_tar_drop and not async_tar_drop:
    advance_fn = getattr(
        day_raw_removal, "advance_startup_drain_blockers", lambda: False,
    )
    if day_raw_removal.any_blocks_startup_drain() and advance_fn():
      return True
    return False
  tar_drop_targets: list[str] = []
  if needs_tar_drop:
    tar_drop_targets.extend(day_raw_removal.days_needing_tar_drop_oldest_first())
  for tar_norm in async_tar_drop:
    if tar_norm not in tar_drop_targets:
      tar_drop_targets.append(tar_norm)
  for tar_norm in tar_drop_targets:
    if day_raw_removal.try_finish_tar_drop_if_ready(tar_norm):
      finalize_day_close_delete(tar_norm)
      made_progress = True
  if tar_drop_targets and getattr(day_raw_removal, "log_fn", None):
    still_present = [t for t in tar_drop_targets if os.path.isfile(t)]
    if still_present:
      day_raw_removal.log_fn(
          "sync_timedb: tar_drop_deferred oldest=%s count=%d"
          % (still_present[0], len(still_present)),
          flush=True,
      )
  if needs_delete:
    if chunk_in_progress:
      if not day_raw_delete_safe_during_chunk(
          day_raw_removal,
          chunk_calendar_day_hint,
      ):
        blocking_tar = day_raw_removal.oldest_day_needing_delete()
        sleep_fn(0.1)
        if log_chunk_wait is not None:
          log_chunk_wait(blocking_tar, len(tar_drop_targets))
        return True
    if on_delete_batch_begin is not None:
      on_delete_batch_begin()
    try:
      for tar_norm in day_raw_removal.days_needing_delete_oldest_first():
        if day_raw_removal.phase(tar_norm) == PHASE_VERIFICATION_COMPLETE:
          day_raw_removal.begin_deleting(tar_norm)
        deleted = day_raw_removal.apply_batch_delete(tar_norm)
        if deleted:
          made_progress = True
        if day_raw_removal.delete_phase_done(tar_norm):
          finalize_day_close_delete(tar_norm)
          made_progress = True
          continue
        if (
            deleted == 0
            and day_raw_removal.needs_delete_phase(tar_norm)
            and not day_raw_removal.delete_phase_done(tar_norm)
        ):
          continue
    finally:
      if on_delete_batch_end is not None:
        on_delete_batch_end()
  if (
      needs_delete
      and day_raw_removal.any_needs_delete_phase()
      and made_progress
  ):
    return True
  return False


def remaining_raw_by_gz_blocking_tar_drop(
    *,
    tar_path: str,
    archive_data_dir: str,
    host_name_ext: str,
    tgz_archive_dir: str,
    get_quarantine_skip_paths: Callable[[], Set[str]],
    get_maintenance_snapshot: Optional[Callable[[], Any]] = None,
    log_fn=None,
) -> Dict[str, List[str]]:
  """Shared tar-drop blocker map for supervisor and async paths."""
  state = _DayRawRemovalState(
      tar_path=tar_path,
      archive_data_dir=archive_data_dir,
      host_name_ext=host_name_ext,
      tgz_archive_dir=tgz_archive_dir,
      log_fn=log_fn,
      get_quarantine_skip_paths=get_quarantine_skip_paths,
      get_maintenance_snapshot=get_maintenance_snapshot,
  )
  return state._remaining_raw_paths_blocking_tar_drop()


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
      on_pipeline_complete: Optional[Callable[[str], None]] = None,
      on_handoff_to_ingest: Optional[Callable[[str, List[str], str], None]] = None,
      get_maintenance_snapshot: Optional[Callable[[], Any]] = None,
  ):
    self.archive_data_dir = archive_data_dir
    self.host_name_ext = host_name_ext
    self.tgz_archive_dir = tgz_archive_dir
    self.log_fn = log_fn
    self.get_quarantine_skip_paths = get_quarantine_skip_paths
    self.ingest_ready_fn = ingest_ready_fn
    self.process_title = process_title
    self.on_pipeline_complete = on_pipeline_complete
    self.on_handoff_to_ingest = on_handoff_to_ingest
    self.get_maintenance_snapshot = get_maintenance_snapshot
    self.enabled = cfg.get_sync_day_close_raw_removal_preflight()
    self._days: Dict[str, _DayRawRemovalState] = {}
    self._days_lock = threading.Lock()
    self._executor: Optional[ThreadPoolExecutor] = None
    self._last_closed_raw_kick_action: Optional[str] = None
    if self.enabled:
      cleanup_orphan_fnctl_lock_sidecars(
          day_removal_manifest_dir(self.archive_data_dir),
      )

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
          get_maintenance_snapshot=self.get_maintenance_snapshot,
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

  def raw_removal_progress_summary(self, tar_path: str) -> Dict[str, Any]:
    tar_norm = os.path.normpath(tar_path or "")
    with self._days_lock:
      state = self._days.get(tar_norm)
    if state is None:
      return {
          "phase": "",
          "verified_count": 0,
          "pending_delete": 0,
          "deleted_count": 0,
      }
    return state.progress_summary()

  def pipeline_future_done(self, tar_path: str) -> bool:
    tar_norm = os.path.normpath(tar_path or "")
    with self._days_lock:
      state = self._days.get(tar_norm)
    if state is None:
      return True
    future = state._pipeline_future
    return future is None or future.done()

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
    for state in states:
      if state.needs_delete_phase() and not state.delete_phase_done():
        return True
      if state.needs_reopen_for_verified_pending():
        return True
      if state.needs_ghost_delete_retry():
        return True
    return False

  def reopen_done_days_with_verified_on_disk(self) -> int:
    """Reopen ``phase=done`` days that still have verified paths on disk."""
    reopened = 0
    with self._days_lock:
      states = list(self._days.values())
    for state in states:
      if state.reopen_delete_phase_if_verified_on_disk():
        reopened += 1
    return reopened

  def advance_startup_drain_blockers(self) -> bool:
    """Kick verify/quarantine for days that block startup drain without delete work."""
    if not self.enabled:
      return False
    progressed = False
    with self._days_lock:
      states = list(self._days.values())
    for state in states:
      if not state.blocks_startup_drain():
        continue
      if state._async_verify_in_flight():
        continue
      if (
          not state.verification_complete()
          or state.phase() == PHASE_VERIFYING
      ):
        self.start_async_verify(state.tar_path)
        progressed = True
        continue
      kick = self.kick_closed_raw_unblock(
          state.tar_path,
          reason="startup_drain",
      )
      if kick not in KICK_NO_HANDOFF_PROGRESS:
        progressed = True
    return progressed

  def blocking_startup_drain_summary(self) -> Tuple[int, str]:
    """Return (blocking_day_count, oldest_summary_token) for drain telemetry."""
    blockers: List[Tuple[Any, str]] = []
    with self._days_lock:
      states = list(self._days.values())
    for state in states:
      if not state.blocks_startup_drain():
        continue
      in_flight = state._async_verify_in_flight()
      pending_n = state._manifest_verified_pending_count()
      token = (
          "%s phase=%s pending_verified=%d in_flight=%s"
          % (
              os.path.basename(state.tar_path),
              state.phase(),
              pending_n,
              in_flight,
          )
      )
      blockers.append((state.day_date, token))
    if not blockers:
      return 0, ""
    blockers.sort(key=lambda item: item[0])
    return len(blockers), blockers[0][1]

  def any_needs_tar_drop_finish(self) -> bool:
    return bool(self.days_needing_tar_drop_oldest_first())

  def days_needing_tar_drop_oldest_first(self) -> List[str]:
    candidates = []
    with self._days_lock:
      states = list(self._days.values())
    for state in states:
      if not os.path.isfile(state.tar_path):
        continue
      zst_path, gz_path = compressed_sibling_paths(state.tar_path)
      if not (os.path.isfile(zst_path) or os.path.isfile(gz_path)):
        continue
      if state._has_closed_raw_existing_on_disk():
        continue
      candidates.append((state.day_date, state.tar_path))
    candidates.sort(key=lambda item: item[0])
    return [tar_path for _day_date, tar_path in candidates]

  def remaining_raw_paths_blocking_tar_drop(self, tar_path: str) -> Dict[str, List[str]]:
    return self._get_or_create_day(tar_path)._remaining_raw_paths_blocking_tar_drop()

  def try_finish_tar_drop_if_ready(self, tar_path: str) -> bool:
    state = self._get_or_create_day(tar_path)
    if not state.try_finish_tar_drop_if_ready():
      return False
    if state.delete_phase_done():
      self._notify_delete_complete(tar_path)
    return True

  def any_blocks_startup_drain(self) -> bool:
    if not self.enabled:
      return False
    with self._days_lock:
      states = list(self._days.values())
    return any(state.blocks_startup_drain() for state in states)

  def count_days_waiting_on_ingest(self) -> int:
    if not self.enabled:
      return 0
    with self._days_lock:
      states = list(self._days.values())
    return sum(1 for state in states if state.waiting_on_ingest_at_startup())

  def oldest_day_needing_delete(self) -> Optional[str]:
    days = self.days_needing_delete_oldest_first()
    return days[0] if days else None

  def days_needing_delete_oldest_first(self) -> List[str]:
    candidates = []
    with self._days_lock:
      states = list(self._days.values())
    for state in states:
      if (
          (state.needs_delete_phase() and not state.delete_phase_done())
          or state.needs_ghost_delete_retry()
      ):
        candidates.append((state.day_date, state.tar_path))
    candidates.sort(key=lambda item: item[0])
    return [tar_path for _day_date, tar_path in candidates]

  def should_handoff_to_ingest(self, tar_path: str) -> bool:
    return self._get_or_create_day(tar_path).should_handoff_day_close_to_ingest()

  def has_closed_raw_on_disk(self, tar_path: str) -> bool:
    return self._get_or_create_day(tar_path)._has_closed_raw_existing_on_disk()

  def closed_raw_paths_on_disk(self, tar_path: str) -> List[str]:
    state = self._get_or_create_day(tar_path)
    return [
        path
        for path in state._closed_raw_paths_on_disk()
        if os.path.isfile(path)
    ]

  def _closed_raw_path_is_quarantine_skip(self, path: str) -> bool:
    path_norm = os.path.normpath(str(path or ""))
    if not path_norm:
      return True
    skip_paths = set(self.get_quarantine_skip_paths() or ())
    if path_norm in skip_paths:
      return True
    quarantine_root = os.path.normpath(
        quarantine_dir_for_archive(self.archive_data_dir),
    )
    if quarantine_root and path_norm.startswith(quarantine_root + os.sep):
      return True
    return False

  def paths_for_closed_raw_handoff_requeue(self, tar_path: str) -> List[str]:
    """Retryable/unmanifested closed raw only — not manifest-blocking verify paths."""
    state = self._get_or_create_day(tar_path)
    paths: List[str] = []
    seen: Set[str] = set()
    for path in state.handoff_paths_for_ingest():
      path_norm = os.path.normpath(str(path or ""))
      if not path_norm or path_norm in seen:
        continue
      if not os.path.isfile(path_norm):
        continue
      if self._closed_raw_path_is_quarantine_skip(path_norm):
        continue
      seen.add(path_norm)
      paths.append(path_norm)
    for path in state._unmanifested_closed_raw_paths():
      path_norm = os.path.normpath(str(path or ""))
      if not path_norm or path_norm in seen:
        continue
      if not os.path.isfile(path_norm):
        continue
      if self._closed_raw_path_is_quarantine_skip(path_norm):
        continue
      seen.add(path_norm)
      paths.append(path_norm)
    return paths

  def needs_verify_for_closed_raw_block(self, tar_path: str) -> bool:
    """True when manifest phase=done but non-retryable manifest paths block DAY_CLOSE."""
    state = self._get_or_create_day(tar_path)
    if state.needs_ghost_delete_retry():
      return True
    if not state.delete_phase_done():
      return False
    blocking = state._blocking_manifest_paths_on_disk()
    if not blocking:
      return False
    retryable = set(state._manifest_retryable_paths_on_disk())
    return any(path not in retryable for path in blocking)

  def kick_closed_raw_unblock(self, tar_path: str, *, reason: str) -> str:
    """Drive delete reopen, ghost retry, or verify for closed-raw blockers."""
    if not self.enabled:
      return "noop"
    state = self._get_or_create_day(tar_path)
    tar_norm = os.path.normpath(tar_path)
    if state._only_quarantine_terminal_on_disk():
      state._finalize_quarantine_terminal_done()
      if self.log_fn:
        self.log_fn(
            "Day raw removal closed-raw quarantine terminal tar=%s reason=%s"
            % (tar_norm, reason or ""),
            flush=True,
        )
      return "quarantine_terminal"
    if state.needs_ghost_delete_retry():
      if state._prepare_ghost_delete_retry():
        if self.log_fn:
          self.log_fn(
              "Day raw removal closed-raw ghost delete kick tar=%s reason=%s"
              % (tar_norm, reason or ""),
              flush=True,
          )
        return "ghost_delete"
    if state.delete_phase_done():
      if state.reopen_delete_phase_if_verified_on_disk():
        return "delete_reopen"
      blocking = state._blocking_manifest_paths_on_disk()
      if blocking:
        retryable = set(state._manifest_retryable_paths_on_disk())
        if any(path not in retryable for path in blocking):
          state.begin_deleting()
          if state.phase() == PHASE_DELETING:
            if self.log_fn:
              self.log_fn(
                  "Day raw removal closed-raw delete kick tar=%s reason=%s "
                  "detail=blocking_manifest"
                  % (tar_norm, reason or ""),
                  flush=True,
              )
            return "delete_reopen"
      return "noop"
    if not state.verification_complete():
      self.start_async_verify(tar_path)
      if self.log_fn:
        self.log_fn(
            "Day raw removal closed-raw verify kick tar=%s reason=%s"
            % (tar_norm, reason or ""),
            flush=True,
        )
      return "verify"
    if state.verification_complete():
      with state._lock:
        entries = state._manifest.get("entries", {})
        pending_delete = any(
            isinstance(entry, dict)
            and entry.get("status") == "verified"
            and not entry.get("deleted")
            for entry in entries.values()
        )
      if pending_delete:
        state.begin_deleting()
        if state.phase() == PHASE_DELETING:
          if self.log_fn:
            self.log_fn(
                "Day raw removal closed-raw delete kick tar=%s reason=%s "
                "detail=verification_complete_pending"
                % (tar_norm, reason or ""),
                flush=True,
            )
          return "delete_reopen"
    return "noop"

  def requeue_closed_raw_paths_for_ingest(
      self,
      tar_path: str,
      *,
      reason: str,
      paths: Optional[List[str]] = None,
  ) -> List[str]:
    """Requeue handoff-eligible closed raw; kick delete/verify when manifest blocks."""
    self._last_closed_raw_kick_action = None
    if not self.enabled or self.on_handoff_to_ingest is None:
      return []
    tar_norm = os.path.normpath(tar_path)
    if paths is None:
      paths = self.paths_for_closed_raw_handoff_requeue(tar_path)
    else:
      normalized: List[str] = []
      seen: Set[str] = set()
      for path in paths:
        path_norm = os.path.normpath(str(path or ""))
        if not path_norm or path_norm in seen:
          continue
        if not os.path.isfile(path_norm):
          continue
        if self._closed_raw_path_is_quarantine_skip(path_norm):
          continue
        seen.add(path_norm)
        normalized.append(path_norm)
      paths = normalized
    if paths:
      try:
        self.on_handoff_to_ingest(tar_norm, paths, reason)
      except Exception:
        if self.log_fn:
          self.log_fn(
              "Day raw removal closed-raw handoff callback failed tar=%s"
              % tar_norm,
              flush=True,
          )
        return []
      return paths
    kick_action = self.kick_closed_raw_unblock(tar_path, reason=reason)
    self._last_closed_raw_kick_action = kick_action
    return []

  def discover_closed_raw_on_disk_handoffs(self) -> List[Tuple[str, List[str]]]:
    """Boot-time handoff for days with closed raw blockers (narrow path lists)."""
    if not self.enabled:
      return []
    manifest_dir = day_removal_manifest_dir(self.archive_data_dir)
    if not os.path.isdir(manifest_dir):
      return []
    handoffs: List[Tuple[str, List[str]]] = []
    for fname in sorted(os.listdir(manifest_dir)):
      if not fname.endswith(".json"):
        continue
      tar_path = os.path.join(
          self.tgz_archive_dir,
          fname.replace(".json", ".tar"),
      )
      if not os.path.isfile(tar_path) and not os.path.isfile(tar_path + ".zst"):
        continue
      tar_norm = os.path.normpath(tar_path)
      state = self._get_or_create_day(tar_path)
      paths = self.paths_for_closed_raw_handoff_requeue(tar_path)
      needs_kick = (
          paths
          or state.needs_ghost_delete_retry()
          or self.needs_verify_for_closed_raw_block(tar_path)
          or (
              not state.delete_phase_done()
              and not state.verification_complete()
          )
      )
      if not needs_kick:
        continue
      handoffs.append((tar_norm, paths))
    return handoffs

  def complete_handoff_to_ingest(
      self,
      tar_path: str,
      *,
      reason: str = "",
  ) -> List[str]:
    """Finalize handoff state and invoke ``on_handoff_to_ingest`` when wired."""
    state = self._get_or_create_day(tar_path)
    if not state.should_handoff_day_close_to_ingest():
      return []
    paths = state.complete_handoff_to_ingest()
    if not paths:
      return []
    tar_norm = os.path.normpath(tar_path)
    if self.on_handoff_to_ingest is not None:
      try:
        self.on_handoff_to_ingest(tar_norm, paths, reason)
      except Exception:
        if self.log_fn:
          self.log_fn(
              "Day raw removal handoff callback failed tar=%s" % tar_norm,
              flush=True,
          )
    return paths

  def discover_manifest_handoffs(self) -> List[Tuple[str, List[str]]]:
    """Scan persisted per-day manifests for retryable-only handoff candidates."""
    if not self.enabled:
      return []
    manifest_dir = day_removal_manifest_dir(self.archive_data_dir)
    if not os.path.isdir(manifest_dir):
      return []
    handoffs: List[Tuple[str, List[str]]] = []
    for fname in sorted(os.listdir(manifest_dir)):
      if not fname.endswith(".json"):
        continue
      day_iso = fname[:-5]
      try:
        day_date = date.fromisoformat(day_iso)
      except ValueError:
        continue
      tar_path = os.path.normpath(
          os.path.join(self.tgz_archive_dir, "%s.tar" % day_date.isoformat()),
      )
      if not os.path.isfile(tar_path):
        zst_path, gz_path = compressed_sibling_paths(tar_path)
        if not (os.path.isfile(zst_path) or os.path.isfile(gz_path)):
          continue
      state = self._get_or_create_day(tar_path)
      if not state.should_handoff_day_close_to_ingest():
        continue
      paths = state.handoff_paths_for_ingest()
      if paths:
        handoffs.append((state.tar_path, paths))
    return handoffs

  def _try_handoff_to_ingest(self, tar_path: str, *, reason: str) -> bool:
    if not self.enabled or self.on_handoff_to_ingest is None:
      return False
    paths = self.complete_handoff_to_ingest(tar_path, reason=reason)
    return bool(paths)

  def verified_paths_pending_delete(self, tar_path: str) -> Set[str]:
    return self._get_or_create_day(tar_path).paths_pending_delete()

  def _ensure_executor(self) -> ThreadPoolExecutor:
    if self._executor is None:
      self._executor = ThreadPoolExecutor(max_workers=1)
    return self._executor

  def _verify_pipeline_body(self, state: _DayRawRemovalState) -> None:
    close_old_connections()
    if state.delete_phase_done():
      if state._needs_retry_after_ingest():
        state._reset_for_reverify()
      else:
        return
    verify_started = time.time()
    verify_budget = cfg.get_sync_day_close_raw_removal_verify_budget_seconds()
    if not state.verification_complete():
      while not shutdown_requested[0]:
        if verify_budget > 0 and time.time() - verify_started >= verify_budget:
          break
        state._verify_body()
        if state.verification_complete():
          break
        time.sleep(0.1)
    if shutdown_requested[0]:
      return
    if not state.verification_complete():
      summary = state.progress_summary()
      if self.log_fn:
        self.log_fn(
            "Day raw removal verify budget exhausted day=%s phase=%s "
            "verified=%d pending_delete=%d"
            % (
                state.day_date.isoformat(),
                summary.get("phase"),
                int(summary.get("verified_count", 0)),
                int(summary.get("pending_delete", 0)),
            ),
            flush=True,
        )
      with state._lock:
        _save_manifest(state._manifest_path, state._manifest)
      return
    self._try_handoff_to_ingest(
        state.tar_path,
        reason="verify_pipeline_complete",
    )

  def _submit_async_verify(self, state: _DayRawRemovalState) -> None:
    if state._pipeline_future is not None and not state._pipeline_future.done():
      return
    executor = self._ensure_executor()

    def _run():
      set_daemon_thread_title(
          "",
          script_name=self.process_title,
          role="day-raw-removal-verify",
      )
      try:
        self._verify_pipeline_body(state)
      finally:
        close_old_connections()

    state._pipeline_future = executor.submit(_run)

  def start_async_verify(
      self,
      tar_path: str,
      *,
      sealed_members=None,
  ) -> None:
    """Run verify for one calendar day on a background thread (production path)."""
    if not self.enabled:
      return
    state = self._get_or_create_day(tar_path)
    state._verify_sealed_members = (
        dict(sealed_members) if sealed_members is not None else None
    )
    if state.delete_phase_done():
      if state._needs_retry_after_ingest():
        state._reset_for_reverify()
      else:
        return
    if state.verification_complete():
      return
    self._submit_async_verify(state)

  def start_async_day_pipeline(self, tar_path: str) -> None:
    """Backward-compatible alias: verify-only async (delete on supervisor thread)."""
    self.start_async_verify(tar_path)

  def _notify_delete_complete(self, tar_path: str) -> None:
    if self.on_pipeline_complete is None:
      return
    try:
      self.on_pipeline_complete(tar_path)
    except Exception:
      if self.log_fn:
        self.log_fn(
            "Day raw removal on_complete failed tar=%s" % tar_path,
            flush=True,
        )

  def begin_deleting(self, tar_path: str) -> None:
    self._get_or_create_day(tar_path).begin_deleting()

  def apply_batch_delete(self, tar_path: str) -> int:
    state = self._get_or_create_day(tar_path)
    deleted = state.apply_batch_delete()
    if state.should_handoff_day_close_to_ingest():
      self.complete_handoff_to_ingest(
          tar_path,
          reason="batch_delete_waiting_on_ingest",
      )
    elif state.delete_phase_done():
      self._notify_delete_complete(tar_path)
    elif state.phase() == PHASE_VERIFYING:
      self.start_async_verify(tar_path)
    return deleted

  def shutdown(self, wait: bool = True) -> None:
    executor = self._executor
    if executor is None:
      return
    if wait:
      with self._days_lock:
        states = list(self._days.values())
      for state in states:
        future = state._pipeline_future
        if future is not None:
          try:
            future.result(timeout=30.0)
          except Exception:
            pass
    executor.shutdown(wait=wait)
