"""Unit tests for analysis.metrics.update_metrics (_iter_chunked_pks).

"""
import contextlib
import inspect
import os
import threading
import time
from collections import deque
from datetime import datetime, timedelta
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

_PG_SESSION_TIMEOUT_CM = update_metrics._pg_session_statement_timeout_for_metrics_batch


def _ready_queue_jids(ready_queue):
  """Normalize scheduler ready-queue entries (jid str or candidate ref)."""
  out = []
  for item in ready_queue:
    out.append(item.jid if hasattr(item, "jid") else item)
  return out


def _patch_strict_readiness_batch(monkeypatch, fn):
  """Patch batched strict readiness in ``_fill_ready_queue`` (jid list + bounds map)."""

  def _wrapped(jids):
    result = fn(jids)
    if isinstance(result, tuple) and len(result) == 2:
      return result
    ready = list(result or [])
    return ready, {j: (None, None) for j in ready}

  monkeypatch.setattr(
      update_metrics,
      "_filter_jids_with_samples_after_end_and_bounds",
      _wrapped,
  )


@pytest.fixture(autouse=True)
def _patch_scheduler_defaults(monkeypatch):
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_mode", lambda: "strict_date")
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_prefetch_chunks", lambda: 2)
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_ready_queue_target", lambda: 4)
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_plot_prewarm_mode", lambda: "inline")
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_compute_batch_max_window_s", lambda: 0.0)
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_compute_batch_max_single_job_s", lambda: 0.0)
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_compute_batch_unknown_runtime_s", lambda: 172800.0)
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_compute_watchdog_s", lambda: 120.0)
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_compute_total_watchdog_s", lambda: 0.0)
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_deferred_not_ready_retry_s", lambda: 10.0)
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_deferred_not_ready_max_retries", lambda: 30)
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_deferred_not_ready_max_age_s", lambda: 900.0)
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_deferred_not_ready_quarantine_s", lambda: 300.0)
  monkeypatch.setattr(update_metrics, "persist_job_detail_artifacts_for_jid", lambda jid: None)


def _enqueue_chunks_from_date_states(**kwargs):
  """Default readiness producer for unit tests: enqueue mocked chunk jids."""
  class _DoneProducer:
    def join(self, timeout=None):
      del timeout

  with kwargs["ready_queue_lock"]:
    for state in kwargs["date_states"]:
      try:
        while True:
          pk_chunk, _total = next(state["iter"])
          jids = [
              item.jid if hasattr(item, "jid") else item
              for item in pk_chunk
          ]
          ready, _bounds = update_metrics._filter_jids_with_samples_after_end_and_bounds(jids)
          for jid in ready:
            kwargs["ready_queue"].append(update_metrics._candidate_ref(jid))
      except StopIteration:
        state["done"] = True
  kwargs["producer_done"].set()
  return _DoneProducer()


@pytest.fixture(autouse=True)
def _patch_machine_unit_mock_scheduler_flow(monkeypatch, request):
  """After scheduler defaults: unit-mock tests use global_fifo and stub /pub refresh."""
  if not request.node.get_closest_marker("machine_unit_mock"):
    return
  import hpcperfstats.dbload.lib.shutdown_utils as shutdown_utils

  monkeypatch.setattr(shutdown_utils, "shutdown_requested", [False])
  monkeypatch.setattr(update_metrics, "shutdown_requested", [False])
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_mode", lambda: "global_fifo")
  monkeypatch.setattr(update_metrics, "close_old_connections", lambda: None)
  monkeypatch.setattr(
      update_metrics,
      "_pg_session_statement_timeout_for_metrics_batch",
      contextlib.nullcontext,
  )
  monkeypatch.setattr(update_metrics, "_pg_local_readiness_timeouts", contextlib.nullcontext)
  monkeypatch.setattr(
      update_metrics,
      "refresh_public_expansion_factor_artifacts_parallel",
      lambda pool, **kwargs: {
          "degraded": 0,
          "worker_exceptions": 0,
          "watchdog_timeouts": 0,
          "pending_tasks": 0,
          "tasks_completed": 0,
          "tasks_total": 0,
      },
  )
  monkeypatch.setattr(update_metrics, "refresh_public_expansion_factor_artifacts_safe", lambda: None)
  monkeypatch.setattr(
      update_metrics.cfg,
      "get_metrics_readiness_require_window_coverage",
      lambda: False,
  )
  monkeypatch.setattr(
      update_metrics.cfg,
      "get_metrics_readiness_start_margin_seconds",
      lambda: 600.0,
  )
  monkeypatch.setattr(
      update_metrics.cfg,
      "get_metrics_readiness_end_margin_seconds",
      lambda: 600.0,
  )


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
  assert [r.jid for r in chunks[0][0]] == [1, 2, 3]
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
  assert ([r.jid for r in chunks[0][0]], chunks[0][1]) == ([10, 20], 2)
  assert ([r.jid for r in chunks[1][0]], chunks[1][1]) == ([30, 40], 4)
  assert ([r.jid for r in chunks[2][0]], chunks[2][1]) == ([50], 5)


def test_iter_chunked_pks_non_queryset_does_not_double_yield():
  """Test doubles without QuerySet.filter must use offset path only (no keyset tail)."""

  class Qs:
    def values_list(self, *args, **kwargs):
      return self

    def __getitem__(self, item):
      return [1, 2, 3][item]

  chunks = list(_iter_chunked_pks(Qs(), 10))
  assert len(chunks) == 1
  assert [r.jid for r in chunks[0][0]] == [1, 2, 3]


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
  assert [([r.jid for r in ch], n) for ch, n in chunks] == [([1, 2], 2), ([3, 4], 4), ([5], 5)]
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


@pytest.mark.machine_unit_mock
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


@pytest.mark.machine_unit_mock
def test_newest_first_metrics_dates_span_inclusive_window():
  start = datetime(2026, 8, 4, 0, 0, 0)
  end = datetime(2026, 8, 10, 0, 0, 0)
  dates = update_metrics._newest_first_metrics_dates(start, end)
  assert dates[0] == end
  assert dates[-1] == start
  assert len(dates) == 7


@pytest.mark.machine_unit_mock
def test_metrics_window_needs_rollover_when_today_after_window_end(monkeypatch):
  monkeypatch.setattr(
      update_metrics,
      "_today_datetime",
      lambda: datetime(2026, 8, 11, 19, 26, 0),
  )
  dates = update_metrics._newest_first_metrics_dates(
      datetime(2026, 8, 4, 0, 0, 0),
      datetime(2026, 8, 10, 0, 0, 0),
  )
  assert update_metrics._metrics_window_needs_rollover(
      dates, allow_rollover=True
  ) is True
  assert update_metrics._metrics_window_needs_rollover(
      dates, allow_rollover=False
  ) is False


@pytest.mark.machine_unit_mock
def test_metrics_window_needs_rollover_false_on_same_calendar_day(monkeypatch):
  monkeypatch.setattr(
      update_metrics,
      "_today_datetime",
      lambda: datetime(2026, 8, 10, 23, 50, 0),
  )
  dates = update_metrics._newest_first_metrics_dates(
      datetime(2026, 8, 4, 0, 0, 0),
      datetime(2026, 8, 10, 0, 0, 0),
  )
  assert update_metrics._metrics_window_needs_rollover(
      dates, allow_rollover=True
  ) is False


@pytest.mark.machine_unit_mock
def test_apply_default_metrics_window_rollover_includes_new_today(monkeypatch):
  """Frozen Aug 10 window must pick up Aug 11 after the clock advances."""
  clock = {"now": datetime(2026, 8, 10, 23, 6, 0)}
  monkeypatch.setattr(
      update_metrics, "_today_datetime", lambda: clock["now"]
  )
  monkeypatch.setattr(
      update_metrics,
      "_build_date_chunk_iterators",
      lambda dates, min_time, rerun, phase_timer: [
          {"date": d, "done": False} for d in dates
      ],
  )
  monkeypatch.setattr(
      update_metrics,
      "_log_metrics_window_census",
      lambda *args, **kwargs: None,
  )
  start, end = update_metrics._default_metrics_date_range()
  dates = update_metrics._newest_first_metrics_dates(start, end)
  date_states = [{"date": d, "done": False} for d in dates]
  assert dates[0] == datetime(2026, 8, 10, 0, 0, 0)
  clock["now"] = datetime(2026, 8, 11, 19, 26, 0)
  rolled = update_metrics._apply_default_metrics_window_rollover(
      dates,
      date_states,
      min_time=300,
      rerun=False,
      phase_timer=None,
      allow_rollover=True,
  )
  assert rolled is True
  assert dates[0] == datetime(2026, 8, 11, 0, 0, 0)
  assert dates[-1] == datetime(2026, 8, 5, 0, 0, 0)
  assert datetime(2026, 8, 4, 0, 0, 0) not in dates
  assert [s["date"] for s in date_states] == dates


@pytest.mark.machine_unit_mock
def test_apply_default_metrics_window_rollover_skips_cli_explicit_dates(
    monkeypatch,
):
  monkeypatch.setattr(
      update_metrics,
      "_today_datetime",
      lambda: datetime(2026, 8, 11, 19, 0, 0),
  )
  monkeypatch.setattr(
      update_metrics,
      "_log_metrics_window_census",
      lambda *args, **kwargs: None,
  )
  dates = update_metrics._newest_first_metrics_dates(
      datetime(2026, 8, 1, 0, 0, 0),
      datetime(2026, 8, 7, 0, 0, 0),
  )
  date_states = [{"date": d} for d in dates]
  before = list(dates)
  rolled = update_metrics._apply_default_metrics_window_rollover(
      dates,
      date_states,
      min_time=300,
      rerun=False,
      phase_timer=None,
      allow_rollover=False,
  )
  assert rolled is False
  assert dates == before


@pytest.mark.machine_unit_mock
def test_argv_has_explicit_metrics_dates():
  assert update_metrics._argv_has_explicit_metrics_dates(
      ["update_metrics.py", "2026-08-01", "2026-08-07"]
  ) is True
  assert update_metrics._argv_has_explicit_metrics_dates(
      ["update_metrics.py"]
  ) is False
  assert update_metrics._argv_has_explicit_metrics_dates(
      ["update_metrics.py", "--jid", "1987"]
  ) is False


@pytest.mark.machine_unit_mock
def test_jobs_queryset_arity_is_date_min_time_rerun():
  params = list(inspect.signature(update_metrics._jobs_queryset).parameters)
  assert params == ["date", "min_time", "rerun"]


@pytest.mark.machine_unit_mock
def test_cheap_metrics_day_job_qs_omits_host_data():
  src = inspect.getsource(update_metrics._cheap_metrics_day_job_qs)
  assert "job_data.objects.filter" in src
  assert "host_data.objects" not in src
  assert "metrics_data.objects" not in src


@pytest.mark.machine_unit_mock
def test_log_metrics_window_census_includes_today_and_window(monkeypatch):
  lines = []
  monkeypatch.setattr(
      update_metrics, "_today_datetime",
      lambda: datetime(2026, 8, 11, 19, 26, 0),
  )
  monkeypatch.setattr(
      update_metrics,
      "_cheap_metrics_day_census",
      lambda _d, _min: {"all": 10, "rt_null": 0, "rt_ge_min_time": 8},
  )
  monkeypatch.setattr(
      update_metrics, "_metrics_day_listed_count", lambda *_a, **_k: 0
  )
  monkeypatch.setattr(
      update_metrics, "log_print", lambda msg, flush=False: lines.append(msg)
  )
  dates = [
      datetime(2026, 8, 11, 0, 0, 0),
      datetime(2026, 8, 5, 0, 0, 0),
  ]
  update_metrics._log_metrics_window_census(
      dates, min_time=300, rerun=False, reason="empty_pass"
  )
  assert len(lines) == 1
  assert "empty_pass" in lines[0]
  assert "today=2026-08-11 19:26:00" in lines[0]
  assert "window_start=2026-08-05" in lines[0]
  assert "window_end=2026-08-11" in lines[0]
  assert "listed=0" in lines[0]
  assert "host_data" not in lines[0].lower()


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
def test_jobs_queryset_filters_end_time_with_half_open_range(monkeypatch):
  """Use ``end_time__gte`` / ``end_time__lt`` instead of ``end_time__date`` for index use."""
  _patch_connections_vendor(monkeypatch, "postgresql")
  update_metrics._expected_job_metrics_row_count.cache_clear()
  d = datetime(2025, 4, 10, 15, 30, 0)
  qs = update_metrics._jobs_queryset(d, 300, rerun=True)
  sql = str(qs.query).lower()
  assert "end_time" in sql
  assert ">=" in sql
  assert "end_time__date" not in sql


@pytest.mark.django_db(databases=[])
def test_end_time_calendar_day_half_open_bounds_span_one_day():
  from datetime import date as date_cls

  lo, hi = update_metrics._end_time_calendar_day_half_open_bounds(
      date_cls(2025, 4, 10)
  )
  assert lo < hi
  assert (hi - lo).total_seconds() == 86400


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
  assert "multiprecision_mix" in sql
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


@pytest.mark.django_db(databases=[])
def test_jobs_queryset_annotates_artifact_only_candidate(monkeypatch):
  _patch_connections_vendor(monkeypatch, "postgresql")
  update_metrics._expected_job_metrics_row_count.cache_clear()
  d = datetime(2025, 4, 10, 15, 30, 0)
  qs = update_metrics._jobs_queryset(d, 300, rerun=False)
  sql = str(qs.query)
  assert "artifact_only_candidate" in sql
  assert "job_plot_artifact" in sql
  assert "job_detail_artifact" in sql


@pytest.mark.django_db(databases=[])
def test_jobs_queryset_includes_gate_failure_recheck(monkeypatch):
  from hpcperfstats.analysis.metrics.lib.metrics import (
      INSUFFICIENT_DATA_FOR_METRICS_PROCESSING,
  )

  _patch_connections_vendor(monkeypatch, "postgresql")
  update_metrics._expected_job_metrics_row_count.cache_clear()
  d = datetime(2025, 4, 10, 15, 30, 0)
  qs = update_metrics._jobs_queryset(d, 300, rerun=False)
  sql = str(qs.query)
  assert INSUFFICIENT_DATA_FOR_METRICS_PROCESSING in sql
  assert "no_data_reason" in sql.lower()


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


@pytest.mark.machine_unit_mock
def test_update_metrics_stops_between_chunks_on_shutdown(monkeypatch):
  """When SIGTERM sets shutdown_requested, metrics processing should stop."""
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_mode", lambda: "strict_date")
  monkeypatch.setattr(
      update_metrics,
      "_proxy_reject_not_ready_jids",
      lambda jids: (set(), list(jids)),
  )
  monkeypatch.setattr(update_metrics, "run_with_db_retry", lambda func, **kwargs: func())
  monkeypatch.setattr(
      update_metrics,
      "_pg_session_statement_timeout_for_metrics_batch",
      contextlib.nullcontext,
  )
  monkeypatch.setattr(update_metrics, "_pg_local_readiness_timeouts", contextlib.nullcontext)
  monkeypatch.setattr(
      update_metrics,
      "_run_public_ef_artifacts_parallel_phase",
      lambda shared_pool, phase_timer: {
          "degraded": 0,
          "worker_exceptions": 0,
          "watchdog_timeouts": 0,
          "pending_tasks": 0,
          "tasks_completed": 0,
          "tasks_total": 0,
      },
  )
  monkeypatch.setattr(update_metrics, "_jobs_queryset", lambda *args, **kwargs: object())
  monkeypatch.setattr(
      update_metrics,
      "_iter_chunked_pks",
      lambda qs, chunk: iter([([101, 102], 2), ([103], 3)]),
  )
  monkeypatch.setattr(update_metrics, "close_old_connections", lambda: None)
  monkeypatch.setattr(update_metrics.gc, "collect", lambda: 0)
  _patch_strict_readiness_batch(monkeypatch, lambda jids: list(jids))

  update_metrics.shutdown_requested[0] = False
  seen = []

  class FakeMetrics:
    simple_metrics_list = {}
    complex_metrics_list = []

    def ensure_pool(self, pool_kind="metrics-pool"):
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
  def _pop_at_most_two(ready_queue, cap):
    out = []
    limit = min(int(cap), 2)
    while ready_queue and len(out) < limit:
      out.append(ready_queue.popleft())
    return out

  monkeypatch.setattr(
      update_metrics,
      "_pop_candidates_for_compute_batch_locked",
      _pop_at_most_two,
  )

  update_metrics.update_metrics(datetime(2025, 4, 10), rerun=False)
  assert seen == [[101, 102]]
  update_metrics.shutdown_requested[0] = False


