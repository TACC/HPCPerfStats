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
    days_quiescent_tar_needs_day_close_at_startup,
    log_day_close_candidate_report,
)
from hpcperfstats.dbload.sync_timedb_async_day_close import AsyncDayCloseCoordinator
from hpcperfstats.dbload.sync_timedb_persistence import (
    load_persistence_document,
    save_persistence_document,
)
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
      "discover_slice_count": 0,
      "last_progress": "",
      "last_progress_at": None,
      "entries": {},
      "pending_eligible": [],
      "pending_retry": [],
  }


def _load_manifest(path: str) -> Dict[str, Any]:
  payload = load_persistence_document(path, "startup_day_close", default=None)
  if not isinstance(payload, dict):
    return _new_manifest()
  payload.setdefault("version", MANIFEST_VERSION)
  payload.setdefault("entries", {})
  payload.setdefault("pending_eligible", [])
  payload.setdefault("pending_retry", [])
  payload.setdefault("discover_slice_count", 0)
  return payload


def _save_manifest(path: str, payload: Dict[str, Any]) -> None:
  save_persistence_document(path, "startup_day_close", payload)


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
      get_startup_snapshot: Optional[Callable[[], Any]] = None,
      get_accrual_remaining_raw_by_gz: Optional[Callable[[], Optional[Dict]]] = None,
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
    self.get_startup_snapshot = get_startup_snapshot
    self.get_accrual_remaining_raw_by_gz = get_accrual_remaining_raw_by_gz
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

  def _resolve_snapshot(self):
    getter = self.get_startup_snapshot
    if getter is None:
      return None
    try:
      return getter()
    except Exception:
      return None

  def _resolve_remaining_raw_by_gz(self, snapshot, slice_index: int):
    if slice_index >= 1:
      accrual_getter = self.get_accrual_remaining_raw_by_gz
      if accrual_getter is not None:
        try:
          accrual_remaining = accrual_getter()
        except Exception:
          accrual_remaining = None
        if accrual_remaining is not None:
          return dict(accrual_remaining)
    return build_remaining_raw_stats_by_daily_gz(
        self.archive_data_dir,
        self.host_name_ext,
        self.tgz_archive_dir,
        maintenance_snapshot=snapshot,
    )

  def _discover_slice(self) -> bool:
    """Run one discover+submit slice. Return False when discover loop should stop."""
    max_inflight = cfg.get_sync_day_close_max_inflight()
    active_count = len(self.async_day_close.active_or_submitted_tar_paths())
    if active_count >= max_inflight:
      self._touch_manifest(
          "discover_backoff_async_saturated",
          detail="active=%d max=%d" % (active_count, max_inflight),
      )
      with self._lock:
        pending_eligible = list(self._manifest.get("pending_eligible") or [])
        pending_retry = list(self._manifest.get("pending_retry") or [])
        self._manifest["last_slice_backoff"] = True
        _save_manifest(self._manifest_path, self._manifest)
      if self.log_fn:
        self.log_fn(
            "sync_timedb: startup day close discover backoff async saturated "
            "active=%d max=%d pending_eligible=%d pending_retry=%d"
            % (
                active_count,
                max_inflight,
                len(pending_eligible),
                len(pending_retry),
            ),
            flush=True,
        )
      has_deferrals = bool(pending_eligible or pending_retry)
      return has_deferrals and not shutdown_requested[0]

    budget_s = cfg.get_sync_startup_day_close_budget_seconds()
    days_per_slice = cfg.get_sync_startup_day_close_days_per_slice()
    scan_warn_s = cfg.get_sync_startup_day_close_scan_budget_seconds()
    scan_t0 = time.time()
    self._touch_manifest("discover_slice_begin")
    if self.log_fn:
      self.log_fn(
          "sync_timedb: startup day close discover slice begin",
          flush=True,
      )
    with self._lock:
      slice_index = int(self._manifest.get("discover_slice_count", 0))
    snapshot = self._resolve_snapshot()
    unprocessed_by_tar = build_unprocessed_raw_by_daily_tar(
        self.archive_data_dir,
        self.host_name_ext,
        self.tgz_archive_dir,
        checkpoint_path=self._checkpoint_path,
        maintenance_snapshot=snapshot,
    )
    captured = self.get_disqualification_inputs()
    unprocessed_by_tar = augment_unprocessed_by_tar_with_pending_paths(
        unprocessed_by_tar,
        pending_stats_paths=captured.get("pending_stats_paths"),
        tgz_archive_dir=self.tgz_archive_dir,
        checkpoint_path=self._checkpoint_path,
        first_timestamp_by_path=(
            snapshot.first_timestamp_by_path if snapshot is not None else None
        ),
    )
    remaining = self._resolve_remaining_raw_by_gz(snapshot, slice_index)
    scan_elapsed = time.time() - scan_t0
    if scan_warn_s > 0 and scan_elapsed >= scan_warn_s and self.log_fn:
      self.log_fn(
          "sync_timedb: startup day close discover scans slow elapsed_s=%.3f "
          "warn_threshold_s=%.0f"
          % (scan_elapsed, scan_warn_s),
          flush=True,
      )
    if self.log_fn:
      self.log_fn(
          "sync_timedb: startup day close discover: scans elapsed_s=%.3f"
          % scan_elapsed,
          flush=True,
      )
    submit_t0 = time.time()
    phases = self.day_phases()
    eligible = days_ingest_complete_by_checkpoint(
        unprocessed_by_tar,
        tgz_archive_dir=self.tgz_archive_dir,
        day_phases=phases,
        remaining_raw_by_gz=remaining,
        local_tz=self.local_tz,
    )
    with self._lock:
      pending_resume = list(self._manifest.get("pending_eligible") or [])
      pending_retry = list(self._manifest.get("pending_retry") or [])
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
    disq_t0 = time.time()
    disqualified = set(self.async_day_close.get_disqualified_daily_tars())
    disq_elapsed_s = time.time() - disq_t0
    quiescent_eligible = days_quiescent_tar_needs_day_close_at_startup(
        unprocessed_by_tar,
        tgz_archive_dir=self.tgz_archive_dir,
        checkpoint_complete_eligible=eligible,
        remaining_raw_by_gz=remaining,
        day_phases=phases,
        local_tz=self.local_tz,
        disqualified_daily_tars=disqualified,
        archive_data_dir=self.archive_data_dir,
        host_name_ext=self.host_name_ext,
        maintenance_snapshot=snapshot,
    )
    checkpoint_set = set(eligible)
    all_eligible_set = checkpoint_set | set(quiescent_eligible)
    reason_by_tar = {
        tar_norm: "startup_checkpoint_complete" for tar_norm in eligible
    }
    for tar_norm in quiescent_eligible:
      reason_by_tar.setdefault(tar_norm, "startup_quiescent_tar")
    submit_pairs: List[tuple[str, str]] = []
    seen_submit: Set[str] = set()

    def _append_submit_tar(tar_norm: str) -> None:
      if tar_norm in seen_submit:
        return
      reason = reason_by_tar.get(tar_norm)
      if not reason or tar_norm not in all_eligible_set:
        return
      seen_submit.add(tar_norm)
      submit_pairs.append((tar_norm, reason))

    if pending_resume or pending_retry:
      for tar_list in (pending_retry, pending_resume, eligible, quiescent_eligible):
        for tar_norm in tar_list:
          _append_submit_tar(tar_norm)
    else:
      for tar_norm in sorted(eligible, key=_tar_sort_key):
        _append_submit_tar(tar_norm)
      for tar_norm in sorted(quiescent_eligible, key=_tar_sort_key):
        _append_submit_tar(tar_norm)
    if self.log_fn:
      self.log_fn(
          "sync_timedb: startup day close discover: eligible=%d quiescent_eligible=%d"
          % (len(eligible), len(quiescent_eligible)),
          flush=True,
      )
    submitted: List[str] = []
    skipped_disqualified = 0
    deferred_eligible: List[str] = []
    retry_next_slice: List[str] = []
    if self.log_fn:
      self.log_fn(
          "sync_timedb: startup day close discover_slice: disqualified_n=%d "
          "disq_elapsed_s=%.3f"
          % (len(disqualified), disq_elapsed_s),
          flush=True,
      )
    try:
      for tar_norm, submit_reason in submit_pairs:
        if shutdown_requested[0]:
          break
        if time.time() - submit_t0 >= budget_s:
          deferred_eligible.append(tar_norm)
          continue
        if len(submitted) >= days_per_slice:
          deferred_eligible.append(tar_norm)
          continue
        if len(self.async_day_close.active_or_submitted_tar_paths()) >= max_inflight:
          deferred_eligible.append(tar_norm)
          continue
        if tar_norm in disqualified:
          skipped_disqualified += 1
          retry_next_slice.append(tar_norm)
          if self.log_fn:
            self.log_fn(
                "sync_timedb: startup day close submit skip tar=%s reason=%s"
                % (tar_norm, "disqualified"),
                flush=True,
            )
          continue
        if not self.async_day_close.submit_day_close(
            tar_norm,
            reason=submit_reason,
            disqualified_daily_tars=disqualified,
        ):
          skipped_disqualified += 1
          retry_next_slice.append(tar_norm)
          if self.log_fn:
            self.log_fn(
                "sync_timedb: startup day close submit skip tar=%s reason=%s"
                % (tar_norm, "submit_returned_false"),
                flush=True,
            )
          continue
        submitted.append(tar_norm)
        with self._lock:
          self._manifest.setdefault("entries", {})[tar_norm] = {
              "tar_path": tar_norm,
              "status": "submitted",
              "reason": submit_reason,
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
          queued_reason="startup_checkpoint_discover",
      )
      log_day_close_candidate_report(
          report_entries,
          reason="startup_checkpoint_discover",
          log_fn=self.log_fn,
          async_progress_fn=getattr(
              self.async_day_close,
              "entry_progress_snapshot",
              None,
          ),
      )
    finally:
      with self._lock:
        self._manifest["pending_eligible"] = deferred_eligible
        self._manifest["pending_retry"] = retry_next_slice
        self._manifest["discover_slice_count"] = slice_index + 1
        slice_backoff = (
            len(submitted) == 0
            and len(self.async_day_close.active_or_submitted_tar_paths())
            >= max_inflight
        )
        self._manifest["last_slice_backoff"] = slice_backoff
        _save_manifest(self._manifest_path, self._manifest)
      if self.log_fn:
        self.log_fn(
            "sync_timedb: startup day close discover slice: submitted=%d "
            "skipped_disqualified=%d deferred=%d retry=%d"
            % (
                len(submitted),
                skipped_disqualified,
                len(deferred_eligible),
                len(retry_next_slice),
            ),
            flush=True,
        )
      self._touch_manifest(
          "discover_slice_done",
          detail="submitted=%d deferred=%d" % (len(submitted), len(deferred_eligible)),
      )
    has_deferrals = (
        len(deferred_eligible) > 0
        or len(retry_next_slice) > 0
    )
    return has_deferrals and not shutdown_requested[0]

  def _discover_loop(self) -> None:
    set_daemon_thread_title(
        "",
        script_name=self.process_title,
        role="startup-day-close-preflight",
    )
    self._touch_manifest("discover_begin")
    if self.log_fn:
      self.log_fn(
          "sync_timedb: startup day close discover begin",
          flush=True,
      )
    try:
      while not shutdown_requested[0]:
        has_more = self._discover_slice()
        if not has_more:
          break
        with self._lock:
          use_backoff = bool(self._manifest.get("last_slice_backoff"))
        sleep_s = (
            cfg.get_sync_startup_day_close_backoff_seconds()
            if use_backoff
            else 1.0
        )
        time.sleep(sleep_s)
    finally:
      with self._lock:
        pending_eligible = list(self._manifest.get("pending_eligible") or [])
        pending_retry = list(self._manifest.get("pending_retry") or [])
        shutting_down = shutdown_requested[0]
        if shutting_down and (pending_eligible or pending_retry):
          self._manifest["phase"] = PHASE_DISCOVERING
          self._manifest.pop("completed_at", None)
        else:
          self._manifest["phase"] = PHASE_DONE
          self._manifest["completed_at"] = time.time()
          self._manifest["pending_eligible"] = []
          self._manifest["pending_retry"] = []
        _save_manifest(self._manifest_path, self._manifest)
      if self.log_fn:
        self.log_fn(
            "sync_timedb: startup day close discover done submitted=%d "
            "shutdown=%s pending_eligible=%d pending_retry=%d"
            % (
                int(self._manifest.get("submitted_count", 0)),
                shutdown_requested[0],
                len(self._manifest.get("pending_eligible") or []),
                len(self._manifest.get("pending_retry") or []),
            ),
            flush=True,
        )
