"""Async DAY_CLOSE coordinator: seal + raw removal + tar drop off janitor thread."""
from __future__ import annotations

import json
import os
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, Optional, Set

import hpcperfstats.conf_parser as cfg
from django.db import close_old_connections

from hpcperfstats.dbload.archive_compress import compressed_sibling_paths
from hpcperfstats.dbload.sync_timedb_archive_helpers import (
    atomic_seal_tar_to_zst,
    build_remaining_raw_for_daily_tar,
    daily_gz_has_remaining_raw_stats,
    daily_tar_seal_calendar_eligible,
    dedupe_sealed_daily_archive,
    dedupe_tar_keep_largest_file_per_member,
    drop_legacy_gz_if_equivalent_to_zst,
    effective_keep_uncompressed_tar,
    remove_verified_uncompressed_daily_tars,
    tar_has_duplicate_file_members,
)
from hpcperfstats.dbload.sync_timedb_persistence import (
    load_persistence_document,
    save_persistence_document,
)
from hpcperfstats.process_title import set_daemon_thread_title
from hpcperfstats.shutdown_utils import shutdown_requested, sleep_until_shutdown

MANIFEST_BASENAME = ".sync_timedb_async_day_close.json"
MANIFEST_VERSION = 1


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
  payload = load_persistence_document(path, "async_day_close", default=None)
  if not isinstance(payload, dict):
    return _new_manifest()
  payload.setdefault("version", MANIFEST_VERSION)
  payload.setdefault("entries", {})
  return payload


def _save_manifest(path: str, payload: Dict[str, Any]) -> None:
  save_persistence_document(path, "async_day_close", payload)


