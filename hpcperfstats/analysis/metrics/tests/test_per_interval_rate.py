"""Tests for safe per-interval rates used by metrics (duplicate sample times)."""

import os
import warnings

import numpy as np
import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")

from hpcperfstats.dbload.lib.django_bootstrap import ensure_django

ensure_django()

from hpcperfstats.analysis.metrics.lib import metrics
from hpcperfstats.analysis.metrics.lib.columnar.testing import host_series
from hpcperfstats.analysis.metrics.lib.gen.utils import utils as job_utils
from hpcperfstats.analysis.metrics.tests import per_host_axis_fixtures as F


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
      self.stats = {
        "cpu": {
          "agg": host_series(
            cpu, np.array([0.0, 0.0, 1.0, 2.0, 3.0, 4.0], dtype=np.float64)
          )
        }
      }

  class _Job:
    def __init__(self):
      self.hosts = {"n1": _Host()}
      self.schemas = {"cpu": schema}
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
      self.stats = {
        "cpu": {
          # Duplicate final timestamp forces zero "after" window for i=3.
          "agg": host_series(
            cpu, np.array([0.0, 1.0, 2.0, 3.0, 4.0, 4.0], dtype=np.float64)
          )
        }
      }

  class _Job:
    def __init__(self):
      self.hosts = {"n1": _Host()}
      self.schemas = {"cpu": schema}
      self.acct = {"cores": 1, "nodes": 1}

  u = job_utils(_Job())
  with warnings.catch_warnings():
    warnings.simplefilter("error", category=RuntimeWarning)
    value, typename, units = metrics.time_imbalance().compute_metric(u)
  assert typename == "cpu"
  assert units == "%"
  assert value is None or np.isfinite(value)


def test_schema_iteration_yields_event_names_not_indices():
  """``for name in schema`` must yield event strings (Python 3 otherwise uses __getitem__(0), ...)."""
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
      self.stats = {
        "amd64_pmc": {
          "agg": host_series(arr, np.array([0.0, 1.0, 2.0], dtype=np.float64))
        }
      }

  class _Job:
    def __init__(self):
      self.hosts = {"a": _Host(fast), "b": _Host(slow)}
      self.schemas = {"amd64_pmc": schema}
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
      self.stats = {
        "ib_ext": {
          "agg": host_series(arr, np.array([0.0, 1.0, 2.0], dtype=np.float64))
        }
      }

  class _Job:
    def __init__(self):
      self.hosts = {"a": _Host(hi), "b": _Host(lo)}
      self.schemas = {"ib_ext": schema}
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
      self.stats = {
        "host_numa": {
          "agg": host_series(a, np.array([0.0, 1.0, 2.0], dtype=np.float64))
        }
      }

  class _Job:
    def __init__(self):
      self.hosts = {"n1": _Host()}
      self.schemas = {"host_numa": schema}
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
      self.stats = {
        "host_opa": {
          "agg": host_series(a, np.array([0.0, 1.0, 2.0], dtype=np.float64))
        }
      }

  class _Job:
    def __init__(self):
      self.hosts = {"n1": _Host()}
      self.schemas = {"host_opa": schema}
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
      self.stats = {
        "amd_x86_uncore_df": {
          "agg": host_series(arr, np.array([0.0, 1.0, 2.0], dtype=np.float64))
        }
      }

  class _Job:
    def __init__(self):
      self.hosts = {"a": _Host(hi), "b": _Host(lo)}
      self.schemas = {"amd_x86_uncore_df": schema}
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
      self.stats = {
        "amd_x86_uncore_df_milan": {
          "agg": host_series(arr, np.array([0.0, 1.0, 2.0], dtype=np.float64))
        }
      }

  class _Job:
    def __init__(self):
      self.hosts = {"a": _Host(hi), "b": _Host(lo)}
      self.schemas = {"amd_x86_uncore_df_milan": schema}
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
      self.stats = {
        "lnet": {
          "agg": host_series(arr, np.array([0.0, 1.0, 2.0], dtype=np.float64))
        }
      }

  class _Job:
    def __init__(self):
      self.hosts = {"a": _Host(hi), "b": _Host(lo)}
      self.schemas = {"lnet": schema}
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
      self.stats = {
        "nvidia_gpu": {
          "agg": host_series(arr, np.array([0.0, 1.0, 2.0], dtype=np.float64))
        }
      }

  class _Job:
    def __init__(self):
      self.hosts = {"a": _Host(a), "b": _Host(b)}
      self.schemas = {"nvidia_gpu": schema}
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
      self.stats = {
        "nvidia_gpu": {
          "agg": host_series(arr, np.array([0.0, 1.0], dtype=np.float64))
        }
      }

  class _Job:
    def __init__(self):
      self.hosts = {"a": _Host(a), "b": _Host(b)}
      self.schemas = {"nvidia_gpu": schema}
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
      self.stats = {
        "nvidia_gpu": {
          "agg": host_series(a, np.array([0.0, 1.0], dtype=np.float64))
        }
      }

  class _Job:
    def __init__(self):
      self.hosts = {"n1": _Host()}
      self.schemas = {"nvidia_gpu": schema}
      self.acct = {"cores": 1, "nodes": 1}

  u = job_utils(_Job())
  val, typename, units = metrics.max_gpu_power().compute_metric(u)
  assert units == "W"
  assert val == 350.0
  assert typename == "nvidia_gpu"


