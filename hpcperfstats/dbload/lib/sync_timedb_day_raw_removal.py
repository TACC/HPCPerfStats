"""
Per-day post-seal raw removal: async verify; ingest-thread batched delete.

Attributes:
  KICK_NO_HANDOFF_PROGRESS: Attribute.
  MANIFEST_SUBDIR: Attribute.
  MANIFEST_VERSION: Attribute.
  QUARANTINE_SKIP_REASONS: Attribute.
  QUARANTINE_SKIP_STATUSES: Attribute.
  RETRYABLE_SKIP_REASONS: Attribute.
  RETRYABLE_SKIP_STATUSES: Attribute.
  VERIFY_STAGE_NONE: Attribute.
  VERIFY_STAGE_POST_SEAL: Attribute.
  VERIFY_STAGE_PRE_SEAL: Attribute.
"""
from __future__ import annotations

import os
import threading
import time
from datetime import date
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import hpcperfstats.dbload.lib.conf_parser as cfg
from django.db import close_old_connections

from hpcperfstats.dbload.lib.archive_compress import compressed_sibling_paths
from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
    build_remaining_raw_for_daily_tar,
    calendar_date_from_daily_tar_path,
    classify_removable_raw_paths_for_daily_gz,
    classify_removable_raw_paths_for_open_tar,
    ensure_daily_tar_restored_for_append,
    filter_remaining_raw_aligned_to_tar,
    quarantine_dir_for_archive,
    remaining_raw_by_gz_has_paths_on_disk,
    remove_verified_uncompressed_daily_tars,
    stats_file_is_active_segment,
    stats_path_aligned_to_daily_tar,
    validate_open_tar_for_raw_removal,
    validate_post_seal_tar_zst_parity,
)
from hpcperfstats.dbload.lib.print_utils import janitorial_logging
from hpcperfstats.dbload.lib.sync_timedb_ingest_readiness import (
    filter_paths_head_ingested,
)
from hpcperfstats.dbload.lib.sync_timedb_persistence import (
    load_persistence_document,
    save_persistence_document,
)
from hpcperfstats.dbload.lib.file_locking import cleanup_orphan_fnctl_lock_sidecars, try_file_write_lock
from hpcperfstats.dbload.lib.shutdown_utils import shutdown_requested
from hpcperfstats.dbload.lib.sync_timedb_session_executor import (
    SessionSingleFlightExecutor,
    iter_bounded_thread_pool,
)

MANIFEST_VERSION = 1
MANIFEST_SUBDIR = ".sync_timedb_day_raw_removal"

from hpcperfstats.dbload.lib.sync_timedb_manifest_contract import (
    PHASE_DELETING,
    PHASE_DONE,
    PHASE_VERIFICATION_COMPLETE,
    PHASE_VERIFYING,
)

RETRYABLE_SKIP_REASONS = frozenset({
    "not_head_tail_ingested",
    "not_sample_ingested",  # legacy manifests
    "not_head_ingested",  # legacy manifests
    "not_in_sealed_archive",
    "size_mismatch",
})
RETRYABLE_SKIP_STATUSES = frozenset({
    "skipped_not_head_tail_ingested",
    "skipped_not_sample_ingested",  # legacy manifests
    "skipped_not_head_ingested",  # legacy manifests
    "skipped_not_in_archive",
    "skipped_size_mismatch",
})
QUARANTINE_SKIP_REASONS = frozenset({"quarantine"})
QUARANTINE_SKIP_STATUSES = frozenset({"skipped_quarantine"})
KICK_NO_HANDOFF_PROGRESS = frozenset({"noop", "quarantine_terminal"})

VERIFY_STAGE_NONE = "none"
VERIFY_STAGE_PRE_SEAL = "pre_seal_complete"
VERIFY_STAGE_POST_SEAL = "post_seal_complete"


def day_removal_manifest_dir(archive_data_dir: str) -> str:
  """
  Day removal manifest dir.
  
  Args:
    archive_data_dir (str): String for archive data dir.
  
  Returns:
    str: str produced by this call.
  
  Examples:
    >>> day_removal_manifest_dir("x")  # doctest: +SKIP
  """
  return os.path.join(archive_data_dir, MANIFEST_SUBDIR)


def day_removal_manifest_path(archive_data_dir: str, day_date: date) -> str:
  """
  Day removal manifest path.
  
  Args:
    archive_data_dir (str): String for archive data dir.
    day_date (date): Day date.
  
  Returns:
    str: str produced by this call.
  
  Examples:
    >>> day_removal_manifest_path("x", None)  # doctest: +SKIP
  """
  return os.path.join(
      day_removal_manifest_dir(archive_data_dir),
      "%s.json" % day_date.isoformat(),
  )


def _path_fingerprint(path: str) -> Optional[Dict[str, int]]:
  """
  Internal helper to handle path fingerprint.
  
  Args:
    path (str): String for path.
  
  Returns:
    Optional[Dict[str, int]]: Optional[Dict[str, int]] — the result, or None
    when unavailable.
  
  Examples:
    >>> _path_fingerprint("x")  # doctest: +SKIP
  """
  try:
    st = os.stat(path)
    return {"mtime": int(st.st_mtime_ns), "size": int(st.st_size)}
  except OSError:
    return None


def _new_manifest(tar_path: str) -> Dict[str, Any]:
  """
  Internal helper to handle new manifest.
  
  Args:
    tar_path (str): String for tar path.
  
  Returns:
    Dict[str, Any]: Dict[str, Any] produced by this call.
  
  Examples:
    >>> _new_manifest("x")  # doctest: +SKIP
  """
  return {
      "version": MANIFEST_VERSION,
      "tar_path": os.path.normpath(tar_path),
      "phase": PHASE_VERIFYING,
      "verify_stage": VERIFY_STAGE_NONE,
      "worker_stage": "",
      "members_done": 0,
      "members_total": 0,
      "last_progress_ts": None,
      "started_at": time.time(),
      "completed_at": None,
      "verified_count": 0,
      "skipped_count": 0,
      "deleted_count": 0,
      "entries": {},
  }


def _load_manifest(path: str, tar_path: str) -> Dict[str, Any]:
  """
  Internal helper to load the manifest.
  
  Args:
    path (str): String for path.
    tar_path (str): String for tar path.
  
  Returns:
    Dict[str, Any]: Dict[str, Any] produced by this call.
  
  Examples:
    >>> _load_manifest("x", "x")  # doctest: +SKIP
  """
  payload = load_persistence_document(path, "day_raw_removal", default=None)
  if not isinstance(payload, dict):
    return _new_manifest(tar_path)
  payload.setdefault("version", MANIFEST_VERSION)
  payload.setdefault("entries", {})
  payload.setdefault("tar_path", os.path.normpath(tar_path))
  payload.setdefault("verify_stage", VERIFY_STAGE_NONE)
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
  save_persistence_document(path, "day_raw_removal", payload)


def _entry_fingerprint(entry: Dict[str, Any]) -> Optional[Dict[str, int]]:
  """
  Internal helper to handle entry fingerprint.
  
  Args:
    entry (Dict[str, Any]): Mapping for entry.
  
  Returns:
    Optional[Dict[str, int]]: Optional[Dict[str, int]] — the result, or None
    when unavailable.
  
  Examples:
    >>> _entry_fingerprint({})  # doctest: +SKIP
  """
  if "mtime" not in entry or "size" not in entry:
    return None
  return {"mtime": int(entry["mtime"]), "size": int(entry["size"])}


