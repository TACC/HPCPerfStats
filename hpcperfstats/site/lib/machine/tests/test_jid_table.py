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


def test_is_metrics_compute_control_flow_error_matches_only_timeouts():
  """The fallback guard keys off timeout control flow."""
  from hpcperfstats.analysis.metrics.lib.gen.jid_table import (
      is_metrics_compute_control_flow_error,
  )

  assert is_metrics_compute_control_flow_error(
      TimeoutError("statement budget exceeded")) is True
  assert is_metrics_compute_control_flow_error(
      OperationalError("canceling statement")) is False


def test_jid_table_get_aggregate_df_reraises_compute_timeout():
  """A query timeout must propagate, not restart the pandas path."""

  inst = jid_table.__new__(jid_table)
  inst.jid = "jid-agg-timeout"
  inst._large_job_plot_cache_token = "full"
  inst._base_filter = {
      "host__in": ["n1.example.com"],
      "time__gte": datetime(2024, 6, 1, tzinfo=timezone.utc),
      "time__lte": datetime(2024, 6, 2, tzinfo=timezone.utc),
  }
  calls = []

  def fake_queryset_to_dataframe(_qs, columns=None):
    calls.append(1)
    raise TimeoutError("statement budget exceeded for jid jid-agg-timeout")

  with patch(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table.queryset_to_dataframe",
      fake_queryset_to_dataframe,
  ), patch(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table.cached_orm",
      lambda _key, _timeout, query_fn: query_fn(),
  ), pytest.raises(TimeoutError):
    jid_table.get_aggregate_df(inst, "host_cpu", "arc", ["user"])

  assert calls == [1], "pandas fallback must not run after a timeout"


