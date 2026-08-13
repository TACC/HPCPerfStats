"""Tests for safe per-interval rates used by metrics (duplicate sample times)."""

import os
import warnings

import numpy as np

os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")

from hpcperfstats.dbload.lib.django_bootstrap import ensure_django

ensure_django()

from hpcperfstats.analysis.metrics.lib.gen.utils import utils as job_utils
from hpcperfstats.analysis.metrics.lib import metrics


def test_per_interval_rate_nan_when_zero_dt():
  t = np.array([0.0, 0.0, 1.0, 2.0], dtype=np.float64)
  y = np.array([0.0, 0.0, 10.0, 30.0], dtype=np.float64)
  with warnings.catch_warnings():
    warnings.simplefilter("error", category=RuntimeWarning)
    r = metrics._per_interval_rate(y, t)
  assert np.isnan(r[0])
  np.testing.assert_allclose(r[1:], [10.0, 20.0])


def test_node_imbalance_and_time_imbalance_no_runtime_warning():
  """Duplicate timestamps on the job time axis must not emit divide-by-zero warnings."""
  schema = metrics._Schema(["user", "system"])
  # Six samples; first two share the same time -> diff(t)[0] == 0.
  # time_imbalance only enters its inner loop when len(t) >= 6.
  cpu = np.array(
      [
          [0.0, 0.0],
          [1.0, 0.0],
          [4.0, 0.0],
          [9.0, 0.0],
          [16.0, 0.0],
          [25.0, 0.0],
      ],
      dtype=np.float64,
  )

  class _Host:
    def __init__(self):
      self.stats = {"cpu": {"agg": cpu}}

  class _Job:
    def __init__(self):
      self.hosts = {"n1": _Host()}
      self.schemas = {"cpu": schema}
      self.times = np.array([0.0, 0.0, 1.0, 2.0, 3.0, 4.0], dtype=np.float64)
      self.acct = {"cores": 1, "nodes": 1}

  u = job_utils(_Job())
  with warnings.catch_warnings():
    warnings.simplefilter("error", category=RuntimeWarning)
    metrics.node_imbalance().compute_metric(u)
    metrics.time_imbalance().compute_metric(u)


def test_time_imbalance_duplicate_tail_timestamps_no_runtime_warning():
  """Duplicate timestamps at tail must not trigger invalid divide warnings."""
  schema = metrics._Schema(["user", "system"])
  cpu = np.array(
      [
          [0.0, 0.0],
          [1.0, 0.0],
          [3.0, 0.0],
          [6.0, 0.0],
          [10.0, 0.0],
          [15.0, 0.0],
      ],
      dtype=np.float64,
  )

  class _Host:
    def __init__(self):
      self.stats = {"cpu": {"agg": cpu}}

  class _Job:
    def __init__(self):
      self.hosts = {"n1": _Host()}
      self.schemas = {"cpu": schema}
      # Duplicate final timestamp forces zero "after" window for i=3.
      self.times = np.array([0.0, 1.0, 2.0, 3.0, 4.0, 4.0], dtype=np.float64)
      self.acct = {"cores": 1, "nodes": 1}

  u = job_utils(_Job())
  with warnings.catch_warnings():
    warnings.simplefilter("error", category=RuntimeWarning)
    value, typename, units = metrics.time_imbalance().compute_metric(u)
  assert typename == "cpu"
  assert units == "%"
  assert value is None or np.isfinite(value)


def test_schema_iteration_yields_event_names_not_indices():
  """``for name in schema`` must yield event strings (Python 3 otherwise uses __getitem__(0), …)."""
  schema = metrics._Schema(["SSE_D_ALL", "FP_ARITH_INST_RETIRED_SCALAR_DOUBLE"])
  assert list(schema) == ["SSE_D_ALL", "FP_ARITH_INST_RETIRED_SCALAR_DOUBLE"]
  assert schema["SSE_D_ALL"].index == 0


