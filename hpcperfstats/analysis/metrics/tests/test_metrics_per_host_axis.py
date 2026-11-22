"""Per-host metrics time axis: fixtures, pinned HEAD baseline and oracles.

The baseline (``fixtures/per_host_axis_baseline.json``) holds every complex
metric and the cluster value / arc grids of the union-axis code at
``BASE_REV`` on fixtures where that code is exact (aligned multi-host and
single-host). The per-host code must reproduce them there (node imbalances
and the listed cluster arc peaks follow their oracle instead). The oracles
in ``per_host_axis_fixtures`` are independent NumPy references of the
per-host semantics; on aligned fixtures they agree with the pinned values,
which is what makes them usable as the reference on offset fixtures.
"""

from __future__ import annotations

import math
import os

import numpy as np
import pytest

from hpcperfstats.analysis.metrics.tests import per_host_axis_fixtures as F

SYNTHETIC = tuple(k for k in F.FIXTURES if k != "edge_multihost")
US = F.US


def _build(name):
  table = F.FIXTURES[name].build()
  if table is None:
    if os.environ.get("HPCPERFSTATS_METRICS_GOLDEN_REQUIRED") == "1":
      pytest.fail("golden corpus required but edge_multihost is absent")
    pytest.skip("golden corpus absent (HPCPERFSTATS_METRICS_GOLDEN_DIR)")
  return table


@pytest.fixture(scope="module")
def baseline():
  return F.load_baseline()


def _host_times(table, typ="host_cpu"):
  return {h: s.t_us for h, s in F.host_series(table, typ, ["user"]).items()}


def _close(got, exp, rtol, atol=0.0):
  if exp is None or got is None:
    return got is exp
  return math.isclose(got, exp, rel_tol=rtol, abs_tol=atol)


@pytest.mark.parametrize("name", SYNTHETIC)
def test_synthetic_fixtures_build_deterministically(name):
  assert F.frame_digest(_build(name)) == F.frame_digest(_build(name))


def test_fixture_time_shapes():
  aligned = _host_times(_build("aligned_2dev"))
  assert len(aligned) == 4
  assert all(np.array_equal(t, aligned["c001"]) for t in aligned.values())

  offsets = _host_times(_build("unaligned_17_41"))
  firsts = sorted((t[0] - F.BASE_EPOCH_S * US) / US for t in offsets.values())
  assert firsts == [0, 17, 41]
  assert np.unique(np.concatenate(list(offsets.values()))).size == 90

  mostly = _host_times(_build("mostly_offset"))
  shared = np.intersect1d(mostly["c001"], mostly["c002"])
  assert shared.size == len(F.MOSTLY_OFFSET_SHARED_TICKS) == 3

  sub = _host_times(_build("subsecond"))
  allt = np.concatenate(list(sub.values()))
  assert np.unique(allt).size == allt.size  # no shared timestamp
  regular = [
    t[np.isin(t, F._ticks(30, offset_s=58 + o))]
    for t, o in zip(sub.values(), F.SUBSECOND_OFFSETS_S, strict=True)
  ]
  assert all(r.size == 30 for r in regular)
  assert {int(np.floor(x / US)) % 60 for r in regular for x in r} == {58}
  assert [t.size for t in sub.values()] == [30, 30, 34, 37]

  single = _build("single_irregular")
  t = _host_times(single)["c001"]
  assert np.unique(np.floor_divide(t, US)).size == t.size - 1
  vm = F.host_series(single, "host_vm", ["pgpgin"])["c001"].t_us
  lnet = F.host_series(single, "host_lnet", ["rx_bytes"])["c001"].t_us
  assert set(vm) < set(t) and set(lnet) < set(t)
  assert vm.size < lnet.size < t.size