def test_job_refs_from_jids_are_lightweight():
  """Chunk payload should only include jid-bearing lightweight objects."""
  refs = update_metrics._job_refs_from_jids([11, 22, 33])
  assert [r.jid for r in refs] == [11, 22, 33]
  assert all(not hasattr(r, "_state") for r in refs)


@pytest.mark.machine_unit_mock
def test_update_metrics_uses_lightweight_job_refs(monkeypatch):
  """update_metrics should not re-query job_data rows per chunk."""
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_mode", lambda: "global_fifo")
  monkeypatch.setattr(update_metrics, "_start_readiness_producer", _enqueue_chunks_from_date_states)
  monkeypatch.setattr(update_metrics, "persist_job_plot_artifacts_for_jid", lambda jid: None)
  monkeypatch.setattr(update_metrics, "_jobs_queryset", lambda *args, **kwargs: object())
  monkeypatch.setattr(
      update_metrics,
      "_iter_chunked_pks",
      lambda qs, chunk: iter([([101, 102], 2), ([103], 3)]),
  )
  monkeypatch.setattr(update_metrics, "close_old_connections", lambda: None)
  monkeypatch.setattr(update_metrics.gc, "collect", lambda: 0)
  _patch_strict_readiness_batch(monkeypatch, lambda jids: list(jids))

  # If a regression re-introduces ORM fetches, fail loudly.
  class _NoQueryManager:
    def filter(self, *args, **kwargs):
      raise AssertionError("update_metrics should not query job_data per chunk")

  monkeypatch.setattr(update_metrics.job_data, "objects", _NoQueryManager())

  seen = []

  class FakeMetrics:
    simple_metrics_list = {}
    complex_metrics_list = []

    def ensure_pool(self, pool_kind="metrics-pool"):
      return object()

    def close_pool(self):
      return None

    def run(self, jobs, pool=None):
      seen.append([j.jid for j in jobs])

  monkeypatch.setattr(update_metrics.metrics, "Metrics", lambda: FakeMetrics())
  monkeypatch.setattr(update_metrics, "DEBUG", False)

  update_metrics.update_metrics(datetime(2025, 4, 10), rerun=False)
  assert seen == [[101, 102, 103]]


@pytest.mark.machine_unit_mock
def test_update_metrics_skips_jobs_without_post_end_host_samples(monkeypatch):
  """Jobs without host latest sample strictly after end_time are skipped."""
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_mode", lambda: "global_fifo")
  monkeypatch.setattr(update_metrics, "_start_readiness_producer", _enqueue_chunks_from_date_states)
  monkeypatch.setattr(update_metrics, "persist_job_plot_artifacts_for_jid", lambda jid: None)
  monkeypatch.setattr(update_metrics, "_jobs_queryset", lambda *args, **kwargs: object())
  monkeypatch.setattr(
      update_metrics,
      "_iter_chunked_pks",
      lambda qs, chunk: iter([([101, 102, 103], 3)]),
  )
  monkeypatch.setattr(update_metrics, "close_old_connections", lambda: None)
  monkeypatch.setattr(update_metrics.gc, "collect", lambda: 0)
  _patch_strict_readiness_batch(
      monkeypatch,
      lambda jids: [jid for jid in jids if jid in (101, 103)],
  )

  seen = []

  class FakeMetrics:
    simple_metrics_list = {}
    complex_metrics_list = []

    def ensure_pool(self, pool_kind="metrics-pool"):
      return object()

    def close_pool(self):
      return None

    def run(self, jobs, pool=None):
      seen.append([j.jid for j in jobs])

  monkeypatch.setattr(update_metrics.metrics, "Metrics", lambda: FakeMetrics())
  monkeypatch.setattr(update_metrics, "DEBUG", False)

  update_metrics.update_metrics(datetime(2025, 4, 10), rerun=False)
  assert seen == [[101, 103]]


@pytest.mark.machine_unit_mock
def test_update_metrics_reuses_shared_pool_per_date(monkeypatch):
  """update_metrics should initialize one shared pool and reuse it per jid run."""
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_mode", lambda: "global_fifo")
  monkeypatch.setattr(update_metrics, "_start_readiness_producer", _enqueue_chunks_from_date_states)
  monkeypatch.setattr(update_metrics, "persist_job_plot_artifacts_for_jid", lambda jid: None)
  monkeypatch.setattr(update_metrics, "_jobs_queryset", lambda *args, **kwargs: object())
  monkeypatch.setattr(
      update_metrics,
      "_iter_chunked_pks",
      lambda qs, chunk: iter([([101, 102], 2), ([103], 3)]),
  )
  monkeypatch.setattr(update_metrics, "close_old_connections", lambda: None)
  monkeypatch.setattr(update_metrics.gc, "collect", lambda: 0)
  _patch_strict_readiness_batch(monkeypatch, lambda jids: list(jids))

  pool_token = object()
  pool_calls = []

  class FakeMetrics:
    simple_metrics_list = {}
    complex_metrics_list = []

    def ensure_pool(self, pool_kind="metrics-pool"):
      pool_calls.append("ensure")
      return pool_token

    def close_pool(self):
      pool_calls.append("close")

    def run(self, jobs, pool=None):
      pool_calls.append(pool)

  monkeypatch.setattr(update_metrics.metrics, "Metrics", lambda: FakeMetrics())
  monkeypatch.setattr(update_metrics, "DEBUG", False)

  update_metrics.update_metrics(datetime(2025, 4, 10), rerun=False)
  assert pool_calls == [
      "ensure",
      "ensure",
      "ensure",
      pool_token,
      "close",
  ]


@pytest.mark.machine_unit_mock
def test_window_coverage_ready_requires_start_and_end_margins_job_aggregate(monkeypatch):
  """Ready when in-window MIN/MAX meet start and end margins (job aggregate, any host)."""
  _patch_connections_vendor(monkeypatch, "sqlite")
  monkeypatch.setattr(
      update_metrics.cfg,
      "get_metrics_readiness_require_window_coverage",
      lambda: True,
  )
  start = datetime(2026, 6, 5, 22, 58, 35, tzinfo=timezone.utc)
  end = datetime(2026, 6, 6, 13, 39, 44, tzinfo=timezone.utc)
  jobs_rows = [
      {
          "jid": "101",
          "start_time": start,
          "end_time": end,
          "host_list": ["n1.example.org", "n2.example.org"],
      },
      {
          "jid": "102",
          "start_time": start,
          "end_time": end,
          "host_list": ["n3.example.org"],
      },
  ]
  window_rows = {
      "101": (
          datetime(2026, 6, 5, 23, 0, 0, tzinfo=timezone.utc),
          datetime(2026, 6, 6, 13, 39, 0, tzinfo=timezone.utc),
      ),
      "102": (
          datetime(2026, 6, 6, 4, 57, 32, tzinfo=timezone.utc),
          datetime(2026, 6, 6, 13, 39, 30, tzinfo=timezone.utc),
      ),
  }

  class _JobManager:
    def filter(self, **kwargs):
      class _Qs:
        def order_by(self, *args):
          return self

        def values(self, *fields):
          return jobs_rows
      return _Qs()

  def _fake_bounds(jobs):
    return {row["jid"]: window_rows[row["jid"]] for row in jobs}

  monkeypatch.setattr(update_metrics.job_data, "objects", _JobManager())
  monkeypatch.setattr(update_metrics, "_in_window_min_max_by_job_rows", _fake_bounds)

  ready = update_metrics._filter_jids_with_samples_after_end(["101", "102"])
  assert ready == ["101"]


@pytest.mark.machine_unit_mock
def test_ready_jids_batches_in_window_min_max_lookups(monkeypatch):
  """Window readiness queries host_data with time__gte/time__lte, not global max."""
  _patch_connections_vendor(monkeypatch, "sqlite")
  monkeypatch.setattr(
      update_metrics.cfg,
      "get_metrics_readiness_require_window_coverage",
      lambda: True,
  )
  monkeypatch.setattr(update_metrics, "HOST_LAST_TIME_LOOKUP_BATCH", 2)
  filter_batches = []
  time_filters = []
  start = datetime(2025, 4, 10, 11, 0, 0, tzinfo=timezone.utc)
  end = datetime(2025, 4, 10, 12, 0, 0, tzinfo=timezone.utc)

  class _HostManager:
    def filter(self, **kwargs):
      filter_batches.append(tuple(sorted(kwargs.get("host__in") or ())))
      time_filters.append(
          (kwargs.get("time__gte"), kwargs.get("time__lte"))
      )

      class _Agg:
        def values(self, *_names, **_kw):
          return self

        def annotate(self, **_ann):
          return self

        def __iter__(self):
          for host in kwargs.get("host__in") or ():
            yield {
                "host": host,
                "mn": start + timedelta(minutes=5),
                "mx": end - timedelta(minutes=5),
            }

      return _Agg()

  monkeypatch.setattr(update_metrics, "_host_name_suffix", lambda: ".example.org")
  monkeypatch.setattr(update_metrics.host_data, "objects", _HostManager())

  jobs = [
      {
          "jid": "1",
          "start_time": start,
          "end_time": end,
          "host_list": ["n1", "n2", "n3"],
      },
  ]
  ready = update_metrics._ready_jids_from_job_rows(jobs)
  assert ready == ["1"]
  assert filter_batches == [("n1.example.org", "n2.example.org"), ("n3.example.org",)]
  assert all(tf == (start, end) for tf in time_filters)


@pytest.mark.machine_unit_mock
def test_latest_sample_time_by_host_postgresql_uses_lateral_unnest(monkeypatch):
  """PostgreSQL path uses LATERAL LIMIT 1 per host (not DISTINCT ON) and batches."""
  from hpcperfstats.site.lib.machine import host_data_latest

  exec_log = []
  monkeypatch.setattr(
      host_data_latest.transaction,
      "atomic",
      lambda using=None: contextlib.nullcontext(),
  )
  monkeypatch.setattr(host_data_latest, "HOST_LAST_TIME_LOOKUP_BATCH", 2)

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
  monkeypatch.setattr(host_data_latest, "connections", handler)

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
  batches = []

  class _M:
    def ensure_pool(self, pool_kind="metrics-pool"):
      return None

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
def test_compute_jid_outcomes_batch_always_submits_prewarm(monkeypatch):
  """Successful jids always get plot prewarm submit (no skip-prewarm escape hatch)."""
  class _M:
    def ensure_pool(self, pool_kind="metrics-pool"):
      return None

    def run(self, job_refs, pool=None):
      del pool
      return [{
          "jid": ref.jid,
          "ok": True,
          "status": "ok",
          "error_type": None,
          "error_message": None,
          "persist_s": 0.0,
      } for ref in job_refs]

  pipe = MagicMock()
  pipe.has_pending.return_value = False
  out = update_metrics._compute_jid_outcomes_batch(
      [SimpleNamespace(jid="only", artifact_only=False)],
      _M(),
      pipe,
      None,
  )
  pipe.submit.assert_called_once_with("only")
  assert [d["jid"] for d in out] == ["only"]
  assert out[0]["ok"] is True


@pytest.mark.machine_unit_mock
def test_compute_jid_outcomes_batch_artifact_only_submits_prewarm(monkeypatch):
  """artifact_only refs get prewarm submit without Metrics.run."""
  class _M:
    def ensure_pool(self, pool_kind="metrics-pool"):
      return None

    def run(self, job_refs, pool=None):
      raise AssertionError("artifact_only must not call Metrics.run")

  pipe = MagicMock()
  pipe.has_pending.return_value = False
  out = update_metrics._compute_jid_outcomes_batch(
      [SimpleNamespace(jid="art-only", artifact_only=True)],
      _M(),
      pipe,
      None,
  )
  pipe.submit.assert_called_once_with("art-only")
  assert out[0]["ok"] is True
  assert out[0]["jid"] == "art-only"


@pytest.mark.machine_unit_mock
def test_compute_jid_outcomes_batch_prewarm_submits_each_jid(monkeypatch):
  class _M:
    def ensure_pool(self, pool_kind="metrics-pool"):
      return None

    def run(self, job_refs, pool=None):
      del pool
      return [{
          "jid": ref.jid,
          "ok": True,
          "status": "ok",
          "error_type": None,
          "error_message": None,
          "persist_s": 0.0,
      } for ref in job_refs]

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
def test_compute_jid_outcomes_batch_skips_prewarm_for_explicit_failed_outcomes(monkeypatch):
  class _M:
    def ensure_pool(self, pool_kind="metrics-pool"):
      return None

    def run(self, job_refs, pool=None):
      del pool
      return [
          {
              "jid": "j1",
              "ok": True,
              "status": "ok",
              "error_type": None,
              "error_message": None,
              "persist_s": 0.0,
          },
          {
              "jid": "j2",
              "ok": False,
              "status": "worker_db_error",
              "error_type": "OperationalError",
              "error_message": "lost synchronization with server",
              "persist_s": 0.0,
          },
          {
              "jid": "j3",
              "ok": False,
              "status": "parent_persist_timeout",
              "error_type": "DatabaseError",
              "error_message": "statement timeout",
              "persist_s": 12.5,
          },
      ]

  pipe = MagicMock()
  pipe.has_pending.return_value = False
  out = update_metrics._compute_jid_outcomes_batch(
      [SimpleNamespace(jid="j1"), SimpleNamespace(jid="j2"), SimpleNamespace(jid="j3")],
      _M(),
      pipe,
      None,
  )
  assert [c.args[0] for c in pipe.submit.call_args_list] == ["j1"]
  by_jid = {d["jid"]: d for d in out}
  assert by_jid["j1"]["ok"] is True
  assert by_jid["j2"]["ok"] is False
  assert by_jid["j2"]["failure_kind"] == "worker_db_error"
  assert by_jid["j3"]["ok"] is False
  assert by_jid["j3"]["failure_kind"] == "parent_persist_timeout"
  assert by_jid["j3"]["persist_s"] == 12.5


@pytest.mark.machine_unit_mock


@pytest.mark.machine_unit_mock
def test_pop_candidates_respects_max_window_seconds(monkeypatch):
  """Greedy dequeue splits long-window jobs across batches when window cap is on."""
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_compute_batch_max_window_s", lambda: 100.0)
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_compute_batch_max_single_job_s", lambda: 0.0)
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_compute_batch_unknown_runtime_s", lambda: 1.0)
  q = deque([
      update_metrics._candidate_ref("a", False, runtime_s=60.0),
      update_metrics._candidate_ref("b", False, runtime_s=60.0),
      update_metrics._candidate_ref("c", False, runtime_s=10.0),
  ])
  first = update_metrics._pop_candidates_for_compute_batch_locked(q, 8)
  assert [r.jid for r in first] == ["a"]
  second = update_metrics._pop_candidates_for_compute_batch_locked(q, 8)
  assert [r.jid for r in second] == ["b", "c"]
  assert len(q) == 0


@pytest.mark.machine_unit_mock


@pytest.mark.machine_unit_mock
def test_compute_jid_outcomes_batch_skips_metrics_run_for_artifact_only_candidates(monkeypatch):
  calls = []

  class _M:
    def ensure_pool(self, pool_kind="metrics-pool"):
      return None

    def run(self, job_refs, pool=None):
      del pool
      calls.append([(ref.jid, bool(getattr(ref, "artifact_only", False))) for ref in job_refs])
      return [{
          "jid": "metrics-jid",
          "ok": True,
          "status": "ok",
          "error_type": None,
          "error_message": None,
          "persist_s": 0.0,
      }]

  class _Pipe:
    def __init__(self):
      self.submitted = []

    def submit(self, jid):
      self.submitted.append(jid)

    def has_pending(self):
      return False

    def drain_some(self, force=False, wait_timeout_s=0.0):
      del force, wait_timeout_s

  pipe = _Pipe()
  out = update_metrics._compute_jid_outcomes_batch(
      [
          SimpleNamespace(jid="artifact-jid", artifact_only=True),
          SimpleNamespace(jid="metrics-jid", artifact_only=False),
      ],
      _M(),
      pipe,
      None,
  )

  assert calls == [[("metrics-jid", False)]]
  assert pipe.submitted == ["artifact-jid", "metrics-jid"]
  by_jid = {row["jid"]: row for row in out}
  assert by_jid["artifact-jid"]["ok"] is True
  assert by_jid["artifact-jid"]["metrics_s"] == 0.0
  assert by_jid["metrics-jid"]["ok"] is True
  assert by_jid["metrics-jid"]["metrics_s"] >= 0.0


