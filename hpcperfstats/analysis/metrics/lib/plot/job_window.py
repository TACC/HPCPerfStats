"""
Parse job start/end from jid_table-like objects for plots (summary).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import hpcperfstats.dbload.lib.conf_parser as cfg


def _as_utc(value: Any) -> datetime | None:
  """
  Coerce a window bound to an aware UTC datetime.

  Args:
    value (Any): Datetime, ISO text, epoch microseconds or None.

  Returns:
    datetime | None: UTC datetime.

  Examples:
    >>> _as_utc("2026-01-01T00:00:00+00:00").tzinfo is not None
    True
    >>> _as_utc(None) is None
    True
  """
  if value is None:
    return None
  if isinstance(value, datetime):
    dt = value
  elif isinstance(value, (int, float)) and not isinstance(value, bool):
    dt = datetime.fromtimestamp(float(value) / 1_000_000.0, tz=UTC)
  elif hasattr(value, "to_pydatetime"):
    dt = value.to_pydatetime()
  else:
    try:
      dt = datetime.fromisoformat(str(value))
    except ValueError:
      return None
  if dt.tzinfo is None:
    dt = dt.replace(tzinfo=UTC)
  return dt.astimezone(UTC)


def job_window_timestamps_utc(jt: Any) -> Any:
  """
  Return (start, end) as UTC datetimes or (None, None) if invalid.

  Args:
    jt (Any): Job table with ``start_time`` / ``end_time``.

  Returns:
    Any: ``(datetime, datetime)`` or ``(None, None)``.

  Examples:
    >>> job_window_timestamps_utc(
    ...   type("J", (), {"start_time": None, "end_time": None})()
    ... )
    (None, None)
  """
  start_ts = _as_utc(getattr(jt, "start_time", None))
  end_ts = _as_utc(getattr(jt, "end_time", None))
  if start_ts is None or end_ts is None or end_ts <= start_ts:
    return (None, None)
  return (start_ts, end_ts)


def job_window_bounds_local(jt: Any) -> Any:
  """
  Return (start, end) in the cluster timezone for Bokeh axes, or (None, None).

  Args:
    jt (Any): Job table with ``start_time`` / ``end_time``.

  Returns:
    Any: Aware local datetimes or ``(None, None)``.

  Examples:
    >>> job_window_bounds_local(
    ...   type("J", (), {"start_time": None, "end_time": None})()
    ... )
    (None, None)
  """
  start_ts, end_ts = job_window_timestamps_utc(jt)
  if start_ts is None:
    return (None, None)
  tz = cfg.get_local_timezone()
  if isinstance(tz, str):
    tz = ZoneInfo(tz)
  return (start_ts.astimezone(tz), end_ts.astimezone(tz))


def job_window_label_strings(jt: Any) -> Any:
  """
  Return (start_label, end_label) as str(UTC datetimes) for plot axes, or
  (None, None).

  Args:
    jt (Any): Job table with ``start_time`` / ``end_time``.

  Returns:
    Any: Two strings or ``(None, None)``.

  Examples:
    >>> job_window_label_strings(
    ...   type("J", (), {"start_time": None, "end_time": None})()
    ... )
    (None, None)
  """
  start_ts, end_ts = job_window_timestamps_utc(jt)
  if start_ts is None:
    return (None, None)
  return (str(start_ts), str(end_ts))
