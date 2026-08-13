import contextlib
import signal
import threading
import time

import numpy as np
import pandas as pd
import pytest
from types import SimpleNamespace

from django.db.utils import OperationalError

from hpcperfstats.analysis.metrics.lib import metrics
from hpcperfstats.dbload.lib.multiprocessing_pool_health import MultiprocessingWorkerExitError


class _FakeJidTable:
  def __init__(self):
    self.jid = 123
    self.host_list = ["host1", "host2"]
    self.schema = {"cpu": ["user", "system"], "mem": ["used"]}

  def get_full_host_data_df(self, columns):
    # Two hosts share two wall-clock times: global distinct times = 2,
    # per-host distinct sum = 2 + 2 = 4 (invalidation / metrics_distinct_time_count).
    data = [
        {"host": "host1", "time": "2024-01-01T00:00:00Z", "type": "cpu", "event": "user", "value": 10},
        {"host": "host1", "time": "2024-01-01T00:00:00Z", "type": "cpu", "event": "system", "value": 5},
        {"host": "host1", "time": "2024-01-01T00:05:00Z", "type": "cpu", "event": "user", "value": 11},
        {"host": "host1", "time": "2024-01-01T00:05:00Z", "type": "cpu", "event": "system", "value": 6},
        {"host": "host2", "time": "2024-01-01T00:00:00Z", "type": "cpu", "event": "user", "value": 20},
        {"host": "host2", "time": "2024-01-01T00:00:00Z", "type": "cpu", "event": "system", "value": 10},
        {"host": "host2", "time": "2024-01-01T00:05:00Z", "type": "cpu", "event": "user", "value": 21},
        {"host": "host2", "time": "2024-01-01T00:05:00Z", "type": "cpu", "event": "system", "value": 11},
        {"host": "host1", "time": "2024-01-01T00:00:00Z", "type": "mem", "event": "used", "value": 100},
    ]
    df = pd.DataFrame(data)
    # Ensure only requested columns are returned; fill missing (e.g. arc).
    for col in columns:
      if col not in df.columns:
        df[col] = np.nan
    return df[list(columns)]


class _FakeJidTableSparseSlowTier:
  """Slow-tier event sampled only on sparse timestamps (two-tier monitor cadence)."""

  def __init__(self):
    self.jid = 789
    self.host_list = ["host1"]
    self.schema = {"host_tt": ["a", "b"]}

  def get_full_host_data_df(self, columns):
    data = [
        {"host": "host1", "time": "2024-01-01T00:00:00Z",
         "type": "host_tt", "event": "a", "value": 1000.0},
        {"host": "host1", "time": "2024-01-01T00:00:30Z",
         "type": "host_tt", "event": "a", "value": 1100.0},
        {"host": "host1", "time": "2024-01-01T00:10:00Z",
         "type": "host_tt", "event": "a", "value": 2000.0},
        {"host": "host1", "time": "2024-01-01T00:10:00Z",
         "type": "host_tt", "event": "b", "value": 2500.0},
    ]
    df = pd.DataFrame(data)
    for col in columns:
      if col not in df.columns:
        df[col] = np.nan
    return df[list(columns)]


def test_job_for_metrics_sparse_slow_tier_uses_nan_not_zero_fill():
  """Missing slow-tier samples at fast timestamps must not become counter value 0."""
  job = metrics._JobForMetrics(_FakeJidTableSparseSlowTier())
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


def test_job_for_metrics_builds_time_axis_and_host_stats():
  jt = _FakeJidTable()

  job = metrics._JobForMetrics(jt)

  # Times should be a sorted NumPy array with two unique timestamps (global axis).
  assert isinstance(job.times, np.ndarray)
  assert job.times.size == 2
  assert job.per_host_distinct_time_sum == 4
  assert job.hosts.keys() == {"host1", "host2"}
  assert "cpu" in job.schemas
  assert "mem" in job.schemas

  host1_cpu = job.hosts["host1"].stats["cpu"]["agg"]
  # Shape: (n_times, n_events) -> 2 timestamps x 2 events
  assert host1_cpu.shape == (2, 2)

  cm_cpu = job.cluster_mean_by_type["cpu"]
  assert cm_cpu.shape == (2, 2)
  # Host-averaged user/system at each global timestamp (user col 0, system 1)
  assert abs(cm_cpu[0, 0] - 15.0) < 1e-9
  assert abs(cm_cpu[0, 1] - 7.5) < 1e-9
  assert abs(cm_cpu[1, 0] - 16.0) < 1e-9
  assert abs(cm_cpu[1, 1] - 8.5) < 1e-9


