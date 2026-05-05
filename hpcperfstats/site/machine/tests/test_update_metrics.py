"""Unit tests for analysis.metrics.update_metrics (_iter_chunked_pks).

"""
import contextlib
import threading
import time
from concurrent.futures import Future
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.db.utils import OperationalError
from django.utils import timezone

from hpcperfstats.analysis.metrics.update_metrics import (
    _iter_chunked_pks,
    _proxy_readiness_has_any_and_post_end,
)
from hpcperfstats.analysis.metrics import update_metrics


@pytest.fixture(autouse=True)
def _patch_scheduler_defaults(monkeypatch):
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_mode", lambda: "strict_date")
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_prefetch_chunks", lambda: 2)
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_ready_queue_target", lambda: 4)
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_plot_prewarm_mode", lambda: "inline")
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_prewarm_workers", lambda: 1)
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_prewarm_retry_attempts", lambda: 1)
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_compute_threads", lambda: 1)
  monkeypatch.setattr(update_metrics, "persist_job_detail_artifacts_for_jid", lambda jid: None)


def _patch_connections_vendor(monkeypatch, vendor):
  """Make update_metrics see connections['default'].vendor == vendor."""
  fake_conn = MagicMock()
  fake_conn.vendor = vendor
  fake_conn.alias = "default"

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


def test_iter_chunked_pks_reraises_operational_error():
  """Keyset pagination must not fall back to offset slicing on DB timeout/cancel."""
  class BadQs:
    def filter(self, *a, **k):
      return self

    def values_list(self, *a, **k):
      raise OperationalError("canceling statement due to statement timeout")

  with pytest.raises(OperationalError):
    list(_iter_chunked_pks(BadQs(), 2))


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
  # Fallback path may probe with [:chunk] before offset slices; all are bounded.
  assert qs.slice_calls[-4:] == [
      slice(0, 2, None),
      slice(2, 4, None),
      slice(4, 6, None),
      slice(6, 8, None),
  ]


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


@pytest.mark.django_db(databases=[])
def test_jobs_queryset_orders_newest_job_first():
  """_jobs_queryset uses -end_time, -jid so streaming processes newest jobs first."""
  d = datetime(2025, 4, 10, 15, 30, 0)
  for rerun in (True, False):
    qs = update_metrics._jobs_queryset(d, 300, rerun=rerun)
    sql = str(qs.query).lower()
    assert "order by" in sql
    assert "end_time" in sql and "desc" in sql
    assert "jid" in sql and "desc" in sql


@pytest.mark.django_db(databases=[])
def test_jobs_queryset_postgresql_sql_jid_scoped_live_samples(monkeypatch):
  """Non-rerun + PostgreSQL: live sum uses jid + window (default path)."""
  monkeypatch.delenv("HPCPERFSTATS_LIVE_DISTINCT_LEGACY_HOSTLIST", raising=False)
  _patch_connections_vendor(monkeypatch, "postgresql")
  update_metrics._expected_job_metrics_row_count.cache_clear()
  d = datetime(2025, 4, 10, 15, 30, 0)
  qs = update_metrics._jobs_queryset(d, 300, rerun=False)
  sql = str(qs.query).lower()
  full_sql = str(qs.query)
  # Default live path is jid-scoped (not host_list unnest in live subquery);
  # unrelated unnest() may appear in plot fingerprint host ordering.
  assert 'h."jid" = "job_data"."jid"' in full_sql
  assert "group by" in sql
  assert "sum(" in sql
  assert "metrics_distinct_time_count" in sql
  assert "encode(sha256" in sql
  assert "job_plot_artifact" in sql
  assert "job_detail_artifact" in sql
  # TypeDetailFreshFingerprintRowCount raw SQL must use the real FK column
  # (db_column="jid"), not Django's jid_id ORM suffix.
  assert 't."jid" = "job_data"."jid"' in str(qs.query)


@pytest.mark.django_db(databases=[])
def test_jobs_queryset_postgresql_live_subquery_correlates_outer_job_row(monkeypatch):
  """Scalar subquery references outer job_data columns; OuterRef is not a bind param."""
  monkeypatch.delenv("HPCPERFSTATS_LIVE_DISTINCT_LEGACY_HOSTLIST", raising=False)
  _patch_connections_vendor(monkeypatch, "postgresql")
  update_metrics._expected_job_metrics_row_count.cache_clear()
  d = datetime(2025, 4, 10, 15, 30, 0)
  qs = update_metrics._jobs_queryset(d, 300, rerun=False)
  sql = str(qs.query)
  assert 'h."jid" = "job_data"."jid"' in sql
  assert 'h.time >= "job_data"."start_time"' in sql
  assert 'h.time <= "job_data"."end_time"' in sql


@pytest.mark.django_db(databases=[])
def test_jobs_queryset_postgresql_legacy_live_uses_unnest(monkeypatch):
  """Optional legacy mode restores unnest(host_list) live SQL."""
  monkeypatch.setenv("HPCPERFSTATS_LIVE_DISTINCT_LEGACY_HOSTLIST", "1")
  _patch_connections_vendor(monkeypatch, "postgresql")
  update_metrics._expected_job_metrics_row_count.cache_clear()
  d = datetime(2025, 4, 10, 15, 30, 0)
  qs = update_metrics._jobs_queryset(d, 300, rerun=False)
  sql = str(qs.query)
  assert 'unnest("job_data"."host_list")' in sql
  assert 'h.time >= "job_data"."start_time"' in sql
  assert 'h.time <= "job_data"."end_time"' in sql


@pytest.mark.django_db(databases=[])
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

    def ensure_pool(self):
      return object()

    def close_pool(self):
      return None

    def run(self, jobs, pool=None):
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

    def ensure_pool(self):
      return object()

    def close_pool(self):
      return None

    def run(self, jobs, pool=None):
      seen.append([j.jid for j in jobs])

  monkeypatch.setattr(update_metrics.metrics, "Metrics", lambda: FakeMetrics())
  monkeypatch.setattr(update_metrics, "DEBUG", False)

  update_metrics.update_metrics(datetime(2025, 4, 10), rerun=False)
  assert seen == [[101], [102], [103]]


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

    def ensure_pool(self):
      return object()

    def close_pool(self):
      return None

    def run(self, jobs, pool=None):
      seen.append([j.jid for j in jobs])

  monkeypatch.setattr(update_metrics.metrics, "Metrics", lambda: FakeMetrics())
  monkeypatch.setattr(update_metrics, "DEBUG", False)

  update_metrics.update_metrics(datetime(2025, 4, 10), rerun=False)
  assert seen == [[101], [103]]


def test_update_metrics_reuses_shared_pool_per_date(monkeypatch):
  """update_metrics should initialize one shared pool and reuse it per jid run."""
  monkeypatch.setattr(update_metrics, "_jobs_queryset", lambda *args, **kwargs: object())
  monkeypatch.setattr(
      update_metrics,
      "_iter_chunked_pks",
      lambda qs, chunk: iter([([101, 102], 2), ([103], 3)]),
  )
  monkeypatch.setattr(update_metrics, "close_old_connections", lambda: None)
  monkeypatch.setattr(update_metrics.gc, "collect", lambda: 0)
  monkeypatch.setattr(update_metrics, "_filter_jids_with_samples_after_end", lambda jids: list(jids))

  pool_token = object()
  pool_calls = []

  class FakeMetrics:
    simple_metrics_list = {}
    complex_metrics_list = []

    def ensure_pool(self):
      pool_calls.append("ensure")
      return pool_token

    def close_pool(self):
      pool_calls.append("close")

    def run(self, jobs, pool=None):
      pool_calls.append(pool)

  monkeypatch.setattr(update_metrics.metrics, "Metrics", lambda: FakeMetrics())
  monkeypatch.setattr(update_metrics, "DEBUG", False)

  update_metrics.update_metrics(datetime(2025, 4, 10), rerun=False)
  assert pool_calls == ["ensure", pool_token, pool_token, pool_token, "close"]


