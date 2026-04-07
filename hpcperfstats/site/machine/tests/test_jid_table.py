"""Unit tests for analysis.gen.jid_table (_ensure_tz) and utils.queryset_to_dataframe.

"""
from datetime import datetime

import pandas as pd
import pytest

pytestmark = pytest.mark.django_db(databases=[])

from hpcperfstats.analysis.gen.jid_table import _ensure_tz
from hpcperfstats.analysis.gen.jid_table import TypeDetailDataProvider
from hpcperfstats.analysis.gen.jid_table import jid_table
from hpcperfstats.analysis.gen.utils import iter_queryset_values_dicts
from hpcperfstats.analysis.gen.utils import queryset_to_dataframe


def test_queryset_to_dataframe_none():
  """queryset_to_dataframe returns empty DataFrame for None."""
  out = queryset_to_dataframe(None)
  assert isinstance(out, pd.DataFrame)
  assert len(out) == 0


def test_queryset_to_dataframe_values_list():
  """queryset_to_dataframe converts iterable of tuples to DataFrame."""
  class QsValuesList:
    def __init__(self, rows):
      self._rows = rows

    def values(self):
      return None

    def __iter__(self):
      return iter(self._rows)

  qs = QsValuesList([(1, "a"), (2, "b")])
  out = queryset_to_dataframe(qs)
  assert isinstance(out, pd.DataFrame)
  assert len(out) == 2
  assert list(out.iloc[0]) == [1, "a"]


def test_queryset_to_dataframe_values_dict():
  """queryset_to_dataframe converts iterable of dicts to DataFrame."""
  class QsValues:
    def __iter__(self):
      return iter([{"host": "h1", "time": 1}, {"host": "h2", "time": 2}])

  qs = QsValues()
  out = queryset_to_dataframe(qs)
  assert isinstance(out, pd.DataFrame)
  assert len(out) == 2
  assert list(out.columns) == ["host", "time"]
  assert out["host"].tolist() == ["h1", "h2"]


def test_queryset_to_dataframe_values_with_columns():
  """queryset_to_dataframe with columns argument uses values(*columns)."""
  class QsValuesCols:
    def values(self, *cols):
      return [{"host": "n1", "time": 1}] if cols else []

  qs = QsValuesCols()
  out = queryset_to_dataframe(qs, columns=["host", "time"])
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


def test_type_detail_provider_aggregate_df_groups_in_pandas():
  """TypeDetailDataProvider.get_aggregate_df aggregates host/time safely."""

  class FakeQuerySet:
    def __init__(self, rows):
      self._rows = rows

    def values(self, *cols):
      return [{col: row[col] for col in cols} for row in self._rows]

  provider = TypeDetailDataProvider(
      jid="j1",
      type_name="pmc",
      start_time=None,
      end_time=None,
      host_list=["n1"],
  )

  rows = [
      {"host": "n1", "time": 1, "arc": 2.0},
      {"host": "n1", "time": 1, "arc": 3.0},
      {"host": "n2", "time": 2, "arc": 4.0},
  ]
  provider._qs = lambda **extra: FakeQuerySet(rows)
  out = provider.get_aggregate_df("FLOPS", metric="arc")
  assert isinstance(out, pd.DataFrame)
  assert out.columns.tolist() == ["host", "time", "sum_val"]
  assert out["sum_val"].tolist() == [5.0, 4.0]


def test_type_detail_provider_invalid_metric_defaults_to_arc():
  """Invalid metric falls back to arc in TypeDetailDataProvider aggregate."""

  class FakeQuerySet:
    def __init__(self, rows):
      self._rows = rows

    def values(self, *cols):
      return [{col: row[col] for col in cols} for row in self._rows]

  provider = TypeDetailDataProvider(
      jid="j2",
      type_name="pmc",
      start_time=None,
      end_time=None,
      host_list=["n1"],
  )
  provider._qs = lambda **extra: FakeQuerySet(
      [{"host": "n1", "time": 1, "arc": 7.0, "value": 99.0}]
  )

  out = provider.get_aggregate_df("FLOPS", metric="not_a_metric")
  assert out["sum_val"].tolist() == [7.0]


def test_jid_table_host_data_time_filter_kwargs_full_window():
  """Unsampled jobs use time__gte/time__lte in ORM kwargs."""
  inst = jid_table.__new__(jid_table)
  inst._base_filter = {
      "time__gte": 1,
      "time__lte": 2,
      "host__in": ["a.example.com"],
  }
  assert inst._host_data_time_filter_kwargs() == {"time__gte": 1, "time__lte": 2}


def test_jid_table_host_data_time_filter_kwargs_sampled():
  """Large-job sampling replaces the window with a finite time__in list."""
  inst = jid_table.__new__(jid_table)
  times = [10, 20, 30]
  inst._base_filter = {"time__in": times, "host__in": ["a.example.com"]}
  assert inst._host_data_time_filter_kwargs() == {"time__in": times}


def test_iter_queryset_values_dicts_yields_rows():
  """iter_queryset_values_dicts streams without list(qs)."""

  class FakeQs:
    def __init__(self):
      self._rows = [{"a": 1, "b": 2}, {"a": 3, "b": 4}]

    def values(self, *fields):
      self._fields = fields
      return self

    def iterator(self, chunk_size=2000):
      for r in self._rows:
        yield r

  qs = FakeQs()
  got = list(iter_queryset_values_dicts(qs, "a", "b", chunk_size=1))
  assert got == [{"a": 1, "b": 2}, {"a": 3, "b": 4}]