class AsyncDayCloseCoordinator:
  """Single-flight async DAY_CLOSE per daily ``.tar`` (no sync zstd on janitor)."""

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
    self.process_title = process_title
    self._manifest_path = manifest_path(archive_data_dir)
    self._lock = threading.Lock()
    self._manifest = _load_manifest(self._manifest_path)
    self._futures: Dict[str, Any] = {}
    self._executor: Optional[ThreadPoolExecutor] = None
    self._recover_stale_manifest_entries()

  def _recover_stale_manifest_entries(self) -> None:
    stale_s = cfg.get_sync_day_close_async_stale_seconds()
    if stale_s <= 0:
      return
    now = time.time()
    recovered: list[str] = []
    with self._lock:
      for tar_norm, entry in list(self._manifest.get("entries", {}).items()):
        if not isinstance(entry, dict):
          continue
        if entry.get("status") not in ("submitted", "sealing", "raw_removal"):
          continue
        future = self._futures.get(tar_norm)
        if future is not None and not future.done():
          continue
        last_at = entry.get("last_progress_at") or entry.get("submitted_at")
        if last_at is None:
          continue
        if now - float(last_at) < stale_s:
          continue
        entry["status"] = "deferred"
        entry["detail"] = "stale_manifest_recovery"
        entry["recovered_at"] = now
        recovered.append(os.path.normpath(tar_norm))
      if recovered:
        _save_manifest(self._manifest_path, self._manifest)
    for tar_norm in recovered:
      self.log_fn(
          "janitor: async day_close stale manifest recovery tar=%s" % tar_norm,
          flush=True,
      )

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

  def _ensure_executor(self) -> ThreadPoolExecutor:
    if self._executor is None:
      workers = max(
          cfg.get_sync_day_close_async_workers(),
          cfg.get_sync_startup_day_close_max_inflight(),
      )
      self._executor = ThreadPoolExecutor(
          max_workers=workers,
          thread_name_prefix="async-day-close",
      )
    return self._executor

  def shutdown(self, wait: bool = True) -> None:
    executor = self._executor
    if executor is None:
      return
    executor.shutdown(wait=wait)

  def active_or_submitted_tar_paths(self) -> Set[str]:
    with self._lock:
      active = set()
      for tar_norm, future in self._futures.items():
        if future is not None and not future.done():
          active.add(tar_norm)
      for tar_norm, entry in self._manifest.get("entries", {}).items():
        if isinstance(entry, dict) and entry.get("status") in (
            "submitted",
            "sealing",
            "raw_removal",
        ):
          active.add(os.path.normpath(tar_norm))
      return active

  def _day_close_filesystem_complete(self, tar_norm: str) -> bool:
    tar_norm = os.path.normpath(tar_norm or "")
    if not tar_norm:
      return False
    zst_path, gz_path = compressed_sibling_paths(tar_norm)
    remaining = build_remaining_raw_for_daily_tar(
        self.archive_data_dir,
        self.host_name_ext,
        self.tgz_archive_dir,
        tar_norm,
    )
    if daily_gz_has_remaining_raw_stats(zst_path, {zst_path: remaining}):
      return False
    if os.path.isfile(tar_norm):
      return False
    return os.path.isfile(zst_path) or os.path.isfile(gz_path)

  def finalize_complete_if_filesystem(self, tar_path: str) -> bool:
    """Mark async DAY_CLOSE complete when filesystem truth holds."""
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

  def submit_day_close(
      self,
      tar_path: str,
      *,
      reason: str,
      disqualified_daily_tars=None,
  ) -> bool:
    """Submit async DAY_CLOSE for ``tar_path`` (single-flight per tar)."""
    tar_norm = os.path.normpath(tar_path or "")
    if not tar_norm:
      return False
    if self.is_complete(tar_norm):
      return True
    with self._lock:
      future = self._futures.get(tar_norm)
      if future is not None and not future.done():
        return True
      if disqualified_daily_tars is None:
        disqualified = self.get_disqualified_daily_tars()
      else:
        disqualified = disqualified_daily_tars
      if tar_norm in disqualified:
        return False
      if self.submit_eligible_fn is not None:
        try:
          eligible, skip_reason = self.submit_eligible_fn(tar_norm)
        except Exception:
          eligible, skip_reason = False, "submit_eligible_error"
        if not eligible:
          if skip_reason:
            self.log_fn(
                "janitor: async day_close submit skip tar=%s reason=%s"
                % (tar_norm, skip_reason),
                flush=True,
            )
          return False
      entry = self._manifest.setdefault("entries", {}).get(tar_norm)
      if isinstance(entry, dict) and entry.get("status") == "complete":
        return True
      self._manifest.setdefault("entries", {})[tar_norm] = {
          "tar_path": tar_norm,
          "status": "submitted",
          "reason": reason,
          "submitted_at": time.time(),
      }
      self._touch_manifest_locked("submitted", tar_norm=tar_norm)
      executor = self._ensure_executor()
      future = executor.submit(self._run_day_close, tar_norm, reason)
      future.add_done_callback(lambda f, t=tar_norm: self._on_future_done(t, f))
      self._futures[tar_norm] = future
    self.log_fn(
        "janitor: async day_close submit tar=%s reason=%s" % (tar_norm, reason),
        flush=True,
    )
    return True

  def _touch_manifest_locked(self, stage: str, *, tar_norm: str = "") -> None:
    """Update manifest progress fields and persist. Caller must hold ``self._lock``."""
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

  def _on_future_done(self, tar_norm: str, future) -> None:
    try:
      exc = future.exception()
    except Exception as callback_exc:
      exc = callback_exc
    if exc is None:
      return
    self._set_entry_status(tar_norm, "failed", error=str(exc))
    self.log_fn(
        "janitor: async day_close failed tar=%s err=%s" % (tar_norm, exc),
        flush=True,
    )

  def _notify_phase(self, tar_norm: str, phase: str) -> None:
    if self.on_day_phase is not None:
      try:
        self.on_day_phase(tar_norm, phase)
      except Exception:
        pass

  def _run_day_close(self, tar_norm: str, reason: str) -> None:
    set_daemon_thread_title(
        "",
        script_name=self.process_title,
        role="async-day-close",
    )
    try:
      close_old_connections()
      if tar_norm in self.get_disqualified_daily_tars():
        self._set_entry_status(tar_norm, "deferred", detail="disqualified")
        return
      self._touch_manifest("sealing", tar_norm=tar_norm)
      self._set_entry_status(tar_norm, "sealing", reason=reason)
      if not self._seal_day(tar_norm):
        self._set_entry_status(tar_norm, "deferred", detail="seal_deferred")
        return
      self._notify_phase(tar_norm, "sealed")
      coord = self.day_raw_removal_coordinator
      if coord is not None and bool(getattr(coord, "enabled", False)):
        self._touch_manifest("raw_removal", tar_norm=tar_norm)
        self._set_entry_status(tar_norm, "raw_removal")
        coord.start_async_verify(tar_norm)
        wait_budget_s = cfg.get_sync_day_close_raw_removal_wait_seconds()
        wait_started = time.time()
        last_progress_log = wait_started
        verify_rekick_done = False
        while not shutdown_requested[0]:
          if coord.verification_complete(tar_norm):
            break
          now = time.time()
          if wait_budget_s > 0 and now - wait_started >= wait_budget_s:
            summary = coord.raw_removal_progress_summary(tar_norm)
            self.log_fn(
                "janitor: async day_close raw_removal verify stall tar=%s "
                "wait_s=%.0f phase=%s verified=%d pending_delete=%d"
                % (
                    tar_norm,
                    wait_budget_s,
                    summary.get("phase"),
                    int(summary.get("verified_count", 0)),
                    int(summary.get("pending_delete", 0)),
                ),
                flush=True,
            )
            self._set_entry_status(
                tar_norm,
                "deferred",
                detail="raw_removal_verify_timeout",
            )
            return
          if now - last_progress_log >= 60.0:
            summary = coord.raw_removal_progress_summary(tar_norm)
            self.log_fn(
                "janitor: async day_close raw_removal verify wait tar=%s "
                "elapsed_s=%.0f phase=%s verified=%d pending_delete=%d"
                % (
                    tar_norm,
                    now - wait_started,
                    summary.get("phase"),
                    int(summary.get("verified_count", 0)),
                    int(summary.get("pending_delete", 0)),
                ),
                flush=True,
            )
            last_progress_log = now
          if (
              not verify_rekick_done
              and coord.pipeline_future_done(tar_norm)
              and not coord.verification_complete(tar_norm)
          ):
            coord.start_async_verify(tar_norm)
            verify_rekick_done = True
          sleep_until_shutdown(0.5)
        if not coord.verification_complete(tar_norm):
          self._set_entry_status(tar_norm, "deferred", detail="raw_removal_verify")
          return
        self._set_entry_status(tar_norm, "raw_delete_pending")
        self._touch_manifest("raw_delete_pending", tar_norm=tar_norm)
        return
      if not self._tar_drop_day(tar_norm):
        self._set_entry_status(tar_norm, "deferred", detail="tar_drop")
        return
      if not self._day_close_filesystem_complete(tar_norm):
        self._set_entry_status(tar_norm, "deferred", detail="raw_remains")
        return
      self._notify_phase(tar_norm, "tar_dropped")
      self._set_entry_status(tar_norm, "complete", completed_at=time.time())
      self._touch_manifest("complete", tar_norm=tar_norm)
    except Exception as exc:
      self._set_entry_status(tar_norm, "failed", error=str(exc))
      self.log_fn(
          "janitor: async day_close error tar=%s: %s\n%s"
          % (tar_norm, exc, traceback.format_exc()),
          flush=True,
      )
    finally:
      close_old_connections()
      with self._lock:
        self._futures.pop(tar_norm, None)

  def _seal_day(self, tar_norm: str) -> bool:
    if not daily_tar_seal_calendar_eligible(tar_norm, self.local_tz):
      self.log_fn(
          "janitor: async day_close seal deferred (calendar grace) %s" % tar_norm,
          flush=True,
      )
      return False
    if os.path.isfile(tar_norm):
      if tar_has_duplicate_file_members(tar_norm):
        dedupe_tar_keep_largest_file_per_member(tar_norm, log_fn=self.log_fn)
    else:
      zst_path, gz_path = compressed_sibling_paths(tar_norm)
      sealed_path = zst_path if os.path.isfile(zst_path) else gz_path
      if os.path.isfile(sealed_path):
        dedupe_sealed_daily_archive(sealed_path, log_fn=self.log_fn)
    zst_path, gz_path = compressed_sibling_paths(tar_norm)
    if os.path.isfile(zst_path) and not (
        os.path.isfile(tar_norm) and tar_has_duplicate_file_members(tar_norm)
    ):
      if not os.path.isfile(tar_norm):
        self._notify_phase(tar_norm, "sealed")
        return True
    if not os.path.isfile(tar_norm):
      return bool(os.path.isfile(zst_path) or os.path.isfile(gz_path))
    remaining_raw_by_gz = {
        zst_path: build_remaining_raw_for_daily_tar(
            self.archive_data_dir,
            self.host_name_ext,
            self.tgz_archive_dir,
            tar_norm,
        ),
    }
    keep_tar = effective_keep_uncompressed_tar(
        tar_norm,
        local_tz=self.local_tz,
    )
    async_inflight = len(self.active_or_submitted_tar_paths())
    async_max = max(
        cfg.get_sync_day_close_async_workers(),
        cfg.get_sync_startup_day_close_max_inflight(),
    )
    tar_size_bytes = -1
    try:
      tar_size_bytes = os.path.getsize(tar_norm)
    except OSError:
      pass
    self.log_fn(
        "janitor: async day_close seal start tar=%s async_inflight=%d/%d "
        "zstd_threads=%s ionice=c%s-n%s nice=%s tar_size_bytes=%d"
        % (
            tar_norm,
            async_inflight,
            async_max,
            cfg.get_archive_zstd_threads(),
            cfg.get_archive_zstd_ionice_class(),
            cfg.get_archive_zstd_ionice_level(),
            cfg.get_archive_zstd_nice(),
            int(tar_size_bytes),
        ),
        flush=True,
    )
    atomic_seal_tar_to_zst(
        tar_norm,
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
          "janitor: async day_close seal done tar=%s" % tar_norm,
          flush=True,
      )
      return True
    return False

  def _tar_drop_day(self, tar_norm: str) -> bool:
    remaining_raw_by_gz = {
        compressed_sibling_paths(tar_norm)[0]: build_remaining_raw_for_daily_tar(
            self.archive_data_dir,
            self.host_name_ext,
            self.tgz_archive_dir,
            tar_norm,
        ),
    }
    zst_path, _gz_path = compressed_sibling_paths(tar_norm)
    if daily_gz_has_remaining_raw_stats(zst_path, remaining_raw_by_gz):
      return False
    remove_verified_uncompressed_daily_tars(
        self.tgz_archive_dir,
        log_fn=self.log_fn,
        remaining_raw_by_gz=remaining_raw_by_gz,
        force_remove_uncompressed_tar=False,
        only_daily_tar_paths={tar_norm},
    )
    return True
