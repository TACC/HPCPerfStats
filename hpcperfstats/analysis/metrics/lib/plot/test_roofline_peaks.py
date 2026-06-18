"""Unit tests for schema-based roofline peak inference."""
from unittest.mock import MagicMock

import pandas as pd
import pytest

from hpcperfstats.analysis.metrics.lib.plot.roofline_peaks import (
  ROOFLINE_CPU_PEAK_GFLOPS_AND_BW_GBPS,
  infer_cpu_roofline_peak_flops_and_bw_gbps,
  infer_gpu_roofline_peak_flops_and_bw_gbps,
  lookup_roofline_cpu_peaks,
)


def _make_jt(schema, aggregate_map=None):
  jt = MagicMock()
  jt.schema = schema
  aggregate_map = aggregate_map or {}

  def get_aggregate_df(typ, val_col, events, conv=1.0):
    rows = aggregate_map.get((typ, val_col, tuple(events)), [])
    if not rows:
      return pd.DataFrame(columns=["host", "time", "sum_val"])
    df = pd.DataFrame(rows, columns=["host", "time", "sum_val"])
    df["sum_val"] = df["sum_val"].astype(float) * conv
    return df

  jt.get_aggregate_df.side_effect = get_aggregate_df
  return jt


def test_infer_roofline_peaks_intel_skx_when_only_skx_in_schema():
  jt = _make_jt({"intel_x86_uncore_imc_skx": []})
  gf, bw = infer_cpu_roofline_peak_flops_and_bw_gbps(jt)
  assert (gf, bw) == ROOFLINE_CPU_PEAK_GFLOPS_AND_BW_GBPS["intel_x86_uncore_imc_skx"]


def test_infer_roofline_peaks_intel_skx_legacy_typename_dual_read():
  jt = _make_jt({"intel_skx_imc": []})
  gf, bw = infer_cpu_roofline_peak_flops_and_bw_gbps(jt)
  assert (gf, bw) == ROOFLINE_CPU_PEAK_GFLOPS_AND_BW_GBPS["intel_x86_uncore_imc_skx"]


def test_infer_roofline_peaks_intel_prefers_earlier_imc_generation_in_list():
  """First imc_types_probe_order() entry present in schema wins (canonical before legacy)."""
  jt = _make_jt({"intel_x86_uncore_imc_hsw": [], "intel_x86_uncore_imc_skx": []})
  gf, bw = infer_cpu_roofline_peak_flops_and_bw_gbps(jt)
  assert (gf, bw) == ROOFLINE_CPU_PEAK_GFLOPS_AND_BW_GBPS["intel_x86_uncore_imc_skx"]


def test_infer_roofline_peaks_amd_default_when_amd_counters_present():
  jt = _make_jt({"amd_x86_pmc": [], "amd_x86_uncore_df": []})
  gf, bw = infer_cpu_roofline_peak_flops_and_bw_gbps(jt)
  assert (gf, bw) == ROOFLINE_CPU_PEAK_GFLOPS_AND_BW_GBPS["amd64_epyc_2s_default"]


def test_infer_roofline_peaks_grace_when_arm_imc_present():
  jt = _make_jt({"arm_aarch64_imc": [], "host_cpu_hw": []})
  gf, bw = infer_cpu_roofline_peak_flops_and_bw_gbps(jt)
  assert (gf, bw) == ROOFLINE_CPU_PEAK_GFLOPS_AND_BW_GBPS["nvidia_grace_cpu_chip"]


def test_infer_roofline_peaks_none_for_empty_or_nondict_schema():
  jt = MagicMock()
  assert infer_cpu_roofline_peak_flops_and_bw_gbps(jt) == (None, None)
  jt = _make_jt({})
  assert infer_cpu_roofline_peak_flops_and_bw_gbps(jt) == (None, None)


