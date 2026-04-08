"""Drift guard: root URLconf route templates vs expected set (update when adding paths)."""
import pytest

from hpcperfstats.tests.urlconf_route_catalog import (
    EXPECTED_ROUTE_TEMPLATES,
    build_pipeline_http_endpoint_specs,
    collect_route_templates,
)
from django.urls import get_resolver


@pytest.mark.django_db(databases=[])
def test_root_urlconf_matches_expected_route_templates():
  live = frozenset(collect_route_templates(get_resolver().url_patterns))
  assert live == EXPECTED_ROUTE_TEMPLATES, (
      "URLconf changed; update EXPECTED_ROUTE_TEMPLATES in "
      "hpcperfstats/tests/urlconf_route_catalog.py and pipeline E2E matrix.\n"
      "Missing from snapshot: {}\nExtra in snapshot: {}".format(
          sorted(live - EXPECTED_ROUTE_TEMPLATES),
          sorted(EXPECTED_ROUTE_TEMPLATES - live),
      )
  )


def test_pipeline_http_matrix_covers_all_expected_templates():
  """Builder must list every template or raise; used by pipeline Playwright tests."""
  specs = build_pipeline_http_endpoint_specs(
      jid="jid_matrix_smoke",
      username="u",
      fqdn="h.example.com",
      time_gte_iso="2020-01-01T00:00:00+00:00",
      time_lte_iso="2020-01-02T00:00:00+00:00",
  )
  templates = frozenset(s.route_template for s in specs)
  assert templates == EXPECTED_ROUTE_TEMPLATES
  assert specs[-1].path == "/api/user-api-key/rotate/"
