"""
Combine Intel IMC DDR (dram_cas_*) and SPR HBM (hbm_cas_*) measured BW scalars.

Same 64 B/CAS conversion is applied by callers before passing scalars here.
Absent or non-finite sides are ignored; when both sides are usable they are
summed. Per-sample series are combined by
``columnar.summary_series.combine_bw``.
"""

from __future__ import annotations

from typing import Any

import numpy as np


def _scalar_usable(value: Any) -> bool:
  """
  True when ``value`` is a finite number.

  Args:
    value (Any): Value to inspect (typically a numeric scalar).

  Returns:
    bool: Finite or not.

  Examples:
    >>> _scalar_usable(None), _scalar_usable(1.5)
    (False, True)
  """
  if value is None:
    return False
  try:
    return bool(np.isfinite(float(value)))
  except TypeError, ValueError:
    return False


def combine_cas_bw_scalars(dram_v: Any, hbm_v: Any) -> float | None:
  """
  Sum usable DDR and HBM scalar BW (GB/s); None if neither side is usable.

  Args:
    dram_v (Any): DDR (DRAM) CAS bandwidth value, or something coercible to
      float.
    hbm_v (Any): HBM CAS bandwidth value, or something coercible to float.

  Returns:
    Optional[float]: Combined bandwidth, or None when unavailable.

  Examples:
    >>> combine_cas_bw_scalars(1.0, None), combine_cas_bw_scalars(1.0, 2.0)
    (1.0, 3.0)
  """
  d_ok = _scalar_usable(dram_v)
  h_ok = _scalar_usable(hbm_v)
  if d_ok and h_ok:
    return float(dram_v) + float(hbm_v)
  if d_ok:
    return float(dram_v)
  if h_ok:
    return float(hbm_v)
  return None
