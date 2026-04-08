"""Gate pipeline E2E collection unless HPCPERFSTATS_PIPELINE_E2E=1."""
import os

import pytest


def pytest_collection_modifyitems(items):
  enabled = os.environ.get(
      "HPCPERFSTATS_PIPELINE_E2E", "").strip().lower() in (
          "1",
          "yes",
          "true",
      )
  skip = pytest.mark.skip(
      reason=(
          "Pipeline E2E (Docker Compose). Export HPCPERFSTATS_PIPELINE_E2E=1 "
          "and run tests/run_pipeline_e2e_workflow.sh."
      ),
  )
  for item in items:
    path = str(item.fspath).replace("\\", "/")
    if "/pipeline_e2e/" not in path:
      continue
    if not enabled:
      item.add_marker(skip)
