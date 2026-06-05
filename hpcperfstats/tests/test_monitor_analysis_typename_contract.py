"""Guard monitor `stats_type.st_name` vs analysis lists (monitor-analysis-architecture-sync)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from hpcperfstats.monitor_naming.canonical import (
    AMD_DF_TYPE,
    AMD_PMC_TYPE,
    ARM_IMC_STATS_TYPES,
    INTEL_CORE_PMC_TYPES_ORDERED,
    INTEL_IMC_STATS_TYPES,
)
from hpcperfstats.monitor_naming.legacy import INGEST_LEGACY_KNL_IMC_TYPE
from hpcperfstats.analysis.plot.roofline_peaks import ROOFLINE_CPU_PEAK_GFLOPS_AND_BW_GBPS

_ST_NAME_RE = re.compile(r'\.st_name\s*=\s*"([^"]+)"')


def _monitor_st_names_from_sources() -> set[str]:
  repo_root = Path(__file__).resolve().parents[2]
  monitor_src = repo_root / "monitor" / "src"
  if not monitor_src.is_dir():
    pytest.skip("monitor sources not present in this checkout")
  names: set[str] = set()
  for path in sorted(monitor_src.glob("*.c")):
    text = path.read_text(encoding="utf-8", errors="replace")
    names.update(_ST_NAME_RE.findall(text))
  return names


def test_intel_imc_stats_types_have_roofline_peak_rows():
  for typename in INTEL_IMC_STATS_TYPES:
    assert typename in ROOFLINE_CPU_PEAK_GFLOPS_AND_BW_GBPS, (
        f"Add ROOFLINE_CPU_PEAK_GFLOPS_AND_BW_GBPS row for {typename!r}."
    )


def test_monitor_st_names_cover_intel_core_pmc_types_ordered():
  monitor = _monitor_st_names_from_sources()
  for typename in INTEL_CORE_PMC_TYPES_ORDERED:
    assert typename in monitor, (
        f"{typename!r} in INTEL_CORE_PMC_TYPES_ORDERED must match monitor .st_name."
    )


def test_monitor_st_names_cover_arm_imc_stats_types():
  monitor = _monitor_st_names_from_sources()
  for typename in ARM_IMC_STATS_TYPES:
    assert typename in monitor, (
        f"{typename!r} in ARM_IMC_STATS_TYPES must match monitor .st_name."
    )


def test_monitor_st_names_cover_intel_imc_stats_types():
  monitor = _monitor_st_names_from_sources()
  for typename in INTEL_IMC_STATS_TYPES:
    assert typename in monitor, (
        f"{typename!r} in INTEL_IMC_STATS_TYPES must match monitor .st_name."
    )


def test_legacy_knl_imc_dclk_in_roofline_peaks_for_historical_host_data():
  """Dual-read: old DB rows may still use ingest-normalized KNL typename."""
  assert INGEST_LEGACY_KNL_IMC_TYPE not in INTEL_IMC_STATS_TYPES
  row = ROOFLINE_CPU_PEAK_GFLOPS_AND_BW_GBPS.get("intel_x86_uncore_mc_knl")
  assert row is not None


def test_monitor_st_names_cover_amd_roofline_prerequisites():
  monitor = _monitor_st_names_from_sources()
  for typename in (AMD_PMC_TYPE, AMD_DF_TYPE):
    assert typename in monitor, (
        f"{typename!r} must be emitted by monitor for AMD roofline prerequisites."
    )
