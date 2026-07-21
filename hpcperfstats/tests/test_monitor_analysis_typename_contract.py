"""Guard monitor `stats_type.st_name` vs analysis lists (monitor-analysis-architecture-sync)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from hpcperfstats.dbload.lib.monitor_naming.canonical import (
    AMD_DF_STATS_TYPES,
    AMD_DF_TYPE,
    AMD_PMC_TYPE,
    AMD_RAPL_STATS_TYPES,
    ARM_IMC_STATS_TYPES,
    INTEL_CORE_PMC_TYPES_ORDERED,
    INTEL_IMC_STATS_TYPES,
    INTEL_RAPL_STATS_TYPES,
)
from hpcperfstats.dbload.lib.monitor_naming.legacy import (
    INGEST_LEGACY_KNL_IMC_TYPE,
    LEGACY_INTEL_IMC_STATS_TYPES,
    MONITOR_LEGACY_KNL_IMC_TYPE,
)
from hpcperfstats.dbload.lib.monitor_naming.resolve import (
    amd_df_types_probe_order,
    imc_types_probe_order,
    rapl_types_probe_order,
)
from hpcperfstats.analysis.metrics.lib.plot.roofline_peaks import ROOFLINE_CPU_PEAK_GFLOPS_AND_BW_GBPS

_RETIRED_KNL_CANONICAL_TYPES = frozenset({
    "intel_x86_pmc_knl",
    "intel_x86_uncore_mc_knl",
    "intel_x86_uncore_edc_knl",
    "intel_x86_uncore_cha_knl",
})

_ST_NAME_RE = re.compile(r'\.st_name\s*=\s*"([^"]+)"')
_ST_NAME_DEFINE_RE = re.compile(r'#define\s+\w+_ST_NAME\s+"([^"]+)"')


def _monitor_st_names_from_sources() -> set[str]:
  repo_root = Path(__file__).resolve().parents[2]
  monitor_src = repo_root / "monitor" / "src"
  if not monitor_src.is_dir():
    pytest.skip("monitor sources not present in this checkout")
  names: set[str] = set()
  for path in sorted(monitor_src.glob("*.c")):
    text = path.read_text(encoding="utf-8", errors="replace")
    names.update(_ST_NAME_RE.findall(text))
  for path in sorted(monitor_src.glob("*.h")):
    text = path.read_text(encoding="utf-8", errors="replace")
    names.update(_ST_NAME_DEFINE_RE.findall(text))
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


def test_knl_retired_from_canonical_lists_but_legacy_imc_remains():
  """KNL no longer in canonical analysis lists; legacy IMC typenames stay for dual-read."""
  for retired in _RETIRED_KNL_CANONICAL_TYPES:
    assert retired not in INTEL_IMC_STATS_TYPES
    assert retired not in INTEL_CORE_PMC_TYPES_ORDERED
    assert retired not in ROOFLINE_CPU_PEAK_GFLOPS_AND_BW_GBPS
  assert INGEST_LEGACY_KNL_IMC_TYPE in LEGACY_INTEL_IMC_STATS_TYPES
  assert MONITOR_LEGACY_KNL_IMC_TYPE in LEGACY_INTEL_IMC_STATS_TYPES


def test_monitor_st_names_cover_amd_df_family_types():
  """Live AMD DF is family-scoped; bare amd_x86_uncore_df is not emitted."""
  monitor = _monitor_st_names_from_sources()
  for typename in AMD_DF_STATS_TYPES:
    assert typename in monitor, (
        f"{typename!r} in AMD_DF_STATS_TYPES must match monitor .st_name."
    )
  assert AMD_DF_TYPE not in monitor
  assert AMD_PMC_TYPE not in monitor
  for typename in AMD_DF_STATS_TYPES:
    assert typename in amd_df_types_probe_order()


def test_monitor_st_names_cover_rapl_types():
  monitor = _monitor_st_names_from_sources()
  for typename in INTEL_RAPL_STATS_TYPES + AMD_RAPL_STATS_TYPES:
    assert typename in monitor, (
        f"{typename!r} RAPL type must match monitor .st_name."
    )
    assert typename in rapl_types_probe_order()


def test_legacy_imc_short_forms_include_icx_spr():
  assert "intel_icx_imc" in LEGACY_INTEL_IMC_STATS_TYPES
  assert "intel_spr_imc" in LEGACY_INTEL_IMC_STATS_TYPES
  order = imc_types_probe_order()
  assert "intel_icx_imc" in order
  assert "intel_spr_imc" in order


def test_ib_merged_to_host_ib_retired_separate_collectors():
  """IB ext/sw drivers merged into unified host_ib (monitor IB driver merge)."""
  monitor = _monitor_st_names_from_sources()
  assert "host_ib" in monitor
  assert "host_ib_ext" not in monitor
  assert "host_ib_sw" not in monitor