@pytest.mark.machine_unit_mock
def test_filter_jids_with_samples_after_end_requires_all_hosts(monkeypatch):
  """A job is ready only when every host has latest sample strictly after end_time."""
  _patch_connections_vendor(monkeypatch, "sqlite")
  end = datetime(2025, 4, 10, 12, 0, 0)
  jobs_rows = [
      {
          "jid": 101,
          "end_time": end,
          "host_list": ["n1.example.org", "n2.example.org"],
      },
      {
          "jid": 102,
          "end_time": end,
          "host_list": ["n3.example.org", "n4.example.org"],
      },
  ]
  # n2 does not meet strict > end_time, so jid 101 must be excluded.
  latest_rows = [
      {"host": "n1.example.org", "last_time": datetime(2025, 4, 10, 12, 0, 1)},
      {"host": "n2.example.org", "last_time": datetime(2025, 4, 10, 12, 0, 0)},
      {"host": "n3.example.org", "last_time": datetime(2025, 4, 10, 12, 0, 5)},
      {"host": "n4.example.org", "last_time": datetime(2025, 4, 10, 12, 0, 2)},
  ]

  class _JobManager:
    def filter(self, **kwargs):
      class _Qs:
        def order_by(self, *args):
          return self

        def values(self, *fields):
          return jobs_rows
      return _Qs()

  class _HostManager:
    def filter(self, **kwargs):
      class _Qs:
        def values(self, *fields):
          class _Annotate:
            def annotate(self, **ann):
              return latest_rows
          return _Annotate()
      return _Qs()

  monkeypatch.setattr(update_metrics.job_data, "objects", _JobManager())
  monkeypatch.setattr(update_metrics.host_data, "objects", _HostManager())

  ready = update_metrics._filter_jids_with_samples_after_end([101, 102])
  assert ready == [102]


@pytest.mark.machine_unit_mock
def test_ready_jids_batches_host_last_time_lookups(monkeypatch):
  """Large host unions should query host_data in bounded host__in batches."""
  _patch_connections_vendor(monkeypatch, "sqlite")
  monkeypatch.setattr(update_metrics, "HOST_LAST_TIME_LOOKUP_BATCH", 2)
  filter_batches = []

  class _HostManager:
    def filter(self, **kwargs):
      filter_batches.append(tuple(sorted(kwargs["host__in"])))
      class _Qs:
        def values(self, *fields):
          class _Annotate:
            def annotate(self, **ann):
              return [
                  {
                      "host": h,
                      "last_time": datetime(2025, 4, 10, 13, 0, 0),
                  }
                  for h in kwargs["host__in"]
              ]
          return _Annotate()
      return _Qs()

  monkeypatch.setattr(update_metrics, "_host_name_suffix", lambda: "")
  monkeypatch.setattr(update_metrics.host_data, "objects", _HostManager())

  jobs = [
      {
          "jid": 1,
          "end_time": datetime(2025, 4, 10, 12, 0, 0),
          "host_list": ["n1", "n2", "n3"],
      },
  ]
  ready = update_metrics._ready_jids_from_job_rows(jobs)
  assert ready == [1]
  assert filter_batches == [("n1", "n2"), ("n3",)]


@pytest.mark.machine_unit_mock
def test_latest_sample_time_by_host_postgresql_uses_lateral_unnest(monkeypatch):
  """PostgreSQL path uses LATERAL LIMIT 1 per host (not DISTINCT ON) and batches."""
  exec_log = []
  monkeypatch.setattr(
      update_metrics.transaction,
      "atomic",
      lambda using=None: contextlib.nullcontext(),
  )
  monkeypatch.setattr(update_metrics, "HOST_LAST_TIME_LOOKUP_BATCH", 2)

  class FakeCursor:
    def __enter__(self):
      return self

    def __exit__(self, *args, **kwargs):
      return False

    def execute(self, sql, params=None):
      exec_log.append((sql, params))
      if "unnest" in sql.lower() and params and params[0]:
        ts = datetime(2025, 1, 1, 12, 0, 5)
        self._rows = [(h, ts) for h in params[0]]
      else:
        self._rows = []

    def fetchall(self):
      return getattr(self, "_rows", [])

  fake_conn = MagicMock()
  fake_conn.vendor = "postgresql"
  fake_conn.alias = "default"

  def quote_name(name):
    return '"%s"' % str(name).replace('"', '""')

  fake_ops = MagicMock()
  fake_ops.quote_name = quote_name
  fake_conn.ops = fake_ops
  fake_conn.cursor = lambda: FakeCursor()

  handler = MagicMock()
  handler.__getitem__ = lambda self, name: fake_conn
  monkeypatch.setattr(update_metrics, "connections", handler)

  out = update_metrics._latest_sample_time_by_host(["z", "y", "x"])
  assert sorted(out.keys()) == ["x", "y", "z"]
  lateral_sql = [e for e in exec_log if e[0] and "unnest" in e[0].lower()]
  assert len(lateral_sql) == 2
  assert [e[1][0] for e in lateral_sql] == [["x", "y"], ["z"]]
  assert all("LEFT JOIN LATERAL" in e[0] for e in lateral_sql)
  assert all("LIMIT 1" in e[0] for e in lateral_sql)
  set_local = [e for e in exec_log if e[0] and "SET LOCAL" in e[0]]
  assert len(set_local) == 2
  assert all("max_parallel_workers_per_gather = 0" in e[0] for e in set_local)


@pytest.mark.machine_unit_mock
def test_filter_jids_postgresql_readiness_uses_orm_no_monolithic_sql(monkeypatch):
  """PostgreSQL strict readiness uses ORM + host probes, not the old parallel CTE."""
  exec_calls = []

  class FakeCursor:
    def __enter__(self):
      return self

    def __exit__(self, *args, **kwargs):
      return False

    def execute(self, sql, params=None):
      exec_calls.append(sql)

    def fetchall(self):
      return []

  fake_conn = MagicMock()
  fake_conn.vendor = "postgresql"
  fake_conn.alias = "default"

  def quote_name(name):
    return '"%s"' % str(name).replace('"', '""')

  fake_ops = MagicMock()
  fake_ops.quote_name = quote_name
  fake_conn.cursor = lambda: FakeCursor()

  handler = MagicMock()
  handler.__getitem__ = lambda self, name: fake_conn
  monkeypatch.setattr(update_metrics, "connections", handler)

  class FakeJobQuery:
    def filter(self, **kwargs):
      return self

    def order_by(self, *args):
      return self

    def values(self, *fields):
      return [{"jid": "j1", "end_time": None, "host_list": []}]

  monkeypatch.setattr(update_metrics.job_data, "objects", FakeJobQuery())
  monkeypatch.setattr(update_metrics, "_ready_jids_from_job_rows", lambda rows: [])

  update_metrics._filter_jids_with_samples_after_end(["j1"])
  assert exec_calls == []


