"""Unit tests for dbload.date_utils."""
from datetime import datetime, timedelta

import pytest

from hpcperfstats.dbload.date_utils import daterange, parse_start_end_dates


def test_parse_start_end_dates_from_argv():
  """Parses start and end from argv with valid dates."""
  argv = ["prog", "2024-01-15", "2024-01-20"]
  default_start = datetime(2020, 1, 1)
  default_end = datetime(2020, 12, 31)
  start, end = parse_start_end_dates(argv, default_start, default_end)
  assert start == datetime(2024, 1, 15)
  assert end == datetime(2024, 1, 20)


def test_parse_start_end_dates_missing_argv_uses_defaults():
  """Uses default_start and default_end when argv is too short."""
  argv = ["prog"]
  default_start = datetime(2023, 6, 1)
  default_end = datetime(2023, 6, 30)
  start, end = parse_start_end_dates(argv, default_start, default_end)
  assert start == default_start
  assert end == default_end


def test_parse_start_end_dates_invalid_start_uses_default():
  """Invalid start date falls back to default_start."""
  argv = ["prog", "not-a-date", "2024-02-01"]
  default_start = datetime(2024, 1, 1)
  default_end = datetime(2024, 2, 1)
  start, end = parse_start_end_dates(argv, default_start, default_end)
  assert start == default_start
  assert end == datetime(2024, 2, 1)


def test_parse_start_end_dates_invalid_end_uses_default():
  """Invalid end date falls back to default_end."""
  argv = ["prog", "2024-01-01", "bad"]
  default_start = datetime(2024, 1, 1)
  default_end = datetime(2024, 1, 31)
  start, end = parse_start_end_dates(argv, default_start, default_end)
  assert start == datetime(2024, 1, 1)
  assert end == default_end


def test_parse_start_end_dates_none_argv():
  """Handles None or non-list argv safely (e.g. IndexError)."""
  default_start = datetime(2024, 1, 1)
  default_end = datetime(2024, 1, 31)
  start, end = parse_start_end_dates(None, default_start, default_end)
  assert start == default_start
  assert end == default_end


def test_daterange_exclusive_end():
  """daterange with inclusive_end=False yields [start, end) (exclusive end)."""
  start = datetime(2024, 1, 1)
  end = datetime(2024, 1, 4)
  days = list(daterange(start, end, inclusive_end=False))
  assert len(days) == 3
  assert days[0] == start
  assert days[-1] == start + timedelta(2)


def test_daterange_inclusive_end():
  """daterange with inclusive_end=True yields [start, end] (inclusive end)."""
  start = datetime(2024, 1, 1)
  end = datetime(2024, 1, 4)
  days = list(daterange(start, end, inclusive_end=True))
  assert len(days) == 4
  assert days[0] == start
  assert days[-1] == end


def test_daterange_same_day_exclusive_empty():
  """Same start and end with exclusive end yields no dates."""
  d = datetime(2024, 6, 15)
  days = list(daterange(d, d, inclusive_end=False))
  assert len(days) == 0


def test_daterange_same_day_inclusive_single():
  """Same start and end with inclusive end yields one date."""
  d = datetime(2024, 6, 15)
  days = list(daterange(d, d, inclusive_end=True))
  assert len(days) == 1
  assert days[0] == d


def test_daterange_single_day_exclusive():
  """One-day span with exclusive end yields one date when end is start+1."""
  start = datetime(2024, 3, 1)
  end = start + timedelta(days=1)
  days = list(daterange(start, end, inclusive_end=False))
  assert len(days) == 1
  assert days[0] == start
