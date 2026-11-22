from collections import OrderedDict

import numpy as np
import pytest

from hpcperfstats.analysis.metrics.lib.columnar.kernels import HostSeries
from hpcperfstats.analysis.metrics.lib.columnar.testing import host_series
from hpcperfstats.analysis.metrics.lib.gen import utils as utils_mod
from hpcperfstats.analysis.metrics.lib.gen.utils import utils


class _MockHost:
  def __init__(self, stats):
    self.stats = stats


class _MockJob:
  def __init__(self):
    # Two hosts: only host1 has "nvidia_gpu" stats; host2 has none.
    self.schemas = {"nvidia_gpu": ["utilization"]}
    self.hosts = {
      "host1": _MockHost(
        {
          "nvidia_gpu": {
            "gpu0": host_series(
              np.array([[0.0], [50.0], [100.0]], dtype=np.float64)
            ),
          }
        }
      ),
      "host2": _MockHost({}),
    }
    # Minimal acct required by utils.__init__
    self.acct = {"cores": 1, "nodes": 1}


def test_get_type_skips_hosts_without_typename():
  """utils.get_type should skip hosts that lack the requested typename instead of raising KeyError."""
  job = _MockJob()
  u = utils(job)

  schema, stats = u.get_type("nvidia_gpu", aggregate=False)

  assert schema is not None
  # Only host1 has nvidia_gpu stats; host2 is skipped.
  assert set(stats.keys()) == {"host1"}
  assert "gpu0" in stats["host1"]


def test_amd64_pmc_sets_pmc_and_freq_for_get_type():
  """AMD jobs expose amd64_pmc as logical 'pmc' like Intel typenames."""
  job = _MockJob()
  job.schemas = {"amd64_pmc": ["FLOPS", "APERF"]}
  job.hosts = {
    "h1": _MockHost(
      {
        "amd64_pmc": {
          "0": host_series(
            np.array([[0.0, 0.0], [1.0, 1.0]], dtype=np.float64)
          ),
        }
      }
    ),
  }
  u = utils(job)
  assert u.pmc == "amd64_pmc"
  assert u.freq == 2.7
  schema, stats = u.get_type("pmc", aggregate=True)
  assert schema is not None
  assert "h1" in stats


def test_cpu_counter_metrics_sets_pmc_for_get_type():
  """LIKWID cpu_counter_metrics is treated as the logical PMC typename for plots."""
  job = _MockJob()
  job.schemas = {"cpu_counter_metrics": ["INST_RETIRED", "APERF"]}
  job.hosts = {
    "h1": _MockHost(
      {
        "cpu_counter_metrics": {
          "0": host_series(
            np.array([[0.0, 0.0], [1.0, 2.0]], dtype=np.float64)
          ),
        }
      }
    ),
  }
  u = utils(job)
  assert u.pmc == "cpu_counter_metrics"


def test_pmc_prefers_amd64_over_intel_when_both_in_schema():
  """PMC resolution must not depend on dict iteration order (AMD before Intel)."""
  job = _MockJob()
  job.schemas = OrderedDict(
    [
      ("intel_x86_pmc_gpr8", ["instr_retired"]),
      ("amd_x86_pmc", ["fp_ops_retired", "aperf"]),
      ("amd64_pmc", ["FLOPS", "APERF"]),
    ]
  )
  job.hosts = {
    "h1": _MockHost(
      {
        "amd64_pmc": {
          "0": host_series(
            np.array([[0.0, 0.0], [1.0, 1.0]], dtype=np.float64)
          ),
        },
      }
    ),
  }
  u = utils(job)
  assert u.pmc in ("amd_x86_pmc", "amd64_pmc")
  assert u.freq == 2.7


def test_imc_first_match_follows_intel_imc_stats_order():
  """First IMC typename in imc_types_probe_order() that appears in schemas wins."""
  from hpcperfstats.dbload.lib.monitor_naming.resolve import (
    imc_types_probe_order,
  )

  job = _MockJob()
  job.schemas = OrderedDict(
    [
      ("intel_x86_uncore_imc_skx", ["dram_cas_reads"]),
      ("intel_x86_uncore_imc_hsw", ["dram_cas_reads", "dram_cas_writes"]),
    ]
  )
  job.hosts = {"h1": _MockHost({})}
  u = utils(job)
  expected_first = next(t for t in imc_types_probe_order() if t in job.schemas)
  assert u.imc == expected_first
  assert u.imc == "intel_x86_uncore_imc_skx"


def test_imc_short_form_spr_sets_u_imc():
  """Historical intel_spr_imc schema key is selected via probe order."""
  job = _MockJob()
  job.schemas = {
    "intel_spr_imc": ["dram_cas_reads", "dram_cas_writes", "hbm_cas_reads"]
  }
  job.hosts = {"h1": _MockHost({})}
  u = utils(job)
  assert u.imc == "intel_spr_imc"