@pytest.mark.machine_unit_mock
def test_update_metrics_for_dates_exported():
  """Regression: scheduler entrypoint must exist at module scope (not nested dead code)."""
  assert callable(update_metrics.update_metrics_for_dates)


@pytest.mark.machine_unit_mock
def test_filter_jids_readiness_query_orders_by_jid(monkeypatch):
  """Strict readiness job fetch uses order_by('jid') for stable ordering."""
  order_calls = []

  class FakeJobQuery:
    def filter(self, **kwargs):
      return self

    def order_by(self, *args):
      order_calls.append(args)
      return self

    def values(self, *fields):
      return []

  monkeypatch.setattr(update_metrics.job_data, "objects", FakeJobQuery())
  monkeypatch.setattr(update_metrics, "_ready_jids_from_job_rows", lambda rows: [])

  update_metrics._filter_jids_with_samples_after_end(["b"])
  assert order_calls == [("jid",)]


@pytest.mark.machine_unit_mock
def test_compute_jid_outcomes_batch_calls_metrics_run_once(monkeypatch):
  """``Metrics.run`` receives the full dequeue batch in one call (pool saturation)."""
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_skip_prewarm", lambda: True)
  batches = []

  class _M:
    def run(self, job_refs, pool=None):
      batches.append([r.jid for r in job_refs])

  job_refs = [
      SimpleNamespace(jid="j3"),
      SimpleNamespace(jid="j1"),
      SimpleNamespace(jid="j2"),
  ]
  out = update_metrics._compute_jid_outcomes_batch(
      job_refs,
      _M(),
      MagicMock(),
      None,
  )
  assert batches == [["j3", "j1", "j2"]]
  assert [d["jid"] for d in out] == ["j1", "j2", "j3"]


@pytest.mark.machine_unit_mock
def test_compute_jid_outcomes_batch_skip_prewarm_skips_plot_submit(monkeypatch):
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_skip_prewarm", lambda: True)

  class _M:
    def run(self, job_refs, pool=None):
      del pool

  pipe = MagicMock()
  out = update_metrics._compute_jid_outcomes_batch(
      [SimpleNamespace(jid="only")],
      _M(),
      pipe,
      None,
  )
  pipe.submit.assert_not_called()
  assert [d["jid"] for d in out] == ["only"]


@pytest.mark.machine_unit_mock
def test_compute_jid_outcomes_batch_prewarm_submits_each_jid(monkeypatch):
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_skip_prewarm", lambda: False)

  class _M:
    def run(self, job_refs, pool=None):
      del pool

  pipe = MagicMock()
  pipe.has_pending.return_value = False
  out = update_metrics._compute_jid_outcomes_batch(
      [SimpleNamespace(jid="j1"), SimpleNamespace(jid="j2"), SimpleNamespace(jid="j3")],
      _M(),
      pipe,
      None,
  )
  assert [c.args[0] for c in pipe.submit.call_args_list] == ["j1", "j2", "j3"]
  assert [d["jid"] for d in out] == ["j1", "j2", "j3"]


@pytest.mark.machine_unit_mock
def test_compute_jid_outcomes_batch_falls_back_per_jid_after_batch_failure(monkeypatch):
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_skip_prewarm", lambda: True)
  calls = []

  class _M:
    def run(self, job_refs, pool=None):
      del pool
      calls.append([r.jid for r in job_refs])
      if len(job_refs) > 1:
        raise TypeError("unhashable type: 'list'")
      if job_refs[0].jid == "bad":
        raise TypeError("still bad")

  job_refs = [
      SimpleNamespace(jid="good1"),
      SimpleNamespace(jid="bad"),
      SimpleNamespace(jid="good2"),
  ]
  out = update_metrics._compute_jid_outcomes_batch(
      job_refs,
      _M(),
      MagicMock(),
      None,
  )
  # One batch attempt, then per-jid fallback attempts.
  assert calls[0] == ["good1", "bad", "good2"]
  assert calls[1:] == [["good1"], ["bad"], ["good2"]]
  by_jid = {d["jid"]: d["ok"] for d in out}
  assert by_jid == {"good1": True, "good2": True, "bad": False}


@pytest.mark.machine_unit_mock
def test_proxy_readiness_has_any_and_post_end_semantics():
  """Matches legacy Exists: any row, and strictly-after-end sample."""
  t0 = datetime(2025, 4, 1, 12, 0, 0, tzinfo=timezone.utc)
  t1 = datetime(2025, 4, 1, 13, 0, 0, tzinfo=timezone.utc)
  assert _proxy_readiness_has_any_and_post_end(t0, None) == (False, False)
  assert _proxy_readiness_has_any_and_post_end(None, t1) == (True, False)
  assert _proxy_readiness_has_any_and_post_end(t0, t0) == (True, False)
  assert _proxy_readiness_has_any_and_post_end(t0, t1) == (True, True)


@pytest.mark.machine_unit_mock
def test_proxy_reject_not_ready_jids_batches_host_aggregates(monkeypatch):
  """Each jid sub-batch issues its own host_data aggregate (bounded queries)."""
  _patch_connections_vendor(monkeypatch, "postgresql")
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_proxy_reject_jid_batch_size", lambda: 2)
  host_batches = []

  def host_filter(*_a, jid__in=None, **_k):
    host_batches.append(tuple(jid__in))

    class _HostAgg:
      def values(self, *_names, **_kw):
        return self

      def annotate(self, **_kw):
        return self

      def __iter__(self):
        t_end = datetime(2025, 4, 1, 12, 0, 0, tzinfo=timezone.utc)
        t_after = datetime(2025, 4, 1, 14, 0, 0, tzinfo=timezone.utc)
        for j in jid__in:
          yield {"jid": j, "max_time": t_after if j != "mid" else t_end}

    return _HostAgg()

  def job_filter(*_a, jid__in=None, **_k):
    class _JobVals:
      def values(self, *_names, **_kw):
        t_end = datetime(2025, 4, 1, 12, 0, 0, tzinfo=timezone.utc)
        for j in jid__in:
          yield {"jid": j, "end_time": t_end}

    return _JobVals()

  monkeypatch.setattr(update_metrics.host_data.objects, "filter", host_filter)
  monkeypatch.setattr(update_metrics.job_data.objects, "filter", job_filter)

  jids = ["a", "mid", "c", "d", "e"]
  reject, unknown = update_metrics._proxy_reject_not_ready_jids(jids)
  assert host_batches == [("a", "mid"), ("c", "d"), ("e",)]
  assert reject == {"mid"}
  assert unknown == ["a", "c", "d", "e"]