def test_jid_table_get_aggregate_df_sql_timeout_splits_not_pandas_fallback():
  """PG statement timeout on SQL SUM must host-split; skip raw-row fallback."""
  inst = jid_table.__new__(jid_table)
  inst.jid = "jid-agg-pg-timeout"
  inst._large_job_plot_cache_token = "full"
  hosts = [f"n{i}.example.com" for i in range(8)]
  inst._base_filter = {
      "host__in": hosts,
      "time__gte": datetime(2024, 6, 1, tzinfo=timezone.utc),
      "time__lte": datetime(2024, 6, 2, tzinfo=timezone.utc),
  }
  pandas_calls = []
  retry_calls = []

  def fake_pandas(*_a, **_k):
    pandas_calls.append(1)
    raise AssertionError("pandas fallback must not run when split succeeds")

  def retry_with_split(host_chunk, build_qs, **kwargs):
    hosts_list = [str(h) for h in host_chunk if h]
    retry_calls.append(len(hosts_list))
    if len(hosts_list) > 4:
      mid = max(1, len(hosts_list) // 2)
      left = retry_with_split(hosts_list[:mid], build_qs, **kwargs)
      right = retry_with_split(hosts_list[mid:], build_qs, **kwargs)
      return pd.concat([left, right], ignore_index=True)
    return pd.DataFrame(
        {
            "host": [hosts_list[0]],
            "time": [datetime(2024, 6, 1, 12, tzinfo=timezone.utc)],
            "sum_val": [1.0],
        }
    )

  with patch(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table."
      "_queryset_to_dataframe_with_host_chunk_retry",
      retry_with_split,
  ), patch(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table."
      "_fetch_host_data_values_frames",
      fake_pandas,
  ), patch(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table.cached_orm",
      lambda _key, _timeout, query_fn: query_fn(),
  ), patch(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table."
      "host_data_sum_val_per_sample_queryset",
      lambda qs, _col: qs,
  ), patch(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table."
      "host_data_restore_time_column",
      lambda df: df,
  ), patch(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table.host_data",
  ) as mock_hd:
    mock_hd.objects.filter.return_value.filter.return_value = (
        mock_hd.objects.filter.return_value
    )
    out = jid_table.get_aggregate_df(inst, "nvidia_gpu", "arc", ["gpu_util"])

  assert pandas_calls == []
  assert retry_calls, "expected host-chunk retry to run"
  assert not out.empty
  assert "sum_val" in out.columns


def test_jid_table_get_aggregate_df_pandas_fallback_uses_fetch_frames():
  """When SQL path fails hard, pandas fallback must use chunked fetch frames."""
  inst = jid_table.__new__(jid_table)
  inst.jid = "jid-agg-pandas-fallback"
  inst._large_job_plot_cache_token = "full"
  inst._base_filter = {
      "host__in": ["n1.example.com", "n2.example.com"],
      "time__gte": datetime(2024, 6, 1, tzinfo=timezone.utc),
      "time__lte": datetime(2024, 6, 2, tzinfo=timezone.utc),
  }
  fetch_batches = []

  def fail_sql(_host_chunk, _build_qs, **_kwargs):
    raise OperationalError("canceling statement due to statement timeout")

  def fake_fetch(host_list, build_qs, batch_size=None, **_kwargs):
    fetch_batches.append(batch_size)
    rows = []
    for h in host_list:
      # Drive build_qs so DCGM/filter wiring still runs (host[, time_filter]).
      try:
        _ = build_qs([h], {"time__gte": datetime(2024, 6, 1, tzinfo=timezone.utc)})
      except TypeError:
        _ = build_qs([h])
      rows.append(
          {
              "host": h,
              "time": datetime(2024, 6, 1, 12, tzinfo=timezone.utc),
              "arc": 2.0,
          }
      )
    return pd.DataFrame(rows)

  with patch(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table."
      "_queryset_to_dataframe_with_host_chunk_retry",
      fail_sql,
  ), patch(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table."
      "_fetch_host_data_values_frames",
      fake_fetch,
  ), patch(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table.cached_orm",
      lambda _key, _timeout, query_fn: query_fn(),
  ), patch(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table."
      "host_data_sum_val_per_sample_queryset",
      lambda qs, _col: qs,
  ), patch(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table.host_data",
  ) as mock_hd:
    mock_hd.objects.filter.return_value = mock_hd.objects.filter.return_value
    mock_hd.objects.filter.return_value.filter.return_value = (
        mock_hd.objects.filter.return_value
    )
    mock_hd.objects.filter.return_value.values.return_value = []
    out = jid_table.get_aggregate_df(inst, "nvidia_gpu", "arc", ["gpu_util"])

  from hpcperfstats.analysis.metrics.lib.gen.jid_table import (
      TYPE_DETAIL_HOST_QUERY_BATCH,
  )

  assert fetch_batches == [TYPE_DETAIL_HOST_QUERY_BATCH]
  assert not out.empty
  assert set(out["sum_val"].tolist()) == {2.0}


def test_type_detail_get_aggregate_df_reraises_compute_timeout():
  """TypeDetailDataProvider must not swallow metrics timeout control flow."""

  provider = TypeDetailDataProvider(
      jid="jid-type-detail-timeout",
      type_name="pmc",
      start_time=datetime(2024, 6, 1, tzinfo=timezone.utc),
      end_time=datetime(2024, 6, 2, tzinfo=timezone.utc),
      host_list=["n1.example.com"],
  )
  fallback_calls = []

  def fake_retry(_host_chunk, _build_qs, **_kwargs):
    raise TimeoutError("statement budget exceeded for jid jid-type-detail-timeout")

  def fake_fallback(*_args, **_kwargs):
    fallback_calls.append(1)
    return pd.DataFrame(columns=["host", "time", "arc"])

  with patch(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table."
      "_queryset_to_dataframe_with_host_chunk_retry",
      fake_retry,
  ), patch(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table."
      "_fetch_host_data_values_frames",
      fake_fallback,
  ), patch(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table.cached_orm",
      lambda _key, _timeout, query_fn: query_fn(),
  ), pytest.raises(TimeoutError):
    provider.get_aggregate_df("FLOPS", metric="arc")

  assert fallback_calls == []


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
  inst.acct_host_list = ["h1.example.com"]
  inst._base_filter = {
      "host__in": ["h1.example.com"],
      "time__gte": datetime(2024, 1, 1, tzinfo=timezone.utc),
      "time__lte": datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
  }
  inst._host_data_time_filter_kwargs = lambda: {
      "time__gte": datetime(2024, 1, 1, tzinfo=timezone.utc),
      "time__lte": datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
  }

  mock_cache = MagicMock()
  mock_cache.get.side_effect = lambda key, default=None: default
  mock_cache.set.side_effect = OSError("redis read-only")

  with patch.object(cu, "cache", mock_cache):
    with patch(
        "hpcperfstats.analysis.metrics.lib.gen.jid_table.get_site_content_cache_timeout",
        return_value=60,
    ):
      with patch(
          "hpcperfstats.analysis.metrics.lib.gen.jid_table.host_data"
      ) as hd:
        hd.objects.filter.return_value = FakeQs()
        df = jid_table.get_llite_delta_by_event(inst)

  assert isinstance(df, pd.DataFrame)
  assert not df.empty
  assert "event" in df.columns
  mock_cache.set.assert_called()


def test_jid_table_get_beegfs_delta_by_event_cache_set_failure_still_returns_df():
  """``get_beegfs_delta_by_event`` uses ``cached_orm``; set failure must not drop results."""
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
      yield {"event": "vfs_read_bytes", "delta_sum": 10.0}
      yield {"event": "vfs_write_bytes", "delta_sum": 20.0}

  inst = jid_table.__new__(jid_table)
  inst.jid = "jid-beegfs-set-fail"
  inst._large_job_plot_cache_token = "full"
  inst.acct_host_list = ["h1.example.com"]
  inst._base_filter = {
      "host__in": ["h1.example.com"],
      "time__gte": datetime(2024, 1, 1, tzinfo=timezone.utc),
      "time__lte": datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
  }
  inst._host_data_time_filter_kwargs = lambda: {
      "time__gte": datetime(2024, 1, 1, tzinfo=timezone.utc),
      "time__lte": datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
  }

  mock_cache = MagicMock()
  mock_cache.get.side_effect = lambda key, default=None: default
  mock_cache.set.side_effect = OSError("redis read-only")

  with patch.object(cu, "cache", mock_cache):
    with patch(
        "hpcperfstats.analysis.metrics.lib.gen.jid_table.get_site_content_cache_timeout",
        return_value=60,
    ):
      with patch(
          "hpcperfstats.analysis.metrics.lib.gen.jid_table.host_data"
      ) as hd:
        hd.objects.filter.return_value = FakeQs()
        df = jid_table.get_beegfs_delta_by_event(inst)

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
  inst.acct_host_list = ["h1.example.com"]
  inst._base_filter = {
      "host__in": ["h1.example.com"],
      "time__gte": datetime(2024, 1, 1, tzinfo=timezone.utc),
      "time__lte": datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
  }
  inst._host_data_time_filter_kwargs = lambda: {
      "time__gte": datetime(2024, 1, 1, tzinfo=timezone.utc),
      "time__lte": datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
  }

  mock_cache = MagicMock()
  mock_cache.get.side_effect = lambda key, default=None: default
  mock_cache.set.side_effect = RuntimeError("no write")

  with patch.object(cu, "cache", mock_cache):
    with patch(
        "hpcperfstats.analysis.metrics.lib.gen.jid_table.get_site_content_cache_timeout",
        return_value=60,
    ):
      with patch(
          "hpcperfstats.analysis.metrics.lib.gen.jid_table.host_data"
      ) as hd:
        hd.objects.filter.return_value = FakeQs()
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


def _compiled_host_data_sql(qs):
  sql, _params = qs.query.get_compiler(using="default").as_sql()
  return sql


def _compiled_group_by_terms(qs):
  sql = _compiled_host_data_sql(qs)
  assert "GROUP BY " in sql, sql
  clause = sql.split("GROUP BY ", 1)[1]
  for stop in (" ORDER BY ", " HAVING ", " LIMIT "):
    clause = clause.split(stop, 1)[0]
  return [term.strip() for term in clause.split(",")]


def _host_data_agg_base_qs():
  from hpcperfstats.site.lib.machine.models import host_data

  return host_data.objects.filter(
      host__in=["h1.example.com", "h2.example.com"],
      type="host_cpu",
      event__in=["user", "system"],
  )


def test_host_data_sum_val_annotation_resolves_float_output_field():
  """``Coalesce(Sum(RealField), Value(0))`` raised FieldError when compiled."""
  from django.db.models import FloatField, Sum, Value
  from django.db.models.functions import Coalesce

  from hpcperfstats.analysis.metrics.lib.gen.jid_table import (
      host_data_sum_val_annotation,
  )

  with pytest.raises(Exception):
    Coalesce(Sum("arc"), Value(0)).output_field  # noqa: B018

  assert isinstance(host_data_sum_val_annotation("arc").output_field, FloatField)


def test_host_data_per_sample_queryset_groups_by_host_and_time():
  """Grouping must survive PostgreSQL's primary-key functional dependency."""
  from hpcperfstats.analysis.metrics.lib.gen.jid_table import (
      HOST_DATA_SUM_VAL_ALIAS,
      host_data_sum_val_annotation,
      host_data_sum_val_per_sample_queryset,
  )

  collapsed = (
      _host_data_agg_base_qs().values("host", "time")
      .annotate(**{HOST_DATA_SUM_VAL_ALIAS: host_data_sum_val_annotation("arc")})
      .order_by("host", "time")
  )
  assert len(_compiled_group_by_terms(collapsed)) == 1, (
      "raw values('host', 'time') no longer collapses; the ExpressionWrapper "
      "workaround in host_data_sum_val_per_sample_queryset can be simplified"
  )

  safe = host_data_sum_val_per_sample_queryset(_host_data_agg_base_qs(), "arc")
  assert len(_compiled_group_by_terms(safe)) == 2


def test_host_data_per_sample_queryset_sql_shape():
  """Float coalesce by default, optional non-negative FILTER, opt-out NULL sums."""
  from hpcperfstats.analysis.metrics.lib.gen.jid_table import (
      host_data_sum_val_per_sample_queryset,
  )

  sql = _compiled_host_data_sql(
      host_data_sum_val_per_sample_queryset(_host_data_agg_base_qs(), "arc"))
  assert 'COALESCE(SUM("host_data"."arc")' in sql
  assert "FILTER" not in sql

  filtered = _compiled_host_data_sql(
      host_data_sum_val_per_sample_queryset(
          _host_data_agg_base_qs(), "arc", nonnegative_only=True)
  )
  assert 'FILTER (WHERE "host_data"."arc" >= ' in filtered

  no_coalesce = _compiled_host_data_sql(
      host_data_sum_val_per_sample_queryset(
          _host_data_agg_base_qs(), "value", coalesce_zero=False)
  )
  assert "COALESCE" not in no_coalesce
  assert 'SUM("host_data"."value")' in no_coalesce


def test_host_data_restore_time_column_renames_alias():
  from hpcperfstats.analysis.metrics.lib.gen.jid_table import (
      HOST_DATA_SUM_VAL_ALIAS,
      HOST_DATA_TIME_ALIAS,
      host_data_restore_time_column,
  )

  df = pd.DataFrame(
      [{"host": "h1", HOST_DATA_TIME_ALIAS: 1, HOST_DATA_SUM_VAL_ALIAS: 2.0}])
  assert host_data_restore_time_column(df).columns.tolist() == [
      "host", "time", HOST_DATA_SUM_VAL_ALIAS
  ]
  passthrough = pd.DataFrame([{"host": "h1", "time": 1}])
  assert host_data_restore_time_column(passthrough) is passthrough
  assert host_data_restore_time_column(None) is None


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
  ), patch(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table.cfg."
      "get_metrics_plot_aggregate_time_slice_s",
      lambda: 86400 * 7,
  ):
    out = provider.get_aggregate_df("ldlm_cancel", metric="arc")
  assert len(filter_calls) == 1
  assert filter_calls[0]["event"] == "ldlm_cancel"
  assert out["sum_val"].tolist() == [3.0, 5.0]


