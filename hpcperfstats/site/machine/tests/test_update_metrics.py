"""Unit tests for analysis.metrics.update_metrics (_iter_chunked_pks).

"""
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from hpcperfstats.analysis.metrics.update_metrics import _iter_chunked_pks
from hpcperfstats.analysis.metrics import update_metrics


def _patch_connections_vendor(monkeypatch, vendor):
  """Make update_metrics see connections['default'].vendor == vendor."""
  fake_conn = MagicMock()
  fake_conn.vendor = vendor

  def quote_name(name):
    return '"%s"' % str(name).replace('"', '""')

  fake_ops = MagicMock()
  fake_ops.quote_name = quote_name
  fake_conn.ops = fake_ops

  handler = MagicMock()
  handler.__getitem__ = lambda self, name: fake_conn
  monkeypatch.setattr(update_metrics, "connections", handler)


def test_iter_chunked_pks_empty_queryset():
  """_iter_chunked_pks yields nothing for empty queryset slicing."""
  class EmptyQs:
    def values_list(self, *args, **kwargs):
      return self

    def __getitem__(self, item):
      return []

  qs = EmptyQs()
  chunks = list(_iter_chunked_pks(qs, 2))
  assert chunks == []


def test_iter_chunked_pks_single_chunk():
  """_iter_chunked_pks yields one (pk_list, total) when pks fit in one chunk."""
  class Qs:
    def values_list(self, *args, **kwargs):
      return self

    def __getitem__(self, item):
      data = [1, 2, 3]
      return data[item]

  qs = Qs()
  chunks = list(_iter_chunked_pks(qs, 10))
  assert len(chunks) == 1
  assert chunks[0][0] == [1, 2, 3]
  assert chunks[0][1] == 3


def test_iter_chunked_pks_multiple_chunks():
  """_iter_chunked_pks yields (pk_list, total_so_far) for each sliced chunk."""
  class Qs:
    def values_list(self, *args, **kwargs):
      return self

    def __getitem__(self, item):
      data = [10, 20, 30, 40, 50]
      return data[item]

  qs = Qs()
  chunks = list(_iter_chunked_pks(qs, 2))
  assert len(chunks) == 3
  assert chunks[0] == ([10, 20], 2)
  assert chunks[1] == ([30, 40], 4)
  assert chunks[2] == ([50], 5)


def test_iter_chunked_pks_uses_slice_windows_not_iterator():
  """Chunking should use bounded slices (avoids long-lived streaming cursors)."""
  class Qs:
    def __init__(self):
      self.slice_calls = []

    def values_list(self, *args, **kwargs):
      return self

    def __getitem__(self, item):
      self.slice_calls.append(item)
      data = [1, 2, 3, 4, 5]
      return data[item]

  qs = Qs()
  chunks = list(_iter_chunked_pks(qs, 2))
  assert chunks == [([1, 2], 2), ([3, 4], 4), ([5], 5)]
  assert qs.slice_calls == [slice(0, 2, None), slice(2, 4, None), slice(4, 6, None), slice(6, 8, None)]


def test_notify_parent_if_sigterm_sends_sigchld(monkeypatch):
  calls = []
  monkeypatch.setattr(
      update_metrics, "send_sigchld_to_parent", lambda: calls.append("sigchld"))

  update_metrics._notify_parent_if_sigterm([True])
  assert calls == ["sigchld"]


def test_default_metrics_date_range_seven_days(monkeypatch):
  """No-arg CLI default spans seven calendar days through today (local midnight bounds)."""
  monkeypatch.setattr(
      update_metrics,
      "_today_datetime",
      lambda: datetime(2025, 3, 23, 15, 30, 0),
  )
  start, end = update_metrics._default_metrics_date_range()
  assert end == datetime(2025, 3, 23, 0, 0, 0)
  assert start == datetime(2025, 3, 17, 0, 0, 0)