def test_flops_node_imbalance_detects_slower_host():
  """FLOPs imbalance uses the same relative shortfall vs peak host as CPU imbalance."""
  schema = metrics._Schema(["FLOPS"])
  fast = np.array([[0.0], [10.0], [40.0]], dtype=np.float64)
  slow = np.array([[0.0], [5.0], [20.0]], dtype=np.float64)

  class _Host:
    def __init__(self, arr):
      self.stats = {"amd64_pmc": {"agg": arr}}

  class _Job:
    def __init__(self):
      self.hosts = {"a": _Host(fast), "b": _Host(slow)}
      self.schemas = {"amd64_pmc": schema}
      self.times = np.array([0.0, 1.0, 2.0], dtype=np.float64)
      self.acct = {"cores": 1, "nodes": 2}

  u = job_utils(_Job())
  val, typename, units = metrics.flops_node_imbalance().compute_metric(u)
  assert typename == "amd64_pmc"
  assert units == "%"
  assert val is not None
  assert val > 40.0


def test_fabric_node_imbalance_ib_ext_two_hosts():
  schema = metrics._Schema(["port_xmit_data", "port_rcv_data"])
  hi = np.array([[0.0, 0.0], [20.0, 20.0], [60.0, 60.0]], dtype=np.float64)
  lo = np.array([[0.0, 0.0], [10.0, 10.0], [30.0, 30.0]], dtype=np.float64)

  class _Host:
    def __init__(self, arr):
      self.stats = {"ib_ext": {"agg": arr}}

  class _Job:
    def __init__(self):
      self.hosts = {"a": _Host(hi), "b": _Host(lo)}
      self.schemas = {"ib_ext": schema}
      self.times = np.array([0.0, 1.0, 2.0], dtype=np.float64)
      self.acct = {"cores": 1, "nodes": 2}

  u = job_utils(_Job())
  val, typename, units = metrics.fabric_node_imbalance().compute_metric(u)
  assert typename == "host_ib"
  assert units == "%"
  assert val is not None
  assert val > 40.0


def test_max_numa_remote_rate_per_host_fallback():
  schema = metrics._Schema(["numa_miss"])
  a = np.array([[0.0], [100.0], [400.0]], dtype=np.float64)

  class _Host:
    def __init__(self):
      self.stats = {"host_numa": {"agg": a}}

  class _Job:
    def __init__(self):
      self.hosts = {"n1": _Host()}
      self.schemas = {"host_numa": schema}
      self.times = np.array([0.0, 1.0, 2.0], dtype=np.float64)
      self.acct = {"cores": 1, "nodes": 1}

  u = job_utils(_Job())
  val, typename, units = metrics.max_numa_remote_rate().compute_metric(u)
  assert typename == "host_numa"
  assert units == "#/s"
  assert val is not None
  assert val >= 200.0


def test_max_opa_congestion_rate_per_host_fallback():
  schema = metrics._Schema(["PortXmitWait"])
  a = np.array([[0.0], [50.0], [200.0]], dtype=np.float64)

  class _Host:
    def __init__(self):
      self.stats = {"host_opa": {"agg": a}}

  class _Job:
    def __init__(self):
      self.hosts = {"n1": _Host()}
      self.schemas = {"host_opa": schema}
      self.times = np.array([0.0, 1.0, 2.0], dtype=np.float64)
      self.acct = {"cores": 1, "nodes": 1}

  u = job_utils(_Job())
  val, typename, units = metrics.max_opa_congestion_rate().compute_metric(u)
  assert typename == "host_opa"
  assert units == "#/s"
  assert val is not None
  assert val >= 100.0


def test_dram_bw_node_imbalance_amd_df():
  schema = metrics._Schema(["MBW_CHANNEL_0"])
  hi = np.array([[0.0], [20.0], [80.0]], dtype=np.float64)
  lo = np.array([[0.0], [10.0], [40.0]], dtype=np.float64)

  class _Host:
    def __init__(self, arr):
      self.stats = {"amd_x86_uncore_df": {"agg": arr}}

  class _Job:
    def __init__(self):
      self.hosts = {"a": _Host(hi), "b": _Host(lo)}
      self.schemas = {"amd_x86_uncore_df": schema}
      self.times = np.array([0.0, 1.0, 2.0], dtype=np.float64)
      self.acct = {"cores": 1, "nodes": 2}

  u = job_utils(_Job())
  val, typename, units = metrics.dram_bw_node_imbalance().compute_metric(u)
  assert typename == "amd_x86_uncore_df"
  assert units == "%"
  assert val is not None
  assert val > 40.0


