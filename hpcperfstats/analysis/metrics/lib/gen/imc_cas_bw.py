"""
Combine Intel IMC DDR (dram_cas_*) and SPR HBM (hbm_cas_*) measured BW series.

Same 64 B/CAS conversion is applied by callers before passing frames/scalars
here. Absent or all-non-finite sides are ignored; when both sides are usable
they are summed.
"""
from __future__ import annotations

from typing import Any, Optional

import numpy as np
import pandas as pd


def _scalar_usable(value: Any) -> bool:
  """
  Internal helper to check if the scalar is usable.
  
  Args:
    value (Any): Value to inspect (typically a numeric scalar).
  
  Returns:
    bool: True or False for this check.
  
  Examples:
    >>> _scalar_usable(None)  # doctest: +SKIP
  """
  if value is None:
    return False
  try:
    return bool(np.isfinite(float(value)))
  except (TypeError, ValueError):
    return False


def _frame_usable(df: Optional[pd.DataFrame]) -> bool:
  """
  Internal helper to check if the DataFrame is usable.
  
  Args:
    df (Optional[pd.DataFrame]): DataFrame to inspect, or None when absent.
  
  Returns:
    bool: True or False for this check.
  
  Examples:
    >>> _frame_usable(None)  # doctest: +SKIP
  """
  if df is None or df.empty or "bw_gb" not in df.columns:
    return False
  vals = pd.to_numeric(df["bw_gb"], errors="coerce").to_numpy(dtype=np.float64, copy=False)
  return bool(np.isfinite(vals).any())


def combine_cas_bw_scalars(dram_v: Any, hbm_v: Any) -> Optional[float]:
  """
  Sum usable DDR and HBM scalar BW (GB/s); None if neither side is usable.
  
  Args:
    dram_v (Any): DDR (DRAM) CAS bandwidth value, or something coercible to
    float.
    hbm_v (Any): HBM CAS bandwidth value, or something coercible to float.
  
  Returns:
    Optional[float]: Optional[float] — the result, or None when unavailable.
  
  Examples:
    >>> combine_cas_bw_scalars(None, None)  # doctest: +SKIP
  """
  d_ok = _scalar_usable(dram_v)
  h_ok = _scalar_usable(hbm_v)
  if d_ok and h_ok:
    return float(dram_v) + float(hbm_v)
  if d_ok:
    return float(dram_v)
  if h_ok:
    return float(hbm_v)
  return None


def combine_cas_bw_frames(
  dram_df: Optional[pd.DataFrame],
  hbm_df: Optional[pd.DataFrame],
) -> Optional[pd.DataFrame]:
  """
  Outer-join host/time BW frames and sum; return None if neither side is usable.
  
  Args:
    dram_df (Optional[pd.DataFrame]): DataFrame to inspect, or None when
    absent.
    hbm_df (Optional[pd.DataFrame]): DataFrame to inspect, or None when
    absent.
  
  Returns:
    Optional[pd.DataFrame]: Optional[pd.DataFrame] — the result, or None when
    unavailable.
  
  Examples:
    >>> combine_cas_bw_frames(None, None)  # doctest: +SKIP
  """
  d_ok = _frame_usable(dram_df)
  h_ok = _frame_usable(hbm_df)
  if d_ok and h_ok:
    left = dram_df[["host", "time", "bw_gb"]].rename(columns={"bw_gb": "bw_dram"})
    right = hbm_df[["host", "time", "bw_gb"]].rename(columns={"bw_gb": "bw_hbm"})
    merged = left.merge(right, on=["host", "time"], how="outer")
    d = pd.to_numeric(merged["bw_dram"], errors="coerce")
    h = pd.to_numeric(merged["bw_hbm"], errors="coerce")
    merged["bw_gb"] = d.fillna(0.0) + h.fillna(0.0)
    out = merged[["host", "time", "bw_gb"]]
    if not _frame_usable(out):
      return None
    return out.reset_index(drop=True)
  if d_ok:
    return dram_df[["host", "time", "bw_gb"]].copy().reset_index(drop=True)
  if h_ok:
    return hbm_df[["host", "time", "bw_gb"]].copy().reset_index(drop=True)
  return None


def agg_sum_val_to_bw_frame(
  agg: Optional[pd.DataFrame],
) -> Optional[pd.DataFrame]:
  """
  Convert aggregate ``sum_val`` frame to ``bw_gb``; drop when empty or all non-.
  
    finite.
  
  Args:
    agg (Optional[pd.DataFrame]): DataFrame to inspect, or None when absent.
  
  Returns:
    Optional[pd.DataFrame]: Optional[pd.DataFrame] — the result, or None when
    unavailable.
  
  Examples:
    >>> agg_sum_val_to_bw_frame(None)  # doctest: +SKIP
  """
  if agg is None or agg.empty or "sum_val" not in agg.columns:
    return None
  out = agg.rename(columns={"sum_val": "bw_gb"})[["host", "time", "bw_gb"]].copy()
  if not _frame_usable(out):
    return None
  return out.reset_index(drop=True)
