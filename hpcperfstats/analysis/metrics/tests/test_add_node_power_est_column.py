"""Parity tests for vectorized Summary node-power column."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
from pandas import isna as pd_isna

from hpcperfstats.analysis.metrics.lib.plot.summaryplot import (
    _add_node_power_est_column,
)


def _add_node_power_est_column_loop_reference(df: pd.DataFrame) -> pd.DataFrame:
  """Frozen iloc priority chain used as the numeric oracle."""
  n = len(df.index)
  out = np.full(n, np.nan, dtype=np.float64)
  has_mod = "nv_module_power_w" in df.columns
  has_gpu = "nv_power_w" in df.columns
  dcg = df["dcg_cpu_power_w"] if "dcg_cpu_power_w" in df.columns else None
  intel = df["watts"] if "watts" in df.columns else None
  amd = df["amd_pkg_w"] if "amd_pkg_w" in df.columns else None
  for i in range(n):
    if has_mod:
      modv = df["nv_module_power_w"].iloc[i]
      if not pd_isna(modv) and float(modv) > 0.0:
        out[i] = float(modv)
        continue
    cpu = float("nan")
    if dcg is not None:
      v = dcg.iloc[i]
      if not pd_isna(v):
        cpu = float(v)
    if math.isnan(cpu) and intel is not None:
      v = intel.iloc[i]
      if not pd_isna(v):
        cpu = float(v)
    if math.isnan(cpu) and amd is not None:
      v = amd.iloc[i]
      if not pd_isna(v):
        cpu = float(v)
    gpu = float("nan")
    if has_gpu:
      gv = df["nv_power_w"].iloc[i]
      if not pd_isna(gv):
        gpu = float(gv)
    if math.isnan(cpu) and math.isnan(gpu):
      continue
    total = 0.0
    if math.isfinite(cpu):
      total += cpu
    if math.isfinite(gpu):
      total += gpu
    if math.isfinite(cpu) or math.isfinite(gpu):
      out[i] = total
  df = df.copy()
  df["node_power_est_w"] = out
  return df


@pytest.mark.machine_unit_mock
def test_add_node_power_est_column_matches_iloc_priority() -> None:
  df = pd.DataFrame(
      {
          "nv_module_power_w": [700.0, 0.0, np.nan, np.nan, np.nan, np.nan],
          "dcg_cpu_power_w": [np.nan, 45.0, np.nan, np.nan, np.nan, np.nan],
          "watts": [10.0, 11.0, 80.0, np.nan, np.nan, np.nan],
          "amd_pkg_w": [20.0, 21.0, 22.0, 90.0, np.nan, np.nan],
          "nv_power_w": [5.0, 6.0, 7.0, 8.0, 30.0, np.nan],
      }
  )
  expected = _add_node_power_est_column_loop_reference(df.copy())
  got = _add_node_power_est_column(df.copy())
  pd.testing.assert_series_equal(
      got["node_power_est_w"],
      expected["node_power_est_w"],
      check_names=False,
  )


@pytest.mark.machine_unit_mock
def test_add_node_power_est_column_empty_and_missing_columns() -> None:
  empty = pd.DataFrame()
  got_empty = _add_node_power_est_column(empty.copy())
  assert list(got_empty["node_power_est_w"]) == []

  cpu_only = pd.DataFrame({"watts": [np.nan, 12.5]})
  expected = _add_node_power_est_column_loop_reference(cpu_only.copy())
  got = _add_node_power_est_column(cpu_only.copy())
  pd.testing.assert_series_equal(
      got["node_power_est_w"],
      expected["node_power_est_w"],
      check_names=False,
  )