class _DayRawRemovalState:
  """
  Per-calendar-day verify/delete state backed by a JSON manifest.
  
  Attributes:
    _closed_raw_pass_memo: Attribute.
    _closed_raw_pass_memo_active: Attribute.
    _closed_raw_paths_pass_memo: Attribute.
    _lock: Attribute.
    _manifest: Attribute.
    _manifest_path: Attribute.
    _pipeline_future: Attribute.
    _validation_cache: Attribute.
    _verify_sealed_members: Attribute.
    archive_data_dir: Attribute.
    classify_quarantine_skip_path: Attribute.
    day_date: Attribute.
    get_allow_day_scoped_closed_raw: Attribute.
    get_ingest_active_skip_paths: Attribute.
    get_maintenance_snapshot: Attribute.
    get_quarantine_skip_paths: Attribute.
    host_name_ext: Attribute.
    ingest_ready_fn: Attribute.
    log_fn: Attribute.
    tar_path: Attribute.
    tgz_archive_dir: Attribute.
  """

  def __init__(
    self,
    *,
    tar_path: str,
    archive_data_dir: str,
    host_name_ext: str,
    tgz_archive_dir: str,
    log_fn: Any,
    get_quarantine_skip_paths: Callable[[], Set[str]],
    ingest_ready_fn: Optional[Callable[[str], bool]] = None,
    get_maintenance_snapshot: Optional[Callable[[], Any]] = None,
    get_ingest_active_skip_paths: Optional[Callable[[], Set[str]]] = None,
    classify_quarantine_skip_path: Optional[Callable[[str], str]] = None,
    get_allow_day_scoped_closed_raw: Optional[Callable[[], bool]] = None,
  ) -> None:
    """
    Initialize a new instance.
    
    Args:
      tar_path (str): String for tar path.
      archive_data_dir (str): String for archive data dir.
      host_name_ext (str): String for host name ext.
      tgz_archive_dir (str): String for tgz archive dir.
      log_fn (Any): Callable invoked by this helper.
      get_quarantine_skip_paths (Callable[[], Set[str]]): Get quarantine skip
      paths.
      ingest_ready_fn (Optional[Callable[[str], bool]]): Ingest ready fn, or
      None when absent.
      get_maintenance_snapshot (Optional[Callable[[], Any]]): Get maintenance
      snapshot, or None when absent.
      get_ingest_active_skip_paths (Optional[Callable[[], Set[str]]]): Get
      ingest active skip paths, or None when absent.
      classify_quarantine_skip_path (Optional[Callable[[str], str]]): Classify
      quarantine skip path, or None when absent.
      get_allow_day_scoped_closed_raw (Optional[Callable[[], bool]]): Get
      allow day scoped closed raw, or None when absent.
    
    Returns:
      None
    
    Raises:
      ValueError: Raised when ``__init__`` hits a ``ValueError`` failure path.
    
    Examples:
      >>> __init__(0)  # doctest: +SKIP
    """
    self.tar_path = os.path.normpath(tar_path)
    self.archive_data_dir = archive_data_dir
    self.host_name_ext = host_name_ext
    self.tgz_archive_dir = tgz_archive_dir
    self.log_fn = log_fn
    self.get_quarantine_skip_paths = get_quarantine_skip_paths
    self.ingest_ready_fn = ingest_ready_fn
    self.get_maintenance_snapshot = get_maintenance_snapshot
    self.get_ingest_active_skip_paths = get_ingest_active_skip_paths
    self.classify_quarantine_skip_path = classify_quarantine_skip_path
    self.get_allow_day_scoped_closed_raw = (
        get_allow_day_scoped_closed_raw or (lambda: True)
    )
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
    # Memoize day-scoped closed_raw within one apply_batch_delete / handoff
    # pass (soak: uncached rebuild logged hundreds of times per tick).
    self._closed_raw_pass_memo: Optional[Dict[str, List[str]]] = None
    self._closed_raw_paths_pass_memo: Optional[List[str]] = None
    self._closed_raw_pass_memo_active: bool = False

  def phase(self) -> str:
    """
    Return the current phase for this object.
    
    Returns:
      str: str produced by this call.
    
    Examples:
      >>> _DayRawRemovalState().phase()  # doctest: +SKIP
    """
    with self._lock:
      return str(self._manifest.get("phase") or PHASE_VERIFYING)

  def verify_stage(self) -> str:
    """
    Verify stage.
    
    Returns:
      str: str produced by this call.
    
    Examples:
      >>> _DayRawRemovalState().verify_stage()  # doctest: +SKIP
    """
    with self._lock:
      return str(self._manifest.get("verify_stage") or VERIFY_STAGE_NONE)

  def pre_seal_verification_complete(self) -> bool:
    """
    Pre seal verification complete.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> _DayRawRemovalState().pre_seal_verification_complete()  # doctest: +SKIP
    """
    stage = self.verify_stage()
    if stage in (VERIFY_STAGE_PRE_SEAL, VERIFY_STAGE_POST_SEAL):
      return True
    # Legacy manifests from post-seal-only verify before verify-before-seal.
    return self.phase() in (
        PHASE_VERIFICATION_COMPLETE,
        PHASE_DELETING,
        PHASE_DONE,
    )

  def post_seal_verification_complete(self) -> bool:
    """
    Post seal verification complete.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> _DayRawRemovalState().post_seal_verification_complete()
    """
    return self.verify_stage() == VERIFY_STAGE_POST_SEAL

  def verification_complete(self) -> bool:
    """
    Verification complete.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> _DayRawRemovalState().verification_complete()  # doctest: +SKIP
    """
    return self.phase() in (
        PHASE_VERIFICATION_COMPLETE,
        PHASE_DELETING,
        PHASE_DONE,
    )

  def needs_delete_phase(self) -> bool:
    """
    Needs delete phase.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> _DayRawRemovalState().needs_delete_phase()  # doctest: +SKIP
    """
    return self.phase() in (PHASE_VERIFICATION_COMPLETE, PHASE_DELETING)

  def delete_phase_done(self) -> bool:
    """
    Delete the phase done.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> _DayRawRemovalState().delete_phase_done()  # doctest: +SKIP
    """
    return self.phase() == PHASE_DONE

  def stale_done_all_skipped_still_on_disk(self) -> bool:
    """
    True when phase=done but verify skipped every on-disk path (RC-I census).
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> _DayRawRemovalState().stale_done_all_skipped_still_on_disk()
    """
    if self.phase() != PHASE_DONE:
      return False
    with self._lock:
      verified_n = int(self._manifest.get("verified_count", 0))
      entries = dict(self._manifest.get("entries", {}))
    if verified_n > 0:
      return False
    for path, entry in entries.items():
      if not isinstance(entry, dict):
        continue
      if not os.path.isfile(path):
        continue
      if entry.get("status") == "verified" and not entry.get("deleted"):
        return True
    return verified_n == 0 and bool(entries) and any(
        os.path.isfile(path) for path in entries
    )

  def reopen_stale_done_all_skipped(self) -> bool:
    """
    Reopen stale done all skipped.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> _DayRawRemovalState().reopen_stale_done_all_skipped()  # doctest: +SKIP
    """
    if not self.stale_done_all_skipped_still_on_disk():
      return False
    with self._lock:
      self._manifest["phase"] = PHASE_VERIFYING
      # Clear stage so _close_one_day re-runs verify (avoid VERIFYING+POST_SEAL trap).
      self._manifest["verify_stage"] = VERIFY_STAGE_NONE
      _save_manifest(self._manifest_path, self._manifest)
    if self.log_fn:
      self.log_fn(
          "Day raw removal reopen stale done (all skipped on disk) day=%s"
          % self.day_date.isoformat(),
          flush=True,
      )
    return True

  def promote_phase_if_verify_stage_ahead(self) -> bool:
    """
    Promote phase when verify_stage already past but phase stuck at verifying.
    
    05-30 operator shape: ``phase=verifying`` +
      ``verify_stage=post_seal_complete``
    caused silent day-close re-enqueue (no delete start).
    
    PRE_SEAL cousin: sealed day stuck ``phase=verifying`` +
      ``pre_seal_complete``
    with only ingest-waiting retryables — promote so handoff/delete eligibility
    matches the POST_SEAL trap fix (do not silent-reenqueue forever).
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> _DayRawRemovalState().promote_phase_if_verify_stage_ahead()
    """
    if self.verification_complete():
      return False
    stage = self.verify_stage()
    promote_reason = ""
    if stage == VERIFY_STAGE_POST_SEAL:
      promote_reason = "verify_stage already post_seal"
    elif stage == VERIFY_STAGE_PRE_SEAL:
      if not self._only_waiting_on_ingest_blocks_completion():
        return False
      from hpcperfstats.dbload.lib.archive_compress import compressed_sibling_paths

      zst_path, gz_path = compressed_sibling_paths(self.tar_path)
      if not (os.path.isfile(zst_path) or os.path.isfile(gz_path)):
        return False
      promote_reason = "verify_stage pre_seal + sealed + waiting_on_ingest"
    else:
      return False
    with self._lock:
      self._manifest["phase"] = PHASE_VERIFICATION_COMPLETE
      _save_manifest(self._manifest_path, self._manifest)
    if self.log_fn:
      self.log_fn(
          "Day raw removal promote phase=verification_complete "
          "(%s) day=%s"
          % (promote_reason, self.day_date.isoformat()),
          flush=True,
      )
    return True

  def _resolve_maintenance_snapshot(self) -> Any:
    """
    Internal helper to resolve the maintenance snapshot.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _DayRawRemovalState()._resolve_maintenance_snapshot()  # doctest: +SKIP
    """
    if self.get_maintenance_snapshot is None:
      return None
    try:
      return self.get_maintenance_snapshot()
    except Exception:
      return None

  def _build_remaining_raw_for_daily_tar(self) -> Dict[str, List[str]]:
    """
    Day-scoped or snapshot remaining_raw for this daily tar.

    Within one ``apply_batch_delete`` / handoff pass, reuse the memoized map so
    completion helpers do not rebuild/log ``day-scoped closed_raw`` many times.

    Returns:
      Dict[str, List[str]]: Mapping of compressed path to closed raw paths.
    
    Examples:
      >>> _DayRawRemovalState()._build_remaining_raw_for_daily_tar()
    """
    if self._closed_raw_pass_memo_active and self._closed_raw_pass_memo is not None:
      return self._closed_raw_pass_memo
    snap = self._resolve_maintenance_snapshot()
    if snap is None and not self.get_allow_day_scoped_closed_raw():
      remaining: Dict[str, List[str]] = {}
    else:
      remaining = build_remaining_raw_for_daily_tar(
          self.archive_data_dir,
          self.host_name_ext,
          self.tgz_archive_dir,
          self.tar_path,
          maintenance_snapshot=snap,
          allow_full_snapshot=False,
          log_fn=self.log_fn,
      )
    if self._closed_raw_pass_memo_active:
      self._closed_raw_pass_memo = remaining
    return remaining

  def _clear_closed_raw_pass_memo(self) -> None:
    """
    Drop day-scoped closed_raw memo for the next delete/handoff pass.

    Returns:
      None

    Examples:
      >>> _DayRawRemovalState()._clear_closed_raw_pass_memo()  # doctest: +SKIP
    """
    self._closed_raw_pass_memo = None
    self._closed_raw_paths_pass_memo = None
    self._closed_raw_pass_memo_active = False

  def _begin_closed_raw_pass_memo(self) -> None:
    """
    Start a fresh closed_raw memo window for one delete/handoff pass.

    Returns:
      None

    Examples:
      >>> _DayRawRemovalState()._begin_closed_raw_pass_memo()  # doctest: +SKIP
    """
    self._closed_raw_pass_memo = None
    self._closed_raw_paths_pass_memo = None
    self._closed_raw_pass_memo_active = True

  def _manifest_paths_on_disk(self) -> List[str]:
    """
    Internal helper to handle manifest paths on disk.
    
    Returns:
      List[str]: List[str] produced by this call.
    
    Examples:
      >>> _DayRawRemovalState()._manifest_paths_on_disk()  # doctest: +SKIP
    """
    return [path for path, entry in self._manifest_entries_on_disk()]

  def _closed_raw_paths_on_disk(self) -> List[str]:
    """
    Closed raw paths still on disk for this day (memoized per delete pass).

    Returns:
      List[str]: Flat list of closed raw paths.
    
    Examples:
      >>> _DayRawRemovalState()._closed_raw_paths_on_disk()  # doctest: +SKIP
    """
    if self.delete_phase_done():
      blocking = self._blocking_manifest_paths_on_disk()
      if blocking:
        return blocking
      return []
    if (
        self._closed_raw_pass_memo_active
        and self._closed_raw_paths_pass_memo is not None
    ):
      return list(self._closed_raw_paths_pass_memo)
    remaining = self._build_remaining_raw_for_daily_tar()
    paths: List[str] = []
    for raw_list in (remaining or {}).values():
      paths.extend(raw_list or [])
    if self._closed_raw_pass_memo_active:
      self._closed_raw_paths_pass_memo = list(paths)
    return paths

  def _has_closed_raw_existing_on_disk(self) -> bool:
    """
    Internal helper to check whether closed raw existing on disk is present.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> _DayRawRemovalState()._has_closed_raw_existing_on_disk()
    """
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
    self,
    remaining: Dict[str, List[str]],
  ) -> Dict[str, List[str]]:
    """
    Internal helper to handle filter accrual paths blocking tar drop.
    
    Args:
      remaining (Dict[str, List[str]]): Mapping for remaining.
    
    Returns:
      Dict[str, List[str]]: Dict[str, List[str]] produced by this call.
    
    Examples:
      >>> _DayRawRemovalState()._filter_accrual_paths_blocking_tar_drop({})
    """
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
    return filter_remaining_raw_aligned_to_tar(
        filtered,
        self.tar_path,
        tgz_archive_dir=self.tgz_archive_dir,
    )

  def _remaining_raw_paths_blocking_tar_drop(self) -> Dict[str, List[str]]:
    """
    Accrual map whose on-disk paths block ``.tar`` unlink (manifest/quarantine-.
    
      aware).
    
    Returns:
      Dict[str, List[str]]: Dict[str, List[str]] produced by this call.
    
    Examples:
      >>> _DayRawRemovalState()._remaining_raw_paths_blocking_tar_drop()
    """
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
    """
    Internal helper to count the quarantine accrual paths on disk.
    
    Returns:
      int: int produced by this call.
    
    Examples:
      >>> _DayRawRemovalState()._count_quarantine_accrual_paths_on_disk()
    """
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
    """
    Internal helper to log the tar drop skip.
    
    Args:
      reason (str): String for reason.
      sealed_ok (bool): Boolean flag for sealed ok.
    
    Returns:
      None
    
    Examples:
      >>> _DayRawRemovalState()._log_tar_drop_skip("x", True)  # doctest: +SKIP
    """
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
    with janitorial_logging():
      self.log_fn(
          "tar_drop_skip day=%s reason=%s remaining_n=%d "
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
    """
    Drop ``.tar`` when sealed and no closed raw files remain on disk.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> _DayRawRemovalState().try_finish_tar_drop_if_ready()  # doctest: +SKIP
    """
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
    """
    Internal helper to handle entry is retryable skip.
    
    Args:
      entry (Dict[str, Any]): Mapping for entry.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> _DayRawRemovalState()._entry_is_retryable_skip({})  # doctest: +SKIP
    """
    if not isinstance(entry, dict):
      return False
    reason = str(entry.get("reason") or "")
    status = str(entry.get("status") or "")
    return (
        reason in RETRYABLE_SKIP_REASONS
        or status in RETRYABLE_SKIP_STATUSES
    )

  def _entry_is_quarantine_terminal_skip(self, entry: Dict[str, Any]) -> bool:
    """
    Internal helper to handle entry is quarantine terminal skip.
    
    Args:
      entry (Dict[str, Any]): Mapping for entry.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> _DayRawRemovalState()._entry_is_quarantine_terminal_skip({})
    """
    if not isinstance(entry, dict):
      return False
    reason = str(entry.get("reason") or "")
    status = str(entry.get("status") or "")
    return (
        reason in QUARANTINE_SKIP_REASONS
        or status in QUARANTINE_SKIP_STATUSES
    )

  def _needs_retry_after_ingest(self) -> bool:
    """
    Internal helper to handle needs retry after ingest.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> _DayRawRemovalState()._needs_retry_after_ingest()  # doctest: +SKIP
    """
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
    """
    Internal helper to handle reset for reverify.
    
    Returns:
      None
    
    Examples:
      >>> _DayRawRemovalState()._reset_for_reverify()  # doctest: +SKIP
    """
    with self._lock:
      self._manifest = _new_manifest(self.tar_path)
      _save_manifest(self._manifest_path, self._manifest)

  def _all_closed_raw_terminal_or_gone(self) -> bool:
    """
    Internal helper to handle all closed raw terminal or gone.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> _DayRawRemovalState()._all_closed_raw_terminal_or_gone()
    """
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
    """
    Internal helper to handle unmanifested closed raw paths.
    
    Returns:
      List[str]: List[str] produced by this call.
    
    Examples:
      >>> _DayRawRemovalState()._unmanifested_closed_raw_paths()  # doctest: +SKIP
    """
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
    """
    Internal helper to handle manifest entries on disk.
    
    Returns:
      List[Tuple[str, Dict[str, Any]]]: List[Tuple[str, Dict[str, Any]]]
      produced by this call.
    
    Examples:
      >>> _DayRawRemovalState()._manifest_entries_on_disk()  # doctest: +SKIP
    """
    with self._lock:
      entries = dict(self._manifest.get("entries", {}))
    on_disk: List[Tuple[str, Dict[str, Any]]] = []
    for path, entry in entries.items():
      if os.path.isfile(path) and isinstance(entry, dict):
        on_disk.append((path, entry))
    return on_disk

  def _entry_is_verified_ghost_on_disk(self, entry: Dict[str, Any]) -> bool:
    """
    Internal helper to handle entry is verified ghost on disk.
    
    Args:
      entry (Dict[str, Any]): Mapping for entry.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> _DayRawRemovalState()._entry_is_verified_ghost_on_disk({})
    """
    if not isinstance(entry, dict):
      return False
    return (
        entry.get("deleted") is True
        and str(entry.get("status") or "") == "verified"
    )

  def _manifest_verified_pending_count(self) -> int:
    """
    Manifest-only count of verified entries not yet marked deleted.
    
    Returns:
      int: int produced by this call.
    
    Examples:
      >>> _DayRawRemovalState()._manifest_verified_pending_count()
    """
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
    """
    True when manifest marks verified paths deleted (ghost retry candidates).
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> _DayRawRemovalState()._manifest_has_ghost_markers()  # doctest: +SKIP
    """
    with self._lock:
      entries = self._manifest.get("entries", {})
    for entry in (entries or {}).values():
      if self._entry_is_verified_ghost_on_disk(entry):
        return True
    return False

  def _verified_pending_paths_on_disk(self) -> List[str]:
    """
    On-disk paths whose manifest entry is verified and not yet deleted.
    
    Returns:
      List[str]: List[str] produced by this call.
    
    Examples:
      >>> _DayRawRemovalState()._verified_pending_paths_on_disk()
    """
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
    """
    Internal helper to handle ghost deleted paths on disk.
    
    Returns:
      List[str]: List[str] produced by this call.
    
    Examples:
      >>> _DayRawRemovalState()._ghost_deleted_paths_on_disk()  # doctest: +SKIP
    """
    if not self._manifest_has_ghost_markers():
      return []
    return [
        path
        for path, entry in self._manifest_entries_on_disk()
        if self._entry_is_verified_ghost_on_disk(entry)
    ]

  def needs_ghost_delete_retry(self) -> bool:
    """
    Needs ghost delete retry.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> _DayRawRemovalState().needs_ghost_delete_retry()  # doctest: +SKIP
    """
    if not self.delete_phase_done():
      return False
    if not self._manifest_has_ghost_markers():
      return False
    return bool(self._ghost_deleted_paths_on_disk())

  def needs_reopen_for_verified_pending(self) -> bool:
    """
    True when ``phase=done`` but verified manifest entries remain undeleted.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> _DayRawRemovalState().needs_reopen_for_verified_pending()
    """
    if self.phase() != PHASE_DONE:
      return False
    return self._manifest_verified_pending_count() > 0

  def _blocking_manifest_paths_on_disk(self) -> List[str]:
    """
    Internal helper to handle blocking manifest paths on disk.
    
    Returns:
      List[str]: List[str] produced by this call.
    
    Examples:
      >>> _DayRawRemovalState()._blocking_manifest_paths_on_disk()
    """
    return [
        path
        for path, entry in self._manifest_entries_on_disk()
        if not self._entry_is_verified_ghost_on_disk(entry)
        and not self._entry_is_quarantine_terminal_skip(entry)
        and stats_path_aligned_to_daily_tar(
            path,
            self.tar_path,
            tgz_archive_dir=self.tgz_archive_dir,
        )
    ]

  def _only_quarantine_terminal_on_disk(self) -> bool:
    """
    Internal helper to handle only quarantine terminal on disk.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> _DayRawRemovalState()._only_quarantine_terminal_on_disk()
    """
    on_disk = self._manifest_entries_on_disk()
    if not on_disk:
      return False
    for _path, entry in on_disk:
      if not self._entry_is_quarantine_terminal_skip(entry):
        return False
    return True

  def _finalize_quarantine_terminal_done(self) -> None:
    """
    Internal helper to handle finalize quarantine terminal done.
    
    Returns:
      None
    
    Examples:
      >>> _DayRawRemovalState()._finalize_quarantine_terminal_done()
    """
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
    """
    Internal helper to prepare the ghost delete retry.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> _DayRawRemovalState()._prepare_ghost_delete_retry()  # doctest: +SKIP
    """
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
    """
    Internal helper to handle manifest retryable paths on disk.
    
    Returns:
      List[str]: List[str] produced by this call.
    
    Examples:
      >>> _DayRawRemovalState()._manifest_retryable_paths_on_disk()
    """
    return [
        path
        for path, entry in self._manifest_entries_on_disk()
        if self._entry_is_retryable_skip(entry)
    ]

  def _reclassify_retryable_skips_on_disk(self) -> int:
    """
    Upgrade retryable skip entries when tar membership and DB gate now pass.
    
    Branch C: must run under ``phase=deleting`` (and verification_complete), not
    only after ``phase=done``. F15 refuses PHASE_DONE while retryables remain,
      so
    a done-only gate left sticky handoff forever.
    
    Returns:
      int: int produced by this call.
    
    Examples:
      >>> _DayRawRemovalState()._reclassify_retryable_skips_on_disk()
    """
    if self.phase() not in (
        PHASE_DONE,
        PHASE_DELETING,
        PHASE_VERIFICATION_COMPLETE,
    ):
      return 0
    retryable_paths = self._manifest_retryable_paths_on_disk()
    if not retryable_paths:
      return 0
    if not ensure_daily_tar_restored_for_append(
        self.tar_path,
        cfg.get_archive_zstd_threads(),
    ):
      if self.log_fn:
        self.log_fn(
            "Day raw removal reclassify deferred (tar restore failed) day=%s"
            % self.day_date.isoformat(),
            flush=True,
        )
      return 0
    ok, members = validate_open_tar_for_raw_removal(
        self.tar_path,
        log_fn=self.log_fn,
        validation_cache=self._validation_cache,
    )
    if not ok or members is None:
      return 0
    zst_path, _gz_path = compressed_sibling_paths(self.tar_path)
    gate_fn = (
        self.ingest_ready_fn
        if cfg.get_sync_archive_require_db_ingest()
        else None
    )
    upgraded = 0
    still_skipped = 0
    for path, status, reason in classify_removable_raw_paths_for_open_tar(
        self.tar_path,
        retryable_paths,
        ingest_ready_fn=gate_fn,
        log_fn=self.log_fn,
        validation_cache=self._validation_cache,
        open_tar_members=members,
    ):
      if status == "verified":
        with self._lock:
          prior = self._manifest.get("entries", {}).get(path)
          if isinstance(prior, dict) and prior.get("status") == "verified":
            continue
        self._record_entry(path, zst_path, status, reason)
        upgraded += 1
      else:
        still_skipped += 1
    if upgraded:
      with self._lock:
        _save_manifest(self._manifest_path, self._manifest)
      if self.log_fn:
        self.log_fn(
            "Day raw removal reclassify retryable skips day=%s "
            "upgraded=%d still_skipped=%d"
            % (self.day_date.isoformat(), upgraded, still_skipped),
            flush=True,
        )
    return upgraded

  def _manifest_only_waiting_on_ingest(self) -> bool:
    """
    True when every on-disk entry is retryable or quarantine, with ≥1 retryable.
    
    Quarantine is transparent: mixed ``skipped_not_in_archive`` +
    ``skipped_quarantine`` (06-07) must hand off like retryable-only, not stay
    stuck at ``verification_complete``.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> _DayRawRemovalState()._manifest_only_waiting_on_ingest()
    """
    on_disk = self._manifest_entries_on_disk()
    if not on_disk:
      return False
    has_retryable = False
    for _path, entry in on_disk:
      if self._entry_is_retryable_skip(entry):
        has_retryable = True
        continue
      if self._entry_is_quarantine_terminal_skip(entry):
        continue
      return False
    return has_retryable

  def _only_waiting_on_ingest_blocks_completion(self) -> bool:
    """
    Internal helper to handle only waiting on ingest blocks completion.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> _DayRawRemovalState()._only_waiting_on_ingest_blocks_completion()
    """
    if self.delete_phase_done():
      return self._manifest_only_waiting_on_ingest()
    if self._unmanifested_closed_raw_paths():
      return False
    with self._lock:
      entries = dict(self._manifest.get("entries", {}))
    has_retryable = False
    for path in self._closed_raw_paths_on_disk():
      if not os.path.isfile(path):
        continue
      entry = entries.get(path)
      if entry is None:
        return False
      if self._entry_is_retryable_skip(entry):
        has_retryable = True
        continue
      if self._entry_is_quarantine_terminal_skip(entry):
        continue
      return False
    return has_retryable

  def _async_verify_in_flight(self) -> bool:
    """
    Internal helper to handle async verify in flight.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> _DayRawRemovalState()._async_verify_in_flight()  # doctest: +SKIP
    """
    future = self._pipeline_future
    return future is not None and not future.done()

  def has_active_raw_removal_work(self) -> bool:
    """
    Return True if active raw removal work.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> _DayRawRemovalState().has_active_raw_removal_work()  # doctest: +SKIP
    """
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
    """
    Waiting on ingest at startup.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> _DayRawRemovalState().waiting_on_ingest_at_startup()  # doctest: +SKIP
    """
    return (
        not self.delete_phase_done()
        and not self.has_active_raw_removal_work()
        and self.needs_delete_phase()
    )

  def handoff_paths_for_ingest(self) -> List[str]:
    """
    Closed raw on disk whose manifest entry is missing or retryable.
    
    Returns:
      List[str]: List[str] produced by this call.
    
    Examples:
      >>> _DayRawRemovalState().handoff_paths_for_ingest()  # doctest: +SKIP
    """
    if self.delete_phase_done():
      return self._manifest_retryable_paths_on_disk()
    # Pre-ingest: no day-scoped census — known manifest retryables only.
    if not self.get_allow_day_scoped_closed_raw():
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
    """
    Return True if handoff day close to ingest.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> _DayRawRemovalState().should_handoff_day_close_to_ingest()
    """
    if not self.verification_complete():
      return False
    if not self._only_waiting_on_ingest_blocks_completion():
      return False
    return bool(self.handoff_paths_for_ingest())

  def complete_handoff_to_ingest(self) -> List[str]:
    """
    Mark waiting-on-ingest done when needed; return paths for ingest requeue.
    
    Returns:
      List[str]: List[str] produced by this call.
    
    Examples:
      >>> _DayRawRemovalState().complete_handoff_to_ingest()  # doctest: +SKIP
    """
    paths = self.handoff_paths_for_ingest()
    if not paths:
      return []
    if not self.delete_phase_done():
      self._mark_done_waiting_on_ingest()
    return list(paths)

  def _mark_done_waiting_on_ingest(self) -> None:
    """
    Internal helper to handle mark done waiting on ingest.
    
    Returns:
      None
    
    Examples:
      >>> _DayRawRemovalState()._mark_done_waiting_on_ingest()  # doctest: +SKIP
    """
    handoff_paths = self.handoff_paths_for_ingest()
    if not handoff_paths:
      return
    retryable_count = 0
    with self._lock:
      entries = dict(self._manifest.get("entries", {}))
    for path in self._closed_raw_paths_on_disk():
      if not os.path.isfile(path):
        continue
      entry = entries.get(path)
      if isinstance(entry, dict) and self._entry_is_retryable_skip(entry):
        retryable_count += 1
    # F15: do not mark PHASE_DONE while retryable closed raw remains on disk.
    if retryable_count > 0:
      if self.log_fn:
        self.log_fn(
            "Day raw removal waiting_on_ingest (raw remains) day=%s "
            "retryable=%d phase=%s"
            % (
                self.day_date.isoformat(),
                retryable_count,
                self.phase(),
            ),
            flush=True,
        )
      return
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
    """
    Progress summary.
    
    Returns:
      Dict[str, Any]: Dict[str, Any] produced by this call.
    
    Examples:
      >>> _DayRawRemovalState().progress_summary()  # doctest: +SKIP
    """
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
          "worker_stage": str(self._manifest.get("worker_stage") or ""),
          "members_done": int(self._manifest.get("members_done", 0) or 0),
          "members_total": int(self._manifest.get("members_total", 0) or 0),
          "last_progress_ts": self._manifest.get("last_progress_ts"),
          "verified_count": int(self._manifest.get("verified_count", 0)),
          "pending_delete": pending_delete,
          "deleted_count": int(self._manifest.get("deleted_count", 0)),
      }

  def paths_pending_delete(self) -> Set[str]:
    """
    Paths pending delete.
    
    Returns:
      Set[str]: Set[str] produced by this call.
    
    Examples:
      >>> _DayRawRemovalState().paths_pending_delete()  # doctest: +SKIP
    """
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
    """
    Consumed paths.
    
    Returns:
      Set[str]: Set[str] produced by this call.
    
    Examples:
      >>> _DayRawRemovalState().consumed_paths()  # doctest: +SKIP
    """
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
    """
    Reopen delete when ``phase=done`` but verified entries remain undeleted.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> _DayRawRemovalState().reopen_delete_phase_if_verified_on_disk()
    """
    if self.phase() != PHASE_DONE:
      return False
    pending_manifest_n = self._manifest_verified_pending_count()
    if pending_manifest_n == 0:
      return False
    pending_on_disk = self._verified_pending_paths_on_disk()
    with self._lock:
      self._manifest["phase"] = PHASE_DELETING
      _save_manifest(self._manifest_path, self._manifest)
    if self.log_fn:
      self.log_fn(
          "Day raw removal pending delete reopen day=%s "
          "manifest_pending=%d on_disk=%d"
          % (
              self.day_date.isoformat(),
              pending_manifest_n,
              len(pending_on_disk),
          ),
          flush=True,
      )
    return True

  def begin_deleting(self) -> None:
    """
    Begin deleting.
    
    Returns:
      None
    
    Examples:
      >>> _DayRawRemovalState().begin_deleting()  # doctest: +SKIP
    """
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

  def _record_entry(
    self,
    path: str,
    daily_gz: str,
    status: str,
    reason: str,
  ) -> None:
    """
    Internal helper to handle record entry.
    
    Args:
      path (str): String for path.
      daily_gz (str): String for daily gz.
      status (str): String for status.
      reason (str): String for reason.
    
    Returns:
      None
    
    Examples:
      >>> _DayRawRemovalState()._record_entry("x", "x", "x", "x")
    """
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
        prior_status = str(prior.get("status") or "")
        if prior_status != "verified":
          if status == "verified":
            self._manifest["skipped_count"] = max(
                0,
                int(self._manifest.get("skipped_count", 0)) - 1,
            )
            self._manifest["verified_count"] = int(
                self._manifest.get("verified_count", 0)) + 1
      else:
        if status == "verified":
          self._manifest["verified_count"] = int(
              self._manifest.get("verified_count", 0)) + 1
        else:
          self._manifest["skipped_count"] = int(
              self._manifest.get("skipped_count", 0)) + 1
      entries[path] = entry

  def _verify_body(self) -> None:
    """
    Internal helper to handle verify body.
    
    Returns:
      None
    
    Examples:
      >>> _DayRawRemovalState()._verify_body()  # doctest: +SKIP
    """
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
        if cfg.get_sync_archive_require_db_ingest()
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

  def update_reconcile_progress(
    self,
    *,
    worker_stage: str = "",
    members_done: int | None = None,
    members_total: int | None = None,
    last_progress_ts: float | None = None,
  ) -> None:
    """
    Persist day-close reconcile progress fields on the raw-removal manifest.

    Args:
      worker_stage (str): Stage label (for example ``reconcile_merge``).
      members_done (int | None): Members copied so far.
      members_total (int | None): Union member count.
      last_progress_ts (float | None): Monotonic or wall progress timestamp.

    Returns:
      None

    Examples:
      >>> _DayRawRemovalState().update_reconcile_progress(
      ...   worker_stage="reconcile_merge")  # doctest: +SKIP
    """
    with self._lock:
      if worker_stage:
        self._manifest["worker_stage"] = str(worker_stage)
      if members_done is not None:
        self._manifest["members_done"] = int(members_done)
      if members_total is not None:
        self._manifest["members_total"] = int(members_total)
      if last_progress_ts is not None:
        self._manifest["last_progress_ts"] = float(last_progress_ts)
      _save_manifest(self._manifest_path, self._manifest)

  def _pre_seal_verify_body(
    self,
    *,
    max_classify_batches: int | None = None,
  ) -> bool:
    """
    Run pre-seal verify; optionally stop after ``max_classify_batches``.

    When ``max_classify_batches`` is set (day-close claim), classify at most
    that many path batches and return False so the worker can yield/requeue
    with cursor resume. ``None`` runs all batches to completion.

    Args:
      max_classify_batches (int | None): Max classify batches this call.

    Returns:
      bool: True when pre-seal verify is complete.

    Examples:
      >>> _DayRawRemovalState()._pre_seal_verify_body()  # doctest: +SKIP
    """
    close_old_connections()
    if self.pre_seal_verification_complete():
      return True
    with self._lock:
      self._manifest["phase"] = PHASE_VERIFYING
      if not self._manifest.get("started_at"):
        self._manifest["started_at"] = time.time()
    paths_per_tick = max(1, int(cfg.get_sync_day_close_raw_paths_per_batch()))
    verify_started = time.time()
    if self.log_fn:
      self.log_fn(
          "janitor: day_close pre_seal_verify tar_restore begin day=%s"
          % self.day_date.isoformat(),
          flush=True,
      )
    if not ensure_daily_tar_restored_for_append(
        self.tar_path,
        cfg.get_archive_zstd_threads(),
        wait_for_other_owner=False,
    ):
      if self.log_fn:
        self.log_fn(
            "Day raw removal pre-seal verify deferred (tar restore failed) "
            "day=%s"
            % self.day_date.isoformat(),
            flush=True,
        )
      return False
    if self.log_fn:
      self.log_fn(
          "janitor: day_close pre_seal_verify tar_restore done day=%s"
          % self.day_date.isoformat(),
          flush=True,
      )
    ok, members = validate_open_tar_for_raw_removal(
        self.tar_path,
        log_fn=self.log_fn,
        validation_cache=self._validation_cache,
    )
    if not ok or members is None:
      return False
    if self.log_fn:
      self.log_fn(
          "janitor: day_close pre_seal_verify open_tar_members n=%d day=%s"
          % (len(members), self.day_date.isoformat()),
          flush=True,
      )
    zst_path, _gz_path = compressed_sibling_paths(self.tar_path)
    remaining = self._build_remaining_raw_for_daily_tar()
    raw_paths: List[str] = []
    for paths in (remaining or {}).values():
      raw_paths.extend(paths or [])
    skip_paths = set(self.get_quarantine_skip_paths() or ())
    filtered: List[str] = []
    for path in raw_paths:
      if stats_file_is_active_segment(path):
        self._record_entry(path, zst_path, "skipped_active_segment", "active_segment")
        continue
      if path in skip_paths:
        self._record_entry(path, zst_path, "skipped_quarantine", "quarantine")
        continue
      filtered.append(path)
    with self._lock:
      cursor = int(self._manifest.get("pre_seal_classify_index", 0))
    if cursor > len(filtered):
      cursor = 0
    gate_fn = (
        self.ingest_ready_fn
        if cfg.get_sync_archive_require_db_ingest()
        else None
    )
    batches_run = 0
    while cursor < len(filtered) and not shutdown_requested[0]:
      if (
          max_classify_batches is not None
          and batches_run >= int(max_classify_batches)
      ):
        break
      batch_end = min(cursor + paths_per_tick, len(filtered))
      batch = filtered[cursor:batch_end]
      classify_paths = batch
      if batch and gate_fn is not None:
        snapshot = (
            self.get_maintenance_snapshot()
            if callable(self.get_maintenance_snapshot)
            else None
        )
        gate_identities = (
            getattr(snapshot, "gate_identities_by_path", None)
            if snapshot is not None
            else None
        )
        if gate_identities:
          gate_ready, gate_skipped = filter_paths_head_ingested(
              batch,
              log_fn=self.log_fn,
              gate_identities_by_path=gate_identities,
          )
          for path in gate_skipped:
            self._record_entry(
                path,
                zst_path,
                "skipped_not_head_tail_ingested",
                "not_head_tail_ingested",
            )
          classify_paths = gate_ready
        else:
          gate_ready = []
          gate_skipped = []
          worker_cap = max(1, int(cfg.get_sync_ingest_pool_processes()))

          def _gate_one(path: str) -> Any:
            """
            Internal helper to handle gate one.

            Args:
              path (str): String for path.

            Returns:
              Any: Value produced by this call (type depends on inputs).

            Examples:
              >>> _DayRawRemovalState()._gate_one("x")  # doctest: +SKIP
            """
            close_old_connections()
            if gate_fn(path):
              return path, True, ""
            return path, False, "not_head_tail_ingested"

          for path, ready, _err in iter_bounded_thread_pool(
              batch,
              _gate_one,
              max_workers=worker_cap,
          ):
            if ready:
              gate_ready.append(path)
            else:
              gate_skipped.append(path)
          for path in gate_skipped:
            self._record_entry(
                path,
                zst_path,
                "skipped_not_head_tail_ingested",
                "not_head_tail_ingested",
            )
          classify_paths = gate_ready
      if classify_paths:
        for path, status, reason in classify_removable_raw_paths_for_open_tar(
            self.tar_path,
            classify_paths,
            ingest_ready_fn=None,
            log_fn=self.log_fn,
            validation_cache=self._validation_cache,
            open_tar_members=members,
        ):
          self._record_entry(path, zst_path, status, reason)
      cursor = batch_end
      batches_run += 1
      with self._lock:
        self._manifest["pre_seal_classify_index"] = cursor
        _save_manifest(self._manifest_path, self._manifest)
      if self.log_fn:
        self.log_fn(
            "janitor: day_close pre_seal_verify classify progress "
            "verified_n=%d/%d elapsed_s=%.1f day=%s"
            % (
                cursor,
                len(filtered),
                time.time() - verify_started,
                self.day_date.isoformat(),
            ),
            flush=True,
        )
    if shutdown_requested[0] or cursor < len(filtered):
      return False
    with self._lock:
      self._manifest["verify_stage"] = VERIFY_STAGE_PRE_SEAL
      self._manifest["phase"] = PHASE_VERIFICATION_COMPLETE
      self._manifest.pop("pre_seal_classify_index", None)
      _save_manifest(self._manifest_path, self._manifest)
    if self.log_fn:
      self.log_fn(
          "Day raw removal pre-seal verify complete day=%s verified=%d skipped=%d"
          % (
              self.day_date.isoformat(),
              int(self._manifest.get("verified_count", 0)),
              int(self._manifest.get("skipped_count", 0)),
          ),
          flush=True,
      )
    return True

  def _post_seal_verify_body(self) -> bool:
    """
    Internal helper to handle post seal verify body.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> _DayRawRemovalState()._post_seal_verify_body()  # doctest: +SKIP
    """
    close_old_connections()
    if self.post_seal_verification_complete():
      return True
    ok = validate_post_seal_tar_zst_parity(
        self.tar_path,
        log_fn=self.log_fn,
        validation_cache=self._validation_cache,
    )
    if ok:
      with self._lock:
        self._manifest["verify_stage"] = VERIFY_STAGE_POST_SEAL
        _save_manifest(self._manifest_path, self._manifest)
      if self.log_fn:
        self.log_fn(
            "Day raw removal post-seal verify complete day=%s"
            % self.day_date.isoformat(),
            flush=True,
        )
    return ok

  def _batch_delete_completion_context(self, entries: Any) -> Any:
    """
    Single completion snapshot after the delete loop (one pass per helper).
    
    Args:
      entries (Any): Entries passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _DayRawRemovalState()._batch_delete_completion_context(None)
    """
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
    """
    Apply the batch delete.
    
    Returns:
      int: int produced by this call.
    
    Examples:
      >>> _DayRawRemovalState().apply_batch_delete()  # doctest: +SKIP
    """
    owns_memo = not self._closed_raw_pass_memo_active
    if owns_memo:
      self._begin_closed_raw_pass_memo()
    try:
      return self._apply_batch_delete_body()
    finally:
      if owns_memo:
        self._clear_closed_raw_pass_memo()

  def _apply_batch_delete_body(self) -> int:
    """
    Batch-delete implementation (runs under an active closed_raw memo window).

    Returns:
      int: Number of paths deleted this pass.

    Examples:
      >>> _DayRawRemovalState()._apply_batch_delete_body()  # doctest: +SKIP
    """
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
      ingest_skip_fn = self.get_ingest_active_skip_paths
      if callable(ingest_skip_fn):
        skip_paths = set(ingest_skip_fn() or ())
      else:
        skip_paths = set(self.get_quarantine_skip_paths() or ())
        skip_paths -= self.paths_pending_delete()
      if path in skip_paths:
        classify_fn = self.classify_quarantine_skip_path
        skip_class = (
            classify_fn(path)
            if callable(classify_fn)
            else "active_ingest"
        )
        if self.log_fn:
          self.log_fn(
              "janitor: day_close delete defer tar=%s day=%s path=%s "
              "reason=active_ingest skip_class=%s"
              % (
                  self.tar_path,
                  self.day_date.isoformat(),
                  path,
                  skip_class,
              ),
              flush=True,
          )
        with self._lock:
          entry = entries.get(path)
          if isinstance(entry, dict):
            entry["delete_deferred"] = "active_ingest"
        continue
      if self.log_fn:
        self.log_fn(
            "removing stats file (day raw removal preflight): " + path,
            flush=True,
        )
      try:
        with try_file_write_lock(path):
          os.remove(path)
        with self._lock:
          entry = entries.get(path)
          if isinstance(entry, dict):
            entry["deleted"] = True
          self._manifest["deleted_count"] = int(
              self._manifest.get("deleted_count", 0)) + 1
        deleted += 1
      except TimeoutError:
        if self.log_fn:
          self.log_fn(
              "janitor: day_close defer tar=%s phase=raw_delete reason=write_lock_contended path=%s"
              % (self.tar_path, path),
              flush=True,
          )
        with self._lock:
          entry = entries.get(path)
          if isinstance(entry, dict):
            entry["delete_deferred"] = "write_lock_contended"
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
  day_raw_removal: Any,
  chunk_calendar_day_hint: Optional[str],
) -> bool:
  """
  True when oldest pending delete day is calendar-disjoint from in-flight.
  
    ingest.
  
  Args:
    day_raw_removal (Any): Day raw removal passed to this helper.
    chunk_calendar_day_hint (Optional[str]): Chunk calendar day hint, or None
    when absent.
  
  Returns:
    bool: True or False for this check.
  
  Examples:
    >>> day_raw_delete_safe_during_chunk(None, None)  # doctest: +SKIP
  """
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
  day_raw_removal: Any,
  day_close_manifest: Any,
  *,
  chunk_in_progress: bool,
  chunk_calendar_day_hint: Optional[str] = None,
  finalize_day_close_delete: Callable[[str], None],
  sleep_fn: Callable[[float], None],
  log_chunk_wait: Optional[Callable[[Optional[str], int], None]] = None,
  on_delete_batch_begin: Optional[Callable[[], None]] = None,
  on_delete_batch_end: Optional[Callable[[], None]] = None,
) -> bool:
  """
  One supervisor delete-driver pass; tar-drop runs before batch-delete chunk.
  
    wait.
  
  Returns True when the caller should keep spinning the delete driver.
  
  Args:
    day_raw_removal (Any): Day raw removal passed to this helper.
    day_close_manifest (Any): Day close manifest passed to this helper.
    chunk_in_progress (bool): Boolean flag for chunk in progress.
    chunk_calendar_day_hint (Optional[str]): Chunk calendar day hint, or None
    when absent.
    finalize_day_close_delete (Callable[[str], None]): Finalize day close
    delete.
    sleep_fn (Callable[[float], None]): Sleep fn.
    log_chunk_wait (Optional[Callable[[Optional[str], int], None]]): Log chunk
    wait, or None when absent.
    on_delete_batch_begin (Optional[Callable[[], None]]): On delete batch
    begin, or None when absent.
    on_delete_batch_end (Optional[Callable[[], None]]): On delete batch end,
    or None when absent.
  
  Returns:
    bool: True or False for this check.
  
  Examples:
    >>> run_supervisor_day_raw_removal_delete_pass(0)  # doctest: +SKIP
  """
  with janitorial_logging():
    return _run_supervisor_day_raw_removal_delete_pass_inner(
        day_raw_removal,
        day_close_manifest,
        chunk_in_progress=chunk_in_progress,
        chunk_calendar_day_hint=chunk_calendar_day_hint,
        finalize_day_close_delete=finalize_day_close_delete,
        sleep_fn=sleep_fn,
        log_chunk_wait=log_chunk_wait,
        on_delete_batch_begin=on_delete_batch_begin,
        on_delete_batch_end=on_delete_batch_end,
    )


