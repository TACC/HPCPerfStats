"""Unit tests for analysis.gen.jid_table (_ensure_tz) and utils.queryset_to_dataframe.

"""
from datetime import datetime, timezone

import pandas as pd
import pytest

pytestmark = pytest.mark.django_db(databases=[])

from contextlib import nullcontext
from unittest.mock import MagicMock, patch

from django.db import OperationalError
from django.db import connections

from hpcperfstats.analysis.metrics.lib.gen.jid_table import _coerce_jid_table_host_query_batch_size
from hpcperfstats.analysis.metrics.lib.gen.jid_table import _build_acct_host_fqdns
from hpcperfstats.analysis.metrics.lib.gen.jid_table import _listify_acct_hosts
from hpcperfstats.analysis.metrics.lib.gen.jid_table import _coerce_jid_table_schema_dataframe
from hpcperfstats.analysis.metrics.lib.gen.jid_table import _coerce_nonnegative_window_row_count
from hpcperfstats.analysis.metrics.lib.gen.jid_table import _count_host_data_rows_for_window
from hpcperfstats.analysis.metrics.lib.gen.jid_table import _count_host_data_rows_for_window_cached
from hpcperfstats.analysis.metrics.lib.gen.jid_table import _distinct_times_in_window_batched
from hpcperfstats.analysis.metrics.lib.gen.jid_table import _ensure_tz
from hpcperfstats.analysis.metrics.lib.gen.jid_table import _iter_acct_host_batches
from hpcperfstats.analysis.metrics.lib.gen.jid_table import _normalize_host_data_schema_label
from hpcperfstats.analysis.metrics.lib.gen.jid_table import _normalize_host_cell_for_host_data
from hpcperfstats.analysis.metrics.lib.gen.jid_table import _normalize_job_accounting_host_list
from hpcperfstats.analysis.metrics.lib.gen.jid_table import _normalize_window_bound_datetime
from hpcperfstats.analysis.metrics.lib.gen.jid_table import _ntile_bucket_max_timestamps
from hpcperfstats.analysis.metrics.lib.gen.jid_table import JID_TABLE_HOST_QUERY_BATCH
from hpcperfstats.analysis.metrics.lib.gen.jid_table import _strided_distinct_times_date_bin_postgresql
from hpcperfstats.analysis.metrics.lib.gen.jid_table import _strided_distinct_times_for_large_job
from hpcperfstats.analysis.metrics.lib.gen.jid_table import _unpack_cached_job_window_row
from hpcperfstats.analysis.metrics.lib.gen.jid_table import TypeDetailDataProvider
from hpcperfstats.analysis.metrics.lib.gen.jid_table import gpu_acct_window_for_job_data
from hpcperfstats.analysis.metrics.lib.gen.jid_table import jid_table
from hpcperfstats.site.lib.machine.models import job_data
from hpcperfstats.analysis.metrics.lib.gen.utils import iter_queryset_values_dicts
from hpcperfstats.analysis.metrics.lib.gen.utils import queryset_to_dataframe


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


def test_normalize_job_accounting_host_list_flattens_nested_sequences():
  assert _normalize_job_accounting_host_list([["n1"], "n2"]) == ["n1", "n2"]


def test_normalize_host_cell_for_host_data_unwraps_list_wrapped_scalar():
  assert _normalize_host_cell_for_host_data(["host.example.com"]) == "host.example.com"
  assert _normalize_host_cell_for_host_data(None) is None
  assert _normalize_host_cell_for_host_data({"a": 1}) is None


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
      "hpcperfstats.analysis.metrics.lib.gen.jid_table.cfg.get_host_name_ext",
      return_value="cluster.example",
  ):
    _st, _et, acct = gpu_acct_window_for_job_data(_Job())
  assert acct == ["n1.cluster.example", "n2.cluster.example"]


