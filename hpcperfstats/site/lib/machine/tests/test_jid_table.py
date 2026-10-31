"""Unit tests for analysis.gen.jid_table (_ensure_tz) and utils.queryset_to_dataframe."""

from datetime import UTC, datetime

import pytest

pytestmark = pytest.mark.django_db(databases=[])

from contextlib import nullcontext
from unittest.mock import patch

from django.db import OperationalError, connections

from hpcperfstats.analysis.metrics.lib.gen.jid_table import (
  JID_TABLE_HOST_QUERY_BATCH,
  _build_acct_host_fqdns,
  _coerce_jid_table_host_query_batch_size,
  _coerce_nonnegative_window_row_count,
  _count_host_data_rows_for_window,
  _count_host_data_rows_for_window_cached,
  _distinct_times_in_window_batched,
  _ensure_tz,
  _iter_acct_host_batches,
  _listify_acct_hosts,
  _normalize_host_cell_for_host_data,
  _normalize_host_data_schema_label,
  _normalize_job_accounting_host_list,
  _normalize_window_bound_datetime,
  _ntile_bucket_max_timestamps,
  _strided_distinct_times_date_bin_postgresql,
  _strided_distinct_times_for_large_job,
  _unpack_cached_job_window_row,
  gpu_acct_window_for_job_data,
  jid_table,
)
from hpcperfstats.analysis.metrics.lib.gen.utils import (
  iter_queryset_values_dicts,
)
from hpcperfstats.site.lib.machine.models import job_data


def test_normalize_job_accounting_host_list_accepts_list_tuple():
  """Accounting host_list is coerced from list/tuple."""
  assert _normalize_job_accounting_host_list(["a", "b"]) == ["a", "b"]
  assert _normalize_job_accounting_host_list(("x",)) == ["x"]


def test_normalize_job_accounting_host_list_flattens_nested_sequences():
  assert _normalize_job_accounting_host_list([["n1"], "n2"]) == ["n1", "n2"]


def test_normalize_host_cell_for_host_data_unwraps_list_wrapped_scalar():
  assert (
    _normalize_host_cell_for_host_data(["host.example.com"])
    == "host.example.com"
  )
  assert _normalize_host_cell_for_host_data(None) is None
  assert _normalize_host_cell_for_host_data({"a": 1}) is None


def test_normalize_job_accounting_host_list_rejects_non_sequence():
  """A lone datetime (corrupt cache/ORM) must not be iterated."""
  dt = datetime(2024, 1, 2, 3, 4, 5)
  assert _normalize_job_accounting_host_list(dt) == []
  assert _normalize_job_accounting_host_list(None) == []


def test_gpu_acct_window_for_job_data_builds_fqdns():
  """gpu_acct_window_for_job_data mirrors jid_table FQDN rules without full init."""

  class _Job:
    host_list = ["n1", "n2"]
    start_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    end_time = datetime(2026, 1, 2, 0, 0, 0, tzinfo=UTC)

  with patch(
    "hpcperfstats.analysis.metrics.lib.gen.jid_table.cfg.get_host_name_ext",
    return_value="cluster.example",
  ):
    _st, _et, acct = gpu_acct_window_for_job_data(_Job())
  assert acct == ["n1.cluster.example", "n2.cluster.example"]


def test_gpu_acct_window_for_job_data_keeps_existing_fqdns():
  """FQDN host_list values must not get a duplicate host suffix appended."""

  class _Job:
    host_list = ["n1.cluster.example", "n2.cluster.example"]
    start_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    end_time = datetime(2026, 1, 2, 0, 0, 0, tzinfo=UTC)

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


def test_count_host_data_rows_for_window_cached_accepts_list_wrapped_scalar(
  monkeypatch,
):
  """Large-job row-count cache hit must accept JSON-style single-element list values."""

  st = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
  et = datetime(2026, 1, 1, 13, 0, 0, tzinfo=UTC)
  calls = {"count": 0}

  def _count_stub(*_a, **_k):
    calls["count"] += 1
    raise AssertionError("ORM count must not run when cache parses")

  monkeypatch.setattr(
    "hpcperfstats.analysis.metrics.lib.gen.jid_table.cfg.get_large_job_window_row_count_cache_ttl",
    lambda: 60,
  )
  monkeypatch.setattr(
    "hpcperfstats.analysis.metrics.lib.gen.jid_table.cache.get",
    lambda _k: [9_000_000],
  )
  monkeypatch.setattr(
    "hpcperfstats.analysis.metrics.lib.gen.jid_table.cache.set",
    lambda *a, **k: None,
  )
  monkeypatch.setattr(
    "hpcperfstats.analysis.metrics.lib.gen.jid_table._count_host_data_rows_for_window",
    _count_stub,
  )
  n = _count_host_data_rows_for_window_cached(
    "j656931", st, et, ["n1.cluster.example"]
  )
  assert n == 9_000_000
  assert calls["count"] == 0


