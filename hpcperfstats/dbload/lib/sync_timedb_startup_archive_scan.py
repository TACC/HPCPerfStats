"""
Single-flight canonical startup archive maintenance snapshot.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional

import hpcperfstats.dbload.lib.conf_parser as cfg
from hpcperfstats.dbload.lib import sync_timedb_retired_b_defaults as _bdef

from hpcperfstats.dbload.lib.sync_timedb_archive_maint import (
    ArchiveMaintenanceSnapshot,
    build_archive_maintenance_snapshot,
)


def copy_archive_maintenance_snapshot(
  snapshot: ArchiveMaintenanceSnapshot,
) -> ArchiveMaintenanceSnapshot:
  """
  Deep-copy list values in mapping/remaining so accrual trim cannot alias.
  
  Args:
    snapshot (ArchiveMaintenanceSnapshot): Snapshot.
  
  Returns:
    ArchiveMaintenanceSnapshot: ArchiveMaintenanceSnapshot produced by this
    call.
  
  Examples:
    >>> copy_archive_maintenance_snapshot(None)  # doctest: +SKIP
  """
  return ArchiveMaintenanceSnapshot(
      closed_paths=list(snapshot.closed_paths),
      first_timestamp_by_path=dict(snapshot.first_timestamp_by_path),
      head_identity_by_path=dict(snapshot.head_identity_by_path),
      gate_identities_by_path={
          path: {host: set(seconds) for host, seconds in hosts.items()}
          for path, hosts in (snapshot.gate_identities_by_path or {}).items()
      },
      mapping={
          key: list(paths)
          for key, paths in (snapshot.mapping or {}).items()
      },
      remaining_raw_by_gz={
          key: list(paths)
          for key, paths in (snapshot.remaining_raw_by_gz or {}).items()
      },
      ready_paths=set(snapshot.ready_paths),
      head_read_stats=dict(snapshot.head_read_stats),
  )


class StartupArchiveScanCoordinator:
  """
  Publish/wait for one startup ``ArchiveMaintenanceSnapshot`` (single-flight).
  
  Attributes:
    _builder_count: Attribute.
    _building: Attribute.
    _cond: Attribute.
    _lock: Attribute.
    _published_by_janitor: Attribute.
    _snapshot: Attribute.
    _startup_heavy_gate_active: Attribute.
    _startup_heavy_maintenance_finished: Attribute.
    _startup_heavy_maintenance_in_progress: Attribute.
    _startup_maintenance_pending: Attribute.
    archive_data_dir: Attribute.
    host_name_ext: Attribute.
    log_fn: Attribute.
    tgz_archive_dir: Attribute.
  """

  def __init__(
    self,
    *,
    archive_data_dir: str,
    host_name_ext: str,
    tgz_archive_dir: str,
    log_fn: Any | None = None,
  ) -> None:
    """
    Initialize a new instance.
    
    Args:
      archive_data_dir (str): String for archive data dir.
      host_name_ext (str): String for host name ext.
      tgz_archive_dir (str): String for tgz archive dir.
      log_fn (Any | None): One of ``Any``, ``None``.
    
    Returns:
      None
    
    Examples:
      >>> StartupArchiveScanCoordinator("x", "x", "x", None)  # doctest: +SKIP
    """
    self.archive_data_dir = archive_data_dir
    self.host_name_ext = host_name_ext
    self.tgz_archive_dir = tgz_archive_dir
    self.log_fn = log_fn
    self._lock = threading.Lock()
    self._cond = threading.Condition(self._lock)
    self._snapshot: Optional[ArchiveMaintenanceSnapshot] = None
    self._building = False
    self._builder_count = 0
    self._published_by_janitor = False
    self._startup_maintenance_pending = False
    self._startup_heavy_gate_active = False
    self._startup_heavy_maintenance_in_progress = False
    self._startup_heavy_maintenance_finished = False

  def note_startup_maintenance_pending(self) -> None:
    """
    Supervisor signals janitor startup maintenance before first publish.
    
    Returns:
      None
    
    Examples:
      >>> StartupArchiveScanCoordinator().note_startup_maintenance_pending()
    """
    with self._cond:
      self._startup_maintenance_pending = True
      self._startup_heavy_gate_active = True
      self._cond.notify_all()

  def begin_build(self) -> None:
    """
    Mark snapshot build in flight (janitor or single-flight fallback builder).
    
    Returns:
      None
    
    Examples:
      >>> StartupArchiveScanCoordinator().begin_build()  # doctest: +SKIP
    """
    with self._cond:
      self._building = True

  def abort_build(self) -> None:
    """
    Abort build.
    
    Returns:
      None
    
    Examples:
      >>> StartupArchiveScanCoordinator().abort_build()  # doctest: +SKIP
    """
    with self._cond:
      self._building = False
      self._cond.notify_all()

  def publish(
    self,
    snapshot: ArchiveMaintenanceSnapshot,
    *,
    from_janitor: bool = False,
  ) -> None:
    """
    Publish state for downstream consumers.
    
    Args:
      snapshot (ArchiveMaintenanceSnapshot): Snapshot.
      from_janitor (bool): Boolean flag for from janitor.
    
    Returns:
      None
    
    Examples:
      >>> StartupArchiveScanCoordinator().publish(None, True)  # doctest: +SKIP
    """
    published = copy_archive_maintenance_snapshot(snapshot)
    with self._cond:
      self._snapshot = published
      self._building = False
      if from_janitor:
        self._published_by_janitor = True
        self._startup_maintenance_pending = False
      self._cond.notify_all()

  def get_snapshot(self) -> Optional[ArchiveMaintenanceSnapshot]:
    """
    Return the snapshot.
    
    Returns:
      Optional[ArchiveMaintenanceSnapshot]:
      Optional[ArchiveMaintenanceSnapshot] — the result, or None when
      unavailable.
    
    Examples:
      >>> StartupArchiveScanCoordinator().get_snapshot()  # doctest: +SKIP
    """
    with self._lock:
      return self._snapshot

  def mark_startup_heavy_maintenance_started(self) -> None:
    """
    Janitor ``run_heavy_maintenance_pass(reason=startup)`` entry.
    
    Returns:
      None
    
    Examples:
      >>> StartupArchiveScanCoordinator().mark_startup_heavy_maintenance_started()
    """
    with self._cond:
      self._startup_heavy_maintenance_in_progress = True
      self._cond.notify_all()

  def mark_startup_heavy_maintenance_finished(self) -> None:
    """
    Janitor startup heavy pass complete (snapshot publish + candidate report).
    
    Returns:
      None
    
    Examples:
      >>> mark_startup_heavy_maintenance_finished(0)  # doctest: +SKIP
    """
    with self._cond:
      self._startup_heavy_maintenance_in_progress = False
      self._startup_heavy_maintenance_finished = True
      self._startup_maintenance_pending = False
      self._startup_heavy_gate_active = False
      self._cond.notify_all()

  def is_startup_heavy_maintenance_idle(self) -> bool:
    """
    Return True if startup heavy maintenance idle.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> StartupArchiveScanCoordinator().is_startup_heavy_maintenance_idle()
    """
    with self._lock:
      return self._is_startup_heavy_maintenance_idle_locked()

  def _is_startup_heavy_maintenance_idle_locked(self) -> bool:
    """
    Internal helper to check if startup heavy maintenance idle locked.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> _is_startup_heavy_maintenance_idle_locked(0)  # doctest: +SKIP
    """
    if not self._startup_heavy_gate_active:
      return True
    return self._startup_heavy_maintenance_finished

  def wait_for_startup_maintenance_idle(
    self,
    *,
    timeout_s: Optional[float] = None,
  ) -> bool:
    """
    Block until janitor startup heavy pass finishes; False on timeout.
    
    Args:
      timeout_s (Optional[float]): Timeout s, or None when absent.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> StartupArchiveScanCoordinator().wait_for_startup_maintenance_idle(None)
    """
    if timeout_s is None:
      timeout_s = max(
          600.0,
          float(_bdef.SYNC_STARTUP_SNAPSHOT_WAIT_SECONDS) * 4.0,
      )
    wait_t0 = time.time()
    while True:
      with self._cond:
        if self._is_startup_heavy_maintenance_idle_locked():
          return True
        elapsed = time.time() - wait_t0
        if elapsed >= float(timeout_s):
          return False
        remaining = max(0.05, float(timeout_s) - elapsed)
        self._cond.wait(timeout=min(1.0, remaining))

  def _effective_wait_timeout_s_locked(self, wait_t0: float) -> float:
    """
    Caller must hold ``self._cond`` lock (same as ``self._lock``).
    
    Args:
      wait_t0 (float): Floating-point value for wait t0.
    
    Returns:
      float: float produced by this call.
    
    Examples:
      >>> StartupArchiveScanCoordinator()._effective_wait_timeout_s_locked(0)
    """
    base = _bdef.SYNC_STARTUP_SNAPSHOT_WAIT_SECONDS
    if self._startup_maintenance_pending and self._snapshot is None:
      return max(base, time.time() - wait_t0 + base)
    return base

  def _log_snapshot_ready(
    self,
    snapshot: ArchiveMaintenanceSnapshot,
    wait_s: float,
  ) -> None:
    """
    Internal helper to check if the log snapshot is ready.
    
    Args:
      snapshot (ArchiveMaintenanceSnapshot): Snapshot.
      wait_s (float): Floating-point value for wait s.
    
    Returns:
      None
    
    Examples:
      >>> StartupArchiveScanCoordinator()._log_snapshot_ready(None, 0)
    """
    if not self.log_fn:
      return
    closed_n = len(snapshot.closed_paths)
    if not closed_n and snapshot.mapping:
      closed_n = sum(len(v) for v in snapshot.mapping.values())
    builders = 1 if self._published_by_janitor else max(1, self._builder_count)
    self.log_fn(
        "startup archive scan ready paths=%d wait_s=%.3f "
        "builders=%d"
        % (closed_n, wait_s, builders),
        flush=True,
    )

  def _try_claim_builder_locked(self) -> bool:
    """
    Internal helper to handle try claim builder locked.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> StartupArchiveScanCoordinator()._try_claim_builder_locked()
    """
    if self._building or self._snapshot is not None:
      return False
    self._building = True
    self._builder_count += 1
    return True

  def wait_for_snapshot(
    self,
    *,
    allow_build: bool = True,
    build_fn: Optional[Callable[[], ArchiveMaintenanceSnapshot]] = None,
  ) -> ArchiveMaintenanceSnapshot:
    """
    Block until snapshot exists; single-flight fallback build when allowed.
    
    Args:
      allow_build (bool): Boolean flag for allow build.
      build_fn (Optional[Callable[[], ArchiveMaintenanceSnapshot]]): Build fn,
      or None when absent.
    
    Returns:
      ArchiveMaintenanceSnapshot: ArchiveMaintenanceSnapshot produced by this
      call.
    
    Raises:
      Exception: Raised when ``wait_for_snapshot`` hits a ``Exception``
      failure path.
    
    Examples:
      >>> StartupArchiveScanCoordinator().wait_for_snapshot(True, None)
    """
    wait_t0 = time.time()
    became_builder = False
    while True:
      with self._cond:
        if self._snapshot is not None:
          wait_s = time.time() - wait_t0
          snapshot = self._snapshot
          self._log_snapshot_ready(snapshot, wait_s)
          return snapshot

        elapsed = time.time() - wait_t0
        timeout_s = self._effective_wait_timeout_s_locked(wait_t0)
        if allow_build and not self._building and elapsed >= timeout_s:
          became_builder = self._try_claim_builder_locked()
          if became_builder:
            break

        if not became_builder:
          remaining = max(0.05, timeout_s - elapsed)
          self._cond.wait(timeout=min(1.0, remaining))

      if became_builder:
        break

    builder = build_fn or self._default_build
    try:
      snapshot = builder()
    except Exception:
      self.abort_build()
      raise
    published = copy_archive_maintenance_snapshot(snapshot)
    with self._cond:
      if self._snapshot is not None:
        wait_s = time.time() - wait_t0
        self._log_snapshot_ready(self._snapshot, wait_s)
        self._building = False
        self._cond.notify_all()
        return self._snapshot
      self._snapshot = published
      self._building = False
      self._cond.notify_all()
    wait_s = time.time() - wait_t0
    self._log_snapshot_ready(published, wait_s)
    return published

  def wait_or_build_snapshot(
    self,
    *,
    build_fn: Optional[Callable[[], ArchiveMaintenanceSnapshot]] = None,
  ) -> ArchiveMaintenanceSnapshot:
    """
    Backward-compatible alias; never returns None.
    
    Args:
      build_fn (Optional[Callable[[], ArchiveMaintenanceSnapshot]]): Build fn,
      or None when absent.
    
    Returns:
      ArchiveMaintenanceSnapshot: ArchiveMaintenanceSnapshot produced by this
      call.
    
    Examples:
      >>> StartupArchiveScanCoordinator().wait_or_build_snapshot(None)
    """
    return self.wait_for_snapshot(allow_build=True, build_fn=build_fn)

  def _default_build(self) -> ArchiveMaintenanceSnapshot:
    """
    Internal helper to handle default build.
    
    Returns:
      ArchiveMaintenanceSnapshot: ArchiveMaintenanceSnapshot produced by this
      call.
    
    Examples:
      >>> StartupArchiveScanCoordinator()._default_build()  # doctest: +SKIP
    """
    return build_archive_maintenance_snapshot(
        self.archive_data_dir,
        self.host_name_ext,
        self.tgz_archive_dir,
        build_ready_set=False,
        log_fn=self.log_fn,
    )
