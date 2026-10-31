"""Tests for node_power_est helper (SummaryPlot merge rules on the columnar table)."""

import pandas as pd

from hpcperfstats.analysis.metrics.lib.columnar.testing import (
  table_from_records,
)
from hpcperfstats.analysis.metrics.lib.gen.node_power_est import (
  build_node_power_est_dataframe,
  max_node_power_est_w,
  mean_node_power_est_w,
)


def test_max_node_power_est_w_from_table():
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  t1 = pd.Timestamp("2024-06-01 12:01:00+00:00")
  c = 0.00001526
  table = table_from_records(
    [
      (t0, "h1", "intel_x86_rapl", "pkg_energy", None, None, 10.0, None),
      (t1, "h1", "intel_x86_rapl", "pkg_energy", None, None, 20.0, None),
    ],
    schema={
      "intel_x86_rapl": ["pkg_energy"],
      "amd_x86_rapl": ["pkg_energy"],
      "nvidia_gpu": ["power_usage", "module_power_usage"],
      "host_cpu_hw": ["dcgm_cpu_power_util_w"],
    },
  )
  frame = build_node_power_est_dataframe(table)
  assert "node_power_est_w" in frame.columns
  assert max_node_power_est_w(table) == 20.0 * c
  m = mean_node_power_est_w(table)
  assert m is not None
  assert abs(m - 15.0 * c) < 1e-18
