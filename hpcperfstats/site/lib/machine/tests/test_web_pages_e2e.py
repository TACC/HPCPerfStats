"""End-to-end style web-access tests for all site pages.

These tests serve the stub SPA shell at ``/machine/...`` deep links and assert
status codes, headers (including CSP), and a few API/JSON flows. They do not
render the React bundle, so SPA-only behavior such as the JobList pagination
top/bottom rendering and per-column first-click sort direction is covered by
the Vitest suite at ``hpcperfstats/site/frontend/src/pages/JobList.test.jsx``.

Bokeh job-list embed regressions (real BokehJS + Playwright) live in
``test_bokeh_job_list_embed_browser_e2e.py``.
"""

from pathlib import Path

import pytest
from django.core.cache import cache
from django.test import Client
from django.test import override_settings
from django.utils import timezone
from unittest.mock import Mock, patch

from hpcperfstats.site.lib.machine.models import job_data
from hpcperfstats.site.lib.machine.tests.csrf_test_utils import csrf_headers
from hpcperfstats.tests.public_robots_js_registry import (
    format_public_robots_txt_body,
    load_public_robots_allow_prefixes,
)

@pytest.mark.django_db
class TestWebPagesEndToEnd:
  def test_public_routes_and_nginx_owned_spa_paths(self):
    """Validate top-level pages and nginx-owned SPA route contract."""
    client = Client()
    root_response = client.get("/")
    assert root_response.status_code == 302
    assert root_response["Location"] == "/machine/"

    # Production contract: nginx serves /machine/* SPA shell, not WSGI.
    assert client.get("/machine/").status_code == 404
    for path in (
        "/machine/home/",
        "/machine/jobs/",
        "/machine/job/123/",
        "/machine/job/123/?tab=roofline",
        "/machine/job/123/?tab=multiprecisionMix",
        "/machine/job/123/cpu/",
        "/machine/year/2020/",
        "/machine/date/2024-01-15/",
        "/machine/host/node1/plot/",
        "/machine/admin_monitor/",
        "/machine/job_monitor/",
        "/machine/test-login/",
        "/machine/logout/",
        "/pub/",
        "/pub/cluster-dashboard",
    ):
      assert client.get(path).status_code == 404

    assert client.get("/robots.txt").status_code == 404
    assert client.get("/test-login/").status_code == 404
    logout_response = client.get("/logout/")
    assert logout_response.status_code == 302
    assert logout_response["Location"] == "/"
    prefixes = load_public_robots_allow_prefixes()
    expected_body = format_public_robots_txt_body(prefixes)
    assert "User-agent: *" in expected_body
    assert "Disallow: /" in expected_body
    for prefix in prefixes:
      assert "Allow: {}".format(prefix) in expected_body
    built_path = Path(__file__).resolve().parents[5] / (
        "hpcperfstats/site/hpcperfstats_site/static/frontend/robots.txt"
    )
    if built_path.is_file():
      assert built_path.read_text(encoding="utf-8") == expected_body

    csp_response = client.post(
        "/csp-report/",
        data='{"csp-report":{"document-uri":"https://example.test"}}',
        content_type="application/csp-report",
    )
    assert csp_response.status_code == 204

  def test_api_key_legacy_path_redirects_to_spa_route(self):
    """Bookmarks to /api-key/ continue to work via redirect to the React route."""
    client = Client()
    redirect_response = client.get("/api-key/")
    assert redirect_response.status_code == 302
    assert "/machine/api-key" in redirect_response["Location"]

  def test_user_api_key_json_flow_with_session(self):
    """SPA-backed API key uses /api/user-api-key/ for status and rotation."""
    client = Client()
    session = client.session
    session["access_token"] = "token"
    session["username"] = "webtest-user"
    session["is_staff"] = True
    session.save()

    active_key = Mock()
    active_key.key_prefix = "abc123"
    list_queryset = Mock()
    list_queryset.order_by.return_value.first.return_value = active_key
    update_queryset = Mock()
    update_queryset.update.return_value = 1
    rotated_key = Mock()
    rotated_key.key_prefix = "def456"

    with patch("hpcperfstats.site.lib.machine.api.ApiKey.objects.filter") as mock_filter, patch(
        "hpcperfstats.site.lib.machine.api.ApiKey.create_from_raw_key",
        return_value=(rotated_key, "raw-new-api-key"),
    ):
      mock_filter.side_effect = [list_queryset, update_queryset]

      first_response = client.get("/api/user-api-key/")
      assert first_response.status_code == 200
      first_payload = first_response.json()
      assert first_payload["username"] == "webtest-user"
      assert first_payload["raw_key"] is None
      assert first_payload["key_prefix"] == "abc123"

      from django.middleware.csrf import get_token
      from django.test import RequestFactory

      csrf_request = RequestFactory().get("/")
      csrf_request.session = client.session
      rotate_response = client.post(
          "/api/user-api-key/rotate/",
          HTTP_X_CSRFTOKEN=get_token(csrf_request),
      )
      assert rotate_response.status_code == 200
      rotate_payload = rotate_response.json()
      assert rotate_payload["raw_key"] == "raw-new-api-key"

  def test_staff_can_disable_staff_for_current_session(self):
    """Validate staff-only session demotion endpoint and resulting session state."""
    client = Client()
    session = client.session
    session["access_token"] = "token"
    session["username"] = "webtest-user"
    session["is_staff"] = True
    session.save()

    drop_response = client.post("/api/session/drop-staff/", **csrf_headers())
    assert drop_response.status_code == 200
    payload = drop_response.json()
    assert payload["ok"] is True
    assert payload["is_staff"] is False
    assert "Log out and log back in" in payload["message"]

    session_response = client.get("/api/session/")
    assert session_response.status_code == 200
    session_payload = session_response.json()
    assert session_payload["logged_in"] is True
    assert session_payload["is_staff"] is False

    # After demotion, staff-only endpoints should be denied for this session.
    staff_only_response = client.get("/api/admin_monitor/")
    assert staff_only_response.status_code == 403

  def test_staff_only_endpoints_appear_for_staff_and_disappear_for_non_staff(self):
    """Staff-only API routes should allow staff and deny non-staff sessions."""
    client = Client()

    session = client.session
    session["access_token"] = "token"
    session["username"] = "staff-user"
    session["is_staff"] = True
    session.save()

    # Keep this test isolated from external DB/Redis dependencies by stubbing
    # expensive data paths. We only validate staff gating behavior here.
    with override_settings(
        CACHES={
            "default": {
                "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                "LOCATION": "staff-visibility-e2e",
            }
        }
    ), patch(
        "hpcperfstats.site.lib.machine.api._get_cache_stats",
        return_value={"ok": True},
    ):
      staff_admin = client.get("/api/admin_monitor/?section=cache")
      assert staff_admin.status_code == 200
      staff_ingest = client.post(
          "/api/sacct/ingest/?date=2026-01-01",
          data="",
          content_type="text/plain",
          **csrf_headers(),
      )
      assert staff_ingest.status_code == 200
      staff_invalidate = client.post(
          "/api/cache/invalidate-page/",
          data={"page_path": "/machine/jobs"},
          content_type="application/json",
          **csrf_headers(),
      )
      assert staff_invalidate.status_code == 200

      non_staff_session = client.session
      non_staff_session["is_staff"] = False
      non_staff_session.save()

      non_staff_admin = client.get("/api/admin_monitor/?section=cache")
      assert non_staff_admin.status_code == 403
      non_staff_ingest = client.post(
          "/api/sacct/ingest/?date=2026-01-01",
          data="",
          content_type="text/plain",
          **csrf_headers(),
      )
      assert non_staff_ingest.status_code == 403
      non_staff_invalidate = client.post(
          "/api/cache/invalidate-page/",
          data={"page_path": "/machine/jobs"},
          content_type="application/json",
          **csrf_headers(),
      )
      assert non_staff_invalidate.status_code == 403

  def test_session_staff_flag_drives_frontend_plot_error_visibility(self):
    """Session API should expose staff state used by frontend error-detail gating."""
    client = Client()
    session = client.session
    session["access_token"] = "token"
    session["username"] = "plot-user"
    session["is_staff"] = True
    session.save()

    staff_response = client.get("/api/session/")
    assert staff_response.status_code == 200
    assert staff_response.json()["is_staff"] is True

    session = client.session
    session["is_staff"] = False
    session.save()

    non_staff_response = client.get("/api/session/")
    assert non_staff_response.status_code == 200
    assert non_staff_response.json()["is_staff"] is False