def test_jobs_queryset_orders_newest_job_first():
  """_jobs_queryset uses -end_time, -jid so streaming processes newest jobs first."""
  d = datetime(2025, 4, 10, 15, 30, 0)
  for rerun in (True, False):
    qs = update_metrics._jobs_queryset(d, 300, rerun=rerun)
    sql = str(qs.query).lower()
    assert "order by" in sql
    assert "end_time" in sql and "desc" in sql
    assert "jid" in sql and "desc" in sql


def test_jobs_queryset_postgresql_sql_contains_unnest_for_live_samples(monkeypatch):
  """Non-rerun + PostgreSQL: SQL sums per-host COUNT(DISTINCT time) via GROUP BY."""
  _patch_connections_vendor(monkeypatch, "postgresql")
  update_metrics._expected_job_metrics_row_count.cache_clear()
  d = datetime(2025, 4, 10, 15, 30, 0)
  qs = update_metrics._jobs_queryset(d, 300, rerun=False)
  sql = str(qs.query).lower()
  assert "unnest" in sql
  assert "group by" in sql
  assert "sum(" in sql
  assert "metrics_distinct_time_count" in sql


def test_jobs_queryset_postgresql_live_subquery_correlates_outer_job_row(monkeypatch):
  """RawSQL must reference outer job_data columns in SQL; OuterRef is not a bind param."""
  _patch_connections_vendor(monkeypatch, "postgresql")
  update_metrics._expected_job_metrics_row_count.cache_clear()
  d = datetime(2025, 4, 10, 15, 30, 0)
  qs = update_metrics._jobs_queryset(d, 300, rerun=False)
  sql = str(qs.query)
  assert 'unnest("job_data"."host_list")' in sql
  assert 'h.time >= "job_data"."start_time"' in sql
  assert 'h.time <= "job_data"."end_time"' in sql


def test_jobs_queryset_non_postgresql_omits_unnest_live_samples(monkeypatch):
  """Non-PostgreSQL: skip live sample annotation (no unnest)."""
  _patch_connections_vendor(monkeypatch, "sqlite")
  update_metrics._expected_job_metrics_row_count.cache_clear()
  d = datetime(2025, 4, 10, 15, 30, 0)
  qs = update_metrics._jobs_queryset(d, 300, rerun=False)
  sql = str(qs.query).lower()
  assert "unnest" not in sql


def test_expected_job_metrics_row_count_cached(monkeypatch):
  """_expected_job_metrics_row_count calls catalog size at most once per process."""
  calls = []
  orig = update_metrics.expected_job_metric_row_count

  def spy():
    calls.append(1)
    return orig()

  monkeypatch.setattr(update_metrics, "expected_job_metric_row_count", spy)
  update_metrics._expected_job_metrics_row_count.cache_clear()
  assert update_metrics._expected_job_metrics_row_count() == orig()
  assert update_metrics._expected_job_metrics_row_count() == orig()
  assert len(calls) == 1


def test_install_sigterm_handler_sets_flag_and_returns(monkeypatch):
  monkeypatch.setattr(update_metrics.signal, "getsignal", lambda sig: "prev")
  monkeypatch.setattr(update_metrics.signal, "signal", lambda sig, h: None)

  update_metrics.shutdown_requested[0] = False

  previous_handler, sigterm_received, handler = update_metrics._install_sigterm_handler(
      exit_code=143
  )
  assert previous_handler == "prev"
  assert sigterm_received[0] is False

  handler(update_metrics.signal.SIGTERM, None)
  assert sigterm_received[0] is True
  assert update_metrics.shutdown_requested[0] is True
  update_metrics.shutdown_requested[0] = False