class _FakeJidTableListyLabels:
  """Labels occasionally deserialize as list/tuple; grouping keys must stay hashable."""

  def __init__(self):
    self.jid = 456
    self.host_list = ["host1"]
    self.schema = {("cpu", "lane"): ["user", "system"]}

  def get_full_host_data_df(self, columns):
    data = [
        {"host": "host1", "time": "2024-01-01T00:00:00Z",
         "type": ["cpu", "lane"], "event": ["user"], "value": 10.0},
        {"host": "host1", "time": "2024-01-01T00:00:00Z",
         "type": ["cpu", "lane"], "event": ["system"], "value": 5.0},
        {"host": "host1", "time": "2024-01-01T00:05:00Z",
         "type": ["cpu", "lane"], "event": ["user"], "value": 11.0},
        {"host": "host1", "time": "2024-01-01T00:05:00Z",
         "type": ["cpu", "lane"], "event": ["system"], "value": 6.0},
    ]
    df = pd.DataFrame(data)
    for col in columns:
      if col not in df.columns:
        df[col] = np.nan
    return df[list(columns)]


def test_job_for_metrics_coerces_list_like_labels_before_groupby():
  job = metrics._JobForMetrics(_FakeJidTableListyLabels())
  assert "cpu,lane" in job.schemas
  assert job.hosts.keys() == {"host1"}
  agg = job.hosts["host1"].stats["cpu,lane"]["agg"]
  assert agg.shape == (2, 2)


def test_coerce_metrics_identity_str_stable():
  assert metrics._coerce_metrics_identity_str(["a", "b"]) == "a,b"
  assert metrics._coerce_metrics_identity_str(("cpu", "x")) == "cpu,x"
  assert metrics._coerce_metrics_identity_str({"z": 1}) == '{"z":1}'


def test_sanitize_metrics_compute_rows_coerces_list_identity_fields():
  rows = [{
      "jid": "j1",
      "type": ["procstat"],
      "metric": ["wallclock"],
      "units": [],
      "value": 1.0,
      "no_data_reason": None,
  }]
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
  metric_names = [["detail_gpu_count"], "avg_gpuutil", ("detail_fsio_llite_read_mb",)]
  out = metrics._coerced_metric_name_set(metric_names)
  assert "detail_gpu_count" in out
  assert "avg_gpuutil" in out
  assert "detail_fsio_llite_read_mb" in out


class _AlwaysTimeoutIterator:
  def next(self, timeout=None):
    raise metrics.multiprocessing.TimeoutError()


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
      return iter([{
          "jid": "j-worker",
          "status": "worker_db_error",
          "rows": [],
          "distinct_time_count": None,
          "error_type": "OperationalError",
          "error_message": "lost synchronization with server",
      }])

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
  release_calls = []
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_worker_memory.release_spawn_pool_worker_memory",
      lambda: release_calls.append(True),
  )
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
  assert release_calls == [True]


def test_drain_metrics_imap_returns_worker_compute_error_outcome():
  class _FakePoolWorkerComputeFailure:
    def imap_unordered(self, fn, tasks, chunksize=1):
      del fn, tasks, chunksize
      return iter([{
          "jid": "j-compute",
          "status": "worker_compute_error",
          "rows": [],
          "distinct_time_count": None,
          "error_type": "ValueError",
          "error_message": "cannot convert float NaN to integer",
      }])

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


def test_run_compute_metrics_timed_raises_when_exceeded():
  if not hasattr(signal, "SIGALRM"):
    pytest.skip("SIGALRM unavailable")

  class _Job:
    jid = "slow"

  class _Metrics:
    def compute_metrics(self, job):
      time.sleep(2.0)
      return {"rows": [], "distinct_time_count": 0}

  with pytest.raises(metrics.MetricsComputeJobTimeoutError):
    metrics._run_compute_metrics_timed(_Metrics(), _Job(), 0.05)


