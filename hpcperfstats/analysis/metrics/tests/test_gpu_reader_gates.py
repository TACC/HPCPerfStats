"""Every GPU reader drops DCGM blanks and devices outside the GPU affinity.

The GPU similar-bug sweep found readers that skipped the gate the Job
Detail GPU stats already applied: the metrics pivot (device mean behind
``avg_gpuutil``, ``max_gpu_power`` and the GPU node imbalances) averaged a
DCGM blank (2**47) with valid devices, the GPU percent gauges read blanks
and co-tenant devices, the Summary GPU sums ignored the per-host
``gpu_list``, legacy empty-``dev`` rows counted as an extra GPU, and the
Summary GPU util plot top was not a per-host count.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import numpy as np
import pytest

from hpcperfstats.analysis.metrics.lib import metrics
from hpcperfstats.analysis.metrics.lib.columnar import summary_series as ss
from hpcperfstats.analysis.metrics.lib.columnar.detail_series import (
  gpu_agg_rows,
)
from hpcperfstats.analysis.metrics.lib.columnar.testing import (
  table_from_records,
)
from hpcperfstats.analysis.metrics.lib.gen.utils import utils
from hpcperfstats.analysis.metrics.lib.gpu_job_detail_summary import (
  reduce_gpu_agg_to_util_stats,
)
from hpcperfstats.lib.dcgm_blank import DCGM_FP64_BLANK

_T0 = datetime(2026, 11, 22, 10, 0, tzinfo=UTC)
_TIMES = [_T0 + timedelta(minutes=i) for i in range(8)]
_PRECEDENCE = ("nvidia_gpu", "amd_gpu", "intel_gpu")


def _rows(host, event, by_dev):
  """One nvidia_gpu row per minute per device: ``{dev: value}``."""
  return [
    (t, host, "nvidia_gpu", event, dev, v, None, None)
    for t in _TIMES
    for dev, v in by_dev.items()
  ]


def _u(table):
  """``gen.utils`` over the table's columnar metrics view."""
  return utils(table.metrics_view())


def _blank_table():
  """Host a: devices 0 (blank), 1, 2; host b: one device at 50."""
  recs = []
  for event, vals in (
    ("gpu_util", (DCGM_FP64_BLANK, 50.0, 50.0)),
    ("tensor_active", (DCGM_FP64_BLANK, 50.0, 50.0)),
    ("power_usage", (DCGM_FP64_BLANK, 300.0, 100.0)),
  ):
    recs += _rows("a", event, dict(zip("012", vals, strict=True)))
    recs += _rows("b", event, {"0": 50.0 if event != "power_usage" else 80.0})
  return table_from_records(recs)


def test_pivot_device_mean_drops_dcgm_blank():
  """[blank, 50, 50] is a mean of 50, not 4.69e13."""
  piv = _blank_table().index.metrics_pivot("nvidia_gpu", ["gpu_util"])
  assert set(piv["value"].ravel().tolist()) == {50.0}


def test_pivot_gpu_metrics_ignore_dcgm_blank():
  """avg_gpuutil and the GPU node imbalances read the valid devices."""
  u = _u(_blank_table())
  assert metrics.avg_gpuutil().compute_metric(u)[0] == 50.0
  assert metrics.gpu_util_node_imbalance().compute_metric(u)[0] == 0.0
  assert metrics.tensor_node_imbalance().compute_metric(u)[0] == 0.0


def test_max_gpu_power_is_the_peak_of_one_device():
  """Catalog: maximum sampled power across all GPUs, not a device mean."""
  assert metrics.max_gpu_power().compute_metric(_u(_blank_table())) == (
    300.0,
    "nvidia_gpu",
    "W",
  )


def test_max_gpu_power_honours_gpu_affinity():
  """A co-tenant's GPU on a shared host is not this job's peak."""
  recs = _rows("a", "power_usage", {"0": 200.0, "1": 400.0})
  table = table_from_records(recs, shared=True, gpu_affinity_devs={"a": ["0"]})
  assert metrics.max_gpu_power().compute_metric(_u(table))[0] == 200.0


def _gauge_rows(monkeypatch, table, names):
  """``compute_metrics`` restricted to the simple metrics ``names``."""
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
    k: v for k, v in m.simple_metrics_list.items() if k in names
  }
  m.complex_metrics_list = []
  out = m.compute_metrics(SimpleNamespace(jid="gpu1"))
  return {r["metric"]: r["value"] for r in out["rows"]}