@pytest.mark.machine_unit_mock
def test_proxy_reject_not_ready_jids_partitions(monkeypatch):
  """Reject vs unknown split matches legacy four-corner classification."""
  _patch_connections_vendor(monkeypatch, "postgresql")
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_proxy_reject_jid_batch_size", lambda: 99)
  t_end = datetime(2025, 4, 1, 12, 0, 0, tzinfo=timezone.utc)
  t_after = datetime(2025, 4, 1, 14, 0, 0, tzinfo=timezone.utc)

  def host_filter(*_a, jid__in=None, **_k):
    class _HostAgg:
      def values(self, *_names, **_kw):
        return self

      def annotate(self, **_kw):
        return self

      def __iter__(self):
        for j in jid__in:
          if j == "j1":
            yield {"jid": j, "max_time": t_after}
          elif j == "j2":
            yield {"jid": j, "max_time": t_end}
          elif j == "j3":
            yield {"jid": j, "max_time": t_after}
          elif j == "j4":
            pass

    return _HostAgg()

  def job_filter(*_a, jid__in=None, **_k):
    class _JobVals:
      def values(self, *_names, **_kw):
        for j in jid__in:
          yield {"jid": j, "end_time": t_end}

    return _JobVals()

  monkeypatch.setattr(update_metrics.host_data.objects, "filter", host_filter)
  monkeypatch.setattr(update_metrics.job_data.objects, "filter", job_filter)

  reject, unknown = update_metrics._proxy_reject_not_ready_jids(
      ["j1", "j2", "j3", "j4"]
  )
  assert reject == {"j2"}
  assert unknown == ["j1", "j3", "j4"]


@pytest.mark.machine_unit_mock
def test_proxy_readiness_for_jid_matches_bulk_singletons(monkeypatch):
  """Single-jid proxy helper matches one-element bulk classification."""
  _patch_connections_vendor(monkeypatch, "postgresql")
  t_end = datetime(2025, 4, 1, 12, 0, 0, tzinfo=timezone.utc)
  t_after = datetime(2025, 4, 1, 14, 0, 0, tzinfo=timezone.utc)

  def host_filter(*_a, jid=None, jid__in=None, **_k):
    if jid is not None:

      class _HostSingle:
        def aggregate(self, **_kw):
          if jid == "j1":
            return {"max_time": t_after}
          if jid == "j2":
            return {"max_time": t_end}
          if jid == "j3":
            return {"max_time": t_after}
          return {"max_time": None}

      return _HostSingle()

    class _HostAgg:
      def values(self, *_names, **_kw):
        return self

      def annotate(self, **_kw):
        return self

      def __iter__(self):
        for j in jid__in:
          if j == "j1":
            yield {"jid": j, "max_time": t_after}
          elif j == "j2":
            yield {"jid": j, "max_time": t_end}
          elif j == "j3":
            yield {"jid": j, "max_time": t_after}
          elif j == "j4":
            pass

    return _HostAgg()

  def job_filter(*_a, jid=None, jid__in=None, **_k):
    targets = [jid] if jid is not None else list(jid__in or ())

    class _JobVals:
      def values(self, *_names, **_kw):
        return self

      def first(self):
        if len(targets) == 1:
          return {"jid": targets[0], "end_time": t_end}
        return None

      def __iter__(self):
        for j in targets:
          yield {"jid": j, "end_time": t_end}

    return _JobVals()

  monkeypatch.setattr(update_metrics.host_data.objects, "filter", host_filter)
  monkeypatch.setattr(update_metrics.job_data.objects, "filter", job_filter)

  for jid in ["j1", "j2", "j3", "j4"]:
    rset, unk = update_metrics._proxy_reject_not_ready_jids([jid])
    bucket = update_metrics._proxy_readiness_for_jid(jid)
    assert (bucket == "reject") == (jid in rset)
    assert (bucket == "unknown") == (jid in unk)


@pytest.mark.django_db(databases=[])
def test_fill_ready_queue_prefetch_one_leaves_pending_tail(monkeypatch):
  """When prefetch is satisfied, remaining jids in the chunk are deferred via pending_tail."""
  strict_calls = []

  def _strict(jids):
    strict_calls.append(list(jids))
    if "j1" in jids:
      return ["j1"]
    return []

  monkeypatch.setattr(update_metrics, "_proxy_readiness_for_jid", lambda jid: "unknown")
  monkeypatch.setattr(
      update_metrics,
      "_proxy_reject_not_ready_jids",
      lambda jids: (set(), list(jids)),
  )
  monkeypatch.setattr(update_metrics, "_filter_jids_with_samples_after_end", _strict)
  monkeypatch.setattr(update_metrics.time, "monotonic", lambda: 0.0)

  states = [{
      "date": datetime(2025, 4, 10),
      "iter": iter([(["j1", "j2", "j3"], 3)]),
      "done": False,
      "pending_tail": None,
  }]
  ready = []
  stats = {
      "candidate_jids": 0,
      "skipped_not_ready": 0,
      "readiness_error_chunks": 0,
      "proxy_checked_chunks": 0,
      "proxy_rejected_jids": 0,
      "proxy_not_ready_jids": 0,
      "strict_not_ready_jids": 0,
      "strict_ready_jids": 0,
      "strict_cooldown_skips": 0,
      "deferred_not_ready_queue_size": 0,
      "deferred_not_ready_due_now": 0,
      "deferred_quarantined_jids": 0,
      "stall_exit_triggered": 0,
      "strict_check_calls": 0,
      "strict_check_timeouts": 0,
      "strict_check_avg_latency_ms": 0.0,
      "strict_batch_size_current": update_metrics.STRICT_CHECK_BATCH_MIN,
  }
  update_metrics._fill_ready_queue(
      states,
      ready,
      mode="strict_date",
      prefetch_chunks=1,
      phase_timer=update_metrics._PhaseTimer(),
      stats=stats,
      strict_check_state={
          "batch_size": update_metrics.STRICT_CHECK_BATCH_MIN,
          "max_batch_size": update_metrics.STRICT_CHECK_BATCH_MIN,
      },
      strict_check_cooldown_until={},
      rr_cursor={"idx": 0},
      scheduler_shared_lock=threading.Lock(),
  )
  assert ready == ["j1"]
  assert strict_calls == [["j1", "j2", "j3"]]
  assert states[0]["pending_tail"] == ["j2", "j3"]
  assert stats["candidate_jids"] == 3


def test_adjust_readiness_probe_target_backoff_and_growth():
  cur = update_metrics._adjust_readiness_probe_target(
      current_target=512,
      had_error=True,
      elapsed_s=1.0,
      produced_ready=False,
      max_target=2000,
  )
  assert cur == 256

  grown = update_metrics._adjust_readiness_probe_target(
      current_target=256,
      had_error=False,
      elapsed_s=0.1,
      produced_ready=True,
      max_target=300,
  )
  assert grown == 300

  same = update_metrics._adjust_readiness_probe_target(
      current_target=256,
      had_error=False,
      elapsed_s=2.0,
      produced_ready=False,
      max_target=2000,
  )
  assert same == 256


def test_adjust_strict_check_batch_size_backoff_and_growth():
  cur = update_metrics._adjust_strict_check_batch_size(
      current_size=128,
      had_timeout=True,
      latency_s=1.0,
      max_size=512,
  )
  assert cur == 64

  grown = update_metrics._adjust_strict_check_batch_size(
      current_size=64,
      had_timeout=False,
      latency_s=0.01,
      max_size=80,
  )
  assert grown == 80

  same = update_metrics._adjust_strict_check_batch_size(
      current_size=64,
      had_timeout=False,
      latency_s=2.0,
      max_size=512,
  )
  assert same == 64


