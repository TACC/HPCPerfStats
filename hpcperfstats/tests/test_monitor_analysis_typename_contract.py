"""Guard monitor `stats_type.st_name` vs analysis lists (monitor-analysis-architecture-sync)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from hpcperfstats.analysis.gen.utils import (
    ARM_IMC_STATS_TYPES,
    INTEL_CORE_PMC_TYPES_ORDERED,
    INTEL_IMC_STATS_TYPES,
)
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
        f"Add ROOFLINE_CPU_PEAK_GFLOPS_AND_BW_GBPS row for {typename!r} "
        "(same string as job schema / INTEL_IMC_STATS_TYPES order)."
    )


def test_monitor_st_names_cover_intel_core_pmc_types_ordered():
  monitor = _monitor_st_names_from_sources()
  for typename in INTEL_CORE_PMC_TYPES_ORDERED:
    assert typename in monitor, (
        f"{typename!r} in INTEL_CORE_PMC_TYPES_ORDERED must match a monitor "
        f"stats_type.st_name (found {len(monitor)} monitor types)."
    )


def test_monitor_st_names_cover_arm_imc_stats_types():
  monitor = _monitor_st_names_from_sources()
  for typename in ARM_IMC_STATS_TYPES:
    assert typename in monitor, (
        f"{typename!r} in ARM_IMC_STATS_TYPES must match monitor .st_name."
    )


def test_intel_imc_stats_types_match_monitor_or_documented_ingest_alias():
  """KNL DRAM uses monitor `intel_knl_mc`; dbload emits `intel_knl_mc_dclk` (see utils.py)."""
  monitor = _monitor_st_names_from_sources()
  for typename in INTEL_IMC_STATS_TYPES:
    if typename == "intel_knl_mc_dclk":
      assert "intel_knl_mc" in monitor
      continue
    assert typename in monitor, (
        f"{typename!r} in INTEL_IMC_STATS_TYPES must match monitor .st_name "
        f"(or be documented ingest alias of a monitor type)."
    )