def test_gpu_percent_gauges_reject_blank_and_apply_affinity(monkeypatch):
  """avg_tensor_active / avg_fp64_active: no blank, no co-tenant GPU."""
  recs = _rows("a", "tensor_active", {"0": 40.0, "1": DCGM_FP64_BLANK})
  recs += _rows("a", "fp64_active", {"0": 20.0, "1": 80.0})
  recs += _rows("b", "tensor_active", {"0": 40.0})
  recs += _rows("b", "fp64_active", {"0": 20.0})
  table = table_from_records(
    recs, shared=True, gpu_affinity_devs={"a": ["0", "1"], "b": ["0"]}
  )
  got = _gauge_rows(
    monkeypatch, table, {"avg_tensor_active", "avg_fp64_active"}
  )
  assert got["avg_tensor_active"] == pytest.approx(40.0)
  scoped = table_from_records(
    recs, shared=True, gpu_affinity_devs={"a": ["0"], "b": ["0"]}
  )
  got = _gauge_rows(monkeypatch, scoped, {"avg_fp64_active"})
  assert got["avg_fp64_active"] == pytest.approx(20.0)


def test_legacy_empty_dev_row_is_no_extra_gpu():
  """'' rows beside per-device rows on one host are not a third GPU."""
  recs = _rows("a", "gpu_util", {"0": 40.0, "1": 60.0, "": 50.0})
  recs += _rows("b", "gpu_util", {"": 70.0})
  table = table_from_records(recs)
  assert table.gpu_count_total(_PRECEDENCE) == 3
  active, vmax, _mean = reduce_gpu_agg_to_util_stats(gpu_agg_rows(table))
  assert (active, vmax) == (3, 170.0)


def test_reduce_util_stats_drops_empty_dev_beside_hardware_devs():
  """The dict-level reducer applies the same rule to any row source."""
  rows = [
    {"host": "a", "dev": d, "event": "gpu_util", "cnt": 5, "vmax": v}
    for d, v in (("0", 40.0), ("", 50.0))
  ]
  rows.append(
    {"host": "b", "dev": "", "event": "gpu_util", "cnt": 5, "vmax": 9.0}
  )
  assert reduce_gpu_agg_to_util_stats(rows)[:2] == (2, 49.0)


def test_summed_gpu_aggregate_honours_per_host_gpu_affinity():
  """Summary util and GPU power sums keep only the affinity devices.

  The Summary util line (``get_aggregate`` without ``group_by_dev``) summed
  every owned device on a shared host while its scale and Job Detail
  counted the affinity list only (170 % against a 100 % top).
  """
  recs = _rows("h", "gpu_util", {"0": 80.0, "1": 90.0})
  recs += _rows("h", "power_usage", {"0": 300.0, "1": 200.0})
  table = table_from_records(recs, shared=True, gpu_affinity_devs={"h": ["0"]})
  util = table.get_aggregate("nvidia_gpu", "value", ["gpu_util"])
  power = table.get_aggregate("nvidia_gpu", "value", ["power_usage"])
  assert set(util.sum_val.tolist()) == {80.0}
  assert set(power.sum_val.tolist()) == {300.0}
  by_dev = table.get_aggregate(
    "nvidia_gpu", "value", ["gpu_util"], group_by_dev=True
  )
  assert set(by_dev.sum_val.tolist()) == {80.0}


def _util_top(table):
  """``y_top`` of the Summary ``nv_gpu_util`` render spec."""
  built = ss.build_summary_series(table)
  (top,) = [y for n, _l, y in built.render_specs if n == "nv_gpu_util"]
  return top, float(np.nanmax(built.frame.columns["nv_gpu_util"]))


def test_gpu_util_top_is_the_largest_host_count_exclusive():
  """A host without a gpu_count sample is not clipped by another's count."""
  recs = _rows("a", "gpu_util", {"0": 90.0, "1": 90.0})
  recs += _rows("a", "gpu_count", {"": 2.0})
  recs += _rows("b", "gpu_util", dict.fromkeys("0123", 90.0))
  top, line_max = _util_top(table_from_records(recs))
  assert top == 400.0
  assert line_max <= top


def test_gpu_util_top_is_per_host_on_shared_jobs():
  """Two hosts with one granted GPU each: top 100, not the 200 total."""
  recs = _rows("a", "gpu_util", {"0": 90.0})
  recs += _rows("b", "gpu_util", {"0": 90.0})
  table = table_from_records(
    recs, shared=True, gpu_affinity_devs={"a": ["0"], "b": ["0"]}
  )
  assert _util_top(table)[0] == 100.0


def test_gpu_util_top_without_any_count_is_auto_range():
  """A shared job with no count leaves the axis automatic, never 0."""
  recs = _rows("a", "gpu_util", {"0": 90.0})
  table = table_from_records(recs, shared=True, gpu_affinity_devs={"a": []})
  assert table.gpu_count_by_host(_PRECEDENCE) == {"a": 0}
  frame = ss.SeriesFrame.from_base(np.array([0]), np.array([1]), ["a"])
  frame.set("nv_gpu_util", np.array([10.0]))
  assert ss._nv_gpu_util_y_top(frame, {"a": 0}) is None