def test_gpu_acct_window_for_job_data_keeps_existing_fqdns():
  """FQDN host_list values must not get a duplicate host suffix appended."""
  from datetime import timezone as dt_utc

  class _Job:
    host_list = ["n1.cluster.example", "n2.cluster.example"]
    start_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=dt_utc.utc)
    end_time = datetime(2026, 1, 2, 0, 0, 0, tzinfo=dt_utc.utc)

  with patch(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table.cfg.get_host_name_ext",
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


def test_unpack_cached_job_window_row_three_tuple():
  """Jid cache stores values_list(host_list, start_time, end_time)."""
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
  assert _unpack_cached_job_window_row(j) == (None, None, None)
  assert _unpack_cached_job_window_row(None) == (None, None, None)
  assert _unpack_cached_job_window_row("bad") == (None, None, None)


def test_coerce_nonnegative_window_row_count_scalar_and_wrapped():
  """Cache serializers may return a count as int, str, or one-element list/tuple."""
  from collections import deque

  assert _coerce_nonnegative_window_row_count(None) is None
  assert _coerce_nonnegative_window_row_count(42) == 42
  assert _coerce_nonnegative_window_row_count("99") == 99
  assert _coerce_nonnegative_window_row_count([1_500_001]) == 1_500_001
  assert _coerce_nonnegative_window_row_count([[9_000_000]]) == 9_000_000
  assert _coerce_nonnegative_window_row_count(([3],)) == 3
  assert _coerce_nonnegative_window_row_count(deque([8_000_000])) == 8_000_000
  assert _coerce_nonnegative_window_row_count(True) is None
  assert _coerce_nonnegative_window_row_count(False) is None
  assert _coerce_nonnegative_window_row_count([1, 2]) is None
  assert _coerce_nonnegative_window_row_count([]) is None
  assert _coerce_nonnegative_window_row_count(-5) is None
  assert _coerce_nonnegative_window_row_count("net") is None


def test_count_host_data_rows_for_window_cached_accepts_list_wrapped_scalar(monkeypatch):
  """Large-job row-count cache hit must accept JSON-style single-element list values."""
  from datetime import timezone as dt_utc

  st = datetime(2026, 1, 1, 12, 0, 0, tzinfo=dt_utc.utc)
  et = datetime(2026, 1, 1, 13, 0, 0, tzinfo=dt_utc.utc)
  calls = {"count": 0}

  def _count_stub(*_a, **_k):
    calls["count"] += 1
    raise AssertionError("ORM count must not run when cache parses")

  monkeypatch.setattr(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table.cfg.get_large_job_window_row_count_cache_ttl",
      lambda: 60,
  )
  monkeypatch.setattr("hpcperfstats.analysis.metrics.lib.gen.jid_table.cache.get", lambda _k: [9_000_000])
  monkeypatch.setattr("hpcperfstats.analysis.metrics.lib.gen.jid_table.cache.set", lambda *a, **k: None)
  monkeypatch.setattr(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table._count_host_data_rows_for_window",
      _count_stub,
  )
  n = _count_host_data_rows_for_window_cached("j656931", st, et, ["n1.cluster.example"])
  assert n == 9_000_000
  assert calls["count"] == 0


def test_count_host_data_rows_for_window_cached_multi_element_list_recomputes(monkeypatch):
  """Malformed cache list must fall back to live COUNT and store a plain int."""
  from datetime import timezone as dt_utc

  st = datetime(2026, 2, 1, 12, 0, 0, tzinfo=dt_utc.utc)
  et = datetime(2026, 2, 1, 13, 0, 0, tzinfo=dt_utc.utc)
  set_calls = []

  monkeypatch.setattr(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table.cfg.get_large_job_window_row_count_cache_ttl",
      lambda: 120,
  )
  monkeypatch.setattr("hpcperfstats.analysis.metrics.lib.gen.jid_table.cache.get", lambda _k: [1, 2, 3])
  monkeypatch.setattr(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table.cache.set",
      lambda key, val, timeout=None: set_calls.append((key, val, timeout)),
  )
  monkeypatch.setattr(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table._count_host_data_rows_for_window",
      lambda *_a, **_k: 555,
  )
  n = _count_host_data_rows_for_window_cached("j2", st, et, ["h.x"])
  assert n == 555
  assert len(set_calls) == 1
  assert set_calls[0][1] == 555
  assert set_calls[0][2] == 120


def test_count_host_data_rows_for_window_cached_handles_invalid_int_parse(monkeypatch):
  """TTL getter failures fall back to default TTL and still run uncached COUNT (*)."""
  from datetime import timezone as dt_utc

  st = datetime(2026, 2, 2, 12, 0, 0, tzinfo=dt_utc.utc)
  et = datetime(2026, 2, 2, 13, 0, 0, tzinfo=dt_utc.utc)

  def _bad_ttl():
    raise ValueError("invalid literal for int() with base 10: 'net'")

  monkeypatch.setattr(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table.cfg.get_large_job_window_row_count_cache_ttl",
      _bad_ttl,
  )
  monkeypatch.setattr(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table._count_host_data_rows_for_window",
      lambda *_a, **_k: 917,
  )
  n = _count_host_data_rows_for_window_cached("j695088", st, et, ["h.x"])
  assert n == 917


def test_count_host_data_rows_for_window_cached_handles_non_numeric_count(monkeypatch):
  from datetime import timezone as dt_utc

  st = datetime(2026, 2, 2, 12, 0, 0, tzinfo=dt_utc.utc)
  et = datetime(2026, 2, 2, 13, 0, 0, tzinfo=dt_utc.utc)
  monkeypatch.setattr(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table.cfg.get_large_job_window_row_count_cache_ttl",
      lambda: 0,
  )
  monkeypatch.setattr(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table._count_host_data_rows_for_window",
      lambda *_a, **_k: ["net"],
  )
  n = _count_host_data_rows_for_window_cached("j696167_17", st, et, ["h.x"])
  assert n == 0


def test_count_host_data_rows_for_window_rejects_non_datetime_bounds(monkeypatch):
  class _FailingObjects:
    def filter(self, **kwargs):
      raise AssertionError("ORM filter should not run for non-datetime bounds")

  class _FailingHostData:
    objects = _FailingObjects()

  monkeypatch.setattr("hpcperfstats.analysis.metrics.lib.gen.jid_table.host_data", _FailingHostData())
  n = _count_host_data_rows_for_window(start=["bad"], end=["bad"], acct_hosts=["h.x"])
  assert n == 0


def test_count_host_data_rows_for_window_rejects_deque_list_bounds(monkeypatch):
  from collections import deque

  class _FailingObjects:
    def filter(self, **kwargs):
      raise AssertionError("ORM filter should not run for list-like bound wrappers")

  class _FailingHostData:
    objects = _FailingObjects()

  monkeypatch.setattr("hpcperfstats.analysis.metrics.lib.gen.jid_table.host_data", _FailingHostData())
  n = _count_host_data_rows_for_window(
      start=deque([["bad"]]),
      end=deque([["bad"]]),
      acct_hosts=["h.x"],
  )
  assert n == 0


def test_normalize_window_bound_datetime_unwraps_singleton_sequences():
  dt = datetime(2026, 5, 6, 22, 13, 0)
  assert _normalize_window_bound_datetime([[dt]]) == dt
  assert _normalize_window_bound_datetime(["not-datetime"]) is None
  assert _normalize_window_bound_datetime([dt, dt]) is None


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


def test_jid_table_get_llite_delta_by_event_cache_set_failure_still_returns_df():
  """``get_llite_delta_by_event`` uses ``cached_orm``; set failure must not drop results."""
  from hpcperfstats.analysis.metrics.lib.gen.jid_table import jid_table
  from hpcperfstats.site.lib.machine import cache_utils as cu

  class FakeQs:
    def values(self, *args):
      return self

    def annotate(self, **kwargs):
      return self

    def order_by(self, *args):
      return self

    def __iter__(self):
      yield {"event": "read_bytes", "delta_sum": 10.0}
      yield {"event": "write_bytes", "delta_sum": 20.0}

  inst = jid_table.__new__(jid_table)
  inst.jid = "jid-llite-set-fail"
  inst._large_job_plot_cache_token = "full"
  inst._host_data_qs = lambda **extra: FakeQs()

  mock_cache = MagicMock()
  mock_cache.get.side_effect = lambda key, default=None: default
  mock_cache.set.side_effect = OSError("redis read-only")

  with patch.object(cu, "cache", mock_cache):
    with patch(
        "hpcperfstats.analysis.metrics.lib.gen.jid_table.get_site_content_cache_timeout",
        return_value=60,
    ):
      df = jid_table.get_llite_delta_by_event(inst)

  assert isinstance(df, pd.DataFrame)
  assert not df.empty
  assert "event" in df.columns
  mock_cache.set.assert_called()


def test_jid_table_get_nfs_delta_totals_mb_cache_set_failure_still_returns_list():
  """``get_nfs_delta_totals_mb`` uses ``cached_orm``; set failure must not drop totals."""
  from hpcperfstats.analysis.metrics.lib.gen.jid_table import jid_table
  from hpcperfstats.site.lib.machine import cache_utils as cu

  class FakeQs:
    def values(self, *args):
      return self

    def annotate(self, **kwargs):
      return self

    def order_by(self, *args):
      return self

    def __iter__(self):
      yield {"event": "normal_read", "delta_sum": 2 * 1024 * 1024}
      yield {"event": "normal_write", "delta_sum": 1024 * 1024}

  inst = jid_table.__new__(jid_table)
  inst.jid = "jid-nfs-set-fail"
  inst._large_job_plot_cache_token = "full"
  inst._host_data_qs = lambda **extra: FakeQs()

  mock_cache = MagicMock()
  mock_cache.get.side_effect = lambda key, default=None: default
  mock_cache.set.side_effect = RuntimeError("no write")

  with patch.object(cu, "cache", mock_cache):
    with patch(
        "hpcperfstats.analysis.metrics.lib.gen.jid_table.get_site_content_cache_timeout",
        return_value=60,
    ):
      out = jid_table.get_nfs_delta_totals_mb(inst)

  assert out == [2.0, 1.0]
  mock_cache.set.assert_called()


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


def test_strided_distinct_times_date_bin_postgresql_skips_distinct_when_sql_nonempty(
    monkeypatch,
):
  """Grouped-max SQL path must not call the batched DISTINCT time enumerator."""
  from datetime import timedelta, timezone as dt_utc

  monkeypatch.setattr(connections["default"], "vendor", "postgresql")
  distinct_calls = []

  def fake_sql(*_a, **_k):
    st = datetime(2025, 1, 1, 0, 0, tzinfo=dt_utc.utc)
    return [st, st + timedelta(seconds=30)]

  monkeypatch.setattr(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table._strided_distinct_times_date_bin_via_grouped_max_sql",
      fake_sql,
  )
  monkeypatch.setattr(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table._distinct_times_in_window_batched",
      lambda *a, **k: distinct_calls.append(1),
  )
  st = datetime(2025, 1, 1, 0, 0, tzinfo=dt_utc.utc)
  en = st + timedelta(minutes=5)
  out = _strided_distinct_times_date_bin_postgresql(st, en, ["n1.example.com"], 64)
  assert out == [st, st + timedelta(seconds=30)]
  assert distinct_calls == []


def test_strided_distinct_times_date_bin_postgresql_falls_back_when_sql_empty(
    monkeypatch,
):
  """When grouped-max SQL returns no rows, fall back to DISTINCT + Python bins."""
  from datetime import timedelta, timezone as dt_utc

  monkeypatch.setattr(connections["default"], "vendor", "postgresql")
  distinct_calls = []
  st = datetime(2025, 1, 1, 0, 0, tzinfo=dt_utc.utc)

  def fake_distinct(*_a, **_k):
    distinct_calls.append(1)
    return [st + timedelta(seconds=i) for i in range(10)]

  monkeypatch.setattr(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table._strided_distinct_times_date_bin_via_grouped_max_sql",
      lambda *_a, **_k: [],
  )
  monkeypatch.setattr(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table._distinct_times_in_window_batched",
      fake_distinct,
  )
  en = st + timedelta(seconds=100)
  out = _strided_distinct_times_date_bin_postgresql(st, en, ["n1.example.com"], 8)
  assert distinct_calls == [1]
  assert out
  assert out[0] >= st
  assert out[-1] <= en


def test_strided_distinct_times_for_large_job_falls_back_to_fixed_window_points(monkeypatch):
  """When SQL striding paths fail, return deterministic start/mid/end samples."""
  from datetime import timedelta
  from django.utils import timezone as django_tz

  start = django_tz.now()
  end = start + timedelta(minutes=30)
  monkeypatch.setattr(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table._strided_distinct_times_postgresql",
      lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("timeout")),
  )
  monkeypatch.setattr(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table.cfg.get_large_job_time_sample_sql_mode",
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
      "hpcperfstats.analysis.metrics.lib.gen.jid_table._strided_distinct_times_postgresql",
      lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("timeout")),
  )
  monkeypatch.setattr(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table.cfg.get_large_job_time_sample_sql_mode",
      lambda: "ntile",
  )
  sampled = _strided_distinct_times_for_large_job(
      start, start, ["n1.example.com"], 64)
  assert sampled == [start]


