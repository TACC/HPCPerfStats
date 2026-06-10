"""Startup checkpoint-driven async DAY_CLOSE preflight."""
from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Any, Callable, Dict, List, Optional, Set

import hpcperfstats.conf_parser as cfg

from hpcperfstats.dbload.sync_timedb_archive_helpers import (
    augment_unprocessed_by_tar_with_pending_paths,
    build_disqualification_reasons_by_tar,
    build_remaining_raw_stats_by_daily_gz,
    build_unprocessed_raw_by_daily_tar,
    calendar_date_from_daily_tar_path,
    classify_day_close_candidates,
    days_ingest_complete_by_checkpoint,
    log_day_close_candidate_report,
)
from hpcperfstats.dbload.sync_timedb_async_day_close import AsyncDayCloseCoordinator
from hpcperfstats.process_title import set_daemon_thread_title
from hpcperfstats.shutdown_utils import shutdown_requested

MANIFEST_BASENAME = ".sync_timedb_startup_day_close.json"
MANIFEST_VERSION = 1

PHASE_DISCOVERING = "discovering"
PHASE_DONE = "done"


def manifest_path(archive_data_dir: str) -> str:
  return os.path.join(archive_data_dir, MANIFEST_BASENAME)


def _new_manifest() -> Dict[str, Any]:
  return {
      "version": MANIFEST_VERSION,
      "phase": PHASE_DISCOVERING,
      "started_at": time.time(),
      "completed_at": None,
      "submitted_count": 0,
      "last_progress": "",
      "last_progress_at": None,
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


class StartupDayClosePreflight:
  """Async startup discover: checkpoint-complete days → async DAY_CLOSE."""

  def __init__(
      self,
      *,
      archive_data_dir: str,
      host_name_ext: str,
      tgz_archive_dir: str,
      local_tz,
      log_fn,
      async_day_close: AsyncDayCloseCoordinator,
      get_disqualification_inputs: Callable[[], Dict[str, Any]],
      get_unmapped_closed_raw_tars: Callable[[], Set[str]],
      day_phases: Callable[[], Dict[str, Any]],
      process_title: str = "sync_timedb.py",
  ):
    self.archive_data_dir = archive_data_dir
    self.host_name_ext = host_name_ext
    self.tgz_archive_dir = tgz_archive_dir
    self.local_tz = local_tz
    self.log_fn = log_fn
    self.async_day_close = async_day_close
    self.get_disqualification_inputs = get_disqualification_inputs
    self.get_unmapped_closed_raw_tars = get_unmapped_closed_raw_tars
    self.day_phases = day_phases
    self.process_title = process_title
    self._manifest_path = manifest_path(archive_data_dir)
    self._checkpoint_path = os.path.join(
        archive_data_dir,
        ".sync_timedb_state.json",
    )
    self._lock = threading.Lock()
    self._manifest = _load_manifest(self._manifest_path)
    self._executor: Optional[ThreadPoolExecutor] = None
    self._discover_future = None
    self.enabled = cfg.get_sync_startup_day_close_preflight()

  def phase(self) -> str:
    with self._lock:
      return str(self._manifest.get("phase") or PHASE_DISCOVERING)

  def discover_done(self) -> bool:
    return self.phase() == PHASE_DONE

  def start_async_discover_and_close(self) -> None:
    if not self.enabled:
      if self.log_fn:
        self.log_fn(
            "sync_timedb: startup day close preflight disabled",
            flush=True,
        )
      return
    with self._lock:
      if self._manifest.get("phase") == PHASE_DONE:
        if self.log_fn:
          self.log_fn(
              "sync_timedb: startup day close skipped (discover already done)",
              flush=True,
          )
        return
      if not self._manifest.get("started_at"):
        self._manifest["started_at"] = time.time()
      self._manifest["phase"] = PHASE_DISCOVERING
      _save_manifest(self._manifest_path, self._manifest)
      if self._executor is not None:
        return
      self._executor = ThreadPoolExecutor(max_workers=1)
      self._discover_future = self._executor.submit(self._discover_loop)
      self._discover_future.add_done_callback(self._on_discover_future_done)
    if self.log_fn:
      self.log_fn(
          "sync_timedb: startup day close discover started",
          flush=True,
      )

  def shutdown(self, wait: bool = True) -> None:
    executor = self._executor
    if executor is None:
      return
    executor.shutdown(wait=wait)

  def _on_discover_future_done(self, future) -> None:
    try:
      exc = future.exception()
    except Exception as callback_exc:
      exc = callback_exc
    if exc is None:
      return
    self._touch_manifest("thread_failed", detail=str(exc))
    if self.log_fn:
      self.log_fn(
          "sync_timedb: startup day close thread failed err=%s" % exc,
          flush=True,
      )

  def _touch_manifest(self, stage: str, *, detail: str = "") -> None:
    with self._lock:
      self._manifest["last_progress"] = stage
      self._manifest["last_progress_at"] = time.time()
      if detail:
        self._manifest["last_progress_detail"] = detail
      _save_manifest(self._manifest_path, self._manifest)

  def _discover_loop(self) -> None:
    set_daemon_thread_title(
        "",
        script_name=self.process_title,
        role="startup-day-close-preflight",
    )
    budget_s = cfg.get_sync_startup_day_close_budget_seconds()
    days_per_slice = cfg.get_sync_startup_day_close_days_per_slice()
    slice_t0 = time.time()
    self._touch_manifest("discover_begin")
    if self.log_fn:
      self.log_fn(
          "sync_timedb: startup day close discover begin",
          flush=True,
      )
    unprocessed_by_tar = build_unprocessed_raw_by_daily_tar(
        self.archive_data_dir,
        self.host_name_ext,
        self.tgz_archive_dir,
        checkpoint_path=self._checkpoint_path,
    )
    captured = self.get_disqualification_inputs()
    unprocessed_by_tar = augment_unprocessed_by_tar_with_pending_paths(
        unprocessed_by_tar,
        pending_stats_paths=captured.get("pending_stats_paths"),
        tgz_archive_dir=self.tgz_archive_dir,
        checkpoint_path=self._checkpoint_path,
    )
    remaining = build_remaining_raw_stats_by_daily_gz(
        self.archive_data_dir,
        self.host_name_ext,
        self.tgz_archive_dir,
    )
    phases = self.day_phases()
    eligible = days_ingest_complete_by_checkpoint(
        unprocessed_by_tar,
        tgz_archive_dir=self.tgz_archive_dir,
        day_phases=phases,
        remaining_raw_by_gz=remaining,
        local_tz=self.local_tz,
    )
    disq_reasons = build_disqualification_reasons_by_tar(
        tgz_archive_dir=self.tgz_archive_dir,
        inflight_paths=captured.get("inflight_paths"),
        pending_append_by_daily_tar=captured.get("pending_append_by_daily_tar"),
        in_flight_archive_tars=captured.get("in_flight_archive_tars"),
        pending_archive_task_tars=captured.get("pending_archive_task_tars"),
        unmapped_closed_raw_tars=self.get_unmapped_closed_raw_tars(),
        unprocessed_by_tar=unprocessed_by_tar,
        local_tz=self.local_tz,
    )
    submitted: List[str] = []
    for tar_norm in eligible:
      if shutdown_requested[0]:
        break
      if time.time() - slice_t0 >= budget_s:
        break
      if len(submitted) >= days_per_slice:
        break
      if tar_norm in self.async_day_close.get_disqualified_daily_tars():
        continue
      if self.async_day_close.submit_day_close(
          tar_norm,
          reason="startup_checkpoint_complete",
      ):
        submitted.append(tar_norm)
        with self._lock:
          self._manifest.setdefault("entries", {})[tar_norm] = {
              "tar_path": tar_norm,
              "status": "submitted",
              "reason": "startup_checkpoint_complete",
          }
          self._manifest["submitted_count"] = int(
              self._manifest.get("submitted_count", 0)) + 1
          _save_manifest(self._manifest_path, self._manifest)
    report_entries = classify_day_close_candidates(
        tgz_archive_dir=self.tgz_archive_dir,
        remaining_raw_by_gz=remaining,
        unprocessed_by_tar=unprocessed_by_tar,
        disqualification_reasons=disq_reasons,
        day_phases=phases,
        local_tz=self.local_tz,
        async_in_progress_tars=self.async_day_close.active_or_submitted_tar_paths(),
        newly_queued_tars=set(submitted),
        queued_reason="startup_checkpoint_complete",
    )
    log_day_close_candidate_report(
        report_entries,
        reason="startup_checkpoint_discover",
        log_fn=self.log_fn,
    )
    self._touch_manifest("discover_done", detail="submitted=%d" % len(submitted))
    with self._lock:
      self._manifest["phase"] = PHASE_DONE
      self._manifest["completed_at"] = time.time()
      _save_manifest(self._manifest_path, self._manifest)
    if self.log_fn:
      self.log_fn(
          "sync_timedb: startup day close discover done submitted=%d"
          % len(submitted),
          flush=True,
      )