# Node imbalance family resolution on real columnar tables: rates are the
# ingest arc, compared against the independent arc oracle.

_T0_S = 1_790_000_040
_RATE_PATTERN = (1.0, 1.3, 0.8, 1.1, 0.9, 1.2, 1.0)
_NOT_PRESENT = -2.0


def _counter_table(spec):
  """Table of counter rows from ``{(type, event): {host: base rate}}``.

  Every host samples every 60 s on the same times. A base rate of None
  stores the monitor NOT_PRESENT sentinel (a static absent counter, as
  ``host_cpu_hw`` carries another CPU family's keys); a number stores a
  cumulative value whose arc varies around it with ``_RATE_PATTERN``.
  """
  from hpcperfstats.analysis.metrics.lib.columnar.testing import (
    table_from_records,
  )

  rows = []
  for (typ, ev), per_host in spec.items():
    for host, base in per_host.items():
      value = 1.0e6
      for k in range(len(_RATE_PATTERN) + 1):
        t_us = (_T0_S + 60 * k) * 1_000_000
        if base is None:
          rows.append((t_us, host, typ, ev, "", _NOT_PRESENT, None, None))
          continue
        rate = base * _RATE_PATTERN[k - 1] if k else None
        value += 60.0 * rate if k else 0.0
        rows.append(
          (t_us, host, typ, ev, "", value, rate, 60.0 * rate if k else None)
        )
  return table_from_records(rows)


def _imbalance(metric_cls, table):
  return metric_cls().compute_metric(job_utils(table.metrics_view()))


_PMU_ON = 2.0e9
_INTEL_FP = (
  ("fp_arith_inst_retired_scalar_double", 1),
  ("fp_arith_inst_retired_256b_packed_double", 4),
)


def _intel_host_cpu_hw(per_host_fp, per_host_pmu, amd=0.0, arm=None):
  """``host_cpu_hw`` spec: Intel FP lanes per host (scaled per width), the
  PMU enabled-time rate per host, and the other families' keys at ``amd`` /
  ``arm`` on every host (0 = older monitors' hard zeros, None = absent)."""
  hosts = list(per_host_fp)
  spec = {
    ("host_cpu_hw", ev): {h: per_host_fp[h] / w for h in hosts}
    for ev, w in _INTEL_FP
  }
  spec[("host_cpu_hw", "pmu_enabled_time")] = dict(per_host_pmu)
  spec[("host_cpu_hw", "fp_ops_retired")] = dict.fromkeys(hosts, amd)
  spec[("host_cpu_hw", "arm_est_flops")] = dict.fromkeys(hosts, arm)
  return spec


