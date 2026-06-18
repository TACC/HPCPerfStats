"""Regression: committed openapi.yaml must parse for Orval (js-yaml)."""
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.machine_unit_mock

COMMITTED_SCHEMA = (
    Path(__file__).resolve().parents[3] / "openapi" / "openapi.yaml"
)


def test_committed_openapi_yaml_parses_for_orval():
    """Unquoted colons in parameter descriptions break js-yaml / npm run generate:api."""
    text = COMMITTED_SCHEMA.read_text(encoding="utf-8")
    schema = yaml.safe_load(text)
    jobs_params = schema["paths"]["/api/jobs/"]["get"]["parameters"]
    state_param = next(p for p in jobs_params if p.get("name") == "state")
    assert "completed" in state_param["description"]