def test_ntile_bucket_max_timestamps_ten_into_three_buckets():
  """NTILE(3) over 10 ordered rows uses bucket sizes 4,3,3; maxima at last of each."""
  ts = list(range(10))
  assert _ntile_bucket_max_timestamps(ts, 3) == [3, 6, 9]


def test_coerce_jid_table_host_query_batch_size_rejects_non_numeric_strings():
  """Hostnames mistaken for batch sizes must fall back (large-job row count must not raise)."""
  assert _coerce_jid_table_host_query_batch_size(
      "c641-092.vista.tacc.utexas.edu") == JID_TABLE_HOST_QUERY_BATCH
  assert _coerce_jid_table_host_query_batch_size(None) == JID_TABLE_HOST_QUERY_BATCH
  assert _coerce_jid_table_host_query_batch_size(128) == 128
  assert _coerce_jid_table_host_query_batch_size("32") == 32
  assert _coerce_jid_table_host_query_batch_size(0) == JID_TABLE_HOST_QUERY_BATCH


def test_listify_acct_hosts_wraps_fqdn_string_without_splitting_chars():
  """A lone FQDN string must not become one character per pseudo-host."""
  fqdn = "c608-081.vista.tacc.utexas.edu"
  assert _listify_acct_hosts(fqdn) == [fqdn]
  assert _listify_acct_hosts("  " + fqdn + "  ") == [fqdn]


