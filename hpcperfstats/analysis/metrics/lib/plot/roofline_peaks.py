"""Nominal CPU roofline peaks (GFLOP/s, GB/s) keyed by monitor ``host_data.type`` names.

These values are **order-of-magnitude theoretical peaks** for roofline visualization only.
They are **not** guaranteed to match any specific SKU, socket count, or turbo state.

**Method (high level)**

- **Intel:** Rows follow canonical IMC typenames in host_data. Numbers target **typical
  dual-socket** scalable for ``intel_x86_uncore_imc_skx`` / ``icx`` / ``spr``; retired
  SNB→BDW canonical names remain for historical ``host_data`` via legacy probe order.
- **AMD:** Monitor does not encode Zen generation in ``host_data.type``; see
  ``amd64_epyc_2s_default`` and named Zen1–Zen5 rows for documentation/overrides.
- **NVIDIA Grace:** Single-die vs Grace Superchip (two CPU dies) per NVIDIA public
  summaries.

If inference returns ``(None, None)``, :mod:`hpcperfstats.analysis.metrics.lib.plot.roofline` keeps
using its built-in numeric defaults.

When ``host_roofline_peak`` is present, CPU memory roof bandwidth sums
``cpu_peak_dram_bw_bytes_per_s`` (DDR) and ``cpu_peak_hbm_bw_bytes_per_s`` (HBM when > 0).
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd

from hpcperfstats.dbload.lib.monitor_naming.canonical import HOST_ROOFLINE_PEAK_TYPE
from hpcperfstats.dbload.lib.monitor_naming.resolve import (
    amd_df_type_names,
    amd_pmc_type_names,
    arm_imc_types_probe_order,
    canonical_type_name,
    host_roofline_peak_type_names,
    imc_types_probe_order,
)

# (peak_fp64_gflop_s, peak_dram_bw_gb_s) — keyed by canonical IMC st_name.
ROOFLINE_CPU_PEAK_GFLOPS_AND_BW_GBPS: Dict[str, Tuple[float, float]] = {
    "intel_x86_uncore_imc_snb": (640.0, 85.0),
    "intel_x86_uncore_imc_ivb": (900.0, 102.0),
    "intel_x86_uncore_imc_hsw": (1400.0, 110.0),
    "intel_x86_uncore_imc_bdw": (1800.0, 140.0),
    "intel_x86_uncore_imc_skx": (6400.0, 460.0),
    "intel_x86_uncore_imc_icx": (6400.0, 480.0),
    "intel_x86_uncore_imc_spr": (8400.0, 550.0),
    "nvidia_grace_cpu_chip": (7100.0, 500.0),
    "nvidia_grace_cpu_superchip": (14200.0, 1000.0),
    "amd64_epyc_2s_zen1_naples": (1800.0, 340.0),
    "amd64_epyc_2s_zen2_rome": (2800.0, 410.0),
    "amd64_epyc_2s_zen3_milan": (4000.0, 410.0),
    "amd64_epyc_2s_zen4_genoa": (8000.0, 920.0),
    "amd64_epyc_2s_zen5_turin": (11000.0, 1080.0),
    "amd64_epyc_2s_default": (4000.0, 410.0),
}


def _max_converted_sum_val(
    jt: Any,
    event: str,
    conv: float,
    *,
    type_name: str = HOST_ROOFLINE_PEAK_TYPE,
) -> Optional[float]:
  for value_column in ("value", "arc"):
    for peak_typ in host_roofline_peak_type_names():
      try:
        df = jt.get_aggregate_df(peak_typ, value_column, [event], conv)
      except Exception:
        continue
      if df is None or df.empty or "sum_val" not in df.columns:
        continue
      values = pd.to_numeric(df["sum_val"], errors="coerce").to_numpy(dtype=float, copy=False)
      if values.size == 0:
        continue
      finite = values[np.isfinite(values)]
      if finite.size == 0:
        continue
      return float(np.max(finite))
  return None


_BYTES_TO_GB = 1 / (1024 ** 3)


def _cpu_peak_memory_bw_gb_from_host_data(jt: Any) -> Optional[float]:
  """DDR + HBM peak bytes/s from host_roofline_peak (HBM omitted when absent or zero)."""
  peak_dram_gb = _max_converted_sum_val(
      jt, "cpu_peak_dram_bw_bytes_per_s", _BYTES_TO_GB
  )
  peak_hbm_gb = _max_converted_sum_val(
      jt, "cpu_peak_hbm_bw_bytes_per_s", _BYTES_TO_GB
  )
  if peak_dram_gb is None and peak_hbm_gb is None:
    return None
  dram = peak_dram_gb if peak_dram_gb is not None and peak_dram_gb > 0 else 0.0
  hbm = peak_hbm_gb if peak_hbm_gb is not None and peak_hbm_gb > 0 else 0.0
  total = dram + hbm
  if not (np.isfinite(total) and total > 0):
    return None
  return float(total)


def _infer_cpu_roofline_peak_from_host_data(jt: Any) -> Tuple[Optional[float], Optional[float]]:
  schema = getattr(jt, "schema", None)
  if not isinstance(schema, dict):
    return (None, None)
  if not any(t in schema for t in host_roofline_peak_type_names()):
    return (None, None)

  peak_flops_gf = _max_converted_sum_val(jt, "cpu_peak_fp64_flops_per_s", 1e-9)
  peak_bw_gb = _cpu_peak_memory_bw_gb_from_host_data(jt)
  if peak_flops_gf is None or peak_bw_gb is None:
    return (None, None)
  if not (
      np.isfinite(peak_flops_gf)
      and peak_flops_gf > 0
      and np.isfinite(peak_bw_gb)
      and peak_bw_gb > 0
  ):
    return (None, None)
  return (peak_flops_gf, peak_bw_gb)


def infer_gpu_roofline_peak_flops_and_bw_gbps(jt: Any) -> Tuple[Optional[float], Optional[float]]:
  schema = getattr(jt, "schema", None)
  if not isinstance(schema, dict):
    return (None, None)
  if not any(t in schema for t in host_roofline_peak_type_names()):
    return (None, None)

  peak_flops_gf = _max_converted_sum_val(jt, "gpu_peak_fp64_flops_per_s", 1e-9)
  peak_bw_gb = _max_converted_sum_val(jt, "gpu_peak_io_link_bw_bytes_per_s", 1 / (1024 ** 3))
  if peak_bw_gb is None:
    peak_bw_gb = _max_converted_sum_val(jt, "gpu_peak_mem_bw_bytes_per_s", 1 / (1024 ** 3))
  if peak_flops_gf is None or peak_bw_gb is None:
    return (None, None)
  if not (
      np.isfinite(peak_flops_gf)
      and peak_flops_gf > 0
      and np.isfinite(peak_bw_gb)
      and peak_bw_gb > 0
  ):
    return (None, None)
  return (peak_flops_gf, peak_bw_gb)


def infer_cpu_roofline_peak_flops_and_bw_gbps(jt: Any) -> Tuple[Optional[float], Optional[float]]:
  host_inferred = _infer_cpu_roofline_peak_from_host_data(jt)
  if host_inferred != (None, None):
    return host_inferred

  schema = getattr(jt, "schema", None)
  if not isinstance(schema, dict) or not schema:
    return (None, None)
  keys = set(schema.keys())

  for imc_typ in imc_types_probe_order():
    if imc_typ in keys:
      row = ROOFLINE_CPU_PEAK_GFLOPS_AND_BW_GBPS.get(canonical_type_name(imc_typ))
      if row is not None:
        return row

  amd_pmc, amd_df = amd_pmc_type_names(), amd_df_type_names()
  if any(t in keys for t in amd_pmc) and any(t in keys for t in amd_df):
    return ROOFLINE_CPU_PEAK_GFLOPS_AND_BW_GBPS["amd64_epyc_2s_default"]

  for arm_typ in arm_imc_types_probe_order():
    if arm_typ in keys:
      return ROOFLINE_CPU_PEAK_GFLOPS_AND_BW_GBPS["nvidia_grace_cpu_chip"]

  return (None, None)


def lookup_roofline_cpu_peaks(key: str) -> Optional[Tuple[float, float]]:
  return ROOFLINE_CPU_PEAK_GFLOPS_AND_BW_GBPS.get(canonical_type_name(key))