def test_infer_cpu_roofline_prefers_host_roofline_peak_when_complete():
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  jt = _make_jt(
      {"host_roofline_peak": [], "intel_x86_uncore_imc_skx": []},
      {
          ("host_roofline_peak", "value", ("cpu_peak_fp64_flops_per_s",)): [
              ("n1.cluster", t0, 12_800_000_000_000.0),
          ],
          ("host_roofline_peak", "value", ("cpu_peak_dram_bw_bytes_per_s",)): [
              ("n1.cluster", t0, 1_099_511_627_776.0),
          ],
      },
  )
  gf, bw = infer_cpu_roofline_peak_flops_and_bw_gbps(jt)
  assert (gf, bw) == (12800.0, 1024.0)


def test_infer_cpu_roofline_partial_host_roofline_peak_falls_back_to_legacy():
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  jt = _make_jt(
      {"host_roofline_peak": [], "intel_x86_uncore_imc_hsw": []},
      {
          ("host_roofline_peak", "value", ("cpu_peak_fp64_flops_per_s",)): [
              ("n1.cluster", t0, 12_800_000_000_000.0),
          ],
      },
  )
  gf, bw = infer_cpu_roofline_peak_flops_and_bw_gbps(jt)
  assert (gf, bw) == ROOFLINE_CPU_PEAK_GFLOPS_AND_BW_GBPS["intel_x86_uncore_imc_hsw"]


def test_infer_gpu_roofline_prefers_io_link_over_mem_bw_when_both_exist():
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  jt = _make_jt(
      {"host_roofline_peak": []},
      {
          ("host_roofline_peak", "value", ("gpu_peak_fp64_flops_per_s",)): [
              ("n1.cluster", t0, 4_000_000_000_000.0),
          ],
          ("host_roofline_peak", "value", ("gpu_peak_io_link_bw_bytes_per_s",)): [
              ("n1.cluster", t0, 1_073_741_824_000.0),
          ],
          ("host_roofline_peak", "value", ("gpu_peak_mem_bw_bytes_per_s",)): [
              ("n1.cluster", t0, 2_147_483_648_000.0),
          ],
      },
  )
  gf, bw = infer_gpu_roofline_peak_flops_and_bw_gbps(jt)
  assert gf == pytest.approx(4000.0)
  assert bw == pytest.approx(1000.0)


def test_infer_gpu_roofline_uses_mem_bw_when_io_link_peak_missing():
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  jt = _make_jt(
      {"host_roofline_peak": []},
      {
          ("host_roofline_peak", "value", ("gpu_peak_fp64_flops_per_s",)): [
              ("n1.cluster", t0, 2_000_000_000_000.0),
          ],
          ("host_roofline_peak", "value", ("gpu_peak_mem_bw_bytes_per_s",)): [
              ("n1.cluster", t0, 536_870_912_000.0),
          ],
      },
  )
  gf, bw = infer_gpu_roofline_peak_flops_and_bw_gbps(jt)
  assert gf == pytest.approx(2000.0)
  assert bw == pytest.approx(500.0)


def test_infer_gpu_roofline_returns_none_for_partial_host_roofline_peak():
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  jt = _make_jt(
      {"host_roofline_peak": []},
      {
          ("host_roofline_peak", "value", ("gpu_peak_fp64_flops_per_s",)): [
              ("n1.cluster", t0, 2_000_000_000_000.0),
          ],
      },
  )
  assert infer_gpu_roofline_peak_flops_and_bw_gbps(jt) == (None, None)


def test_lookup_roofline_cpu_peaks_returns_row_or_none():
  assert lookup_roofline_cpu_peaks("intel_x86_uncore_imc_hsw") == (
      ROOFLINE_CPU_PEAK_GFLOPS_AND_BW_GBPS["intel_x86_uncore_imc_hsw"]
  )
  assert lookup_roofline_cpu_peaks("intel_hsw_imc") == (
      ROOFLINE_CPU_PEAK_GFLOPS_AND_BW_GBPS["intel_x86_uncore_imc_hsw"]
  )
  assert lookup_roofline_cpu_peaks("no_such_key") is None


