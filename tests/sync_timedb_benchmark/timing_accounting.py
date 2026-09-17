"""Pure helpers for closed-book residual accounting in sync_timedb benchmarks."""
from __future__ import annotations


def account_phases(wall_s: float, phases: dict[str, float]) -> float:
  """
  Return unaccounted wall seconds after subtracting additive phase totals.

  Args:
    wall_s (float): Measured wall-clock seconds for the window.
    phases (dict[str, float]): Named phase durations in seconds.

  Returns:
    float: Non-negative residual ``wall_s - sum(phases)``.

  Examples:
    >>> account_phases(10.0, {"parse_s": 3.0, "postgres_s": 4.0})
    3.0
  """
  accounted = sum(float(value) for value in phases.values())
  return max(0.0, float(wall_s) - accounted)


def assert_residual_within(
    wall_s: float,
    phases: dict[str, float],
    *,
    max_frac: float = 0.05,
) -> None:
  """
  Assert closed-book residual is within ``max_frac`` of ``wall_s``.

  Args:
    wall_s (float): Measured wall-clock seconds for the window.
    phases (dict[str, float]): Named phase durations in seconds.
    max_frac (float): Maximum allowed residual fraction of ``wall_s``.

  Returns:
    None

  Raises:
    AssertionError: When residual exceeds ``max_frac * wall_s``.

  Examples:
    >>> assert_residual_within(100.0, {"a": 48.0, "b": 49.0}, max_frac=0.05)
  """
  wall = float(wall_s)
  if wall <= 0.0:
    return
  residual = account_phases(wall, phases)
  limit = float(max_frac) * wall
  if residual > limit:
    raise AssertionError(
        "residual %.6fs exceeds %.1f%% of wall %.6fs (limit %.6fs); phases=%r"
        % (residual, max_frac * 100.0, wall, limit, phases),
    )
