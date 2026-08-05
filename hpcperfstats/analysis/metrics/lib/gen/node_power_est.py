"""
Build per-(host, time) ``node_power_est_w`` using the same rules as SummaryPlot.
"""
from __future__ import annotations

from typing import Any

import numpy as np


def build_node_power_est_dataframe(jt: Any) -> Any:
  """
  Merge power components and return DataFrame with ``node_power_est_w`` (and.
  
    optional parts).
  
  Lazy-imports summaryplot helpers to avoid circular imports at module load.
  
  Args:
    jt (Any): Jt passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> build_node_power_est_dataframe(None)  # doctest: +SKIP
  """
  from hpcperfstats.analysis.metrics.lib.plot import summaryplot as sp

  df = jt.get_host_time_df()
  if df.empty:
    return df
  df = df.copy()
  rows = [
      ("intel_x86_rapl", "arc", ["pkg_energy"], "watts", 0.00001526),
      ("amd_x86_rapl", "arc", ["pkg_energy"], "amd_pkg_w", 0.00001526),
      ("nvidia_gpu", "value", ["power_usage"], "nv_power_w", 1.0),
      ("nvidia_gpu", "value", ["module_power_usage"], "nv_module_power_w", 1.0),
      (
          "host_cpu_hw",
          "value",
          ["dcgm_cpu_power_util_w"],
          "dcg_cpu_power_w",
          1.0,
      ),
  ]
  for typ, val, events, name, conv in rows:
    agg = sp._get_agg_if_feasible(jt, typ, val, list(events), conv)
    if agg.empty or "sum_val" not in agg.columns:
      df[name] = np.nan
    else:
      df = df.merge(
          agg[["host", "time", "sum_val"]],
          on=["host", "time"],
          how="left",
      )
      df[name] = df["sum_val"]
      df.drop(columns=["sum_val"], inplace=True)
  return sp._add_node_power_est_column(df)


def max_node_power_est_w(jt: Any) -> Any:
  """
  Peak ``node_power_est_w`` over all hosts and samples, or None.
  
  Args:
    jt (Any): Jt passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> max_node_power_est_w(None)  # doctest: +SKIP
  """
  df = build_node_power_est_dataframe(jt)
  if df.empty or "node_power_est_w" not in df.columns:
    return None
  s = df["node_power_est_w"]
  if not s.notna().any():
    return None
  return float(s.max())


def mean_node_power_est_w(jt: Any) -> Any:
  """
  Job-wide mean of ``node_power_est_w`` over samples where it is finite, or.
  
    None.
  
  Args:
    jt (Any): Jt passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> mean_node_power_est_w(None)  # doctest: +SKIP
  """
  df = build_node_power_est_dataframe(jt)
  if df.empty or "node_power_est_w" not in df.columns:
    return None
  s = df["node_power_est_w"].dropna()
  if s.empty:
    return None
  return float(s.mean())


def _has_cpu_power_fragments(df: Any) -> Any:
  """
  True when a CPU power column appears with finite samples.
  
  GPU power is optional: watt-hours integrate ``node_power_est_w``, which may
  include GPU when present. Module-only estimates without a CPU side fail this
  gate.
  
  Args:
    df (Any): Df passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _has_cpu_power_fragments(None)  # doctest: +SKIP
  """
  cpu_cols = [
      c for c in ("dcg_cpu_power_w", "watts", "amd_pkg_w") if c in df.columns
  ]
  if not cpu_cols:
    return False

  def _col_has_finite(col: Any) -> Any:
    """
    Internal helper to handle col has finite.
    
    Args:
      col (Any): Col passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _col_has_finite(None)  # doctest: +SKIP
    """
    s = df[col].dropna()
    if s.empty:
      return False
    return bool(np.isfinite(s.astype(float).to_numpy()).any())

  return bool(any(_col_has_finite(c) for c in cpu_cols))


def job_cpu_gpu_watt_hours(jt: Any) -> Any:
  """
  ∫ node_power_est_w dt per host (Wh), summed across hosts; None if CPU gate.
  
    fails.
  
  Requires finite CPU power fragments in the estimate dataframe (GPU optional;
  not module-only without a CPU side). Integrates watts × seconds / 3600.
  
  Args:
    jt (Any): Jt passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> job_cpu_gpu_watt_hours(None)  # doctest: +SKIP
  """
  import pandas as pd

  df = build_node_power_est_dataframe(jt)
  if df.empty or "node_power_est_w" not in df.columns:
    return None
  if not _has_cpu_power_fragments(df):
    return None

  total_wh = 0.0
  any_host = False
  for _host, g in df.groupby("host"):
    g = g.dropna(subset=["node_power_est_w"]).sort_values("time")
    if len(g) < 2:
      continue
    t = pd.to_datetime(g["time"])
    t_s = (t - t.iloc[0]).dt.total_seconds().to_numpy(dtype=np.float64)
    p = g["node_power_est_w"].to_numpy(dtype=np.float64)
    if not np.isfinite(p).any():
      continue
    trapz = getattr(np, "trapezoid", None) or np.trapz
    joules = float(trapz(p, t_s))
    if not np.isfinite(joules) or joules < 0:
      continue
    total_wh += joules / 3600.0
    any_host = True
  if not any_host:
    return None
  return float(total_wh)
