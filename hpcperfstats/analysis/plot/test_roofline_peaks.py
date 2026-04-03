"""Unit tests for schema-based roofline peak inference."""
from unittest.mock import MagicMock

from hpcperfstats.analysis.plot.roofline_peaks import (
  ROOFLINE_CPU_PEAK_GFLOPS_AND_BW_GBPS,
  infer_cpu_roofline_peak_flops_and_bw_gbps,
  lookup_roofline_cpu_peaks,
)


def test_infer_roofline_peaks_intel_skx_when_only_skx_in_schema():
  jt = MagicMock()
  jt.schema = {"intel_skx_imc": []}
  gf, bw = infer_cpu_roofline_peak_flops_and_bw_gbps(jt)
  assert (gf, bw) == ROOFLINE_CPU_PEAK_GFLOPS_AND_BW_GBPS["intel_skx_imc"]


def test_infer_roofline_peaks_intel_prefers_earlier_imc_generation_in_list():
  """First INTEL_IMC_STATS_TYPES present in schema wins (same order as roofline IMC scan)."""
  jt = MagicMock()
  jt.schema = {"intel_hsw_imc": [], "intel_skx_imc": []}
  gf, bw = infer_cpu_roofline_peak_flops_and_bw_gbps(jt)
  assert (gf, bw) == ROOFLINE_CPU_PEAK_GFLOPS_AND_BW_GBPS["intel_hsw_imc"]


def test_infer_roofline_peaks_amd_default_when_amd_counters_present():
  jt = MagicMock()
  jt.schema = {"amd64_pmc": [], "amd64_df": []}
  gf, bw = infer_cpu_roofline_peak_flops_and_bw_gbps(jt)
  assert (gf, bw) == ROOFLINE_CPU_PEAK_GFLOPS_AND_BW_GBPS["amd64_epyc_2s_default"]


def test_infer_roofline_peaks_grace_when_arm_imc_present():
  jt = MagicMock()
  jt.schema = {"arm_imc": [], "cpu_counter_metrics": []}
  gf, bw = infer_cpu_roofline_peak_flops_and_bw_gbps(jt)
  assert (gf, bw) == ROOFLINE_CPU_PEAK_GFLOPS_AND_BW_GBPS["nvidia_grace_cpu_chip"]


def test_infer_roofline_peaks_none_for_empty_or_nondict_schema():
  jt = MagicMock()
  assert infer_cpu_roofline_peak_flops_and_bw_gbps(jt) == (None, None)
  jt.schema = {}
  assert infer_cpu_roofline_peak_flops_and_bw_gbps(jt) == (None, None)


def test_lookup_roofline_cpu_peaks_returns_row_or_none():
  assert lookup_roofline_cpu_peaks("intel_hsw_imc") == (
      ROOFLINE_CPU_PEAK_GFLOPS_AND_BW_GBPS["intel_hsw_imc"]
  )
  assert lookup_roofline_cpu_peaks("no_such_key") is None