@pytest.mark.parametrize("name", SYNTHETIC)
def test_fixture_arc_is_the_ingest_rate_of_the_counter(name):
  table = _build(name)
  for typ, events in table.schema.items():
    counters = [e for e in events if F.TYPE_EVENTS[typ][e][0] == F.COUNTER]
    arcs = F.host_series(table, typ, counters, "arc")
    for host, v in F.host_series(table, typ, counters, "value").items():
      dt = np.diff(v.t_us) / US
      assert np.all(np.isnan(arcs[host].grid[0]))
      np.testing.assert_allclose(
        arcs[host].grid[1:],
        np.diff(v.grid, axis=0) / dt[:, None],
        rtol=2e-7,
        err_msg=f"{typ} {host}",
      )


MULTI_ALIGNED = ("edge_multihost", "aligned_2dev")
NODE_IMBALANCES = tuple(F.SYNTH_IMBALANCE_SPECS)
MIB = 1024 * 1024
# Pinned union-axis values that are wrong on purpose (oracle per metric): the
# union-axis code looked up "lnet" in grids keyed "host_lnet", so the arc peak
# never ran; the cluster lookup resolves type_probe_names.
CLUSTER_ARC_PEAK_SPECS = {
  "max_lnetbw": ("host_lnet", ("tx_bytes", "rx_bytes"), MIB),
}


def _metrics(table):
  return {r["metric"]: r["value"] for r in F.compute_rows(table)}


@pytest.mark.parametrize("name", MULTI_ALIGNED)
def test_aligned_multi_host_cluster_grids_unchanged(name, baseline):
  """Hosts sampling on the same ticks: the binned cluster value grids are
  the pinned union-axis grids bit for bit (bins are the ticks, rows enter
  each cell in frame order). The arc grids are too, except the first tick,
  which holds every host's first owned sample: its arc covers the interval
  before the job, so the cluster arc leaves it out (NaN)."""
  table = _build(name)
  exp = baseline["fixtures"][name]
  assert F.frame_digest(table) == exp["frame_sha256"]
  got = F.cluster_grids(table)
  assert got.keys() == exp["cluster_grids"].keys()
  for typ, grids in exp["cluster_grids"].items():
    hosts = F.host_series(table, typ, table.schema[typ]).values()
    firsts = {s.t_us[0] for s in hosts}
    assert len(firsts) == 1  # aligned: every host starts on the first tick
    np.testing.assert_array_equal(
      got[typ]["value"], F.decode_grid(grids["value"]), err_msg=typ
    )
    arc, pinned = got[typ]["arc"], F.decode_grid(grids["arc"])
    assert np.isnan(arc[0]).all(), typ
    np.testing.assert_array_equal(arc[1:], pinned[1:], err_msg=typ)


@pytest.mark.parametrize("name", MULTI_ALIGNED)
def test_aligned_multi_host_metrics_match_baseline(name, baseline):
  """Every complex metric equals the pinned union-axis value exactly, except
  the node imbalances (arc oracle; their rate source is the ingest arc) and
  the listed cluster arc peaks (cluster lookup now resolves the type)."""
  table = _build(name)
  pinned = baseline["fixtures"][name]["metrics"]
  got = _metrics(table)
  imb = F.node_imbalance_oracles(table, F.FIXTURES[name].imbalance_specs)
  for metric, exp in pinned.items():
    if metric in imb:
      assert _close(got[metric], imb[metric], 1e-9), (metric, got[metric])
    elif metric in CLUSTER_ARC_PEAK_SPECS:
      oracle = F.cluster_arc_peak_oracle(table, *CLUSTER_ARC_PEAK_SPECS[metric])
      assert oracle is not None and oracle != exp
      assert _close(got[metric], oracle, 1e-12), (metric, got[metric], oracle)
    else:
      assert got[metric] == exp, (metric, got[metric], exp)


def test_cluster_lookup_resolves_type_probe_names():
  """Regression: the cluster grids were looked up by the metric's short name
  ("lnet", "nfs", "opa", "net") while the pivot is keyed by schema type
  (host_lnet, ...), so the arc peak never ran. The lookup resolves aliases
  like get_type."""
  from hpcperfstats.analysis.metrics.lib import metrics
  from hpcperfstats.analysis.metrics.lib.gen.utils import utils

  table = _build("aligned_2dev")
  u = utils(table.metrics_view())
  short = metrics._cluster_grids(u, "lnet")
  full = metrics._cluster_grids(u, "host_lnet")
  assert short is not None
  np.testing.assert_array_equal(short.arc, full.arc)
  assert metrics._cluster_grids(u, "net") is not None
  value, typ, _units = metrics.max_lnetbw().compute_metric(u)
  assert typ == "lnet"
  assert value == F.cluster_arc_peak_oracle(
    table, "host_lnet", ("tx_bytes", "rx_bytes"), MIB
  )