def _run_supervisor_day_raw_removal_delete_pass_inner(
  day_raw_removal: Any,
  day_close_manifest: Any,
  *,
  chunk_in_progress: bool,
  chunk_calendar_day_hint: Optional[str] = None,
  finalize_day_close_delete: Callable[[str], None],
  sleep_fn: Callable[[float], None],
  log_chunk_wait: Optional[Callable[[Optional[str], int], None]] = None,
  on_delete_batch_begin: Optional[Callable[[], None]] = None,
  on_delete_batch_end: Optional[Callable[[], None]] = None,
) -> bool:
  """
  Internal helper to run the supervisor day raw removal delete pass inner.
  
  Args:
    day_raw_removal (Any): Day raw removal passed to this helper.
    day_close_manifest (Any): Day close manifest passed to this helper.
    chunk_in_progress (bool): Boolean flag for chunk in progress.
    chunk_calendar_day_hint (Optional[str]): Chunk calendar day hint, or None
    when absent.
    finalize_day_close_delete (Callable[[str], None]): Finalize day close
    delete.
    sleep_fn (Callable[[float], None]): Sleep fn.
    log_chunk_wait (Optional[Callable[[Optional[str], int], None]]): Log chunk
    wait, or None when absent.
    on_delete_batch_begin (Optional[Callable[[], None]]): On delete batch
    begin, or None when absent.
    on_delete_batch_end (Optional[Callable[[], None]]): On delete batch end,
    or None when absent.
  
  Returns:
    bool: True or False for this check.
  
  Examples:
    >>> _run_supervisor_day_raw_removal_delete_pass_inner(0)  # doctest: +SKIP
  """
  if day_raw_removal is None or not day_raw_removal.enabled:
    return False
  reopen_fn = getattr(
      day_raw_removal, "reopen_done_days_with_verified_on_disk", lambda: 0,
  )
  made_progress = reopen_fn() > 0
  needs_delete = day_raw_removal.any_needs_delete_phase()
  needs_tar_drop = day_raw_removal.any_needs_tar_drop_finish()
  if not needs_delete and not needs_tar_drop:
    advance_fn = getattr(
        day_raw_removal, "advance_raw_removal_blockers", lambda: False,
    )
    if day_raw_removal.any_active_raw_removal_work() and advance_fn():
      return True
    return False
  tar_drop_targets: list[str] = []
  if needs_tar_drop:
    tar_drop_targets.extend(day_raw_removal.days_needing_tar_drop_oldest_first())
  for tar_norm in tar_drop_targets:
    if day_raw_removal.try_finish_tar_drop_if_ready(tar_norm):
      finalize_day_close_delete(tar_norm)
      made_progress = True
  if tar_drop_targets and getattr(day_raw_removal, "log_fn", None):
    still_present = [t for t in tar_drop_targets if os.path.isfile(t)]
    if still_present:
      day_raw_removal.log_fn(
          "tar_drop_deferred oldest=%s count=%d"
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
  log_fn: Any | None = None,
) -> Dict[str, List[str]]:
  """
  Shared tar-drop blocker map for supervisor and async paths.
  
  Args:
    tar_path (str): String for tar path.
    archive_data_dir (str): String for archive data dir.
    host_name_ext (str): String for host name ext.
    tgz_archive_dir (str): String for tgz archive dir.
    get_quarantine_skip_paths (Callable[[], Set[str]]): Get quarantine skip
    paths.
    get_maintenance_snapshot (Optional[Callable[[], Any]]): Get maintenance
    snapshot, or None when absent.
    log_fn (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Dict[str, List[str]]: Dict[str, List[str]] produced by this call.
  
  Examples:
    >>> remaining_raw_by_gz_blocking_tar_drop(0)  # doctest: +SKIP
  """
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


