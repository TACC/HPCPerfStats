"""Unit tests for analysis.gen.jid_table (_ensure_tz) and utils.queryset_to_dataframe.

"""
from datetime import datetime

import pandas as pd
import pytest

pytestmark = pytest.mark.django_db(databases=[])

from unittest.mock import patch

from hpcperfstats.analysis.gen.jid_table import _coerce_jid_table_schema_dataframe
from hpcperfstats.analysis.gen.jid_table import _count_host_data_rows_for_window
from hpcperfstats.analysis.gen.jid_table import _ensure_tz
from hpcperfstats.analysis.gen.jid_table import _normalize_host_data_schema_label
from hpcperfstats.analysis.gen.jid_table import _normalize_job_accounting_host_list
from hpcperfstats.analysis.gen.jid_table import JID_TABLE_ROW_COUNT_POSTGRES_ARRAY_MAX_HOSTS
from hpcperfstats.analysis.gen.jid_table import _strided_distinct_times_for_large_job
from hpcperfstats.analysis.gen.jid_table import _unpack_cached_job_window_row
from hpcperfstats.analysis.gen.jid_table import TypeDetailDataProvider
from hpcperfstats.analysis.gen.jid_table import gpu_acct_window_for_job_data
from hpcperfstats.analysis.gen.jid_table import jid_table
from hpcperfstats.site.machine.models import job_data
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


def test_normalize_job_accounting_host_list_accepts_list_tuple():
  """Accounting host_list is coerced from list/tuple."""
  assert _normalize_job_accounting_host_list(["a", "b"]) == ["a", "b"]
  assert _normalize_job_accounting_host_list(("x",)) == ["x"]


def test_normalize_job_accounting_host_list_rejects_non_sequence():
  """A lone datetime (corrupt cache/ORM) must not be iterated."""
  dt = datetime(2024, 1, 2, 3, 4, 5)
  assert _normalize_job_accounting_host_list(dt) == []
  assert _normalize_job_accounting_host_list(None) == []


def test_gpu_acct_window_for_job_data_builds_fqdns():
  """gpu_acct_window_for_job_data mirrors jid_table FQDN rules without full init."""
  from datetime import timezone as dt_utc

  class _Job:
    host_list = ["n1", "n2"]
    start_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=dt_utc.utc)
    end_time = datetime(2026, 1, 2, 0, 0, 0, tzinfo=dt_utc.utc)

  with patch(
      "hpcperfstats.analysis.gen.jid_table.cfg.get_host_name_ext",
      return_value="cluster.example",
  ):
    _st, _et, acct = gpu_acct_window_for_job_data(_Job())
  assert acct == ["n1.cluster.example", "n2.cluster.example"]


def test_gpu_acct_window_for_job_data_empty_when_no_window_times():
  """No start/end => empty accounting host list (same guard as jid_table)."""

  class _Job:
    host_list = ["n1"]
    start_time = None
    end_time = None

  _st, _et, acct = gpu_acct_window_for_job_data(_Job())
  assert acct == []


def test_normalize_host_data_schema_label_hashable_strings():
  """Schema labels must stringify nested structures so pandas unique() works."""
  assert _normalize_host_data_schema_label("cpu") == "cpu"
  assert _normalize_host_data_schema_label(None) is None
  assert _normalize_host_data_schema_label(["a", 1]) == '["a",1]'
  assert _normalize_host_data_schema_label({"z": 1, "a": 2}) == '{"a":2,"z":1}'


def test_coerce_jid_table_schema_dataframe_unique_on_list_types():
  """DataFrame with list-valued type column must coerce before unique()."""
  import pandas as pd

  df = pd.DataFrame(
      {
          "type": [["nested"], "ok", ["nested"]],
          "event": ["e1", "e2", "e1"],
      }
  )
  out = _coerce_jid_table_schema_dataframe(df)
  types = sorted(out["type"].unique().tolist())
  assert types == ['["nested"]', "ok"]
  assert len(out) == 3


def test_unpack_cached_job_window_row_tuple_and_model():
  """Cached row may be a values_list tuple or a legacy job_data instance."""
  st = datetime(2024, 1, 1, 0, 0, 0)
  et = datetime(2024, 1, 1, 1, 0, 0)
  assert _unpack_cached_job_window_row((["h1"], st, et)) == (["h1"], st, et)
  j = job_data(
      jid="z",
      submit_time=st,
      start_time=st,
      end_time=et,
      username="u",
      host_list=["n1"],
  )
  assert _unpack_cached_job_window_row(j) == (["n1"], st, et)
  assert _unpack_cached_job_window_row(None) == (None, None, None)
  assert _unpack_cached_job_window_row("bad") == (None, None, None)


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


