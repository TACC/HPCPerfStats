"""Anonymous `/api/pub/` regression coverage."""

from unittest.mock import patch

import pytest
from django.test import Client


@pytest.mark.django_db(databases=[])
@patch(
    "hpcperfstats.site.machine.public_api.assemble_public_monthly_metrics_bundle",
    return_value={
        "status": "ready",
        "detail": None,
        "retry_hint": None,
        "schema_version": 1,
        "sections": {"expansion_factor": {}},
    },
)
def test_public_cluster_dashboard_allows_anonymous_get(_mock_bundle):
    client = Client()
    response = client.get("/api/pub/cluster-dashboard/")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] in ("loading", "ready")
    assert "sections" in payload


@pytest.mark.django_db(databases=[])
def test_public_cluster_dashboard_rejects_post():
    client = Client()
    response = client.post("/api/pub/cluster-dashboard/")
    assert response.status_code == 405


@pytest.mark.django_db(databases=[])
@patch(
    "hpcperfstats.site.machine.public_api.assemble_public_monthly_metrics_bundle",
    return_value={"status": "loading", "sections": {}},
)
def test_public_cluster_dashboard_sets_reasonable_cache_header(_mock_bundle):
    client = Client()
    response = client.get("/api/pub/cluster-dashboard/")
    cache_control = response.get("Cache-Control", "")
    assert "max-age" in cache_control.lower()
