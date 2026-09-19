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


def _knee_enabled() -> bool:
  return os.environ.get("HPCPERFSTATS_SYNC_TIMEDB_KNEE", "").strip().lower() in (
      "1",
      "yes",
      "true",
  )


def _e2_enabled() -> bool:
  return os.environ.get("HPCPERFSTATS_SYNC_TIMEDB_E2", "").strip().lower() in (
      "1",
      "yes",
      "true",
  )


def _knobs_enabled() -> bool:
  return os.environ.get("HPCPERFSTATS_SYNC_TIMEDB_KNOBS", "").strip().lower() in (
      "1",
      "yes",
      "true",
  )


def _e6_enabled() -> bool:
  return os.environ.get("HPCPERFSTATS_SYNC_TIMEDB_E6", "").strip().lower() in (
      "1",
      "yes",
      "true",
  )


def _e7_enabled() -> bool:
  return os.environ.get("HPCPERFSTATS_SYNC_TIMEDB_E7", "").strip().lower() in (
      "1",
      "yes",
      "true",
  )


def _contention_enabled() -> bool:
  return os.environ.get(
      "HPCPERFSTATS_SYNC_TIMEDB_CONTENTION",
      "",
  ).strip().lower() in (
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

  Also defer ``django_db`` for ingest-width screening/knee/E2/knobs/E6/E7/
  contention until compose flags are enabled so host unit sessions do not
  attempt PostgreSQL at hostname ``db``.

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

  width_study_on = _compose_network_enabled() and (
      _screening_enabled() or _knee_enabled()
  )
  skip_width = pytest.mark.skip(
      reason=(
          "Requires HPCPERFSTATS_COMPOSE_NETWORK=1 and "
          "HPCPERFSTATS_SYNC_TIMEDB_SCREENING=1 or "
          "HPCPERFSTATS_SYNC_TIMEDB_KNEE=1"
      ),
  )
  e2_on = _compose_network_enabled() and _e2_enabled()
  skip_e2 = pytest.mark.skip(
      reason=(
          "Requires HPCPERFSTATS_COMPOSE_NETWORK=1 and "
          "HPCPERFSTATS_SYNC_TIMEDB_E2=1 (workflow --e2)"
      ),
  )
  knobs_on = _compose_network_enabled() and _knobs_enabled()
  skip_knobs = pytest.mark.skip(
      reason=(
          "Requires HPCPERFSTATS_COMPOSE_NETWORK=1 and "
          "HPCPERFSTATS_SYNC_TIMEDB_KNOBS=1 (workflow --knobs)"
      ),
  )
  e6_on = _compose_network_enabled() and _e6_enabled()
  skip_e6 = pytest.mark.skip(
      reason=(
          "Requires HPCPERFSTATS_COMPOSE_NETWORK=1 and "
          "HPCPERFSTATS_SYNC_TIMEDB_E6=1 (workflow --e6)"
      ),
  )
  e7_on = _compose_network_enabled() and _e7_enabled()
  skip_e7 = pytest.mark.skip(
      reason=(
          "Requires HPCPERFSTATS_COMPOSE_NETWORK=1 and "
          "HPCPERFSTATS_SYNC_TIMEDB_E7=1 (workflow --e7)"
      ),
  )
  contention_on = _compose_network_enabled() and _contention_enabled()
  skip_contention = pytest.mark.skip(
      reason=(
          "Requires HPCPERFSTATS_COMPOSE_NETWORK=1 and "
          "HPCPERFSTATS_SYNC_TIMEDB_CONTENTION=1 (workflow --contention)"
      ),
  )
  db_mark = pytest.mark.django_db(transaction=True)
  for item in items:
    if "test_ingest_width_screening" in item.nodeid:
      if width_study_on:
        item.add_marker(db_mark)
      else:
        item.add_marker(skip_width)
    if "test_e2_closed_book_mid_size" in item.nodeid:
      if e2_on:
        item.add_marker(db_mark)
      else:
        item.add_marker(skip_e2)
    if "test_supporting_knobs" in item.nodeid:
      if knobs_on:
        item.add_marker(db_mark)
      else:
        item.add_marker(skip_knobs)
    if "test_e6_parse_feed_ab" in item.nodeid:
      if e6_on:
        item.add_marker(db_mark)
      else:
        item.add_marker(skip_e6)
    if "test_e7_proc_build_ab" in item.nodeid:
      if e7_on:
        item.add_marker(db_mark)
      else:
        item.add_marker(skip_e7)
    if "test_contention_ab" in item.nodeid:
      if contention_on:
        item.add_marker(db_mark)
      else:
        item.add_marker(skip_contention)