def test_listify_acct_hosts_comma_separated_short_names():
  assert _listify_acct_hosts("a,b, c") == ["a", "b", "c"]


def test_listify_acct_hosts_flattens_nested_sequences_and_dedupes():
  nested = [["n1.example"], ("n2.example", ["n3.example", "n1.example"])]
  assert _listify_acct_hosts(nested) == ["n1.example", "n2.example", "n3.example"]


def test_iter_acct_host_batches_accepts_single_fqdn_string():
  fqdn = "c608-081.vista.tacc.utexas.edu"
  chunks = list(_iter_acct_host_batches(fqdn))
  assert chunks == [[fqdn]]


def test_normalize_job_accounting_host_list_accepts_plain_string():
  fqdn = "c608-081.vista.tacc.utexas.edu"
  assert _normalize_job_accounting_host_list(fqdn) == [fqdn]


def test_build_acct_host_fqdns_normalizes_suffix_and_avoids_double_append():
  """host_name_ext with leading dot should still generate one correct suffix."""
  with patch(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table.cfg.get_host_name_ext",
      return_value=".cluster.example",
  ):
    out = _build_acct_host_fqdns(["n1", "n2.cluster.example", ""])
  assert out == ["n1.cluster.example", "n2.cluster.example"]


def test_iter_acct_host_batches_non_numeric_batch_size_uses_default_chunking():
  """Invalid batch_size must not raise; chunk like default JID_TABLE_HOST_QUERY_BATCH."""
  n = JID_TABLE_HOST_QUERY_BATCH + 5
  hosts = [f"n{i}.example.com" for i in range(n)]
  chunks = list(
      _iter_acct_host_batches(hosts, "c641-092.vista.tacc.utexas.edu"))
  assert len(chunks) == 2
  assert len(chunks[0]) == JID_TABLE_HOST_QUERY_BATCH
  assert len(chunks[1]) == 5


