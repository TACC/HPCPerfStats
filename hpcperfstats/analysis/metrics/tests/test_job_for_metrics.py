import contextlib
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from django.db.utils import OperationalError

from hpcperfstats.analysis.metrics.lib import metrics


def _two_host_table():
  """Two hosts share two wall-clock times: per-host distinct sum = 2 + 2 = 4."""
  from hpcperfstats.analysis.metrics.lib.columnar.testing import (
    table_from_records,
  )

  t0 = "2024-01-01T00:00:00+00:00"
  t1 = "2024-01-01T00:05:00+00:00"
  rows = []
  for host, user0, sys0 in (("host1", 10, 5), ("host2", 20, 10)):
    rows.append(
      (pd.Timestamp(t0), host, "cpu", "user", None, float(user0), None, None)
    )
    rows.append(
      (pd.Timestamp(t0), host, "cpu", "system", None, float(sys0), None, None)
    )
    rows.append(
      (
        pd.Timestamp(t1),
        host,
        "cpu",
        "user",
        None,
        float(user0 + 1),
        None,
        None,
      )
    )
    rows.append(
      (
        pd.Timestamp(t1),
        host,
        "cpu",
        "system",
        None,
        float(sys0 + 1),
        None,
        None,
      )
    )
  rows.append(
    (pd.Timestamp(t0), "host1", "mem", "used", None, 100.0, None, None)
  )
  return table_from_records(
    rows, jid="123", schema={"cpu": ["user", "system"], "mem": ["used"]}
  )


def test_metrics_view_sparse_slow_tier_uses_nan_not_zero_fill():
  """Missing slow-tier samples at fast timestamps must not become counter value 0."""
  from hpcperfstats.analysis.metrics.lib.columnar.testing import (
    table_from_records,
  )

  table = table_from_records(
    [
      (
        pd.Timestamp("2024-01-01T00:00:00Z"),
        "host1",
        "host_tt",
        "a",
        None,
        1000.0,
        None,
        None,
      ),
      (
        pd.Timestamp("2024-01-01T00:00:30Z"),
        "host1",
        "host_tt",
        "a",
        None,
        1100.0,
        None,
        None,
      ),
      (
        pd.Timestamp("2024-01-01T00:10:00Z"),
        "host1",
        "host_tt",
        "a",
        None,
        2000.0,
        None,
        None,
      ),
      (
        pd.Timestamp("2024-01-01T00:10:00Z"),
        "host1",
        "host_tt",
        "b",
        None,
        2500.0,
        None,
        None,
      ),
    ],
    jid="789",
    schema={"host_tt": ["a", "b"]},
  )
  job = table.metrics_view()
  agg = job.hosts["host1"].stats["host_tt"]["agg"]
  assert agg.shape == (3, 2)
  # Fast event a present at all three global timestamps.
  assert agg[0, 0] == 1000.0
  assert agg[1, 0] == 1100.0
  assert agg[2, 0] == 2000.0
  # Slow event b only at the third timestamp; earlier slots NaN (not 0).
  assert np.isnan(agg[0, 1])
  assert np.isnan(agg[1, 1])
  assert agg[2, 1] == 2500.0
  rates = metrics._per_interval_rate(agg[:, 1], job.times)
  assert np.isnan(rates[0])
  assert np.isnan(rates[1])


def test_metrics_view_builds_time_axis_and_host_stats():
  job = _two_host_table().metrics_view()
  assert job.jid == "123"
  assert job.times.shape == (2,)
  assert job.times[1] - job.times[0] == 300.0
  assert set(job.hosts) == {"host1", "host2"}
  assert list(job.schemas["cpu"].events) == ["user", "system"]
  agg1 = job.hosts["host1"].stats["cpu"]["agg"]
  assert agg1.tolist() == [[10.0, 5.0], [11.0, 6.0]]
  agg2 = job.hosts["host2"].stats["cpu"]["agg"]
  assert agg2.tolist() == [[20.0, 10.0], [21.0, 11.0]]
  # host2 has no mem rows: no mem stats entry (utils.get_type skips the host).
  assert "mem" not in job.hosts["host2"].stats
  assert job.per_host_distinct_time_sum == 4
  cm_cpu = job.cluster_mean_by_type["cpu"]
  assert abs(cm_cpu[0, 0] - 15.0) < 1e-9
  assert abs(cm_cpu[0, 1] - 7.5) < 1e-9
  assert abs(cm_cpu[1, 0] - 16.0) < 1e-9
  assert abs(cm_cpu[1, 1] - 8.5) < 1e-9