def test_unaligned_hosts_metrics_match_per_host_oracle():
  """Hosts at 0 / 17 / 41 s offsets: endpoint, per-host rate and cross-host
  metrics equal the oracle on each host's own axis; none is NaN, None or a
  false 0."""
  table = _build("unaligned_17_41")
  got = _metrics(table)
  want = {
    **F.per_host_oracles(table),
    **F.node_imbalance_oracles(table, F.SYNTH_IMBALANCE_SPECS),
  }
  for metric, exp in want.items():
    assert exp is not None and exp > 0, metric
    assert _close(got[metric], exp, 1e-9), (metric, got[metric], exp)


def test_peak_value_fallback_never_mixes_hosts():
  """Without arc, the cluster value fallback differences bins: on offset
  hosts each bin difference is over the same hosts' counters, never one
  host's counter minus another's (counters differ by ~1e10 between hosts,
  so a mixed difference would be a rate many orders too large)."""
  from hpcperfstats.analysis.metrics.lib import metrics
  from hpcperfstats.analysis.metrics.lib.gen.utils import utils

  table = _build("unaligned_17_41")
  table.frame.arc[:] = np.nan
  u = utils(table.metrics_view())
  events = ("tx_bytes", "rx_bytes")
  grids = metrics._cluster_grids(u, "host_lnet")
  assert not grids.same_hosts.all()  # the last bin holds only one host
  value, _typ, _units = metrics.max_lnetbw().compute_metric(u)
  host_peak = F.host_value_peak_oracle(table, "host_lnet", events) / MIB
  assert value is not None and 0 < value <= host_peak
  # Each kept bin difference is the mean of the bin's hosts' own rates (one
  # sample per host per bin here); host-set changes are left out.
  series = F.host_series(table, "host_lnet", list(events))
  grid = metrics._bin_grid([s.t_us for s in series.values()])
  by_end_bin: dict[int, list[float]] = {}
  for s in series.values():
    rate = np.diff(s.grid.sum(axis=1)) / np.diff(s.t_s)
    ends = grid.bin_of(s.t_us[1:]).tolist()
    for b, r in zip(ends, rate.tolist(), strict=True):
      by_end_bin.setdefault(b, []).append(r)
  cluster = metrics._per_interval_rate(
    grids.value[:, 0] + grids.value[:, 1], grids.t_s
  )
  kept = np.flatnonzero(grids.same_hosts)
  # The first bin lacks the 41 s host and the last holds only it.
  assert kept.size == grid.raw_bins.size - 3
  for k in kept.tolist():
    assert cluster[k] == pytest.approx(np.mean(by_end_bin[k + 1]), rel=1e-9)
  assert value == pytest.approx(cluster[kept].max() / MIB, rel=1e-12)