def blocking_closed_raw_remains_for_day(
  tar_path: str,
  *,
  archive_data_dir: Optional[str] = None,
  host_name_ext: Optional[str] = None,
  tgz_archive_dir: Optional[str] = None,
  get_quarantine_skip_paths: Optional[Callable[[], Set[str]]] = None,
  get_maintenance_snapshot: Optional[Callable[[], Any]] = None,
  log_fn: Any | None = None,
) -> Dict[str, List[str]]:
  """
  Canonical DECISION map: on-disk paths that still block day-close completion.
  
  Quarantine-skip and quarantine-terminal paths are excluded. Use for tar_drop,
  seal retain, needs_work, filesystem-complete, and decompress-unlink gates.
  Census builders (``build_remaining_raw_*``) remain inventory-only.
  
  Distinct from checkpoint ``checkpoint_incomplete`` (ingest DB), which means
  unprocessed ingest paths — not closed raw on disk.
  
  Args:
    tar_path (str): String for tar path.
    archive_data_dir (Optional[str]): Archive data dir, or None when absent.
    host_name_ext (Optional[str]): Host name ext, or None when absent.
    tgz_archive_dir (Optional[str]): Tgz archive dir, or None when absent.
    get_quarantine_skip_paths (Optional[Callable[[], Set[str]]]): Get
    quarantine skip paths, or None when absent.
    get_maintenance_snapshot (Optional[Callable[[], Any]]): Get maintenance
    snapshot, or None when absent.
    log_fn (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Dict[str, List[str]]: Dict[str, List[str]] produced by this call.
  
  Examples:
    >>> blocking_closed_raw_remains_for_day(0)  # doctest: +SKIP
  """
  archive_data_dir = archive_data_dir or cfg.get_archive_dir_path()
  host_name_ext = (
      host_name_ext if host_name_ext is not None else cfg.get_host_name_ext()
  )
  tgz_archive_dir = (
      tgz_archive_dir
      or cfg.get_daily_archive_dir_path()
      or archive_data_dir
  )
  if not tar_path or not archive_data_dir or not tgz_archive_dir:
    return {}
  return remaining_raw_by_gz_blocking_tar_drop(
      tar_path=tar_path,
      archive_data_dir=archive_data_dir,
      host_name_ext=host_name_ext or "",
      tgz_archive_dir=tgz_archive_dir,
      get_quarantine_skip_paths=get_quarantine_skip_paths or (lambda: set()),
      get_maintenance_snapshot=get_maintenance_snapshot,
      log_fn=log_fn,
  )


