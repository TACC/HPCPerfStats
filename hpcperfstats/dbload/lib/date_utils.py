"""
Shared date parsing and range utilities for dbload and CLI scripts.
"""
from __future__ import annotations

from typing import Any, Iterator

from datetime import datetime, timedelta, timezone

import pandas as pd
from hpcperfstats.dbload.lib.print_utils import log_print


def to_pydatetime_or_none(ts: Any) -> Any:
  """
  Convert Timestamp, unix seconds, or NaT to a Python datetime or None.

  Uses ``warn=False`` because Python ``datetime`` only has microsecond
  resolution; monitor/pandas timestamps often carry nanoseconds and the
  default warning floods listend/sync_timedb logs. Unix-second floats from
  ingest parse stay numeric until this ORM boundary.

  Args:
    ts (Any): ``datetime``, pandas ``Timestamp``, unix seconds, ISO string,
      sentinel, or ``None``.

  Returns:
    Any: Timezone-aware UTC ``datetime``, or ``None`` when missing.

  Examples:
    >>> to_pydatetime_or_none(None) is None
    True
    >>> to_pydatetime_or_none(float("nan")) is None
    True
  """
  if ts is None:
    return None
  try:
    if pd.isna(ts):
      return None
  except (TypeError, ValueError):
    pass
  to_py = getattr(ts, "to_pydatetime", None)
  if callable(to_py):
    dt = to_py(warn=False)
    if dt is None:
      return None
    if getattr(dt, "tzinfo", None) is None:
      return dt.replace(tzinfo=timezone.utc)
    return dt
  if isinstance(ts, datetime):
    return ts
  return datetime.fromtimestamp(float(ts), tz=timezone.utc)


def parse_start_end_dates(
  argv: Any,
  default_start: Any,
  default_end: Any,
  date_fmt: str = "%Y-%m-%d",
) -> Any:
  """
  Parse start and end dates from argv[1] and argv[2].
  
  Returns (start_date, end_date). Uses default_start if argv[1] is missing or
  invalid; uses default_end if argv[2] is missing or invalid.
  
  Args:
    argv (Any): CLI argument list (``sys.argv``-like).
    default_start (Any): Default start passed to this helper.
    default_end (Any): Default end passed to this helper.
    date_fmt (str): String for date fmt.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> parse_start_end_dates(None, None, None, "x")  # doctest: +SKIP
  """
  try:
    start = datetime.strptime(argv[1], date_fmt)
  except (IndexError, ValueError, TypeError):
    start = default_start
  try:
    end = datetime.strptime(argv[2], date_fmt)
  except (IndexError, ValueError, TypeError):
    end = default_end
  return start, end


def log_date_range(kind: Any, start: Any, end: Any) -> None:
  """
  Print the standard date-range log line. kind e.g. 'stats files to ingest',.
  
    'job files to ingest', 'metrics to update'.
  
  Args:
    kind (Any): Mode or kind token selecting a code path.
    start (Any): Time value (``datetime``, ISO string, sentinel, or ``None``).
    end (Any): Time value (``datetime``, ISO string, sentinel, or ``None``).
  
  Returns:
    None
  
  Examples:
    >>> log_date_range(None, None, None)  # doctest: +SKIP
  """
  log_print("###Date Range of {}: {} -> {}####".format(kind, start, end))


def daterange(
  start_date: Any,
  end_date: Any,
  inclusive_end: bool = False,
) -> Iterator[Any]:
  """
  Yield each date from start_date through end_date, one day at a time.
  
  Args:
    start_date (Any): Start date passed to this helper.
    end_date (Any): End date passed to this helper.
    inclusive_end (bool): Boolean flag for inclusive end.
  
  Yields:
    Iterator[Any]: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> daterange(None, None, True)  # doctest: +SKIP
  """
  days = int((end_date - start_date).days)
  if inclusive_end:
    days += 1
  for n in range(max(0, days)):
    yield start_date + timedelta(n)
