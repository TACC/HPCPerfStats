import numpy as np
import pandas as pd
import pytest

from hpcperfstats.analysis.metrics import metrics


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
    # Ensure only requested columns are returned
    return df[columns]


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
    return pd.DataFrame(data)[columns]


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
  def imap_unordered(self, fn, tasks, chunksize=1):
    return _AlwaysTimeoutIterator()


def test_drain_metrics_imap_times_out_when_no_worker_progress():
  with pytest.raises(TimeoutError):
    metrics._drain_metrics_imap(
        _FakePoolTimeout(),
        tasks=[("m", "j1")],
        chunksize=1,
        poll_timeout_s=0.0,
        stall_timeout_s=0.0,
    )


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