def test_update_metrics_stops_between_chunks_on_shutdown(monkeypatch):
  """When SIGTERM sets shutdown_requested, metrics processing should stop."""
  monkeypatch.setattr(update_metrics, "_jobs_queryset", lambda *args, **kwargs: object())
  monkeypatch.setattr(
      update_metrics,
      "_iter_chunked_pks",
      lambda qs, chunk: iter([([101, 102], 2), ([103], 3)]),
  )
  monkeypatch.setattr(update_metrics, "close_old_connections", lambda: None)
  monkeypatch.setattr(update_metrics.gc, "collect", lambda: 0)
  monkeypatch.setattr(update_metrics, "_filter_jids_with_samples_after_end", lambda jids: list(jids))

  update_metrics.shutdown_requested[0] = False
  seen = []

  class FakeMetrics:
    simple_metrics_list = {}
    complex_metrics_list = []

    def run(self, jobs):
      seen.append([j.jid for j in jobs])
      # Simulate shutdown arriving mid-processing; updater should stop on
      # the next chunk boundary.
      update_metrics.shutdown_requested[0] = True

  monkeypatch.setattr(update_metrics.metrics, "Metrics", lambda: FakeMetrics())
  monkeypatch.setattr(update_metrics, "DEBUG", False)

  update_metrics.update_metrics(datetime(2025, 4, 10), rerun=False)
  assert seen == [[101, 102]]
  update_metrics.shutdown_requested[0] = False


def test_job_refs_from_jids_are_lightweight():
  """Chunk payload should only include jid-bearing lightweight objects."""
  refs = update_metrics._job_refs_from_jids([11, 22, 33])
  assert [r.jid for r in refs] == [11, 22, 33]
  assert all(not hasattr(r, "_state") for r in refs)


def test_update_metrics_uses_lightweight_job_refs(monkeypatch):
  """update_metrics should not re-query job_data rows per chunk."""
  monkeypatch.setattr(update_metrics, "_jobs_queryset", lambda *args, **kwargs: object())
  monkeypatch.setattr(
      update_metrics,
      "_iter_chunked_pks",
      lambda qs, chunk: iter([([101, 102], 2), ([103], 3)]),
  )
  monkeypatch.setattr(update_metrics, "close_old_connections", lambda: None)
  monkeypatch.setattr(update_metrics.gc, "collect", lambda: 0)
  monkeypatch.setattr(update_metrics, "_filter_jids_with_samples_after_end", lambda jids: list(jids))

  # If a regression re-introduces ORM fetches, fail loudly.
  class _NoQueryManager:
    def filter(self, *args, **kwargs):
      raise AssertionError("update_metrics should not query job_data per chunk")

  monkeypatch.setattr(update_metrics.job_data, "objects", _NoQueryManager())

  seen = []

  class FakeMetrics:
    simple_metrics_list = {}
    complex_metrics_list = []

    def run(self, jobs):
      seen.append([j.jid for j in jobs])

  monkeypatch.setattr(update_metrics.metrics, "Metrics", lambda: FakeMetrics())
  monkeypatch.setattr(update_metrics, "DEBUG", False)

  update_metrics.update_metrics(datetime(2025, 4, 10), rerun=False)
  assert seen == [[101, 102], [103]]


def test_update_metrics_skips_jobs_without_post_end_host_samples(monkeypatch):
  """Jobs without host latest sample strictly after end_time are skipped."""
  monkeypatch.setattr(update_metrics, "_jobs_queryset", lambda *args, **kwargs: object())
  monkeypatch.setattr(
      update_metrics,
      "_iter_chunked_pks",
      lambda qs, chunk: iter([([101, 102, 103], 3)]),
  )
  monkeypatch.setattr(update_metrics, "close_old_connections", lambda: None)
  monkeypatch.setattr(update_metrics.gc, "collect", lambda: 0)
  monkeypatch.setattr(
      update_metrics,
      "_filter_jids_with_samples_after_end",
      lambda jids: [jid for jid in jids if jid in (101, 103)],
  )

  seen = []

  class FakeMetrics:
    simple_metrics_list = {}
    complex_metrics_list = []

    def run(self, jobs):
      seen.append([j.jid for j in jobs])

  monkeypatch.setattr(update_metrics.metrics, "Metrics", lambda: FakeMetrics())
  monkeypatch.setattr(update_metrics, "DEBUG", False)

  update_metrics.update_metrics(datetime(2025, 4, 10), rerun=False)
  assert seen == [[101, 103]]