def test_resolve_plot_aggregate_time_bucket_count_design_capacity_5000():
  """Design 5000×48×60: budget/hosts caps buckets (not full 2048 times)."""
  from hpcperfstats.analysis.metrics.lib.gen.jid_table import (
      _resolve_plot_aggregate_time_bucket_count,
  )

  with patch(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table.cfg.get_large_job_time_buckets",
      lambda: 2048,
  ), patch(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table.cfg."
      "get_plot_aggregate_max_host_time_points",
      lambda: 1_000_000,
  ):
    # 5000 hosts → floor(1e6/5000)=200 (14.4M host-sample design capacity).
    assert _resolve_plot_aggregate_time_bucket_count(5000) == 200


def test_iter_aggregate_time_filter_chunks_wall_and_time_in():
  from hpcperfstats.analysis.metrics.lib.gen.jid_table import (
      _iter_aggregate_time_filter_chunks,
  )

  start = datetime(2024, 1, 1, tzinfo=timezone.utc)
  end = datetime(2024, 1, 1, 3, tzinfo=timezone.utc)
  chunks = list(
      _iter_aggregate_time_filter_chunks(
          {"time__gte": start, "time__lte": end},
          3600,
      )
  )
  assert len(chunks) == 3
  times = [
      datetime(2024, 1, 1, i, tzinfo=timezone.utc) for i in range(5)
  ]
  tin = list(
      _iter_aggregate_time_filter_chunks({"time__in": times}, 120)
  )
  # slice_s=120 → 2 timestamps per chunk
  assert len(tin) == 3
  assert len(tin[0]["time__in"]) == 2