def test_infer_gpu_roofline_returns_none_when_peak_bw_is_zero():
  """Zero max BW must not produce (gf, 0): log roofline cannot use ridge_ai = flops/0."""
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  jt = _make_jt(
      {"host_roofline_peak": []},
      {
          ("host_roofline_peak", "value", ("gpu_peak_fp64_flops_per_s",)): [
              ("n1.cluster", t0, 4_000_000_000_000.0),
          ],
          ("host_roofline_peak", "value", ("gpu_peak_io_link_bw_bytes_per_s",)): [
              ("n1.cluster", t0, 0.0),
          ],
          ("host_roofline_peak", "value", ("gpu_peak_mem_bw_bytes_per_s",)): [
              ("n1.cluster", t0, 0.0),
          ],
      },
  )
  assert infer_gpu_roofline_peak_flops_and_bw_gbps(jt) == (None, None)


def test_infer_gpu_roofline_returns_none_when_peak_flops_is_zero():
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  jt = _make_jt(
      {"host_roofline_peak": []},
      {
          ("host_roofline_peak", "value", ("gpu_peak_fp64_flops_per_s",)): [
              ("n1.cluster", t0, 0.0),
          ],
          ("host_roofline_peak", "value", ("gpu_peak_mem_bw_bytes_per_s",)): [
              ("n1.cluster", t0, 536_870_912_000.0),
          ],
      },
  )
  assert infer_gpu_roofline_peak_flops_and_bw_gbps(jt) == (None, None)


def test_infer_cpu_host_roofline_peak_zero_dram_and_no_hbm_returns_none():
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  jt = _make_jt(
      {"host_roofline_peak": []},
      {
          ("host_roofline_peak", "value", ("cpu_peak_fp64_flops_per_s",)): [
              ("n1.cluster", t0, 12_800_000_000_000.0),
          ],
          ("host_roofline_peak", "value", ("cpu_peak_dram_bw_bytes_per_s",)): [
              ("n1.cluster", t0, 0.0),
          ],
      },
  )
  assert infer_cpu_roofline_peak_flops_and_bw_gbps(jt) == (None, None)


def test_infer_cpu_roofline_spr_hbm_adds_ddr_and_hbm():
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  dram_bytes_per_s = 200_000_000_000.0
  hbm_bytes_per_s = 800_000_000_000.0
  jt = _make_jt(
      {"host_roofline_peak": []},
      {
          ("host_roofline_peak", "value", ("cpu_peak_fp64_flops_per_s",)): [
              ("n1.cluster", t0, 12_800_000_000_000.0),
          ],
          ("host_roofline_peak", "value", ("cpu_peak_dram_bw_bytes_per_s",)): [
              ("n1.cluster", t0, dram_bytes_per_s),
          ],
          ("host_roofline_peak", "value", ("cpu_peak_hbm_bw_bytes_per_s",)): [
              ("n1.cluster", t0, hbm_bytes_per_s),
          ],
      },
  )
  gf, bw = infer_cpu_roofline_peak_flops_and_bw_gbps(jt)
  assert gf == pytest.approx(12800.0)
  assert bw == pytest.approx((dram_bytes_per_s + hbm_bytes_per_s) / (1024 ** 3))


def test_infer_cpu_roofline_hbm_only_when_dram_zero():
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  hbm_bytes_per_s = 900_000_000_000.0
  jt = _make_jt(
      {"host_roofline_peak": []},
      {
          ("host_roofline_peak", "value", ("cpu_peak_fp64_flops_per_s",)): [
              ("n1.cluster", t0, 6_400_000_000_000.0),
          ],
          ("host_roofline_peak", "value", ("cpu_peak_dram_bw_bytes_per_s",)): [
              ("n1.cluster", t0, 0.0),
          ],
          ("host_roofline_peak", "value", ("cpu_peak_hbm_bw_bytes_per_s",)): [
              ("n1.cluster", t0, hbm_bytes_per_s),
          ],
      },
  )
  gf, bw = infer_cpu_roofline_peak_flops_and_bw_gbps(jt)
  assert gf == pytest.approx(6400.0)
  assert bw == pytest.approx(hbm_bytes_per_s / (1024 ** 3))
