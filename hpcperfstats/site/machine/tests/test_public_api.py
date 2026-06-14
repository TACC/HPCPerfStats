"""Anonymous `/api/pub/` regression coverage."""

from unittest.mock import patch

import pytest
from django.test import Client


@pytest.mark.django_db(databases=[])
@patch(
    "hpcperfstats.site.machine.public_api.cfg.get_host_name_ext",
    return_value="cluster.test",
)
@patch(
    "hpcperfstats.site.machine.public_api.assemble_public_dashboard_meta_bundle",
    return_value={
        "status": "ready",
        "detail": None,
        "retry_hint": None,
        "schema_version": 1,
        "sections": {"expansion_factor": {"yearly_period_keys": ["2024"]}},
    },
)
def test_public_cluster_dashboard_allows_anonymous_get(_mock_bundle, _mock_host):
    client = Client()
    response = client.get("/api/pub/cluster-dashboard/")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] in ("loading", "ready")
    assert "sections" in payload
    assert payload["machine_name"] == "cluster.test"
    cc = (response.get("Cache-Control") or "").lower()
    assert "public" in cc
    assert "max-age=" in cc


@pytest.mark.django_db(databases=[])
def test_public_cluster_dashboard_rejects_post():
    client = Client()
    response = client.post("/api/pub/cluster-dashboard/")
    assert response.status_code == 405


@pytest.mark.django_db(databases=[])
@patch(
    "hpcperfstats.site.machine.public_api.cfg.get_host_name_ext",
    return_value="cluster.test",
)
@patch(
    "hpcperfstats.site.machine.public_api.assemble_public_dashboard_meta_bundle",
    return_value={"status": "loading", "sections": {}},
)
def test_public_cluster_dashboard_loading_not_publicly_cached(_mock_bundle, _mock_host):
    client = Client()
    response = client.get("/api/pub/cluster-dashboard/")
    assert response.json()["machine_name"] == "cluster.test"
    cc = (response.get("Cache-Control") or "").lower()
    assert "public" not in cc
    assert "no-store" in cc