def test_split_time_filter_for_timeout_bisects_time_in():
  from hpcperfstats.analysis.metrics.lib.gen.jid_table import (
      _split_time_filter_for_timeout,
  )

  times = list(range(4))
  left, right = _split_time_filter_for_timeout({"time__in": times})
  assert left["time__in"] == [0, 1]
  assert right["time__in"] == [2, 3]
  assert _split_time_filter_for_timeout({"time__in": [1]}) is None


def test_assemble_sum_val_parts_bounded_single_part_skips_concat():
  from hpcperfstats.analysis.metrics.lib.gen.jid_table import (
      _assemble_sum_val_parts_bounded,
  )

  df = pd.DataFrame(
      {
          "host": ["a", "b"],
          "time": [
              datetime(2024, 1, 1, tzinfo=timezone.utc),
              datetime(2024, 1, 1, tzinfo=timezone.utc),
          ],
          "sum_val": [1.0, 2.0],
      }
  )
  with patch("pandas.concat", side_effect=AssertionError("no concat")):
    out = _assemble_sum_val_parts_bounded([df], 2.0, 100)
  assert list(out["sum_val"]) == [2.0, 4.0]


def test_apply_large_job_sampling_uses_host_sample_budget(monkeypatch):
  """5000×48×60 estimate must sample even when COUNT(*) is below 1.5M threshold."""
  from hpcperfstats.analysis.metrics.lib.gen import jid_table as jt_mod

  inst = jt_mod.jid_table.__new__(jt_mod.jid_table)
  inst.jid = "jid-design-cap"
  inst.acct_host_list = [f"n{i}" for i in range(5000)]
  inst.start_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
  inst.end_time = datetime(2024, 1, 3, tzinfo=timezone.utc)  # 48h
  inst._base_filter = {
      "host__in": inst.acct_host_list,
      "time__gte": inst.start_time,
      "time__lte": inst.end_time,
  }
  from datetime import timedelta as _td
  sampled_times = [
      inst.start_time + _td(minutes=i * 15)
      for i in range(200)
  ]
  monkeypatch.setattr(
      jt_mod.cfg,
      "get_plot_aggregate_max_host_time_points",
      lambda: 1_000_000,
  )
  monkeypatch.setattr(jt_mod.cfg, "get_large_job_time_buckets", lambda: 2048)
  monkeypatch.setattr(
      jt_mod.cfg, "get_large_job_host_data_row_threshold", lambda: 1_500_000
  )
  monkeypatch.setattr(
      jt_mod,
      "_count_host_data_rows_for_window_cached",
      lambda *_a, **_k: 1000,
  )
  monkeypatch.setattr(
      jt_mod,
      "_strided_distinct_times_for_large_job",
      lambda *_a, **_k: sampled_times,
  )
  jt_mod.jid_table._apply_large_job_time_sampling_if_needed(inst)
  assert "time__in" in inst._base_filter
  assert len(inst._base_filter["time__in"]) == 200
  assert inst._large_job_plot_cache_token == "lb200"


