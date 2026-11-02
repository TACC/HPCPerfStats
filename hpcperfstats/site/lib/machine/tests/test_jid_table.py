"""Unit tests for analysis.gen.jid_table (_ensure_tz) and utils.queryset_to_dataframe."""

from datetime import UTC, datetime, timedelta

import pytest

pytestmark = pytest.mark.django_db(databases=[])

from unittest.mock import patch

from django.db import OperationalError

from hpcperfstats.analysis.metrics.lib.gen.jid_table import (
  JID_TABLE_HOST_QUERY_BATCH,
  _build_acct_host_fqdns,
  _coerce_jid_table_host_query_batch_size,
  _coerce_nonnegative_window_row_count,
  _count_host_data_rows_for_window,
  _count_host_data_rows_for_window_cached,
  _ensure_tz,
  _iter_acct_host_batches,
  _listify_acct_hosts,
  _normalize_host_cell_for_host_data,
  _normalize_host_data_schema_label,
  _normalize_job_accounting_host_list,
  _normalize_window_bound_datetime,
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
  def _fail(*_a, **_k):
    raise AssertionError("estimate should not run for non-datetime bounds")

  monkeypatch.setattr(
    "hpcperfstats.analysis.metrics.lib.metrics_host_data_sql."
    "rows_per_sample_estimate",
    _fail,
  )
  n = _count_host_data_rows_for_window(
    start=["bad"], end=["bad"], acct_hosts=["h.x"]
  )
  assert n == 0


def test_count_host_data_rows_for_window_rejects_deque_list_bounds(monkeypatch):
  from collections import deque

  def _fail(*_a, **_k):
    raise AssertionError("estimate should not run for list-like bounds")

  monkeypatch.setattr(
    "hpcperfstats.analysis.metrics.lib.metrics_host_data_sql."
    "rows_per_sample_estimate",
    _fail,
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
  """Large-job thinning replaces the window with time slots + width."""
  inst = jid_table.__new__(jid_table)
  slots = [10, 20, 30]
  inst._base_filter = {
    "time__slots": slots,
    "time__slot_s": 60,
    "host__in": ["a.example.com"],
  }
  assert inst._host_data_time_filter_kwargs() == {
    "time__slots": slots,
    "time__slot_s": 60,
  }


def test_time_slot_filters_chunk_and_bisect_keep_slot_width():
  """Slot lists split like time__in lists and every piece keeps the width."""
  from hpcperfstats.analysis.metrics.lib.gen.jid_table import (
    _iter_aggregate_time_filter_chunks,
    _split_time_filter_for_timeout,
  )

  tkw = {"time__slots": list(range(130)), "time__slot_s": 60}
  chunks = list(_iter_aggregate_time_filter_chunks(tkw, 3600))
  assert [len(c["time__slots"]) for c in chunks] == [60, 60, 10]
  assert all(c["time__slot_s"] == 60 for c in chunks)
  left, right = _split_time_filter_for_timeout(chunks[2])
  assert left == {"time__slots": [120, 121, 122, 123, 124], "time__slot_s": 60}
  assert right["time__slot_s"] == 60 and len(right["time__slots"]) == 5


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
  """5000x48x60 must thin even when the row estimate is small: 200 slots
  (budget / hosts), one per window / 200, each LARGE_JOB_SLOT_S wide, and
  no query picks the times."""
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
  monkeypatch.setattr(
    jt_mod.cfg, "get_plot_aggregate_max_host_time_points", lambda: 1_000_000
  )
  monkeypatch.setattr(jt_mod.cfg, "get_large_job_time_buckets", lambda: 2048)
  monkeypatch.setattr(
    jt_mod.cfg, "get_large_job_host_data_row_threshold", lambda: 1_500_000
  )
  monkeypatch.setattr(
    jt_mod, "_count_host_data_rows_for_window_cached", lambda *_a, **_k: 1000
  )
  jt_mod.jid_table._apply_large_job_time_sampling_if_needed(inst)
  slots = inst._base_filter["time__slots"]
  assert len(slots) == 200
  assert inst._base_filter["time__slot_s"] == jt_mod.LARGE_JOB_SLOT_S == 60
  assert slots[0] == inst.start_time
  assert slots[1] - slots[0] == timedelta(minutes=14, seconds=24)
  assert "time__in" not in inst._base_filter
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


def _patch_rows_per_sample(monkeypatch, fn):
  import hpcperfstats.analysis.metrics.lib.metrics_host_data_sql as sql_mod

  monkeypatch.setattr(sql_mod, "rows_per_sample_estimate", fn)
  monkeypatch.setattr(
    "hpcperfstats.analysis.metrics.lib.gen.jid_table.close_old_connections",
    lambda: None,
  )


def test_count_host_data_rows_for_window_estimates_without_count(monkeypatch):
  """hs04: window COUNT(*) timed out; estimate = per-sample x hosts x minutes."""
  seen = []

  def _per_sample(hosts, start, end):
    seen.append(list(hosts))
    return 10

  _patch_rows_per_sample(monkeypatch, _per_sample)

  class _NoCount:
    def filter(self, **_k):
      raise AssertionError("window COUNT(*) must not run")

  monkeypatch.setattr(
    "hpcperfstats.site.lib.machine.models.host_data.objects",
    _NoCount(),
  )
  n = JID_TABLE_HOST_QUERY_BATCH + 5
  hosts = [[f"n{i}.example"] for i in range(n)]
  st = datetime(2026, 5, 1, 0, 0, 0)
  et = datetime(2026, 5, 1, 1, 0, 0)
  out = _count_host_data_rows_for_window(st, et, hosts)
  assert out == 10 * n * 60
  # One probe on the first (flattened) host only: hs04 batch probes cost
  # ~7.5 s of unvacuumed heap reads per 8 hosts.
  assert seen == [["n0.example"]]


def test_count_host_data_rows_retries_after_lost_sync(monkeypatch):
  """One retry with close_old_connections after psycopg desynchronization."""
  calls = {"n": 0}

  def _per_sample(_hosts, _start, _end):
    calls["n"] += 1
    if calls["n"] == 1:
      raise OperationalError(
        'lost synchronization with server: got message type "1", length 9'
      )
    return 7

  _patch_rows_per_sample(monkeypatch, _per_sample)
  st = datetime(2026, 5, 1, 0, 0, 0)
  et = datetime(2026, 5, 1, 0, 1, 0)
  assert _count_host_data_rows_for_window(st, et, ["h1.example.com"]) == 7
  assert calls["n"] == 2


def test_jid_table_host_list_and_schema_use_index_probes(monkeypatch):
  """hs04: DISTINCT host / DISTINCT type,event over the window timed out."""
  import hpcperfstats.analysis.metrics.lib.metrics_host_data_sql as sql_mod

  probe_calls = []
  schema_calls = []

  def _bounds(hosts, start, end, jid=None, exclusive_hosts=()):
    return dict.fromkeys(hosts, (start, end))

  def _owning(hosts, start, end, jid, exclusive_hosts=()):
    probe_calls.append((tuple(hosts), jid))
    return {h for h in hosts if h != "idle.x"}

  def _schema(host, first, last, slice_s, jid=None, exclusive_hosts=()):
    schema_calls.append((host, slice_s, jid))
    return [("cpu", "user", "cs")] if jid else [("host_mem", "used", "kB")]

  monkeypatch.setattr(sql_mod, "first_last_scoped_times", _bounds)
  monkeypatch.setattr(sql_mod, "hosts_owning_mid_sample", _owning)
  monkeypatch.setattr(sql_mod, "schema_rows_near_bounds", _schema)
  mod = "hpcperfstats.analysis.metrics.lib.gen.jid_table"
  st = datetime(2026, 5, 1, tzinfo=UTC)
  et = datetime(2026, 5, 1, 2, tzinfo=UTC)
  window = iter([(["a.x", "idle.x"], st, et)])

  def _cached(_key, _ttl, fn):
    return next(window, None) or fn()

  monkeypatch.setattr(f"{mod}.cached_orm", _cached)
  monkeypatch.setattr(f"{mod}._build_acct_host_fqdns", list)
  monkeypatch.setattr(f"{mod}.job_is_shared_node", lambda _j: True)
  monkeypatch.setattr(
    f"{mod}.jid_table._apply_large_job_time_sampling_if_needed",
    lambda self: None,
  )
  jt = jid_table("77")
  assert jt.host_list == ["a.x"]
  assert probe_calls[0] == (("a.x", "idle.x"), "77")
  assert jt.schema == {"cpu": ["user"], "host_mem": ["used"]}
  assert [c[2] for c in schema_calls] == ["77", None]


def test_jid_table_exclusive_hosts_reach_window_probes(monkeypatch):
  """Idle-but-allocated hosts of an exclusive job count as owned: the
  exclusive set (D3) is computed once per window and passed to every scoped
  probe; the host-list cache key carries its fingerprint."""
  import hpcperfstats.analysis.metrics.lib.metrics_host_data_sql as sql_mod

  seen = {"owning": [], "bounds": [], "schema": [], "keys": []}

  def _bounds(hosts, start, end, jid=None, exclusive_hosts=()):
    seen["bounds"].append((jid, tuple(exclusive_hosts)))
    return dict.fromkeys(hosts, (start, end))

  def _owning(hosts, start, end, jid, exclusive_hosts=()):
    seen["owning"].append((tuple(hosts), tuple(exclusive_hosts)))
    return set(hosts)

  def _schema(host, first, last, slice_s, jid=None, exclusive_hosts=()):
    seen["schema"].append((jid, tuple(exclusive_hosts)))
    return [("cpu", "user", "cs")]

  monkeypatch.setattr(sql_mod, "first_last_scoped_times", _bounds)
  monkeypatch.setattr(sql_mod, "hosts_owning_mid_sample", _owning)
  monkeypatch.setattr(sql_mod, "schema_rows_near_bounds", _schema)
  mod = "hpcperfstats.analysis.metrics.lib.gen.jid_table"
  st = datetime(2026, 5, 1, tzinfo=UTC)
  et = datetime(2026, 5, 1, 2, tzinfo=UTC)
  window = iter([(["a.x", "busy.x"], st, et)])

  def _cached(key, _ttl, fn):
    seen["keys"].append(key)
    return next(window, None) or fn()

  calls = []

  def _exclusive(
    jid, hosts, start, end, *, shared, normalize=None, to_host_data=None
  ):
    assert to_host_data is not None
    calls.append((jid, list(hosts), start, end, shared))
    return [h for h in hosts if h != "busy.x"]

  monkeypatch.setattr(f"{mod}.cached_orm", _cached)
  monkeypatch.setattr(f"{mod}._build_acct_host_fqdns", list)
  monkeypatch.setattr(f"{mod}._normalize_job_accounting_host_list", list)
  monkeypatch.setattr(f"{mod}.job_is_shared_node", lambda _j: False)
  monkeypatch.setattr(f"{mod}.job_exclusive_hosts", _exclusive)
  monkeypatch.setattr(
    f"{mod}.jid_table._apply_large_job_time_sampling_if_needed",
    lambda self: None,
  )
  jt = jid_table("71526")
  assert calls == [("71526", ["a.x", "busy.x"], st, et, False)]
  assert jt._exclusive_hosts == ["a.x"]
  assert seen["owning"] == [(("a.x", "busy.x"), ("a.x",))]
  assert seen["bounds"] == [("71526", ("a.x",))]
  assert seen["schema"] == [("71526", ("a.x",))]
  from hpcperfstats.analysis.metrics.lib.job_host_data_scope import (
    exclusive_hosts_cache_token,
  )

  token = exclusive_hosts_cache_token(["a.x"])
  assert token != exclusive_hosts_cache_token([])
  assert any(token in str(k) for k in seen["keys"])


def test_apply_large_job_sampling_skips_when_samples_fit_buckets(monkeypatch):
  """hs04: 24 hosts x 9.3 h crossed the row threshold, the strided query
  timed out and the fallback left a 3-point axis. With <= n_buckets samples
  thinning keeps every sample anyway, so it must not run at all."""
  from hpcperfstats.analysis.metrics.lib.gen import jid_table as jt_mod

  inst = jt_mod.jid_table.__new__(jt_mod.jid_table)
  inst.jid = "69000"
  inst.acct_host_list = [f"n{i}" for i in range(24)]
  inst.start_time = datetime(2026, 10, 25, 2, 42, tzinfo=UTC)
  inst.end_time = datetime(2026, 10, 25, 12, 2, tzinfo=UTC)
  inst._base_filter = {
    "host__in": inst.acct_host_list,
    "time__gte": inst.start_time,
    "time__lte": inst.end_time,
  }
  monkeypatch.setattr(
    jt_mod.cfg, "get_plot_aggregate_max_host_time_points", lambda: 1_000_000
  )
  monkeypatch.setattr(jt_mod.cfg, "get_large_job_time_buckets", lambda: 2048)
  monkeypatch.setattr(
    jt_mod.cfg, "get_large_job_host_data_row_threshold", lambda: 1_500_000
  )

  def _no_call(*_a, **_k):
    raise AssertionError("row estimate must not run")

  monkeypatch.setattr(
    jt_mod, "_count_host_data_rows_for_window_cached", _no_call
  )
  jt_mod.jid_table._apply_large_job_time_sampling_if_needed(inst)
  assert "time__in" not in inst._base_filter
  assert inst._large_job_plot_cache_token == "full"


def test_scoped_probe_sql_cannot_use_jid_or_dev_indexes():
  """hs04: plain jid/dev predicates let the planner pick the jid / j: indexes
  (whole 64-host job, then host filter + sort) and the probe timed out."""
  from hpcperfstats.analysis.metrics.lib.metrics_host_data_sql import _scope_sql

  sql, params = _scope_sql("69000")
  assert sql == (
    "(COALESCE(d.jid, '') = %s OR COALESCE(d.dev, '') = ('j:' || %s))"
  )
  assert params == ["69000", "69000"]
  assert _scope_sql("") == ("TRUE", [])
  ex_sql, ex_params = _scope_sql("69000", ["h1.x"])
  assert ex_sql.startswith(sql[:-1] + " OR (d.host = ANY(%s::text[])")
  assert ex_params == ["69000", "69000", ["h1.x"]]
  # No jid: host-wide read, exclusive hosts are irrelevant.
  assert _scope_sql("", ["h1.x"]) == ("TRUE", [])


def test_first_last_scoped_times_bisects_instead_of_walking_untagged_tail(
  monkeypatch,
):
  """hs04 job 71526: c117-002 carried no job rows for the last 1 h 48 min of
  the window (681k untagged rows), so the scoped ``ORDER BY time DESC LIMIT
  1`` walk timed out. Bounds come from per-sample bisection: the scope only
  ever filters one sample (``d.time = s.t``), and the step count is bounded
  by the window length in microseconds, not by rows."""
  import re

  from django.db import connection

  import hpcperfstats.analysis.metrics.lib.metrics_host_data_sql as sql_mod

  st = datetime(2026, 10, 27, 15, 13, 2, tzinfo=UTC)
  et = datetime(2026, 10, 27, 20, 13, 12, tzinfo=UTC)
  samples = [st + timedelta(seconds=54 + 60 * i) for i in range(300)]
  first_own = datetime(2026, 10, 27, 15, 13, 56, tzinfo=UTC)
  # Job-end payload 1 us after a periodic sample: bisection must not stop
  # at the earlier neighbour.
  last_own = datetime(2026, 10, 27, 18, 24, 56, 1, tzinfo=UTC)
  samples = sorted([*samples, last_own])
  owned = {s for s in samples if first_own <= s <= last_own}
  seen = []

  class _Cur:
    def __enter__(self):
      return self

    def __exit__(self, *_a):
      return False

    def execute(self, sql, params):
      seen.append(sql)
      assert "d.time = s.t AND (COALESCE(d.jid, '')" in sql
      assert not re.search(r"d\.time [<>]=? %s AND \(COALESCE", sql)
      _h, _j1, _j2, _h2, a, b = params
      if "ORDER BY d2.time ASC" in sql:
        hit = [s for s in samples if a <= s < b][:1]
      else:
        hit = [s for s in samples if a < s <= b][-1:]
      self._row = (hit[0], 1 if hit[0] in owned else None) if hit else None

    def fetchone(self):
      return self._row

  monkeypatch.setattr(connection, "cursor", lambda: _Cur())
  out = sql_mod.first_last_scoped_times(["c117-002.x"], st, et, "71526")
  assert out == {"c117-002.x": (first_own, last_own)}
  assert len(seen) <= 80

  # Not owned at the midpoint sample (accounting host without job rows).
  owned = set()
  seen.clear()
  out = sql_mod.first_last_scoped_times(["idle.x"], st, et, "71526")
  assert out == {"idle.x": (None, None)}
  assert len(seen) == 1


def test_hosts_owning_mid_sample_checks_one_sample_not_the_window(monkeypatch):
  """hs04: accounting hosts without job rows made a scoped "first owned row"
  probe read the host's whole window (job 71526, 5 of 16 hosts timed out).
  Ownership is decided on the host's first sample at/after the midpoint."""
  from django.db import connection

  import hpcperfstats.analysis.metrics.lib.metrics_host_data_sql as sql_mod

  seen = []

  class _Cur:
    def __enter__(self):
      return self

    def __exit__(self, *_a):
      return False

    def execute(self, sql, params):
      seen.append((sql, params))

    def fetchall(self):
      return [("a.x", 1), ("b.x", None)]

  monkeypatch.setattr(connection, "cursor", lambda: _Cur())
  st = datetime(2026, 10, 27, 15, tzinfo=UTC)
  et = datetime(2026, 10, 27, 20, tzinfo=UTC)
  out = sql_mod.hosts_owning_mid_sample(["a.x", "b.x"], st, et, "71526")
  assert out == {"a.x"}
  sql, params = seen[0]
  assert "d.time = (SELECT d2.time FROM host_data d2" in sql
  assert "ORDER BY d2.time LIMIT 1" in sql
  assert "COALESCE(d.jid, '')" in sql
  # hs04 v3: WHERE EXISTS was flattened into a hash semi-join over all of
  # host_data; the check must stay a SELECT-list subquery per host.
  assert "EXISTS" not in sql
  assert sql.startswith("SELECT h.host, (SELECT 1 FROM host_data d")
  assert params == [
    datetime(2026, 10, 27, 17, 30, tzinfo=UTC),
    et,
    "71526",
    "71526",
    ["a.x", "b.x"],
  ]
