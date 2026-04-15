"""Unit tests for analysis.metrics.update_metrics (_iter_chunked_pks).

"""
import contextlib
from concurrent.futures import Future
from datetime import datetime
from unittest.mock import MagicMock

import pytest
from django.db.utils import OperationalError

from hpcperfstats.analysis.metrics.update_metrics import _iter_chunked_pks
from hpcperfstats.analysis.metrics import update_metrics


@pytest.fixture(autouse=True)
def _patch_scheduler_defaults(monkeypatch):
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_mode", lambda: "strict_date")
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_prefetch_chunks", lambda: 2)
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_ready_queue_target", lambda: 4)
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_plot_prewarm_mode", lambda: "inline")
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_prewarm_workers", lambda: 1)
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_prewarm_retry_attempts", lambda: 1)


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
  assert "unnest" not in sql
  assert "group by" in sql
  assert "sum(" in sql
  assert "metrics_distinct_time_count" in sql


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

    def ensure_pool(self):
      return object()

    def close_pool(self):
      return None

    def run(self, jobs, pool=None):
      seen.append([j.jid for j in jobs])

  monkeypatch.setattr(update_metrics.metrics, "Metrics", lambda: FakeMetrics())
  monkeypatch.setattr(update_metrics, "DEBUG", False)

  update_metrics.update_metrics(datetime(2025, 4, 10), rerun=False)
  assert seen == [[101, 103]]


def test_update_metrics_reuses_shared_pool_per_date(monkeypatch):
  """update_metrics should initialize one shared pool and reuse it per chunk."""
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
  assert pool_calls == ["ensure", pool_token, pool_token, "close"]


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


@pytest.mark.django_db(databases=[])
def test_filter_jids_postgresql_readiness_sql_avoids_nested_aggregates(monkeypatch):
  """Readiness CTE must not nest aggregates (PostgreSQL: GroupingError)."""
  exec_log = []

  class FakeCursor:
    def __enter__(self):
      return self

    def __exit__(self, *args, **kwargs):
      return False

    def execute(self, sql, params=None):
      exec_log.append(sql)

    def fetchall(self):
      return []

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
  monkeypatch.setattr(update_metrics.transaction, "atomic", lambda using=None: contextlib.nullcontext())
  monkeypatch.setattr(update_metrics, "_host_name_suffix", lambda: ".example.org")

  update_metrics._filter_jids_with_samples_after_end(["j1", "j2"])
  assert len(exec_log) >= 2
  assert exec_log[0].lower().startswith("set local statement_timeout")
  sql = exec_log[1].lower()
  assert "having count(*) filter (where last_time is null or last_time <= end_time) = 0" in sql
  assert "bool_and(last_time" not in sql


@pytest.mark.django_db(databases=[])
def test_filter_jids_postgresql_readiness_sql_timeout_falls_back(monkeypatch):
  class BadCursor:
    def __enter__(self):
      return self

    def __exit__(self, *args, **kwargs):
      return False

    def execute(self, _sql, _params=None):
      raise OperationalError("statement timeout")

  fake_conn = MagicMock()
  fake_conn.vendor = "postgresql"
  fake_conn.alias = "default"

  def quote_name(name):
    return '"%s"' % str(name).replace('"', '""')

  fake_ops = MagicMock()
  fake_ops.quote_name = quote_name
  fake_conn.ops = fake_ops
  fake_conn.cursor = lambda: BadCursor()

  handler = MagicMock()
  handler.__getitem__ = lambda self, name: fake_conn
  monkeypatch.setattr(update_metrics, "connections", handler)
  monkeypatch.setattr(update_metrics.transaction, "atomic", lambda using=None: contextlib.nullcontext())
  monkeypatch.setattr(update_metrics, "_host_name_suffix", lambda: ".example.org")

  fallback_called = []
  monkeypatch.setattr(
      update_metrics.job_data.objects,
      "filter",
      lambda **_kwargs: MagicMock(values=lambda *_a, **_k: [{"jid": "j1", "end_time": None, "host_list": []}]),
  )
  monkeypatch.setattr(
      update_metrics,
      "_ready_jids_from_job_rows",
      lambda rows: fallback_called.append(rows) or ["j1"],
  )

  assert update_metrics._filter_jids_with_samples_after_end(["j1"]) == ["j1"]
  assert len(fallback_called) == 1


@pytest.mark.django_db(databases=[])
def test_update_metrics_for_dates_global_scheduler_interleaves_dates(monkeypatch):
  """Global scheduler should dispatch cross-date jobs instead of waiting per date."""
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_mode", lambda: "global_fifo")
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_prefetch_chunks", lambda: 10)
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_ready_queue_target", lambda: 8)
  monkeypatch.setattr(update_metrics, "close_old_connections", lambda: None)
  monkeypatch.setattr(update_metrics, "_pg_session_statement_timeout_for_metrics_batch", contextlib.nullcontext)
  monkeypatch.setattr(update_metrics, "_filter_jids_with_samples_after_end", lambda jids: list(jids))
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


@pytest.mark.django_db(databases=[])
def test_update_metrics_for_dates_batch_failure_falls_back_and_continues(monkeypatch):
  """One failing jid should not stop progress for the rest of the batch."""
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_mode", lambda: "global_fifo")
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_prefetch_chunks", lambda: 2)
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_ready_queue_target", lambda: 8)
  monkeypatch.setattr(update_metrics, "close_old_connections", lambda: None)
  monkeypatch.setattr(update_metrics, "_pg_session_statement_timeout_for_metrics_batch", contextlib.nullcontext)
  monkeypatch.setattr(update_metrics, "_filter_jids_with_samples_after_end", lambda jids: list(jids))
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

  reporter = FakeReporter()
  monkeypatch.setattr(update_metrics, "_CompletionReporter", lambda: reporter)

  successful = []
  prewarmed = []

  class FakeMetrics:
    simple_metrics_list = {}
    complex_metrics_list = []

    def ensure_pool(self):
      return object()

    def close_pool(self):
      return None

    def run(self, jobs, pool=None):
      jids = [j.jid for j in jobs]
      if len(jids) > 1:
        raise RuntimeError("synthetic batched failure")
      if jids[0] == 1002:
        raise RuntimeError("single-job failure")
      successful.append(jids[0])

  monkeypatch.setattr(update_metrics.metrics, "Metrics", lambda: FakeMetrics())
  monkeypatch.setattr(update_metrics, "persist_job_plot_artifacts_for_jid", lambda jid: prewarmed.append(jid))
  d1 = datetime(2025, 4, 10)
  d2 = datetime(2025, 4, 9)
  update_metrics.update_metrics_for_dates([d1, d2], rerun=False)

  assert sorted(successful) == [901, 1001]
  assert sorted(prewarmed) == [901, 1001]
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