def test_count_host_data_rows_for_window_cached_multi_element_list_recomputes(
  monkeypatch,
):
  """Malformed cache list must fall back to live COUNT and store a plain int."""

  st = datetime(2026, 2, 1, 12, 0, 0, tzinfo=UTC)
  et = datetime(2026, 2, 1, 13, 0, 0, tzinfo=UTC)
  set_calls = []

  monkeypatch.setattr(
    "hpcperfstats.analysis.metrics.lib.gen.jid_table.cfg.get_large_job_window_row_count_cache_ttl",
    lambda: 120,
  )
  monkeypatch.setattr(
    "hpcperfstats.analysis.metrics.lib.gen.jid_table.cache.get",
    lambda _k: [1, 2, 3],
  )
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


def test_count_host_data_rows_for_window_cached_handles_invalid_int_parse(
  monkeypatch,
):
  """TTL getter failures fall back to default TTL and still run uncached COUNT (*)."""

  st = datetime(2026, 2, 2, 12, 0, 0, tzinfo=UTC)
  et = datetime(2026, 2, 2, 13, 0, 0, tzinfo=UTC)

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


def test_count_host_data_rows_for_window_cached_handles_non_numeric_count(
  monkeypatch,
):

  st = datetime(2026, 2, 2, 12, 0, 0, tzinfo=UTC)
  et = datetime(2026, 2, 2, 13, 0, 0, tzinfo=UTC)
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


def test_count_host_data_rows_for_window_rejects_non_datetime_bounds(
  monkeypatch,
):
  class _FailingObjects:
    def filter(self, **kwargs):
      raise AssertionError("ORM filter should not run for non-datetime bounds")

  class _FailingHostData:
    objects = _FailingObjects()

  monkeypatch.setattr(
    "hpcperfstats.analysis.metrics.lib.gen.jid_table.host_data",
    _FailingHostData(),
  )
  n = _count_host_data_rows_for_window(
    start=["bad"], end=["bad"], acct_hosts=["h.x"]
  )
  assert n == 0


def test_count_host_data_rows_for_window_rejects_deque_list_bounds(monkeypatch):
  from collections import deque

  class _FailingObjects:
    def filter(self, **kwargs):
      raise AssertionError(
        "ORM filter should not run for list-like bound wrappers"
      )

  class _FailingHostData:
    objects = _FailingObjects()

  monkeypatch.setattr(
    "hpcperfstats.analysis.metrics.lib.gen.jid_table.host_data",
    _FailingHostData(),
  )
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

  utc_aware = datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC)
  result = _ensure_tz(utc_aware)
  assert result is not None
  assert result.tzinfo is not None
  assert result.year == 2024 and result.month == 6 and result.day == 15


def test_jid_table_host_data_time_filter_kwargs_full_window():
  """Unsampled jobs use time__gte/time__lte in ORM kwargs."""
  inst = jid_table.__new__(jid_table)
  inst._base_filter = {
    "time__gte": 1,
    "time__lte": 2,
    "host__in": ["a.example.com"],
  }
  assert inst._host_data_time_filter_kwargs() == {
    "time__gte": 1,
    "time__lte": 2,
  }


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
      yield from self._rows

  qs = FakeQs()
  got = list(iter_queryset_values_dicts(qs, "a", "b", chunk_size=1))
  assert got == [{"a": 1, "b": 2}, {"a": 3, "b": 4}]


def test_strided_distinct_times_date_bin_postgresql_skips_distinct_when_sql_nonempty(
  monkeypatch,
):
  """Grouped-max SQL path must not call the batched DISTINCT time enumerator."""
  from datetime import timedelta

  monkeypatch.setattr(connections["default"], "vendor", "postgresql")
  distinct_calls = []

  def fake_sql(*_a, **_k):
    st = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
    return [st, st + timedelta(seconds=30)]

  monkeypatch.setattr(
    "hpcperfstats.analysis.metrics.lib.gen.jid_table._strided_distinct_times_date_bin_via_grouped_max_sql",
    fake_sql,
  )
  monkeypatch.setattr(
    "hpcperfstats.analysis.metrics.lib.gen.jid_table._distinct_times_in_window_batched",
    lambda *a, **k: distinct_calls.append(1),
  )
  st = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
  en = st + timedelta(minutes=5)
  out = _strided_distinct_times_date_bin_postgresql(
    st, en, ["n1.example.com"], 64
  )
  assert out == [st, st + timedelta(seconds=30)]
  assert distinct_calls == []


