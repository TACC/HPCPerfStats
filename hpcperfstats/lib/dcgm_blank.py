"""
NVIDIA DCGM blank-family sentinels (mirror ``dcgm_structs.h``).

Telemetry that equals or exceeds these bases is missing/unsupported/not-found —
not a real watt, percent, or bitmask. Analysis and ingest must reject them
before sum/mean/max/OR so blank GPUs cannot poison job aggregates.

Attributes:
  DCGM_FP64_BLANK: Attribute.
  DCGM_INT64_BLANK: Attribute.
  _DCGM_INT64_BLANK_AS_FLOAT: Attribute.
"""

from __future__ import annotations

from typing import Any

import numpy as np

# From monitor/third_party/nvidia-dcgm/dcgm_structs.h (DCGM_BLANK_VALUES).
DCGM_INT64_BLANK = 0x7FFFFFFFFFFFFFF0
DCGM_FP64_BLANK = 140737488355328.0  # 2**47

# Float view of INT64 blank (lossy around 2**63; still >= INT64 blank).
_DCGM_INT64_BLANK_AS_FLOAT = float(DCGM_INT64_BLANK)


def is_dcgm_fp64_blank(value: Any) -> bool:
  """
  True when ``value`` is in the DCGM FP64 blank family (``>= DCGM_FP64_BLANK``).
  
  Args:
    value (Any): Value to inspect (typically a numeric scalar).
  
  Returns:
    bool: True or False for this check.
  
  Examples:
    >>> is_dcgm_fp64_blank(None)  # doctest: +SKIP
  """
  if value is None:
    return False
  try:
    v = float(value)
  except (TypeError, ValueError):
    return False
  if not np.isfinite(v):
    return False
  return v >= DCGM_FP64_BLANK


def is_dcgm_int64_blank(value: Any) -> bool:
  """
  True when ``value`` is in the DCGM INT64 blank family (``>=.
  
    DCGM_INT64_BLANK``).
  
  Accepts int or float storage (archives often promote i64 gauges to float64).
  
  Args:
    value (Any): Value to inspect (typically a numeric scalar).
  
  Returns:
    bool: True or False for this check.
  
  Examples:
    >>> is_dcgm_int64_blank(None)  # doctest: +SKIP
  """
  if value is None:
    return False
  try:
    if isinstance(value, (int, np.integer)):
      return int(value) >= DCGM_INT64_BLANK
    v = float(value)
  except (TypeError, ValueError, OverflowError):
    return False
  if not np.isfinite(v):
    return False
  return v >= _DCGM_INT64_BLANK_AS_FLOAT or v >= DCGM_INT64_BLANK


def is_dcgm_numeric_blank(value: Any) -> bool:
  """
  True when ``value`` is FP64- or INT64-blank family (either implies missing).
  
  Args:
    value (Any): Value to inspect (typically a numeric scalar).
  
  Returns:
    bool: True or False for this check.
  
  Examples:
    >>> is_dcgm_numeric_blank(None)  # doctest: +SKIP
  """
  return is_dcgm_fp64_blank(value) or is_dcgm_int64_blank(value)


def nan_out_dcgm_numeric_blanks(values: Any) -> np.ndarray:
  """
  Return float64 copy of ``values`` with DCGM blank-family entries set to NaN.
  
  Args:
    values (Any): Values passed to this helper.
  
  Returns:
    np.ndarray: np.ndarray produced by this call.
  
  Examples:
    >>> nan_out_dcgm_numeric_blanks(None)  # doctest: +SKIP
  """
  arr = np.asarray(values, dtype=np.float64)
  if arr.size == 0:
    return arr.copy() if arr.ndim else np.asarray([], dtype=np.float64)
  out = arr.copy()
  # FP64 blank base catches INT64 blanks too (INT64 blank >> FP64 blank).
  blank = np.isfinite(out) & (out >= DCGM_FP64_BLANK)
  out[blank] = np.nan
  return out
