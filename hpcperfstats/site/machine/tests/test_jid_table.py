"""Unit tests for analysis.gen.jid_table (_ensure_tz) and utils.queryset_to_dataframe.

"""
from datetime import datetime

import pandas as pd
import pytest

pytestmark = pytest.mark.django_db(databases=[])

from contextlib import nullcontext
from unittest.mock import patch

from django.db import OperationalError

from hpcperfstats.analysis.gen.jid_table import _coerce_jid_table_host_query_batch_size
from hpcperfstats.analysis.gen.jid_table import _build_acct_host_fqdns
from hpcperfstats.analysis.gen.jid_table import _listify_acct_hosts
from hpcperfstats.analysis.gen.jid_table import _coerce_jid_table_schema_dataframe
from hpcperfstats.analysis.gen.jid_table import _coerce_nonnegative_window_row_count
from hpcperfstats.analysis.gen.jid_table import _count_host_data_rows_for_window
from hpcperfstats.analysis.gen.jid_table import _count_host_data_rows_for_window_cached
from hpcperfstats.analysis.gen.jid_table import _distinct_times_in_window_batched
from hpcperfstats.analysis.gen.jid_table import _ensure_tz
from hpcperfstats.analysis.gen.jid_table import _iter_acct_host_batches
from hpcperfstats.analysis.gen.jid_table import _normalize_host_data_schema_label
from hpcperfstats.analysis.gen.jid_table import _normalize_job_accounting_host_list
from hpcperfstats.analysis.gen.jid_table import _ntile_bucket_max_timestamps
from hpcperfstats.analysis.gen.jid_table import JID_TABLE_HOST_QUERY_BATCH
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


def test_gpu_acct_window_for_job_data_keeps_existing_fqdns():
  """FQDN host_list values must not get a duplicate host suffix appended."""
  from datetime import timezone as dt_utc

  class _Job:
    host_list = ["n1.cluster.example", "n2.cluster.example"]
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
      "hpcperfstats.analysis.gen.jid_table.cfg.get_large_job_window_row_count_cache_ttl",
      lambda: 60,
  )
  monkeypatch.setattr("hpcperfstats.analysis.gen.jid_table.cache.get", lambda _k: [9_000_000])
  monkeypatch.setattr("hpcperfstats.analysis.gen.jid_table.cache.set", lambda *a, **k: None)
  monkeypatch.setattr(
      "hpcperfstats.analysis.gen.jid_table._count_host_data_rows_for_window",
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
      "hpcperfstats.analysis.gen.jid_table.cfg.get_large_job_window_row_count_cache_ttl",
      lambda: 120,
  )
  monkeypatch.setattr("hpcperfstats.analysis.gen.jid_table.cache.get", lambda _k: [1, 2, 3])
  monkeypatch.setattr(
      "hpcperfstats.analysis.gen.jid_table.cache.set",
      lambda key, val, timeout=None: set_calls.append((key, val, timeout)),
  )
  monkeypatch.setattr(
      "hpcperfstats.analysis.gen.jid_table._count_host_data_rows_for_window",
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
      "hpcperfstats.analysis.gen.jid_table.cfg.get_large_job_window_row_count_cache_ttl",
      _bad_ttl,
  )
  monkeypatch.setattr(
      "hpcperfstats.analysis.gen.jid_table._count_host_data_rows_for_window",
      lambda *_a, **_k: 917,
  )
  n = _count_host_data_rows_for_window_cached("j695088", st, et, ["h.x"])
  assert n == 917


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
      "hpcperfstats.analysis.gen.jid_table.cfg.get_host_name_ext",
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
      "hpcperfstats.analysis.gen.jid_table._pg_relax_statement_timeout_for_large_job_time_sql",
      nullcontext,
  )
  monkeypatch.setattr("hpcperfstats.analysis.gen.jid_table.host_data.objects", FakeManager())

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

  monkeypatch.setattr("hpcperfstats.analysis.gen.jid_table.host_data.objects", FakeManager())

  out = _count_host_data_rows_for_window(1, 2, hosts)
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

  monkeypatch.setattr("hpcperfstats.analysis.gen.jid_table.host_data.objects", FakeManager())

  out = _count_host_data_rows_for_window(1, 2, hosts)
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

  monkeypatch.setattr("hpcperfstats.analysis.gen.jid_table.host_data.objects", FakeManager())
  closed = []

  monkeypatch.setattr(
      "hpcperfstats.analysis.gen.jid_table.close_old_connections",
      lambda: closed.append(1),
  )
  out = _count_host_data_rows_for_window(1, 2, ["h1.example.com"])
  assert out == 7
  assert calls["n"] == 2
  assert len(closed) == 2