def test_pmu_gate_per_host():
  """A host with the PMU off is left out of the FLOP comparison, so the
  imbalance is the two measured hosts', not a false ~100%."""
  fp = {"c001": 4.0e9, "c002": 2.5e9}
  table = _counter_table(
    _intel_host_cpu_hw(
      fp | {"c003": 0.0}, {"c001": _PMU_ON, "c002": _PMU_ON, "c003": 0.0}
    )
  )
  measured = _counter_table(_intel_host_cpu_hw(fp, dict.fromkeys(fp, _PMU_ON)))
  u = job_utils(table.metrics_view())
  typename, weighted, hosts = metrics._flops_weighted_events_for_imbalance(u)
  assert typename == "host_cpu_hw"
  assert hosts == {"c001", "c002"}
  assert sorted(weighted) == sorted((ev, float(w)) for ev, w in _INTEL_FP)
  val, typename, units = _imbalance(metrics.flops_node_imbalance, table)
  expected = F.rate_imbalance_oracle(measured, "host_cpu_hw", weighted)
  with_off_host = F.rate_imbalance_oracle(table, "host_cpu_hw", weighted)
  assert (typename, units) == ("host_cpu_hw", "%")
  assert val == pytest.approx(expected, rel=1e-9)
  assert 30.0 < val < 50.0 < with_off_host


def test_pmu_gate_reads_each_host_once():
  """Regression: the per-host PMU gate probed the family once per host, and
  each probe read every host's series, so the gate was quadratic in hosts
  (15.8 s for 1000 hosts). It resolves once and reads each host once."""
  from unittest import mock

  def series_reads(n_hosts):
    hosts = [f"c{i:03d}" for i in range(n_hosts)]
    table = _counter_table(
      _intel_host_cpu_hw(
        {h: 1.0e9 * (1 + i) for i, h in enumerate(hosts)},
        dict.fromkeys(hosts, _PMU_ON),
      )
    )
    u = job_utils(table.metrics_view())
    with mock.patch(
      "hpcperfstats.analysis.metrics.lib.gen.utils._read_only_series",
      side_effect=lambda s: s,
    ) as reads:
      typename, _we, gated = metrics._flops_weighted_events_for_imbalance(u)
    assert typename == "host_cpu_hw" and gated == set(hosts)
    return reads.call_count

  assert series_reads(16) == 2 * series_reads(8)