def test_strided_distinct_times_for_large_job_falls_back_to_fixed_window_points(monkeypatch):
  """When SQL striding paths fail, return deterministic start/mid/end samples."""
  from datetime import timedelta
  from django.utils import timezone as django_tz

  start = django_tz.now()
  end = start + timedelta(minutes=30)
  monkeypatch.setattr(
      "hpcperfstats.analysis.gen.jid_table._strided_distinct_times_postgresql",
      lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("timeout")),
  )
  monkeypatch.setattr(
      "hpcperfstats.analysis.gen.jid_table.cfg.get_large_job_time_sample_sql_mode",
      lambda: "ntile",
  )
  sampled = _strided_distinct_times_for_large_job(
      start, end, ["n1.example.com"], 64)
  assert sampled[0] == start
  assert sampled[-1] == end
  assert len(sampled) == 3


def test_strided_distinct_times_for_large_job_degenerate_window_returns_start(monkeypatch):
  """Fallback sample for zero-width windows is a single timestamp."""
  from django.utils import timezone as django_tz

  start = django_tz.now()
  monkeypatch.setattr(
      "hpcperfstats.analysis.gen.jid_table._strided_distinct_times_postgresql",
      lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("timeout")),
  )
  monkeypatch.setattr(
      "hpcperfstats.analysis.gen.jid_table.cfg.get_large_job_time_sample_sql_mode",
      lambda: "ntile",
  )
  sampled = _strided_distinct_times_for_large_job(
      start, start, ["n1.example.com"], 64)
  assert sampled == [start]


def test_count_host_data_rows_for_window_postgres_error_falls_back_to_chunked(monkeypatch):
  """PostgreSQL row-count probe retries via chunked ORM on cursor/SQL failures."""
  hosts = ["n1.example.com", "n2.example.com", "n3.example.com"]

  class FakeCursor:
    def __enter__(self):
      return self

    def __exit__(self, exc_type, exc, tb):
      return False

    def execute(self, *_args, **_kwargs):
      raise RuntimeError("lost synchronization")

  class FakeConn:
    vendor = "postgresql"

    def __init__(self):
      self.closed_checked = False

    def cursor(self):
      return FakeCursor()

    def close_if_unusable_or_obsolete(self):
      self.closed_checked = True

  class FakeCountQS:
    def __init__(self, n):
      self._n = n

    def count(self):
      return self._n

  class FakeManager:
    def filter(self, **kwargs):
      return FakeCountQS(len(kwargs["host__in"]))

  fake_conn = FakeConn()
  fake_db_conn = type(
      "FakeDBConn",
      (),
      {"ops": type("FakeOps", (), {"quote_name": staticmethod(lambda s: s)})()},
  )()
  monkeypatch.setattr("django.db.connections", {"default": fake_conn}, raising=False)
  monkeypatch.setattr("django.db.connection", fake_db_conn, raising=False)
  monkeypatch.setattr("hpcperfstats.analysis.gen.jid_table.host_data.objects", FakeManager())

  out = _count_host_data_rows_for_window(1, 2, hosts)
  assert out == len(hosts)
  assert fake_conn.closed_checked is True


def test_count_host_data_rows_for_window_large_host_list_skips_postgres_any(monkeypatch):
  """Very large host lists bypass ANY(text[]) probe and use chunked ORM path."""
  hosts = [
      f"n{i}.example.com"
      for i in range(JID_TABLE_ROW_COUNT_POSTGRES_ARRAY_MAX_HOSTS + 1)
  ]

  class FakeConn:
    vendor = "postgresql"

    def cursor(self):
      raise AssertionError("cursor path should be bypassed for large host lists")

    def close_if_unusable_or_obsolete(self):
      return None

  class FakeCountQS:
    def __init__(self, n):
      self._n = n

    def count(self):
      return self._n

  class FakeManager:
    def filter(self, **kwargs):
      return FakeCountQS(len(kwargs["host__in"]))

  monkeypatch.setattr("django.db.connections", {"default": FakeConn()}, raising=False)
  monkeypatch.setattr("hpcperfstats.analysis.gen.jid_table.host_data.objects", FakeManager())

  out = _count_host_data_rows_for_window(1, 2, hosts)
  assert out == len(hosts)