def test_compute_metrics_persists_per_host_distinct_sum(monkeypatch):
  """Sample Count must come from the job table distinct sum, not stub live getter."""
  table = _two_host_table()
  monkeypatch.setattr(metrics, "open_job_table", lambda job, **_kw: table)
  monkeypatch.setattr(
    metrics, "gpu_summary_tuple", lambda jt: (None, None, None, None)
  )
  monkeypatch.setattr(
    metrics, "compute_job_detail_fsio_metric_rows", lambda jt: []
  )

  m = metrics.Metrics()
  m.simple_metrics_list = {}
  m.complex_metrics_list = []
  job = SimpleNamespace(
    jid=123,
    telemetry_first_time=None,
    telemetry_last_time=None,
    host_data_schema_json=None,
  )
  monkeypatch.setattr(
    metrics,
    "_in_window_telemetry_bounds_for_job",
    lambda job_obj: (None, None),
  )
  # Stub getter would wrongly echo 0; persist path must ignore it.
  monkeypatch.setattr(
    "hpcperfstats.site.lib.machine.job_plot_artifacts.get_live_distinct_time_count_for_jid",
    lambda jid: 0,
  )
  out = m.compute_metrics(job)
  assert out["distinct_time_count"] == 4
  # S4: the schema travels in the payload; the worker never saves the job.
  assert out["host_data_schema"] == table.schema


def test_coerce_metrics_identity_str_stable():
  assert metrics._coerce_metrics_identity_str(["a", "b"]) == "a,b"
  assert metrics._coerce_metrics_identity_str(("cpu", "x")) == "cpu,x"
  assert metrics._coerce_metrics_identity_str({"z": 1}) == '{"z":1}'


def test_sanitize_metrics_compute_rows_coerces_list_identity_fields():
  rows = [
    {
      "jid": "j1",
      "type": ["procstat"],
      "metric": ["wallclock"],
      "units": [],
      "value": 1.0,
      "no_data_reason": None,
    }
  ]
  out = metrics._sanitize_metrics_compute_rows(rows)
  assert len(out) == 1
  assert out[0]["jid"] == "j1"
  assert out[0]["type"] == "procstat"
  assert out[0]["metric"] == "wallclock"
  assert out[0]["units"] == ""


def test_coerced_catalog_metric_is_hashable_for_set_membership():
  entry = {"metric": ["oops"], "type": "job", "units": "s"}
  catalog_metric = metrics._coerce_metrics_identity_str(entry["metric"])
  assert catalog_metric == "oops"
  assert catalog_metric in frozenset({"oops", "other"})


def test_coerced_metric_name_set_normalizes_unhashable_metric_names():
  metric_names = [
    ["detail_gpu_count"],
    "avg_gpuutil",
    ("detail_fsio_llite_read_mb",),
  ]
  out = metrics._coerced_metric_name_set(metric_names)
  assert "detail_gpu_count" in out
  assert "avg_gpuutil" in out
  assert "detail_fsio_llite_read_mb" in out


class _AlwaysTimeoutIterator:
  def next(self, timeout=None):
    raise TimeoutError()


class _FakePoolTimeout:
  def __init__(self):
    self.seen_chunksize = None

  def imap_unordered(self, fn, tasks, chunksize=1):
    self.seen_chunksize = chunksize
    return _AlwaysTimeoutIterator()


def test_drain_metrics_imap_times_out_when_no_worker_progress():
  pool = _FakePoolTimeout()
  with pytest.raises(TimeoutError):
    metrics._drain_metrics_imap(
      pool,
      tasks=[("m", "j1")],
      chunksize=8,
      poll_timeout_s=0.0,
      stall_timeout_s=0.0,
    )
  assert pool.seen_chunksize == 1


def test_drain_metrics_imap_supports_generator_without_next():
  def _gen():
    yield {"rows": [], "distinct_time_count": 1}

  class _FakePoolGenerator:
    def imap_unordered(self, fn, tasks, chunksize=1):
      return _gen()

  # Should not raise AttributeError("'generator' object has no attribute 'next'").
  metrics._drain_metrics_imap(
    _FakePoolGenerator(),
    tasks=[("m", "j1")],
    chunksize=1,
    poll_timeout_s=0.0,
    stall_timeout_s=0.5,
  )


