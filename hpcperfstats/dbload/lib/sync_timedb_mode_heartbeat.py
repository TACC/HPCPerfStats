"""Transient heartbeat so CLI ``all`` can exit near an active ``current`` run.

Not a persistence-contract artifact — missing/stale/corrupt → fail-open (``all`` continues).
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
    "should_all_exit_for_current_proximity",
]


def calendar_day_from_stats_path(path, tgz_archive_dir) -> Optional[date]:
  """Best-effort calendar day for a raw stats path (tar basename day preferred)."""
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
  """Min calendar day among ``paths`` (in-flight ∪ chunk), not full pending."""
  days = []
  for path in paths or ():
    day = calendar_day_from_stats_path(path, daily_archive_dir)
    if day is not None:
      days.append(day)
  if not days:
    return None
  return min(days)


def _heartbeat_sidecar_path(archive_dir: str) -> str:
  return os.path.join(str(archive_dir), HEARTBEAT_BASENAME)


def _coerce_day(value) -> Optional[date]:
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
    active_paths,
    daily_archive_dir: Optional[str] = None,
    now: Optional[float] = None,
    redis_client=None,
) -> dict:
  """Publish oldest active calendar day for CLI ``current``.

  Prefers Redis when ``redis_client`` is provided; otherwise writes the
  archive-dir sidecar atomically.
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
    redis_client=None,
) -> Optional[dict]:
  """Return a fresh heartbeat dict, or ``None`` if missing/corrupt/stale."""
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


def should_all_exit_for_current_proximity(
    *,
    next_pending_day,
    heartbeat,
    proximity_days: int,
) -> bool:
  """True when ``all``'s next oldest pending day is within proximity of heartbeat D."""
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