def test_dram_bw_node_imbalance_amd_family_df_dram_chan():
  schema = metrics._Schema(["dram_chan0_bytes", "dram_chan1_bytes"])
  hi = np.array([[0.0, 0.0], [20.0, 20.0], [80.0, 80.0]], dtype=np.float64)
  lo = np.array([[0.0, 0.0], [10.0, 10.0], [40.0, 40.0]], dtype=np.float64)

  class _Host:
    def __init__(self, arr):
      self.stats = {"amd_x86_uncore_df_milan": {"agg": arr}}

  class _Job:
    def __init__(self):
      self.hosts = {"a": _Host(hi), "b": _Host(lo)}
      self.schemas = {"amd_x86_uncore_df_milan": schema}
      self.times = np.array([0.0, 1.0, 2.0], dtype=np.float64)
      self.acct = {"cores": 1, "nodes": 2}

  u = job_utils(_Job())
  val, typename, units = metrics.dram_bw_node_imbalance().compute_metric(u)
  assert typename == "amd_x86_uncore_df_milan"
  assert units == "%"
  assert val is not None
  assert val > 40.0


def test_lnet_node_imbalance_two_hosts():
  schema = metrics._Schema(["tx_bytes", "rx_bytes"])
  hi = np.array([[0.0, 0.0], [20.0, 20.0], [60.0, 60.0]], dtype=np.float64)
  lo = np.array([[0.0, 0.0], [10.0, 10.0], [30.0, 30.0]], dtype=np.float64)

  class _Host:
    def __init__(self, arr):
      self.stats = {"lnet": {"agg": arr}}

  class _Job:
    def __init__(self):
      self.hosts = {"a": _Host(hi), "b": _Host(lo)}
      self.schemas = {"lnet": schema}
      self.times = np.array([0.0, 1.0, 2.0], dtype=np.float64)
      self.acct = {"cores": 1, "nodes": 2}

  u = job_utils(_Job())
  val, typename, units = metrics.lnet_node_imbalance().compute_metric(u)
  assert typename == "lnet"
  assert units == "%"
  assert val is not None
  assert val > 40.0


def test_gpu_util_node_imbalance_nvidia_snapshots():
  schema = metrics._Schema(["gpu_util"])
  a = np.array([[80.0], [80.0], [80.0]], dtype=np.float64)
  b = np.array([[40.0], [40.0], [40.0]], dtype=np.float64)

  class _Host:
    def __init__(self, arr):
      self.stats = {"nvidia_gpu": {"agg": arr}}

  class _Job:
    def __init__(self):
      self.hosts = {"a": _Host(a), "b": _Host(b)}
      self.schemas = {"nvidia_gpu": schema}
      self.times = np.array([0.0, 1.0, 2.0], dtype=np.float64)
      self.acct = {"cores": 1, "nodes": 2}

  u = job_utils(_Job())
  val, typename, units = metrics.gpu_util_node_imbalance().compute_metric(u)
  assert typename == "nvidia_gpu"
  assert units == "%"
  assert val is not None
  assert 45.0 < val < 55.0


def test_tensor_node_imbalance_nvidia():
  schema = metrics._Schema(["tensor_active"])
  a = np.array([[100.0], [100.0]], dtype=np.float64)
  b = np.array([[50.0], [50.0]], dtype=np.float64)

  class _Host:
    def __init__(self, arr):
      self.stats = {"nvidia_gpu": {"agg": arr}}

  class _Job:
    def __init__(self):
      self.hosts = {"a": _Host(a), "b": _Host(b)}
      self.schemas = {"nvidia_gpu": schema}
      self.times = np.array([0.0, 1.0], dtype=np.float64)
      self.acct = {"cores": 1, "nodes": 2}

  u = job_utils(_Job())
  val, typename, units = metrics.tensor_node_imbalance().compute_metric(u)
  assert typename == "nvidia_gpu"
  assert units == "%"
  assert val is not None
  assert 45.0 < val < 55.0


def test_max_gpu_power_nvidia():
  schema = metrics._Schema(["power_usage"])
  a = np.array([[200.0], [350.0]], dtype=np.float64)

  class _Host:
    def __init__(self):
      self.stats = {"nvidia_gpu": {"agg": a}}

  class _Job:
    def __init__(self):
      self.hosts = {"n1": _Host()}
      self.schemas = {"nvidia_gpu": schema}
      self.times = np.array([0.0, 1.0], dtype=np.float64)
      self.acct = {"cores": 1, "nodes": 1}

  u = job_utils(_Job())
  val, typename, units = metrics.max_gpu_power().compute_metric(u)
  assert units == "W"
  assert val == 350.0
  assert typename == "nvidia_gpu"
