"""
Per-(host, time) ``node_power_est_w`` using the same rules as SummaryPlot.

Thin wrappers over ``columnar.detail_series`` kept for import stability.
"""

from __future__ import annotations

from typing import Any

from hpcperfstats.analysis.metrics.lib.columnar import detail_series as _ds


def build_node_power_est_dataframe(jt: Any) -> Any:
  """
  Power components on the ``(host, time)`` base plus ``node_power_est_w``.

  Args:
    jt (Any): ``ColumnarJobTable`` of the job.

  Returns:
    Any: ``SeriesFrame`` (empty when the job has no base rows).

  Examples:
    >>> build_node_power_est_dataframe(None)  # doctest: +SKIP
  """
  return _ds.node_power_est_frame(jt)


def max_node_power_est_w(jt: Any) -> Any:
  """
  Peak ``node_power_est_w`` over all hosts and samples, or None.

  Args:
    jt (Any): ``ColumnarJobTable`` of the job.

  Returns:
    Any: Peak watts or None.

  Examples:
    >>> max_node_power_est_w(None)  # doctest: +SKIP
  """
  return _ds.max_node_power_est_w(jt)


def mean_node_power_est_w(jt: Any) -> Any:
  """
  Job-wide mean of ``node_power_est_w`` over finite samples, or None.

  Args:
    jt (Any): ``ColumnarJobTable`` of the job.

  Returns:
    Any: Mean watts or None.

  Examples:
    >>> mean_node_power_est_w(None)  # doctest: +SKIP
  """
  return _ds.mean_node_power_est_w(jt)


def job_cpu_gpu_watt_hours(jt: Any) -> Any:
  """
  Trapezoid integral of ``node_power_est_w`` per host (Wh), summed; None
  without finite CPU power fragments.

  Args:
    jt (Any): ``ColumnarJobTable`` of the job.

  Returns:
    Any: Watt-hours or None.

  Examples:
    >>> job_cpu_gpu_watt_hours(None)  # doctest: +SKIP
  """
  return _ds.job_cpu_gpu_watt_hours(jt)