@pytest.mark.parametrize(
  "host_offsets_s", [(0.0,), (0.0, 0.0, 0.0), (0.0, 17.0, 41.0)]
)
def test_cluster_arc_peak_ignores_first_sample_pre_job_interval(
  host_offsets_s,
):
  """Regression (golden 3449555 ``max_fabricbw``): a host's first job-owned
  sample carries the ingest arc of the interval *before* it, which starts
  before the job and can hold the previous job's traffic. The cluster arc
  grid drops that arc, so a huge pre-job rate never becomes the peak; the
  value grid keeps the sample."""
  from hpcperfstats.analysis.metrics.lib import metrics
  from hpcperfstats.analysis.metrics.lib.gen.utils import utils

  table = F.build_table(
    "pre_job_arc",
    {
      f"c{k:03d}": F._ticks(12, offset_s=o)
      for k, o in enumerate(host_offsets_s)
    },
    seed=11,
    types=("host_lnet",),
  )
  f = table.frame
  events = ("tx_bytes", "rx_bytes")
  clean = F.cluster_arc_peak_oracle(table, "host_lnet", events, MIB)
  first = np.zeros(f.time_us.size, dtype=bool)
  for h in np.unique(f.host_id).tolist():
    on_h = f.host_id == h
    first |= on_h & (f.time_us == f.time_us[on_h].min())
  pre_job = 1e6 * MIB  # far above every in-job rate
  f.arc[first] = pre_job
  u = utils(table.metrics_view())
  grids = metrics._cluster_grids(u, "host_lnet")
  value, _typ, _units = metrics.max_lnetbw().compute_metric(u)
  assert clean is not None and 0 < clean < pre_job / MIB / 1e3
  assert value == pytest.approx(clean, rel=1e-12)
  # The oracle encodes the same rule independently of the pivot.
  assert F.cluster_arc_peak_oracle(
    table, "host_lnet", events, MIB
  ) == pytest.approx(clean, rel=1e-12)
  # Only the arc of first samples is dropped; value keeps every sample.
  assert np.isfinite(grids.value).all()
  assert np.nanmax(grids.arc) < pre_job


def test_value_fallback_host_set_check_is_exact():
  """Regression: the host-set check compared count, sum, max and min of the
  bins' host ids, which {0, 1, 4, 5} and {0, 2, 3, 5} share, so the value
  fallback differenced counters of different hosts (about 2x the per-host
  peak). Neighbouring bins with different host sets are left out."""
  from hpcperfstats.analysis.metrics.lib import metrics
  from hpcperfstats.analysis.metrics.lib.gen.utils import utils

  t = F._ticks(20)
  times = {}
  for h in range(6):
    keep = np.ones(t.size, dtype=bool)
    keep[10] = h not in (2, 3)
    keep[11] = h not in (1, 4)
    times[f"c{h:03d}"] = t[keep]
  table = F.build_table(
    "host_set",
    times,
    seed=7,
    types=("host_lnet",),
    host_factors=(1.0, 0.72, 0.86, 0.55, 0.93, 0.6),
  )
  table.frame.arc[:] = np.nan
  u = utils(table.metrics_view())
  same = metrics._cluster_grids(u, "host_lnet").same_hosts
  assert np.flatnonzero(~same).tolist() == [9, 10, 11]
  value, _typ, _units = metrics.max_lnetbw().compute_metric(u)
  host_peak = F.host_value_peak_oracle(
    table, "host_lnet", ("tx_bytes", "rx_bytes")
  )
  assert value is not None and 0 < value <= host_peak / MIB


@pytest.mark.parametrize("name", ["unaligned_17_41", "mostly_offset"])
def test_gpu_instantaneous_imbalance_offset_hosts(name):
  """Snapshot imbalances on offset samples compare hosts per bin (not one
  host per union tick, which gave 0 or the noise of shared ticks)."""
  table = _build(name)
  got = _metrics(table)
  for metric in ("gpu_util_node_imbalance", "tensor_node_imbalance"):
    _kind, typ, event = F.SYNTH_IMBALANCE_SPECS[metric]
    exp = F.snapshot_imbalance_oracle(table, typ, event)
    assert exp is not None and exp > 0
    assert _close(got[metric], exp, 1e-9), (metric, got[metric], exp)


def _host_cpu_times(table):
  return [s.t_us for s in F.host_series(table, "host_cpu", ["user"]).values()]