def test_drain_metrics_imap_returns_explicit_worker_failure_outcome():
  class _FakePoolWorkerFailure:
    def imap_unordered(self, fn, tasks, chunksize=1):
      del fn, tasks, chunksize
      return iter(
        [
          {
            "jid": "j-worker",
            "status": "worker_db_error",
            "rows": [],
            "distinct_time_count": None,
            "error_type": "OperationalError",
            "error_message": "lost synchronization with server",
          }
        ]
      )

  out = metrics._drain_metrics_imap(
    _FakePoolWorkerFailure(),
    tasks=[("m", "j-worker")],
    chunksize=1,
    poll_timeout_s=0.0,
    stall_timeout_s=0.5,
  )
  assert len(out) == 1
  assert out[0]["jid"] == "j-worker"
  assert out[0]["ok"] is False
  assert out[0]["status"] == "worker_db_error"
  assert out[0]["error_type"] == "OperationalError"


def test_unwrap_returns_worker_compute_error_on_value_error(monkeypatch):
  class _Job:
    jid = "j-bad"

  class _Metrics:
    def compute_metrics(self, job):
      del job
      raise ValueError("cannot convert float NaN to integer")

  monkeypatch.setattr(metrics, "run_with_db_retry", lambda fn, attempts=2: fn())
  monkeypatch.setattr(
    metrics,
    "_pg_session_statement_timeout_for_metrics_worker",
    contextlib.nullcontext,
  )
  out = metrics._unwrap((_Metrics(), _Job()))
  assert out["status"] == "worker_compute_error"
  assert out["jid"] == "j-bad"
  assert out["error_type"] == "ValueError"
  assert out["rows"] == []


def test_drain_metrics_imap_returns_worker_compute_error_outcome():
  class _FakePoolWorkerComputeFailure:
    def imap_unordered(self, fn, tasks, chunksize=1):
      del fn, tasks, chunksize
      return iter(
        [
          {
            "jid": "j-compute",
            "status": "worker_compute_error",
            "rows": [],
            "distinct_time_count": None,
            "error_type": "ValueError",
            "error_message": "cannot convert float NaN to integer",
          }
        ]
      )

  out = metrics._drain_metrics_imap(
    _FakePoolWorkerComputeFailure(),
    tasks=[("m", "j-compute")],
    chunksize=1,
    poll_timeout_s=0.0,
    stall_timeout_s=0.5,
  )
  assert len(out) == 1
  assert out[0]["jid"] == "j-compute"
  assert out[0]["ok"] is False
  assert out[0]["status"] == "worker_compute_error"
  assert out[0]["error_type"] == "ValueError"


def test_drain_metrics_imap_returns_parent_persist_timeout_outcome(monkeypatch):
  class _FakePoolPersistTimeout:
    def imap_unordered(self, fn, tasks, chunksize=1):
      del fn, tasks, chunksize
      return iter(
        [
          {
            "jid": "j-persist",
            "status": "ok",
            "rows": [
              {
                "jid": "j-persist",
                "type": "cpu",
                "metric": "avg_cpuusage",
                "units": "#cores",
                "value": 1.0,
                "no_data_reason": None,
              }
            ],
            "distinct_time_count": 2,
            "error_type": None,
            "error_message": None,
          }
        ]
      )

  monkeypatch.setattr(metrics, "run_with_db_retry", lambda fn, attempts=2: fn())

  def _raise_timeout(job_rows, distinct_time_count, **kwargs):
    del job_rows, distinct_time_count, kwargs
    raise metrics.DatabaseError("canceling statement due to statement timeout")

  monkeypatch.setattr(metrics, "_persist_metrics_batch", _raise_timeout)

  out = metrics._drain_metrics_imap(
    _FakePoolPersistTimeout(),
    tasks=[("m", "j-persist")],
    chunksize=1,
    poll_timeout_s=0.0,
    stall_timeout_s=0.5,
  )
  assert len(out) == 1
  assert out[0]["jid"] == "j-persist"
  assert out[0]["ok"] is False
  assert out[0]["status"] == "parent_persist_timeout"
  assert out[0]["error_type"] == "DatabaseError"