@pytest.mark.machine_unit_mock
def test_compute_jid_outcomes_batch_falls_back_per_jid_after_batch_failure(monkeypatch):
  calls = []

  class _M:
    def ensure_pool(self, pool_kind="metrics-pool"):
      return None

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
  assert any(d.get("_batch_exception") for d in out)
  assert sum(1 for d in out if d.get("_fallback_failed")) == 1


@pytest.mark.machine_unit_mock
def test_compute_jid_outcomes_batch_stall_recovery_budget_marks_remaining_failed(monkeypatch):
  monkeypatch.setattr(update_metrics, "STALL_RECOVERY_MAX_WALL_SECONDS", 0.0)
  calls = []

  class _M:
    def ensure_pool(self, pool_kind="metrics-pool"):
      return None

    def run(self, job_refs, pool=None):
      del pool
      calls.append([r.jid for r in job_refs])
      if len(job_refs) > 1:
        raise update_metrics.metrics.MetricsRunWorkerStallError(
            stalled_for_s=601.0,
            message="stall",
            pool_reset_confirmed=True,
        )

  job_refs = [
      SimpleNamespace(jid="j1"),
      SimpleNamespace(jid="j2"),
      SimpleNamespace(jid="j3"),
  ]
  out = update_metrics._compute_jid_outcomes_batch(
      job_refs,
      _M(),
      MagicMock(),
      None,
  )
  # Batch attempt happens once; no per-jid retries after immediate budget exhaustion.
  assert calls == [["j1", "j2", "j3"]]
  assert [d["jid"] for d in out] == ["j1", "j2", "j3"]
  assert all(not d["ok"] for d in out)
  assert all(d.get("_batch_exception") for d in out)
  assert sum(1 for d in out if d.get("_fallback_failed")) == 3


@pytest.mark.machine_unit_mock
def test_temporary_metrics_run_timeouts_restores_env(monkeypatch):
  poll_key = "HPCPERFSTATS_METRICS_RUN_POLL_TIMEOUT_S"
  stall_key = "HPCPERFSTATS_METRICS_RUN_STALL_TIMEOUT_S"
  monkeypatch.setenv(poll_key, "9")
  monkeypatch.setenv(stall_key, "99")
  with update_metrics._temporary_metrics_run_timeouts(
      poll_timeout_s=1.5,
      stall_timeout_s=45.0,
  ):
    assert os.environ[poll_key] == "1.5"
    assert os.environ[stall_key] == "45.0"
  assert os.environ[poll_key] == "9"
  assert os.environ[stall_key] == "99"


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
  monkeypatch.setattr(
      update_metrics.cfg,
      "get_metrics_readiness_require_window_coverage",
      lambda: False,
  )
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
  monkeypatch.setattr(
      update_metrics.cfg,
      "get_metrics_readiness_require_window_coverage",
      lambda: False,
  )
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
def test_proxy_reject_legacy_end_by_jid_not_double_consumed(monkeypatch):
  """Legacy proxy path must not re-iterate exhausted job_rows for end_time lookup."""
  _patch_connections_vendor(monkeypatch, "postgresql")
  monkeypatch.setattr(
      update_metrics.cfg,
      "get_metrics_readiness_require_window_coverage",
      lambda: False,
  )
  t_end = datetime(2025, 4, 1, 12, 0, 0, tzinfo=timezone.utc)
  t_after = datetime(2025, 4, 1, 14, 0, 0, tzinfo=timezone.utc)
  job_values_calls = []

  def host_filter(*_a, jid__in=None, **_k):
    class _HostAgg:
      def values(self, *_names, **_kw):
        return self

      def annotate(self, **_kw):
        return self

      def __iter__(self):
        for j in jid__in:
          yield {"jid": j, "max_time": t_after if j == "j1" else t_end}

    return _HostAgg()

  def job_filter(*_a, jid__in=None, **_k):
    class _JobVals:
      def values(self, *_names, **_kw):
        job_values_calls.append(tuple(jid__in))
        for j in jid__in:
          yield {"jid": j, "end_time": t_end}

    return _JobVals()

  monkeypatch.setattr(update_metrics.host_data.objects, "filter", host_filter)
  monkeypatch.setattr(update_metrics.job_data.objects, "filter", job_filter)

  reject, unknown = update_metrics._proxy_reject_not_ready_jids(["j1", "j2"])
  assert job_values_calls == [("j1", "j2")]
  assert reject == {"j2"}
  assert unknown == ["j1"]


@pytest.mark.machine_unit_mock
def test_proxy_window_coverage_reject_buckets_match_singleton(monkeypatch):
  """Coverage proxy bulk classification matches host_list window bounds helper."""
  _patch_connections_vendor(monkeypatch, "postgresql")
  monkeypatch.setattr(
      update_metrics.cfg,
      "get_metrics_readiness_require_window_coverage",
      lambda: True,
  )
  start = datetime(2025, 4, 1, 10, 0, 0, tzinfo=timezone.utc)
  end = datetime(2025, 4, 1, 12, 0, 0, tzinfo=timezone.utc)
  first_ok = start + timedelta(minutes=5)
  last_ok = end - timedelta(minutes=5)
  first_bad = start + timedelta(hours=1)
  host = "n1.example.org"

  bounds_by_jid = {
      "ok": (first_ok, last_ok),
      "bad_start": (first_bad, last_ok),
      "no_rows": (None, None),
  }

  monkeypatch.setattr(
      update_metrics,
      "_in_window_min_max_by_job_rows",
      lambda rows: {r["jid"]: bounds_by_jid.get(r["jid"], (None, None)) for r in rows},
  )
  monkeypatch.setattr(
      update_metrics,
      "_in_window_min_max_for_hosts",
      lambda hosts, st, et: bounds_by_jid.get("ok", (None, None))
      if hosts else (None, None),
  )

  def job_filter(*_a, jid=None, jid__in=None, **_k):
    if jid is not None:
      rows = [{
          "jid": jid,
          "start_time": start,
          "end_time": end,
          "host_list": [host],
      }]
    else:
      rows = [
          {
              "jid": j,
              "start_time": start,
              "end_time": end,
              "host_list": [host],
          }
          for j in (jid__in or [])
      ]

    class _JobVals:
      def __init__(self, job_rows):
        self._rows = job_rows

      def values(self, *_names, **_kw):
        return self

      def first(self):
        return self._rows[0] if self._rows else None

      def __iter__(self):
        for row in self._rows:
          yield row

    return _JobVals(rows)

  monkeypatch.setattr(update_metrics.job_data.objects, "filter", job_filter)

  for jid, expected in (
      ("ok", "unknown"),
      ("bad_start", "reject"),
      ("no_rows", "unknown"),
  ):
    monkeypatch.setattr(
        update_metrics,
        "_in_window_min_max_for_hosts",
        lambda hosts, st, et, j=jid: bounds_by_jid[j],
    )
    assert update_metrics._proxy_readiness_for_jid(jid) == expected

  reject, unknown = update_metrics._proxy_reject_not_ready_jids(
      ["ok", "bad_start", "no_rows"]
  )
  assert reject == {"bad_start"}
  assert unknown == ["ok", "no_rows"]


@pytest.mark.machine_unit_mock
def test_proxy_readiness_for_jid_matches_bulk_singletons(monkeypatch):
  """Single-jid proxy helper matches one-element bulk classification."""
  _patch_connections_vendor(monkeypatch, "postgresql")
  monkeypatch.setattr(
      update_metrics.cfg,
      "get_metrics_readiness_require_window_coverage",
      lambda: False,
  )
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


@pytest.mark.machine_unit_mock
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
  _patch_strict_readiness_batch(monkeypatch, _strict)
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
  assert _ready_queue_jids(ready) == ["j1"]
  assert strict_calls == [["j1", "j2", "j3"]]
  assert _ready_queue_jids(states[0]["pending_tail"]) == ["j2", "j3"]
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


@pytest.mark.machine_unit_mock
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

  _patch_strict_readiness_batch(monkeypatch, _strict)
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

  assert "j3" in _ready_queue_jids(ready)
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
  monkeypatch.setattr(
      update_metrics.cfg,
      "get_metrics_readiness_require_window_coverage",
      lambda: False,
  )
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
  _patch_strict_readiness_batch(monkeypatch, lambda jids: list(jids))
  monkeypatch.setattr(update_metrics, "_proxy_readiness_for_jid", lambda jid: "unknown")
  monkeypatch.setattr(
      update_metrics,
      "_proxy_reject_not_ready_jids",
      lambda jids: (set(), list(jids)),
  )
  monkeypatch.setattr(update_metrics.gc, "collect", lambda: 0)
  monkeypatch.setattr(update_metrics, "shutdown_requested", [False])
  monkeypatch.setattr(
      update_metrics.cfg,
      "get_metrics_readiness_require_window_coverage",
      lambda: False,
  )

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

    def ensure_pool(self, pool_kind="metrics-pool"):
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
  """Readiness may drop every jid in a chunk; stall exit when none become ready."""
  d1 = datetime(2025, 4, 10)
  d2 = datetime(2025, 4, 9)
  monkeypatch.delenv("HPCPERFSTATS_METRICS_SCHEDULER_MODE", raising=False)
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_mode", lambda: "global_fifo")
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_prefetch_chunks", lambda: 2)
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_ready_queue_target", lambda: 8)
  monkeypatch.setattr(update_metrics, "close_old_connections", lambda: None)
  monkeypatch.setattr(update_metrics, "_pg_session_statement_timeout_for_metrics_batch", contextlib.nullcontext)
  _patch_strict_readiness_batch(monkeypatch, lambda jids: [])
  monkeypatch.setattr(update_metrics, "_proxy_reject_not_ready_jids", lambda jids: (set(), list(jids)))
  monkeypatch.setattr(update_metrics, "STALL_EXIT_AFTER_SECONDS", 0.1)
  monkeypatch.setattr(update_metrics.gc, "collect", lambda: 0)
  monkeypatch.setattr(update_metrics, "shutdown_requested", [False])
  monkeypatch.setattr(
      update_metrics.cfg,
      "get_metrics_readiness_require_window_coverage",
      lambda: False,
  )
  monkeypatch.setattr(update_metrics, "_start_candidate_rescan_thread", lambda **kwargs: None)

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

    def ensure_pool(self, pool_kind="metrics-pool"):
      return object()

    def close_pool(self):
      return None

    def run(self, jobs, pool=None):
      raise AssertionError("metrics run should not run when readiness filters all jids")

  monkeypatch.setattr(update_metrics.metrics, "Metrics", lambda: FakeMetrics())
  monkeypatch.setattr(update_metrics, "persist_job_plot_artifacts_for_jid", lambda jid: None)
  with pytest.raises(update_metrics.MetricsSchedulerStallExit) as excinfo:
    update_metrics.update_metrics_for_dates([d1, d2], rerun=False)
  assert excinfo.value.stall_reason == "no_ready_candidates"


@pytest.mark.machine_unit_mock
@pytest.mark.django_db(databases=[])
def test_update_metrics_exits_on_compute_all_failed(monkeypatch):
  """All compute failures with zero processed must trigger stall exit (Option B)."""
  monkeypatch.setenv("HPCPERFSTATS_UPDATE_METRICS_RETURN_DIAGNOSTICS", "1")
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_mode", lambda: "global_fifo")
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_prefetch_chunks", lambda: 1)
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_ready_queue_target", lambda: 4)
  monkeypatch.setattr(update_metrics, "close_old_connections", lambda: None)
  monkeypatch.setattr(update_metrics, "_pg_session_statement_timeout_for_metrics_batch", contextlib.nullcontext)
  _patch_strict_readiness_batch(monkeypatch, lambda jids: list(jids))
  monkeypatch.setattr(update_metrics, "_proxy_reject_not_ready_jids", lambda jids: (set(), list(jids)))
  monkeypatch.setattr(update_metrics, "STALL_EXIT_AFTER_SECONDS", 0.1)
  monkeypatch.setattr(update_metrics.gc, "collect", lambda: 0)
  monkeypatch.setattr(update_metrics, "shutdown_requested", [False])
  monkeypatch.setattr(
      update_metrics.cfg,
      "get_metrics_readiness_require_window_coverage",
      lambda: False,
  )

  class _FakeQs:
    def __init__(self, chunks):
      self.chunks = chunks

  monkeypatch.setattr(
      update_metrics,
      "_jobs_queryset",
      lambda d, *_args, **_kwargs: _FakeQs([([1001, 1002], 2)]),
  )
  monkeypatch.setattr(update_metrics, "_iter_chunked_pks", lambda qs, _chunk: iter(qs.chunks))
  monkeypatch.setattr(update_metrics, "_start_candidate_rescan_thread", lambda **kwargs: None)

  class FakeMetrics:
    simple_metrics_list = {}
    complex_metrics_list = []

    def ensure_pool(self, pool_kind="metrics-pool"):
      return object()

    def close_pool(self):
      return None

    def run(self, jobs, pool=None):
      del pool
      raise RuntimeError("compute failure")

  monkeypatch.setattr(update_metrics.metrics, "Metrics", lambda: FakeMetrics())
  monkeypatch.setattr(update_metrics, "persist_job_plot_artifacts_for_jid", lambda jid: None)
  update_metrics.LAST_UPDATE_METRICS_DIAGNOSTICS = None
  with pytest.raises(update_metrics.MetricsSchedulerStallExit) as excinfo:
    update_metrics.update_metrics_for_dates([datetime(2025, 4, 10)], rerun=False)
  assert excinfo.value.stall_reason == "compute_all_failed"
  diag = update_metrics.LAST_UPDATE_METRICS_DIAGNOSTICS
  assert diag is not None
  assert diag["stats"]["stall_exit_triggered"] == 1
  assert diag["stats"]["attempted_total"] == 2


@pytest.mark.django_db(databases=[])
def test_update_metrics_stall_reason_doc_drift_guard():
  """Lock documented stall_reason strings to code constants (docs/TESTING.md)."""
  expected = frozenset({
      "no_ready_candidates",
      "compute_stuck_inflight",
      "compute_all_failed",
  })
  assert update_metrics.DOCUMENTED_SCHEDULER_STALL_REASONS == expected
  assert update_metrics.CONSUMER_STALL_EXIT_REASONS <= expected | frozenset({
      "worker_failed_outcomes",
      "parent_persist_failed",
  })