def test_subsecond_offsets_share_bins():
  """Production Horizon shape: same second, sub-second offsets, no shared
  timestamp, extra samples ``SUBSECOND_EXTRA_DELAY_S`` after a tick. Every
  regular sample and every interval rate (end-sample rule) of every host
  lands in one bin per tick, so the metrics equal the oracle and avg_ethbw
  is not NULL."""
  from hpcperfstats.analysis.metrics.lib import metrics

  table = _build("subsecond")
  times = _host_cpu_times(table)
  grid = metrics._bin_grid(times)
  assert grid.period_us == 60 * US and grid.raw_bins.size == 30
  delay = round(F.SUBSECOND_EXTRA_DELAY_S * US)
  for t, off in zip(times, F.SUBSECOND_OFFSETS_S, strict=True):
    regular = F._ticks(30, offset_s=58 + off)
    assert np.array_equal(grid.bin_of(regular), np.arange(30))
    extra = t[~np.isin(t, regular)]
    assert np.array_equal(grid.bin_of(extra), grid.bin_of(extra - delay))
  end_bins = [set(grid.bin_of(t[1:]).tolist()) for t in times]
  assert (
    set.intersection(*end_bins) == set.union(*end_bins) == set(range(1, 30))
  )
  got = _metrics(table)
  want = {
    **F.per_host_oracles(table),
    **F.node_imbalance_oracles(table, F.SYNTH_IMBALANCE_SPECS),
  }
  for metric, exp in want.items():
    assert exp is not None and exp > 0, metric
    assert _close(got[metric], exp, 1e-9), (metric, got[metric], exp)
  assert got["avg_ethbw"] is not None


