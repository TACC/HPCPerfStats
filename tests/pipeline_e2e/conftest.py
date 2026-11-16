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
  if enabled:
    return
  skip = pytest.mark.skip(
      reason=(
          "Pipeline E2E (Docker Compose). Export HPCPERFSTATS_PIPELINE_E2E=1 "
          "and run tests/run_pipeline_e2e_workflow.sh."
      ),
  )
  for item in items:
    if "/pipeline_e2e/" in str(item.fspath).replace("\\", "/"):
      item.add_marker(skip)
