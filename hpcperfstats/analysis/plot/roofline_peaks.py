"""Nominal CPU roofline peaks (GFLOP/s, GB/s) keyed by monitor ``host_data.type`` names.

These values are **order-of-magnitude theoretical peaks** for roofline visualization only.
They are **not** guaranteed to match any specific SKU, socket count, or turbo state.

**Method (high level)**

- **Intel:** Rows follow ``INTEL_IMC_STATS_TYPES`` (IMC typenames in host_data). Numbers
  target **typical dual-socket Xeon EP/SP-class** nodes for SNB→BDW; **single-socket**
  KNL; **dual-socket** scalable for ``intel_skx_imc`` (Skylake through Sapphire Rapids
  often share this monitor bucket—tune locally if needed).
- **AMD:** Monitor does not encode Zen generation in ``host_data.type``; see
  ``amd64_epyc_2s_default`` and named Zen1–Zen5 rows for documentation/overrides.
- **NVIDIA Grace:** Single-die vs Grace Superchip (two CPU dies) per NVIDIA public
  summaries.

**Sources (consult for definitions and updates)**

- NVIDIA Grace / Grace Superchip: NVIDIA Grace CPU / GH200 product and technical blog
  material (FP64 peak and LPDDR5X bandwidth).
- Intel Xeon generations: Intel Xeon Scalable family technical overviews; third-party
  aggregation (e.g. Microway knowledge-base SKU tables) for core × AVX/AVX-512 FMA
  rates and memory-channel bandwidth.
- AMD EPYC: AMD EPYC architecture white papers; memory-bandwidth analyses (e.g. DDR4
  channel scaling for Naples/Rome/Milan; DDR5 12-channel Genoa/Turin class).

If inference returns ``(None, None)``, :mod:`hpcperfstats.analysis.plot.roofline` keeps
using its built-in numeric defaults.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd

from hpcperfstats.analysis.gen.utils import INTEL_IMC_STATS_TYPES

# (peak_fp64_gflop_s, peak_dram_bw_gb_s) — see module docstring for interpretation.
ROOFLINE_CPU_PEAK_GFLOPS_AND_BW_GBPS: Dict[str, Tuple[float, float]] = {
    # --- Intel IMC typenames (aligned with INTEL_IMC_STATS_TYPES) -----------------
    # Dual-socket Xeon EP-class ballpark unless noted.
    "intel_snb_imc": (640.0, 85.0),
    "intel_ivb_imc": (900.0, 102.0),
    "intel_hsw_imc": (1400.0, 110.0),
    "intel_bdw_imc": (1800.0, 140.0),
    # Single-socket KNL, MCDRAM-oriented roof (flat segment uses FP peak; slope uses BW).
    "intel_knl_mc_dclk": (2700.0, 400.0),
    # Broad 2S Xeon Scalable class (SKX through many deployments; wide SKU spread).
    "intel_skx_imc": (6400.0, 460.0),
    # --- NVIDIA Grace (ARM) — not an IMC key; used when arm_imc or Grace-class ARM ----
    "nvidia_grace_cpu_chip": (7100.0, 500.0),
    "nvidia_grace_cpu_superchip": (14200.0, 1000.0),
    # --- AMD EPYC — documentation / optional override keys (2S node, Zen class) ------
    "amd64_epyc_2s_zen1_naples": (1800.0, 340.0),
    "amd64_epyc_2s_zen2_rome": (2800.0, 410.0),
    "amd64_epyc_2s_zen3_milan": (4000.0, 410.0),
    "amd64_epyc_2s_zen4_genoa": (8000.0, 920.0),
    "amd64_epyc_2s_zen5_turin": (11000.0, 1080.0),
    # Default when only amd64_pmc + amd64_df are known (middle-ground 2S EPYC).
    "amd64_epyc_2s_default": (4000.0, 410.0),
}


def _max_converted_sum_val(
    jt: Any,
    event: str,
    conv: float,
    *,
    type_name: str = "roofline_hw_peak",
) -> Optional[float]:
  """Return max converted value for one roofline_hw_peak event, or None when unavailable.

  Prefer ``value`` samples for gauge-like peak fields, then fall back to ``arc`` if needed.
  """
  for value_column in ("value", "arc"):
    try:
      df = jt.get_aggregate_df(type_name, value_column, [event], conv)
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


def _infer_cpu_roofline_peak_from_host_data(jt: Any) -> Tuple[Optional[float], Optional[float]]:
  """Infer CPU roofline peaks from ``roofline_hw_peak`` events when complete."""
  schema = getattr(jt, "schema", None)
  if not isinstance(schema, dict) or "roofline_hw_peak" not in schema:
    return (None, None)

  peak_flops_gf = _max_converted_sum_val(jt, "cpu_peak_fp64_flops_per_s", 1e-9)
  peak_bw_gb = _max_converted_sum_val(jt, "cpu_peak_dram_bw_bytes_per_s", 1 / (1024 ** 3))
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
  """Infer GPU roofline peaks from ``roofline_hw_peak`` when complete, else (None, None).

  Bandwidth preference matches the current GPU roofline x-axis semantics:
  use IO-link BW first, then memory BW as fallback.
  """
  schema = getattr(jt, "schema", None)
  if not isinstance(schema, dict) or "roofline_hw_peak" not in schema:
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
  """Return ``(peak_flops_gf, peak_bw_gb)`` from job schema when recognizable, else (None, None).

  Uses the same Intel IMC precedence as roofline bandwidth collection: first typename
  in ``INTEL_IMC_STATS_TYPES`` present in ``jt.schema``.
  """
  host_inferred = _infer_cpu_roofline_peak_from_host_data(jt)
  if host_inferred != (None, None):
    return host_inferred

  schema = getattr(jt, "schema", None)
  if not isinstance(schema, dict) or not schema:
    return (None, None)
  keys = set(schema.keys())

  for imc_typ in INTEL_IMC_STATS_TYPES:
    if imc_typ in keys:
      row = ROOFLINE_CPU_PEAK_GFLOPS_AND_BW_GBPS.get(imc_typ)
      if row is not None:
        return row

  if "amd64_pmc" in keys and "amd64_df" in keys:
    return ROOFLINE_CPU_PEAK_GFLOPS_AND_BW_GBPS["amd64_epyc_2s_default"]

  if "arm_imc" in keys:
    return ROOFLINE_CPU_PEAK_GFLOPS_AND_BW_GBPS["nvidia_grace_cpu_chip"]

  return (None, None)


def lookup_roofline_cpu_peaks(key: str) -> Optional[Tuple[float, float]]:
  """Return the configured (GFLOP/s, GB/s) tuple for *key*, or None if unknown."""
  return ROOFLINE_CPU_PEAK_GFLOPS_AND_BW_GBPS.get(key)
