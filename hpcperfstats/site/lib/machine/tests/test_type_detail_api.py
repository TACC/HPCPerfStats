"""Regression tests for type_detail host_data scoping (time + hosts only; jid not in filters)."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.test import RequestFactory

from hpcperfstats.analysis.metrics.lib.gen.jid_table import TYPE_DETAIL_HOST_QUERY_BATCH


@pytest.mark.django_db(databases=[])
def test_type_detail_host_data_filter_sql_has_no_jid_in_where_clause():
  """Type-detail host_data queries must not filter on host_data.jid."""
  from hpcperfstats.analysis.metrics.lib.gen.jid_table import TypeDetailDataProvider

  st = datetime(2024, 1, 1, tzinfo=timezone.utc)
  et = datetime(2024, 1, 2, tzinfo=timezone.utc)
  provider = TypeDetailDataProvider(
      jid="job123",
      type_name="cpu",
      start_time=st,
      end_time=et,
      host_list=["n1.example.com"],
  )
  qs = provider._qs()
  sql = str(qs.query).lower()
  # jid may appear in the SELECT list, but not in WHERE (filtering).
  where_idx = sql.find(" where ")
  where_sql = sql[where_idx:] if where_idx != -1 else ""
  assert ".jid" not in where_sql


@pytest.mark.django_db(databases=[])
def test_type_detail_get_aggregate_df_batches_large_host_list():
  """TypeDetailDataProvider runs bounded host__in when host_list exceeds batch size."""
  from hpcperfstats.analysis.metrics.lib.gen.jid_table import TypeDetailDataProvider

  st = datetime(2024, 1, 1, tzinfo=timezone.utc)
  et = datetime(2024, 1, 2, tzinfo=timezone.utc)
  n = TYPE_DETAIL_HOST_QUERY_BATCH + 1
  hosts = ["n{0}.example.com".format(i) for i in range(n)]
  provider = TypeDetailDataProvider(
      jid="job123",
      type_name="cpu",
      start_time=st,
      end_time=et,
      host_list=hosts,
  )
  chunk_sizes = []

  class Qs:
    def values(self, *cols):
      return self

    def annotate(self, **kwargs):
      return self

    def order_by(self, *args):
      return self

    def __iter__(self):
      return iter(())

  class Mgr:
    def filter(self, **kwargs):
      chunk_sizes.append(len(kwargs.get("host__in") or []))
      return Qs()

  with patch(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table.host_data.objects",
      Mgr(),
  ):
    with patch(
        "hpcperfstats.analysis.metrics.lib.gen.jid_table.cached_orm",
        lambda _k, _ttl, fn: fn(),
    ):
      provider.get_aggregate_df("some_event", metric="arc")
  assert len(chunk_sizes) == 2
  assert chunk_sizes[0] == TYPE_DETAIL_HOST_QUERY_BATCH
  assert chunk_sizes[1] == 1


@pytest.mark.django_db(databases=[])
def test_type_detail_response_omits_legacy_tscript_tdiv():
  """Type detail uses Bokeh json_item (tplot_item) only; legacy script/div fields are removed."""
  from hpcperfstats.site.lib.machine import api

  job = SimpleNamespace(
      host_list=["n1"],
      start_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
      end_time=datetime(2024, 1, 2, tzinfo=timezone.utc),
  )
  factory = RequestFactory()
  request = factory.get("/api/jobs/j1/cpu/")
  request.session = {"username": "u"}

  with patch.object(api, "_require_auth", return_value=None), patch.object(
      api,
      "_get_visible_job_or_error_response",
      return_value=(job, None),
  ), patch.object(api, "load_job_detail_artifact", return_value=None), patch(
      "hpcperfstats.site.lib.machine.api.cfg.get_host_name_ext",
      return_value="example.com",
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

  with patch.object(api, "_require_auth", return_value=None), patch.object(
      api, "get_site_content_cache_timeout", return_value=60
  ), patch.object(api, "cached_orm", return_value={"type": "object"}):
    response = api.host_plot(request)

  assert response.status_code == 200
  assert response.data["plot_item"] == {"type": "object"}
