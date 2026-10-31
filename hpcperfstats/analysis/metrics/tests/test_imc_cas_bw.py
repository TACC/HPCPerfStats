"""Unit tests for Intel IMC DDR+HBM CAS bandwidth combine helpers."""

from __future__ import annotations

import numpy as np

from hpcperfstats.analysis.metrics.lib.columnar.kernels import AggTable
from hpcperfstats.analysis.metrics.lib.columnar.summary_series import (
  bw_table,
  combine_bw,
)
from hpcperfstats.analysis.metrics.lib.gen.imc_cas_bw import (
  combine_cas_bw_scalars,
)


def test_combine_cas_bw_scalars_dram_only():
  assert combine_cas_bw_scalars(1.5, None) == 1.5
  assert combine_cas_bw_scalars(1.5, float("nan")) == 1.5


def test_combine_cas_bw_scalars_hbm_only():
  assert combine_cas_bw_scalars(None, 2.25) == 2.25


def test_combine_cas_bw_scalars_sums_both():
  assert abs(combine_cas_bw_scalars(1.0, 2.0) - 3.0) < 1e-12


def test_combine_cas_bw_scalars_neither():
  assert combine_cas_bw_scalars(None, None) is None
  assert combine_cas_bw_scalars(float("nan"), float("inf")) is None


def _bw(value, time_us=1):
  return AggTable(np.array([0]), np.array([time_us]), np.array([value]))


def test_combine_bw_dram_only():
  out = combine_bw(_bw(4.0), None)
  assert out is not None
  assert out.sum_val.tolist() == [4.0]


def test_combine_bw_hbm_only():
  out = combine_bw(None, _bw(5.0))
  assert out is not None
  assert out.sum_val.tolist() == [5.0]


def test_combine_bw_sums_both_on_outer_join():
  out = combine_bw(_bw(1.5), _bw(2.5))
  assert out is not None
  assert abs(float(out.sum_val[0]) - 4.0) < 1e-12
  # Disjoint sample times: outer join keeps both rows, missing side counts 0.
  out2 = combine_bw(_bw(1.0, time_us=1), _bw(2.0, time_us=2))
  assert out2.time_us.tolist() == [1, 2]
  assert out2.sum_val.tolist() == [1.0, 2.0]


def test_combine_bw_rejects_all_non_finite():
  bad = _bw(np.nan)
  assert combine_bw(bad, None) is None
  assert combine_bw(None, bad) is None
  assert combine_bw(None, None) is None


def test_bw_table_drops_empty_or_non_finite():
  assert bw_table(_bw(3.0)) is not None
  assert bw_table(AggTable.zero()) is None
  assert bw_table(_bw(np.nan)) is None
