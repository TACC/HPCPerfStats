"""Startup-only quiescent daily tar seal: async seal + drop when no raw on disk."""
from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Any, Callable, Dict, List, Optional

import hpcperfstats.conf_parser as cfg
from django.db import close_old_connections

from hpcperfstats.dbload.archive_compress import compressed_sibling_paths
from hpcperfstats.dbload.sync_timedb_archive_helpers import (
    atomic_seal_tar_to_zst,
    build_remaining_raw_for_daily_tar,
    calendar_date_from_daily_tar_path,
    daily_gz_has_remaining_raw_stats,
    daily_tar_seal_calendar_eligible,
    drop_legacy_gz_if_equivalent_to_zst,
    effective_keep_uncompressed_tar,
    is_daily_tar_sealed_dirty,
    iter_daily_tar_paths,
)
from hpcperfstats.process_title import set_daemon_thread_title
from hpcperfstats.shutdown_utils import shutdown_requested

MANIFEST_BASENAME = ".sync_timedb_startup_tar_seal.json"
MANIFEST_VERSION = 1

PHASE_SEALING = "sealing"
PHASE_DONE = "done"


def manifest_path(archive_data_dir: str) -> str:
  return os.path.join(archive_data_dir, MANIFEST_BASENAME)


def _new_manifest() -> Dict[str, Any]:
  return {
      "version": MANIFEST_VERSION,
      "phase": PHASE_SEALING,
      "started_at": time.time(),
      "completed_at": None,
      "sealed_count": 0,
      "skipped_count": 0,
      "failed_count": 0,
      "pending_tar_paths": [],
      "entries": {},
  }


def _load_manifest(path: str) -> Dict[str, Any]:
  try:
    with open(path, encoding="utf-8") as handle:
      payload = json.load(handle)
  except (OSError, json.JSONDecodeError, TypeError, ValueError):
    return _new_manifest()
  if not isinstance(payload, dict):
    return _new_manifest()
  payload.setdefault("version", MANIFEST_VERSION)
  payload.setdefault("entries", {})
  payload.setdefault("pending_tar_paths", [])
  return payload


def _save_manifest(path: str, payload: Dict[str, Any]) -> None:
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


def _tar_sort_key(tar_path: str) -> date:
  day = calendar_date_from_daily_tar_path(tar_path)
  return day if day is not None else date.max