def test_unwrap_returns_worker_compute_error_on_per_job_timeout(monkeypatch):
  if not hasattr(signal, "SIGALRM"):
    pytest.skip("SIGALRM unavailable")

  class _Job:
    jid = "slow"

  class _Metrics:
    def compute_metrics(self, job):
      time.sleep(2.0)
      return {"rows": [], "distinct_time_count": 0}

  monkeypatch.setattr(metrics, "run_with_db_retry", lambda fn, attempts=2: fn())
  monkeypatch.setattr(metrics, "_resolve_metrics_run_per_job_timeout_s", lambda: 0.05)
  monkeypatch.setattr(
      metrics,
      "_pg_session_statement_timeout_for_metrics_worker",
      contextlib.nullcontext,
  )
  out = metrics._unwrap((_Metrics(), _Job()))
  assert out["status"] == "worker_compute_error"
  assert out["error_type"] == "MetricsComputeJobTimeoutError"


def test_drain_metrics_imap_returns_parent_persist_timeout_outcome(monkeypatch):
  class _FakePoolPersistTimeout:
    def imap_unordered(self, fn, tasks, chunksize=1):
      del fn, tasks, chunksize
      return iter([{
          "jid": "j-persist",
          "status": "ok",
          "rows": [{
              "jid": "j-persist",
              "type": "cpu",
              "metric": "avg_cpuusage",
              "units": "#cores",
              "value": 1.0,
              "no_data_reason": None,
          }],
          "distinct_time_count": 2,
          "error_type": None,
          "error_message": None,
      }])

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
      raise metrics.multiprocessing.TimeoutError()

  class _Pool:
    def imap_unordered(self, fn, tasks, chunksize=1):
      del fn, tasks, chunksize
      return _It()

  def _slow_persist(pl, *, wall_timeout_s=0.0):
    del wall_timeout_s
    persist_started_at.append(clock["t"])
    clock["t"] += 10.0
    return metrics._metrics_run_outcome("j1", ok=True, status="ok")

  monkeypatch.setattr(metrics, "_persist_metrics_payload_bounded", _slow_persist)
  monkeypatch.setattr(metrics, "abort_if_pool_workers_dead", lambda *a, **k: None)

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