@pytest.mark.django_db(databases=[])
def test_fill_ready_queue_strict_subbatch_timeout_does_not_abort(monkeypatch):
  """Per-jid strict timeouts should not prevent a later jid in the same chunk."""
  states = [{
      "date": datetime(2025, 4, 10),
      "iter": iter([(["j1", "j2", "j3"], 3)]),
      "done": False,
      "pending_tail": None,
  }]
  ready = []
  timer = update_metrics._PhaseTimer()
  stats = {
      "candidate_jids": 0,
      "skipped_not_ready": 0,
      "readiness_error_chunks": 0,
      "proxy_checked_chunks": 0,
      "proxy_rejected_jids": 0,
      "proxy_not_ready_jids": 0,
      "strict_not_ready_jids": 0,
      "strict_ready_jids": 0,
      "strict_cooldown_skips": 0,
      "deferred_not_ready_queue_size": 0,
      "deferred_not_ready_due_now": 0,
      "deferred_quarantined_jids": 0,
      "stall_exit_triggered": 0,
      "strict_check_calls": 0,
      "strict_check_timeouts": 0,
      "strict_check_avg_latency_ms": 0.0,
      "strict_batch_size_current": update_metrics.STRICT_CHECK_BATCH_MIN,
  }
  strict_state = {"batch_size": 1, "max_batch_size": 4}
  cooldown = {}
  rr = {"idx": 0}

  monkeypatch.setattr(update_metrics, "_proxy_readiness_for_jid", lambda jid: "unknown")
  monkeypatch.setattr(
      update_metrics,
      "_proxy_reject_not_ready_jids",
      lambda jids: (set(), list(jids)),
  )

  def _strict(jids):
    assert len(jids) == 1
    if jids[0] in ("j1", "j2"):
      raise OperationalError("timeout")
    if jids[0] == "j3":
      return ["j3"]
    return []

  monkeypatch.setattr(update_metrics, "_filter_jids_with_samples_after_end", _strict)
  monkeypatch.setattr(update_metrics.time, "monotonic", lambda: 100.0)

  update_metrics._fill_ready_queue(
      states,
      ready,
      mode="strict_date",
      prefetch_chunks=10,
      phase_timer=timer,
      stats=stats,
      strict_check_state=strict_state,
      strict_check_cooldown_until=cooldown,
      rr_cursor=rr,
      scheduler_shared_lock=threading.Lock(),
  )

  assert "j3" in ready
  assert stats["readiness_error_chunks"] == 2
  assert stats["strict_check_timeouts"] == 2
  # Batched path counts successful strict completions; j1/j2 time out in fallback.
  assert stats["strict_check_calls"] == 1
  # Two timeouts clamp to STRICT_CHECK_BATCH_MIN, then the j3 success path grows
  # toward max_batch_size under STRICT_CHECK_FAST_SUCCESS_S.
  assert strict_state["batch_size"] == strict_state["max_batch_size"]


@pytest.mark.django_db(databases=[])
def test_fill_ready_queue_counts_cooldown_skips(monkeypatch):
  """Cooldown-suppressed strict checks should be visible in scheduler counters."""
  states = [{
      "date": datetime(2025, 4, 10),
      "iter": iter([(["j1"], 1)]),
      "done": False,
      "pending_tail": None,
  }]
  ready = []
  timer = update_metrics._PhaseTimer()
  stats = {
      "candidate_jids": 0,
      "skipped_not_ready": 0,
      "readiness_error_chunks": 0,
      "proxy_checked_chunks": 0,
      "proxy_rejected_jids": 0,
      "proxy_not_ready_jids": 0,
      "strict_not_ready_jids": 0,
      "strict_ready_jids": 0,
      "strict_cooldown_skips": 0,
      "deferred_not_ready_queue_size": 0,
      "deferred_not_ready_due_now": 0,
      "deferred_quarantined_jids": 0,
      "stall_exit_triggered": 0,
      "strict_check_calls": 0,
      "strict_check_timeouts": 0,
      "strict_check_avg_latency_ms": 0.0,
      "strict_batch_size_current": update_metrics.STRICT_CHECK_BATCH_MIN,
  }
  monkeypatch.setattr(update_metrics, "_proxy_reject_not_ready_jids", lambda jids: (set(), list(jids)))
  monkeypatch.setattr(update_metrics.time, "monotonic", lambda: 100.0)
  update_metrics._fill_ready_queue(
      states,
      ready,
      mode="strict_date",
      prefetch_chunks=10,
      phase_timer=timer,
      stats=stats,
      strict_check_state={
          "batch_size": update_metrics.STRICT_CHECK_BATCH_MIN,
          "max_batch_size": update_metrics.STRICT_CHECK_BATCH_MIN,
      },
      strict_check_cooldown_until={"j1": 200.0},
      rr_cursor={"idx": 0},
      scheduler_shared_lock=threading.Lock(),
  )
  assert ready == []
  assert stats["strict_cooldown_skips"] == 1
  assert stats["strict_not_ready_jids"] == 1


@pytest.mark.django_db(databases=[])
def test_update_metrics_for_dates_global_scheduler_interleaves_dates(monkeypatch):
  """Global scheduler should dispatch cross-date jobs instead of waiting per date."""
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_mode", lambda: "global_fifo")
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_prefetch_chunks", lambda: 10)
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_ready_queue_target", lambda: 8)
  monkeypatch.setattr(update_metrics, "close_old_connections", lambda: None)
  monkeypatch.setattr(update_metrics, "_pg_session_statement_timeout_for_metrics_batch", contextlib.nullcontext)
  monkeypatch.setattr(update_metrics, "_filter_jids_with_samples_after_end", lambda jids: list(jids))
  monkeypatch.setattr(update_metrics, "_proxy_readiness_for_jid", lambda jid: "unknown")
  monkeypatch.setattr(
      update_metrics,
      "_proxy_reject_not_ready_jids",
      lambda jids: (set(), list(jids)),
  )
  monkeypatch.setattr(update_metrics.gc, "collect", lambda: 0)
  monkeypatch.setattr(update_metrics, "shutdown_requested", [False])

  class _FakeQs:
    def __init__(self, chunks):
      self.chunks = chunks

  by_day = {
      10: [([1001, 1002], 2), ([1003], 3)],
      9: [([901], 1), ([902], 2)],
  }

  monkeypatch.setattr(
      update_metrics,
      "_jobs_queryset",
      lambda d, *_args, **_kwargs: _FakeQs(list(by_day[d.day])),
  )
  monkeypatch.setattr(update_metrics, "_iter_chunked_pks", lambda qs, _chunk: iter(qs.chunks))

  seen_batches = []

  class FakeMetrics:
    simple_metrics_list = {}
    complex_metrics_list = []

    def ensure_pool(self):
      return object()

    def close_pool(self):
      return None

    def run(self, jobs, pool=None):
      seen_batches.append([j.jid for j in jobs])

  monkeypatch.setattr(update_metrics.metrics, "Metrics", lambda: FakeMetrics())
  monkeypatch.setattr(update_metrics, "persist_job_plot_artifacts_for_jid", lambda jid: None)
  d1 = datetime(2025, 4, 10)
  d2 = datetime(2025, 4, 9)
  update_metrics.update_metrics_for_dates([d1, d2], rerun=False)
  flat = [jid for batch in seen_batches for jid in batch]
  assert 901 in flat and 1001 in flat
  assert min(flat) == 901


