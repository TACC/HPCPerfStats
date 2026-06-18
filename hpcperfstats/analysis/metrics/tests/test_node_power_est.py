"""Tests for node_power_est helper (uses SummaryPlot merge rules)."""

import pandas as pd
from unittest.mock import MagicMock

from hpcperfstats.analysis.metrics.lib.gen.node_power_est import (
    build_node_power_est_dataframe,
    max_node_power_est_w,
    mean_node_power_est_w,
)


def test_max_node_power_est_w_from_jt_mock():
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  base = pd.DataFrame([("h1", t0), ("h1", pd.Timestamp("2024-06-01 12:01:00+00:00"))],
                      columns=["host", "time"])

  def get_aggregate_df(typ, val_col, events, conv=1.0):
    ev = list(events)
    rapl_types = ("intel_x86_rapl", "intel_rapl")
    pkg_hits = {"pkg_energy", "MSR_PKG_ENERGY_STATUS", "MSR_PKG_ENERGY_STAT"}
    if typ in rapl_types and pkg_hits.intersection(ev):
      c = 0.00001526
      assert abs(conv - c) < 1e-12
      return pd.DataFrame(
          [
              ("h1", base.iloc[0]["time"], 10.0 * c),
              ("h1", base.iloc[1]["time"], 20.0 * c),
          ],
          columns=["host", "time", "sum_val"],
      )
    return pd.DataFrame(columns=["host", "time", "sum_val"])

  jt = MagicMock()
  jt.schema = {
      "intel_x86_rapl": ["pkg_energy"],
      "amd_x86_rapl": ["pkg_energy"],
      "nvidia_gpu": ["power_usage", "module_power_usage"],
      "host_cpu_hw": ["dcgm_cpu_power_util_w"],
  }
  jt.get_host_time_df.return_value = base
  jt.get_aggregate_df.side_effect = get_aggregate_df

  df = build_node_power_est_dataframe(jt)
  assert "node_power_est_w" in df.columns
  assert max_node_power_est_w(jt) == 20.0 * 0.00001526
  m = mean_node_power_est_w(jt)
  assert m is not None
  assert abs(m - 15.0 * 0.00001526) < 1e-18