def test_iter_host_time_query_chunks_nests_host_and_time():
  from hpcperfstats.analysis.metrics.lib.gen.jid_table import (
      _iter_host_time_query_chunks,
  )

  hosts = ["h{0}.example.com".format(i) for i in range(5)]
  tkw = {
      "time__gte": datetime(2024, 1, 1, tzinfo=timezone.utc),
      "time__lte": datetime(2024, 1, 1, 2, tzinfo=timezone.utc),
  }
  pairs = list(
      _iter_host_time_query_chunks(hosts, tkw, batch_size=2, slice_s=3600)
  )
  # 2 time hours × ceil(5/2)=3 host batches
  assert len(pairs) == 6
  assert pairs[0][0] == ["h0.example.com", "h1.example.com"]
  assert "time__gte" in pairs[0][1]


def test_run_with_host_time_timeout_retry_splits_hosts(monkeypatch):
  from hpcperfstats.analysis.metrics.lib.gen.jid_table import (
      _merge_list_results,
      _run_with_host_time_timeout_retry,
  )

  hosts = ["h{0}.example.com".format(i) for i in range(4)]
  seen = []

  def run(hosts_list, tf_cur):
    seen.append(list(hosts_list))
    if len(hosts_list) == 4:
      raise OperationalError("canceling statement due to statement timeout")
    return list(hosts_list)

  monkeypatch.setattr(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table.close_old_connections",
      lambda: None,
  )
  out = _run_with_host_time_timeout_retry(
      hosts, {"time__gte": 1}, run, _merge_list_results, empty=[]
  )
  assert seen[0] == hosts
  assert sorted(out) == sorted(hosts)