def test_count_host_data_rows_for_window_flattens_nested_acct_hosts(monkeypatch):
  captured_host_chunks = []

  class _FakeCountQuerySet:
    def count(self):
      return 7

  class _FakeObjects:
    def filter(self, **kwargs):
      captured_host_chunks.append(kwargs["host__in"])
      return _FakeCountQuerySet()

  class _FakeHostData:
    objects = _FakeObjects()

  monkeypatch.setattr("hpcperfstats.analysis.metrics.lib.gen.jid_table.host_data", _FakeHostData())
  start = datetime(2026, 5, 1, 0, 0, 0)
  end = datetime(2026, 5, 1, 0, 5, 0)
  n = _count_host_data_rows_for_window(
      start,
      end,
      [["n1.example"], ["n2.example", ["n3.example"]]],
  )
  assert n == 7
  assert captured_host_chunks == [["n1.example", "n2.example", "n3.example"]]


def test_distinct_times_in_window_batched_uses_host_chunks(monkeypatch):
  """Strided-time prep runs one DISTINCT-time ORM query per host__in batch."""
  chunk_lens = []

  class FakeQS:
    def __init__(self, n_hosts):
      self._n_hosts = n_hosts

    def values_list(self, *_a, **_k):
      return self

    def distinct(self):
      chunk_lens.append(self._n_hosts)
      return self

    def __iter__(self):
      return iter(())

  class FakeManager:
    def filter(self, **kwargs):
      return FakeQS(len(kwargs["host__in"]))

  monkeypatch.setattr(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table._pg_relax_statement_timeout_for_large_job_time_sql",
      nullcontext,
  )
  monkeypatch.setattr("hpcperfstats.analysis.metrics.lib.gen.jid_table.host_data.objects", FakeManager())

  n = JID_TABLE_HOST_QUERY_BATCH + 5
  hosts = ["h{0}.x".format(i) for i in range(n)]
  _distinct_times_in_window_batched(1, 2, hosts)
  assert len(chunk_lens) == 2
  assert chunk_lens[0] == JID_TABLE_HOST_QUERY_BATCH
  assert chunk_lens[1] == 5


