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
    remaining_raw_by_gz_has_paths_on_disk,
    remove_verified_uncompressed_daily_tars,
    stats_file_is_active_segment,
)
from hpcperfstats.dbload.lib.sync_timedb_persistence import (
    load_persistence_document,
    save_persistence_document,
)
from hpcperfstats.dbload.lib.file_locking import file_write_lock
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
      manifest_paths = self._manifest_paths_on_disk()
      if manifest_paths:
        return manifest_paths
    remaining = self._build_remaining_raw_for_daily_tar()
    paths: List[str] = []
    for raw_list in (remaining or {}).values():
      paths.extend(raw_list or [])
    return paths

  def _has_closed_raw_existing_on_disk(self) -> bool:
    if self.delete_phase_done():
      if self._manifest_paths_on_disk():
        return True
      if self._unmanifested_closed_raw_paths():
        return True
    remaining = self._build_remaining_raw_for_daily_tar()
    zst_path, _gz_path = compressed_sibling_paths(self.tar_path)
    return remaining_raw_by_gz_has_paths_on_disk(remaining, zst_path)

  def try_finish_tar_drop_if_ready(self) -> bool:
    """Drop ``.tar`` when sealed and no closed raw files remain on disk."""
    if not os.path.isfile(self.tar_path):
      return True
    zst_path, gz_path = compressed_sibling_paths(self.tar_path)
    if not (os.path.isfile(zst_path) or os.path.isfile(gz_path)):
      return False
    remaining_raw = self._build_remaining_raw_for_daily_tar()
    if remaining_raw_by_gz_has_paths_on_disk(remaining_raw, zst_path):
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
      if self._entry_is_retryable_skip(entry):
        return False
    return True

  def _unmanifested_closed_raw_paths(self) -> List[str]:
    with self._lock:
      entries = dict(self._manifest.get("entries", {}))
    if self.delete_phase_done() and self._manifest_paths_on_disk():
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
    if self.delete_phase_done():
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
      if not self._all_closed_raw_terminal_or_gone():
        if self._only_waiting_on_ingest_blocks_completion():
          if self._has_closed_raw_existing_on_disk():
            self._mark_done_waiting_on_ingest()
            return deleted
          # Retryable skips in manifest but raw already absent; drop tar below.
        if self._unmanifested_closed_raw_paths():
          with self._lock:
            self._manifest["phase"] = PHASE_VERIFYING
            _save_manifest(self._manifest_path, self._manifest)
          return deleted
        with self._lock:
          self._manifest["phase"] = PHASE_VERIFICATION_COMPLETE
          _save_manifest(self._manifest_path, self._manifest)
        return deleted
      remaining_raw = self._build_remaining_raw_for_daily_tar()
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
  needs_delete = day_raw_removal.any_needs_delete_phase()
  needs_tar_drop = day_raw_removal.any_needs_tar_drop_finish()
  async_tar_drop = (
      async_day_close.tar_paths_raw_delete_pending()
      if async_day_close is not None
      else []
  )
  if not needs_delete and not needs_tar_drop and not async_tar_drop:
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
        if day_raw_removal.delete_phase_done(tar_norm):
          finalize_day_close_delete(tar_norm)
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
  return False


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
    return any(s.needs_delete_phase() and not s.delete_phase_done() for s in states)

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
      if state.needs_delete_phase() and not state.delete_phase_done():
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

  def requeue_closed_raw_paths_for_ingest(
      self,
      tar_path: str,
      *,
      reason: str,
  ) -> List[str]:
    """Requeue every closed raw path on disk for ingest (checkpoint-complete days)."""
    if not self.enabled or self.on_handoff_to_ingest is None:
      return []
    paths = self.closed_raw_paths_on_disk(tar_path)
    if not paths:
      return []
    tar_norm = os.path.normpath(tar_path)
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

  def discover_closed_raw_on_disk_handoffs(self) -> List[Tuple[str, List[str]]]:
    """Boot-time handoff for days with closed raw on disk (any manifest entry status)."""
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
      paths = self.closed_raw_paths_on_disk(tar_path)
      if paths:
        handoffs.append((os.path.normpath(tar_path), paths))
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
