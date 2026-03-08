"""Unit tests for analysis.gen.jid_table (_ensure_tz, _queryset_to_dataframe).

"""
from datetime import datetime

import pandas as pd

from hpcperfstats.analysis.gen.jid_table import _ensure_tz, _queryset_to_dataframe


def test_queryset_to_dataframe_none():
  """_queryset_to_dataframe returns empty DataFrame for None."""
  out = _queryset_to_dataframe(None)
  assert isinstance(out, pd.DataFrame)
  assert len(out) == 0


def test_queryset_to_dataframe_values_list():
  """_queryset_to_dataframe converts values_list() queryset to DataFrame."""
  class QsValuesList:
    def __init__(self, rows):
      self._rows = rows

    def values(self):
      return None

    def __iter__(self):
      return iter(self._rows)

  qs = QsValuesList([(1, "a"), (2, "b")])
  out = _queryset_to_dataframe(qs)
  assert isinstance(out, pd.DataFrame)
  assert len(out) == 2
  assert list(out.iloc[0]) == [1, "a"]


def test_queryset_to_dataframe_values_dict():
  """_queryset_to_dataframe converts values() queryset to DataFrame with columns."""
  class QsValues:
    def values(self, *cols):
      return [{"host": "h1", "time": 1}, {"host": "h2", "time": 2}]

  qs = QsValues()
  out = _queryset_to_dataframe(qs)
  assert isinstance(out, pd.DataFrame)
  assert len(out) == 2
  assert list(out.columns) == ["host", "time"]
  assert out["host"].tolist() == ["h1", "h2"]


def test_queryset_to_dataframe_values_with_columns():
  """_queryset_to_dataframe with columns argument uses values(*columns)."""
  class QsValuesCols:
    def values(self, *cols):
      return [{"host": "n1", "time": 1}] if cols else []

  qs = QsValuesCols()
  out = _queryset_to_dataframe(qs, columns=["host", "time"])
  assert isinstance(out, pd.DataFrame)
  assert list(out.columns) == ["host", "time"]


def test_ensure_tz_none():
  """_ensure_tz returns None for None input."""
  assert _ensure_tz(None) is None


def test_ensure_tz_aware_returns_astimezone():
  """_ensure_tz converts timezone-aware datetime to local_timezone."""
  from datetime import timezone

  utc_aware = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
  result = _ensure_tz(utc_aware)
  assert result is not None
  assert result.tzinfo is not None
  assert result.year == 2024 and result.month == 6 and result.day == 15
