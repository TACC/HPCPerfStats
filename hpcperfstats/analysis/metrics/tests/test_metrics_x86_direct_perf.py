"""x86 direct-perf host_cpu_hw keys in the job metrics and plots.

The x86 monitor publishes on ``host_cpu_hw``:

* AMD ``fp_ops_retired`` (total FLOPs, Zen3 / Zen4 / Zen5) and
  ``amd_fp_dp_ops`` (double-precision FLOPs, Zen5 only); a class that does
  not program a key stores the NOT_PRESENT sentinel (-2).
* Intel ``fp_arith_inst_retired_<width>_<precision>`` (lowercase, W=48, no
  schema unit): retired instructions, so FLOPs need the lanes per width.
* ``pmu_enabled_time`` / ``pmu_running_time`` / ``pmu_solo_running_time``.

SPR HBM CAS counts are 32 bytes, DDR CAS 64; RAPL energy keys are running
totals in mJ (W=32).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from hpcperfstats.analysis.metrics.lib import metrics
from hpcperfstats.analysis.metrics.lib.columnar.roofline_series import (
  _intel_imc_bw,
  cpu_roofline_points,
)
from hpcperfstats.analysis.metrics.lib.columnar.summary_series import (
  RAPL_MJ_RATE_TO_W,
  build_summary_series,
)
from hpcperfstats.analysis.metrics.lib.columnar.testing import (
  table_from_records,
)
from hpcperfstats.analysis.metrics.lib.gen.cpu_fp_events import (
  INTEL_FP_ARITH_DOUBLE_LANES,
  INTEL_FP_ARITH_SINGLE_LANES,
  amd_flop_type_names,
)
from hpcperfstats.analysis.metrics.lib.gen.imc_cas_bw import (
  DDR_CAS_BYTES,
  HBM_CAS_BYTES,
)
from hpcperfstats.analysis.metrics.lib.gen.node_power_est import (
  mean_node_power_est_w,
)
from hpcperfstats.dbload.lib.sync_timedb_parsing import (
  _apply_counter_deltas,
  _compile_schema_token,
)

_T0 = datetime(2026, 11, 18, 10, 0, tzinfo=UTC)
_NOT_PRESENT = -2.0
_FLOP_METRICS = (
  "avg_flops",
  "avg_flops64b",
  "avg_flops32b",
  "avg_fp64_instr",
  "avg_fp32_instr",
)
_SHARING = ("avg_pmu_running_pct", "avg_pmu_outside_pct")
_INTEL_KEYS = tuple(
  ev for ev, _ in INTEL_FP_ARITH_DOUBLE_LANES + INTEL_FP_ARITH_SINGLE_LANES
)
_AMD_KEYS = ("fp_ops_retired", "amd_fp_dp_ops")
# Monitor alone on the PMU: 9 events enabled, 7 on a counter.
_PMU = {
  "pmu_enabled_time": 9.0e9,
  "pmu_running_time": 7.0e9,
  "pmu_solo_running_time": 7.0e9,
}


def _records(rates, typ="host_cpu_hw", hosts=("c1",), minutes=15):
  """One sample per minute per host; None rates are NOT_PRESENT rows."""
  rows = []
  for host in hosts:
    for i in range(minutes + 1):
      t = _T0 + timedelta(minutes=i)
      for event, rate in rates.items():
        if rate is None:
          rows.append((t, host, typ, event, "-", _NOT_PRESENT, None, None))
        else:
          rows.append((t, host, typ, event, "-", rate * 60.0 * i, rate, None))
  return rows


def _metric_rows(monkeypatch, table, names):
  """Run ``compute_metrics`` on ``table`` for the simple metrics ``names``."""
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
    n: spec for n, spec in m.simple_metrics_list.items() if n in names
  }
  m.complex_metrics_list = []
  job = SimpleNamespace(
    jid="x86perf1",
    telemetry_first_time=None,
    telemetry_last_time=None,
    host_data_schema_json=None,
  )
  out = m.compute_metrics(job)
  return {r["metric"]: r for r in out["rows"] if r["metric"] in names}


def _amd(total, dp, **extra):
  """AMD host_cpu_hw rates: Intel FP_ARITH keys NOT_PRESENT."""
  rates = dict.fromkeys(_INTEL_KEYS)
  rates.update(_PMU, fp_ops_retired=total, amd_fp_dp_ops=dp, **extra)
  return rates


def _intel(rate_by_key, pmu=True):
  """Intel host_cpu_hw rates: AMD FLOP keys NOT_PRESENT."""
  rates = dict.fromkeys(_AMD_KEYS)
  rates.update(rate_by_key)
  if pmu:
    rates.update(_PMU)
  return rates


@pytest.mark.parametrize("cpu_class", ["zen3_milan", "zen4_genoa"])
def test_zen3_zen4_total_flops_feed_avg_flops_but_no_cpu_pie(
  monkeypatch, cpu_class
):
  """Total FLOPs only: avg_flops has a value, the pie inputs have none."""
  del cpu_class  # Both classes publish the same keys (no DP split).
  rows = _metric_rows(
    monkeypatch,
    table_from_records(_records(_amd(2.0e12, None))),
    set(_FLOP_METRICS),
  )

  assert rows["avg_flops"]["value"] == pytest.approx(2000.0)
  assert rows["avg_flops"]["type"] == "host_cpu_hw"
  assert rows["avg_flops"]["no_data_reason"] is None
  for name in ("avg_fp64_instr", "avg_fp32_instr"):
    assert rows[name]["value"] is None, name
    assert rows[name]["no_data_reason"], name
  assert rows["avg_flops64b"]["value"] is None
  assert rows["avg_flops32b"]["value"] is None


def test_zen5_dp_split_feeds_pie_and_precision_flops(monkeypatch):
  """DP = amd_fp_dp_ops, SP = total - DP; no note from absent Intel keys."""
  rows = _metric_rows(
    monkeypatch,
    table_from_records(_records(_amd(3.0e12, 1.0e12), hosts=("c1", "c2"))),
    set(_FLOP_METRICS),
  )

  expected = {
    "avg_flops": 3000.0,
    "avg_fp64_instr": 1000.0,
    "avg_fp32_instr": 2000.0,
    "avg_flops64b": 1000.0,
    "avg_flops32b": 2000.0,
  }
  for name, value in expected.items():
    assert rows[name]["value"] == pytest.approx(value), name
    assert rows[name]["no_data_reason"] is None, name
    assert rows[name]["type"] == "host_cpu_hw", name


def test_zen5_idle_job_is_measured_zero(monkeypatch):
  """An idle Zen5 job stores 0 for both pie inputs (idle, not missing)."""
  rows = _metric_rows(
    monkeypatch,
    table_from_records(_records(_amd(0.0, 0.0))),
    {"avg_fp64_instr", "avg_fp32_instr", "avg_flops"},
  )

  for name in ("avg_fp64_instr", "avg_fp32_instr", "avg_flops"):
    assert rows[name]["value"] == 0.0, name
    assert rows[name]["no_data_reason"] is None, name


def test_zen5_scaled_dp_above_total_clamps_sp_to_zero(monkeypatch):
  """Separately scaled counters may put DP a hair above total: SP is 0."""
  rows = _metric_rows(
    monkeypatch,
    table_from_records(_records(_amd(1.0e12, 1.001e12))),
    {"avg_fp32_instr"},
  )

  assert rows["avg_fp32_instr"]["value"] == 0.0


def test_intel_fp_arith_lanes_weights(monkeypatch):
  """FLOPs = count x lanes (DP 1/2/4/8, SP 1/4/8/16); pie = instructions."""
  rows = _metric_rows(
    monkeypatch,
    table_from_records(_records(_intel(dict.fromkeys(_INTEL_KEYS, 1.0e9)))),
    set(_FLOP_METRICS),
  )

  expected = {
    "avg_flops": 15.0 + 29.0,
    "avg_flops64b": 15.0,
    "avg_flops32b": 29.0,
    "avg_fp64_instr": 4.0,
    "avg_fp32_instr": 4.0,
  }
  for name, value in expected.items():
    assert rows[name]["value"] == pytest.approx(value), name
    assert rows[name]["no_data_reason"] is None, name


def test_intel_512b_double_only(monkeypatch):
  """A 512-bit DGEMM: 1 Ginstr/s of 512b packed double = 8 GFLOP/s."""
  rates = dict.fromkeys(_INTEL_KEYS, 0.0)
  rates["fp_arith_inst_retired_512b_packed_double"] = 1.0e9
  rows = _metric_rows(
    monkeypatch,
    table_from_records(_records(_intel(rates))),
    set(_FLOP_METRICS),
  )

  assert rows["avg_flops"]["value"] == pytest.approx(8.0)
  assert rows["avg_flops64b"]["value"] == pytest.approx(8.0)
  assert rows["avg_flops32b"]["value"] == 0.0
  assert rows["avg_fp64_instr"]["value"] == pytest.approx(1.0)
  assert rows["avg_fp32_instr"]["value"] == 0.0


def test_intel_keys_without_direct_perf_marker_are_not_read(monkeypatch):
  """Older monitors left the lowercase keys at 0: no idle pie from them."""
  rows = _metric_rows(
    monkeypatch,
    table_from_records(
      _records(_intel(dict.fromkeys(_INTEL_KEYS, 0.0), pmu=False))
    ),
    {"avg_fp64_instr", "avg_fp32_instr"},
  )

  for name in ("avg_fp64_instr", "avg_fp32_instr"):
    assert rows[name]["value"] is None, name


def test_intel_idle_job_with_marker_is_measured_zero(monkeypatch):
  """Direct-perf Intel rows at 0 are an idle job."""
  rows = _metric_rows(
    monkeypatch,
    table_from_records(_records(_intel(dict.fromkeys(_INTEL_KEYS, 0.0)))),
    {"avg_fp64_instr", "avg_fp32_instr"},
  )

  for name in ("avg_fp64_instr", "avg_fp32_instr"):
    assert rows[name]["value"] == 0.0, name
    assert rows[name]["no_data_reason"] is None, name


def _counter_frame(event, token_flags, values):
  """Ingest frame of one lifetime counter, schema token compiled as ingest."""
  _ev, wid, mult, unit = _compile_schema_token(f"{event},{token_flags}")
  return pd.DataFrame(
    {
      "host": "h",
      "type": "host_cpu_hw",
      "dev": "0",
      "event": event,
      "unit": unit,
      "wid": wid,
      "mult": mult,
      "time": [float(60 * i) for i in range(len(values))],
      "value": values,
    }
  )


def test_intel_fp_arith_w48_wrap_and_unweighted_ingest():
  """W=48 lifetimes wrap mod 2^48 per CPU; ingest applies no lanes."""
  top = float(2**48) - 1.0e9
  out = _apply_counter_deltas(
    _counter_frame(
      "fp_arith_inst_retired_256b_packed_double",
      "E,W=48",
      [top, top + 6.0e10 - float(2**48)],
    )
  )
  assert float(out["delta"].iloc[1]) == pytest.approx(6.0e10)
  # Historical uppercase rows carried the lanes in U=, applied at ingest.
  legacy = _apply_counter_deltas(
    _counter_frame(
      "FP_ARITH_INST_RETIRED_256B_PACKED_DOUBLE", "E,W=48,U=4", [0.0, 10.0]
    )
  )
  assert float(legacy["delta"].iloc[1]) == pytest.approx(40.0)


def test_lane_tables_match_fp_arith_widths():
  """Lowercase keys and lanes per width, both precisions."""
  assert dict(INTEL_FP_ARITH_DOUBLE_LANES) == {
    "fp_arith_inst_retired_scalar_double": 1,
    "fp_arith_inst_retired_128b_packed_double": 2,
    "fp_arith_inst_retired_256b_packed_double": 4,
    "fp_arith_inst_retired_512b_packed_double": 8,
  }
  assert dict(INTEL_FP_ARITH_SINGLE_LANES) == {
    "fp_arith_inst_retired_scalar_single": 1,
    "fp_arith_inst_retired_128b_packed_single": 4,
    "fp_arith_inst_retired_256b_packed_single": 8,
    "fp_arith_inst_retired_512b_packed_single": 16,
  }
  assert amd_flop_type_names()[-2:] == ("host_cpu_hw", "cpu_counter_metrics")


def test_x86_sharing_gauge_from_host_cpu_hw(monkeypatch):
  """Intel rows carry the same sharing keys: running and outside shares."""
  holder = dict(_PMU, pmu_running_time=3.5e9)
  rows = _metric_rows(
    monkeypatch,
    table_from_records(
      _records(_intel(dict.fromkeys(_INTEL_KEYS, 1.0e9)) | holder)
    ),
    set(_SHARING),
  )

  assert rows["avg_pmu_running_pct"]["value"] == pytest.approx(350.0 / 9.0)
  assert rows["avg_pmu_outside_pct"]["value"] == pytest.approx(50.0)
  assert set(_SHARING) == metrics.STAFF_ONLY_JOB_METRICS


def _spr_imc_rows(dram, hbm):
  """SPR IMC rows with DDR and HBM CAS rates (counts per second)."""
  rates = {
    "dram_cas_reads": dram,
    "dram_cas_writes": 0.0,
    "hbm_cas_reads": hbm,
    "hbm_cas_writes": 0.0,
  }
  return _records(rates, typ="intel_x86_uncore_imc_spr")


def test_hbm_cas_is_32_bytes_ddr_64_in_avg_mbw(monkeypatch):
  """avg_mbw = DDR x 64 B + HBM x 32 B per CAS, in GiB/s."""
  assert (DDR_CAS_BYTES, HBM_CAS_BYTES) == (64, 32)
  rows = _metric_rows(
    monkeypatch,
    table_from_records(_spr_imc_rows(1.0e9, 4.0e9)),
    {"avg_mbw"},
  )

  assert rows["avg_mbw"]["value"] == pytest.approx(
    (1.0e9 * 64 + 4.0e9 * 32) / 1024**3
  )


def test_hbm_only_roofline_bandwidth_uses_32_bytes():
  """SPR Max (HBM only) roofline bandwidth: 32 B per HBM CAS."""
  rates = {"hbm_cas_reads": 2.0e9, "hbm_cas_writes": 1.0e9}
  table = table_from_records(_records(rates, typ="intel_x86_uncore_imc_spr"))

  bw = _intel_imc_bw(table, [])

  assert bw is not None
  assert np.allclose(bw.sum_val, 3.0e9 * 32 / 1024**3)


def test_summary_series_x86_flop_columns():
  """Summary: AMD total from host_cpu_hw, Zen5 SP = total - DP."""
  frame = build_summary_series(
    table_from_records(_records(_amd(3.0e12, 1.0e12)))
  ).frame
  assert np.allclose(frame.columns["amd_flops"], 3000.0)
  assert np.allclose(frame.columns["flops64b"], 1000.0)
  assert np.allclose(frame.columns["flops32b"], 2000.0)

  intel = build_summary_series(
    table_from_records(_records(_intel(dict.fromkeys(_INTEL_KEYS, 1.0e9))))
  ).frame
  assert np.allclose(intel.columns["flops64b"], 15.0)
  assert np.allclose(intel.columns["flops32b"], 29.0)
  assert "amd_flops" not in intel.columns


def test_amd_roofline_reads_host_cpu_hw_fp_ops_retired():
  """Zen roofline: host_cpu_hw FLOPs joined with family DF bandwidth."""
  df_rates = {f"dram_chan{i}_bytes": 1.0e10 for i in range(4)}
  table = table_from_records(
    _records(_amd(2.0e12, None))
    + _records(df_rates, typ="amd_x86_uncore_df_milan")
  )

  points, reason = cpu_roofline_points(table)

  assert reason is None
  assert points is not None
  assert np.allclose(points.flops_gf, 2000.0)


def test_intel_roofline_weights_lanes_only_on_direct_perf_rows():
  """Intel roofline FLOPs = count x lanes; old zero-filled rows are skipped."""
  imc = _spr_imc_rows(1.0e9, 0.0)
  table = table_from_records(
    _records(_intel(dict.fromkeys(_INTEL_KEYS, 1.0e9))) + imc
  )

  points, reason = cpu_roofline_points(table)

  assert reason is None
  assert np.allclose(points.flops_gf, 44.0)
  old = table_from_records(
    _records(_intel(dict.fromkeys(_INTEL_KEYS, 1.0e9), pmu=False)) + imc
  )
  assert cpu_roofline_points(old)[0] is None


def test_rapl_mj_running_total_w32_wrap_gives_watts():
  """RAPL energy: mJ totals mod 2^32; watts = 1e-3 x unwrapped mJ / s."""
  top = float(2**32) - 3.0e6
  out = _apply_counter_deltas(
    _counter_frame(
      "pkg_energy", "E,W=32,U=mJ", [top, top + 1.2e7 - float(2**32)]
    )
  )
  # 1.2e7 mJ over 60 s = 200 W.
  assert float(out["delta"].iloc[1]) == pytest.approx(1.2e7)
  assert float(out["delta"].iloc[1]) / 60.0 * RAPL_MJ_RATE_TO_W == (
    pytest.approx(200.0)
  )
  table = table_from_records(
    _records({"pkg_energy": 2.0e5}, typ="amd_x86_rapl")
  )
  assert mean_node_power_est_w(table) == pytest.approx(200.0)


def test_metrics_contract_version_bumped_for_x86_direct_perf():
  """Contract 12 covered the x86 FLOP, sharing, HBM and RAPL changes; 13 is
  the AMD DF 64 B per beat fix."""
  assert metrics.METRICS_CONTRACT_VERSION >= 13
