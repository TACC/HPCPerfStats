"""Build per-(host, time) ``node_power_est_w`` using the same rules as SummaryPlot."""

import numpy as np


def build_node_power_est_dataframe(jt):
  """Merge power components and return DataFrame with ``node_power_est_w`` (and optional parts).

  Lazy-imports summaryplot helpers to avoid circular imports at module load.
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


def max_node_power_est_w(jt):
  """Peak ``node_power_est_w`` over all hosts and samples, or None."""
  df = build_node_power_est_dataframe(jt)
  if df.empty or "node_power_est_w" not in df.columns:
    return None
  s = df["node_power_est_w"]
  if not s.notna().any():
    return None
  return float(s.max())


def mean_node_power_est_w(jt):
  """Job-wide mean of ``node_power_est_w`` over samples where it is finite, or None."""
  df = build_node_power_est_dataframe(jt)
  if df.empty or "node_power_est_w" not in df.columns:
    return None
  s = df["node_power_est_w"].dropna()
  if s.empty:
    return None
  return float(s.mean())