@pytest.mark.django_db(databases=[])
def test_update_metrics_for_dates_sets_stall_diagnostics_on_no_progress(monkeypatch):
  """Persistent not-ready loops should terminate with explicit stall diagnostics."""
  monkeypatch.setenv("HPCPERFSTATS_UPDATE_METRICS_RETURN_DIAGNOSTICS", "1")
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_mode", lambda: "global_fifo")
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_prefetch_chunks", lambda: 1)
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_ready_queue_target", lambda: 4)
  monkeypatch.setattr(update_metrics, "close_old_connections", lambda: None)
  monkeypatch.setattr(update_metrics, "_pg_session_statement_timeout_for_metrics_batch", contextlib.nullcontext)
  _patch_strict_readiness_batch(monkeypatch, lambda jids: [])
  monkeypatch.setattr(update_metrics, "_proxy_reject_not_ready_jids", lambda jids: (set(), list(jids)))
  monkeypatch.setattr(update_metrics, "STALL_EXIT_AFTER_SECONDS", 0.1)
  monkeypatch.setattr(update_metrics.gc, "collect", lambda: 0)
  monkeypatch.setattr(update_metrics, "shutdown_requested", [False])
  monkeypatch.setattr(
      update_metrics.cfg,
      "get_metrics_readiness_require_window_coverage",
      lambda: False,
  )

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

    def ensure_pool(self, pool_kind="metrics-pool"):
      return object()

    def close_pool(self):
      return None

    def run(self, jobs, pool=None):
      raise AssertionError("metrics run should not execute when all jobs are not-ready")

  monkeypatch.setattr(update_metrics.metrics, "Metrics", lambda: FakeMetrics())
  monkeypatch.setattr(update_metrics, "persist_job_plot_artifacts_for_jid", lambda jid: None)
  update_metrics.LAST_UPDATE_METRICS_DIAGNOSTICS = None
  with pytest.raises(update_metrics.MetricsSchedulerStallExit) as excinfo:
    update_metrics.update_metrics_for_dates([datetime(2025, 4, 10)], rerun=False)
  assert excinfo.value.stall_reason == "no_ready_candidates"
  diag = update_metrics.LAST_UPDATE_METRICS_DIAGNOSTICS
  assert diag is not None
  assert diag["stats"]["stall_exit_triggered"] == 1
  assert diag["stats"]["strict_not_ready_jids"] >= 1
  assert diag["stats"]["stall_reason"] == "no_ready_candidates"


@pytest.mark.machine_unit_mock
def test_pg_session_statement_timeout_restore_swallows_closed_connection(monkeypatch):
  """Closed connection during timeout restore must not raise."""
  monkeypatch.setattr(
      update_metrics,
      "_pg_session_statement_timeout_for_metrics_batch",
      _PG_SESSION_TIMEOUT_CM,
  )
  monkeypatch.setattr(update_metrics.cfg, "get_db_statement_timeout_ms", lambda: 120000)

  class FakeCursor:
    def __enter__(self):
      return self

    def __exit__(self, *args, **kwargs):
      return False

    def execute(self, sql, params=None):
      if params is not None and "statement_timeout" in sql:
        raise OperationalError("the connection is closed")

  fake_conn = MagicMock()
  fake_conn.vendor = "postgresql"
  fake_conn.cursor = lambda: FakeCursor()
  handler = MagicMock()
  handler.__getitem__ = lambda self, name: fake_conn
  monkeypatch.setattr(update_metrics, "connections", handler)

  with update_metrics._pg_session_statement_timeout_for_metrics_batch():
    pass


@pytest.mark.django_db(databases=[])
def test_stall_exit_survives_closed_connection_on_timeout_restore(monkeypatch):
  """Stall exit must propagate when statement_timeout restore hits a dead connection."""
  monkeypatch.setenv("HPCPERFSTATS_UPDATE_METRICS_RETURN_DIAGNOSTICS", "1")
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_mode", lambda: "global_fifo")
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_prefetch_chunks", lambda: 1)
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_ready_queue_target", lambda: 4)
  monkeypatch.setattr(update_metrics, "close_old_connections", lambda: None)
  monkeypatch.setattr(
      update_metrics,
      "_pg_session_statement_timeout_for_metrics_batch",
      _PG_SESSION_TIMEOUT_CM,
  )
  monkeypatch.setattr(update_metrics.cfg, "get_db_statement_timeout_ms", lambda: 120000)
  _patch_strict_readiness_batch(monkeypatch, lambda jids: [])
  monkeypatch.setattr(update_metrics, "_proxy_reject_not_ready_jids", lambda jids: (set(), list(jids)))
  monkeypatch.setattr(update_metrics, "STALL_EXIT_AFTER_SECONDS", 0.1)
  monkeypatch.setattr(update_metrics.gc, "collect", lambda: 0)
  monkeypatch.setattr(update_metrics, "shutdown_requested", [False])
  monkeypatch.setattr(
      update_metrics.cfg,
      "get_metrics_readiness_require_window_coverage",
      lambda: False,
  )

  class FakeCursor:
    def __enter__(self):
      return self

    def __exit__(self, *args, **kwargs):
      return False

    def execute(self, sql, params=None):
      if params is not None and "statement_timeout" in sql:
        raise OperationalError("the connection is closed")

  fake_conn = MagicMock()
  fake_conn.vendor = "postgresql"
  fake_conn.cursor = lambda: FakeCursor()
  handler = MagicMock()
  handler.__getitem__ = lambda self, name: fake_conn
  monkeypatch.setattr(update_metrics, "connections", handler)

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

    def ensure_pool(self, pool_kind="metrics-pool"):
      return object()

    def close_pool(self):
      return None

    def run(self, jobs, pool=None):
      raise AssertionError("metrics run should not execute when all jobs are not-ready")

  monkeypatch.setattr(update_metrics.metrics, "Metrics", lambda: FakeMetrics())
  monkeypatch.setattr(update_metrics, "persist_job_plot_artifacts_for_jid", lambda jid: None)
  update_metrics.LAST_UPDATE_METRICS_DIAGNOSTICS = None
  with pytest.raises(update_metrics.MetricsSchedulerStallExit) as excinfo:
    update_metrics.update_metrics_for_dates([datetime(2025, 4, 10)], rerun=False)
  assert excinfo.value.stall_reason == "no_ready_candidates"


@pytest.mark.django_db(databases=[])
def test_update_metrics_for_dates_records_queue_and_attempt_counters(monkeypatch):
  """Scheduler diagnostics should expose enqueue/dequeue/inflight and attempt progress."""
  monkeypatch.setenv("HPCPERFSTATS_UPDATE_METRICS_RETURN_DIAGNOSTICS", "1")
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_mode", lambda: "global_fifo")
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_prefetch_chunks", lambda: 1)
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_ready_queue_target", lambda: 4)
  monkeypatch.setattr(update_metrics, "close_old_connections", lambda: None)
  monkeypatch.setattr(update_metrics, "_pg_session_statement_timeout_for_metrics_batch", contextlib.nullcontext)
  _patch_strict_readiness_batch(monkeypatch, lambda jids: list(jids))
  monkeypatch.setattr(update_metrics, "_proxy_reject_not_ready_jids", lambda jids: (set(), list(jids)))
  monkeypatch.setattr(update_metrics.gc, "collect", lambda: 0)
  monkeypatch.setattr(update_metrics, "shutdown_requested", [False])

  class _FakeQs:
    def __init__(self, chunks):
      self.chunks = chunks

  monkeypatch.setattr(
      update_metrics,
      "_jobs_queryset",
      lambda d, *_args, **_kwargs: _FakeQs([([1001, 1002], 2)]),
  )
  monkeypatch.setattr(update_metrics, "_iter_chunked_pks", lambda qs, _chunk: iter(qs.chunks))
  monkeypatch.setattr(update_metrics, "_start_candidate_rescan_thread", lambda **kwargs: None)

  class FakeMetrics:
    simple_metrics_list = {}
    complex_metrics_list = []

    def ensure_pool(self, pool_kind="metrics-pool"):
      return object()

    def close_pool(self):
      return None

    def run(self, jobs, pool=None):
      del pool
      raise RuntimeError("compute failure")

  monkeypatch.setattr(update_metrics.metrics, "Metrics", lambda: FakeMetrics())
  monkeypatch.setattr(update_metrics, "persist_job_plot_artifacts_for_jid", lambda jid: None)
  update_metrics.LAST_UPDATE_METRICS_DIAGNOSTICS = None
  monkeypatch.setattr(update_metrics, "STALL_EXIT_AFTER_SECONDS", 0.1)
  monkeypatch.setattr(
      update_metrics.cfg,
      "get_metrics_readiness_require_window_coverage",
      lambda: False,
  )
  with pytest.raises(update_metrics.MetricsSchedulerStallExit) as excinfo:
    update_metrics.update_metrics_for_dates([datetime(2025, 4, 10)], rerun=False)
  assert excinfo.value.stall_reason == "compute_all_failed"
  diag = update_metrics.LAST_UPDATE_METRICS_DIAGNOSTICS
  assert diag is not None
  assert diag["stats"]["ready_enqueued_total"] == 2
  assert diag["stats"]["ready_dequeued_total"] == 2
  assert diag["stats"]["inflight_jids"] == 0
  assert diag["stats"]["attempted_total"] == 2
  assert diag["stats"]["batch_compute_exceptions_total"] >= 1
  assert diag["stats"]["per_jid_fallback_failures_total"] == 2
  assert diag["stats"]["stall_reason"] == "compute_all_failed"
  assert diag["stats"]["stall_exit_triggered"] == 1


@pytest.mark.machine_unit_mock
def test_update_metrics_for_dates_records_explicit_worker_and_parent_persist_failures(monkeypatch):
  """Scheduler diagnostics should track explicit worker and parent-persist failed outcomes."""
  monkeypatch.setenv("HPCPERFSTATS_UPDATE_METRICS_RETURN_DIAGNOSTICS", "1")
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_mode", lambda: "global_fifo")
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_prefetch_chunks", lambda: 1)
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_ready_queue_target", lambda: 4)
  monkeypatch.setattr(update_metrics, "close_old_connections", lambda: None)
  monkeypatch.setattr(update_metrics, "_pg_session_statement_timeout_for_metrics_batch", contextlib.nullcontext)
  _patch_strict_readiness_batch(monkeypatch, lambda jids: list(jids))
  monkeypatch.setattr(update_metrics, "_proxy_reject_not_ready_jids", lambda jids: (set(), list(jids)))
  monkeypatch.setattr(update_metrics.gc, "collect", lambda: 0)
  monkeypatch.setattr(update_metrics, "shutdown_requested", [False])
  monkeypatch.setattr(update_metrics, "_start_candidate_rescan_thread", lambda **kwargs: None)
  monkeypatch.setattr(
      update_metrics,
      "refresh_public_expansion_factor_artifacts_parallel",
      lambda pool, **kwargs: {},
  )
  monkeypatch.setattr(update_metrics, "refresh_public_expansion_factor_artifacts_safe", lambda: None)

  class _FakeQs:
    def __init__(self, chunks):
      self.chunks = chunks

  monkeypatch.setattr(
      update_metrics,
      "_jobs_queryset",
      lambda d, *_args, **_kwargs: _FakeQs([([1001, 1002, 1003], 3)]),
  )
  monkeypatch.setattr(update_metrics, "_iter_chunked_pks", lambda qs, _chunk: iter(qs.chunks))

  class _DoneProducer:
    def join(self, timeout=None):
      del timeout

  def _producer_stub(**kwargs):
    with kwargs["ready_queue_lock"]:
      kwargs["ready_queue"].extend([1001, 1002, 1003])
    kwargs["producer_done"].set()
    return _DoneProducer()

  monkeypatch.setattr(update_metrics, "_start_readiness_producer", _producer_stub)

  class FakeMetrics:
    simple_metrics_list = {}
    complex_metrics_list = []

    def ensure_pool(self, pool_kind="metrics-pool"):
      return object()

    def close_pool(self):
      return None

    def run(self, jobs, pool=None):
      del pool
      return [
          {
              "jid": 1001,
              "ok": True,
              "status": "ok",
              "error_type": None,
              "error_message": None,
              "persist_s": 0.0,
          },
          {
              "jid": 1002,
              "ok": False,
              "status": "worker_db_error",
              "error_type": "OperationalError",
              "error_message": "lost synchronization with server",
              "persist_s": 0.0,
          },
          {
              "jid": 1003,
              "ok": False,
              "status": "parent_persist_timeout",
              "error_type": "DatabaseError",
              "error_message": "canceling statement due to statement timeout",
              "persist_s": 7.5,
          },
      ]

  prewarmed = []
  monkeypatch.setattr(update_metrics.metrics, "Metrics", lambda: FakeMetrics())
  monkeypatch.setattr(update_metrics, "persist_job_plot_artifacts_for_jid", lambda jid: prewarmed.append(jid))
  monkeypatch.setattr(update_metrics, "persist_job_detail_artifacts_for_jid", lambda jid: None)
  update_metrics.LAST_UPDATE_METRICS_DIAGNOSTICS = None
  update_metrics.update_metrics_for_dates([datetime(2025, 4, 10)], rerun=False)
  diag = update_metrics.LAST_UPDATE_METRICS_DIAGNOSTICS
  assert diag is not None
  assert diag["stats"]["processed"] == 1
  assert diag["stats"]["failed"] == 2
  assert diag["stats"]["attempted_total"] == 3
  assert diag["stats"]["worker_failed_outcomes_total"] == 1
  assert diag["stats"]["parent_persist_failures_total"] == 1
  assert prewarmed == [1001]


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

  _patch_strict_readiness_batch(monkeypatch, _strict)
  monkeypatch.setattr(update_metrics.gc, "collect", lambda: 0)
  monkeypatch.setattr(update_metrics, "shutdown_requested", [False])
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_deferred_not_ready_retry_s", lambda: 0.0)

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

  class _DoneProducer:
    def join(self, timeout=None):
      del timeout

  def _producer_enqueue_initial(**kwargs):
    with kwargs["ready_queue_lock"]:
      kwargs["ready_queue"].append(update_metrics._candidate_ref(1001))

    def _enqueue_rescan_jid():
      time.sleep(0.15)
      with kwargs["ready_queue_lock"]:
        kwargs["ready_queue"].append(update_metrics._candidate_ref(2002))
      kwargs["producer_done"].set()

    threading.Thread(target=_enqueue_rescan_jid, daemon=True).start()
    return _DoneProducer()

  monkeypatch.setattr(update_metrics, "_start_readiness_producer", _producer_enqueue_initial)
  monkeypatch.setattr(
      update_metrics,
      "refresh_public_expansion_factor_artifacts_parallel",
      lambda pool, **kwargs: {},
  )

  seen_batches = []

  class FakeMetrics:
    simple_metrics_list = {}
    complex_metrics_list = []

    def ensure_pool(self, pool_kind="metrics-pool"):
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


@pytest.mark.machine_unit_mock
def test_update_metrics_refreshes_pub_dashboards_before_first_compute(monkeypatch):
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_mode", lambda: "global_fifo")
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_prefetch_chunks", lambda: 1)
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_ready_queue_target", lambda: 4)
  monkeypatch.setattr(update_metrics, "close_old_connections", lambda: None)
  monkeypatch.setattr(update_metrics, "_pg_session_statement_timeout_for_metrics_batch", contextlib.nullcontext)
  monkeypatch.setattr(update_metrics, "_proxy_readiness_for_jid", lambda jid: "unknown")
  monkeypatch.setattr(update_metrics, "_proxy_reject_not_ready_jids", lambda jids: (set(), list(jids)))
  _patch_strict_readiness_batch(monkeypatch, lambda jids: list(jids))
  monkeypatch.setattr(update_metrics.gc, "collect", lambda: 0)
  monkeypatch.setattr(update_metrics, "shutdown_requested", [False])
  monkeypatch.setattr(update_metrics, "_start_candidate_rescan_thread", lambda **kwargs: None)
  monkeypatch.setattr(update_metrics, "persist_job_plot_artifacts_for_jid", lambda jid: None)

  class _FakeQs:
    def __init__(self, chunks):
      self.chunks = chunks

  monkeypatch.setattr(
      update_metrics,
      "_jobs_queryset",
      lambda d, *_args, **_kwargs: _FakeQs([([1001], 1)]),
  )
  monkeypatch.setattr(update_metrics, "_iter_chunked_pks", lambda qs, _chunk: iter(qs.chunks))

  order = []
  monkeypatch.setattr(
      update_metrics,
      "refresh_public_expansion_factor_artifacts_parallel",
      lambda pool, **kwargs: order.append(("pub_parallel", pool)) or {},
  )
  monkeypatch.setattr(
      update_metrics,
      "refresh_public_expansion_factor_artifacts_safe",
      lambda: order.append("safe_final"),
  )

  class FakeMetrics:
    simple_metrics_list = {}
    complex_metrics_list = []

    def ensure_pool(self, pool_kind="metrics-pool"):
      return object()

    def close_pool(self):
      return None

    def run(self, jobs, pool=None):
      del pool
      order.append("compute")

  monkeypatch.setattr(update_metrics.metrics, "Metrics", lambda: FakeMetrics())
  class _DoneProducer:
    def join(self, timeout=None):
      del timeout

  def _producer_stub(**kwargs):
    order.append("producer_start")
    with kwargs["ready_queue_lock"]:
      kwargs["ready_queue"].append(1001)
    kwargs["producer_done"].set()
    return _DoneProducer()

  monkeypatch.setattr(update_metrics, "_start_readiness_producer", _producer_stub)
  update_metrics.update_metrics_for_dates([datetime(2025, 4, 10)], rerun=False)
  assert "producer_start" in order
  pub_i = next(i for i, x in enumerate(order) if isinstance(x, tuple) and x[0] == "pub_parallel")
  prod_i = order.index("producer_start")
  assert pub_i < prod_i
  assert "compute" in order
  assert order.index("compute") > prod_i
  assert order[-1] == "safe_final"