def test_run_with_host_time_timeout_retry_splits_time(monkeypatch):
  from hpcperfstats.analysis.metrics.lib.gen.jid_table import (
      _merge_list_results,
      _run_with_host_time_timeout_retry,
  )

  hosts = ["h0.example.com"]
  tkw = {
      "time__gte": datetime(2024, 1, 1, tzinfo=timezone.utc),
      "time__lte": datetime(2024, 1, 1, 1, tzinfo=timezone.utc),
  }
  calls = []

  def run(hosts_list, tf_cur):
    calls.append(dict(tf_cur or {}))
    if "time__lte" in (tf_cur or {}) and "time__gt" not in (tf_cur or {}):
      # full window or first half that still has large span may timeout once
      if len(calls) == 1:
        raise OperationalError("canceling statement due to statement timeout")
    return [1]

  monkeypatch.setattr(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table.close_old_connections",
      lambda: None,
  )
  out = _run_with_host_time_timeout_retry(
      hosts, tkw, run, _merge_list_results, empty=[]
  )
  assert out == [1, 1]
  assert len(calls) >= 3


def test_fold_sum_by_key_and_count_max_avg():
  from hpcperfstats.analysis.metrics.lib.gen.jid_table import (
      _fold_count_max_avg_rows,
      _fold_sum_by_key,
  )

  summed = _fold_sum_by_key(
      [
          {"event": "a", "delta_sum": 1},
          {"event": "a", "delta_sum": 2},
          {"event": "b", "delta_sum": 4},
      ],
      ("event",),
      "delta_sum",
  )
  by_ev = {r["event"]: r["delta_sum"] for r in summed}
  assert by_ev == {"a": 3.0, "b": 4.0}

  folded = _fold_count_max_avg_rows(
      [
          {
              "host": "h",
              "dev": "0",
              "event": "gpu_util",
              "cnt": 2,
              "vmax": 10.0,
              "vmean": 4.0,
          },
          {
              "host": "h",
              "dev": "0",
              "event": "gpu_util",
              "cnt": 2,
              "vmax": 20.0,
              "vmean": 8.0,
          },
      ],
      ("host", "dev", "event"),
  )
  assert len(folded) == 1
  assert folded[0]["cnt"] == 4
  assert folded[0]["vmax"] == 20.0
  assert folded[0]["vmean"] == 6.0


def test_full_host_data_rows_batched_uses_metrics_host_batch_and_time_slices(
    monkeypatch,
):
  """Regression: ~48-host jobs must not issue one full-window values_list."""
  from hpcperfstats.analysis.metrics.lib.gen import jid_table as jt_mod
  from hpcperfstats.analysis.metrics.lib.metrics import METRICS_HOST_QUERY_BATCH

  hosts = ["h{0}.example.com".format(i) for i in range(20)]
  inst = jt_mod.jid_table.__new__(jt_mod.jid_table)
  inst.acct_host_list = hosts
  inst._base_filter = {
      "host__in": hosts,
      "time__gte": datetime(2024, 1, 1, tzinfo=timezone.utc),
      "time__lte": datetime(2024, 1, 1, 2, tzinfo=timezone.utc),
  }
  seen_host_lens = []
  seen_tfs = []

  class FakeQs:
    def __init__(self, host_in, tkw):
      self._host_in = list(host_in)
      self._tkw = dict(tkw)

    def values_list(self, *cols):
      return self

    def order_by(self, *args):
      return self

    def __iter__(self):
      return iter([])

  def fake_filter(**kwargs):
    host_in = kwargs.get("host__in") or []
    seen_host_lens.append(len(host_in))
    seen_tfs.append(
        {k: v for k, v in kwargs.items() if k.startswith("time__")}
    )
    return FakeQs(host_in, kwargs)

  monkeypatch.setattr(
      jt_mod.cfg, "get_metrics_plot_aggregate_time_slice_s", lambda: 3600
  )
  monkeypatch.setattr(jt_mod, "close_old_connections", lambda: None)
  monkeypatch.setattr(jt_mod.host_data.objects, "filter", fake_filter)
  out = inst._full_host_data_rows_batched(
      ["host", "time", "type", "event", "value", "arc"]
  )
  assert out == []
  assert seen_host_lens
  assert max(seen_host_lens) <= METRICS_HOST_QUERY_BATCH
  # 2 hour window / 3600s → ≥2 time slices × ceil(20/16)=2 host batches
  assert len(seen_host_lens) >= 4