def remaining_raw_blocking_day_incomplete(
  tar_path: str,
  *,
  archive_data_dir: Optional[str] = None,
  host_name_ext: Optional[str] = None,
  tgz_archive_dir: Optional[str] = None,
  get_quarantine_skip_paths: Optional[Callable[[], Set[str]]] = None,
  get_maintenance_snapshot: Optional[Callable[[], Any]] = None,
  log_fn: Any | None = None,
) -> Dict[str, List[str]]:
  """
  Deprecated alias for :func:`blocking_closed_raw_remains_for_day`.
  
  Args:
    tar_path (str): String for tar path.
    archive_data_dir (Optional[str]): Archive data dir, or None when absent.
    host_name_ext (Optional[str]): Host name ext, or None when absent.
    tgz_archive_dir (Optional[str]): Tgz archive dir, or None when absent.
    get_quarantine_skip_paths (Optional[Callable[[], Set[str]]]): Get
    quarantine skip paths, or None when absent.
    get_maintenance_snapshot (Optional[Callable[[], Any]]): Get maintenance
    snapshot, or None when absent.
    log_fn (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Dict[str, List[str]]: Dict[str, List[str]] produced by this call.
  
  Examples:
    >>> remaining_raw_blocking_day_incomplete(0)  # doctest: +SKIP
  """
  return blocking_closed_raw_remains_for_day(
      tar_path,
      archive_data_dir=archive_data_dir,
      host_name_ext=host_name_ext,
      tgz_archive_dir=tgz_archive_dir,
      get_quarantine_skip_paths=get_quarantine_skip_paths,
      get_maintenance_snapshot=get_maintenance_snapshot,
      log_fn=log_fn,
  )