def test_persist_metrics_payload_empty_rows_is_failure_not_success(
  monkeypatch,
):
  """S12: an ok payload with no rows must not report a persisted job."""
  calls = []
  monkeypatch.setattr(
    metrics, "_persist_metrics_batch", lambda *a, **k: calls.append(a)
  )
  out = metrics._persist_metrics_payload(
    {"jid": "j-empty", "status": "ok", "rows": [], "distinct_time_count": 3}
  )
  assert out["ok"] is False
  assert out["status"] == "empty_rows"
  assert out["persisted_rows"] == 0
  assert calls == []


def test_drain_metrics_imap_stall_clock_waits_for_persist(monkeypatch):
  """Stall clock must not refresh until parent persist returns (not at worker return)."""
  clock = {"t": 1000.0}
  monkeypatch.setattr(metrics.time, "monotonic", lambda: clock["t"])
  persist_started_at = []

  payload = {
    "jid": "j1",
    "status": "ok",
    "rows": [],
    "distinct_time_count": 1,
    "error_type": None,
    "error_message": None,
  }

  class _It:
    n = 0

    def next(self, timeout=None):
      del timeout
      _It.n += 1
      if _It.n == 1:
        return payload
      clock["t"] += 0.25
      raise TimeoutError()

  class _Pool:
    def imap_unordered(self, fn, tasks, chunksize=1):
      del fn, tasks, chunksize
      return _It()

  def _slow_persist(pl):
    del pl
    persist_started_at.append(clock["t"])
    clock["t"] += 10.0
    return metrics._metrics_run_outcome("j1", ok=True, status="ok")

  monkeypatch.setattr(metrics, "_persist_metrics_payload", _slow_persist)

  with pytest.raises(metrics.MetricsRunWorkerStallError) as ei:
    metrics._drain_metrics_imap(
      _Pool(),
      tasks=[("m", "j1"), ("m", "j2")],
      chunksize=1,
      poll_timeout_s=0.05,
      stall_timeout_s=1.0,
    )
  assert persist_started_at == [1000.0]
  # Progress clock starts after the 10s persist, so stall needs ~1s more.
  assert clock["t"] >= 1011.0
  assert float(ei.value.stalled_for_s) >= 1.0
  assert len(ei.value.partial_outcomes) == 1
  assert ei.value.partial_outcomes[0]["jid"] == "j1"


def test_metrics_run_stall_with_owned_pool_returns_partial_outcomes(
  monkeypatch,
):
  fake_calls = {"terminate": 0}

  class _OwnedPool:
    def __init__(self, **kwargs):
      del kwargs

    def terminate(self):
      fake_calls["terminate"] += 1

    def close(self):
      return None

    def join(self):
      return None

  monkeypatch.setattr(metrics, "SyncTimedbThreadPool", _OwnedPool)

  partial = [
    metrics._metrics_run_outcome(
      "j-done", ok=True, status="ok", persisted_rows=3
    ),
  ]

  def _raise_stall(*args, **kwargs):
    raise metrics.MetricsRunWorkerStallError(
      stalled_for_s=999.0,
      message="stall",
      pool_reset_confirmed=False,
      partial_outcomes=partial,
      pending_jobs=[SimpleNamespace(jid="j-pending")],
    )

  monkeypatch.setattr(metrics, "_drain_metrics_imap", _raise_stall)

  m = metrics.Metrics()
  outcomes = m.run(
    [SimpleNamespace(jid="j-done"), SimpleNamespace(jid="j-pending")]
  )
  assert fake_calls["terminate"] >= 1
  assert len(outcomes) == 2
  by_jid = {o["jid"]: o for o in outcomes}
  assert by_jid["j-done"]["ok"] is True
  assert by_jid["j-pending"]["ok"] is False
  assert by_jid["j-pending"]["status"] == "worker_stall_timeout"


def test_metrics_run_stall_with_owned_pool_raises_when_no_partial_outcomes(
  monkeypatch,
):
  fake_calls = {"terminate": 0}

  class _OwnedPool:
    def __init__(self, **kwargs):
      del kwargs

    def terminate(self):
      fake_calls["terminate"] += 1

    def close(self):
      return None

    def join(self):
      return None

  monkeypatch.setattr(metrics, "SyncTimedbThreadPool", _OwnedPool)

  def _raise_stall(*args, **kwargs):
    raise metrics.MetricsRunWorkerStallError(
      stalled_for_s=999.0,
      message="stall",
      pool_reset_confirmed=False,
    )

  monkeypatch.setattr(metrics, "_drain_metrics_imap", _raise_stall)

  m = metrics.Metrics()
  with pytest.raises(metrics.MetricsRunWorkerStallError) as excinfo:
    m.run([SimpleNamespace(jid="j1")])
  assert fake_calls["terminate"] >= 1
  assert excinfo.value.pool_reset_confirmed is True


