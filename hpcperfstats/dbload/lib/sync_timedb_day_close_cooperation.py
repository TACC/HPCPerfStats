"""Cooperative defer/yield between ingest hot path and janitor day-close mutations."""
from __future__ import annotations

import os
import threading
import time
from typing import Optional, Set

from hpcperfstats.dbload.lib.print_utils import log_print

DAY_CLOSE_YIELD_POLL_SECONDS = 5.0
JANITOR_DEFER_CAP_TICKS = 3
JANITOR_DEFER_CAP_WALL_SECONDS = 15 * 60


class DayCloseYieldError(Exception):
  """Raised when janitor aborts a long mutation cooperatively for ingest."""

  def __init__(self, tar_path: str, *, phase: str = "", reason: str = ""):
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
  return os.path.normpath(tar_path or "")


def signal_day_close_yield(tar_path: str, *, reason: str, log_fn=log_print) -> None:
  """Non-blocking hint for day-close worker to yield (janitor-first race)."""
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
  tar_norm = _tar_norm(tar_path)
  if not tar_norm:
    return
  with _yield_lock:
    _yield_events.pop(tar_norm, None)
    _yield_reasons.pop(tar_norm, None)


def day_close_yield_event_set(tar_path: str) -> bool:
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
  """Shared ingest-hot checks for yield and janitor defer (pre-flight subset)."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      calendar_date_from_daily_tar_path,
  )
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
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
  if day_close_yield_event_set(tar_norm):
    with _yield_lock:
      return True, _yield_reasons.get(tar_norm, "yield_requested")
  if (
      day_token
      and tgz_archive_dir
      and archive_members_populate_shows_progress_for_day(day_token, tgz_archive_dir)
  ):
    return True, "populate_active"
  return False, ""


def day_close_yield_requested(
    tar_path: str,
    *,
    tgz_archive_dir: str = "",
    phase: str = "",
) -> tuple[bool, str]:
  """True when ingest hot signals require cooperative yield mid-mutation."""
  return _hot_path_contention_reasons(
      tar_path,
      tgz_archive_dir=tgz_archive_dir,
  )


def should_poll_day_close_yield(last_poll_monotonic: float) -> bool:
  return (time.monotonic() - last_poll_monotonic) >= DAY_CLOSE_YIELD_POLL_SECONDS


def check_day_close_yield_or_continue(
    tar_path: str,
    *,
    last_poll_monotonic: float,
    tgz_archive_dir: str = "",
    phase: str = "",
) -> tuple[float, bool]:
  """Return updated poll time; True if caller should raise DayCloseYieldError."""
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
  """Pre-flight: janitor cold path should skip write and re-enqueue day-close."""
  if defer_cap_exceeded:
    return False, ""
  from hpcperfstats.dbload.lib.file_locking import try_file_write_lock
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      calendar_date_from_daily_tar_path,
  )
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
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
    log_fn=log_print,
) -> None:
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
    log_fn=log_print,
) -> None:
  if log_fn:
    log_fn(
        "janitor: day_close yield tar=%s phase=%s reason=%s"
        % (_tar_norm(tar_path), phase or "", reason or ""),
        flush=True,
    )


class JanitorDeferTracker:
  """Per-tar defer streak for starvation cap (in-memory on ArchiveJanitor)."""

  def __init__(self):
    self._lock = threading.Lock()
    self._by_tar: dict[str, dict] = {}

  def record_defer(self, tar_path: str) -> None:
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

  def defer_cap_exceeded(self, tar_path: str) -> bool:
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
    tar_norm = _tar_norm(tar_path)
    with self._lock:
      self._by_tar.pop(tar_norm, None)
