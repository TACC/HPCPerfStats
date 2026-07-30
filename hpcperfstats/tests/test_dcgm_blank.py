"""Unit tests for DCGM blank-family helpers (match dcgm_structs.h)."""

from __future__ import annotations

import numpy as np
import pytest

from hpcperfstats.lib.dcgm_blank import (
    DCGM_FP64_BLANK,
    DCGM_INT64_BLANK,
    is_dcgm_fp64_blank,
    is_dcgm_int64_blank,
    is_dcgm_numeric_blank,
    nan_out_dcgm_numeric_blanks,
)


def test_fp64_blank_base_and_family():
  assert is_dcgm_fp64_blank(DCGM_FP64_BLANK)
  assert is_dcgm_fp64_blank(DCGM_FP64_BLANK + 1.0)  # NOT_FOUND
  assert is_dcgm_fp64_blank(DCGM_FP64_BLANK + 2.0)  # NOT_SUPPORTED
  assert is_dcgm_fp64_blank(DCGM_FP64_BLANK + 3.0)  # NOT_PERMISSIONED
  assert not is_dcgm_fp64_blank(DCGM_FP64_BLANK - 1.0)
  assert not is_dcgm_fp64_blank(300.0)
  assert not is_dcgm_fp64_blank(0.0)
  assert not is_dcgm_fp64_blank(None)
  assert not is_dcgm_fp64_blank(float("nan"))


def test_int64_blank_base_and_family():
  assert is_dcgm_int64_blank(DCGM_INT64_BLANK)
  assert is_dcgm_int64_blank(DCGM_INT64_BLANK + 1)
  assert is_dcgm_int64_blank(float(DCGM_INT64_BLANK))
  assert not is_dcgm_int64_blank(100)
  assert not is_dcgm_int64_blank(0x6B48C000)  # garbage bitmask, not blank
  assert not is_dcgm_int64_blank(None)


def test_four_device_blank_sums_match_production_poison():
  """JID 3351747-class: 4 × FP64 blank power; 4 × INT64 blank util."""
  power_sum = 4 * DCGM_FP64_BLANK
  util_sum = 4 * float(DCGM_INT64_BLANK)
  assert is_dcgm_numeric_blank(power_sum)
  assert is_dcgm_numeric_blank(util_sum)
  assert power_sum == pytest.approx(562949950000000.0)


def test_nan_out_preserves_real_gpu_gauges():
  arr = np.array([0.0, 42.5, 99.0, 300.0, DCGM_FP64_BLANK, float("nan")], dtype=np.float64)
  out = nan_out_dcgm_numeric_blanks(arr)
  assert out[0] == 0.0
  assert out[1] == 42.5
  assert out[2] == 99.0
  assert out[3] == 300.0
  assert np.isnan(out[4])
  assert np.isnan(out[5])


def test_nan_out_int64_blank_as_float():
  arr = np.array([50.0, float(DCGM_INT64_BLANK)], dtype=np.float64)
  out = nan_out_dcgm_numeric_blanks(arr)
  assert out[0] == 50.0
  assert np.isnan(out[1])
