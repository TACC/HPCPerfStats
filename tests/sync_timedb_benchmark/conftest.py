"""Pytest hooks for opt-in sync_timedb benchmark long runs."""
from __future__ import annotations

import os

import pytest

BENCH_ENV = "HPCPERFSTATS_SYNC_TIMEDB_BENCH"


def pytest_configure(config: pytest.Config) -> None:
  """
  Register the sync_timedb benchmark marker.

  Args:
    config (pytest.Config): Active pytest configuration.

  Returns:
    None
  """
  config.addinivalue_line(
      "markers",
      "sync_timedb_bench(long): long sync_timedb benchmark run (opt-in)",
  )


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
  """
  Skip long benchmark tests unless ``HPCPERFSTATS_SYNC_TIMEDB_BENCH=1``.

  Args:
    config (pytest.Config): Active pytest configuration.
    items (list[pytest.Item]): Collected test items.

  Returns:
    None
  """
  if os.environ.get(BENCH_ENV) == "1":
    return
  skip = pytest.mark.skip(
      reason="set %s=1 for long sync_timedb benchmark runs" % BENCH_ENV,
  )
  for item in items:
    if "sync_timedb_bench" in item.keywords:
      item.add_marker(skip)
