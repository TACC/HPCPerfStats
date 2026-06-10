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

from hpcperfstats.dbload.archive_compress import compressed_sibling_paths
from hpcperfstats.dbload.sync_timedb_archive_helpers import (
    atomic_seal_tar_to_zst,
    calendar_date_from_daily_tar_path,
    collect_stats_files_in_range,
    daily_tar_path_for_stats_path,
    daily_tar_seal_calendar_eligible,
    drop_legacy_gz_if_equivalent_to_zst,
    effective_keep_uncompressed_tar,
    is_daily_tar_sealed_dirty,
    iter_daily_tar_paths,
    remaining_raw_paths_by_daily_tar_from_snapshot,
    stats_file_is_active_segment,
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


def _build_remaining_raw_paths_by_daily_tar(
    archive_data_dir: str,
    host_name_ext: str,
    tgz_archive_dir: str,
) -> Dict[str, List[str]]:
  """Closed raw paths grouped by daily ``.tar`` without head-metadata reads."""
  remaining_by_tar: Dict[str, List[str]] = {}
  closed_paths = collect_stats_files_in_range(
      archive_data_dir,
      "all",
      None,
      host_name_ext,
  )
  for path in closed_paths:
    if stats_file_is_active_segment(path):
      continue
    tar_path = daily_tar_path_for_stats_path(
        path,
        tgz_archive_dir,
        first_ts=None,
    )
    if not tar_path:
      continue
    remaining_by_tar.setdefault(os.path.normpath(tar_path), []).append(path)
  return remaining_by_tar


def _any_dirty_daily_tar_on_disk(tgz_archive_dir: str) -> bool:
  for tar_path in iter_daily_tar_paths(tgz_archive_dir):
    if not os.path.isfile(tar_path):
      continue
    zst_path, gz_path = compressed_sibling_paths(tar_path)
    if is_daily_tar_sealed_dirty(tar_path, zst_path, gz_path):
      return True
  return False


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
      has_async_day_close_for_tar: Optional[Callable[[str], bool]] = None,
      get_startup_snapshot: Optional[Callable[[], Any]] = None,
      process_title: str = "sync_timedb.py",
  ):
    self.archive_data_dir = archive_data_dir
    self.host_name_ext = host_name_ext
    self.tgz_archive_dir = tgz_archive_dir
    self.local_tz = local_tz
    self.log_fn = log_fn
    self.has_active_append_for_tar = has_active_append_for_tar
    self.has_async_day_close_for_tar = has_async_day_close_for_tar
    self.get_startup_snapshot = get_startup_snapshot
    self.process_title = process_title
    self._manifest_path = manifest_path(archive_data_dir)
    self._lock = threading.Lock()
    self._manifest = _load_manifest(self._manifest_path)
    self._executor: Optional[ThreadPoolExecutor] = None
    self._seal_future = None
    self._remaining_by_tar: Optional[Dict[str, List[str]]] = None
    self._last_full_discover_at: Optional[float] = None
    self._remaining_signature: Optional[tuple] = None
    self._discover_complete = False
    self.enabled = cfg.get_sync_startup_tar_seal_preflight()

  def phase(self) -> str:
    with self._lock:
      return str(self._manifest.get("phase") or PHASE_SEALING)

  def seal_pass_done(self) -> bool:
    return self.phase() == PHASE_DONE

  def start_async_seal(self) -> None:
    if not self.enabled:
      if self.log_fn:
        self.log_fn(
            "sync_timedb: startup tar seal preflight disabled",
            flush=True,
        )
      return
    with self._lock:
      phase = str(self._manifest.get("phase") or "")
      if phase == PHASE_DONE:
        if _any_dirty_daily_tar_on_disk(self.tgz_archive_dir):
          if self.log_fn:
            self.log_fn(
                "sync_timedb: startup tar seal resuming (dirty daily .tar remain)",
                flush=True,
            )
          self._manifest["phase"] = PHASE_SEALING
          self._manifest.pop("completed_at", None)
          self._manifest["pending_tar_paths"] = []
          phase = PHASE_SEALING
        else:
          if self.log_fn:
            self.log_fn(
                "sync_timedb: startup tar seal skipped (no dirty daily .tar)",
                flush=True,
            )
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
      self._seal_future.add_done_callback(self._on_seal_future_done)
    if self.log_fn:
      self.log_fn(
          "sync_timedb: startup tar seal pass started",
          flush=True,
      )

  def shutdown(self, wait: bool = True) -> None:
    executor = self._executor
    if executor is None:
      return
    executor.shutdown(wait=wait)

  def _on_seal_future_done(self, future) -> None:
    try:
      exc = future.exception()
    except Exception as callback_exc:
      exc = callback_exc
    if exc is None:
      return
    self._touch_manifest_progress("thread_failed", detail=str(exc))
    if self.log_fn:
      self.log_fn(
          "sync_timedb: startup tar seal thread failed err=%s" % exc,
          flush=True,
      )

  def _touch_manifest_progress(
      self,
      stage: str,
      *,
      detail: str = "",
  ) -> None:
    with self._lock:
      self._manifest["last_progress"] = stage
      self._manifest["last_progress_at"] = time.time()
      if detail:
        self._manifest["last_progress_detail"] = detail
      _save_manifest(self._manifest_path, self._manifest)

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

  def _resolve_remaining_by_tar(self) -> Dict[str, List[str]]:
    getter = self.get_startup_snapshot
    if getter is not None:
      try:
        snapshot = getter()
      except Exception:
        snapshot = None
      if snapshot is not None:
        return remaining_raw_paths_by_daily_tar_from_snapshot(
            snapshot,
            self.tgz_archive_dir,
        )
    return _build_remaining_raw_paths_by_daily_tar(
        self.archive_data_dir,
        self.host_name_ext,
        self.tgz_archive_dir,
    )

  def _remaining_signature_key(self, remaining_by_tar: Dict[str, List[str]]) -> tuple:
    items = []
    for tar_norm, paths in sorted((remaining_by_tar or {}).items()):
      items.append((tar_norm, len(paths)))
    return tuple(items)

  def _live_remaining_by_tar(self) -> Dict[str, List[str]]:
    getter = self.get_startup_snapshot
    if getter is not None:
      try:
        snapshot = getter()
      except Exception:
        snapshot = None
      if snapshot is not None:
        return remaining_raw_paths_by_daily_tar_from_snapshot(
            snapshot,
            self.tgz_archive_dir,
        )
    return _build_remaining_raw_paths_by_daily_tar(
        self.archive_data_dir,
        self.host_name_ext,
        self.tgz_archive_dir,
    )

  def _needs_full_rediscover(self) -> bool:
    if not self._discover_complete:
      return True
    interval = cfg.get_sync_startup_tar_seal_rediscover_interval_seconds()
    if interval <= 0:
      return False
    if self._last_full_discover_at is None:
      return True
    if time.time() - self._last_full_discover_at < interval:
      return False
    remaining = self._live_remaining_by_tar()
    sig = self._remaining_signature_key(remaining)
    return sig != self._remaining_signature

  def _discover_pending_tar_paths(self, *, full_scan: bool = True) -> List[str]:
    pending = []
    skip_counts: Dict[str, int] = {}
    discover_started = time.time()
    self._touch_manifest_progress("discover_begin")
    if self.log_fn:
      self.log_fn(
          "sync_timedb: startup tar seal discover begin full_scan=%s"
          % full_scan,
          flush=True,
      )
    if full_scan:
      self._remaining_by_tar = self._resolve_remaining_by_tar()
      self._remaining_signature = self._remaining_signature_key(self._remaining_by_tar)
      self._last_full_discover_at = time.time()
      self._discover_complete = True
    elif self._remaining_by_tar is None:
      self._remaining_by_tar = self._resolve_remaining_by_tar()
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
        skip_counts["already_sealed"] = skip_counts.get("already_sealed", 0) + 1
        continue
      if not daily_tar_seal_calendar_eligible(tar_path, self.local_tz):
        self._record_entry(
            tar_norm,
            "skipped_calendar_grace",
            "calendar_today_grace",
        )
        skip_counts["calendar_grace"] = skip_counts.get("calendar_grace", 0) + 1
        continue
      if (self._remaining_by_tar or {}).get(tar_norm):
        self._record_entry(tar_norm, "skipped_remaining_raw", "raw_on_disk")
        skip_counts["remaining_raw"] = skip_counts.get("remaining_raw", 0) + 1
        continue
      if self.has_active_append_for_tar(tar_norm):
        self._record_entry(tar_norm, "skipped_active_append", "active_append")
        skip_counts["active_append"] = skip_counts.get("active_append", 0) + 1
        continue
      if (
          self.has_async_day_close_for_tar is not None
          and self.has_async_day_close_for_tar(tar_norm)
      ):
        self._record_entry(
            tar_norm,
            "skipped_async_day_close",
            "async_day_close_submitted",
        )
        skip_counts["async_day_close"] = skip_counts.get("async_day_close", 0) + 1
        continue
      pending.append(tar_norm)
    self._touch_manifest_progress(
        "discover_done",
        detail="pending=%d skips=%s"
        % (len(pending), skip_counts or "{}"),
    )
    if self.log_fn:
      self.log_fn(
          "sync_timedb: startup tar seal discover done pending=%d skips=%s "
          "elapsed_s=%d"
          % (
              len(pending),
              skip_counts or "{}",
              int(time.time() - discover_started),
          ),
          flush=True,
      )
    return pending

  def _seal_one_tar(self, tar_path: str) -> bool:
    tar_norm = os.path.normpath(tar_path)
    zst_path, gz_path = compressed_sibling_paths(tar_norm)
    if self._remaining_by_tar is None:
      self._remaining_by_tar = self._resolve_remaining_by_tar()
    if (self._remaining_by_tar or {}).get(tar_norm):
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
      if self._remaining_by_tar is not None:
        self._remaining_by_tar.pop(tar_norm, None)
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
    if pending:
      if self.log_fn:
        self.log_fn(
            "sync_timedb: startup tar seal resuming pending=%d"
            % len(pending),
            flush=True,
        )
    if not pending:
      if self._needs_full_rediscover():
        pending = self._discover_pending_tar_paths(full_scan=True)
      else:
        pending = []
      with self._lock:
        self._manifest["pending_tar_paths"] = pending
        _save_manifest(self._manifest_path, self._manifest)
      if not pending:
        blocked_skips = 0
        blocked_remaining_raw = 0
        blocked_async_day_close = 0
        dirty_remain = 0
        for tar_path in iter_daily_tar_paths(self.tgz_archive_dir):
          if not os.path.isfile(tar_path):
            continue
          zst_path, gz_path = compressed_sibling_paths(tar_path)
          if not is_daily_tar_sealed_dirty(tar_path, zst_path, gz_path):
            continue
          dirty_remain += 1
          tar_norm = os.path.normpath(tar_path)
          entry = self._manifest.get("entries", {}).get(tar_norm)
          if not isinstance(entry, dict):
            continue
          status = str(entry.get("status", ""))
          if not status.startswith("skipped"):
            continue
          blocked_skips += 1
          if status == "skipped_remaining_raw":
            blocked_remaining_raw += 1
          elif status == "skipped_async_day_close":
            blocked_async_day_close += 1
        with self._lock:
          self._manifest["phase"] = PHASE_DONE
          self._manifest["completed_at"] = time.time()
          _save_manifest(self._manifest_path, self._manifest)
        if self.log_fn:
          self.log_fn(
              "sync_timedb: startup tar seal pass complete actionable=0 "
              "blocked_skips=%d blocked_remaining_raw=%d "
              "blocked_async_day_close=%d dirty_remain=%d sealed=%d "
              "skipped=%d failed=%d"
              % (
                  blocked_skips,
                  blocked_remaining_raw,
                  blocked_async_day_close,
                  dirty_remain,
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
        if self.log_fn:
          self.log_fn(
              "sync_timedb: startup tar seal slice budget reached "
              "processed=%d pending_left=%d budget_s=%.0f"
              % (processed_days, len(pending), budget),
              flush=True,
          )
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
              "sync_timedb: startup tar seal pass complete actionable=0 "
              "blocked_skips=0 blocked_remaining_raw=0 "
              "blocked_async_day_close=0 dirty_remain=0 sealed=%d "
              "skipped=%d failed=%d"
              % (
                  int(self._manifest.get("sealed_count", 0)),
                  int(self._manifest.get("skipped_count", 0)),
                  int(self._manifest.get("failed_count", 0)),
              ),
              flush=True,
          )
      _save_manifest(self._manifest_path, self._manifest)
    if not pending:
      return True
    return False

  def _seal_loop(self) -> None:
    with self._lock:
      phase = str(self._manifest.get("phase") or "")
      pending_n = len(self._manifest.get("pending_tar_paths") or [])
    self._touch_manifest_progress(
        "thread_running",
        detail="phase=%s manifest_pending=%d" % (phase, pending_n),
    )
    set_daemon_thread_title(
        "",
        script_name=self.process_title,
        role="startup-tar-seal-preflight",
    )
    try:
      if self.log_fn:
        self.log_fn(
            "sync_timedb: startup tar seal thread running phase=%s "
            "manifest_pending=%d"
            % (phase, pending_n),
            flush=True,
        )
      if phase == PHASE_DONE:
        return
      while not shutdown_requested[0]:
        if self._seal_slice():
          break
        interval = cfg.get_sync_startup_tar_seal_rediscover_interval_seconds()
        sleep_s = interval if interval > 0 else 1.0
        if self.log_fn:
          self.log_fn(
              "sync_timedb: startup tar seal idle backoff interval_s=%.0f"
              % sleep_s,
              flush=True,
          )
        time.sleep(sleep_s)
    except Exception as exc:
      self._touch_manifest_progress("thread_failed", detail=str(exc))
      if self.log_fn:
        self.log_fn(
            "sync_timedb: startup tar seal thread failed err=%s" % exc,
            flush=True,
        )
      raise
