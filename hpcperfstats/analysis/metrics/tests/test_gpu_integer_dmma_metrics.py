"""avg_integer_active and avg_tensor_dmma_active from nvidia_gpu pipe keys.

The monitor publishes ``integer_active`` (integer pipe busy share) and
``tensor_dmma_active`` (FP64 tensor-core busy share) as ratio percents, like
``tensor_hmma_active``. ``tensor_dmma_active`` is NOT_PRESENT on the DCGM
path (A100, monitor 3.0 hosts) and real only on the GPM path (Hopper /
Blackwell, monitor 3.1); A100 schemas carry no ``integer_active``. Both
metrics are job means of the per-sample value, a measured 0 is a value, and
a sentinel or absent key is a no-data reason, never 0.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from hpcperfstats.analysis.metrics.lib import metrics
from hpcperfstats.analysis.metrics.lib.columnar.testing import (
  table_from_records,
  to_time_us,
)
from hpcperfstats.analysis.metrics.lib.job_metric_display_labels import (
  JOB_METRIC_SHORT_LABELS,
)
from hpcperfstats.analysis.metrics.lib.sentinel_reasons import SentinelRun
from hpcperfstats.dbload.lib.monitor_naming import canonical

_T0 = datetime(2026, 11, 18, 10, 0, tzinfo=UTC)
_HOSTS = ["g1.x", "g2.x"]
_NEW = ("avg_integer_active", "avg_tensor_dmma_active")


def _gpu_rows(event, values):
  """One nvidia_gpu row per minute per host for ``event`` (dev 0)."""
  rows = []
  for host in _HOSTS:
    for i, v in enumerate(values):
      t = _T0 + timedelta(minutes=i)
      rows.append((t, host, "nvidia_gpu", event, "0", v, None, None))
  return rows


def _cpu_rows(n=4):
  """host_cpu rows so the job has a window and a schema on every host."""
  return [
    (
      _T0 + timedelta(minutes=i),
      h,
      "host_cpu",
      "user",
      "-",
      10.0 * i,
      1.0,
      None,
    )
    for h in _HOSTS
    for i in range(n)
  ]


def _compute(monkeypatch, table):
  """Run ``compute_metrics`` for the two new metrics only."""
  monkeypatch.setattr(metrics, "open_job_table", lambda job, **_kw: table)
  monkeypatch.setattr(
    metrics, "gpu_summary_tuple", lambda jt: (None, None, None, None)
  )
  monkeypatch.setattr(
    metrics, "compute_job_detail_fsio_metric_rows", lambda jt: []
  )
  monkeypatch.setattr(
    metrics, "_in_window_telemetry_bounds_for_job", lambda job: (None, None)
  )
  m = metrics.Metrics()
  m.simple_metrics_list = {
    k: v for k, v in m.simple_metrics_list.items() if k in _NEW
  }
  m.complex_metrics_list = []
  out = m.compute_metrics(SimpleNamespace(jid="gpu1"))
  return {r["metric"]: r for r in out["rows"]}


def test_new_metrics_are_registered_with_canonical_events():
  """Both metrics read their own nvidia_gpu key, in %, like HMMA."""
  m = metrics.Metrics()
  assert canonical.NVIDIA_GPU_INTEGER_ACTIVE == "integer_active"
  assert canonical.NVIDIA_GPU_TENSOR_DMMA_ACTIVE == "tensor_dmma_active"
  for name, event in (
    ("avg_integer_active", "integer_active"),
    ("avg_tensor_dmma_active", "tensor_dmma_active"),
  ):
    spec = m.simple_metrics_list[name]
    assert spec["typename"] == "nvidia_gpu"
    assert spec["events"] == [event]
    assert spec["units"] == "%"
  names = [e["metric"] for e in metrics.job_metrics_catalog_entries()]
  assert set(_NEW) <= set(names)
  assert "avg_tensor_dfma_active" in names


def test_short_labels_for_new_and_relabelled_metrics():
  """Job Detail table labels; DFMA is named for what it measures."""
  assert JOB_METRIC_SHORT_LABELS["avg_integer_active"] == (
    "Average GPU integer pipeline activity"
  )
  assert JOB_METRIC_SHORT_LABELS["avg_tensor_dmma_active"] == (
    "Average GPU tensor DMMA (FP64) activity"
  )
  assert JOB_METRIC_SHORT_LABELS["avg_tensor_dfma_active"] == (
    "FP64 FMA pipe (same as FP64 on Hopper/Blackwell)"
  )
  assert JOB_METRIC_SHORT_LABELS["avg_tensor_hmma_active"] == (
    "Average GPU tensor HMMA (FP16/BF16/TF32/FP8) activity"
  )


def test_job_mean_of_integer_and_dmma(monkeypatch):
  """Hopper-like job: integer pipe busy under FP work, DMMA real."""
  table = table_from_records(
    _gpu_rows("integer_active", [40.0, 50.0, 60.0, 50.0])
    + _gpu_rows("tensor_dmma_active", [10.0, 30.0, 30.0, 10.0])
  )
  out = _compute(monkeypatch, table)
  assert out["avg_integer_active"]["value"] == pytest.approx(50.0)
  assert out["avg_tensor_dmma_active"]["value"] == pytest.approx(20.0)
  assert out["avg_integer_active"]["type"] == "nvidia_gpu"
  assert out["avg_integer_active"]["no_data_reason"] is None


def test_measured_zero_is_a_value_not_missing(monkeypatch):
  """Blackwell integer pipe near 0 under FP loads stays a stored 0."""
  table = table_from_records(
    _gpu_rows("integer_active", [0.0, 0.0, 0.0])
    + _gpu_rows("tensor_dmma_active", [0.0, 0.0, 0.0])
  )
  out = _compute(monkeypatch, table)
  assert out["avg_integer_active"]["value"] == 0.0
  assert out["avg_tensor_dmma_active"]["value"] == 0.0


def test_dcgm_path_dmma_not_present_is_a_reason_not_zero(monkeypatch):
  """A100 / 3.0 DCGM path: tensor_dmma_active is NOT_PRESENT (-2) on
  every host, so the metric has no value and says why."""
  runs = [
    SentinelRun(
      h,
      "nvidia_gpu",
      "0",
      "tensor_dmma_active",
      -2,
      to_time_us(_T0),
      to_time_us(_T0 + timedelta(minutes=3)),
      1,
      1,
    )
    for h in _HOSTS
  ]
  table = table_from_records(
    _cpu_rows(),
    hosts=_HOSTS,
    schema={"host_cpu": ["user"], "nvidia_gpu": ["tensor_dmma_active"]},
    sentinel_runs=runs,
  )
  row = _compute(monkeypatch, table)["avg_tensor_dmma_active"]
  assert row["value"] is None
  assert row["no_data_reason"] == "nvidia_gpu: counter not present (2/2 hosts)"


def test_a100_schema_without_integer_key_has_no_value(monkeypatch):
  """A100: nvidia_gpu present without integer_active; no value, a reason."""
  table = table_from_records(
    _cpu_rows() + _gpu_rows("tensor_hmma_active", [20.0, 20.0, 20.0, 20.0]),
    hosts=_HOSTS,
  )
  row = _compute(monkeypatch, table)["avg_integer_active"]
  assert row["value"] is None
  assert row["no_data_reason"]


def test_metrics_contract_bumped_for_gpu_pipe_metrics():
  """Contract 14 added avg_integer_active / avg_tensor_dmma_active."""
  assert metrics.METRICS_CONTRACT_VERSION >= 14
