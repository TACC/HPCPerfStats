"""Regression tests for the type_detail and host_plot API responses."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.test import RequestFactory


@pytest.mark.django_db(databases=[])
def test_type_detail_response_omits_legacy_tscript_tdiv():
  """Type detail uses Bokeh json_item (tplot_item) only; legacy script/div fields are removed."""
  from hpcperfstats.site.lib.machine import api

  job = SimpleNamespace(
    host_list=["n1"],
    start_time=datetime(2024, 1, 1, tzinfo=UTC),
    end_time=datetime(2024, 1, 2, tzinfo=UTC),
  )
  factory = RequestFactory()
  request = factory.get("/api/jobs/j1/cpu/")
  request.session = {"username": "u"}

  with (
    patch.object(api, "_require_auth", return_value=None),
    patch.object(
      api,
      "_get_visible_job_or_error_response",
      return_value=(job, None),
    ),
    patch.object(api, "load_job_detail_artifact", return_value=None),
    patch(
      "hpcperfstats.site.lib.machine.api.cfg.get_host_name_ext",
      return_value="example.com",
    ),
  ):
    response = api.type_detail(request, "j1", "cpu")

  assert response.status_code == 200
  body = response.data
  assert "tscript" not in body
  assert "tdiv" not in body
  assert body["tplot_item"] is None
  assert body["status"] == "loading"
  assert body["type_name"] == "cpu"
  assert body["jobid"] == "j1"


@pytest.mark.django_db(databases=[])
def test_host_plot_allows_non_staff_authenticated_users():
  from hpcperfstats.site.lib.machine import api

  factory = RequestFactory()
  request = factory.get(
    "/api/host_plot/",
    {
      "host": "h1",
      "end_time__gte": "2026-08-01T00:00:00Z",
      "end_time__lte": "2026-08-01T12:00:00Z",
    },
  )
  request.session = {"username": "u1", "is_staff": False}

  with (
    patch.object(api, "_require_auth", return_value=None),
    patch.object(api, "get_site_content_cache_timeout", return_value=60),
    patch.object(api, "cached_orm", return_value={"type": "object"}),
  ):
    response = api.host_plot(request)

  assert response.status_code == 200
  assert response.data["plot_item"] == {"type": "object"}