def test_strided_distinct_times_date_bin_postgresql_falls_back_when_sql_empty(
  monkeypatch,
):
  """When grouped-max SQL returns no rows, fall back to DISTINCT + Python bins."""
  from datetime import timedelta

  monkeypatch.setattr(connections["default"], "vendor", "postgresql")
  distinct_calls = []
  st = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)

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
  out = _strided_distinct_times_date_bin_postgresql(
    st, en, ["n1.example.com"], 8
  )
  assert distinct_calls == [1]
  assert out
  assert out[0] >= st
  assert out[-1] <= en


def test_strided_distinct_times_for_large_job_falls_back_to_fixed_window_points(
  monkeypatch,
):
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
    start, end, ["n1.example.com"], 64
  )
  assert sampled[0] == start
  assert sampled[-1] == end
  assert len(sampled) == 3


def test_strided_distinct_times_for_large_job_degenerate_window_returns_start(
  monkeypatch,
):
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
    start, start, ["n1.example.com"], 64
  )
  assert sampled == [start]


def test_ntile_bucket_max_timestamps_ten_into_three_buckets():
  """NTILE(3) over 10 ordered rows uses bucket sizes 4,3,3; maxima at last of each."""
  ts = list(range(10))
  assert _ntile_bucket_max_timestamps(ts, 3) == [3, 6, 9]


def test_coerce_jid_table_host_query_batch_size_rejects_non_numeric_strings():
  """Hostnames mistaken for batch sizes must fall back (large-job row count must not raise)."""
  assert (
    _coerce_jid_table_host_query_batch_size("c641-092.vista.tacc.utexas.edu")
    == JID_TABLE_HOST_QUERY_BATCH
  )
  assert (
    _coerce_jid_table_host_query_batch_size(None) == JID_TABLE_HOST_QUERY_BATCH
  )
  assert _coerce_jid_table_host_query_batch_size(128) == 128
  assert _coerce_jid_table_host_query_batch_size("32") == 32
  assert (
    _coerce_jid_table_host_query_batch_size(0) == JID_TABLE_HOST_QUERY_BATCH
  )


def test_listify_acct_hosts_wraps_fqdn_string_without_splitting_chars():
  """A lone FQDN string must not become one character per pseudo-host."""
  fqdn = "c608-081.vista.tacc.utexas.edu"
  assert _listify_acct_hosts(fqdn) == [fqdn]
  assert _listify_acct_hosts("  " + fqdn + "  ") == [fqdn]


def test_listify_acct_hosts_comma_separated_short_names():
  assert _listify_acct_hosts("a,b, c") == ["a", "b", "c"]


def test_listify_acct_hosts_flattens_nested_sequences_and_dedupes():
  nested = [["n1.example"], ("n2.example", ["n3.example", "n1.example"])]
  assert _listify_acct_hosts(nested) == [
    "n1.example",
    "n2.example",
    "n3.example",
  ]


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
    _iter_acct_host_batches(hosts, "c641-092.vista.tacc.utexas.edu")
  )
  assert len(chunks) == 2
  assert len(chunks[0]) == JID_TABLE_HOST_QUERY_BATCH
  assert len(chunks[1]) == 5


def test_count_host_data_rows_for_window_flattens_nested_acct_hosts(
  monkeypatch,
):
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

  monkeypatch.setattr(
    "hpcperfstats.analysis.metrics.lib.gen.jid_table.host_data",
    _FakeHostData(),
  )
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
  monkeypatch.setattr(
    "hpcperfstats.analysis.metrics.lib.gen.jid_table.host_data.objects",
    FakeManager(),
  )

  n = JID_TABLE_HOST_QUERY_BATCH + 5
  hosts = [f"h{i}.x" for i in range(n)]
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

  monkeypatch.setattr(
    "hpcperfstats.analysis.metrics.lib.gen.jid_table.host_data.objects",
    FakeManager(),
  )

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

  monkeypatch.setattr(
    "hpcperfstats.analysis.metrics.lib.gen.jid_table.host_data.objects",
    FakeManager(),
  )

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

  monkeypatch.setattr(
    "hpcperfstats.analysis.metrics.lib.gen.jid_table.host_data.objects",
    FakeManager(),
  )
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
  from hpcperfstats.analysis.metrics.lib.gen.jid_table import (
    _is_statement_timeout_error,
  )

  assert _is_statement_timeout_error(
    OperationalError("canceling statement due to statement timeout")
  )
  assert not _is_statement_timeout_error(OperationalError("connection refused"))


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


