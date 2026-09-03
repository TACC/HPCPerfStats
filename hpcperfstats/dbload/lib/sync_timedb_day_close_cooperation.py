"""
Cooperative defer/yield between ingest hot path and janitor day-close mutations.

Attributes:
  DAY_CLOSE_YIELD_POLL_SECONDS: Attribute.
  JANITOR_DEFER_CAP_TICKS: Attribute.
  JANITOR_DEFER_CAP_WALL_SECONDS: Attribute.
  WRITE_LOCK_BACKOFF_BASE_S: Attribute.
  WRITE_LOCK_BACKOFF_MAX_S: Attribute.
  _yield_events: Attribute.
  _yield_lock: Attribute.
  _yield_reasons: Attribute.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any, Optional, Set

from hpcperfstats.dbload.lib.print_utils import log_print

DAY_CLOSE_YIELD_POLL_SECONDS = 5.0
JANITOR_DEFER_CAP_TICKS = 3
JANITOR_DEFER_CAP_WALL_SECONDS = 15 * 60
# Sticky backoff when seal/dedupe hits live flock (write_lock_contended).
# Prevents day-close ticks from re-popping the same tar and burning
# hundreds of seconds on pre_seal/dup-scan before deferring again.
WRITE_LOCK_BACKOFF_BASE_S = 30.0
WRITE_LOCK_BACKOFF_MAX_S = 300.0


class DayCloseYieldError(Exception):
  """
  Raised when janitor aborts a long mutation cooperatively for ingest.
  
  Attributes:
    phase: Attribute.
    reason: Attribute.
    tar_path: Attribute.
  """

  def __init__(
    self,
    tar_path: str,
    *,
    phase: str = "",
    reason: str = "",
  ) -> None:
    """
    Initialize a new instance.
    
    Args:
      tar_path (str): String for tar path.
      phase (str): String for phase.
      reason (str): String for reason.
    
    Returns:
      None
    
    Examples:
      >>> DayCloseYieldError("x", "x", "x")  # doctest: +SKIP
    """
    self.tar_path = os.path.normpath(tar_path or "")
    self.phase = phase or ""
    self.reason = reason or ""
    super().__init__(
        "day_close yield tar=%s phase=%s reason=%s"
        % (self.tar_path, self.phase, self.reason),
    )


_yield_lock = threading.Lock()
_yield_events: dict[str, threading.Event] = {}
_yield_reasons: dict[str, str] = {}


def _tar_norm(tar_path: str) -> str:
  """
  Internal helper to handle tar norm.
  
  Args:
    tar_path (str): String for tar path.
  
  Returns:
    str: str produced by this call.
  
  Examples:
    >>> _tar_norm("x")  # doctest: +SKIP
  """
  return os.path.normpath(tar_path or "")


def signal_day_close_yield(
  tar_path: str,
  *,
  reason: str,
  log_fn: Any = log_print,
) -> None:
  """
  Non-blocking hint for day-close worker to yield (janitor-first race).
  
  Args:
    tar_path (str): String for tar path.
    reason (str): String for reason.
    log_fn (Any): Callable invoked by this helper.
  
  Returns:
    None
  
  Examples:
    >>> signal_day_close_yield("x", "x", None)  # doctest: +SKIP
  """
  tar_norm = _tar_norm(tar_path)
  if not tar_norm:
    return
  with _yield_lock:
    ev = _yield_events.setdefault(tar_norm, threading.Event())
    _yield_reasons[tar_norm] = reason or "yield_requested"
    ev.set()
  if log_fn:
    log_fn(
        "janitor: day_close yield signal tar=%s reason=%s"
        % (tar_norm, reason or "yield_requested"),
        flush=True,
    )


def clear_day_close_yield(tar_path: str) -> None:
  """
  Clear day close yield.
  
  Args:
    tar_path (str): String for tar path.
  
  Returns:
    None
  
  Examples:
    >>> clear_day_close_yield("x")  # doctest: +SKIP
  """
  tar_norm = _tar_norm(tar_path)
  if not tar_norm:
    return
  with _yield_lock:
    _yield_events.pop(tar_norm, None)
    _yield_reasons.pop(tar_norm, None)


def day_close_yield_event_set(tar_path: str) -> bool:
  """
  Day close yield event set.
  
  Args:
    tar_path (str): String for tar path.
  
  Returns:
    bool: True or False for this check.
  
  Examples:
    >>> day_close_yield_event_set("x")  # doctest: +SKIP
  """
  tar_norm = _tar_norm(tar_path)
  if not tar_norm:
    return False
  with _yield_lock:
    ev = _yield_events.get(tar_norm)
    return bool(ev is not None and ev.is_set())


def _hot_path_contention_reasons(
  tar_path: str,
  *,
  tgz_archive_dir: str = "",
) -> tuple[bool, str]:
  """
  Shared ingest-hot checks for yield and janitor defer (pre-flight subset).
  
  Args:
    tar_path (str): String for tar path.
    tgz_archive_dir (str): String for tgz archive dir.
  
  Returns:
    tuple[bool, str]: tuple[bool, str] produced by this call.
  
  Examples:
    >>> _hot_path_contention_reasons("x", "x")  # doctest: +SKIP
  """
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      calendar_date_from_daily_tar_path,
  )
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_coord import (
      archive_members_populate_shows_progress_for_day,
      ingest_tar_hot_for_day,
  )

  tar_norm = _tar_norm(tar_path)
  if not tar_norm:
    return False, ""
  day = calendar_date_from_daily_tar_path(tar_norm)
  day_token = day.isoformat() if day is not None else ""
  if day_token and ingest_tar_hot_for_day(day_token):
    return True, "ingest_tar_hot"
  populate_active = bool(
      day_token
      and tgz_archive_dir
      and archive_members_populate_shows_progress_for_day(day_token, tgz_archive_dir)
  )
  if populate_active:
    return True, "populate_active"
  # Sticky yield Event alone must not abort after hot/populate clear (F11c).
  if day_close_yield_event_set(tar_norm):
    clear_day_close_yield(tar_norm)
  return False, ""


def day_close_yield_requested(
  tar_path: str,
  *,
  tgz_archive_dir: str = "",
  phase: str = "",
) -> tuple[bool, str]:
  """
  True when ingest hot signals require cooperative yield mid-mutation.
  
  Args:
    tar_path (str): String for tar path.
    tgz_archive_dir (str): String for tgz archive dir.
    phase (str): String for phase.
  
  Returns:
    tuple[bool, str]: tuple[bool, str] produced by this call.
  
  Examples:
    >>> day_close_yield_requested("x", "x", "x")  # doctest: +SKIP
  """
  return _hot_path_contention_reasons(
      tar_path,
      tgz_archive_dir=tgz_archive_dir,
  )


def should_poll_day_close_yield(last_poll_monotonic: float) -> bool:
  """
  Return True if poll day close yield.
  
  Args:
    last_poll_monotonic (float): Floating-point value for last poll monotonic.
  
  Returns:
    bool: True or False for this check.
  
  Examples:
    >>> should_poll_day_close_yield(0)  # doctest: +SKIP
  """
  return (time.monotonic() - last_poll_monotonic) >= DAY_CLOSE_YIELD_POLL_SECONDS


def check_day_close_yield_or_continue(
  tar_path: str,
  *,
  last_poll_monotonic: float,
  tgz_archive_dir: str = "",
  phase: str = "",
) -> tuple[float, bool]:
  """
  Return updated poll time; True if caller should raise DayCloseYieldError.
  
  Args:
    tar_path (str): String for tar path.
    last_poll_monotonic (float): Floating-point value for last poll monotonic.
    tgz_archive_dir (str): String for tgz archive dir.
    phase (str): String for phase.
  
  Returns:
    tuple[float, bool]: tuple[float, bool] produced by this call.
  
  Raises:
    DayCloseYieldError: Raised when ``check_day_close_yield_or_continue`` hits
    a ``DayCloseYieldError`` failure path.
  
  Examples:
    >>> check_day_close_yield_or_continue("x", 0, "x", "x")  # doctest: +SKIP
  """
  if not should_poll_day_close_yield(last_poll_monotonic):
    return last_poll_monotonic, False
  requested, reason = day_close_yield_requested(
      tar_path,
      tgz_archive_dir=tgz_archive_dir,
      phase=phase,
  )
  if requested:
    raise DayCloseYieldError(tar_path, phase=phase, reason=reason)
  return time.monotonic(), False


def daily_tar_janitor_mutation_should_defer(
  tar_path: str,
  *,
  tgz_archive_dir: str,
  disqualified_daily_tars: Set[str],
  delete_disqualified_daily_tars: Optional[Set[str]] = None,
  phase: str = "",
  defer_cap_exceeded: bool = False,
  chunk_in_progress: bool = False,
  chunk_day_tokens: Optional[Set[str]] = None,
) -> tuple[bool, str]:
  """
  Pre-flight: janitor cold path should skip write and re-enqueue day-close.
  
  ``defer_cap_exceeded`` stops aging forever but must **not** skip write-lock /
  hot / populate / restore checks (F5). When those are clear, cap may proceed.
  
  Args:
    tar_path (str): String for tar path.
    tgz_archive_dir (str): String for tgz archive dir.
    disqualified_daily_tars (Set[str]): Sequence for disqualified daily tars.
    delete_disqualified_daily_tars (Optional[Set[str]]): Delete disqualified
    daily tars, or None when absent.
    phase (str): String for phase.
    defer_cap_exceeded (bool): Boolean flag for defer cap exceeded.
    chunk_in_progress (bool): Boolean flag for chunk in progress.
    chunk_day_tokens (Optional[Set[str]]): Chunk day tokens, or None when
    absent.
  
  Returns:
    tuple[bool, str]: tuple[bool, str] produced by this call.
  
  Examples:
    >>> daily_tar_janitor_mutation_should_defer(0)  # doctest: +SKIP
  """
  del defer_cap_exceeded  # aging handled by caller; still run safety checks
  from hpcperfstats.dbload.lib.file_locking import try_file_write_lock
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      calendar_date_from_daily_tar_path,
  )
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_coord import (
      daily_tar_restore_in_progress_for_day,
  )

  tar_norm = _tar_norm(tar_path)
  if not tar_norm:
    return False, ""
  day = calendar_date_from_daily_tar_path(tar_norm)
  day_token = day.isoformat() if day is not None else ""
  if day_token and daily_tar_restore_in_progress_for_day(day_token):
    return True, "daily_tar_restore"
  hot, reason = _hot_path_contention_reasons(
      tar_path,
      tgz_archive_dir=tgz_archive_dir,
  )
  if hot:
    return True, reason
  if chunk_in_progress and day_token and chunk_day_tokens and day_token in chunk_day_tokens:
    return True, "chunk_in_progress_day"
  if tar_norm in (disqualified_daily_tars or set()):
    return True, "inflight_append"
  delete_disq = delete_disqualified_daily_tars if delete_disqualified_daily_tars is not None else disqualified_daily_tars
  if delete_disq and tar_norm in delete_disq:
    return True, "delete_disqualified"
  try:
    with try_file_write_lock(tar_norm):
      pass
  except TimeoutError:
    return True, "write_lock_contended"
  return False, ""


def log_janitor_day_close_defer(
  tar_path: str,
  *,
  phase: str,
  reason: str,
  log_fn: Any = log_print,
) -> None:
  """
  Log the janitor day close defer.
  
  Args:
    tar_path (str): String for tar path.
    phase (str): String for phase.
    reason (str): String for reason.
    log_fn (Any): Callable invoked by this helper.
  
  Returns:
    None
  
  Examples:
    >>> log_janitor_day_close_defer("x", "x", "x", None)  # doctest: +SKIP
  """
  if log_fn:
    log_fn(
        "janitor: day_close defer tar=%s phase=%s reason=%s"
        % (_tar_norm(tar_path), phase or "", reason or ""),
        flush=True,
    )


def log_janitor_day_close_yield(
  tar_path: str,
  *,
  phase: str,
  reason: str,
  log_fn: Any = log_print,
) -> None:
  """
  Log the janitor day close yield.
  
  Args:
    tar_path (str): String for tar path.
    phase (str): String for phase.
    reason (str): String for reason.
    log_fn (Any): Callable invoked by this helper.
  
  Returns:
    None
  
  Examples:
    >>> log_janitor_day_close_yield("x", "x", "x", None)  # doctest: +SKIP
  """
  if log_fn:
    log_fn(
        "janitor: day_close yield tar=%s phase=%s reason=%s"
        % (_tar_norm(tar_path), phase or "", reason or ""),
        flush=True,
    )


class JanitorDeferTracker:
  """
  Per-tar defer streak for starvation cap (in-memory on ArchiveJanitor).
  
  Attributes:
    _by_tar: Attribute.
    _lock: Attribute.
  """

  def __init__(self) -> None:
    """
    Initialize a new instance.
    
    Returns:
      None
    
    Examples:
      >>> JanitorDeferTracker()  # doctest: +SKIP
    """
    self._lock = threading.Lock()
    self._by_tar: dict[str, dict] = {}

  def record_defer(self, tar_path: str, *, reason: str = "") -> None:
    """
    Record defer.
    
    Args:
      tar_path (str): String for tar path.
      reason (str): String for reason.
    
    Returns:
      None
    
    Examples:
      >>> JanitorDeferTracker().record_defer("x", "x")  # doctest: +SKIP
    """
    tar_norm = _tar_norm(tar_path)
    if not tar_norm:
      return
    now = time.time()
    with self._lock:
      entry = self._by_tar.setdefault(
          tar_norm,
          {"count": 0, "first_ts": now},
      )
      entry["count"] = int(entry.get("count", 0)) + 1
      entry["last_ts"] = now
      entry["last_reason"] = reason or ""
      if reason == "write_lock_contended":
        streak = int(entry.get("write_lock_streak", 0)) + 1
        entry["write_lock_streak"] = streak
        # 30s, 60s, 120s, 240s, then cap at WRITE_LOCK_BACKOFF_MAX_S
        exp = min(max(0, streak - 1), 4)
        delay = min(
            WRITE_LOCK_BACKOFF_MAX_S,
            WRITE_LOCK_BACKOFF_BASE_S * (2 ** exp),
        )
        entry["write_lock_until"] = now + float(delay)

  def record_yield_backoff(self, tar_path: str) -> None:
    """
    Record a cooperative day_close yield for sticky reclaim backoff.

    Same 30s→300s schedule as write_lock contention. Fill skips reclaim
    while active so claim/vacate cannot busy-wait for minutes.

    Args:
      tar_path (str): Daily ``.tar`` path or day_close LIST identity.

    Returns:
      None

    Examples:
      >>> JanitorDeferTracker().record_yield_backoff("/d/2020-01-01.tar")
    """
    tar_norm = _tar_norm(tar_path)
    if not tar_norm:
      return
    now = time.time()
    with self._lock:
      entry = self._by_tar.setdefault(
          tar_norm,
          {"count": 0, "first_ts": now},
      )
      streak = int(entry.get("yield_streak", 0)) + 1
      entry["yield_streak"] = streak
      exp = min(max(0, streak - 1), 4)
      delay = min(
          WRITE_LOCK_BACKOFF_MAX_S,
          WRITE_LOCK_BACKOFF_BASE_S * (2 ** exp),
      )
      entry["yield_until"] = now + float(delay)
      entry["last_ts"] = now
      entry["last_reason"] = "yielded"

  def yield_backoff_active(
    self,
    tar_path: str,
    *,
    now: Optional[float] = None,
  ) -> bool:
    """
    True while sticky day_close yield reclaim backoff has not expired.

    Args:
      tar_path (str): Daily ``.tar`` path or day_close LIST identity.
      now (Optional[float]): Wall clock override for tests.

    Returns:
      bool: Whether reclaim should skip submit for this identity.

    Examples:
      >>> JanitorDeferTracker().yield_backoff_active("x", now=0.0)
      False
    """
    tar_norm = _tar_norm(tar_path)
    if not tar_norm:
      return False
    clock = time.time() if now is None else float(now)
    with self._lock:
      entry = self._by_tar.get(tar_norm)
      if not entry:
        return False
      until = float(entry.get("yield_until", 0.0) or 0.0)
      return until > clock

  def write_lock_backoff_active(
    self,
    tar_path: str,
    *,
    now: Optional[float] = None,
  ) -> bool:
    """
    True while sticky write_lock_contended backoff has not expired.
    
    Args:
      tar_path (str): String for tar path.
      now (Optional[float]): Now, or None when absent.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> JanitorDeferTracker().write_lock_backoff_active("x", None)
    """
    tar_norm = _tar_norm(tar_path)
    if not tar_norm:
      return False
    clock = time.time() if now is None else float(now)
    with self._lock:
      entry = self._by_tar.get(tar_norm)
      if not entry:
        return False
      until = float(entry.get("write_lock_until", 0.0) or 0.0)
      return until > clock

  def write_lock_backoff_skip_tars(
    self,
    tar_paths: Any,
    *,
    now: Optional[float] = None,
  ) -> Set[str]:
    """
    Subset of ``tar_paths`` still inside write_lock sticky backoff.
    
    Args:
      tar_paths (Any): Iterable of filesystem paths as strings.
      now (Optional[float]): Now, or None when absent.
    
    Returns:
      Set[str]: Set[str] produced by this call.
    
    Examples:
      >>> JanitorDeferTracker().write_lock_backoff_skip_tars(None, None)
    """
    clock = time.time() if now is None else float(now)
    skipped: Set[str] = set()
    for tar_path in tar_paths or ():
      if self.write_lock_backoff_active(tar_path, now=clock):
        skipped.add(_tar_norm(tar_path))
    return skipped

  def defer_cap_exceeded(self, tar_path: str) -> bool:
    """
    Defer cap exceeded.
    
    Args:
      tar_path (str): String for tar path.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> JanitorDeferTracker().defer_cap_exceeded("x")  # doctest: +SKIP
    """
    tar_norm = _tar_norm(tar_path)
    if not tar_norm:
      return False
    with self._lock:
      entry = self._by_tar.get(tar_norm)
      if not entry:
        return False
      count = int(entry.get("count", 0))
      first_ts = float(entry.get("first_ts", 0.0))
      if count >= JANITOR_DEFER_CAP_TICKS:
        return True
      if first_ts and (time.time() - first_ts) >= JANITOR_DEFER_CAP_WALL_SECONDS:
        return True
      return False

  def clear_tar(self, tar_path: str) -> None:
    """
    Clear tar.
    
    Args:
      tar_path (str): String for tar path.
    
    Returns:
      None
    
    Examples:
      >>> JanitorDeferTracker().clear_tar("x")  # doctest: +SKIP
    """
    tar_norm = _tar_norm(tar_path)
    with self._lock:
      self._by_tar.pop(tar_norm, None)