@pytest.mark.parametrize(
  ("spec", "weighted", "zero"),
  [
    pytest.param(
      {
        ("host_cpu_hw", "fp_ops_retired"): {"c001": 3.0e9, "c002": 1.0e9},
        ("host_cpu_hw", "arm_est_flops"): {"c001": None, "c002": None},
      },
      [("fp_ops_retired", 1.0)],
      False,
      id="amd-zen-fp_ops_retired",
    ),
    pytest.param(
      _intel_host_cpu_hw(
        {"c001": 4.0e9, "c002": 1.5e9}, {"c001": _PMU_ON, "c002": _PMU_ON}
      ),
      [(ev, float(w)) for ev, w in _INTEL_FP],
      False,
      id="intel-lanes-win-over-amd-zeros",
    ),
    pytest.param(
      {
        ("host_cpu_hw", "fp_ops_retired"): {"c001": None, "c002": None},
        ("host_cpu_hw", "fp_arith_inst_retired_scalar_double"): {
          "c001": 0.0,
          "c002": 0.0,
        },
        ("host_cpu_hw", "pmu_enabled_time"): {"c001": _PMU_ON, "c002": _PMU_ON},
        ("host_cpu_hw", "arm_est_flops"): {"c001": 2.0e9, "c002": 5.0e8},
      },
      [("arm_est_flops", 1.0)],
      False,
      id="grace-arm_est_flops",
    ),
    pytest.param(
      _intel_host_cpu_hw(
        {"c001": 0.0, "c002": 0.0}, {"c001": _PMU_ON, "c002": _PMU_ON}
      ),
      [("fp_ops_retired", 1.0)],
      True,
      id="idle-first-family-read-0",
    ),
    pytest.param(
      {("host_cpu_hw", "fp_ops_retired"): {"c001": 3.0e9}},
      [("fp_ops_retired", 1.0)],
      True,
      id="single-host",
    ),
  ],
)
def test_flops_node_imbalance_resolves_host_cpu_hw_families(
  spec, weighted, zero
):
  """host_cpu_hw resolves like avg_flops: AMD ``fp_ops_retired``, Intel
  lowercase lanes with their FLOP weights, Grace ``arm_est_flops``; a
  positive family beats another family's 0, and with no positive family the
  first that read 0 wins. One host compares with nothing: 0.0; every host
  reading 0 has no ratio: ``NoValue.ALL_HOSTS_ZERO``."""
  table = _counter_table(spec)
  u = job_utils(table.metrics_view())
  typename, got, _hosts = metrics._flops_weighted_events_for_imbalance(u)
  assert (typename, sorted(got)) == ("host_cpu_hw", sorted(weighted))
  assert metrics.Metrics()._job_arc_avg_flops(table)[1] == typename
  val, typename, units = _imbalance(metrics.flops_node_imbalance, table)
  assert (typename, units) == ("host_cpu_hw", "%")
  oracle = F.rate_imbalance_oracle(table, typename, weighted)
  if oracle == F.ALL_HOSTS_ZERO:
    assert zero
    assert val is metrics.NoValue.ALL_HOSTS_ZERO
  elif zero:
    assert val == 0.0 == oracle
  else:
    assert val == pytest.approx(oracle, rel=1e-9)
    assert val > 20.0


_SSE_LEGACY = metrics.INTEL_LEGACY_SSE_FLOP_EVENTS
_FP_ARITH_UPPER = metrics.INTEL_FP_ARITH_ALL_EVENTS


@pytest.mark.parametrize(
  ("typ", "events", "weighted"),
  [
    ("amd64_pmc", ["FLOPS"], [("FLOPS", 1.0)]),
    (
      "intel_x86_pmc_gpr8",
      list(_FP_ARITH_UPPER[:3]),
      [(ev, 1.0) for ev in _FP_ARITH_UPPER[:3]],
    ),
    (
      "intel_8pmc3",
      [ev for ev, _w in _SSE_LEGACY],
      [(ev, float(w)) for ev, w in _SSE_LEGACY],
    ),
    ("cpu_counter_metrics", ["ARM_EST_FLOPS"], [("ARM_EST_FLOPS", 1.0)]),
  ],
)
def test_flops_node_imbalance_legacy_pmc_unchanged_families(
  typ, events, weighted
):
  """Historical PMC schemas keep the event lists and weights the
  schema-only resolver gave them (FLOPS, uppercase FP_ARITH at 1, SSE
  proxies at their lanes, ARM_EST_FLOPS); only the rate source is the arc."""
  table = _counter_table(
    {
      (typ, ev): {"c001": 2.0e9 / (i + 1), "c002": 9.0e8 / (i + 1)}
      for i, ev in enumerate(events)
    }
  )
  u = job_utils(table.metrics_view())
  typename, got, hosts = metrics._flops_weighted_events_for_imbalance(u)
  assert (typename, got, hosts) == (typ, weighted, None)
  val, typename, _units = _imbalance(metrics.flops_node_imbalance, table)
  assert typename == typ
  assert val == pytest.approx(
    F.rate_imbalance_oracle(table, typ, weighted), rel=1e-9
  )
  assert val > 40.0


