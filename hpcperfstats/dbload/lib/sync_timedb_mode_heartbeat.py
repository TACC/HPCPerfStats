"""
Transient heartbeat so CLI ``backlog`` can exit near an active ``current`` run.

Not a persistence-contract artifact — missing/stale/corrupt → fail-open
(``backlog`` continues).

Attributes:
  DEFAULT_MAX_AGE_S: Attribute.
  HEARTBEAT_BASENAME: Attribute.
  REDIS_HEARTBEAT_KEY: Attribute.
  REDIS_HEARTBEAT_TTL_S: Attribute.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import date, datetime
from typing import Any, Iterable, Optional

from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
    calendar_date_from_daily_tar_path,
    daily_tar_path_for_stats_path,
)
from hpcperfstats.dbload.lib.sync_timedb_persistence import (
    PERSISTENCE_ARTIFACT_REGISTRY,
)

HEARTBEAT_BASENAME = ".sync_timedb_current_heartbeat.json"
REDIS_HEARTBEAT_KEY = "hpcperfstats:sync_timedb:current_heartbeat"
REDIS_HEARTBEAT_TTL_S = 600
DEFAULT_MAX_AGE_S = 300

__all__ = [
    "HEARTBEAT_BASENAME",
    "PERSISTENCE_ARTIFACT_REGISTRY",
    "REDIS_HEARTBEAT_KEY",
    "calendar_day_from_stats_path",
    "oldest_active_day_from_paths",
    "publish_current_heartbeat",
    "read_current_heartbeat",
    "should_backlog_exit_for_current_proximity",
]


def calendar_day_from_stats_path(
  path: str,
  tgz_archive_dir: str,
) -> Optional[date]:
  """
  Best-effort calendar day for a raw stats path (tar basename day preferred).
  
  Args:
    path (str): String for path.
    tgz_archive_dir (str): String for tgz archive dir.
  
  Returns:
    Optional[date]: Optional[date] — the result, or None when unavailable.
  
  Examples:
    >>> calendar_day_from_stats_path("x", "x")  # doctest: +SKIP
  """
  if not path:
    return None
  tar = daily_tar_path_for_stats_path(path, tgz_archive_dir)
  if tar:
    day = calendar_date_from_daily_tar_path(tar)
    if day is not None:
      return day
  try:
    epoch = int(os.path.basename(str(path)))
  except (TypeError, ValueError):
    return None
  try:
    return datetime.fromtimestamp(epoch).date()
  except (OSError, OverflowError, ValueError):
    return None


def oldest_active_day_from_paths(
  paths: Iterable[str],
  *,
  daily_archive_dir: Optional[str] = None,
) -> Optional[date]:
  """
  Min calendar day among ``paths`` (in-flight ∪ chunk), not full pending.
  
  Args:
    paths (Iterable[str]): Paths.
    daily_archive_dir (Optional[str]): Daily archive dir, or None when absent.
  
  Returns:
    Optional[date]: Optional[date] — the result, or None when unavailable.
  
  Examples:
    >>> oldest_active_day_from_paths(None, None)  # doctest: +SKIP
  """
  days = []
  for path in paths or ():
    day = calendar_day_from_stats_path(path, daily_archive_dir)
    if day is not None:
      days.append(day)
  if not days:
    return None
  return min(days)


def _heartbeat_sidecar_path(archive_dir: str) -> str:
  """
  Internal helper to handle heartbeat sidecar path.
  
  Args:
    archive_dir (str): String for archive dir.
  
  Returns:
    str: str produced by this call.
  
  Examples:
    >>> _heartbeat_sidecar_path("x")  # doctest: +SKIP
  """
  return os.path.join(str(archive_dir), HEARTBEAT_BASENAME)


def _coerce_day(value: Any) -> Optional[date]:
  """
  Internal helper to coerce the day.
  
  Args:
    value (Any): Value to inspect (typically a numeric scalar).
  
  Returns:
    Optional[date]: Optional[date] — the result, or None when unavailable.
  
  Examples:
    >>> _coerce_day(None)  # doctest: +SKIP
  """
  if value is None:
    return None
  if isinstance(value, date) and not isinstance(value, datetime):
    return value
  text = str(value).strip()
  if not text:
    return None
  try:
    return date.fromisoformat(text[:10])
  except ValueError:
    return None


def _payload_from_raw(raw: Any) -> Optional[dict]:
  """
  Internal helper to handle payload from raw.
  
  Args:
    raw (Any): Raw passed to this helper.
  
  Returns:
    Optional[dict]: Optional[dict] — the result, or None when unavailable.
  
  Examples:
    >>> _payload_from_raw(None)  # doctest: +SKIP
  """
  if raw is None:
    return None
  if isinstance(raw, (bytes, bytearray)):
    raw = raw.decode("utf-8", errors="replace")
  if isinstance(raw, str):
    try:
      raw = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
      return None
  if not isinstance(raw, dict):
    return None
  day = _coerce_day(raw.get("oldest_active_day"))
  try:
    written_at = float(raw.get("written_at"))
  except (TypeError, ValueError):
    return None
  if day is None:
    return None
  payload = {
      "oldest_active_day": day.isoformat(),
      "written_at": written_at,
      "writer_pid": int(raw.get("writer_pid") or 0),
      "mode": str(raw.get("mode") or "current"),
  }
  return payload


def _write_sidecar_atomic(archive_dir: str, payload: dict) -> None:
  """
  Internal helper to write the sidecar atomic.
  
  Args:
    archive_dir (str): String for archive dir.
    payload (dict): Mapping for payload.
  
  Returns:
    None
  
  Raises:
    Exception: Raised when ``_write_sidecar_atomic`` hits a ``Exception``
    failure path.
  
  Examples:
    >>> _write_sidecar_atomic("x", {})  # doctest: +SKIP
  """
  os.makedirs(archive_dir, exist_ok=True)
  dest = _heartbeat_sidecar_path(archive_dir)
  parent = archive_dir
  fd, tmp_path = tempfile.mkstemp(
      prefix=".sync_timedb_current_heartbeat.",
      suffix=".tmp",
      dir=parent,
  )
  try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
      json.dump(payload, handle, separators=(",", ":"))
      handle.write("\n")
      handle.flush()
      os.fsync(handle.fileno())
    os.replace(tmp_path, dest)
  except Exception:
    try:
      os.unlink(tmp_path)
    except OSError:
      pass
    raise


def publish_current_heartbeat(
  *,
  archive_dir: str,
  active_paths: Any,
  daily_archive_dir: Optional[str] = None,
  now: Optional[float] = None,
  redis_client: Any | None = None,
) -> dict:
  """
  Publish oldest active calendar day for CLI ``current``.
  
  Prefers Redis when ``redis_client`` is provided; otherwise writes the
  archive-dir sidecar atomically.
  
  Args:
    archive_dir (str): String for archive dir.
    active_paths (Any): Iterable of filesystem paths as strings.
    daily_archive_dir (Optional[str]): Daily archive dir, or None when absent.
    now (Optional[float]): Now, or None when absent.
    redis_client (Any | None): One of ``Any``, ``None``.
  
  Returns:
    dict: dict produced by this call.
  
  Examples:
    >>> publish_current_heartbeat("x", None, None, None, None)  # doctest: +SKIP
  """
  day = oldest_active_day_from_paths(
      active_paths,
      daily_archive_dir=daily_archive_dir,
  )
  written_at = float(time.time() if now is None else now)
  payload = {
      "oldest_active_day": day.isoformat() if day is not None else "",
      "written_at": written_at,
      "writer_pid": int(os.getpid()),
      "mode": "current",
  }
  if day is None:
    payload["oldest_active_day"] = ""
  if redis_client is not None:
    redis_client.set(
        REDIS_HEARTBEAT_KEY,
        json.dumps(payload, separators=(",", ":")),
        ex=REDIS_HEARTBEAT_TTL_S,
    )
    return payload
  _write_sidecar_atomic(str(archive_dir), payload)
  return payload


def read_current_heartbeat(
  *,
  archive_dir: str,
  max_age_s: float = DEFAULT_MAX_AGE_S,
  now: Optional[float] = None,
  redis_client: Any | None = None,
) -> Optional[dict]:
  """
  Return a fresh heartbeat dict, or ``None`` if missing/corrupt/stale.
  
  Args:
    archive_dir (str): String for archive dir.
    max_age_s (float): Floating-point value for max age s.
    now (Optional[float]): Now, or None when absent.
    redis_client (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Optional[dict]: Optional[dict] — the result, or None when unavailable.
  
  Examples:
    >>> read_current_heartbeat("x", 0, None, None)  # doctest: +SKIP
  """
  raw = None
  if redis_client is not None:
    try:
      raw = redis_client.get(REDIS_HEARTBEAT_KEY)
    except Exception:
      raw = None
  if raw is None:
    sidecar = _heartbeat_sidecar_path(str(archive_dir))
    try:
      with open(sidecar, "r", encoding="utf-8") as handle:
        raw = handle.read()
    except (OSError, UnicodeError):
      raw = None
  payload = _payload_from_raw(raw)
  if payload is None:
    return None
  if not payload.get("oldest_active_day"):
    return None
  mono_now = float(time.time() if now is None else now)
  age = mono_now - float(payload["written_at"])
  if age < 0 or age > float(max_age_s):
    return None
  return payload


def should_backlog_exit_for_current_proximity(
  *,
  next_pending_day: Any,
  heartbeat: Any,
  proximity_days: int,
) -> bool:
  """
  True when ``backlog``'s next oldest pending day is within proximity of.
  
    heartbeat D.
  
  Args:
    next_pending_day (Any): Next pending day passed to this helper.
    heartbeat (Any): Heartbeat passed to this helper.
    proximity_days (int): Integer value for proximity days.
  
  Returns:
    bool: True or False for this check.
  
  Examples:
    >>> should_backlog_exit_for_current_proximity(None, None, 0)
  """
  if not heartbeat:
    return False
  pending = _coerce_day(next_pending_day)
  active = _coerce_day(
      heartbeat.get("oldest_active_day") if isinstance(heartbeat, dict) else None,
  )
  if pending is None or active is None:
    return False
  try:
    limit = max(0, int(proximity_days))
  except (TypeError, ValueError):
    limit = 0
  return abs((pending - active).days) <= limit
