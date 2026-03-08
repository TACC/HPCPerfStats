"""Shared date parsing and range utilities for dbload and CLI scripts."""
from datetime import datetime, timedelta


def parse_start_end_dates(
    argv,
    default_start,
    default_end,
    date_fmt="%Y-%m-%d",
):
  """Parse start and end dates from argv[1] and argv[2].

  Returns (start_date, end_date). Uses default_start if argv[1] is missing or
  invalid; uses default_end if argv[2] is missing or invalid.
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


def log_date_range(kind, start, end):
  """Print the standard date-range log line. kind e.g. 'stats files to ingest', 'job files to ingest', 'metrics to update'."""
  print("###Date Range of {}: {} -> {}####".format(kind, start, end))


def daterange(start_date, end_date, inclusive_end=False):
  """Yield each date from start_date through end_date, one day at a time.

  Args:
    start_date: First date (inclusive).
    end_date: Last date; inclusive if inclusive_end=True, else exclusive.
    inclusive_end: If True, end_date is included; if False, range is [start, end).

  Yields:
    datetime (date part) for each day.
  """
  days = int((end_date - start_date).days)
  if inclusive_end:
    days += 1
  for n in range(max(0, days)):
    yield start_date + timedelta(n)
