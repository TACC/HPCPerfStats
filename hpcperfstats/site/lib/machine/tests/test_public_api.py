"""Anonymous `/api/pub/` regression coverage."""

from unittest.mock import patch

import pytest
from django.test import Client


@pytest.mark.django_db(databases=[])
@patch(
    "hpcperfstats.site.lib.machine.public_api.cfg.get_host_name_ext",
    return_value="cluster.test",
)
@patch(
    "hpcperfstats.site.lib.machine.public_api.assemble_public_dashboard_meta_bundle",
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
    "hpcperfstats.site.lib.machine.public_api.cfg.get_host_name_ext",
    return_value="cluster.test",
)
@patch(
    "hpcperfstats.site.lib.machine.public_api.assemble_public_dashboard_meta_bundle",
    return_value={"status": "loading", "sections": {}},
)
def test_public_cluster_dashboard_loading_not_publicly_cached(_mock_bundle, _mock_host):
    client = Client()
    response = client.get("/api/pub/cluster-dashboard/")
    assert response.json()["machine_name"] == "cluster.test"
    cc = (response.get("Cache-Control") or "").lower()
    assert "public" not in cc
    assert "no-store" in cc


@pytest.mark.django_db(databases=[])
@pytest.mark.parametrize(
    "query",
    [
        "section=expansion_factor",
        "grouping=monthly",
        "period=2024-01",
        "section=expansion_factor&grouping=monthly",
        "section=expansion_factor&period=2024-01",
        "grouping=monthly&period=2024-01",
    ],
)
def test_public_cluster_dashboard_rejects_incomplete_lazy_tuple(query):
    client = Client()
    response = client.get(f"/api/pub/cluster-dashboard/?{query}")
    assert response.status_code == 400
    payload = response.json()
    assert payload["error"] == "invalid_request"
    assert payload["detail"] == "Invalid or incomplete query parameters."
    body = response.content.decode("utf-8")
    assert "<script" not in body.lower()
    # Never echo rejected query tokens back into the JSON body.
    for token in ("weekly", "not-a-period", "<script", "alert(1)"):
        assert token not in body


@pytest.mark.django_db(databases=[])
@pytest.mark.parametrize(
    "query",
    [
        "section=other&grouping=monthly&period=2024-01",
        "section=expansion_factor&grouping=weekly&period=2024-01",
        "section=expansion_factor&grouping=monthly&period=2024",
        "section=expansion_factor&grouping=yearly&period=2024-01",
        "section=expansion_factor&grouping=monthly&period=<script>alert(1)</script>",
        "section=expansion_factor&grouping=monthly&period=not-a-period",
    ],
)
def test_public_cluster_dashboard_rejects_invalid_lazy_values_without_reflection(query):
    client = Client()
    response = client.get(f"/api/pub/cluster-dashboard/?{query}")
    assert response.status_code == 400
    payload = response.json()
    assert payload["error"] == "invalid_request"
    assert payload["detail"] == "Invalid or incomplete query parameters."
    body = response.content.decode("utf-8")
    assert "<script" not in body.lower()
    assert "alert(1)" not in body
    assert "not-a-period" not in body
    assert "weekly" not in body


@pytest.mark.django_db(databases=[])
@patch(
    "hpcperfstats.site.lib.machine.public_api.cfg.get_host_name_ext",
    return_value="cluster.test",
)
@patch(
    "hpcperfstats.site.lib.machine.public_api.load_public_expansion_factor_period",
    return_value=None,
)
def test_public_cluster_dashboard_missing_period_is_generic_404(_mock_load, _mock_host):
    client = Client()
    response = client.get(
        "/api/pub/cluster-dashboard/"
        "?section=expansion_factor&grouping=monthly&period=2099-01"
    )
    assert response.status_code == 404
    payload = response.json()
    assert payload["error"] == "period_not_available"
    assert payload["detail"] == "Requested period is not available."
    body = response.content.decode("utf-8")
    assert "2099-01" not in body
    assert "grouping=" not in body


@pytest.mark.django_db(databases=[])
@patch(
    "hpcperfstats.site.lib.machine.public_api.cfg.get_host_name_ext",
    return_value="cluster.test",
)
@patch(
    "hpcperfstats.site.lib.machine.public_api.load_public_expansion_factor_period",
    return_value={"bins": [], "counts": []},
)
def test_public_cluster_dashboard_lazy_period_ok(_mock_load, _mock_host):
    client = Client()
    response = client.get(
        "/api/pub/cluster-dashboard/"
        "?section=expansion_factor&grouping=yearly&period=2024"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["section"] == "expansion_factor"
    assert payload["grouping"] == "yearly"
    assert payload["period_key"] == "2024"
    assert payload["block"] == {"bins": [], "counts": []}