@pytest.mark.machine_unit_mock
def test_run_public_ef_artifacts_parallel_phase_logs_degraded_state(monkeypatch):
  logs = []
  monkeypatch.setattr(update_metrics, "log_print", lambda msg, **kwargs: logs.append(msg))
  monkeypatch.setattr(
      update_metrics,
      "refresh_public_expansion_factor_artifacts_parallel",
      lambda pool, **kwargs: {
          "degraded": 1,
          "worker_exceptions": 2,
          "watchdog_timeouts": 1,
          "pending_tasks": 3,
      },
  )

  stats = update_metrics._run_public_ef_artifacts_parallel_phase(
      shared_pool=object(),
      phase_timer=update_metrics._PhaseTimer(),
  )

  assert stats["degraded"] == 1
  assert any("/pub/ EF artifacts degraded" in msg for msg in logs)


@pytest.mark.machine_unit_mock
def test_rescan_thread_discovers_candidates_without_pub_refresh(monkeypatch):
  """Rescan only queries job candidates; /pub/ EF runs once before job compute."""
  monkeypatch.setattr(update_metrics, "close_old_connections", lambda: None)
  monkeypatch.setattr(update_metrics, "shutdown_requested", [False])
  monkeypatch.setattr(update_metrics, "RESCAN_INTERVAL_SECONDS", 0.0)
  events = []
  stop_event = threading.Event()

  class _FakeQs:
    def values_list(self, *args, **kwargs):
      return []

  def _jobs_queryset(*args, **kwargs):
    events.append("query")
    stop_event.set()
    return _FakeQs()

  monkeypatch.setattr(update_metrics, "_jobs_queryset", _jobs_queryset)
  thread = update_metrics._start_candidate_rescan_thread(
      dates=[datetime(2025, 4, 10)],
      min_time=300,
      rerun=False,
      rescan_candidate_jids=deque(),
      rescan_seen_jids=set(),
      rescan_seen_order=deque(),
      rescan_seen_cap=8,
      rescan_lock=threading.Lock(),
      stop_event=stop_event,
  )
  assert thread is not None
  thread.join(timeout=1.0)
  assert events == ["query"]


@pytest.mark.machine_unit_mock
def test_add_bounded_seen_jid_evicts_oldest_entries():
  seen = set()
  order = deque()
  assert update_metrics._add_bounded_seen_jid(seen, order, 101, cap=2)
  assert update_metrics._add_bounded_seen_jid(seen, order, 102, cap=2)
  assert update_metrics._add_bounded_seen_jid(seen, order, 103, cap=2)
  assert seen == {102, 103}
  assert list(order) == [102, 103]


@pytest.mark.machine_unit_mock


@pytest.mark.machine_unit_mock
def test_merge_deferred_retry_at_never_pushes_retry_later():
  now = 100.0
  existing = now + 2.0
  candidate_later = now + 10.0
  candidate_earlier = now + 1.0
  assert update_metrics._merge_deferred_retry_at(None, candidate_later) == candidate_later
  assert update_metrics._merge_deferred_retry_at(existing, candidate_later) == existing
  assert update_metrics._merge_deferred_retry_at(existing, candidate_earlier) == candidate_earlier


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
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_deferred_not_ready_retry_s", lambda: 0.0)
  monkeypatch.setattr(
      update_metrics.cfg,
      "get_metrics_readiness_require_window_coverage",
      lambda: False,
  )

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

  _patch_strict_readiness_batch(monkeypatch, _strict)

  seen = []

  class FakeMetrics:
    simple_metrics_list = {}
    complex_metrics_list = []

    def ensure_pool(self, pool_kind="metrics-pool"):
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
def test_update_metrics_pub_parallel_once_then_safe_in_finally(monkeypatch):
  """/pub/ EF uses the metrics pool once up front; sequential safe() only in shutdown."""
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_mode", lambda: "global_fifo")
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_prefetch_chunks", lambda: 1)
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_ready_queue_target", lambda: 4)
  monkeypatch.setattr(update_metrics, "close_old_connections", lambda: None)
  monkeypatch.setattr(update_metrics, "_pg_session_statement_timeout_for_metrics_batch", contextlib.nullcontext)
  monkeypatch.setattr(update_metrics, "_proxy_readiness_for_jid", lambda jid: "unknown")
  monkeypatch.setattr(update_metrics, "_proxy_reject_not_ready_jids", lambda jids: (set(), list(jids)))
  _patch_strict_readiness_batch(monkeypatch, lambda jids: list(jids))
  monkeypatch.setattr(update_metrics, "_start_candidate_rescan_thread", lambda **kwargs: None)
  monkeypatch.setattr(update_metrics.gc, "collect", lambda: 0)
  monkeypatch.setattr(update_metrics, "shutdown_requested", [False])
  monkeypatch.setattr(update_metrics, "persist_job_plot_artifacts_for_jid", lambda jid: None)

  class _FakeQs:
    def __init__(self, chunks):
      self.chunks = chunks

  monkeypatch.setattr(
      update_metrics,
      "_jobs_queryset",
      lambda d, *_args, **_kwargs: _FakeQs([([1001], 1)]),
  )
  monkeypatch.setattr(update_metrics, "_iter_chunked_pks", lambda qs, _chunk: iter(qs.chunks))
  class _DoneProducer:
    def join(self, timeout=None):
      del timeout

  def _producer_stub(**kwargs):
    with kwargs["ready_queue_lock"]:
      kwargs["ready_queue"].append(update_metrics._candidate_ref(1001))
    kwargs["producer_done"].set()
    return _DoneProducer()

  monkeypatch.setattr(update_metrics, "_start_readiness_producer", _producer_stub)

  parallel_calls = []
  safe_calls = []
  reset_calls = []

  monkeypatch.setattr(
      update_metrics,
      "refresh_public_expansion_factor_artifacts_parallel",
      lambda pool, **kwargs: parallel_calls.append(pool) or {},
  )
  monkeypatch.setattr(
      update_metrics,
      "refresh_public_expansion_factor_artifacts_safe",
      lambda: safe_calls.append("safe"),
  )

  class FakeMetrics:
    simple_metrics_list = {}
    complex_metrics_list = []

    def ensure_pool(self, pool_kind="metrics-pool"):
      return object()

    def close_pool(self):
      return None

    def reset_pool_hard(self):
      reset_calls.append("reset")

    def run(self, jobs, pool=None):
      del pool

  monkeypatch.setattr(update_metrics.metrics, "Metrics", lambda: FakeMetrics())
  update_metrics.update_metrics_for_dates([datetime(2025, 4, 10)], rerun=False)
  assert len(parallel_calls) == 1
  assert reset_calls == ["reset"]
  assert safe_calls == ["safe"]


@pytest.mark.django_db(databases=[])
def test_update_metrics_scheduler_runs_real_detail_prewarm_with_jid_table_stub(monkeypatch):
  """Integration-style scheduler check: real detail prewarm path must execute without NameError."""
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_mode", lambda: "global_fifo")
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_prefetch_chunks", lambda: 1)
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_ready_queue_target", lambda: 4)
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_plot_prewarm_mode", lambda: "inline")
  monkeypatch.setattr(update_metrics, "close_old_connections", lambda: None)
  monkeypatch.setattr(update_metrics, "_pg_session_statement_timeout_for_metrics_batch", contextlib.nullcontext)
  monkeypatch.setattr(update_metrics, "_proxy_readiness_for_jid", lambda jid: "unknown")
  monkeypatch.setattr(update_metrics, "_proxy_reject_not_ready_jids", lambda jids: (set(), list(jids)))
  _patch_strict_readiness_batch(monkeypatch, lambda jids: list(jids))
  monkeypatch.setattr(update_metrics, "_start_candidate_rescan_thread", lambda **kwargs: None)
  monkeypatch.setattr(update_metrics.gc, "collect", lambda: 0)
  monkeypatch.setattr(update_metrics, "shutdown_requested", [False])

  class _FakeQs:
    def __init__(self, chunks):
      self.chunks = chunks

  monkeypatch.setattr(
      update_metrics,
      "_jobs_queryset",
      lambda d, *_args, **_kwargs: _FakeQs([(["jid-1"], 1)]),
  )
  monkeypatch.setattr(update_metrics, "_iter_chunked_pks", lambda qs, _chunk: iter(qs.chunks))
  monkeypatch.setattr(
      update_metrics,
      "refresh_public_expansion_factor_artifacts_parallel",
      lambda pool, **kwargs: {},
  )
  monkeypatch.setattr(update_metrics, "refresh_public_expansion_factor_artifacts_safe", lambda: None)
  monkeypatch.setattr(update_metrics, "persist_job_plot_artifacts_for_jid", lambda *a, **k: None)

  class _DoneProducer:
    def join(self, timeout=None):
      del timeout

  def _producer_stub(**kwargs):
    with kwargs["ready_queue_lock"]:
      kwargs["ready_queue"].append("jid-1")
    kwargs["producer_done"].set()
    return _DoneProducer()

  monkeypatch.setattr(update_metrics, "_start_readiness_producer", _producer_stub)

  class _FakeMetrics:
    simple_metrics_list = {}
    complex_metrics_list = []

    def ensure_pool(self, pool_kind="metrics-pool"):
      return object()

    def close_pool(self):
      return None

    def reset_pool_hard(self):
      return None

    def run(self, jobs, pool=None):
      del pool

  monkeypatch.setattr(update_metrics.metrics, "Metrics", lambda: _FakeMetrics())

  # Keep detail prewarm real, but with lightweight providers so scheduler executes
  # the actual symbol path ``job_detail_artifacts.jid_table`` without DB-heavy work.
  from hpcperfstats.site.lib.machine import job_detail_artifacts as jda

  class _MiniJt:
    schema = {}
    acct_host_list = ["n1.example.org"]
    start_time = datetime(2025, 4, 10, 0, 0, 0)
    end_time = datetime(2025, 4, 10, 0, 5, 0)

    def get_llite_delta_by_event(self):
      import pandas as pd
      return pd.DataFrame(columns=["event", "delta_sum"])

    def get_nfs_delta_totals_mb(self):
      return None

  monkeypatch.setattr(jda.jid_table, "jid_table", lambda _jid: _MiniJt())
  monkeypatch.setattr(jda.jid_table, "TypeDetailDataProvider", lambda *a, **k: object())

  class _MiniDevPlot:
    def __init__(self, provider, hosts):
      del provider, hosts

    def plot(self):
      import pandas as pd
      return pd.DataFrame(), None

  import types
  monkeypatch.setattr(
      jda,
      "plots",
      types.SimpleNamespace(DevPlot=_MiniDevPlot),
      raising=False,
  )
  monkeypatch.setattr(jda, "upsert_job_detail_artifact", lambda **kwargs: None)
  monkeypatch.setattr(jda, "_metric_value_map", lambda job: {})
  monkeypatch.setattr(jda, "_gpu_detail_from_jid_table", lambda jt: {})
  monkeypatch.setattr(jda, "_multiprecision_mix_payload", lambda metric_values: {})
  monkeypatch.setattr(jda, "extend_fsio_payload_lists_with_peaks", lambda fsio, jt: None)

  class _MiniMetricsSet:
    def all(self):
      return []

  class _MiniJob:
    jid = "jid-1"
    host_data_schema_json = {}
    metrics_data_set = _MiniMetricsSet()

  class _MiniJobQ:
    def prefetch_related(self, *a, **k):
      return self

    def first(self):
      return _MiniJob()

  monkeypatch.setattr(jda.job_data.objects, "filter", lambda **kwargs: _MiniJobQ())

  update_metrics.update_metrics_for_dates([datetime(2025, 4, 10)], rerun=False)


@pytest.mark.django_db(databases=[])
def test_update_metrics_for_dates_empty_date_list_returns(monkeypatch):
  monkeypatch.setattr(update_metrics, "close_old_connections", lambda: None)
  monkeypatch.setattr(update_metrics, "_pg_session_statement_timeout_for_metrics_batch", contextlib.nullcontext)
  monkeypatch.setattr(update_metrics, "shutdown_requested", [False])

  class FakeMetrics:
    simple_metrics_list = {}
    complex_metrics_list = {}

    def ensure_pool(self, pool_kind="metrics-pool"):
      return object()

    def close_pool(self):
      return None

    def run(self, jobs, pool=None):
      raise AssertionError("no work for zero days")

  monkeypatch.setattr(update_metrics.metrics, "Metrics", lambda: FakeMetrics())
  update_metrics.update_metrics_for_dates([], rerun=False)


def test_update_metrics_for_dates_per_jid_failure_does_not_stop_progress(monkeypatch):
  """One failing jid should not stop progress for the rest of the queue."""
  monkeypatch.setattr(
      update_metrics,
      "refresh_public_expansion_factor_artifacts_parallel",
      lambda pool, **kwargs: {},
  )
  monkeypatch.setattr(update_metrics, "refresh_public_expansion_factor_artifacts_safe", lambda: None)
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_mode", lambda: "global_fifo")
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_prefetch_chunks", lambda: 1)
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_ready_queue_target", lambda: 1)
  monkeypatch.setattr(update_metrics, "close_old_connections", lambda: None)
  monkeypatch.setattr(update_metrics, "_pg_session_statement_timeout_for_metrics_batch", contextlib.nullcontext)
  _patch_strict_readiness_batch(monkeypatch, lambda jids: list(jids))
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

  class _DoneProducer:
    def join(self, timeout=None):
      del timeout

  def _producer_enqueue_all(**kwargs):
    with kwargs["ready_queue_lock"]:
      for state in kwargs["date_states"]:
        try:
          while True:
            pk_chunk, _total = next(state["iter"])
            for item in pk_chunk:
              jid = item.jid if hasattr(item, "jid") else item
              kwargs["ready_queue"].append(update_metrics._candidate_ref(jid))
        except StopIteration:
          state["done"] = True
    kwargs["producer_done"].set()
    return _DoneProducer()

  monkeypatch.setattr(update_metrics, "_start_readiness_producer", _producer_enqueue_all)
  monkeypatch.setattr(update_metrics, "_start_candidate_rescan_thread", lambda **kwargs: None)

  class FakeReporter:
    def __init__(self):
      self.completed = 0

    def start(self):
      return None

    def stop(self):
      return None

    def set_extra_stats_getter(self, fn):
      del fn

    def sync_completed_total(self, total):
      self.completed = int(total)

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

    def ensure_pool(self, pool_kind="metrics-pool"):
      return object()

    def close_pool(self):
      return None

    def run(self, jobs, pool=None):
      del pool
      outcomes = []
      for ref in jobs:
        if ref.jid == 1002:
          outcomes.append({
              "jid": ref.jid,
              "ok": False,
              "status": "worker_db_error",
              "error_type": "RuntimeError",
              "error_message": "single-job failure",
              "persist_s": 0.0,
          })
          continue
        successful.append(ref.jid)
        outcomes.append({
            "jid": ref.jid,
            "ok": True,
            "status": "ok",
            "error_type": None,
            "error_message": None,
            "persist_s": 0.0,
        })
      return outcomes

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


@pytest.mark.machine_unit_mock


@pytest.mark.machine_unit_mock


@pytest.mark.machine_unit_mock


@pytest.mark.machine_unit_mock


@pytest.mark.machine_unit_mock


@pytest.mark.machine_unit_mock