def test_count_host_data_rows_for_window_chunked_single_batch(monkeypatch):
  """Row-count probe uses chunked ORM; one batch when host list fits batch size."""
  hosts = ["n1.example.com", "n2.example.com", "n3.example.com"]

  class FakeCountQS:
    def __init__(self, n):
      self._n = n

    def count(self):
      return self._n

  class FakeManager:
    def filter(self, **kwargs):
      return FakeCountQS(len(kwargs["host__in"]))

  monkeypatch.setattr("hpcperfstats.analysis.metrics.lib.gen.jid_table.host_data.objects", FakeManager())

  st = datetime(2026, 5, 1, 0, 0, 0)
  et = datetime(2026, 5, 1, 0, 1, 0)
  out = _count_host_data_rows_for_window(st, et, hosts)
  assert out == len(hosts)


def test_count_host_data_rows_for_window_chunked_multiple_batches(monkeypatch):
  """Row-count probe sums one COUNT per host__in batch (no raw SQL)."""
  n = JID_TABLE_HOST_QUERY_BATCH + 5
  hosts = [f"n{i}.example.com" for i in range(n)]

  class FakeCountQS:
    def __init__(self, n_chunk):
      self._n_chunk = n_chunk

    def count(self):
      return self._n_chunk

  class FakeManager:
    def filter(self, **kwargs):
      return FakeCountQS(len(kwargs["host__in"]))

  monkeypatch.setattr("hpcperfstats.analysis.metrics.lib.gen.jid_table.host_data.objects", FakeManager())

  st = datetime(2026, 5, 1, 0, 0, 0)
  et = datetime(2026, 5, 1, 0, 1, 0)
  out = _count_host_data_rows_for_window(st, et, hosts)
  assert out == n