def test_reset_pool_hard_detaches_and_cancels_pending_threads():
  calls = {"terminate": 0}

  class _Pool:
    def terminate(self):
      calls["terminate"] += 1

  pool = _Pool()
  m = metrics.Metrics()
  m._shared_pool = pool
  m._shared_pool_kind = "metrics-pool"

  m.reset_pool_hard()
  assert m._shared_pool is None
  assert calls["terminate"] == 1


def test_metrics_pool_worker_finally_closes_all_thread_local_connections(
  monkeypatch,
):
  """Detached pool threads must close Django backends (CONN_MAX_AGE never fires)."""
  calls = []
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_session_executor.connections.close_all",
    lambda: calls.append("close_all"),
  )
  monkeypatch.setattr(
    metrics.cfg,
    "get_metrics_pool_processes",
    lambda: 1,
  )
  m = metrics.Metrics()
  pool = m.ensure_pool(pool_kind="metrics-pool")
  try:
    assert pool.apply_async(lambda: 7).get(timeout=5) == 7
  finally:
    m.close_pool()
  assert "close_all" in calls


def test_metrics_run_stall_with_shared_pool_calls_reset(monkeypatch):
  class _SharedPool:
    pass

  partial = [
    metrics._metrics_run_outcome("j2", ok=True, status="ok", persisted_rows=1),
  ]

  def _raise_stall(*args, **kwargs):
    raise metrics.MetricsRunWorkerStallError(
      stalled_for_s=321.0,
      message="stall",
      pool_reset_confirmed=False,
      partial_outcomes=partial,
      pending_jobs=[SimpleNamespace(jid="j3")],
    )

  monkeypatch.setattr(metrics, "_drain_metrics_imap", _raise_stall)
  called = {"n": 0}

  m = metrics.Metrics()

  def _reset():
    called["n"] += 1

  monkeypatch.setattr(m, "reset_pool_hard", _reset)

  outcomes = m.run(
    [SimpleNamespace(jid="j2"), SimpleNamespace(jid="j3")],
    pool=_SharedPool(),
  )
  assert called["n"] == 1
  assert len(outcomes) == 2
  assert outcomes[1]["status"] == "worker_stall_timeout"


def test_pg_session_statement_timeout_for_metrics_worker_disables_when_zero(
  monkeypatch,
):
  executed = []

  class _Cursor:
    def __enter__(self):
      return self

    def __exit__(self, *args):
      return False

    def execute(self, sql, params=None):
      executed.append((sql, params))

  class _Conn:
    vendor = "postgresql"

    def cursor(self):
      return _Cursor()

  monkeypatch.setattr(
    metrics.cfg, "get_metrics_worker_statement_timeout_ms", lambda: 0
  )
  monkeypatch.setattr(
    metrics.cfg, "get_db_statement_timeout_ms", lambda: 120000
  )
  monkeypatch.setattr(metrics, "connections", {"default": _Conn()})
  with metrics._pg_session_statement_timeout_for_metrics_worker():
    pass
  assert executed[0][0] == "SET statement_timeout = 0"
  assert executed[-1] == ("SET statement_timeout = %s", [120000])


def test_pg_session_statement_timeout_for_metrics_worker_sets_positive_ms(
  monkeypatch,
):
  executed = []

  class _Cursor:
    def __enter__(self):
      return self

    def __exit__(self, *args):
      return False

    def execute(self, sql, params=None):
      executed.append((sql, params))

  class _Conn:
    vendor = "postgresql"

    def cursor(self):
      return _Cursor()

  monkeypatch.setattr(
    metrics.cfg, "get_metrics_worker_statement_timeout_ms", lambda: 600000
  )
  monkeypatch.setattr(
    metrics.cfg, "get_db_statement_timeout_ms", lambda: 120000
  )
  monkeypatch.setattr(metrics, "connections", {"default": _Conn()})
  with metrics._pg_session_statement_timeout_for_metrics_worker():
    pass
  assert executed[0] == ("SET statement_timeout = %s", [600000])
  assert executed[-1] == ("SET statement_timeout = %s", [120000])