def test_jid_table_host_query_batch_matches_metrics_batch():
  """Drift gate: plot default host batch must stay aligned with metrics law."""
  from hpcperfstats.analysis.metrics.lib.gen.jid_table import (
      JID_TABLE_HOST_QUERY_BATCH,
  )
  from hpcperfstats.analysis.metrics.lib.metrics import METRICS_HOST_QUERY_BATCH

  assert JID_TABLE_HOST_QUERY_BATCH == 16
  assert JID_TABLE_HOST_QUERY_BATCH == METRICS_HOST_QUERY_BATCH
  assert JID_TABLE_HOST_QUERY_BATCH != 64


def test_aggregate_df_host_batch_dense_nfs_read_write_iops():
  """NFS Summary triples (read/write/iops) must start at batch 8, not 64/16."""
  from hpcperfstats.analysis.metrics.lib.gen.jid_table import (
      TYPE_DETAIL_HOST_QUERY_BATCH,
      _aggregate_df_host_batch,
  )

  assert (
      _aggregate_df_host_batch(
          "host_nfs",
          ["normal_read", "direct_read", "server_read"],
          reject_dcgm_blank=False,
      )
      == TYPE_DETAIL_HOST_QUERY_BATCH
  )
  assert (
      _aggregate_df_host_batch(
          "nfs",
          ["normal_write", "direct_write", "server_write"],
          reject_dcgm_blank=False,
      )
      == TYPE_DETAIL_HOST_QUERY_BATCH
  )
  assert (
      _aggregate_df_host_batch(
          "nfs",
          ["READ_ops", "read_ops", "WRITE_ops", "write_ops"],
          reject_dcgm_blank=False,
      )
      == TYPE_DETAIL_HOST_QUERY_BATCH
  )
  assert (
      _aggregate_df_host_batch(
          "host_cpu",
          ["user"],
          reject_dcgm_blank=False,
      )
      == JID_TABLE_HOST_QUERY_BATCH
  )
  assert (
      _aggregate_df_host_batch(
          "nvidia_gpu",
          ["gpu_util"],
          reject_dcgm_blank=True,
      )
      == TYPE_DETAIL_HOST_QUERY_BATCH
  )


def test_get_aggregate_df_nfs_first_host_chunk_is_eight(monkeypatch):
  """48-host NFS read SUM must not issue host__in of all 48 on first attempt."""
  from hpcperfstats.analysis.metrics.lib.gen.jid_table import (
      TYPE_DETAIL_HOST_QUERY_BATCH,
  )

  inst = jid_table.__new__(jid_table)
  inst.jid = "jid-nfs-batch8"
  inst._large_job_plot_cache_token = "full"
  hosts = [f"c101-{i:03d}.horizon.tacc.utexas.edu" for i in range(1, 49)]
  inst._base_filter = {
      "host__in": hosts,
      "time__gte": datetime(2026, 8, 5, 0, 33, 33, tzinfo=timezone.utc),
      "time__lte": datetime(2026, 8, 5, 1, 33, 33, tzinfo=timezone.utc),
  }
  # Align with production: one hour window uses one 3600s slice.
  monkeypatch.setattr(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table.cfg."
      "get_metrics_plot_aggregate_time_slice_s",
      lambda: 3600,
  )
  first_host_lens = []

  def capture_retry(host_chunk, build_qs, **kwargs):
    first_host_lens.append(len([str(h) for h in host_chunk if h]))
    return pd.DataFrame(
        {
            "host": [list(host_chunk)[0]],
            "time": [datetime(2026, 8, 5, 1, tzinfo=timezone.utc)],
            "sum_val": [1.0],
        }
    )

  with patch(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table."
      "_queryset_to_dataframe_with_host_chunk_retry",
      capture_retry,
  ), patch(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table.cached_orm",
      lambda _key, _timeout, query_fn: query_fn(),
  ), patch(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table."
      "host_data_sum_val_per_sample_queryset",
      lambda qs, _col: qs,
  ), patch(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table."
      "host_data_restore_time_column",
      lambda df: df,
  ), patch(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table.host_data",
  ):
    out = jid_table.get_aggregate_df(
        inst,
        "nfs",
        "arc",
        ["normal_read", "direct_read", "server_read"],
    )

  assert first_host_lens, "expected SQL host chunks"
  assert max(first_host_lens) <= TYPE_DETAIL_HOST_QUERY_BATCH
  assert first_host_lens[0] == TYPE_DETAIL_HOST_QUERY_BATCH
  assert not out.empty


