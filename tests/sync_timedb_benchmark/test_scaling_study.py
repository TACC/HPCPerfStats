"""Unit tests for sync_timedb scaling-study winner selection."""
from __future__ import annotations

from tests.sync_timedb_benchmark.scaling_selection import select_thread_winner


def _point(
    threads: int,
    lower: float,
    upper: float,
    *,
    long_lock_wait: bool = False,
) -> dict:
  return {
      "threads": threads,
      "lower_ci_files_per_s": lower,
      "upper_ci_files_per_s": upper,
      "long_lock_wait": long_lock_wait,
  }


def test_select_thread_winner_rejects_long_lock_wait():
  """Points with sustained lock waits must not win the scaling study."""
  winner = select_thread_winner([
      _point(64, 12.0, 13.0, long_lock_wait=True),
      _point(32, 10.0, 10.5),
  ])
  assert winner["threads"] == 32


def test_select_thread_winner_prefers_highest_lower_ci():
  """Winner maximizes the lower confidence bound on durable throughput."""
  winner = select_thread_winner([
      _point(16, 8.0, 9.0),
      _point(32, 9.5, 10.5),
      _point(48, 9.0, 9.2),
  ])
  assert winner["threads"] == 32


def test_select_thread_winner_prefers_narrower_ci_within_five_percent_of_peak():
  """Among near-peak lower bounds, prefer the smallest CI width."""
  winner = select_thread_winner([
      _point(24, 9.6, 11.0),
      _point(32, 10.0, 10.4),
      _point(40, 9.7, 10.8),
  ])
  assert winner["threads"] == 32


def test_select_thread_winner_returns_empty_when_all_rejected():
  """No eligible points yields an empty winner dict."""
  assert select_thread_winner([
      _point(8, 5.0, 6.0, long_lock_wait=True),
  ]) == {}
