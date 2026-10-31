"""Unique-watt aggregation for Grace DCGM ``dcgm_cpu_power_util_w``.

Monitor replicates per-socket watts onto every logical CPU ``dev`` row. A raw
SUM across CPUs overcounts by ~Ncores. Metrics and SummaryPlot collapse those
replicas by summing **unique** finite watt paints per ``(host, time)`` after
rounding (float-stable uniqueness). The array implementation lives in
``columnar.summary_series.unique_watt_sum``; this module keeps the public
name and the rounding constant.

Attributes:
  WATT_UNIQUE_ROUND_DECIMALS (int): Decimal places applied before uniqueness.
  __all__ (tuple): Public exports for ``from ... import *``.
"""

from __future__ import annotations

from typing import Any

from hpcperfstats.analysis.metrics.lib.columnar.summary_series import (
  WATT_UNIQUE_ROUND_DECIMALS,
  unique_watt_sum,
)

__all__ = (
  "WATT_UNIQUE_ROUND_DECIMALS",
  "sum_unique_watt_values_per_host_time",
)


def sum_unique_watt_values_per_host_time(agg: Any) -> Any:
  """
  Collapse per-device watt rows to the sum of unique rounded paints per
  host/time.

  Args:
    agg (Any): Per-device ``AggTable`` (``dev_id`` set).

  Returns:
    Any: Host-level ``AggTable``.

  Examples:
    >>> import numpy as np
    >>> from hpcperfstats.analysis.metrics.lib.columnar.kernels import AggTable
    >>> t = AggTable(
    ...   np.array([0, 0]),
    ...   np.array([1, 1]),
    ...   np.array([45.0, 45.0]),
    ...   np.array([1, 2]),
    ... )
    >>> sum_unique_watt_values_per_host_time(t).sum_val.tolist()
    [45.0]
  """
  return unique_watt_sum(agg)