@pytest.mark.machine_unit_mock
def test_prewarm_pipeline_run_for_jid_shares_context(monkeypatch):
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_plot_prewarm_mode", lambda: "inline")
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
  monkeypatch.setattr(update_metrics, "close_old_connections", lambda: None)
  monkeypatch.setattr(update_metrics, "persist_job_detail_artifacts_for_jid", lambda *a, **k: None)
  monkeypatch.setattr(update_metrics, "persist_job_plot_artifacts_for_jid", lambda *a, **k: None)
  recorded = []

  def _capture(*args, **kwargs):
    recorded.append(args)

  monkeypatch.setattr(update_metrics, "log_print", _capture)

  class _M:
    def ensure_pool(self, pool_kind="metrics-pool"):
      return None

    def run(self, job_list, pool=None):
      return None

  ref = SimpleNamespace(jid="jid-x")
  pipe = update_metrics._PrewarmPipeline()
  out = update_metrics._compute_and_prewarm_jid(_M(), pipe, ref, None)
  assert out["ok"] is True
  joined = " ".join(" ".join(str(x) for x in tup) for tup in recorded)
  assert "jid-x" in joined and "compute complete" in joined
  assert "metrics=" in joined and "job_detail=" in joined and "job_plots=" in joined


@pytest.mark.machine_unit_mock
def test_parse_jid_cli_arg_forms():
  assert update_metrics._parse_jid_cli_arg(["update_metrics.py"]) == (None, None)
  assert update_metrics._parse_jid_cli_arg(
      ["update_metrics.py", "--jid", "857260"]
  ) == ("857260", None)
  assert update_metrics._parse_jid_cli_arg(
      ["update_metrics.py", "--jid=857260"]
  ) == ("857260", None)
  jid, err = update_metrics._parse_jid_cli_arg(["update_metrics.py", "--jid"])
  assert jid is None and err and "usage" in err
  jid, err = update_metrics._parse_jid_cli_arg(
      ["update_metrics.py", "--jid", "857260", "2025-04-01"]
  )
  assert jid is None and err and "cannot be combined" in err


@pytest.mark.machine_unit_mock
def test_main_jid_recalculates_once_without_scheduler_or_sleep(monkeypatch):
  """--jid invalidates caches, then uses _compute_and_prewarm_jid; no sleep."""
  calls = []

  class _FakeQs:
    def exists(self):
      return True

  class _FakeMgr:
    def filter(self, **kwargs):
      del kwargs
      return _FakeQs()

  class _FakeJobData:
    objects = _FakeMgr()

  monkeypatch.setattr(update_metrics, "job_data", _FakeJobData)

  def _fake_invalidate(jid):
    calls.append(("invalidate", jid))

  monkeypatch.setattr(
      update_metrics, "_invalidate_caches_before_one_jid_recalc", _fake_invalidate
  )
  monkeypatch.setattr(
      update_metrics,
      "_compute_and_prewarm_jid",
      lambda metrics_manager, prewarm_pipeline, job_ref, shared_pool, metrics_run_lock=None: (
          calls.append(("compute", job_ref.jid, bool(job_ref.artifact_only))),
          {
              "ok": True,
              "jid": job_ref.jid,
              "metrics_s": 1.5,
              "prewarm_s": 0.5,
          },
      )[-1],
  )
  monkeypatch.setattr(
      update_metrics.metrics,
      "Metrics",
      lambda: SimpleNamespace(close_pool=lambda: calls.append("close_pool")),
  )

  class _FakePrewarm:
    def finish(self):
      calls.append("finish")

  monkeypatch.setattr(update_metrics, "_PrewarmPipeline", _FakePrewarm)
  scheduled = []
  monkeypatch.setattr(
      update_metrics,
      "update_metrics_for_dates",
      lambda dates: scheduled.append(dates),
  )
  monkeypatch.setattr(
      update_metrics,
      "parse_start_end_dates",
      lambda *a, **k: (_ for _ in ()).throw(AssertionError("dates path")),
  )
  sleeps = []
  monkeypatch.setattr(update_metrics, "sleep_until_shutdown", lambda secs: sleeps.append(secs))
  monkeypatch.setattr(update_metrics, "log_print", lambda *a, **k: None)
  update_metrics.shutdown_requested[0] = False

  rc = update_metrics.main(
      argv=["update_metrics.py", "--jid", "857260"],
      sleep_after=True,
  )
  assert rc == 0
  assert sleeps == []
  assert scheduled == []
  assert calls[0] == ("invalidate", "857260")
  assert ("compute", "857260", False) in calls
  assert calls.index(("invalidate", "857260")) < calls.index(
      ("compute", "857260", False)
  )
  assert "finish" in calls
  assert "close_pool" in calls


@pytest.mark.machine_unit_mock
def test_invalidate_caches_before_one_jid_recalc_calls_cache_helpers(monkeypatch):
  """--jid invalidation must clear plot/detail artifacts, derived keys, and job cache."""
  seen = []

  monkeypatch.setattr(
      "hpcperfstats.site.lib.machine.cache_utils.invalidate_job_plot_cache_keys_for_jids",
      lambda jids: seen.append(("plots", list(jids))),
  )
  monkeypatch.setattr(
      "hpcperfstats.site.lib.machine.cache_utils.invalidate_jid_derived_cache_keys",
      lambda jids: seen.append(("derived", list(jids))),
  )
  deleted = []
  monkeypatch.setattr(
      "django.core.cache.cache.delete",
      lambda key: deleted.append(key),
  )
  monkeypatch.setattr(
      "hpcperfstats.site.lib.machine.cache_utils.make_job_detail_cache_key",
      lambda jid: "job:v2:{0}".format(jid),
  )

  update_metrics._invalidate_caches_before_one_jid_recalc("857260")
  assert seen == [("plots", ["857260"]), ("derived", ["857260"])]
  assert deleted == ["job:v2:857260"]


@pytest.mark.machine_unit_mock
def test_main_jid_missing_job_exits_one(monkeypatch):
  class _FakeQs:
    def exists(self):
      return False

  class _FakeMgr:
    def filter(self, **kwargs):
      del kwargs
      return _FakeQs()

  class _FakeJobData:
    objects = _FakeMgr()

  monkeypatch.setattr(update_metrics, "job_data", _FakeJobData)
  compute_calls = []
  inv_calls = []
  monkeypatch.setattr(
      update_metrics,
      "_invalidate_caches_before_one_jid_recalc",
      lambda jid: inv_calls.append(jid),
  )
  monkeypatch.setattr(
      update_metrics,
      "_compute_and_prewarm_jid",
      lambda *a, **k: compute_calls.append(1) or {"ok": True},
  )
  monkeypatch.setattr(update_metrics, "log_print", lambda *a, **k: None)
  rc = update_metrics.main(argv=["update_metrics.py", "--jid=missing"])
  assert rc == 1
  assert inv_calls == []
  assert compute_calls == []


@pytest.mark.machine_unit_mock
def test_main_jid_invalidate_failure_exits_one_without_compute(monkeypatch):
  class _FakeQs:
    def exists(self):
      return True

  class _FakeMgr:
    def filter(self, **kwargs):
      del kwargs
      return _FakeQs()

  class _FakeJobData:
    objects = _FakeMgr()

  monkeypatch.setattr(update_metrics, "job_data", _FakeJobData)
  compute_calls = []
  monkeypatch.setattr(
      update_metrics,
      "_invalidate_caches_before_one_jid_recalc",
      lambda jid: (_ for _ in ()).throw(RuntimeError("redis down")),
  )
  monkeypatch.setattr(
      update_metrics,
      "_compute_and_prewarm_jid",
      lambda *a, **k: compute_calls.append(1) or {"ok": True},
  )
  monkeypatch.setattr(update_metrics, "log_print", lambda *a, **k: None)
  rc = update_metrics.main(argv=["update_metrics.py", "--jid", "x"])
  assert rc == 1
  assert compute_calls == []


@pytest.mark.machine_unit_mock
def test_main_jid_compute_failure_exits_one(monkeypatch):
  class _FakeQs:
    def exists(self):
      return True

  class _FakeMgr:
    def filter(self, **kwargs):
      del kwargs
      return _FakeQs()

  class _FakeJobData:
    objects = _FakeMgr()

  monkeypatch.setattr(update_metrics, "job_data", _FakeJobData)
  monkeypatch.setattr(
      update_metrics, "_invalidate_caches_before_one_jid_recalc", lambda jid: None
  )
  monkeypatch.setattr(
      update_metrics,
      "_compute_and_prewarm_jid",
      lambda *a, **k: {"ok": False, "jid": "x", "metrics_s": 0.1, "prewarm_s": 0.0},
  )
  monkeypatch.setattr(
      update_metrics.metrics,
      "Metrics",
      lambda: SimpleNamespace(close_pool=lambda: None),
  )
  monkeypatch.setattr(
      update_metrics,
      "_PrewarmPipeline",
      lambda: SimpleNamespace(finish=lambda: None),
  )
  monkeypatch.setattr(update_metrics, "log_print", lambda *a, **k: None)
  rc = update_metrics.main(argv=["update_metrics.py", "--jid", "x"])
  assert rc == 1


@pytest.mark.machine_unit_mock
def test_main_jid_with_dates_exits_one_without_compute(monkeypatch):
  compute_calls = []
  monkeypatch.setattr(
      update_metrics,
      "_compute_and_prewarm_jid",
      lambda *a, **k: compute_calls.append(1) or {"ok": True},
  )
  monkeypatch.setattr(update_metrics, "log_print", lambda *a, **k: None)
  rc = update_metrics.main(
      argv=["update_metrics.py", "--jid", "1", "2025-04-01"],
  )
  assert rc == 1
  assert compute_calls == []


@pytest.mark.machine_unit_mock
def test_main_exits_on_scheduler_stall_without_sleep(monkeypatch):
  """Stall exit should terminate the process path and skip legacy post-run sleep."""
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
  update_metrics.shutdown_requested[0] = False
  sleeps = []
  monkeypatch.setattr(update_metrics, "sleep_until_shutdown", lambda secs: sleeps.append(secs))

  def _raise_stall(_dates):
    raise update_metrics.MetricsSchedulerStallExit(stall_reason="no_ready_candidates")

  monkeypatch.setattr(update_metrics, "update_metrics_for_dates", _raise_stall)

  with pytest.raises(update_metrics.MetricsSchedulerStallExit):
    update_metrics.main(argv=["update_metrics.py"], sleep_after=True)

  assert sleeps == []


@pytest.mark.machine_unit_mock
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

  assert sleeps == [600]


@pytest.mark.machine_unit_mock
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

  assert sleeps == [600]


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


@pytest.mark.machine_unit_mock
def test_proxy_coverage_matches_strict_host_list_bucket(monkeypatch):
  """Proxy and strict host_list bucket helpers stay aligned."""
  _patch_connections_vendor(monkeypatch, "postgresql")
  monkeypatch.setattr(
      update_metrics.cfg,
      "get_metrics_readiness_require_window_coverage",
      lambda: True,
  )
  start = datetime(2025, 4, 1, 10, 0, 0, tzinfo=timezone.utc)
  end = datetime(2025, 4, 1, 12, 0, 0, tzinfo=timezone.utc)
  cases = (
      (start + timedelta(minutes=5), end - timedelta(minutes=5), "unknown"),
      (start + timedelta(hours=1), end - timedelta(minutes=5), "reject"),
      (None, None, "unknown"),
  )
  for first, last, expected in cases:
    strict = update_metrics._strict_host_list_coverage_bucket(
        start, end, first, last)
    proxy = update_metrics._proxy_window_coverage_bucket(start, end, first, last)
    assert strict == expected
    assert proxy == expected


@pytest.mark.machine_unit_mock
def test_proxy_coverage_does_not_reject_when_host_list_bounds_pass(monkeypatch):
  """Host_list in-window pass must not land in proxy reject (jid column irrelevant)."""
  _patch_connections_vendor(monkeypatch, "postgresql")
  monkeypatch.setattr(
      update_metrics.cfg,
      "get_metrics_readiness_require_window_coverage",
      lambda: True,
  )
  start = datetime(2025, 4, 1, 10, 0, 0, tzinfo=timezone.utc)
  end = datetime(2025, 4, 1, 12, 0, 0, tzinfo=timezone.utc)
  first_ok = start + timedelta(minutes=5)
  last_ok = end - timedelta(minutes=5)

  monkeypatch.setattr(
      update_metrics,
      "_in_window_min_max_by_job_rows",
      lambda rows: {r["jid"]: (first_ok, last_ok) for r in rows},
  )

  def job_filter(*_a, jid__in=None, **_k):
    class _JobVals:
      def values(self, *_names, **_kw):
        return [
            {
                "jid": j,
                "start_time": start,
                "end_time": end,
                "host_list": ["n1.example.org"],
            }
            for j in (jid__in or [])
        ]
    return _JobVals()

  monkeypatch.setattr(update_metrics.job_data.objects, "filter", job_filter)
  reject, unknown = update_metrics._proxy_reject_not_ready_jids(["j_pass"])
  assert reject == set()
  assert unknown == ["j_pass"]


@pytest.mark.machine_unit_mock
def test_process_pk_chunk_proxy_reject_still_runs_strict_when_host_list_passes(monkeypatch):
  """When proxy leaves jid unknown, strict probe enqueues jobs with passing host_list bounds."""
  _patch_connections_vendor(monkeypatch, "postgresql")
  monkeypatch.setattr(
      update_metrics.cfg,
      "get_metrics_readiness_require_window_coverage",
      lambda: True,
  )
  strict_calls = []
  start = datetime(2025, 4, 1, 10, 0, 0, tzinfo=timezone.utc)
  end = datetime(2025, 4, 1, 12, 0, 0, tzinfo=timezone.utc)
  first_ok = start + timedelta(minutes=5)
  last_ok = end - timedelta(minutes=5)

  def _strict_and_bounds(jids):
    strict_calls.append(list(jids))
    return list(jids), {j: (first_ok, last_ok) for j in jids}

  monkeypatch.setattr(
      update_metrics,
      "_filter_jids_with_samples_after_end_and_bounds",
      _strict_and_bounds,
  )
  monkeypatch.setattr(
      update_metrics,
      "_in_window_min_max_by_job_rows",
      lambda rows: {r["jid"]: (first_ok, last_ok) for r in rows},
  )

  def job_filter(*_a, jid__in=None, **_k):
    class _JobVals:
      def values(self, *_names, **_kw):
        return [
            {
                "jid": j,
                "start_time": start,
                "end_time": end,
                "host_list": ["n1.example.org"],
            }
            for j in (jid__in or [])
        ]
    return _JobVals()

  monkeypatch.setattr(update_metrics.job_data.objects, "filter", job_filter)

  ready_queue = deque()
  stats = {
      "candidate_jids": 0,
      "skipped_not_ready": 0,
      "proxy_not_ready_jids": 0,
      "proxy_rejected_jids": 0,
      "proxy_checked_chunks": 0,
      "strict_ready_jids": 0,
      "strict_not_ready_jids": 0,
      "strict_cooldown_skips": 0,
      "strict_check_calls": 0,
      "strict_check_avg_latency_ms": 0.0,
      "strict_batch_size_current": 32,
      "readiness_error_chunks": 0,
  }
  strict_check_state = {"batch_size": 32, "max_batch_size": 32}
  strict_check_cooldown_until = {}
  scheduler_shared_lock = threading.Lock()
  phase_timer = update_metrics._PhaseTimer()
  state = {
      "date": datetime(2025, 4, 10),
      "iter": iter([([update_metrics._candidate_ref("j_pass")], 1)]),
      "done": False,
      "pending_tail": None,
  }

  update_metrics._fill_ready_queue(
      [state],
      ready_queue,
      "strict_date",
      prefetch_chunks=1,
      phase_timer=phase_timer,
      stats=stats,
      strict_check_state=strict_check_state,
      strict_check_cooldown_until=strict_check_cooldown_until,
      rr_cursor=[0],
      scheduler_shared_lock=scheduler_shared_lock,
  )
  assert strict_calls in ([["j_pass"]], [])
  assert _ready_queue_jids(ready_queue) == ["j_pass"]
  assert getattr(ready_queue[0], "telemetry_first_time", None) == first_ok
  assert getattr(ready_queue[0], "telemetry_last_time", None) == last_ok