@pytest.mark.django_db(databases=[])
def test_update_metrics_for_dates_exhausts_when_readiness_filters_all(monkeypatch):
  """Readiness may drop every jid in a chunk; iterators must still advance and exit."""
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_mode", lambda: "global_fifo")
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_prefetch_chunks", lambda: 2)
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_ready_queue_target", lambda: 8)
  monkeypatch.setattr(update_metrics, "close_old_connections", lambda: None)
  monkeypatch.setattr(update_metrics, "_pg_session_statement_timeout_for_metrics_batch", contextlib.nullcontext)
  monkeypatch.setattr(update_metrics, "_filter_jids_with_samples_after_end", lambda jids: [])
  monkeypatch.setattr(update_metrics, "STALL_EXIT_AFTER_SECONDS", 0.1)
  monkeypatch.setattr(update_metrics.gc, "collect", lambda: 0)
  monkeypatch.setattr(update_metrics, "shutdown_requested", [False])

  class _FakeQs:
    def __init__(self, chunks):
      self.chunks = chunks

  by_day = {
      10: [([1001], 1), ([1002], 2)],
      9: [([901], 1)],
  }

  monkeypatch.setattr(
      update_metrics,
      "_jobs_queryset",
      lambda d, *_args, **_kwargs: _FakeQs(list(by_day[d.day])),
  )
  monkeypatch.setattr(update_metrics, "_iter_chunked_pks", lambda qs, _chunk: iter(qs.chunks))

  class FakeMetrics:
    simple_metrics_list = {}
    complex_metrics_list = []

    def ensure_pool(self):
      return object()

    def close_pool(self):
      return None

    def run(self, jobs, pool=None):
      raise AssertionError("metrics run should not run when readiness filters all jids")

  monkeypatch.setattr(update_metrics.metrics, "Metrics", lambda: FakeMetrics())
  monkeypatch.setattr(update_metrics, "persist_job_plot_artifacts_for_jid", lambda jid: None)
  d1 = datetime(2025, 4, 10)
  d2 = datetime(2025, 4, 9)
  update_metrics.update_metrics_for_dates([d1, d2], rerun=False)


@pytest.mark.django_db(databases=[])
def test_update_metrics_for_dates_sets_stall_diagnostics_on_no_progress(monkeypatch):
  """Persistent not-ready loops should terminate with explicit stall diagnostics."""
  monkeypatch.setenv("HPCPERFSTATS_UPDATE_METRICS_RETURN_DIAGNOSTICS", "1")
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_mode", lambda: "global_fifo")
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_prefetch_chunks", lambda: 1)
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_ready_queue_target", lambda: 4)
  monkeypatch.setattr(update_metrics, "close_old_connections", lambda: None)
  monkeypatch.setattr(update_metrics, "_pg_session_statement_timeout_for_metrics_batch", contextlib.nullcontext)
  monkeypatch.setattr(update_metrics, "_filter_jids_with_samples_after_end", lambda jids: [])
  monkeypatch.setattr(update_metrics, "_proxy_reject_not_ready_jids", lambda jids: (set(), list(jids)))
  monkeypatch.setattr(update_metrics, "STALL_EXIT_AFTER_SECONDS", 0.1)
  monkeypatch.setattr(update_metrics.gc, "collect", lambda: 0)
  monkeypatch.setattr(update_metrics, "shutdown_requested", [False])

  class _FakeQs:
    def __init__(self, chunks):
      self.chunks = chunks

  by_day = {10: [([1001], 1)]}
  monkeypatch.setattr(
      update_metrics,
      "_jobs_queryset",
      lambda d, *_args, **_kwargs: _FakeQs(list(by_day[d.day])),
  )
  monkeypatch.setattr(update_metrics, "_iter_chunked_pks", lambda qs, _chunk: iter(qs.chunks))
  monkeypatch.setattr(update_metrics, "_start_candidate_rescan_thread", lambda **kwargs: None)

  class FakeMetrics:
    simple_metrics_list = {}
    complex_metrics_list = []

    def ensure_pool(self):
      return object()

    def close_pool(self):
      return None

    def run(self, jobs, pool=None):
      raise AssertionError("metrics run should not execute when all jobs are not-ready")

  monkeypatch.setattr(update_metrics.metrics, "Metrics", lambda: FakeMetrics())
  monkeypatch.setattr(update_metrics, "persist_job_plot_artifacts_for_jid", lambda jid: None)
  update_metrics.LAST_UPDATE_METRICS_DIAGNOSTICS = None
  update_metrics.update_metrics_for_dates([datetime(2025, 4, 10)], rerun=False)
  diag = update_metrics.LAST_UPDATE_METRICS_DIAGNOSTICS
  assert diag is not None
  assert diag["stats"]["stall_exit_triggered"] == 1
  assert diag["stats"]["strict_not_ready_jids"] >= 1


@pytest.mark.django_db(databases=[])
def test_update_metrics_for_dates_rescan_picks_up_new_mid_run_jid(monkeypatch):
  """Background rescan candidates should be merged without interrupting ready work."""
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_mode", lambda: "global_fifo")
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_prefetch_chunks", lambda: 2)
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_ready_queue_target", lambda: 8)
  monkeypatch.setattr(update_metrics, "close_old_connections", lambda: None)
  monkeypatch.setattr(update_metrics, "_pg_session_statement_timeout_for_metrics_batch", contextlib.nullcontext)
  monkeypatch.setattr(update_metrics, "_proxy_readiness_for_jid", lambda jid: "unknown")
  monkeypatch.setattr(
      update_metrics,
      "_proxy_reject_not_ready_jids",
      lambda jids: (set(), list(jids)),
  )
  strict_calls = {"n": 0}

  def _strict(jids):
    # Keep producer alive briefly so background rescan can inject work.
    strict_calls["n"] += 1
    if strict_calls["n"] <= 2:
      return []
    return list(jids)

  monkeypatch.setattr(update_metrics, "_filter_jids_with_samples_after_end", _strict)
  monkeypatch.setattr(update_metrics.gc, "collect", lambda: 0)
  monkeypatch.setattr(update_metrics, "shutdown_requested", [False])
  monkeypatch.setattr(update_metrics, "DEFERRED_NOT_READY_RETRY_SECONDS", 0.0)

  class _FakeQs:
    def __init__(self, chunks):
      self.chunks = chunks

  by_day = {
      10: [([1001], 1)],
  }
  monkeypatch.setattr(
      update_metrics,
      "_jobs_queryset",
      lambda d, *_args, **_kwargs: _FakeQs(list(by_day[d.day])),
  )
  monkeypatch.setattr(update_metrics, "_iter_chunked_pks", lambda qs, _chunk: iter(qs.chunks))

  def _injecting_rescan_thread(**kwargs):
    lock = kwargs["rescan_lock"]
    pending = kwargs["rescan_candidate_jids"]
    seen = kwargs["rescan_seen_jids"]
    stop_event = kwargs["stop_event"]

    def _worker():
      time.sleep(0.05)
      if stop_event.is_set():
        return
      with lock:
        seen.add(2002)
        pending.append(2002)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    return thread

  monkeypatch.setattr(update_metrics, "_start_candidate_rescan_thread", _injecting_rescan_thread)

  seen_batches = []

  class FakeMetrics:
    simple_metrics_list = {}
    complex_metrics_list = []

    def ensure_pool(self):
      return object()

    def close_pool(self):
      return None

    def run(self, jobs, pool=None):
      del pool
      seen_batches.append([j.jid for j in jobs])

  monkeypatch.setattr(update_metrics.metrics, "Metrics", lambda: FakeMetrics())
  monkeypatch.setattr(update_metrics, "persist_job_plot_artifacts_for_jid", lambda jid: None)
  d1 = datetime(2025, 4, 10)
  update_metrics.update_metrics_for_dates([d1], rerun=False)
  flat = [jid for batch in seen_batches for jid in batch]
  assert 1001 in flat
  assert 2002 in flat


