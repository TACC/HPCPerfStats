"""Winner selection helpers for sync_timedb thread-scaling studies."""
from __future__ import annotations


def _ci_width(point: dict) -> float:
  """
  Return the confidence-interval width for a scaling study point.

  Args:
    point (dict): Study point with ``lower_ci_files_per_s`` and
      ``upper_ci_files_per_s``.

  Returns:
    float: ``upper_ci_files_per_s - lower_ci_files_per_s``.

  Examples:
    >>> _ci_width({"lower_ci_files_per_s": 1.0, "upper_ci_files_per_s": 1.2})
    0.2
  """
  lower = float(point["lower_ci_files_per_s"])
  upper = float(point["upper_ci_files_per_s"])
  return max(0.0, upper - lower)


def select_thread_winner(points: list[dict]) -> dict:
  """
  Select the best ingest thread count from scaling study points.

  Prefer the highest ``lower_ci_files_per_s``. Among points within 5% of the
  peak lower bound, prefer the smallest confidence-interval width. Reject
  points with ``long_lock_wait`` set true.

  Args:
    points (list[dict]): Scaling study result rows.

  Returns:
    dict: The winning point, or an empty dict when no eligible points exist.

  Examples:
    >>> select_thread_winner([
    ...     {
    ...         "threads": 8,
    ...         "lower_ci_files_per_s": 9.0,
    ...         "upper_ci_files_per_s": 10.0,
    ...         "long_lock_wait": False,
    ...     },
    ... ])["threads"]
    8
  """
  eligible = [
      point for point in points
      if not bool(point.get("long_lock_wait"))
  ]
  if not eligible:
    return {}
  peak_lower = max(float(point["lower_ci_files_per_s"]) for point in eligible)
  threshold = peak_lower * 0.95
  near_peak = [
      point for point in eligible
      if float(point["lower_ci_files_per_s"]) >= threshold
  ]
  return min(
      near_peak,
      key=lambda point: (
          _ci_width(point),
          -float(point["lower_ci_files_per_s"]),
          int(point.get("threads", 0)),
      ),
  )
