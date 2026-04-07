"""Stress tests: not collected by default ``pytest hpcperfstats`` (see ``testpaths``)."""
import os

import pytest


def pytest_collection_modifyitems(items):
  """Opt-in gate so ``pytest tests/stress_host_data`` does nothing unless requested."""
  enabled = os.environ.get(
      "HPCPERFSTATS_STRESS_HOST_DATA", "").strip().lower() in (
          "1",
          "yes",
          "true",
      )
  skip = pytest.mark.skip(
      reason=(
          "Stress suite (large DB load). Export HPCPERFSTATS_STRESS_HOST_DATA=1 "
          "and run inside Docker Compose with PostgreSQL/Timescale. "
          "See tests/stress_host_data/test_massive_host_data_job.py docstring."
      ),
  )
  for item in items:
    path = str(item.fspath).replace("\\", "/")
    if "/stress_host_data/" not in path:
      continue
    if not enabled:
      item.add_marker(skip)