@pytest.mark.django_db(databases=[])
def test_update_metrics_for_dates_rechecks_deferred_not_ready_jid_until_ready(monkeypatch):
  """A jid skipped as not-ready should be retried later and processed once ready."""
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_mode", lambda: "global_fifo")
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_prefetch_chunks", lambda: 1)
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_ready_queue_target", lambda: 4)
  monkeypatch.setattr(update_metrics, "close_old_connections", lambda: None)
  monkeypatch.setattr(update_metrics, "_pg_session_statement_timeout_for_metrics_batch", contextlib.nullcontext)
  monkeypatch.setattr(update_metrics, "_proxy_readiness_for_jid", lambda jid: "unknown")
  monkeypatch.setattr(
      update_metrics,
      "_proxy_reject_not_ready_jids",
      lambda jids: (set(), list(jids)),
  )
  monkeypatch.setattr(update_metrics.gc, "collect", lambda: 0)
  monkeypatch.setattr(update_metrics, "shutdown_requested", [False])
  monkeypatch.setattr(update_metrics, "DEFERRED_NOT_READY_RETRY_SECONDS", 0.0)

  class _FakeQs:
    def __init__(self, chunks):
      self.chunks = chunks

  by_day = {
      10: [([1001], 1)],
  }
  monkeypatch.setattr(
      update_metrics,
      "_jobs_queryset",
      lambda d, *_args, **_kwargs: _FakeQs(list(by_day[d.day])),
  )
  monkeypatch.setattr(update_metrics, "_iter_chunked_pks", lambda qs, _chunk: iter(qs.chunks))
  monkeypatch.setattr(update_metrics, "_start_candidate_rescan_thread", lambda **kwargs: None)

  readiness_calls = {"count": 0}

  def _strict(jids):
    readiness_calls["count"] += 1
    if readiness_calls["count"] == 1:
      return []
    return list(jids)

  monkeypatch.setattr(update_metrics, "_filter_jids_with_samples_after_end", _strict)

  seen = []

  class FakeMetrics:
    simple_metrics_list = {}
    complex_metrics_list = []

    def ensure_pool(self):
      return object()

    def close_pool(self):
      return None

    def run(self, jobs, pool=None):
      del pool
      seen.extend([j.jid for j in jobs])

  monkeypatch.setattr(update_metrics.metrics, "Metrics", lambda: FakeMetrics())
  monkeypatch.setattr(update_metrics, "persist_job_plot_artifacts_for_jid", lambda jid: None)
  d1 = datetime(2025, 4, 10)
  update_metrics.update_metrics_for_dates([d1], rerun=False)
  assert seen == [1001]
  assert readiness_calls["count"] >= 2


@pytest.mark.django_db(databases=[])
def test_update_metrics_for_dates_empty_date_list_returns(monkeypatch):
  monkeypatch.setattr(update_metrics, "close_old_connections", lambda: None)
  monkeypatch.setattr(update_metrics, "_pg_session_statement_timeout_for_metrics_batch", contextlib.nullcontext)
  monkeypatch.setattr(update_metrics, "shutdown_requested", [False])

  class FakeMetrics:
    simple_metrics_list = {}
    complex_metrics_list = {}

    def ensure_pool(self):
      return object()

    def close_pool(self):
      return None

    def run(self, jobs, pool=None):
      raise AssertionError("no work for zero days")

  monkeypatch.setattr(update_metrics.metrics, "Metrics", lambda: FakeMetrics())
  update_metrics.update_metrics_for_dates([], rerun=False)


def test_update_metrics_for_dates_per_jid_failure_does_not_stop_progress(monkeypatch):
  """One failing jid should not stop progress for the rest of the queue."""
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_mode", lambda: "global_fifo")
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_prefetch_chunks", lambda: 1)
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_ready_queue_target", lambda: 1)
  monkeypatch.setattr(update_metrics, "close_old_connections", lambda: None)
  monkeypatch.setattr(update_metrics, "_pg_session_statement_timeout_for_metrics_batch", contextlib.nullcontext)
  monkeypatch.setattr(update_metrics, "_filter_jids_with_samples_after_end", lambda jids: list(jids))
  monkeypatch.setattr(update_metrics, "_proxy_readiness_for_jid", lambda jid: "unknown")
  monkeypatch.setattr(
      update_metrics,
      "_proxy_reject_not_ready_jids",
      lambda jids: (set(), list(jids)),
  )
  monkeypatch.setattr(update_metrics.gc, "collect", lambda: 0)
  monkeypatch.setattr(update_metrics, "shutdown_requested", [False])

  class _FakeQs:
    def __init__(self, chunks):
      self.chunks = chunks

  by_day = {
      10: [([1001, 1002], 2)],
      9: [([901], 1)],
  }
  monkeypatch.setattr(
      update_metrics,
      "_jobs_queryset",
      lambda d, *_args, **_kwargs: _FakeQs(list(by_day[d.day])),
  )
  monkeypatch.setattr(update_metrics, "_iter_chunked_pks", lambda qs, _chunk: iter(qs.chunks))

  class FakeReporter:
    def __init__(self):
      self.completed = 0

    def start(self):
      return None

    def stop(self):
      return None

    def record_completed(self, count):
      self.completed += int(count)

    def completed_in_window(self):
      return self.completed

    def readiness_errors_total(self):
      return 0

    def record_readiness_error_chunk(self, count=1):
      del count

  reporter = FakeReporter()
  monkeypatch.setattr(update_metrics, "_CompletionReporter", lambda: reporter)

  successful = []
  prewarmed = []
  detail_prewarmed = []

  class FakeMetrics:
    simple_metrics_list = {}
    complex_metrics_list = []

    def ensure_pool(self):
      return object()

    def close_pool(self):
      return None

    def run(self, jobs, pool=None):
      jids = [j.jid for j in jobs]
      assert len(jids) == 1
      if jids[0] == 1002:
        raise RuntimeError("single-job failure")
      successful.append(jids[0])

  monkeypatch.setattr(update_metrics.metrics, "Metrics", lambda: FakeMetrics())
  monkeypatch.setattr(update_metrics, "persist_job_plot_artifacts_for_jid", lambda jid: prewarmed.append(jid))
  monkeypatch.setattr(update_metrics, "persist_job_detail_artifacts_for_jid", lambda jid: detail_prewarmed.append(jid))
  d1 = datetime(2025, 4, 10)
  d2 = datetime(2025, 4, 9)
  update_metrics.update_metrics_for_dates([d1, d2], rerun=False)

  assert sorted(successful) == [901, 1001]
  assert sorted(prewarmed) == [901, 1001]
  assert sorted(detail_prewarmed) == [901, 1001]
  assert reporter.completed == 2


def test_prewarm_pipeline_drain_some_force_does_not_use_invalid_wait_condition(monkeypatch):
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_plot_prewarm_mode", lambda: "pipeline_required")
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_prewarm_workers", lambda: 1)
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_prewarm_retry_attempts", lambda: 1)

  pipeline = update_metrics._PrewarmPipeline()
  fut = Future()
  fut.set_result(None)
  pipeline._pending.add(fut)
  pipeline._created_at[fut] = update_metrics.time.monotonic()

  pipeline.drain_some(force=True)

  assert pipeline._done == 1
  assert len(pipeline._pending) == 0
  pipeline._executor.shutdown(wait=True)