@pytest.mark.machine_unit_mock
def test_strict_in_window_bounds_query_count_bounded(monkeypatch):
  """Batched strict bounds use O(unique_hosts/64) queries, not O(jobs*hosts/64)."""
  monkeypatch.setattr(update_metrics, "HOST_LAST_TIME_LOOKUP_BATCH", 64)
  query_count = [0]

  class _HostManager:
    def filter(self, **kwargs):
      query_count[0] += 1

      class _Agg:
        def values(self, *_a, **_k):
          return self

        def annotate(self, **_k):
          return self

        def __iter__(self):
          for host in kwargs.get("host__in") or ():
            yield {
                "host": host,
                "mn": kwargs.get("time__gte"),
                "mx": kwargs.get("time__lte"),
            }

      return _Agg()

  monkeypatch.setattr(update_metrics.host_data, "objects", _HostManager())
  monkeypatch.setattr(update_metrics, "_host_name_suffix", lambda: ".example.org")
  start = datetime(2025, 4, 1, 10, 0, 0, tzinfo=timezone.utc)
  end = datetime(2025, 4, 1, 12, 0, 0, tzinfo=timezone.utc)
  jobs = []
  for j in range(32):
    hosts = ["n{:02d}".format(i) for i in range(128)]
    jobs.append({
        "jid": str(j),
        "start_time": start,
        "end_time": end,
        "host_list": hosts,
    })
  update_metrics._in_window_min_max_by_job_rows(jobs)
  assert query_count[0] == 2


@pytest.mark.machine_unit_mock
def test_ready_jids_from_job_rows_batched_matches_per_job_reference(monkeypatch):
  """Batched in-window bounds match the per-job reference implementation."""
  monkeypatch.setattr(update_metrics, "HOST_LAST_TIME_LOOKUP_BATCH", 2)
  start = datetime(2025, 4, 1, 10, 0, 0, tzinfo=timezone.utc)
  end = datetime(2025, 4, 1, 12, 0, 0, tzinfo=timezone.utc)
  end2 = end + timedelta(hours=1)
  jobs = [
      {
          "jid": "a",
          "start_time": start,
          "end_time": end,
          "host_list": ["n1", "n2"],
      },
      {
          "jid": "b",
          "start_time": start,
          "end_time": end2,
          "host_list": ["n2", "n3"],
      },
  ]

  class _HostManager:
    def filter(self, **kwargs):
      st = kwargs.get("time__gte")
      et = kwargs.get("time__lte")

      class _Agg:
        def values(self, *_a, **_k):
          return self

        def annotate(self, **_k):
          return self

        def __iter__(self):
          for host in kwargs.get("host__in") or ():
            yield {
                "host": host,
                "mn": st + timedelta(minutes=1) if st else None,
                "mx": et - timedelta(minutes=1) if et else None,
            }

      return _Agg()

  monkeypatch.setattr(update_metrics.host_data, "objects", _HostManager())
  monkeypatch.setattr(update_metrics, "_host_name_suffix", lambda: ".example.org")
  batched = update_metrics._in_window_min_max_by_job_rows(jobs)
  reference = update_metrics._in_window_min_max_by_job_rows_reference(jobs)
  assert batched == reference


@pytest.mark.machine_unit_mock
def test_proxy_coverage_batch_query_count_bounded(monkeypatch):
  """Coverage proxy sub-batch uses batched bounds, not one aggregate per jid."""
  _patch_connections_vendor(monkeypatch, "postgresql")
  monkeypatch.setattr(
      update_metrics.cfg,
      "get_metrics_readiness_require_window_coverage",
      lambda: True,
  )
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_proxy_reject_jid_batch_size", lambda: 48)
  bounds_calls = [0]

  def _bounds(rows):
    bounds_calls[0] += 1
    return {r["jid"]: (None, None) for r in rows}

  monkeypatch.setattr(update_metrics, "_in_window_min_max_by_job_rows", _bounds)

  def job_filter(*_a, jid__in=None, **_k):
    class _JobVals:
      def values(self, *_names, **_kw):
        return [
            {
                "jid": j,
                "start_time": datetime(2025, 4, 1, 10, 0, 0, tzinfo=timezone.utc),
                "end_time": datetime(2025, 4, 1, 12, 0, 0, tzinfo=timezone.utc),
                "host_list": ["n1.example.org"],
            }
            for j in (jid__in or [])
        ]
    return _JobVals()

  monkeypatch.setattr(update_metrics.job_data.objects, "filter", job_filter)
  jids = ["j{:02d}".format(i) for i in range(10)]
  update_metrics._proxy_reject_not_ready_jids(jids)
  assert bounds_calls[0] == 1


@pytest.mark.machine_unit_mock
def test_deferred_not_ready_quarantine_timing_unchanged(monkeypatch):
  """Defer/quarantine retry contract unchanged under coverage gate work."""
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_deferred_not_ready_retry_s", lambda: 10.0)
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_deferred_not_ready_max_retries", lambda: 2)
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_deferred_not_ready_max_age_s", lambda: 900.0)
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_deferred_not_ready_quarantine_s", lambda: 300.0)
  deferred_not_ready = {}
  deferred_meta = {}
  stats = {"deferred_quarantined_jids": 0}
  scheduler_shared_lock = threading.Lock()
  times = iter([100.0, 110.0, 120.0, 430.0])

  def _mono():
    return next(times)

  monkeypatch.setattr(update_metrics.time, "monotonic", _mono)

  def _defer(jid):
    now = update_metrics.time.monotonic()
    meta = deferred_meta.setdefault(
        jid, {"first_seen": now, "attempts": 0, "artifact_only": False})
    meta["attempts"] += 1
    age_s = max(0.0, now - float(meta["first_seen"]))
    max_retries = int(update_metrics.cfg.get_metrics_deferred_not_ready_max_retries())
    use_quarantine = (
        meta["attempts"] >= max_retries
        or age_s >= float(update_metrics.cfg.get_metrics_deferred_not_ready_max_age_s())
    )
    retry_after = (
        float(update_metrics.cfg.get_metrics_deferred_not_ready_quarantine_s())
        if use_quarantine
        else float(update_metrics.cfg.get_metrics_deferred_not_ready_retry_s())
    )
    if use_quarantine:
      with scheduler_shared_lock:
        stats["deferred_quarantined_jids"] += 1
    deferred_not_ready[jid] = now + retry_after

  _defer("j1")
  assert deferred_not_ready["j1"] == 110.0
  _defer("j1")
  assert deferred_not_ready["j1"] == 410.0
  assert stats["deferred_quarantined_jids"] == 1


@pytest.mark.machine_unit_mock
def test_strict_readiness_batch_timeout_falls_back_per_jid_without_dropping(monkeypatch):
  """Batch strict DB failure triggers per-jid fallback without dropping jids."""
  _patch_connections_vendor(monkeypatch, "postgresql")
  monkeypatch.setattr(update_metrics, "_proxy_reject_not_ready_jids", lambda jids: (set(), list(jids)))
  call_state = {"batch": 0}

  def _strict(jids):
    if call_state["batch"] == 0:
      call_state["batch"] = 1
      raise OperationalError("statement timeout")
    start = datetime(2025, 4, 1, 10, 0, 0, tzinfo=timezone.utc)
    end = datetime(2025, 4, 1, 12, 0, 0, tzinfo=timezone.utc)
    return jids, {jids[0]: (start, end)}

  monkeypatch.setattr(
      update_metrics,
      "_filter_jids_with_samples_after_end_and_bounds",
      _strict,
  )

  ready_queue = deque()
  stats = {
      "candidate_jids": 0,
      "skipped_not_ready": 0,
      "proxy_not_ready_jids": 0,
      "proxy_rejected_jids": 0,
      "proxy_checked_chunks": 0,
      "strict_ready_jids": 0,
      "strict_not_ready_jids": 0,
      "strict_cooldown_skips": 0,
      "strict_check_calls": 0,
      "strict_check_avg_latency_ms": 0.0,
      "strict_batch_size_current": 32,
      "readiness_error_chunks": 0,
      "strict_check_timeouts": 0,
  }
  strict_check_state = {"batch_size": 32, "max_batch_size": 32}
  strict_check_cooldown_until = {}
  scheduler_shared_lock = threading.Lock()
  phase_timer = update_metrics._PhaseTimer()
  state = {
      "date": datetime(2025, 4, 10),
      "iter": iter([([update_metrics._candidate_ref("j1")], 1)]),
      "done": False,
      "pending_tail": None,
  }
  update_metrics._fill_ready_queue(
      [state],
      ready_queue,
      "strict_date",
      prefetch_chunks=1,
      phase_timer=phase_timer,
      stats=stats,
      strict_check_state=strict_check_state,
      strict_check_cooldown_until=strict_check_cooldown_until,
      rr_cursor=[0],
      scheduler_shared_lock=scheduler_shared_lock,
  )
  assert _ready_queue_jids(ready_queue) == ["j1"]
  assert stats["strict_check_timeouts"] >= 1


@pytest.mark.django_db(databases=[])
def test_strict_readiness_sets_max_parallel_workers_when_configured():
  """PostgreSQL strict readiness disables parallel gather workers per batch."""
  import inspect
  from pathlib import Path

  # conftest autouse replaces the runtime symbol for django_db(databases=[]);
  # assert contract against the on-disk module definition.
  module_path = Path(inspect.getfile(update_metrics))
  text = module_path.read_text(encoding="utf-8")
  start = text.index("def _pg_local_readiness_timeouts")
  end = text.index("def _metrics_telemetry_enabled", start)
  src = text[start:end]
  assert "SET LOCAL statement_timeout" in src
  assert "max_parallel_workers_per_gather = 0" in src


@pytest.mark.machine_unit_mock


@pytest.mark.machine_unit_mock


@pytest.mark.django_db(databases=[])
def test_prewarm_pipeline_is_sync_only_no_executor(monkeypatch):
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_plot_prewarm_mode", lambda: "pipeline_required")
  pipe = update_metrics._PrewarmPipeline()
  assert pipe.has_pending() is False
  assert getattr(pipe, "_executor", None) is None
  assert pipe._attempts == 2


@pytest.mark.django_db(databases=[])
def test_prewarm_successful_refs_runs_inline_for_ok_jids(monkeypatch):
  calls = []
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_plot_prewarm_mode", lambda: "inline")

  class _Pipe:
    def run_for_jid(self, jid, shared_context=None):
      calls.append(jid)
      return {"prewarm_total_s": 0.0, "undivided": True}

    def record_pool_result(self, ok):
      del ok

  refs = [SimpleNamespace(jid="ok1"), SimpleNamespace(jid="ok2")]
  update_metrics._prewarm_successful_refs_on_metrics_pool(refs, _Pipe(), shared_pool=None)
  assert calls == ["ok1", "ok2"]


@pytest.mark.django_db(databases=[])
def test_compute_jid_outcomes_batch_prewarm_waits_and_counts_batch_wall(monkeypatch):
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_plot_prewarm_mode", lambda: "inline")
  monkeypatch.setattr(update_metrics, "shutdown_requested", [False])
  prewarm_calls = []

  class _Mgr:
    def ensure_pool(self, pool_kind=None):
      del pool_kind
      return object()

    def run(self, job_refs, pool=None):
      del pool
      return [{
          "jid": ref.jid,
          "ok": True,
          "status": "ok",
          "error_type": None,
          "error_message": None,
          "persist_s": 0.01,
      } for ref in job_refs]

  class _Pipe:
    def __init__(self):
      self._done = 0

    def submit(self, jid, shared_context=None):
      del shared_context
      prewarm_calls.append(jid)
      time.sleep(0.01)
      self._done += 1
      return {
          "prewarm_total_s": 0.01,
          "undivided": True,
          "detail_s": None,
          "plots_s": None,
      }

    def record_pool_result(self, ok):
      del ok

  refs = [
      SimpleNamespace(jid="j1", artifact_only=False),
      SimpleNamespace(jid="j2", artifact_only=False),
  ]
  timing = {}
  outcomes = update_metrics._compute_jid_outcomes_batch(
      refs, _Mgr(), _Pipe(), shared_pool=None, batch_timing=timing,
  )
  assert [o["jid"] for o in outcomes] == ["j1", "j2"]
  assert all(o["ok"] for o in outcomes)
  assert prewarm_calls == ["j1", "j2"]
  assert timing["batch_wall_s"] >= timing["metrics_wall_s"]
  assert timing["prewarm_wall_s"] > 0.0
  assert timing["batch_wall_s"] >= timing["metrics_wall_s"] + timing["prewarm_wall_s"] - 1e-6


@pytest.mark.django_db(databases=[])
def test_prewarm_pipeline_run_for_jid_retries_hard_coded(monkeypatch):
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_plot_prewarm_mode", lambda: "inline")
  attempts = {"n": 0}

  def _boom(jid, shared_context=None):
    del shared_context
    attempts["n"] += 1
    if attempts["n"] < 2:
      raise RuntimeError("transient")
    return 0.01, 0.02

  pipe = update_metrics._PrewarmPipeline()
  monkeypatch.setattr(pipe, "_persist_detail_plot_elapsed", _boom)
  timing = pipe.run_for_jid("jid-x", shared_context={})
  assert attempts["n"] == 2
  assert timing["prewarm_total_s"] > 0.0


@pytest.mark.machine_unit_mock
def test_compute_batch_should_downshift_uses_batch_wall():
  """metrics_compute_watchdog_s gates metrics+prewarm batch_wall_s (not metrics-only)."""
  assert update_metrics._compute_batch_should_downshift(
      batch_wall_s=5716.91,
      metrics_watchdog_s=120.0,
      total_watchdog_s=0.0,
  )
  assert update_metrics._compute_batch_should_downshift(
      batch_wall_s=200.0,
      metrics_watchdog_s=120.0,
      total_watchdog_s=0.0,
  )
  assert not update_metrics._compute_batch_should_downshift(
      batch_wall_s=50.0,
      metrics_watchdog_s=120.0,
      total_watchdog_s=0.0,
  )
  assert not update_metrics._compute_batch_should_downshift(
      batch_wall_s=200.0,
      metrics_watchdog_s=0.0,
      total_watchdog_s=0.0,
  )
  assert update_metrics._compute_batch_should_downshift(
      batch_wall_s=200.0,
      metrics_watchdog_s=0.0,
      total_watchdog_s=180.0,
  )


@pytest.mark.machine_unit_mock
def test_compute_batch_heartbeat_fields_on_progress():
  """Mid-batch heartbeat updates shared stats exposed on metrics progress extras."""
  stats = update_metrics._new_scheduler_stats()
  lock = threading.Lock()
  hb = update_metrics._ComputeBatchHeartbeat(stats, lock)
  hb.begin(48)
  assert stats["compute_batch_phase"] == "metrics"
  assert stats["compute_batch_size"] == 48
  assert stats["compute_batch_completed_jids"] == 0
  assert stats["compute_batch_started_at"] is not None
  assert update_metrics._compute_batch_age_s(stats) >= 0.0
  hb.set_phase("persist")
  hb.note_completed(12, 48)
  assert stats["compute_batch_phase"] == "persist"
  assert stats["compute_batch_completed_jids"] == 12
  hb.set_phase("prewarm")
  hb.note_completed(24, 48)
  extras = {
      "compute_batch_phase": stats["compute_batch_phase"],
      "compute_batch_age_s": update_metrics._compute_batch_age_s(stats),
      "compute_batch_completed_jids": stats["compute_batch_completed_jids"],
      "compute_batch_size": stats["compute_batch_size"],
  }
  assert extras["compute_batch_phase"] == "prewarm"
  assert extras["compute_batch_completed_jids"] == 24
  assert extras["compute_batch_size"] == 48
  assert extras["compute_batch_age_s"] >= 0.0
  hb.clear()
  assert stats["compute_batch_phase"] == "idle"
  assert stats["compute_batch_size"] == 0
  assert stats["compute_batch_started_at"] is None


@pytest.mark.machine_unit_mock
def test_prewarm_pool_drain_stalls_without_progress(monkeypatch):
  class _It:
    def next(self, timeout=None):
      del timeout
      raise update_metrics.multiprocessing.TimeoutError()

  class _Pool:
    def imap_unordered(self, fn, jids):
      del fn, jids
      return _It()

  monkeypatch.setattr(update_metrics, "abort_if_pool_workers_dead", lambda *a, **k: None)
  with pytest.raises(update_metrics.MetricsPrewarmStallError) as ei:
    update_metrics._drain_prewarm_imap(
        _Pool(),
        ["j1", "j2"],
        poll_timeout_s=0.0,
        stall_timeout_s=0.0,
    )
  assert float(ei.value.stalled_for_s) >= 0.0