def test_pg_session_statement_timeout_for_metrics_worker_restore_swallows_db_error(
  monkeypatch,
):
  class _Cursor:
    def __init__(self, fail_restore=False):
      self._fail_restore = fail_restore
      self._n = 0

    def __enter__(self):
      return self

    def __exit__(self, *args):
      return False

    def execute(self, sql, params=None):
      del params
      self._n += 1
      if self._fail_restore and "statement_timeout" in sql and self._n > 1:
        raise OperationalError("connection already closed")

  class _Conn:
    vendor = "postgresql"

    def __init__(self):
      self._cursors = 0

    def cursor(self):
      self._cursors += 1
      return _Cursor(fail_restore=self._cursors > 1)

  monkeypatch.setattr(
    metrics.cfg, "get_metrics_worker_statement_timeout_ms", lambda: 0
  )
  monkeypatch.setattr(
    metrics.cfg, "get_db_statement_timeout_ms", lambda: 120000
  )
  monkeypatch.setattr(metrics, "connections", {"default": _Conn()})
  with metrics._pg_session_statement_timeout_for_metrics_worker():
    pass


def test_metric_type_events_feasible_skips_impossible_types():
  assert (
    metrics._metric_type_events_feasible({}, "amd64_pmc", ["FLOPS"]) is True
  )
  assert (
    metrics._metric_type_events_feasible(
      {"cpu": ["user", "system"]}, "amd64_pmc", ["FLOPS"]
    )
    is False
  )
  assert (
    metrics._metric_type_events_feasible(
      {"cpu": ["user", "system"]}, "cpu", ["user"]
    )
    is True
  )


def test_job_arc_skips_orm_when_schema_rules_out_type(monkeypatch):
  """Empty vendor probes must not spend the database statement budget."""
  from django.utils import timezone as django_tz

  t0 = django_tz.now()
  jt = SimpleNamespace(
    schema={"cpu": ["user", "system", "idle"]},
    _base_filter={
      "time__gte": t0,
      "time__lte": t0,
      "host__in": ["h1.x"],
    },
  )
  filter_calls = []

  class _HD:
    objects = SimpleNamespace()

  def _filter(**kwargs):
    filter_calls.append(kwargs)
    raise AssertionError(
      "job_arc must not query host_data for impossible types"
    )

  _HD.objects.filter = _filter
  monkeypatch.setattr(
    "hpcperfstats.site.lib.machine.models.host_data",
    _HD,
  )
  m = metrics.Metrics()
  assert (
    m.job_arc(jt, typename="amd64_pmc", events=["FLOPS"], conv=1e-9) is None
  )
  assert filter_calls == []


def test_ensure_pool_creates_titled_in_process_threads(monkeypatch):
  monkeypatch.setattr(metrics.cfg, "get_metrics_pool_processes", lambda: 3)
  m = metrics.Metrics()
  pool = m.ensure_pool(pool_kind="metrics-pool")
  try:
    assert pool._processes == 3
    assert pool.process_title == "update_metrics.py"
    assert pool.thread_role == "metrics-pool"
    assert pool.is_active is True
  finally:
    m.close_pool()


def test_ensure_pool_reuses_full_width_pool(monkeypatch):
  monkeypatch.setattr(metrics.cfg, "get_metrics_pool_processes", lambda: 2)
  m = metrics.Metrics()
  first = m.ensure_pool(pool_kind="metrics-pool")
  second = m.ensure_pool(pool_kind="metrics-pool")
  try:
    assert first is second
  finally:
    m.close_pool()


def test_ensure_pool_switches_thread_role_with_fresh_executor(monkeypatch):
  monkeypatch.setattr(metrics.cfg, "get_metrics_pool_processes", lambda: 2)
  m = metrics.Metrics()
  first = m.ensure_pool(pool_kind="public-ef-pool")
  second = m.ensure_pool(pool_kind="metrics-pool")
  try:
    assert second is not first
    assert first.is_active is False
    assert second.thread_role == "metrics-pool"
  finally:
    m.close_pool()


def test_ensure_pool_logs_configured_and_alive_after_create(monkeypatch):
  logs = []

  monkeypatch.setattr(metrics.cfg, "get_metrics_pool_processes", lambda: 24)
  monkeypatch.setattr(
    metrics, "log_print", lambda msg, flush=False: logs.append(str(msg))
  )

  m = metrics.Metrics()
  try:
    m.ensure_pool(pool_kind="metrics-pool")
    joined = "\n".join(logs)
    assert "configured=24" in joined
    assert "pool_kind=metrics-pool" in joined
  finally:
    m.close_pool()