def test_job_detail_api_includes_staff_metrics_distinct_time_count_for_staff():
  """Staff job detail JSON includes sample-count field (SPA Job Detail page; no DB)."""
  from concurrent.futures import ThreadPoolExecutor
  from contextlib import ExitStack
  from django.test import RequestFactory
  from unittest.mock import patch

  from hpcperfstats.site.lib.machine import api
  from hpcperfstats.site.lib.machine.tests.test_job_detail_staff_sample_count import (
      _patch_job_detail_for_staff_count,
  )

  jid = "e2e-staff-metrics-distinct-jid"
  factory = RequestFactory()
  request = factory.get(f"/api/jobs/{jid}/")
  request.session = {"username": "e2e-user", "is_staff": True}

  ctx = _patch_job_detail_for_staff_count(api, jid, 42_000)
  with ThreadPoolExecutor(max_workers=4) as executor:
    with ExitStack() as stack:
      stack.enter_context(patch.object(api, "_get_small_executor", return_value=executor))
      for cm in ctx:
        stack.enter_context(cm)
      response = api.job_detail(request, jid)

  assert response.status_code == 200
  assert response.data["staff_metrics_distinct_time_count"] == 42_000


@pytest.mark.django_db
def test_job_list_api_exposes_sample_count_only_for_staff():
  """Staff sessions receive sample_count in job list rows; non-staff do not."""
  client = Client()
  now = timezone.now()
  job = job_data.objects.create(
      jid="e2e-job-list-sample-count",
      submit_time=now,
      start_time=now,
      end_time=now,
      runtime=60.0,
      username="webtest-user",
      host_list=["n1.example.com"],
      metrics_distinct_time_count=321,
  )

  with patch(
      "hpcperfstats.site.lib.machine.api._build_job_list_queryset_from_request",
      return_value=(job_data.objects.filter(pk=job.pk).order_by("pk"), {}, None, "-end_time"),
  ):
    session = client.session
    session["access_token"] = "token"
    session["username"] = "webtest-user"
    session["is_staff"] = True
    session.save()
    cache.clear()
    staff_response = client.get("/api/jobs/")
    assert staff_response.status_code == 200
    assert staff_response.json()["job_list"][0]["sample_count"] == 321

    session = client.session
    session["is_staff"] = False
    session.save()
    cache.clear()
    non_staff_response = client.get("/api/jobs/")
    assert non_staff_response.status_code == 200
    assert "sample_count" not in non_staff_response.json()["job_list"][0]