class DayRawRemovalCoordinator:
  """
  Registry of per-day verify/delete state machines.
  
  Attributes:
    _days: Attribute.
    _days_lock: Attribute.
    _last_closed_raw_kick_action: Attribute.
    _session_executor: Attribute.
    archive_data_dir: Attribute.
    classify_quarantine_skip_path: Attribute.
    enabled: Attribute.
    get_allow_day_scoped_closed_raw: Attribute.
    get_ingest_active_skip_paths: Attribute.
    get_maintenance_snapshot: Attribute.
    get_quarantine_skip_paths: Attribute.
    host_name_ext: Attribute.
    ingest_ready_fn: Attribute.
    log_fn: Attribute.
    on_handoff_to_ingest: Attribute.
    on_pipeline_complete: Attribute.
    process_title: Attribute.
    tgz_archive_dir: Attribute.
  """

  def __init__(
    self,
    *,
    archive_data_dir: str,
    host_name_ext: str,
    tgz_archive_dir: str,
    log_fn: Any,
    get_quarantine_skip_paths: Callable[[], Set[str]],
    ingest_ready_fn: Optional[Callable[[str], bool]] = None,
    process_title: str = "sync_timedb.py",
    on_pipeline_complete: Optional[Callable[[str], None]] = None,
    on_handoff_to_ingest: (
      Optional[Callable[[str, List[str], str], None]]
    ) = None,
    get_maintenance_snapshot: Optional[Callable[[], Any]] = None,
    get_ingest_active_skip_paths: Optional[Callable[[], Set[str]]] = None,
    classify_quarantine_skip_path: Optional[Callable[[str], str]] = None,
  ) -> None:
    """
    Initialize a new instance.
    
    Args:
      archive_data_dir (str): String for archive data dir.
      host_name_ext (str): String for host name ext.
      tgz_archive_dir (str): String for tgz archive dir.
      log_fn (Any): Callable invoked by this helper.
      get_quarantine_skip_paths (Callable[[], Set[str]]): Get quarantine skip
      paths.
      ingest_ready_fn (Optional[Callable[[str], bool]]): Ingest ready fn, or
      None when absent.
      process_title (str): String for process title.
      on_pipeline_complete (Optional[Callable[[str], None]]): On pipeline
      complete, or None when absent.
      on_handoff_to_ingest (Optional[Callable[[str, List[str], str], None]]):
      On handoff to ingest, or None when absent.
      get_maintenance_snapshot (Optional[Callable[[], Any]]): Get maintenance
      snapshot, or None when absent.
      get_ingest_active_skip_paths (Optional[Callable[[], Set[str]]]): Get
      ingest active skip paths, or None when absent.
      classify_quarantine_skip_path (Optional[Callable[[str], str]]): Classify
      quarantine skip path, or None when absent.
    
    Returns:
      None
    
    Examples:
      >>> __init__(0)  # doctest: +SKIP
    """
    self.archive_data_dir = archive_data_dir
    self.host_name_ext = host_name_ext
    self.tgz_archive_dir = tgz_archive_dir
    self.log_fn = log_fn
    self.get_quarantine_skip_paths = get_quarantine_skip_paths
    self.ingest_ready_fn = ingest_ready_fn
    self.get_ingest_active_skip_paths = get_ingest_active_skip_paths
    self.classify_quarantine_skip_path = classify_quarantine_skip_path
    self.process_title = process_title
    self.on_pipeline_complete = on_pipeline_complete
    self.on_handoff_to_ingest = on_handoff_to_ingest
    self.get_maintenance_snapshot = get_maintenance_snapshot
    self.get_allow_day_scoped_closed_raw = lambda: True
    self.enabled = True
    self._days: Dict[str, _DayRawRemovalState] = {}
    self._days_lock = threading.Lock()
    self._last_closed_raw_kick_action: Optional[str] = None
    self._session_executor = SessionSingleFlightExecutor(
        thread_name_prefix="day-raw-removal",
        process_title=self.process_title,
        thread_role="day-raw-removal-verify",
        enabled=True,
    )
    cleanup_orphan_fnctl_lock_sidecars(
        day_removal_manifest_dir(self.archive_data_dir),
    )

  def _get_or_create_day(self, tar_path: str) -> _DayRawRemovalState:
    """
    Internal helper to return the or create day.
    
    Args:
      tar_path (str): String for tar path.
    
    Returns:
      _DayRawRemovalState: _DayRawRemovalState produced by this call.
    
    Examples:
      >>> DayRawRemovalCoordinator()._get_or_create_day("x")  # doctest: +SKIP
    """
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
          get_ingest_active_skip_paths=self.get_ingest_active_skip_paths,
          classify_quarantine_skip_path=self.classify_quarantine_skip_path,
          get_allow_day_scoped_closed_raw=self.get_allow_day_scoped_closed_raw,
      )
      self._days[tar_norm] = state
      return state

  def phase(self, tar_path: str) -> str:
    """
    Return the current phase for this object.
    
    Args:
      tar_path (str): String for tar path.
    
    Returns:
      str: str produced by this call.
    
    Examples:
      >>> DayRawRemovalCoordinator().phase("x")  # doctest: +SKIP
    """
    return self._get_or_create_day(tar_path).phase()

  def verification_complete(self, tar_path: str) -> bool:
    """
    Verification complete.
    
    Args:
      tar_path (str): String for tar path.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> DayRawRemovalCoordinator().verification_complete("x")  # doctest: +SKIP
    """
    return self._get_or_create_day(tar_path).verification_complete()

  def pre_seal_verification_complete(self, tar_path: str) -> bool:
    """
    Pre seal verification complete.
    
    Args:
      tar_path (str): String for tar path.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> DayRawRemovalCoordinator().pre_seal_verification_complete("x")
    """
    return self._get_or_create_day(tar_path).pre_seal_verification_complete()

  def post_seal_verification_complete(self, tar_path: str) -> bool:
    """
    Post seal verification complete.
    
    Args:
      tar_path (str): String for tar path.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> DayRawRemovalCoordinator().post_seal_verification_complete("x")
    """
    return self._get_or_create_day(tar_path).post_seal_verification_complete()

  def promote_phase_if_verify_stage_ahead(self, tar_path: str) -> bool:
    """
    Promote phase if verify stage ahead.
    
    Args:
      tar_path (str): String for tar path.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> DayRawRemovalCoordinator().promote_phase_if_verify_stage_ahead("x")
    """
    return self._get_or_create_day(tar_path).promote_phase_if_verify_stage_ahead()

  def should_handoff_before_seal(self, tar_path: str) -> bool:
    """
    True when closed raw handoff paths exist (pre-seal gate; paths only).
    
    Args:
      tar_path (str): String for tar path.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> DayRawRemovalCoordinator().should_handoff_before_seal("x")
    """
    return self.handoff_paths_exist_before_seal(tar_path)

  def handoff_paths_exist_before_seal(self, tar_path: str) -> bool:
    """
    Handoff paths exist before seal.
    
    Args:
      tar_path (str): String for tar path.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> DayRawRemovalCoordinator().handoff_paths_exist_before_seal("x")
    """
    return bool(self._get_or_create_day(tar_path).handoff_paths_for_ingest())

  def run_pre_seal_verify_sync(
    self,
    tar_path: str,
    *,
    max_classify_batches: int | None = None,
  ) -> bool:
    """
    Run the pre seal verify sync.

    Args:
      tar_path (str): String for tar path.
      max_classify_batches (int | None): When set, classify at most this many
        batches then return False for cursor resume (day-close claim).

    Returns:
      bool: True or False for this check.

    Examples:
      >>> DayRawRemovalCoordinator().run_pre_seal_verify_sync("x")
    """
    if not self.enabled:
      return True
    state = self._get_or_create_day(tar_path)
    if state.pre_seal_verification_complete():
      return True
    return state._pre_seal_verify_body(max_classify_batches=max_classify_batches)

  def update_reconcile_progress(
    self,
    tar_path: str,
    *,
    worker_stage: str = "",
    members_done: int | None = None,
    members_total: int | None = None,
    last_progress_ts: float | None = None,
  ) -> None:
    """
    Persist reconcile progress on the day raw-removal manifest.

    Args:
      tar_path (str): Daily tar path.
      worker_stage (str): Stage label.
      members_done (int | None): Members copied.
      members_total (int | None): Union size.
      last_progress_ts (float | None): Progress timestamp.

    Returns:
      None

    Examples:
      >>> DayRawRemovalCoordinator().update_reconcile_progress("x")
    """
    if not self.enabled:
      return
    self._get_or_create_day(tar_path).update_reconcile_progress(
        worker_stage=worker_stage,
        members_done=members_done,
        members_total=members_total,
        last_progress_ts=last_progress_ts,
    )

  def run_post_seal_verify_sync(self, tar_path: str) -> bool:
    """
    Run the post seal verify sync.
    
    Args:
      tar_path (str): String for tar path.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> DayRawRemovalCoordinator().run_post_seal_verify_sync("x")
    """
    if not self.enabled:
      return True
    state = self._get_or_create_day(tar_path)
    if state.post_seal_verification_complete():
      return True
    return state._post_seal_verify_body()

  def needs_delete_phase(self, tar_path: str) -> bool:
    """
    Needs delete phase.
    
    Args:
      tar_path (str): String for tar path.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> DayRawRemovalCoordinator().needs_delete_phase("x")  # doctest: +SKIP
    """
    return self._get_or_create_day(tar_path).needs_delete_phase()

  def delete_phase_done(self, tar_path: str) -> bool:
    """
    Delete the phase done.
    
    Args:
      tar_path (str): String for tar path.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> DayRawRemovalCoordinator().delete_phase_done("x")  # doctest: +SKIP
    """
    return self._get_or_create_day(tar_path).delete_phase_done()

  def reclassify_retryable_skips_after_handoff_sync(self, tar_path: str) -> int:
    """
    Re-run classify on retryable manifest skips after handoff ingest succeeds.
    
    Args:
      tar_path (str): String for tar path.
    
    Returns:
      int: int produced by this call.
    
    Examples:
      >>> reclassify_retryable_skips_after_handoff_sync(0)  # doctest: +SKIP
    """
    if not self.enabled:
      return 0
    return self._get_or_create_day(tar_path)._reclassify_retryable_skips_on_disk()

  def raw_removal_progress_summary(self, tar_path: str) -> Dict[str, Any]:
    """
    Raw removal progress summary.
    
    Args:
      tar_path (str): String for tar path.
    
    Returns:
      Dict[str, Any]: Dict[str, Any] produced by this call.
    
    Examples:
      >>> DayRawRemovalCoordinator().raw_removal_progress_summary("x")
    """
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
    """
    Pipeline future done.
    
    Args:
      tar_path (str): String for tar path.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> DayRawRemovalCoordinator().pipeline_future_done("x")  # doctest: +SKIP
    """
    tar_norm = os.path.normpath(tar_path or "")
    with self._days_lock:
      state = self._days.get(tar_norm)
    if state is None:
      return True
    future = state._pipeline_future
    return future is None or future.done()

  def paths_pending_delete(self) -> Set[str]:
    """
    Paths pending delete.
    
    Returns:
      Set[str]: Set[str] produced by this call.
    
    Examples:
      >>> DayRawRemovalCoordinator().paths_pending_delete()  # doctest: +SKIP
    """
    pending: Set[str] = set()
    with self._days_lock:
      states = list(self._days.values())
    for state in states:
      pending |= state.paths_pending_delete()
    return pending

  def consumed_paths(self) -> Set[str]:
    """
    Consumed paths.
    
    Returns:
      Set[str]: Set[str] produced by this call.
    
    Examples:
      >>> DayRawRemovalCoordinator().consumed_paths()  # doctest: +SKIP
    """
    removed: Set[str] = set()
    with self._days_lock:
      states = list(self._days.values())
    for state in states:
      removed |= state.consumed_paths()
    return removed

  def any_needs_delete_phase(self) -> bool:
    """
    Any needs delete phase.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> DayRawRemovalCoordinator().any_needs_delete_phase()  # doctest: +SKIP
    """
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
    """
    Reopen ``phase=done`` days that still have verified paths on disk.
    
    Returns:
      int: int produced by this call.
    
    Examples:
      >>> DayRawRemovalCoordinator().reopen_done_days_with_verified_on_disk()
    """
    reopened = 0
    with self._days_lock:
      states = list(self._days.values())
    for state in states:
      if state.reopen_delete_phase_if_verified_on_disk():
        reopened += 1
      elif state.reopen_stale_done_all_skipped():
        reopened += 1
    return reopened

  def advance_raw_removal_blockers(self) -> bool:
    """
    Kick verify/quarantine for days that block startup drain without delete.
    
      work.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> DayRawRemovalCoordinator().advance_raw_removal_blockers()
    """
    if not self.enabled:
      return False
    progressed = False
    with self._days_lock:
      states = list(self._days.values())
    for state in states:
      if not state.has_active_raw_removal_work():
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
    """
    Return (blocking_day_count, oldest_summary_token) for drain telemetry.
    
    Returns:
      Tuple[int, str]: Tuple[int, str] produced by this call.
    
    Examples:
      >>> DayRawRemovalCoordinator().blocking_startup_drain_summary()
    """
    blockers: List[Tuple[Any, str]] = []
    with self._days_lock:
      states = list(self._days.values())
    for state in states:
      if not state.has_active_raw_removal_work():
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
    """
    Any needs tar drop finish.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> DayRawRemovalCoordinator().any_needs_tar_drop_finish()  # doctest: +SKIP
    """
    return bool(self.days_needing_tar_drop_oldest_first())

  def days_needing_tar_drop_oldest_first(self) -> List[str]:
    """
    Days needing tar drop oldest first.
    
    Returns:
      List[str]: List[str] produced by this call.
    
    Examples:
      >>> DayRawRemovalCoordinator().days_needing_tar_drop_oldest_first()
    """
    candidates = []
    with self._days_lock:
      states = list(self._days.values())
    for state in states:
      if not os.path.isfile(state.tar_path):
        continue
      zst_path, gz_path = compressed_sibling_paths(state.tar_path)
      if not (os.path.isfile(zst_path) or os.path.isfile(gz_path)):
        continue
      blocking = state._remaining_raw_paths_blocking_tar_drop()
      if remaining_raw_by_gz_has_paths_on_disk(blocking, zst_path):
        continue
      candidates.append((state.day_date, state.tar_path))
    candidates.sort(key=lambda item: item[0])
    return [tar_path for _day_date, tar_path in candidates]

  def remaining_raw_paths_blocking_tar_drop(
    self,
    tar_path: str,
  ) -> Dict[str, List[str]]:
    """
    Remaining raw paths blocking tar drop.
    
    Args:
      tar_path (str): String for tar path.
    
    Returns:
      Dict[str, List[str]]: Dict[str, List[str]] produced by this call.
    
    Examples:
      >>> DayRawRemovalCoordinator().remaining_raw_paths_blocking_tar_drop("x")
    """
    return self._get_or_create_day(tar_path)._remaining_raw_paths_blocking_tar_drop()

  def try_finish_tar_drop_if_ready(self, tar_path: str) -> bool:
    """
    Return True if the try finish tar drop if is ready.
    
    Args:
      tar_path (str): String for tar path.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> DayRawRemovalCoordinator().try_finish_tar_drop_if_ready("x")
    """
    state = self._get_or_create_day(tar_path)
    if not state.try_finish_tar_drop_if_ready():
      return False
    if state.delete_phase_done():
      self._notify_delete_complete(tar_path)
    return True

  def any_active_raw_removal_work(self) -> bool:
    """
    Any active raw removal work.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> DayRawRemovalCoordinator().any_active_raw_removal_work()
    """
    if not self.enabled:
      return False
    with self._days_lock:
      states = list(self._days.values())
    return any(state.has_active_raw_removal_work() for state in states)

  def count_days_waiting_on_ingest(self) -> int:
    """
    Count the days waiting on ingest.
    
    Returns:
      int: int produced by this call.
    
    Examples:
      >>> DayRawRemovalCoordinator().count_days_waiting_on_ingest()
    """
    if not self.enabled:
      return 0
    with self._days_lock:
      states = list(self._days.values())
    return sum(1 for state in states if state.waiting_on_ingest_at_startup())

  def oldest_day_needing_delete(self) -> Optional[str]:
    """
    Oldest day needing delete.
    
    Returns:
      Optional[str]: Optional[str] — the result, or None when unavailable.
    
    Examples:
      >>> DayRawRemovalCoordinator().oldest_day_needing_delete()  # doctest: +SKIP
    """
    days = self.days_needing_delete_oldest_first()
    return days[0] if days else None

  def days_needing_delete_oldest_first(self) -> List[str]:
    """
    Days needing delete oldest first.
    
    Returns:
      List[str]: List[str] produced by this call.
    
    Examples:
      >>> DayRawRemovalCoordinator().days_needing_delete_oldest_first()
    """
    candidates = []
    with self._days_lock:
      states = list(self._days.values())
    for state in states:
      if (
          (state.needs_delete_phase() and not state.delete_phase_done())
          or state.needs_ghost_delete_retry()
          or state.needs_reopen_for_verified_pending()
      ):
        candidates.append((state.day_date, state.tar_path))
    candidates.sort(key=lambda item: item[0])
    return [tar_path for _day_date, tar_path in candidates]

  def should_handoff_to_ingest(self, tar_path: str) -> bool:
    """
    Return True if handoff to ingest.
    
    Args:
      tar_path (str): String for tar path.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> DayRawRemovalCoordinator().should_handoff_to_ingest("x")
    """
    return self.handoff_eligible_after_verify(tar_path)

  def handoff_eligible_after_verify(self, tar_path: str) -> bool:
    """
    Handoff eligible after verify.
    
    Args:
      tar_path (str): String for tar path.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> DayRawRemovalCoordinator().handoff_eligible_after_verify("x")
    """
    return self._get_or_create_day(tar_path).should_handoff_day_close_to_ingest()

  def has_closed_raw_on_disk(self, tar_path: str) -> bool:
    """
    Return True if closed raw on disk.
    
    Args:
      tar_path (str): String for tar path.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> DayRawRemovalCoordinator().has_closed_raw_on_disk("x")  # doctest: +SKIP
    """
    return self._get_or_create_day(tar_path)._has_closed_raw_existing_on_disk()

  def closed_raw_paths_on_disk(self, tar_path: str) -> List[str]:
    """
    Closed raw paths on disk.
    
    Args:
      tar_path (str): String for tar path.
    
    Returns:
      List[str]: List[str] produced by this call.
    
    Examples:
      >>> DayRawRemovalCoordinator().closed_raw_paths_on_disk("x")
    """
    state = self._get_or_create_day(tar_path)
    return [
        path
        for path in state._closed_raw_paths_on_disk()
        if os.path.isfile(path)
    ]

  def _closed_raw_path_is_quarantine_skip(self, path: str) -> bool:
    """
    Internal helper to handle closed raw path is quarantine skip.
    
    Args:
      path (str): String for path.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> DayRawRemovalCoordinator()._closed_raw_path_is_quarantine_skip("x")
    """
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

  def rescan_exclude_paths(self) -> Set[str]:
    """
    Paths that must stay out of pending rescan during handoff/delete drain.
    
    Computes handoff/requeue paths **once** per tracked day. Does not use
    ``handoff_paths_for_ingest()`` as a boolean probe (that builds remaining-
      raw).
    
    Verifying×exclude×handoff deadlock: while ``phase=verifying`` and not yet
    ``verification_complete``, retryables stay **eligible for pending** —
    ``should_handoff_day_close_to_ingest`` requires verification_complete, so
    excluding them starves both pending and handoff.
    
    Returns:
      Set[str]: Set[str] produced by this call.
    
    Examples:
      >>> DayRawRemovalCoordinator().rescan_exclude_paths()  # doctest: +SKIP
    """
    excluded: Set[str] = set()
    with self._days_lock:
      tar_paths = list(self._days.keys())
    for tar_norm in tar_paths:
      state = self._get_or_create_day(tar_norm)
      if (
          state.phase() == PHASE_VERIFYING
          and not state.verification_complete()
      ):
        continue
      if not (
          state.delete_phase_done()
          or state.verification_complete()
          or state.has_active_raw_removal_work()
          or state.waiting_on_ingest_at_startup()
      ):
        continue
      excluded.update(self.paths_for_closed_raw_handoff_requeue(tar_norm))
    return excluded

  def paths_for_closed_raw_handoff_requeue(self, tar_path: str) -> List[str]:
    """
    Retryable/unmanifested closed raw only — not manifest-blocking verify paths.
    
    Args:
      tar_path (str): String for tar path.
    
    Returns:
      List[str]: List[str] produced by this call.
    
    Examples:
      >>> DayRawRemovalCoordinator().paths_for_closed_raw_handoff_requeue("x")
    """
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
    """
    True when manifest phase=done but non-retryable manifest paths block.
    
      DAY_CLOSE.
    
    Args:
      tar_path (str): String for tar path.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> DayRawRemovalCoordinator().needs_verify_for_closed_raw_block("x")
    """
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
    """
    Drive delete reopen, ghost retry, verify, or ingest handoff for closed-raw
    blockers. H18: never return pure ``noop`` while closed raw remains on disk;
    retryable-only remaining raw returns ``handoff`` (not ``begin_deleting``).
    
    Args:
      tar_path (str): String for tar path.
      reason (str): String for reason.
    
    Returns:
      str: Kick outcome (``handoff``, ``delete_reopen``, ``noop``, …).
    
    Examples:
      >>> DayRawRemovalCoordinator().kick_closed_raw_unblock("x", "x")
    """
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
        if blocking and all(path in retryable for path in blocking):
          upgraded = state._reclassify_retryable_skips_on_disk()
          if upgraded:
            if state.reopen_delete_phase_if_verified_on_disk():
              return "delete_reopen"
            state.begin_deleting()
            if state.phase() == PHASE_DELETING:
              if self.log_fn:
                self.log_fn(
                    "Day raw removal closed-raw delete kick tar=%s reason=%s "
                    "detail=reclassify_upgraded"
                    % (tar_norm, reason or ""),
                    flush=True,
                )
              return "delete_reopen"
          # H18: retryable-only remaining raw must drive ingest handoff —
          # never pure noop, and never begin_deleting (lock re-entry hang).
          paths = (
              self.paths_for_closed_raw_handoff_requeue(tar_path)
              or list(blocking)
          )
          if paths:
            if self.on_handoff_to_ingest is not None:
              try:
                self.on_handoff_to_ingest(
                    tar_norm,
                    paths,
                    reason or "closed_raw_unblock_retryable",
                )
              except Exception:
                if self.log_fn:
                  self.log_fn(
                      "Day raw removal closed-raw handoff callback failed "
                      "tar=%s" % tar_norm,
                      flush=True,
                  )
            if self.log_fn:
              self.log_fn(
                  "Day raw removal closed-raw handoff kick tar=%s reason=%s "
                  "detail=retryable_only"
                  % (tar_norm, reason or ""),
                  flush=True,
              )
            return "handoff"
        elif any(path not in retryable for path in blocking):
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
      # Fall through when phase=done with no blocking / no handoff paths —
      # has_closed may still need verify/delete reopen (H18).
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
      # Skip-only deleting (F15): reclassify, else durable ingest handoff.
      upgraded = state._reclassify_retryable_skips_on_disk()
      if upgraded:
        state.begin_deleting()
        self.apply_batch_delete(tar_path)
        if self.log_fn:
          self.log_fn(
              "Day raw removal closed-raw delete kick tar=%s reason=%s "
              "detail=reclassify_upgraded_deleting"
              % (tar_norm, reason or ""),
              flush=True,
          )
        return "delete_reopen"
      if state.should_handoff_day_close_to_ingest():
        self.complete_handoff_to_ingest(
            tar_path,
            reason=reason or "closed_raw_unblock_waiting_on_ingest",
        )
        if self.log_fn:
          self.log_fn(
              "Day raw removal closed-raw handoff kick tar=%s reason=%s"
              % (tar_norm, reason or ""),
              flush=True,
          )
        return "handoff"
    # H18: age-eligible closed raw remaining must never end as pure noop.
    if self.has_closed_raw_on_disk(tar_path):
      if state.delete_phase_done():
        if state.reopen_delete_phase_if_verified_on_disk():
          return "delete_reopen"
      paths = self.paths_for_closed_raw_handoff_requeue(tar_path)
      if paths and self.on_handoff_to_ingest is not None:
        try:
          self.on_handoff_to_ingest(
              tar_norm,
              paths,
              reason or "closed_raw_unblock_has_closed",
          )
        except Exception:
          if self.log_fn:
            self.log_fn(
                "Day raw removal closed-raw handoff callback failed tar=%s"
                % tar_norm,
                flush=True,
            )
        if self.log_fn:
          self.log_fn(
              "Day raw removal closed-raw handoff kick tar=%s reason=%s "
              "detail=has_closed_fallback"
              % (tar_norm, reason or ""),
              flush=True,
          )
        return "handoff"
      state.begin_deleting()
      if state.phase() == PHASE_DELETING:
        if self.log_fn:
          self.log_fn(
              "Day raw removal closed-raw delete kick tar=%s reason=%s "
              "detail=has_closed_fallback"
              % (tar_norm, reason or ""),
              flush=True,
          )
        return "delete_reopen"
      self.start_async_verify(tar_path)
      if self.log_fn:
        self.log_fn(
            "Day raw removal closed-raw verify kick tar=%s reason=%s "
            "detail=has_closed_fallback"
            % (tar_norm, reason or ""),
            flush=True,
        )
      return "verify"
    return "noop"

  def requeue_closed_raw_paths_for_ingest(
    self,
    tar_path: str,
    *,
    reason: str,
    paths: Optional[List[str]] = None,
  ) -> List[str]:
    """
    Requeue handoff-eligible closed raw; kick delete/verify when manifest.
    
      blocks.
    
    Args:
      tar_path (str): String for tar path.
      reason (str): String for reason.
      paths (Optional[List[str]]): Paths, or None when absent.
    
    Returns:
      List[str]: List[str] produced by this call.
    
    Examples:
      >>> requeue_closed_raw_paths_for_ingest(0)  # doctest: +SKIP
    """
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

  def kick_closed_raw_paths_to_ingest(
    self,
    tar_path: str,
    *,
    reason: str = "",
  ) -> List[str]:
    """
    Enqueue closed-raw paths for ingest without verify-handoff gating.

    Used by H17 day_close ``wait_on_ingest`` yield when
    ``complete_handoff_to_ingest`` returns empty because
    ``should_handoff_day_close_to_ingest`` is false.

    Args:
      tar_path (str): Daily ``.tar`` path.
      reason (str): Handoff reason token for the callback.

    Returns:
      List[str]: Paths passed to ``on_handoff_to_ingest`` (may be empty).

    Examples:
      >>> DayRawRemovalCoordinator().kick_closed_raw_paths_to_ingest("x")
      []
    """
    return self.requeue_closed_raw_paths_for_ingest(
        tar_path,
        reason=reason or "day_close_wait_on_ingest",
    )

  def discover_closed_raw_on_disk_handoffs(self) -> List[Tuple[str, List[str]]]:
    """
    Boot-time handoff for days with closed raw blockers (narrow path lists).
    
    Returns:
      List[Tuple[str, List[str]]]: List[Tuple[str, List[str]]] produced by
      this call.
    
    Examples:
      >>> DayRawRemovalCoordinator().discover_closed_raw_on_disk_handoffs()
    """
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
      needs_kick = (
          state.needs_ghost_delete_retry()
          or self.needs_verify_for_closed_raw_block(tar_path)
          or (
              not state.delete_phase_done()
              and not state.verification_complete()
          )
      )
      paths: List[str] = []
      if needs_kick or state.should_handoff_day_close_to_ingest():
        paths = self.paths_for_closed_raw_handoff_requeue(tar_path)
        needs_kick = needs_kick or bool(paths)
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
    """
    Finalize handoff state and invoke ``on_handoff_to_ingest`` when wired.
    
    Args:
      tar_path (str): String for tar path.
      reason (str): String for reason.
    
    Returns:
      List[str]: List[str] produced by this call.
    
    Examples:
      >>> DayRawRemovalCoordinator().complete_handoff_to_ingest("x", "x")
    """
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
    """
    Scan persisted per-day manifests for retryable-only handoff candidates.
    
    Returns:
      List[Tuple[str, List[str]]]: List[Tuple[str, List[str]]] produced by
      this call.
    
    Examples:
      >>> DayRawRemovalCoordinator().discover_manifest_handoffs()
    """
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
    """
    Internal helper to handle try handoff to ingest.
    
    Args:
      tar_path (str): String for tar path.
      reason (str): String for reason.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> DayRawRemovalCoordinator()._try_handoff_to_ingest("x", "x")
    """
    if not self.enabled or self.on_handoff_to_ingest is None:
      return False
    paths = self.complete_handoff_to_ingest(tar_path, reason=reason)
    return bool(paths)

  def verified_paths_pending_delete(self, tar_path: str) -> Set[str]:
    """
    Verified paths pending delete.
    
    Args:
      tar_path (str): String for tar path.
    
    Returns:
      Set[str]: Set[str] produced by this call.
    
    Examples:
      >>> DayRawRemovalCoordinator().verified_paths_pending_delete("x")
    """
    return self._get_or_create_day(tar_path).paths_pending_delete()

  def _verify_pipeline_body(self, state: _DayRawRemovalState) -> None:
    """
    Internal helper to handle verify pipeline body.
    
    Args:
      state (_DayRawRemovalState): State.
    
    Returns:
      None
    
    Examples:
      >>> DayRawRemovalCoordinator()._verify_pipeline_body(None)  # doctest: +SKIP
    """
    close_old_connections()
    if state.delete_phase_done():
      if state._needs_retry_after_ingest():
        state._reset_for_reverify()
      else:
        return
    if shutdown_requested[0]:
      return
    if not state.verification_complete():
      state._verify_body()
    if shutdown_requested[0]:
      return
    if not state.verification_complete():
      return
    self._try_handoff_to_ingest(
        state.tar_path,
        reason="verify_pipeline_complete",
    )

  def _submit_async_verify(self, state: _DayRawRemovalState) -> None:
    """
    Internal helper to handle submit async verify.
    
    Args:
      state (_DayRawRemovalState): State.
    
    Returns:
      None
    
    Examples:
      >>> DayRawRemovalCoordinator()._submit_async_verify(None)  # doctest: +SKIP
    """
    if state._pipeline_future is not None and not state._pipeline_future.done():
      return
    state._pipeline_future = self._session_executor.submit(
        self._verify_pipeline_body,
        state,
    )

  def run_verify_sync(
    self,
    tar_path: str,
    *,
    sealed_members: Any | None = None,
  ) -> None:
    """
    Run verify for one calendar day on the caller thread (janitor path).
    
    Args:
      tar_path (str): String for tar path.
      sealed_members (Any | None): One of ``Any``, ``None``.
    
    Returns:
      None
    
    Examples:
      >>> DayRawRemovalCoordinator().run_verify_sync("x", None)  # doctest: +SKIP
    """
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
    self._verify_pipeline_body(state)

  def start_async_verify(
    self,
    tar_path: str,
    *,
    sealed_members: Any | None = None,
  ) -> None:
    """
    Run verify for one calendar day on a background thread (production path).
    
    Args:
      tar_path (str): String for tar path.
      sealed_members (Any | None): One of ``Any``, ``None``.
    
    Returns:
      None
    
    Examples:
      >>> DayRawRemovalCoordinator().start_async_verify("x", None)
    """
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
    """
    Backward-compatible alias: verify-only async (delete on supervisor thread).
    
    Args:
      tar_path (str): String for tar path.
    
    Returns:
      None
    
    Examples:
      >>> DayRawRemovalCoordinator().start_async_day_pipeline("x")
    """
    self.start_async_verify(tar_path)

  def _notify_delete_complete(self, tar_path: str) -> None:
    """
    Internal helper to handle notify delete complete.
    
    Args:
      tar_path (str): String for tar path.
    
    Returns:
      None
    
    Examples:
      >>> DayRawRemovalCoordinator()._notify_delete_complete("x")
    """
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
    """
    Begin deleting.
    
    Args:
      tar_path (str): String for tar path.
    
    Returns:
      None
    
    Examples:
      >>> DayRawRemovalCoordinator().begin_deleting("x")  # doctest: +SKIP
    """
    self._get_or_create_day(tar_path).begin_deleting()

  def apply_batch_delete(self, tar_path: str) -> int:
    """
    Apply the batch delete.
    
    Args:
      tar_path (str): String for tar path.
    
    Returns:
      int: int produced by this call.
    
    Examples:
      >>> DayRawRemovalCoordinator().apply_batch_delete("x")  # doctest: +SKIP
    """
    state = self._get_or_create_day(tar_path)
    # One closed_raw census for reclassify + delete completion + handoff.
    state._begin_closed_raw_pass_memo()
    try:
      # Branch C: reclassify under deleting/verification_complete before delete or
      # handoff so post-ingest upgrades are not skipped (F15 keeps phase=deleting).
      upgraded = state._reclassify_retryable_skips_on_disk()
      if upgraded and self.log_fn:
        self.log_fn(
            "Day raw removal reclassify before batch_delete tar=%s upgraded=%d"
            % (tar_path, upgraded),
            flush=True,
        )
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
    finally:
      state._clear_closed_raw_pass_memo()

  def shutdown(self, wait: bool = True) -> None:
    """
    Shut down this object and release resources.
    
    Args:
      wait (bool): Boolean flag for wait.
    
    Returns:
      None
    
    Examples:
      >>> DayRawRemovalCoordinator().shutdown(True)  # doctest: +SKIP
    """
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
    self._session_executor.shutdown(wait=wait)