_DF_BEATS_CONV = metrics.amd_df_bw_event_conv_tries()[0][1]
_DF_MBW_CONV = metrics.amd_df_bw_event_conv_tries()[1][1]
_TWO_HOSTS = {"c001": 4.0e8, "c002": 1.0e8}


@pytest.mark.parametrize(
  ("spec", "typ", "weighted"),
  [
    pytest.param(
      {
        ("arm_aarch64_imc", "dram_cas_reads"): _TWO_HOSTS,
        ("arm_aarch64_imc", "dram_cas_writes"): _TWO_HOSTS,
        ("host_cpu_hw", "arm_dram_bw_bytes"): _TWO_HOSTS,
      },
      "arm_aarch64_imc",
      [
        ("dram_cas_reads", metrics.DDR_CAS_GIB),
        ("dram_cas_writes", metrics.DDR_CAS_GIB),
      ],
      id="arm-imc",
    ),
    pytest.param(
      {
        ("host_cpu_hw", "arm_dram_bw_bytes"): _TWO_HOSTS,
        ("host_cpu_hw", "dram_chan0_bytes"): {"c001": None, "c002": None},
      },
      "host_cpu_hw",
      [("arm_dram_bw_bytes", 1 / 1024**3)],
      id="host_cpu_hw-arm_dram_bw_bytes-corpus-11877-shape",
    ),
    pytest.param(
      {("amd_x86_uncore_df_milan", "dram_data_beats"): _TWO_HOSTS},
      "amd_x86_uncore_df_milan",
      [("dram_data_beats", _DF_BEATS_CONV)],
      id="df-family-type-beats-only",
    ),
    pytest.param(
      {
        ("amd64_df", "MBW_CHANNEL_0"): _TWO_HOSTS,
        ("amd64_df", "MBW_CHANNEL_1"): _TWO_HOSTS,
      },
      "amd64_df",
      [("MBW_CHANNEL_0", _DF_MBW_CONV), ("MBW_CHANNEL_1", _DF_MBW_CONV)],
      id="historical-df-mbw-first",
    ),
    pytest.param(
      {
        ("amd_x86_uncore_df_milan", "dram_chan0_bytes"): {
          "c001": None,
          "c002": None,
        },
        ("intel_x86_uncore_imc_spr", "dram_cas_reads"): _TWO_HOSTS,
        ("intel_x86_uncore_imc_spr", "hbm_cas_reads"): {
          "c001": 1.0e8,
          "c002": 3.0e8,
        },
      },
      "intel_x86_uncore_imc_spr",
      [
        ("dram_cas_reads", metrics.DDR_CAS_GIB),
        ("hbm_cas_reads", metrics.HBM_CAS_GIB),
      ],
      id="df-without-samples-falls-through-to-imc-ddr-plus-hbm",
    ),
  ],
)
def test_dram_bw_node_imbalance_matches_avg_mbw_family(spec, typ, weighted):
  """dram_bw_node_imbalance reads the family avg_mbw reads (same try order,
  fall-through, ARM IMC, host_cpu_hw ``arm_dram_bw_bytes``), with the
  tries' GiB per count as weights so DDR and HBM compare bytes."""
  table = _counter_table(spec)
  u = job_utils(table.metrics_view())
  assert metrics._dram_bw_weighted_events_for_imbalance(u) == (typ, weighted)
  # avg_mbw labels the probe name, the imbalance the schema type it read.
  assert typ in metrics.type_probe_names(
    metrics.Metrics()._job_arc_avg_mbw(table)[1]
  )
  val, typename, units = _imbalance(metrics.dram_bw_node_imbalance, table)
  assert (typename, units) == (typ, "%")
  assert val == pytest.approx(
    F.rate_imbalance_oracle(table, typ, weighted), rel=1e-9
  )
  assert val > 20.0
