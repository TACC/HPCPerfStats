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


def _compose_network_enabled() -> bool:
  return os.environ.get("HPCPERFSTATS_COMPOSE_NETWORK", "").strip().lower() in (
      "1",
      "yes",
      "true",
  )


def _screening_enabled() -> bool:
  return os.environ.get("HPCPERFSTATS_SYNC_TIMEDB_SCREENING", "").strip().lower() in (
      "1",
      "yes",
      "true",
  )


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
  """
  Skip long benchmark tests unless ``HPCPERFSTATS_SYNC_TIMEDB_BENCH=1``.

  Also defer ``django_db`` for ingest-width screening until compose screening is
  enabled so host unit sessions do not attempt PostgreSQL at hostname ``db``.

  Args:
    config (pytest.Config): Active pytest configuration.
    items (list[pytest.Item]): Collected test items.

  Returns:
    None
  """
  del config
  if os.environ.get(BENCH_ENV) != "1":
    skip = pytest.mark.skip(
        reason="set %s=1 for long sync_timedb benchmark runs" % BENCH_ENV,
    )
    for item in items:
      if "sync_timedb_bench" in item.keywords:
        item.add_marker(skip)

  screening_on = _compose_network_enabled() and _screening_enabled()
  skip_screening = pytest.mark.skip(
      reason=(
          "Requires HPCPERFSTATS_COMPOSE_NETWORK=1 and "
          "HPCPERFSTATS_SYNC_TIMEDB_SCREENING=1 (workflow --screening)"
      ),
  )
  db_mark = pytest.mark.django_db(transaction=True)
  for item in items:
    if "test_ingest_width_screening" not in item.nodeid:
      continue
    if screening_on:
      item.add_marker(db_mark)
    else:
      item.add_marker(skip_screening)
