"""OpenAPI schema drift guard: committed YAML must match drf-spectacular output."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from django.test import override_settings
from drf_spectacular.generators import SchemaGenerator

COMMITTED_SCHEMA = (
    Path(__file__).resolve().parents[3] / "openapi" / "openapi.yaml"
)


def _normalize_schema(schema: dict) -> dict:
  """Stable JSON round-trip for comparable OpenAPI dicts."""
  return json.loads(json.dumps(schema, sort_keys=True))


def _load_committed_schema() -> dict:
  text = COMMITTED_SCHEMA.read_text(encoding="utf-8")
  return yaml.safe_load(text)


def _generate_live_schema() -> dict:
  generator = SchemaGenerator(
      title="HPCPerfStats API",
      version="3.0",
      description="REST API for the HPCPerfStats machine SPA and public dashboards.",
  )
  return generator.get_schema(request=None, public=True)


@pytest.mark.django_db(databases=[])
def test_openapi_schema_matches_committed_file():
  if not COMMITTED_SCHEMA.is_file():
    pytest.fail(
        "Missing committed OpenAPI at {}. Regenerate with:\n"
        "  cd HPCPerfStats && python manage.py spectacular "
        "--file hpcperfstats/site/openapi/openapi.yaml --format openapi".format(
            COMMITTED_SCHEMA,
        ),
    )
  live = _normalize_schema(_generate_live_schema())
  committed = _normalize_schema(_load_committed_schema())
  assert live == committed, (
      "OpenAPI schema drift. Regenerate:\n"
      "  cd HPCPerfStats && python manage.py spectacular "
      "--file hpcperfstats/site/openapi/openapi.yaml --format openapi"
  )


@pytest.mark.django_db(databases=[])
def test_openapi_schema_endpoint_registered():
  from django.urls import reverse

  assert reverse("schema") == "/api/schema/"