def test_pick_pmc_typename_set_build_handles_sequence_typenames():
  """Iterable schema labels may stringify nested sequences; ``set()`` must not crash."""
  keys = ["intel_x86_pmc_gpr8", ["legacy", "label"], "intel_x86_pmc_gpr4"]
  assert utils_mod._pick_pmc_typename(keys) == "intel_x86_pmc_gpr8"


def test_get_type_returns_read_only_views_not_copies():
  """Regression (hs04 OOM, 2026-11-21): ``get_type`` copied every host's
  grid with ``astype`` (3.3-3.9 GiB per call on a 162-host job). It now
  returns read-only views of the pivot arrays, same values."""
  job = _MockJob()
  series = job.hosts["host1"].stats["nvidia_gpu"]["gpu0"]
  u = utils(job)
  _, split = u.get_type("nvidia_gpu", aggregate=False)
  view = split["host1"]["gpu0"]
  for name in ("t_s", "t_us", "values", "arc"):
    got, src = getattr(view, name), getattr(series, name)
    assert np.shares_memory(got, src) and not got.flags.writeable, name
    assert np.array_equal(got, src, equal_nan=True)
  _, agg = u.get_type("nvidia_gpu", aggregate=True)
  assert np.shares_memory(agg["host1"].values, series.values)
  # The caller's series stays writable; only the returned view is locked.
  assert series.values.flags.writeable


def test_host_series_array_like():
  """``HostSeries`` indexes and shapes like its value grid, so ``stats[:, j]``
  call sites keep working; ``aggregate=False`` keeps one series per device;
  several device series of one host sum on their shared times."""
  values = np.array([[1.0, 2.0], [3.0, 5.0], [6.0, 9.0]])
  s = host_series(values, [10.0, 20.0, 30.0])
  assert isinstance(s, HostSeries)
  assert s.shape == (3, 2)
  assert s[:, 1].tolist() == [2.0, 5.0, 9.0]
  assert s[-1, 0] == 6.0
  assert s.t_us.dtype == np.int64 and s.t_us.tolist()[0] == 10_000_000
  job = _MockJob()
  job.schemas = {"host_cpu": ["user", "system"]}
  job.hosts = {"h1": _MockHost({"host_cpu": {"0": s, "1": s}})}
  u = utils(job)
  _, per_dev = u.get_type("host_cpu", aggregate=False)
  assert set(per_dev["h1"]) == {"0", "1"}
  _, agg = u.get_type("host_cpu", aggregate=True)
  assert agg["h1"].values.tolist() == (2 * values).tolist()
  assert agg["h1"].t_s.tolist() == [10.0, 20.0, 30.0]
  with pytest.raises(ValueError):
    shifted = host_series(values, [11.0, 20.0, 30.0])
    job.hosts = {"h1": _MockHost({"host_cpu": {"0": s, "1": shifted}})}
    utils(job).get_type("host_cpu")


def test_get_type_columnar_series_on_each_hosts_own_times():
  """Columnar view: each host's series of a type is on that host's own
  sample times of the type (no job-wide axis, no NaN padding)."""
  from hpcperfstats.analysis.metrics.tests import per_host_axis_fixtures as F

  table = F.unaligned_17_41_table()
  u = utils(table.metrics_view())
  _, stats = u.get_type("host_cpu")
  oracle = F.host_series(table, "host_cpu", table.schema["host_cpu"])
  assert stats.keys() == oracle.keys()
  for host, s in stats.items():
    assert np.array_equal(s.t_us, oracle[host].t_us)
    np.testing.assert_array_equal(s.values, oracle[host].grid)
    assert not np.isnan(s.values).any()


def test_single_host_irregular_sampling_unchanged():
  """Single host, corpus-like irregular intervals, a duplicate floored
  second and sparse types (host_vm, host_lnet): every complex metric equals
  the pinned union-axis value (no binning path), except max_lnetbw, which
  the union axis left None (sparse host_lnet plus the "lnet" cluster lookup
  miss) and is now the host's own cluster arc peak."""
  from hpcperfstats.analysis.metrics.tests import per_host_axis_fixtures as F

  table = F.single_irregular_table()
  base = F.load_baseline()
  pinned = base["fixtures"]["single_irregular"]["metrics"]
  assert set(base["known_head_defects"]["single_irregular"]) == {"max_lnetbw"}
  got = {r["metric"]: r["value"] for r in F.compute_rows(table)}
  for metric, exp in pinned.items():
    if metric == "max_lnetbw":
      assert exp is None
      oracle = F.cluster_arc_peak_oracle(
        table, "host_lnet", ("tx_bytes", "rx_bytes"), 1024 * 1024
      )
      assert oracle is not None and got[metric] == oracle
    else:
      assert got[metric] == exp, (metric, got[metric], exp)
  # The sparse types keep only their own samples.
  _, vm = utils(table.metrics_view()).get_type("host_vm")
  assert vm["c001"].shape[0] == len(
    F.host_series(table, "host_vm", ["pgpgin"])["c001"].t_us
  )