@pytest.mark.machine_unit_mock
def test_compute_watchdog_downshifts_on_batch_wall_not_metrics_only(monkeypatch):
  """Short metrics + long prewarm batch_wall must reduce next_batch_cap."""
  monkeypatch.setenv("HPCPERFSTATS_UPDATE_METRICS_RETURN_DIAGNOSTICS", "1")
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_mode", lambda: "global_fifo")
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_prefetch_chunks", lambda: 4)
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_ready_queue_target", lambda: 48)
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_pool_processes", lambda: 24)
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_compute_watchdog_s", lambda: 120.0)
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_compute_total_watchdog_s", lambda: 0.0)
  monkeypatch.setattr(update_metrics, "close_old_connections", lambda: None)
  monkeypatch.setattr(
      update_metrics, "_pg_session_statement_timeout_for_metrics_batch", contextlib.nullcontext,
  )
  _patch_strict_readiness_batch(monkeypatch, lambda jids: list(jids))
  monkeypatch.setattr(
      update_metrics, "_proxy_reject_not_ready_jids", lambda jids: (set(), list(jids)),
  )
  monkeypatch.setattr(update_metrics.gc, "collect", lambda: 0)
  monkeypatch.setattr(update_metrics, "shutdown_requested", [False])
  monkeypatch.setattr(update_metrics, "_start_candidate_rescan_thread", lambda **kwargs: None)
  monkeypatch.setattr(
      update_metrics,
      "_run_public_ef_artifacts_parallel_phase",
      lambda shared_pool, phase_timer: {
          "degraded": 0,
          "worker_exceptions": 0,
          "watchdog_timeouts": 0,
          "pending_tasks": 0,
          "tasks_completed": 0,
          "tasks_total": 0,
      },
  )
  jids = list(range(1001, 1049))

  class _FakeQs:
    def __init__(self, chunks):
      self.chunks = chunks

  monkeypatch.setattr(
      update_metrics,
      "_jobs_queryset",
      lambda d, *_args, **_kwargs: _FakeQs([(jids, len(jids))]),
  )
  monkeypatch.setattr(update_metrics, "_iter_chunked_pks", lambda qs, _chunk: iter(qs.chunks))

  class _DoneProducer:
    def join(self, timeout=None):
      del timeout

  def _producer_stub(**kwargs):
    with kwargs["ready_queue_lock"]:
      kwargs["ready_queue"].extend(jids)
    kwargs["producer_done"].set()
    return _DoneProducer()

  monkeypatch.setattr(update_metrics, "_start_readiness_producer", _producer_stub)

  class FakeMetrics:
    simple_metrics_list = {}
    complex_metrics_list = []

    def ensure_pool(self, pool_kind="metrics-pool"):
      return object()

    def close_pool(self):
      return None

    def run(self, jobs, pool=None, progress_callback=None):
      del pool, progress_callback
      return [{
          "jid": j.jid,
          "ok": True,
          "status": "ok",
          "error_type": None,
          "error_message": None,
          "persist_s": 0.0,
      } for j in jobs]

  def _slow_prewarm_batch(job_refs, metrics_manager, prewarm_pipeline, shared_pool,
                          batch_timing=None, heartbeat=None):
    del metrics_manager, prewarm_pipeline, shared_pool, heartbeat
    if batch_timing is not None:
      batch_timing["metrics_wall_s"] = 10.0
      batch_timing["prewarm_wall_s"] = 200.0
      batch_timing["batch_wall_s"] = 210.0
    return [
        update_metrics._scheduler_jid_outcome(
            ok=True,
            jid=ref.jid,
            metrics_s=0.1,
            prewarm_s=0.1,
            telemetry=update_metrics._empty_jid_outcome_telemetry(),
        )
        for ref in job_refs
    ]

  logs = []
  monkeypatch.setattr(update_metrics.metrics, "Metrics", lambda: FakeMetrics())
  monkeypatch.setattr(update_metrics, "_compute_jid_outcomes_batch", _slow_prewarm_batch)
  monkeypatch.setattr(
      update_metrics, "log_print", lambda msg, flush=False: logs.append(str(msg)),
  )
  monkeypatch.setattr(
      update_metrics.cfg,
      "get_metrics_readiness_require_window_coverage",
      lambda: False,
  )
  update_metrics.LAST_UPDATE_METRICS_DIAGNOSTICS = None
  update_metrics.update_metrics_for_dates([datetime(2025, 4, 10)], rerun=False)
  watchdog_lines = [line for line in logs if "compute watchdog" in line]
  assert watchdog_lines, "expected compute watchdog log after prewarm-dominated batch; logs={0}".format(
      logs[-20:],
  )
  assert any("batch_wall_s=210.0" in line or "batch_wall_s=210" in line for line in watchdog_lines)
  cap_lines = [line for line in logs if "next_batch_cap=" in line]
  assert cap_lines
  # Starting cap with 24 workers ×2 = 48; half → 24 (floor MIN_CAP=16).
  assert any("next_batch_cap=24" in line for line in cap_lines)


@pytest.mark.machine_unit_mock
def test_candidate_ref_attaches_estimated_sample_count(monkeypatch):
  monkeypatch.setattr(
      update_metrics.cfg,
      "get_metrics_compute_batch_unknown_runtime_s",
      lambda: 60.0,
  )
  ref = update_metrics._candidate_ref("j1", False, runtime_s=120.0, nhosts=2)
  assert ref.nhosts == 2
  assert ref.estimated_sample_count == 4
  ref2 = update_metrics._candidate_ref(
      "j2", False, runtime_s=60.0, nhosts=0, host_list=["a", "b", "c"],
  )
  assert ref2.nhosts == 3
  assert ref2.estimated_sample_count == 3


@pytest.mark.machine_unit_mock
def test_ready_queue_cap_uses_ini_target_not_chunks_product(monkeypatch):
  """prefetch_ready_cap equals ready_queue_target; ignores chunks×CHUNK_SIZE."""
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_mode", lambda: "global_fifo")
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_prefetch_chunks", lambda: 8)
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_ready_queue_target", lambda: 100)
  monkeypatch.setattr(update_metrics, "close_old_connections", lambda: None)
  monkeypatch.setattr(
      update_metrics, "_pg_session_statement_timeout_for_metrics_batch", contextlib.nullcontext,
  )
  monkeypatch.setattr(update_metrics, "shutdown_requested", [False])
  monkeypatch.setattr(update_metrics, "_start_candidate_rescan_thread", lambda **kwargs: None)
  monkeypatch.setattr(update_metrics.gc, "collect", lambda: 0)
  captured = {}

  class _DoneProducer:
    def join(self, timeout=None):
      del timeout

  def _producer_stub(**kwargs):
    captured["prefetch_ready_cap"] = kwargs.get("prefetch_ready_cap")
    kwargs["producer_done"].set()
    return _DoneProducer()

  monkeypatch.setattr(update_metrics, "_start_readiness_producer", _producer_stub)
  monkeypatch.setattr(
      update_metrics,
      "_run_public_ef_artifacts_parallel_phase",
      lambda *a, **k: {
          "degraded": 0,
          "worker_exceptions": 0,
          "watchdog_timeouts": 0,
          "pending_tasks": 0,
          "tasks_completed": 0,
          "tasks_total": 0,
      },
  )

  class FakeMetrics:
    simple_metrics_list = {}
    complex_metrics_list = []

    def ensure_pool(self, pool_kind="metrics-pool"):
      return object()

    def close_pool(self):
      return None

    def reset_pool_hard(self):
      return None

    def run(self, jobs, pool=None):
      del jobs, pool
      return []

  monkeypatch.setattr(update_metrics.metrics, "Metrics", lambda: FakeMetrics())
  monkeypatch.setattr(
      update_metrics,
      "_jobs_queryset",
      lambda *a, **k: SimpleNamespace(chunks=[]),
  )
  monkeypatch.setattr(update_metrics, "_iter_chunked_pks", lambda qs, _c: iter([]))
  update_metrics.update_metrics_for_dates([datetime(2025, 4, 10)], rerun=False)
  assert captured.get("prefetch_ready_cap") == 100
  # chunks×CHUNK_SIZE would be 4000; must not be used as the cap.
  assert captured.get("prefetch_ready_cap") != 8 * update_metrics.CHUNK_SIZE


@pytest.mark.machine_unit_mock
def test_prewarm_stall_recycles_pool_and_keeps_partial_results(monkeypatch):
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_plot_prewarm_mode", lambda: "pipeline_required")
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_run_poll_timeout_s", lambda: 0.0)
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_run_stall_timeout_s", lambda: 0.0)
  monkeypatch.setattr(update_metrics, "abort_if_pool_workers_dead", lambda *a, **k: None)
  monkeypatch.setattr(update_metrics, "shutdown_requested", [False])

  resets = []
  soft_fails = []

  class _Mgr:
    def reset_pool_hard(self):
      resets.append("reset")

  class _Pipe:
    def record_pool_result(self, ok):
      soft_fails.append(bool(ok))

    def submit(self, jid):
      raise AssertionError("pipeline mode must not submit inline")

  class _It:
    def __init__(self):
      self._n = 0

    def next(self, timeout=None):
      del timeout
      self._n += 1
      if self._n == 1:
        return {"jid": "ok1", "ok": True}
      raise update_metrics.multiprocessing.TimeoutError()

  class _Pool:
    def imap_unordered(self, fn, jids):
      del fn, jids
      return _It()

  update_metrics._prewarm_successful_refs_on_metrics_pool(
      [SimpleNamespace(jid="ok1"), SimpleNamespace(jid="ok2")],
      _Pipe(),
      _Pool(),
      metrics_manager=_Mgr(),
  )
  assert resets == ["reset"]
  # One partial success recorded + one soft-fail for unfinished.
  assert soft_fails.count(True) == 1
  assert soft_fails.count(False) == 1


@pytest.mark.machine_unit_mock
def test_maybe_reap_metrics_main_zombie_children_throttles(monkeypatch):
  """Local throttle must skip shared waitpid within the interval."""
  calls = []

  def _reap(*, context=""):
    calls.append(context)
    return [1]

  monkeypatch.setattr(update_metrics.metrics, "reap_zombie_children_of_self", _reap)
  monkeypatch.setattr(
      update_metrics.metrics, "warn_unreaped_zombie_children", lambda **k: None
  )
  monkeypatch.setattr(
      update_metrics.metrics, "_METRICS_MAIN_ZOMBIE_REAP_INTERVAL_S", 60.0
  )
  monkeypatch.setattr(
      update_metrics.metrics, "_last_metrics_main_zombie_reap_mono", 0.0
  )
  assert update_metrics._maybe_reap_metrics_main_zombie_children(context="first") is True
  assert update_metrics._maybe_reap_metrics_main_zombie_children(context="second") is False
  assert calls == ["first"]


@pytest.mark.machine_unit_mock
def test_scheduler_empty_pass_invokes_metrics_zombie_reap(monkeypatch):
  """Empty ready-queue idle path must invoke throttled metrics [main] zombie reap."""
  hygiene_contexts = []
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_mode", lambda: "global_fifo")
  monkeypatch.setattr(update_metrics, "close_old_connections", lambda: None)
  monkeypatch.setattr(update_metrics.gc, "collect", lambda: 0)
  monkeypatch.setattr(update_metrics, "shutdown_requested", [False])
  monkeypatch.setattr(update_metrics, "_start_candidate_rescan_thread", lambda **kwargs: None)
  monkeypatch.setattr(
      update_metrics,
      "_run_public_ef_artifacts_parallel_phase",
      lambda *_a, **_k: {
          "degraded": 0,
          "worker_exceptions": 0,
          "watchdog_timeouts": 0,
          "pending_tasks": 0,
      },
  )
  monkeypatch.setattr(
      update_metrics,
      "_maybe_reap_metrics_main_zombie_children",
      lambda *, context="": hygiene_contexts.append(context) or True,
  )
  monkeypatch.setattr(update_metrics, "_log_metrics_window_census", lambda *a, **k: None)
  monkeypatch.setattr(update_metrics, "STALL_EXIT_AFTER_SECONDS", 0.05)

  def _empty_producer(**kwargs):
    class _DoneProducer:
      def join(self, timeout=None):
        del timeout

    # Leave ready_queue empty; delay producer_done so the consumer hits empty_pass.
    def _finish():
      time.sleep(0.08)
      kwargs["producer_done"].set()

    threading.Thread(target=_finish, daemon=True).start()
    return _DoneProducer()

  monkeypatch.setattr(update_metrics, "_start_readiness_producer", _empty_producer)
  monkeypatch.setattr(update_metrics, "_jobs_queryset", lambda *a, **k: object())
  monkeypatch.setattr(update_metrics, "_iter_chunked_pks", lambda qs, _c: iter([]))

  class FakeMetrics:
    simple_metrics_list = {}
    complex_metrics_list = []

    def ensure_pool(self, pool_kind="metrics-pool"):
      return object()

    def close_pool(self):
      return None

    def reset_pool_hard(self):
      return None

    def run(self, jobs, pool=None):
      raise AssertionError("metrics run should not run on empty readiness")

  monkeypatch.setattr(update_metrics.metrics, "Metrics", lambda: FakeMetrics())
  try:
    update_metrics.update_metrics_for_dates([datetime(2025, 4, 10)], rerun=False)
  except update_metrics.MetricsSchedulerStallExit:
    pass
  assert "empty_pass" in hygiene_contexts


@pytest.mark.machine_unit_mock
def test_compute_batch_start_invokes_metrics_zombie_reap(monkeypatch):
  """Compute-batch start must invoke throttled metrics [main] zombie reap."""
  hygiene_contexts = []
  monkeypatch.setattr(update_metrics.cfg, "get_metrics_scheduler_mode", lambda: "global_fifo")
  monkeypatch.setattr(update_metrics, "_start_readiness_producer", _enqueue_chunks_from_date_states)
  monkeypatch.setattr(update_metrics, "close_old_connections", lambda: None)
  monkeypatch.setattr(update_metrics.gc, "collect", lambda: 0)
  monkeypatch.setattr(update_metrics, "shutdown_requested", [False])
  monkeypatch.setattr(update_metrics, "_start_candidate_rescan_thread", lambda **kwargs: None)
  monkeypatch.setattr(
      update_metrics,
      "_run_public_ef_artifacts_parallel_phase",
      lambda *_a, **_k: {
          "degraded": 0,
          "worker_exceptions": 0,
          "watchdog_timeouts": 0,
          "pending_tasks": 0,
      },
  )
  monkeypatch.setattr(
      update_metrics,
      "_maybe_reap_metrics_main_zombie_children",
      lambda *, context="": hygiene_contexts.append(context) or True,
  )
  monkeypatch.setattr(
      update_metrics,
      "_compute_jid_outcomes_batch",
      lambda *a, **k: [{"jid": 1001, "ok": True, "metrics_s": 0.0, "prewarm_s": 0.0}],
  )
  _patch_strict_readiness_batch(monkeypatch, lambda jids: list(jids))
  monkeypatch.setattr(update_metrics, "_jobs_queryset", lambda *a, **k: object())
  monkeypatch.setattr(
      update_metrics,
      "_iter_chunked_pks",
      lambda qs, _chunk: iter([([1001], 1)]),
  )

  class FakeMetrics:
    simple_metrics_list = {}
    complex_metrics_list = []

    def ensure_pool(self, pool_kind="metrics-pool"):
      return object()

    def close_pool(self):
      return None

    def reset_pool_hard(self):
      return None

    def run(self, jobs, pool=None):
      return []

  monkeypatch.setattr(update_metrics.metrics, "Metrics", lambda: FakeMetrics())
  monkeypatch.setattr(update_metrics, "persist_job_plot_artifacts_for_jid", lambda jid: None)
  update_metrics.update_metrics_for_dates([datetime(2025, 4, 10)], rerun=False)
  assert "compute_batch_start" in hygiene_contexts
