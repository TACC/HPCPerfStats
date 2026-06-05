"""Tests for small pure helpers in metrics.py (no Django DB)."""

import numpy as np

from hpcperfstats.analysis.metrics.metrics import NUMEXPR_MIN_ARRAY_SIZE, _add_arrays, _finite_amax


def test_finite_amax_returns_none_for_all_nan():
  assert _finite_amax([np.nan, np.nan]) is None


def test_finite_amax_ignores_nan():
  assert _finite_amax([np.nan, 5.0, np.nan]) == 5.0


def test_add_arrays_small_uses_numpy_add():
  a = np.array([1.0, 2.0])
  b = np.array([3.0, 4.0])
  out = _add_arrays(a, b)
  np.testing.assert_array_equal(out, np.array([4.0, 6.0]))


def test_add_arrays_large_uses_numexpr_path():
  n = NUMEXPR_MIN_ARRAY_SIZE
  a = np.ones(n, dtype=np.float64)
  b = np.ones(n, dtype=np.float64)
  out = _add_arrays(a, b)
  assert out.shape == (n,)
  assert float(out[0]) == 2.0