def test_prewarm_pipeline_run_for_jid_shares_context(monkeypatch):
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_plot_prewarm_mode", lambda: "inline")
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_prewarm_workers", lambda: 1)
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_prewarm_retry_attempts", lambda: 1)
  monkeypatch.setattr(update_metrics, "close_old_connections", lambda: None)
  calls = []

  def _detail(jid, context=None):
    calls.append(("detail", jid, context))
    context["detail_seen"] = True

  def _plot(jid, context=None):
    calls.append(("plot", jid, context))
    assert context.get("detail_seen") is True

  monkeypatch.setattr(update_metrics, "persist_job_detail_artifacts_for_jid", _detail)
  monkeypatch.setattr(update_metrics, "persist_job_plot_artifacts_for_jid", _plot)

  pipeline = update_metrics._PrewarmPipeline()
  shared = {"_telemetry": {}}
  timing = pipeline.run_for_jid("j42", shared_context=shared)
  assert calls[0][0] == "detail"
  assert calls[1][0] == "plot"
  assert calls[0][2] is shared
  assert calls[1][2] is shared
  assert timing["undivided"] is False
  assert timing["prewarm_total_s"] == timing["detail_s"] + timing["plots_s"]


@pytest.mark.machine_unit_mock
def test_compute_and_prewarm_jid_logs_when_full_pipeline_succeeds(monkeypatch):
  """After metrics + job detail + plots, emit one compute-complete timing line."""
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_plot_prewarm_mode", lambda: "inline")
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_prewarm_workers", lambda: 1)
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_prewarm_retry_attempts", lambda: 1)
  monkeypatch.setattr(update_metrics, "close_old_connections", lambda: None)
  monkeypatch.setattr(update_metrics, "persist_job_detail_artifacts_for_jid", lambda *a, **k: None)
  monkeypatch.setattr(update_metrics, "persist_job_plot_artifacts_for_jid", lambda *a, **k: None)
  recorded = []

  def _capture(*args, **kwargs):
    recorded.append(args)

  monkeypatch.setattr(update_metrics, "log_print", _capture)

  class _M:
    def run(self, job_list, pool=None):
      return None

  ref = SimpleNamespace(jid="jid-x")
  pipe = update_metrics._PrewarmPipeline()
  out = update_metrics._compute_and_prewarm_jid(_M(), pipe, ref, None)
  assert out["ok"] is True
  joined = " ".join(" ".join(str(x) for x in tup) for tup in recorded)
  assert "jid-x" in joined and "compute complete" in joined
  assert "metrics=" in joined and "job_detail=" in joined and "job_plots=" in joined


def test_main_sleep_after_waits_60_seconds(monkeypatch):
  """sleep_after mode should wait exactly 60s after metrics completion."""
  monkeypatch.setattr(update_metrics, "_default_metrics_date_range", lambda: (
      datetime(2025, 4, 1), datetime(2025, 4, 1)))
  monkeypatch.setattr(
      update_metrics,
      "parse_start_end_dates",
      lambda argv, default_start, default_end: (default_start, default_end),
  )
  monkeypatch.setattr(update_metrics, "log_date_range", lambda *args, **kwargs: None)
  monkeypatch.setattr(update_metrics, "log_print", lambda *args, **kwargs: None)
  monkeypatch.setattr(
      update_metrics.cfg, "get_sync_enable_cpuset_priority_budget", lambda: False
  )
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_mode", lambda: "global_fifo")
  monkeypatch.setattr(update_metrics, "update_metrics_for_dates", lambda dates: None)
  monkeypatch.setattr(update_metrics, "close_old_connections", lambda: None)
  monkeypatch.setattr(update_metrics.connections, "close_all", lambda: None)
  update_metrics.shutdown_requested[0] = False
  sleeps = []
  monkeypatch.setattr(update_metrics, "sleep_until_shutdown", lambda secs: sleeps.append(secs))

  update_metrics.main(argv=["update_metrics.py"], sleep_after=True)

  assert sleeps == [60]


def test_main_default_sleep_after_waits_60_seconds(monkeypatch):
  """Default behavior should sleep after completion when not overridden."""
  monkeypatch.delenv("HPCPERFSTATS_UPDATE_METRICS_MAIN_SLEEP_AFTER", raising=False)
  monkeypatch.setattr(update_metrics, "_default_metrics_date_range", lambda: (
      datetime(2025, 4, 1), datetime(2025, 4, 1)))
  monkeypatch.setattr(
      update_metrics,
      "parse_start_end_dates",
      lambda argv, default_start, default_end: (default_start, default_end),
  )
  monkeypatch.setattr(update_metrics, "log_date_range", lambda *args, **kwargs: None)
  monkeypatch.setattr(update_metrics, "log_print", lambda *args, **kwargs: None)
  monkeypatch.setattr(
      update_metrics.cfg, "get_sync_enable_cpuset_priority_budget", lambda: False
  )
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_mode", lambda: "global_fifo")
  monkeypatch.setattr(update_metrics, "update_metrics_for_dates", lambda dates: None)
  monkeypatch.setattr(update_metrics, "close_old_connections", lambda: None)
  monkeypatch.setattr(update_metrics.connections, "close_all", lambda: None)
  update_metrics.shutdown_requested[0] = False
  sleeps = []
  monkeypatch.setattr(update_metrics, "sleep_until_shutdown", lambda secs: sleeps.append(secs))

  update_metrics.main(argv=["update_metrics.py"])

  assert sleeps == [60]


@pytest.mark.machine_unit_mock
def test_completion_reporter_sync_completed_total_tracks_delta():
  rep = update_metrics._CompletionReporter(report_interval_s=5, window_s=3600)
  rep.sync_completed_total(0)
  rep.sync_completed_total(3)
  rep.sync_completed_total(3)
  rep.sync_completed_total(8)
  assert rep.completed_total() == 8
  assert rep.completed_in_window() == 8


def test_main_env_false_disables_default_sleep(monkeypatch):
  """Env override should disable sleep when set to false-like values."""
  monkeypatch.setenv("HPCPERFSTATS_UPDATE_METRICS_MAIN_SLEEP_AFTER", "false")
  monkeypatch.setattr(update_metrics, "_default_metrics_date_range", lambda: (
      datetime(2025, 4, 1), datetime(2025, 4, 1)))
  monkeypatch.setattr(
      update_metrics,
      "parse_start_end_dates",
      lambda argv, default_start, default_end: (default_start, default_end),
  )
  monkeypatch.setattr(update_metrics, "log_date_range", lambda *args, **kwargs: None)
  monkeypatch.setattr(update_metrics, "log_print", lambda *args, **kwargs: None)
  monkeypatch.setattr(
      update_metrics.cfg, "get_sync_enable_cpuset_priority_budget", lambda: False
  )
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_mode", lambda: "global_fifo")
  monkeypatch.setattr(update_metrics, "update_metrics_for_dates", lambda dates: None)
  monkeypatch.setattr(update_metrics, "close_old_connections", lambda: None)
  monkeypatch.setattr(update_metrics.connections, "close_all", lambda: None)
  update_metrics.shutdown_requested[0] = False
  sleeps = []
  monkeypatch.setattr(update_metrics, "sleep_until_shutdown", lambda secs: sleeps.append(secs))

  update_metrics.main(argv=["update_metrics.py"])

  assert sleeps == []