def test_resolve_plot_aggregate_time_bucket_count_design_capacity_5000():
  """Design 5000x48x60: budget/hosts caps buckets (not full 2048 times)."""
  from hpcperfstats.analysis.metrics.lib.gen.jid_table import (
    _resolve_plot_aggregate_time_bucket_count,
  )

  with (
    patch(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table.cfg.get_large_job_time_buckets",
      lambda: 2048,
    ),
    patch(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table.cfg."
      "get_plot_aggregate_max_host_time_points",
      lambda: 1_000_000,
    ),
  ):
    # 5000 hosts -> floor(1e6/5000)=200 (14.4M host-sample design capacity).
    assert _resolve_plot_aggregate_time_bucket_count(5000) == 200


def test_iter_aggregate_time_filter_chunks_wall_and_time_in():
  from hpcperfstats.analysis.metrics.lib.gen.jid_table import (
    _iter_aggregate_time_filter_chunks,
  )

  start = datetime(2024, 1, 1, tzinfo=UTC)
  end = datetime(2024, 1, 1, 3, tzinfo=UTC)
  chunks = list(
    _iter_aggregate_time_filter_chunks(
      {"time__gte": start, "time__lte": end},
      3600,
    )
  )
  assert len(chunks) == 3
  times = [datetime(2024, 1, 1, i, tzinfo=UTC) for i in range(5)]
  tin = list(_iter_aggregate_time_filter_chunks({"time__in": times}, 120))
  # slice_s=120 -> 2 timestamps per chunk
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


def test_apply_large_job_sampling_uses_host_sample_budget(monkeypatch):
  """5000x48x60 estimate must sample even when COUNT(*) is below 1.5M threshold."""
  from hpcperfstats.analysis.metrics.lib.gen import jid_table as jt_mod

  inst = jt_mod.jid_table.__new__(jt_mod.jid_table)
  inst.jid = "jid-design-cap"
  inst.acct_host_list = [f"n{i}" for i in range(5000)]
  inst.start_time = datetime(2024, 1, 1, tzinfo=UTC)
  inst.end_time = datetime(2024, 1, 3, tzinfo=UTC)  # 48h
  inst._base_filter = {
    "host__in": inst.acct_host_list,
    "time__gte": inst.start_time,
    "time__lte": inst.end_time,
  }
  from datetime import timedelta as _td

  sampled_times = [inst.start_time + _td(minutes=i * 15) for i in range(200)]
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

  hosts = [f"h{i}.example.com" for i in range(5)]
  tkw = {
    "time__gte": datetime(2024, 1, 1, tzinfo=UTC),
    "time__lte": datetime(2024, 1, 1, 2, tzinfo=UTC),
  }
  pairs = list(
    _iter_host_time_query_chunks(hosts, tkw, batch_size=2, slice_s=3600)
  )
  # 2 time hours x ceil(5/2)=3 host batches
  assert len(pairs) == 6
  assert pairs[0][0] == ["h0.example.com", "h1.example.com"]
  assert "time__gte" in pairs[0][1]


def test_run_with_host_time_timeout_retry_splits_hosts(monkeypatch):
  from hpcperfstats.analysis.metrics.lib.gen.jid_table import (
    _merge_list_results,
    _run_with_host_time_timeout_retry,
  )

  hosts = [f"h{i}.example.com" for i in range(4)]
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
    "time__gte": datetime(2024, 1, 1, tzinfo=UTC),
    "time__lte": datetime(2024, 1, 1, 1, tzinfo=UTC),
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


def test_jid_table_host_query_batch_matches_metrics_batch():
  """Drift gate: plot default host batch must stay aligned with metrics law."""
  from hpcperfstats.analysis.metrics.lib.gen.jid_table import (
    JID_TABLE_HOST_QUERY_BATCH,
  )
  from hpcperfstats.analysis.metrics.lib.metrics import (
    METRICS_HOST_QUERY_BATCH,
  )

  assert JID_TABLE_HOST_QUERY_BATCH == 16
  assert JID_TABLE_HOST_QUERY_BATCH == METRICS_HOST_QUERY_BATCH
  assert JID_TABLE_HOST_QUERY_BATCH != 64
