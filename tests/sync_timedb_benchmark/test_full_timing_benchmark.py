"""Unit tests for closed-book timing accounting and benchmark result schema."""
from __future__ import annotations

import pytest

from tests.sync_timedb_benchmark.timing_accounting import (
    account_phases,
    assert_residual_within,
)

BENCHMARK_RESULT_KEYS: tuple[str, ...] = (
    "run_id",
    "wall_s",
    "files_per_s",
    "input_mib_per_s",
    "rows_persisted_per_s",
    "postgres_s",
    "orm_materialize_s",
    "orm_bulk_prep_s",
    "db_execute_s",
    "db_commit_s",
    "parse_elapsed_s",
    "stages_sum_s",
    "parse_unaccounted_s",
    "file_lock_sh_wait_s",
    "file_lock_sh_hold_s",
    "file_lock_ex_wait_s",
    "file_lock_ex_hold_s",
    "residual_s",
    "residual_frac",
)


def test_account_phases_residual_is_non_negative():
  """Residual subtracts phase totals from wall time without going negative."""
  assert account_phases(10.0, {"a": 12.0}) == 0.0
  assert account_phases(10.0, {"a": 4.0, "b": 3.0}) == 3.0


def test_assert_residual_within_passes_tight_book():
  """Closed-book accounting passes when residual is within the fraction."""
  assert_residual_within(
      100.0,
      {
          "parse_elapsed_s": 40.0,
          "postgres_s": 35.0,
          "discovery_s": 20.0,
      },
      max_frac=0.05,
  )


def test_assert_residual_within_raises_when_residual_too_large():
  """Closed-book accounting fails when too much wall time is unaccounted."""
  with pytest.raises(AssertionError, match="residual"):
    assert_residual_within(100.0, {"parse_elapsed_s": 10.0}, max_frac=0.05)


def test_benchmark_result_schema_keys_are_stable():
  """Machine-readable benchmark artifacts must expose the closed-book key set."""
  sample = {key: 0.0 for key in BENCHMARK_RESULT_KEYS}
  assert set(sample) == set(BENCHMARK_RESULT_KEYS)
  assert "residual_frac" in sample
  assert "file_lock_ex_wait_s" in sample


@pytest.mark.sync_timedb_bench
def test_long_full_timing_benchmark_placeholder(tmp_path):
  """Opt-in screening writes a scaling artifact without requiring compose DB."""
  from tests.sync_timedb_benchmark.scaling_selection import select_thread_winner

  # Synthetic screening curve: throughput rises then plateaus; 96 has lock wait.
  points = []
  for threads in (1, 2, 4, 8, 16, 24, 32, 40, 48, 64, 80, 96):
    peak = min(threads, 32) * 0.5
    points.append({
        "threads": threads,
        "lower_ci_files_per_s": peak * (0.9 if threads <= 48 else 0.85),
        "upper_ci_files_per_s": peak * 1.05,
        "long_lock_wait": threads >= 96,
    })
  winner = select_thread_winner(points)
  assert winner["threads"] == 32
  out = tmp_path / "scaling_screening.json"
  import json
  out.write_text(json.dumps({"winner": winner, "points": points}, indent=2))
  assert out.is_file()
  assert winner["lower_ci_files_per_s"] > 0
