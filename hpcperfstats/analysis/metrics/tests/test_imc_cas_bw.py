"""Unit tests for Intel IMC DDR+HBM CAS bandwidth combine helpers."""
from __future__ import annotations

import numpy as np
import pandas as pd

from hpcperfstats.analysis.metrics.lib.gen.imc_cas_bw import (
    agg_sum_val_to_bw_frame,
    combine_cas_bw_frames,
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


def _bw_frame(host, t0, value):
  return pd.DataFrame(
      [(host, t0, value)],
      columns=["host", "time", "bw_gb"],
  )


def test_combine_cas_bw_frames_dram_only():
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  out = combine_cas_bw_frames(_bw_frame("n1", t0, 4.0), None)
  assert out is not None
  assert list(out["bw_gb"]) == [4.0]


def test_combine_cas_bw_frames_hbm_only():
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  out = combine_cas_bw_frames(None, _bw_frame("n1", t0, 5.0))
  assert out is not None
  assert list(out["bw_gb"]) == [5.0]


def test_combine_cas_bw_frames_sums_both():
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  out = combine_cas_bw_frames(
      _bw_frame("n1", t0, 1.5),
      _bw_frame("n1", t0, 2.5),
  )
  assert out is not None
  assert abs(float(out["bw_gb"].iloc[0]) - 4.0) < 1e-12


def test_combine_cas_bw_frames_rejects_all_non_finite():
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  bad = _bw_frame("n1", t0, np.nan)
  assert combine_cas_bw_frames(bad, None) is None
  assert combine_cas_bw_frames(None, bad) is None
  assert combine_cas_bw_frames(None, None) is None


def test_agg_sum_val_to_bw_frame():
  t0 = pd.Timestamp("2024-06-01 12:00:00+00:00")
  agg = pd.DataFrame(
      [("n1", t0, 3.0)],
      columns=["host", "time", "sum_val"],
  )
  out = agg_sum_val_to_bw_frame(agg)
  assert out is not None
  assert list(out.columns) == ["host", "time", "bw_gb"]
  assert float(out["bw_gb"].iloc[0]) == 3.0
  assert agg_sum_val_to_bw_frame(pd.DataFrame(columns=["host", "time", "sum_val"])) is None