def test_type_detail_get_host_time_df_uses_time_slices(monkeypatch):
  """TypeDetail host/time distinct must nest wall-clock slices, not full window."""
  st = datetime(2024, 6, 1, tzinfo=timezone.utc)
  et = datetime(2024, 6, 1, 2, tzinfo=timezone.utc)
  provider = TypeDetailDataProvider(
      jid="j-ht",
      type_name="mdc",
      start_time=st,
      end_time=et,
      host_list=["n1.example.com", "n2.example.com"],
  )
  seen = {"time_chunks": 0}

  def fake_fetch(host_list, build_qs, batch_size=None, **kwargs):
    tfc = kwargs.get("time_filter_chunks")
    seen["time_chunks"] = len(list(tfc or []))
    seen["batch_size"] = batch_size
    return pd.DataFrame(
        {
            "host": ["n1.example.com"],
            "time": [st],
        }
    )

  monkeypatch.setattr(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table.cfg."
      "get_metrics_plot_aggregate_time_slice_s",
      lambda: 3600,
  )
  with patch(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table."
      "_fetch_host_data_values_frames",
      fake_fetch,
  ), patch(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table.cached_orm",
      lambda _k, _ttl, fn: fn(),
  ):
    out = provider.get_host_time_df()

  assert seen["time_chunks"] >= 2
  from hpcperfstats.analysis.metrics.lib.gen.jid_table import (
      TYPE_DETAIL_HOST_QUERY_BATCH,
  )
  assert seen["batch_size"] == TYPE_DETAIL_HOST_QUERY_BATCH
  assert not out.empty


@pytest.mark.machine_unit_mock
def test_host_data_provider_get_aggregate_df_accepts_group_by_dev(monkeypatch):
  """HostDataProvider must accept group_by_dev and switch queryset grain."""
  import inspect

  from hpcperfstats.analysis.metrics.lib.gen import jid_table as jt_mod
  from hpcperfstats.analysis.metrics.lib.gen.jid_table import HostDataProvider

  sig = inspect.signature(HostDataProvider.get_aggregate_df)
  assert "group_by_dev" in sig.parameters
  assert sig.parameters["group_by_dev"].kind is inspect.Parameter.KEYWORD_ONLY

  calls = {"host": 0, "dev": 0}
  empty_host = pd.DataFrame(columns=["host", "time", "sum_val"])
  empty_dev = pd.DataFrame(columns=["host", "time", "dev", "sum_val"])

  def host_qs(*_a, **_k):
    calls["host"] += 1
    return MagicMock()

  def dev_qs(*_a, **_k):
    calls["dev"] += 1
    return MagicMock()

  monkeypatch.setattr(jt_mod, "host_data_sum_val_per_sample_queryset", host_qs)
  monkeypatch.setattr(
      jt_mod, "host_data_sum_val_per_sample_dev_queryset", dev_qs
  )
  monkeypatch.setattr(
      jt_mod,
      "queryset_to_dataframe",
      lambda qs: empty_dev.copy() if calls["dev"] else empty_host.copy(),
  )
  monkeypatch.setattr(jt_mod, "host_data_restore_time_column", lambda df: df)
  monkeypatch.setattr(
      jt_mod, "events_probe_names", lambda events, typ=None: list(events)
  )
  monkeypatch.setattr(jt_mod, "type_probe_names", lambda typ: [typ])
  monkeypatch.setattr(
      jt_mod, "_incr_summary_aggregate_count_if_active", lambda: None
  )

  provider = HostDataProvider.__new__(HostDataProvider)
  provider.jid = "h"
  provider.host_list = ["h.fqdn"]
  provider._base_filter = {"host": "h.fqdn"}
  provider._host_data_qs = lambda **_extra: MagicMock()

  host_df = provider.get_aggregate_df("nvidia_gpu", "arc", ["gpu_util"], 1.0)
  assert calls["host"] >= 1
  assert calls["dev"] == 0
  assert "dev" not in host_df.columns

  calls["host"] = 0
  calls["dev"] = 0
  dev_df = provider.get_aggregate_df(
      "nvidia_gpu", "arc", ["gpu_util"], 1.0, group_by_dev=True
  )
  assert calls["dev"] >= 1
  assert "dev" in list(dev_df.columns)

