"""Parity tests for the vectorized Summary node-power column."""

from __future__ import annotations

import math

import numpy as np
import pytest

from hpcperfstats.analysis.metrics.lib.columnar.series_frame import SeriesFrame
from hpcperfstats.analysis.metrics.lib.columnar.summary_series import (
  add_node_power_est_column,
)


def _frame(columns: dict[str, list[float]]) -> SeriesFrame:
  n = len(next(iter(columns.values()))) if columns else 0
  f = SeriesFrame.from_base(
    np.zeros(n, dtype=np.int64), np.arange(n, dtype=np.int64), ["h"]
  )
  for name, vals in columns.items():
    f.set(name, np.array(vals, dtype=np.float64))
  return f


def _loop_reference(columns: dict[str, list[float]]) -> np.ndarray:
  """Frozen per-row priority chain used as the numeric oracle."""
  n = len(next(iter(columns.values()))) if columns else 0
  out = np.full(n, np.nan, dtype=np.float64)
  for i in range(n):
    modv = columns.get("nv_module_power_w", [np.nan] * n)[i]
    if not math.isnan(modv) and modv > 0.0:
      out[i] = modv
      continue
    cpu = float("nan")
    for name in ("dcg_cpu_power_w", "watts", "amd_pkg_w"):
      if (
        name in columns and math.isnan(cpu) and not math.isnan(columns[name][i])
      ):
        cpu = columns[name][i]
    gpu = columns.get("nv_power_w", [np.nan] * n)[i]
    if math.isnan(cpu) and math.isnan(gpu):
      continue
    out[i] = (0.0 if math.isnan(cpu) else cpu) + (
      0.0 if math.isnan(gpu) else gpu
    )
  return out


@pytest.mark.machine_unit_mock
def test_add_node_power_est_column_matches_iloc_priority() -> None:
  columns = {
    "nv_module_power_w": [700.0, 0.0, np.nan, np.nan, np.nan, np.nan],
    "dcg_cpu_power_w": [np.nan, 45.0, np.nan, np.nan, np.nan, np.nan],
    "watts": [10.0, 11.0, 80.0, np.nan, np.nan, np.nan],
    "amd_pkg_w": [20.0, 21.0, 22.0, 90.0, np.nan, np.nan],
    "nv_power_w": [5.0, 6.0, 7.0, 8.0, 30.0, np.nan],
  }
  f = _frame(columns)
  add_node_power_est_column(f)
  np.testing.assert_allclose(
    f.columns["node_power_est_w"], _loop_reference(columns), equal_nan=True
  )


@pytest.mark.machine_unit_mock
def test_add_node_power_est_column_empty_and_missing_columns() -> None:
  empty = SeriesFrame.from_base(
    np.array([], dtype=np.int64), np.array([], dtype=np.int64), []
  )
  add_node_power_est_column(empty)
  assert empty.columns["node_power_est_w"].tolist() == []

  cpu_only = {"watts": [np.nan, 12.5]}
  f = _frame(cpu_only)
  add_node_power_est_column(f)
  np.testing.assert_allclose(
    f.columns["node_power_est_w"], _loop_reference(cpu_only), equal_nan=True
  )