@pytest.mark.parametrize(
  "offsets_s", [(0.0, -0.4, 0.4, 0.25), F.SUBSECOND_OFFSETS_S]
)
def test_rate_bins_hold_all_hosts_with_offsets(offsets_s):
  """Rates go to the bin of their interval's end sample, which sits at a bin
  centre: every rate bin holds every host. Midpoints sit on bin edges, so
  midpoint binning splits the hosts between neighbouring bins."""
  from hpcperfstats.analysis.metrics.lib import metrics

  times = [F._ticks(20, offset_s=58 + o) for o in offsets_s]
  grid = metrics._bin_grid(times)
  end_bins = [set(grid.bin_of(t[1:]).tolist()) for t in times]
  assert set.intersection(*end_bins) == set.union(*end_bins)
  mid_bins = [set(grid.bin_of((t[:-1] + t[1:]) // 2).tolist()) for t in times]
  assert set.intersection(*mid_bins) != set.union(*mid_bins)


def test_bin_rule_edge_cases():
  from hpcperfstats.analysis.metrics.lib import metrics

  # No host with two distinct samples: no period, cross-host value None.
  one_each = [np.array([US]), np.array([5 * US, 5 * US])]
  assert metrics._bin_grid(one_each) is None
  assert (
    metrics._binned_imbalance_percent(
      one_each, one_each, [np.ones(1), np.ones(2)], [np.ones(1), np.ones(2)]
    )
    is None
  )
  # Phases spread evenly over the period: R < 0.5, phase 0, still compacted.
  spread = [
    F._ticks(10, offset_s=o) for o in np.linspace(0, 60, 12, endpoint=False)
  ]
  grid = metrics._bin_grid(spread)
  assert grid.phase_us == 0 and grid.raw_bins.size <= 11
  # Phases straddling the period edge (59.9 s and 0.1 s) average to the edge.
  edge = [F._ticks(10, offset_s=59.9), F._ticks(10, offset_s=60.1)]
  grid = metrics._bin_grid(edge)
  assert abs(grid.phase_us) < US and grid.raw_bins.size == 10
  assert np.array_equal(grid.bin_of(edge[0]), grid.bin_of(edge[1]))
  # One stray far sample: one extra compacted bin, not a long empty run.
  stray = [
    F._ticks(10),
    np.concatenate((F._ticks(10), F._ticks(1, offset_s=1e6))),
  ]
  grid = metrics._bin_grid(stray)
  assert grid.raw_bins.size == 11
  assert grid.bin_of(stray[1]).max() == 10
  # A type on one host of a multi-host job is not binned: 0.0 by itself.
  table = F.build_table(
    "gpu_on_one_host",
    {"c001": F._ticks(10), "c002": F._ticks(10, offset_s=17)},
    seed=7,
    types=("host_cpu",),
  )
  gpu_only = F.build_table(
    "gpu_rows", {"c001": F._ticks(10)}, seed=8, types=("nvidia_gpu",)
  )
  merged = F.table_from_records(
    [
      r
      for t in (table, gpu_only)
      for r in zip(
        t.frame.time_us.tolist(),
        [t.frame.hosts[h] for h in t.frame.host_id],
        [t.frame.series[s][0] for s in t.frame.series_id],
        [t.frame.series[s][1] for s in t.frame.series_id],
        [t.frame.devs[d] for d in t.frame.dev_id],
        t.frame.owned.tolist(),
        t.frame.value.tolist(),
        t.frame.arc.tolist(),
        t.frame.delta.tolist(),
        strict=True,
      )
    ],
    jid="merged",
  )
  got = _metrics(merged)
  assert got["gpu_util_node_imbalance"] == 0.0
  assert got["node_imbalance"] > 0
  # Duplicate floored seconds (golden 3339983 shape): two samples 0.6 s
  # apart share a bin; their rates are a time-weighted collision mean.
  t_dup = np.array([0, 60_000_000, 60_600_000, 120_000_000])
  t_reg = np.array([0, 60_000_000, 120_000_000])
  grid = metrics._bin_grid([t_dup, t_reg])
  assert grid.bin_of(t_dup).tolist() == [0, 1, 1, 2]
  v = metrics._binned_imbalance_percent(
    [t_dup, t_reg],
    [t_dup[1:], t_reg[1:]],
    [np.array([1.0, 4.0, 1.0]), np.array([1.0, 1.0])],
    [np.diff(t_dup) / US, np.diff(t_reg) / US],
  )
  # Host 1 bin 1: (1 * 60 + 4 * 0.6) / 60.6; bin 2: 1. Host 2: 1, 1.
  peak = (60 + 2.4) / 60.6
  assert v == pytest.approx(100 * (peak - 1) / peak / 2, rel=1e-12)


@pytest.mark.parametrize("name", SYNTHETIC)
def test_baseline_fixture_digests_match_builders(name, baseline):
  rec = baseline["fixtures"].get(name) or baseline[
    "head_unaligned_characterization"
  ].get(name)
  assert rec is not None, name
  assert rec["frame_sha256"] == F.frame_digest(_build(name))


def test_baseline_metric_values_are_json_safe(baseline):
  for name, rec in baseline["fixtures"].items():
    for metric, v in rec["metrics"].items():
      assert v is None or (isinstance(v, (int, float)) and math.isfinite(v)), (
        name,
        metric,
      )
    assert rec["nan_metrics"] == [], name


@pytest.mark.parametrize("name", ["aligned_2dev", "single_irregular"])
def test_imbalance_oracles_agree_with_head_on_aligned(name, baseline):
  """Arc rates differ from diff(value) / diff(t) only by float32 rounding of
  arc, so on a job where HEAD's union axis is exact the arc oracle equals
  the pinned value to ~1e-8; snapshot imbalances read the same values."""
  table = _build(name)
  pinned = baseline["fixtures"][name]["metrics"]
  got = F.node_imbalance_oracles(table, F.FIXTURES[name].imbalance_specs)
  for metric, value in got.items():
    kind = F.FIXTURES[name].imbalance_specs[metric][0]
    rtol = 1e-7 if kind == "rate" else 1e-12
    assert _close(value, pinned[metric], rtol), (metric, value, pinned[metric])
  if name == "aligned_2dev":
    assert all(v > 10 for v in got.values())


def test_imbalance_oracles_on_edge_multihost(baseline):
  table = _build("edge_multihost")
  pinned = baseline["fixtures"]["edge_multihost"]["metrics"]
  got = F.node_imbalance_oracles(table, F.EDGE_IMBALANCE_SPECS)
  # HEAD misses the AMD fp_ops_retired family: None where the oracle reads
  # the resolved family.
  assert pinned["flops_node_imbalance"] is None
  assert got["flops_node_imbalance"] == 0.0
  # Near-equal host rates: HEAD's value diffs leave ~1e-5 noise, arc gives 0.
  assert _close(
    got["fabric_node_imbalance"], pinned["fabric_node_imbalance"], 0, atol=1e-4
  )
  for metric in set(got) - {"flops_node_imbalance", "fabric_node_imbalance"}:
    assert _close(got[metric], pinned[metric], 1e-7), metric


@pytest.mark.parametrize("name", ["aligned_2dev", "single_irregular"])
def test_per_host_oracles_agree_with_head_on_aligned(name, baseline):
  pinned = baseline["fixtures"][name]["metrics"]
  for metric, value in F.per_host_oracles(_build(name)).items():
    assert value is not None, metric
    assert _close(value, pinned[metric], 1e-12), (metric, value, pinned[metric])


def test_time_imbalance_oracle_on_edge_multihost(baseline):
  pinned = baseline["fixtures"]["edge_multihost"]["metrics"]["time_imbalance"]
  assert _close(
    F.time_imbalance_oracle(_build("edge_multihost")), pinned, 1e-12
  )


def test_bin_grid_subsecond_offsets_share_tick_bins():
  table = _build("subsecond")
  times = _host_times(table)
  grid = F.bin_grid(times)
  assert grid.period_us == 60 * US
  assert grid.resultant > 0.99
  for t, off in zip(times.values(), F.SUBSECOND_OFFSETS_S, strict=True):
    regular = F._ticks(30, offset_s=58 + off)
    assert np.array_equal(grid.bin_of(regular), np.arange(30))
    extra = t[~np.isin(t, regular)]
    delay = round(F.SUBSECOND_EXTRA_DELAY_S * US)
    assert np.array_equal(grid.bin_of(extra), grid.bin_of(extra - delay))
  end_bins = [set(grid.bin_of(t[1:]).tolist()) for t in times.values()]
  assert set.intersection(*end_bins) == set(range(1, 30))
  mids = [
    set(grid.bin_of((t[:-1] + t[1:]) // 2).tolist())
    for t in (F._ticks(30, offset_s=58 + o) for o in F.SUBSECOND_OFFSETS_S)
  ]
  assert set.intersection(*mids) != set.union(*mids)


def test_bin_grid_edge_cases():
  assert F.bin_grid({"a": np.array([US]), "b": np.array([5 * US])}) is None
  spread = {
    f"h{i}": F._ticks(10, offset_s=o)
    for i, o in enumerate(np.linspace(0, 60, 12, endpoint=False))
  }
  grid = F.bin_grid(spread)
  assert grid.resultant < 0.5 and grid.phase_us == 0.0
  stray = {
    "a": F._ticks(10),
    "b": np.concatenate((F._ticks(10), F._ticks(1, offset_s=1e6))),
  }
  grid = F.bin_grid(stray)
  assert grid.raw_bins.size == 11
  assert grid.bin_of(stray["b"]).max() == 10
  offset = F.bin_grid(_host_times(_build("unaligned_17_41")))
  assert offset.phase_us == 0.0 and offset.resultant < 0.5


def test_single_host_imbalance_is_zero_without_binning():
  table = _build("single_irregular")
  got = F.node_imbalance_oracles(table, F.SYNTH_IMBALANCE_SPECS)
  assert set(got.values()) == {0.0}


@pytest.mark.parametrize(
  "name", ["unaligned_17_41", "mostly_offset", "subsecond"]
)
def test_oracles_give_real_values_where_head_gives_nan_or_false_zero(
  name, baseline
):
  table = _build(name)
  head = baseline["head_unaligned_characterization"][name]
  imb = F.node_imbalance_oracles(table, F.SYNTH_IMBALANCE_SPECS)
  per_host = F.per_host_oracles(table)
  for metric, value in {**imb, **per_host}.items():
    assert value is not None and math.isfinite(value) and value > 0, metric
  assert set(head["nan_metrics"]) == {
    "avg_freq",
    "avg_packetsize",
    "avg_vector_width_32b",
    "avg_vector_width_64b",
    "vecpercent_32b",
    "vecpercent_64b",
  }
  assert head["metrics"]["avg_ethbw"] is None
  assert head["metrics"]["time_imbalance"] in (None, 0.0)
  assert head["metrics"]["node_imbalance"] == 0.0