class StartupTarSealPreflight:
  """Async startup pass: seal quiescent daily ``.tar`` files (no closed raw on disk)."""

  def __init__(
      self,
      *,
      archive_data_dir: str,
      host_name_ext: str,
      tgz_archive_dir: str,
      local_tz,
      log_fn,
      has_active_append_for_tar: Callable[[str], bool],
      process_title: str = "sync_timedb.py",
  ):
    self.archive_data_dir = archive_data_dir
    self.host_name_ext = host_name_ext
    self.tgz_archive_dir = tgz_archive_dir
    self.local_tz = local_tz
    self.log_fn = log_fn
    self.has_active_append_for_tar = has_active_append_for_tar
    self.process_title = process_title
    self._manifest_path = manifest_path(archive_data_dir)
    self._lock = threading.Lock()
    self._manifest = _load_manifest(self._manifest_path)
    self._executor: Optional[ThreadPoolExecutor] = None
    self._seal_future = None
    self.enabled = cfg.get_sync_startup_tar_seal_preflight()

  def phase(self) -> str:
    with self._lock:
      return str(self._manifest.get("phase") or PHASE_SEALING)

  def seal_pass_done(self) -> bool:
    return self.phase() == PHASE_DONE

  def start_async_seal(self) -> None:
    if not self.enabled:
      return
    with self._lock:
      phase = str(self._manifest.get("phase") or "")
      if phase == PHASE_DONE:
        return
      if phase in ("", PHASE_SEALING):
        if not self._manifest.get("started_at"):
          self._manifest["started_at"] = time.time()
        self._manifest["phase"] = PHASE_SEALING
        _save_manifest(self._manifest_path, self._manifest)
      if self._executor is not None:
        return
      self._executor = ThreadPoolExecutor(max_workers=1)
      self._seal_future = self._executor.submit(self._seal_loop)

  def shutdown(self, wait: bool = True) -> None:
    executor = self._executor
    if executor is None:
      return
    executor.shutdown(wait=wait)

  def _record_entry(self, tar_path: str, status: str, reason: str) -> None:
    tar_norm = os.path.normpath(tar_path)
    entry = {
        "tar_path": tar_norm,
        "status": status,
        "reason": reason,
    }
    with self._lock:
      entries = self._manifest.setdefault("entries", {})
      prior = entries.get(tar_norm)
      if isinstance(prior, dict) and prior.get("status") == status:
        return
      if not isinstance(prior, dict):
        if status == "sealed":
          self._manifest["sealed_count"] = int(
              self._manifest.get("sealed_count", 0)) + 1
        elif status == "failed":
          self._manifest["failed_count"] = int(
              self._manifest.get("failed_count", 0)) + 1
        elif status.startswith("skipped"):
          self._manifest["skipped_count"] = int(
              self._manifest.get("skipped_count", 0)) + 1
      entries[tar_norm] = entry

  def _discover_pending_tar_paths(self) -> List[str]:
    pending = []
    tar_paths = sorted(
        iter_daily_tar_paths(self.tgz_archive_dir),
        key=_tar_sort_key,
    )
    for tar_path in tar_paths:
      tar_norm = os.path.normpath(tar_path)
      with self._lock:
        entry = self._manifest.get("entries", {}).get(tar_norm)
        if isinstance(entry, dict) and entry.get("status") == "sealed":
          continue
      if not os.path.isfile(tar_path):
        continue
      zst_path, gz_path = compressed_sibling_paths(tar_path)
      if not is_daily_tar_sealed_dirty(tar_path, zst_path, gz_path):
        self._record_entry(tar_norm, "skipped_already_sealed", "zst_current")
        continue
      if not daily_tar_seal_calendar_eligible(tar_path, self.local_tz):
        self._record_entry(
            tar_norm,
            "skipped_calendar_grace",
            "calendar_today_grace",
        )
        continue
      remaining = build_remaining_raw_for_daily_tar(
          self.archive_data_dir,
          self.host_name_ext,
          self.tgz_archive_dir,
          tar_path,
      )
      if daily_gz_has_remaining_raw_stats(zst_path, remaining):
        self._record_entry(tar_norm, "skipped_remaining_raw", "raw_on_disk")
        continue
      if self.has_active_append_for_tar(tar_norm):
        self._record_entry(tar_norm, "skipped_active_append", "active_append")
        continue
      pending.append(tar_norm)
    return pending

  def _seal_one_tar(self, tar_path: str) -> bool:
    tar_norm = os.path.normpath(tar_path)
    zst_path, gz_path = compressed_sibling_paths(tar_norm)
    remaining_raw_by_gz = build_remaining_raw_for_daily_tar(
        self.archive_data_dir,
        self.host_name_ext,
        self.tgz_archive_dir,
        tar_norm,
    )
    if daily_gz_has_remaining_raw_stats(zst_path, remaining_raw_by_gz):
      self._record_entry(tar_norm, "skipped_remaining_raw", "raw_on_disk")
      return False
    if self.has_active_append_for_tar(tar_norm):
      self._record_entry(tar_norm, "skipped_active_append", "active_append")
      return False
    keep_tar = effective_keep_uncompressed_tar(
        tar_norm,
        local_tz=self.local_tz,
    )
    if self.log_fn:
      self.log_fn(
          "sync_timedb: startup tar seal start tar=%s" % tar_norm,
          flush=True,
      )
    try:
      atomic_seal_tar_to_zst(
          tar_norm,
          zst_path,
          cfg.get_archive_zstd_threads(),
          cfg.get_archive_zstd_level(),
          keep_tar,
          log_fn=self.log_fn,
          remaining_raw_by_gz={},
          force_remove_uncompressed_tar=False,
      )
      drop_legacy_gz_if_equivalent_to_zst(gz_path, zst_path, log_fn=self.log_fn)
    except Exception as exc:
      self._record_entry(tar_norm, "failed", str(exc))
      if self.log_fn:
        self.log_fn(
            "sync_timedb: startup tar seal failed tar=%s err=%s"
            % (tar_norm, exc),
            flush=True,
        )
      return False
    if os.path.isfile(zst_path) or os.path.isfile(gz_path):
      self._record_entry(tar_norm, "sealed", "ok")
      if self.log_fn:
        self.log_fn(
            "sync_timedb: startup tar seal done tar=%s" % tar_norm,
            flush=True,
        )
      return True
    self._record_entry(tar_norm, "failed", "no_sealed_output")
    return False

  def _seal_slice(self) -> bool:
    """Return True when the seal pass is complete."""
    with self._lock:
      phase = str(self._manifest.get("phase") or "")
      if phase == PHASE_DONE:
        return True
      pending = list(self._manifest.get("pending_tar_paths") or [])
    if not pending:
      pending = self._discover_pending_tar_paths()
      with self._lock:
        self._manifest["pending_tar_paths"] = pending
        _save_manifest(self._manifest_path, self._manifest)
      if not pending:
        with self._lock:
          self._manifest["phase"] = PHASE_DONE
          self._manifest["completed_at"] = time.time()
          _save_manifest(self._manifest_path, self._manifest)
        if self.log_fn:
          self.log_fn(
              "sync_timedb: startup tar seal complete "
              "sealed=%d skipped=%d failed=%d"
              % (
                  int(self._manifest.get("sealed_count", 0)),
                  int(self._manifest.get("skipped_count", 0)),
                  int(self._manifest.get("failed_count", 0)),
              ),
              flush=True,
          )
        return True
      if self.log_fn:
        self.log_fn(
            "sync_timedb: startup tar seal discovered pending=%d"
            % len(pending),
            flush=True,
        )
    budget = cfg.get_sync_startup_tar_seal_budget_seconds()
    days_per_slice = cfg.get_sync_startup_tar_seal_days_per_slice()
    slice_started = time.time()
    processed_days = 0
    while pending and processed_days < days_per_slice:
      if shutdown_requested[0]:
        return False
      if time.time() - slice_started >= budget:
        break
      tar_path = pending.pop(0)
      self._seal_one_tar(tar_path)
      processed_days += 1
      with self._lock:
        self._manifest["pending_tar_paths"] = list(pending)
        _save_manifest(self._manifest_path, self._manifest)
    with self._lock:
      self._manifest["pending_tar_paths"] = pending
      if not pending:
        self._manifest["phase"] = PHASE_DONE
        self._manifest["completed_at"] = time.time()
        if self.log_fn:
          self.log_fn(
              "sync_timedb: startup tar seal complete "
              "sealed=%d skipped=%d failed=%d"
              % (
                  int(self._manifest.get("sealed_count", 0)),
                  int(self._manifest.get("skipped_count", 0)),
                  int(self._manifest.get("failed_count", 0)),
              ),
              flush=True,
          )
      _save_manifest(self._manifest_path, self._manifest)
    return not pending

  def _seal_loop(self) -> None:
    set_daemon_thread_title(
        script_name=self.process_title,
        role="startup-tar-seal-preflight",
    )
    close_old_connections()
    try:
      with self._lock:
        phase = str(self._manifest.get("phase") or "")
      if phase == PHASE_DONE:
        return
      while not shutdown_requested[0]:
        if self._seal_slice():
          break
        time.sleep(0.25)
    finally:
      close_old_connections()
