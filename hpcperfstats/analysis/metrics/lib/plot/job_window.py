"""
Parse job start/end from jid_table-like objects for plots (summary).
"""
from __future__ import annotations

from typing import Any

import hpcperfstats.dbload.lib.conf_parser as cfg
from pandas import isna as pd_isna
from pandas import to_datetime


def job_window_timestamps_utc(jt: Any) -> Any:
  """
  Return (start_ts, end_ts) as UTC timestamps or (None, None) if invalid.
  
  Args:
    jt (Any): Jt passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> job_window_timestamps_utc(None)  # doctest: +SKIP
  """
  start = getattr(jt, "start_time", None)
  end = getattr(jt, "end_time", None)
  if start is None or end is None:
    return (None, None)
  try:
    start_ts = to_datetime(start, utc=True)
    end_ts = to_datetime(end, utc=True)
  except (TypeError, ValueError):
    return (None, None)
  if pd_isna(start_ts) or pd_isna(end_ts) or end_ts <= start_ts:
    return (None, None)
  return (start_ts, end_ts)


def job_window_bounds_local(jt: Any) -> Any:
  """
  Return (start, end) in local timezone for Bokeh axes, or (None, None).
  
  Args:
    jt (Any): Jt passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> job_window_bounds_local(None)  # doctest: +SKIP
  """
  start_ts, end_ts = job_window_timestamps_utc(jt)
  if start_ts is None:
    return (None, None)
  local_timezone = cfg.get_local_timezone()
  return (start_ts.tz_convert(local_timezone), end_ts.tz_convert(local_timezone))


def job_window_label_strings(jt: Any) -> Any:
  """
  Return (start_label, end_label) as str(UTC timestamps) for plot axis, or.
  
    (None, None).
  
  Args:
    jt (Any): Jt passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> job_window_label_strings(None)  # doctest: +SKIP
  """
  start_ts, end_ts = job_window_timestamps_utc(jt)
  if start_ts is None:
    return (None, None)
  return (str(start_ts), str(end_ts))
