"""Canonical URL route templates + helpers for drift-safe HTTP smoke tests.

``EXPECTED_ROUTE_TEMPLATES`` must match Django's root URLconf (see
``test_endpoint_route_snapshot``). Pipeline E2E builds concrete requests from
this set so new routes fail CI until the matrix is updated.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet, List, Optional
from urllib.parse import quote, urlencode

from django.urls import URLPattern, URLResolver, get_resolver

# Snapshot of collect_route_templates(); update when adding URL patterns.
EXPECTED_ROUTE_TEMPLATES: FrozenSet[str] = frozenset({
    "",
    "admin_monitor/",
    "api-key/",
    "api/admin_monitor/",
    "api/cache/invalidate-page/",
    "api/home/",
    "api/host_plot/",
    "api/job_monitor/",
    "api/job_monitor/gpu/",
    "api/jobs/",
    "api/jobs/<str:jid>/<str:type_name>/",
    "api/jobs/<str:pk>/",
    "api/jobs/<str:pk>/plots/",
    "api/jobs/histograms/",
    "api/sacct/ingest/",
    "api/search/",
    "api/session/",
    "api/session/drop-staff/",
    "api/user-api-key/",
    "api/user-api-key/rotate/",
    "csp-report/",
    "login/",
    "login_prompt",
    "logout/",
    "machine/",
    "machine/<path:path>",
    "oauth_callback/",
    "robots.txt",
})


def collect_route_templates(urlpatterns=None, prefix=""):
  if urlpatterns is None:
    urlpatterns = get_resolver().url_patterns
  out = []
  for p in urlpatterns:
    if isinstance(p, URLResolver):
      out.extend(
          collect_route_templates(
              p.url_patterns,
              prefix + str(p.pattern),
          ))
    elif isinstance(p, URLPattern):
      out.append(prefix + str(p.pattern))
  return sorted(set(out))


@dataclass(frozen=True)
class PipelineHttpEndpointSpec:
  """One HTTP check derived from a resolver template."""

  route_template: str
  method: str
  path: str
  ok_status_min: int
  ok_status_max: int
  content_type_substring: Optional[str]
  use_playwright_page_goto: bool
  csrf_post: bool = False
  post_data: Optional[str] = None
  extra_headers: Optional[dict] = None


def build_pipeline_http_endpoint_specs(
    *,
    jid: str,
    username: str,
    fqdn: str,
    time_gte_iso: str,
    time_lte_iso: str,
) -> List[PipelineHttpEndpointSpec]:
  """Concrete URLs for every entry in ``EXPECTED_ROUTE_TEMPLATES``.

  Raises ``ValueError`` if a template is not mapped — forces matrix updates
  when the URLconf gains patterns.
  """
  host_q = urlencode({
      "host": fqdn,
      "end_time__gte": time_gte_iso,
      "end_time__lte": time_lte_iso,
  })
  jid_q = quote(jid, safe="")
  user_q = quote(username, safe="")

  specs: List[PipelineHttpEndpointSpec] = []
  by_template: dict[str, List[PipelineHttpEndpointSpec]] = {}

  def add(spec: PipelineHttpEndpointSpec):
    by_template.setdefault(spec.route_template, []).append(spec)
    specs.append(spec)

  # --- Playwright page navigation (HTML / redirects) ---
  add(PipelineHttpEndpointSpec(
      "", "GET", "/", 200, 399, None, True))
  add(PipelineHttpEndpointSpec(
      "admin_monitor/", "GET", "/admin_monitor/", 200, 399, "text/html", True))
  add(PipelineHttpEndpointSpec(
      "api-key/", "GET", "/api-key/", 200, 399, "text/html", True))
  add(PipelineHttpEndpointSpec(
      "csp-report/", "GET", "/csp-report/", 405, 405, None, True))
  add(PipelineHttpEndpointSpec(
      "login/", "GET", "/login/", 200, 499, None, True))
  add(PipelineHttpEndpointSpec(
      "login_prompt", "GET", "/login_prompt", 200, 499, None, True))
  add(PipelineHttpEndpointSpec(
      "logout/", "GET", "/logout/", 200, 399, None, True))
  add(PipelineHttpEndpointSpec(
      "machine/", "GET", "/machine/", 200, 399, "text/html", True))
  add(PipelineHttpEndpointSpec(
      "machine/<path:path>", "GET", "/machine/job/{}/".format(jid),
      200, 399, "text/html", True))
  add(PipelineHttpEndpointSpec(
      "machine/<path:path>", "GET", "/machine/api-key",
      200, 399, "text/html", True))
  add(PipelineHttpEndpointSpec(
      "machine/<path:path>", "GET", "/machine/admin_monitor/",
      200, 399, "text/html", True))
  add(PipelineHttpEndpointSpec(
      "oauth_callback/", "GET", "/oauth_callback/", 200, 399, None, True))
  add(PipelineHttpEndpointSpec(
      "robots.txt", "GET", "/robots.txt", 200, 299, "text/plain", True))

  # --- APIRequestContext (JSON / API) ---
  add(PipelineHttpEndpointSpec(
      "api/home/", "GET", "/api/home/", 200, 299, "application/json", False))
  add(PipelineHttpEndpointSpec(
      "api/search/", "GET", "/api/search/?jid={}".format(jid_q),
      200, 299, "application/json", False))
  add(PipelineHttpEndpointSpec(
      "api/jobs/", "GET", "/api/jobs/", 200, 299, "application/json", False))
  add(PipelineHttpEndpointSpec(
      "api/jobs/histograms/", "GET", "/api/jobs/histograms/",
      200, 299, "application/json", False))
  add(PipelineHttpEndpointSpec(
      "api/jobs/<str:pk>/", "GET", "/api/jobs/{}/".format(jid),
      200, 299, "application/json", False))
  add(PipelineHttpEndpointSpec(
      "api/jobs/<str:pk>/plots/", "GET",
      "/api/jobs/{}/plots/?plot=summary_plot".format(jid),
      200, 299, "application/json", False))
  add(PipelineHttpEndpointSpec(
      "api/jobs/<str:jid>/<str:type_name>/", "GET",
      "/api/jobs/{}/{}/".format(jid, "cpu"),
      200, 499, "application/json", False))
  add(PipelineHttpEndpointSpec(
      "api/host_plot/", "GET", "/api/host_plot/?" + host_q,
      200, 499, "application/json", False))
  add(PipelineHttpEndpointSpec(
      "api/admin_monitor/", "GET", "/api/admin_monitor/",
      200, 299, "application/json", False))
  add(PipelineHttpEndpointSpec(
      "api/job_monitor/", "GET", "/api/job_monitor/",
      200, 299, "application/json", False))
  add(PipelineHttpEndpointSpec(
      "api/job_monitor/gpu/", "GET",
      "/api/job_monitor/gpu/?username={}".format(user_q),
      200, 299, "application/json", False))
  add(PipelineHttpEndpointSpec(
      "api/session/", "GET", "/api/session/", 200, 299, "application/json", False))
  add(PipelineHttpEndpointSpec(
      "api/user-api-key/", "GET", "/api/user-api-key/",
      200, 299, "application/json", False))

  add(PipelineHttpEndpointSpec(
      "csp-report/", "POST", "/csp-report/", 204, 204, None, False,
      csrf_post=False,
      post_data="{}",
      extra_headers={"Content-Type": "application/json"},
  ))

  add(PipelineHttpEndpointSpec(
      "api/cache/invalidate-page/", "POST", "/api/cache/invalidate-page/",
      200, 299, "application/json", False,
      csrf_post=True,
      post_data='{"page_path":"/machine/"}',
      extra_headers={"Content-Type": "application/json"},
  ))
  add(PipelineHttpEndpointSpec(
      "api/sacct/ingest/", "POST", "/api/sacct/ingest/?date=2020-01-01",
      400, 499, None, False,
      csrf_post=True,
      post_data="",
  ))
  add(PipelineHttpEndpointSpec(
      "api/session/drop-staff/", "POST", "/api/session/drop-staff/",
      200, 299, "application/json", False,
      csrf_post=True,
  ))
  add(PipelineHttpEndpointSpec(
      "api/user-api-key/rotate/", "POST", "/api/user-api-key/rotate/",
      200, 299, "application/json", False,
      csrf_post=True,
  ))

  covered = frozenset(by_template.keys())
  if covered != EXPECTED_ROUTE_TEMPLATES:
    raise ValueError(
        "Pipeline HTTP matrix out of sync with EXPECTED_ROUTE_TEMPLATES.\n"
        "Missing templates (add specs): {}\n"
        "Extra templates (remove from EXPECTED or add routes): {}".format(
            sorted(EXPECTED_ROUTE_TEMPLATES - covered),
            sorted(covered - EXPECTED_ROUTE_TEMPLATES),
        )
    )
  return specs