def test_metrics_run_stall_with_owned_pool_returns_partial_outcomes(monkeypatch):
  fake_calls = {"terminate": 0}

  class _Proc:
    def __init__(self):
      self.pid = 111

    def join(self, timeout=None):
      return None

    def is_alive(self):
      return False

  class _OwnedPool:
    def __init__(self, processes=None, initializer=None, initargs=None, **kwargs):
      del processes, initializer, initargs, kwargs
      self._pool = [_Proc()]

    def terminate(self):
      fake_calls["terminate"] += 1

    def close(self):
      return None

  monkeypatch.setattr(metrics.multiprocessing, "Pool", _OwnedPool)

  partial = [
      metrics._metrics_run_outcome("j-done", ok=True, status="ok", persisted_rows=3),
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
  outcomes = m.run([SimpleNamespace(jid="j-done"), SimpleNamespace(jid="j-pending")])
  assert fake_calls["terminate"] >= 1
  assert len(outcomes) == 2
  by_jid = {o["jid"]: o for o in outcomes}
  assert by_jid["j-done"]["ok"] is True
  assert by_jid["j-pending"]["ok"] is False
  assert by_jid["j-pending"]["status"] == "worker_stall_timeout"


def test_metrics_run_stall_with_owned_pool_raises_when_no_partial_outcomes(monkeypatch):
  fake_calls = {"terminate": 0}

  class _Proc:
    pid = 111

    def join(self, timeout=None):
      return None

    def is_alive(self):
      return False

  class _OwnedPool:
    def __init__(self, processes=None, initializer=None, initargs=None, **kwargs):
      del processes, initializer, initargs, kwargs
      self._pool = [_Proc()]

    def terminate(self):
      fake_calls["terminate"] += 1

    def close(self):
      return None

  monkeypatch.setattr(metrics.multiprocessing, "Pool", _OwnedPool)

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


def test_reset_pool_hard_terminates_synchronously_after_detach(monkeypatch):
  calls = {"terminate": 0}

  def sync_terminate(active_pool, timeout_s):
    del active_pool, timeout_s
    calls["terminate"] += 1
    return True

  class _Pool:
    def terminate(self):
      return None

    _pool = []

  pool = _Pool()
  m = metrics.Metrics()
  m._shared_pool = pool
  m._shared_pool_kind = "metrics-pool"
  monkeypatch.setattr(metrics, "_terminate_pool_bounded", sync_terminate)

  m.reset_pool_hard()
  assert m._shared_pool is None
  assert calls["terminate"] == 1


def test_metrics_terminate_pool_delegates_to_shared_sigkill_reap(monkeypatch):
  """Metrics terminate must use shared helper (SIGKILL lingerers + zombie reap)."""
  calls = []

  def fake_shared(active_pool, timeout_s=30.0, *, context="", **kwargs):
    del kwargs
    calls.append({
        "pool": active_pool,
        "timeout_s": timeout_s,
        "context": context,
    })
    return True

  monkeypatch.setattr(metrics, "_shared_terminate_pool_bounded", fake_shared)
  assert metrics._terminate_pool_bounded(object(), 1.5) is True
  assert len(calls) == 1
  assert calls[0]["timeout_s"] == 1.5
  assert calls[0]["context"] == "metrics_pool"


def test_drain_metrics_imap_aborts_when_pool_worker_dead(monkeypatch):
  class _DeadProc:
    pid = 4242

    def is_alive(self):
      return False

  class _Pool:
    _pool = [_DeadProc()]

  with pytest.raises(MultiprocessingWorkerExitError):
    metrics._drain_metrics_imap(
        _Pool(),
        [("task",)],
        1,
        poll_timeout_s=0.01,
        stall_timeout_s=60.0,
    )


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


def test_pg_session_statement_timeout_for_metrics_worker_disables_when_zero(monkeypatch):
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
  monkeypatch.setattr(metrics.cfg, "get_db_statement_timeout_ms", lambda: 120000)
  monkeypatch.setattr(metrics, "connections", {"default": _Conn()})
  with metrics._pg_session_statement_timeout_for_metrics_worker():
    pass
  assert executed[0][0] == "SET statement_timeout = 0"
  assert executed[-1] == ("SET statement_timeout = %s", [120000])


def test_pg_session_statement_timeout_for_metrics_worker_sets_positive_ms(monkeypatch):
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
  monkeypatch.setattr(metrics.cfg, "get_db_statement_timeout_ms", lambda: 120000)
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
  monkeypatch.setattr(metrics.cfg, "get_db_statement_timeout_ms", lambda: 120000)
  monkeypatch.setattr(metrics, "connections", {"default": _Conn()})
  with metrics._pg_session_statement_timeout_for_metrics_worker():
    pass


def test_host_data_metric_rows_with_host_chunk_retry_splits_on_timeout(monkeypatch):
  """48-host / single-batch style: timeout on full chunk → halve and succeed."""
  hosts = ["h{0}.example.com".format(i) for i in range(8)]
  seen = []

  class _QS:
    def __init__(self, chunk):
      self._chunk = chunk

    def values(self, *args, **kwargs):
      del args, kwargs
      return self

    def order_by(self, *args):
      del args
      return self

    def __iter__(self):
      size = len(self._chunk)
      seen.append(size)
      if size == 8:
        raise OperationalError("canceling statement due to statement timeout")
      for h in self._chunk:
        yield {"host": h, "time": "t0", "value": 1.0}

  class _HD:
    objects = SimpleNamespace()

  def _filter(**kwargs):
    chunk = list(kwargs.get("host__in") or [])
    return _QS(chunk)

  _HD.objects.filter = _filter
  monkeypatch.setattr(
      "hpcperfstats.site.lib.machine.models.host_data",
      _HD,
  )
  monkeypatch.setattr(metrics, "close_old_connections", lambda: None)
  out = metrics._host_data_metric_rows_with_host_chunk_retry(
      hosts,
      {"time__gte": "a", "time__lte": "b"},
      "cpu",
      ["user"],
      "value",
  )
  assert seen == [8, 4, 4]
  assert len(out) == 8


def test_metric_type_events_feasible_skips_impossible_types():
  assert metrics._metric_type_events_feasible({}, "amd64_pmc", ["FLOPS"]) is True
  assert metrics._metric_type_events_feasible(
      {"cpu": ["user", "system"]}, "amd64_pmc", ["FLOPS"]
  ) is False
  assert metrics._metric_type_events_feasible(
      {"cpu": ["user", "system"]}, "cpu", ["user"]
  ) is True


def test_job_arc_skips_orm_when_schema_rules_out_type(monkeypatch):
  """Regression: empty vendor probes must not list(qs) until 900s SIGALRM."""
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
    raise AssertionError("job_arc must not query host_data for impossible types")

  _HD.objects.filter = _filter
  monkeypatch.setattr(
      "hpcperfstats.site.lib.machine.models.host_data",
      _HD,
  )
  m = metrics.Metrics()
  assert m.job_arc(jt, typename="amd64_pmc", events=["FLOPS"], conv=1e-9) is None
  assert filter_calls == []


def test_host_data_metric_rows_batched_uses_metrics_host_batch(monkeypatch):
  """~48-host jobs must not issue a single 64-host query by default."""
  n = metrics.METRICS_HOST_QUERY_BATCH + 2
  hosts = ["h{0}.x".format(i) for i in range(n)]
  chunk_sizes = []

  class _QS:
    def __init__(self, chunk):
      self._chunk = chunk

    def values(self, *args, **kwargs):
      del args, kwargs
      return self

    def order_by(self, *args):
      del args
      return self

    def __iter__(self):
      chunk_sizes.append(len(self._chunk))
      return iter(())

  class _HD:
    objects = SimpleNamespace()

  def _filter(**kwargs):
    return _QS(list(kwargs.get("host__in") or []))

  _HD.objects.filter = _filter
  monkeypatch.setattr(
      "hpcperfstats.site.lib.machine.models.host_data",
      _HD,
  )
  monkeypatch.setattr(metrics, "close_old_connections", lambda: None)
  rows = metrics._host_data_metric_rows_batched(
      {"time__gte": "a", "time__lte": "b"},
      hosts,
      "net",
      ["rx_bytes"],
      "arc",
  )
  assert rows == []
  assert chunk_sizes == [metrics.METRICS_HOST_QUERY_BATCH, 2]


def test_ensure_pool_recreates_when_alive_below_configured(monkeypatch):
  """hs04 signature: configured 24, alive 4 → terminate and recreate at 24."""
  created = []
  terminated = {"n": 0}

  class _AliveProc:
    def __init__(self, alive):
      self._alive = alive

    def is_alive(self):
      return self._alive

  class _Pool:
    def __init__(self, processes, initializer=None, initargs=()):
      del initializer, initargs
      self._processes = processes
      # Undersized live set when processes is the configured width and we
      # simulate attrition by attaching fewer alive workers after the fact.
      self._pool = [_AliveProc(True) for _ in range(processes)]
      created.append(self)

    def terminate(self):
      return None

    def close(self):
      return None

    def join(self):
      return None

  def sync_terminate(active_pool, timeout_s):
    del active_pool, timeout_s
    terminated["n"] += 1
    return True

  monkeypatch.setattr(metrics.multiprocessing, "Pool", _Pool)
  monkeypatch.setattr(metrics, "_terminate_pool_bounded", sync_terminate)
  monkeypatch.setattr(
      metrics.cfg, "get_metrics_pool_processes", lambda: 24
  )

  m = metrics.Metrics()
  first = m.ensure_pool(pool_kind="metrics-pool")
  assert first._processes == 24
  assert len(created) == 1

  # Simulate attrition: only 4 workers still alive (hs04 production).
  first._pool = [_AliveProc(True) for _ in range(4)]
  second = m.ensure_pool(pool_kind="metrics-pool")
  assert terminated["n"] == 1
  assert second is not first
  assert second._processes == 24
  assert len(created) == 2
  assert m._shared_pool is second


def test_ensure_pool_reuses_full_width_pool(monkeypatch):
  created = []

  class _AliveProc:
    def is_alive(self):
      return True

  class _Pool:
    def __init__(self, processes, initializer=None, initargs=()):
      del initializer, initargs
      self._processes = processes
      self._pool = [_AliveProc() for _ in range(processes)]
      created.append(self)

  monkeypatch.setattr(metrics.multiprocessing, "Pool", _Pool)
  monkeypatch.setattr(
      metrics.cfg, "get_metrics_pool_processes", lambda: 24
  )

  m = metrics.Metrics()
  first = m.ensure_pool(pool_kind="metrics-pool")
  second = m.ensure_pool(pool_kind="metrics-pool")
  assert first is second
  assert len(created) == 1


def test_ensure_pool_logs_configured_and_alive_after_create(monkeypatch):
  logs = []

  class _AliveProc:
    def is_alive(self):
      return True

  class _Pool:
    def __init__(self, processes, initializer=None, initargs=()):
      del initializer, initargs
      self._processes = processes
      self._pool = [_AliveProc() for _ in range(processes)]

  monkeypatch.setattr(metrics.multiprocessing, "Pool", _Pool)
  monkeypatch.setattr(
      metrics.cfg, "get_metrics_pool_processes", lambda: 24
  )
  monkeypatch.setattr(
      metrics, "log_print", lambda msg, flush=False: logs.append(str(msg))
  )

  m = metrics.Metrics()
  m.ensure_pool(pool_kind="metrics-pool")
  joined = "\n".join(logs)
  assert "configured=24" in joined
  assert "alive=24" in joined
  assert "pool_kind=metrics-pool" in joined


def test_metrics_health_checks_supply_expected_worker_count(monkeypatch):
  seen = []

  def fake_abort(pool, *, context="", pool_health_context=None):
    del pool
    seen.append({
        "context": context,
        "pool_health_context": dict(pool_health_context or {}),
    })

  monkeypatch.setattr(metrics, "abort_if_pool_workers_dead", fake_abort)
  monkeypatch.setattr(
      metrics.cfg, "get_metrics_pool_processes", lambda: 24
  )

  metrics.abort_if_metrics_pool_workers_dead(
      object(),
      context="unit-test",
  )
  assert len(seen) == 1
  assert seen[0]["context"] == "unit-test"
  assert seen[0]["pool_health_context"].get("expected_pool_workers") == 24

  drain_seen = []

  def fake_metrics_abort(pool, *, context=""):
    del pool
    drain_seen.append(context)

  monkeypatch.setattr(
      metrics, "abort_if_metrics_pool_workers_dead", fake_metrics_abort
  )

  class _ReadyIter:
    def next(self, timeout=None):
      del timeout
      raise StopIteration

  class _Pool:
    def imap_unordered(self, *args, **kwargs):
      del args, kwargs
      return _ReadyIter()

  # Drain catches StopIteration and returns; preflight abort must still run.
  metrics._drain_metrics_imap(
      _Pool(),
      [("metrics", SimpleNamespace(jid="j1"))],
      1,
      poll_timeout_s=0.01,
      stall_timeout_s=60.0,
  )
  assert drain_seen
  assert "Metrics.run imap_unordered (preflight)" in drain_seen[0]


def test_metrics_worker_exit_error_recreates_pool(monkeypatch):
  """Non-OOM MultiprocessingWorkerExitError must reset+recreate, not shrink."""
  created = []

  class _AliveProc:
    def is_alive(self):
      return True

  class _Pool:
    def __init__(self, processes, initializer=None, initargs=()):
      del initializer, initargs
      self._processes = processes
      self._pool = [_AliveProc() for _ in range(processes)]
      created.append(self)

  def sync_terminate(active_pool, timeout_s):
    del active_pool, timeout_s
    return True

  monkeypatch.setattr(metrics.multiprocessing, "Pool", _Pool)
  monkeypatch.setattr(metrics, "_terminate_pool_bounded", sync_terminate)
  monkeypatch.setattr(
      metrics.cfg, "get_metrics_pool_processes", lambda: 24
  )

  m = metrics.Metrics()
  first = m.ensure_pool(pool_kind="metrics-pool")
  assert first._processes == 24

  exc = MultiprocessingWorkerExitError(
      "dead",
      dead_pids=[101],
      context="metrics",
      exit_code=0,
      likely_cause="recycle_stuck",
      diagnostics={
          "dead_workers": [{"pid": 101, "exitcode": 0}],
          "alive_workers": 4,
          "likely_cause": "recycle_stuck",
      },
  )
  recreated = m.handle_pool_worker_exit(exc)
  assert m._shared_pool is recreated
  assert recreated is not first
  assert recreated._processes == 24
  assert len(created) == 2


def test_metrics_worker_exit_oom_remains_fatal(monkeypatch):
  class _AliveProc:
    def is_alive(self):
      return True

  class _Pool:
    def __init__(self, processes, initializer=None, initargs=()):
      del initializer, initargs
      self._processes = processes
      self._pool = [_AliveProc() for _ in range(processes)]

  monkeypatch.setattr(metrics.multiprocessing, "Pool", _Pool)
  monkeypatch.setattr(
      metrics.cfg, "get_metrics_pool_processes", lambda: 24
  )

  m = metrics.Metrics()
  m.ensure_pool(pool_kind="metrics-pool")
  exc = MultiprocessingWorkerExitError(
      "oom",
      dead_pids=[202],
      context="metrics",
      exit_code=137,
      likely_cause="sigkill",
      diagnostics={
          "dead_workers": [{"pid": 202, "exitcode": -9}],
          "likely_cause": "sigkill",
          "cgroup_oom_kill": 1,
      },
  )
  with pytest.raises(MultiprocessingWorkerExitError):
    m.handle_pool_worker_exit(exc)
  assert m._shared_pool is not None