def test_count_host_data_rows_retries_after_lost_sync(monkeypatch):
  """One retry with close_old_connections after psycopg desynchronization."""
  calls = {"n": 0}

  class FakeCountQS:
    def count(self):
      calls["n"] += 1
      if calls["n"] == 1:
        raise OperationalError(
            'lost synchronization with server: got message type "1", length 942485560'
        )
      return 7

  class FakeManager:
    def filter(self, **_kwargs):
      return FakeCountQS()

  monkeypatch.setattr("hpcperfstats.analysis.metrics.lib.gen.jid_table.host_data.objects", FakeManager())
  closed = []

  monkeypatch.setattr(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table.close_old_connections",
      lambda: closed.append(1),
  )
  st = datetime(2026, 5, 1, 0, 0, 0)
  et = datetime(2026, 5, 1, 0, 1, 0)
  out = _count_host_data_rows_for_window(st, et, ["h1.example.com"])
  assert out == 7
  assert calls["n"] == 2
  assert len(closed) == 2


def test_is_statement_timeout_error_detects_operational_timeout():
  from hpcperfstats.analysis.metrics.lib.gen.jid_table import _is_statement_timeout_error

  assert _is_statement_timeout_error(
      OperationalError("canceling statement due to statement timeout")
  )
  assert not _is_statement_timeout_error(OperationalError("connection refused"))


def test_queryset_to_dataframe_with_host_chunk_retry_splits_on_timeout(monkeypatch):
  """On statement timeout, halve host__in and merge sub-chunk results."""
  from hpcperfstats.analysis.metrics.lib.gen.jid_table import (
      _queryset_to_dataframe_with_host_chunk_retry,
  )

  hosts = ["h{0}.example.com".format(i) for i in range(8)]
  chunk_sizes = []

  def build_qs(chunk):
    chunk_sizes.append(len(chunk))
    return ("marker", len(chunk))

  def fake_q2df(qs, columns=None):
    _, size = qs
    if size == 8:
      raise OperationalError("canceling statement due to statement timeout")
    return pd.DataFrame([{"host": "h0.example.com", "sum_val": float(size)}])

  monkeypatch.setattr(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table.queryset_to_dataframe",
      fake_q2df,
  )
  monkeypatch.setattr(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table.close_old_connections",
      lambda: None,
  )
  out = _queryset_to_dataframe_with_host_chunk_retry(hosts, build_qs)
  assert chunk_sizes == [8, 4, 4]
  assert len(out) == 2
  assert set(out["sum_val"].tolist()) == {4.0}


def test_type_detail_get_aggregate_df_sql_fast_path(monkeypatch):
  """SQL Sum/annotate path returns sum_val without pandas groupby on raw rows."""
  st = datetime(2024, 6, 1, tzinfo=timezone.utc)
  et = datetime(2024, 6, 2, tzinfo=timezone.utc)
  provider = TypeDetailDataProvider(
      jid="j-sql",
      type_name="mdc",
      start_time=st,
      end_time=et,
      host_list=["n1.example.com", "n2.example.com"],
  )
  filter_calls = []

  class Qs:
    def values(self, *cols):
      return self

    def annotate(self, **kwargs):
      return self

    def order_by(self, *args):
      return self

    def __iter__(self):
      return iter([
          {"host": "n1.example.com", "time": 1, "sum_val": 3.0},
          {"host": "n2.example.com", "time": 2, "sum_val": 5.0},
      ])

  class Mgr:
    def filter(self, **kwargs):
      filter_calls.append(kwargs)
      return Qs()

  monkeypatch.setattr("hpcperfstats.analysis.metrics.lib.gen.jid_table.host_data.objects", Mgr())
  with patch(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table.cached_orm",
      lambda _k, _ttl, fn: fn(),
  ):
    out = provider.get_aggregate_df("ldlm_cancel", metric="arc")
  assert len(filter_calls) == 1
  assert filter_calls[0]["event"] == "ldlm_cancel"
  assert out["sum_val"].tolist() == [3.0, 5.0]
