"""Unique-watt aggregation for Grace DCGM ``dcgm_cpu_power_util_w``.

Monitor replicates per-socket watts onto every logical CPU ``dev`` row. A raw
SUM across CPUs overcounts by ~Ncores. Metrics and SummaryPlot collapse those
replicas by summing **unique** finite watt paints per ``(host, time)`` after
rounding (float-stable uniqueness).

Attributes:
  WATT_UNIQUE_ROUND_DECIMALS (int): Decimal places applied before uniqueness.
  __all__ (tuple): Public exports for ``from … import *``.
"""
from __future__ import annotations

from typing import Any

import numpy as np

# Round before uniqueness so identical socket paints with float noise collapse.
WATT_UNIQUE_ROUND_DECIMALS: int = 3

__all__ = (
    "WATT_UNIQUE_ROUND_DECIMALS",
    "sum_unique_watt_values_per_host_time",
)


def sum_unique_watt_values_per_host_time(
    df: Any,
    *,
    value_col: str = "sum_val",
    round_decimals: int = WATT_UNIQUE_ROUND_DECIMALS,
) -> Any:
  """
  Collapse per-device watt rows to sum of unique rounded paints per host/time.

  Identical replicas of one socket paint become a single contribution. Distinct
  socket paints (multi-socket hosts) are summed. Empty or missing ``value_col``
  yields an empty ``host``/``time``/``sum_val`` frame.

  Args:
    df (Any): Aggregate DataFrame with ``host``, ``time``, and ``value_col``
      (typically one row per logical CPU ``dev`` after ``group_by_dev=True``).
    value_col (str): Column holding watt samples (default ``sum_val``).
    round_decimals (int): Decimal places used before uniqueness (default
      ``WATT_UNIQUE_ROUND_DECIMALS``).

  Returns:
    Any: DataFrame with columns ``host``, ``time``, ``sum_val``.

  Examples:
    >>> import pandas as pd
    >>> raw = pd.DataFrame(
    ...     {
    ...         "host": ["h1", "h1", "h1"],
    ...         "time": [1, 1, 1],
    ...         "sum_val": [45.0, 45.0, 45.0],
    ...     }
    ... )
    >>> out = sum_unique_watt_values_per_host_time(raw)
    >>> float(out.iloc[0]["sum_val"])
    45.0
  """
  import pandas as pd

  empty = pd.DataFrame(columns=["host", "time", "sum_val"])
  if df is None or getattr(df, "empty", True):
    return empty
  if (
      value_col not in df.columns
      or "host" not in df.columns
      or "time" not in df.columns
  ):
    return empty

  work = df[["host", "time", value_col]].copy()
  work["_watts"] = pd.to_numeric(work[value_col], errors="coerce")
  work = work.dropna(subset=["_watts"])
  if work.empty:
    return empty

  work["_rounded"] = work["_watts"].round(int(round_decimals))

  def _unique_sum(values: Any) -> float:
    """
    Sum unique finite values in one (host, time) group.

    Args:
      values (Any): Series or array-like of rounded watt paints.

    Returns:
      float: Sum of unique paints, or 0.0 when empty.

    Examples:
      >>> float(_unique_sum([10.0, 10.0, 20.0]))
      30.0
    """
    arr = np.asarray(values, dtype=np.float64)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
      return 0.0
    return float(np.unique(arr).sum())

  out = (
      work.groupby(["host", "time"], as_index=False)["_rounded"]
      .agg(_unique_sum)
      .rename(columns={"_rounded": "sum_val"})
  )
  return out[["host", "time", "sum_val"]].reset_index(drop=True)
