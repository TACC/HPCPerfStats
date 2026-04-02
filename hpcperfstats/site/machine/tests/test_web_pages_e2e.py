"""End-to-end style web-access tests for all site pages."""

import pytest
from django.test import Client
from django.test import override_settings
from unittest.mock import Mock, patch


@pytest.mark.django_db
class TestWebPagesEndToEnd:
  def test_public_routes_and_spa_pages_are_served(self, tmp_path):
    """Validate top-level pages and SPA deep links via HTTP."""
    client = Client()
    frontend_dir = tmp_path / "frontend"
    frontend_dir.mkdir(parents=True, exist_ok=True)
    (frontend_dir / "index.html").write_text(
        "<!doctype html><html><head><title>HPCPerfStats</title></head>"
        "<body><div id='root'>spa-shell</div></body></html>",
        encoding="utf-8",
    )

    with override_settings(STATICFILES_DIRS=(str(tmp_path),)):
      root_response = client.get("/")
      assert root_response.status_code == 302
      assert root_response["Location"] == "/machine/"

      machine_response = client.get("/machine/")
      assert machine_response.status_code == 200
      machine_html = machine_response.content.decode("utf-8")
      assert "spa-shell" in machine_html

      # React router deep-link pages should still return the SPA shell.
      for path in (
          "/machine/home/",
          "/machine/jobs/",
          "/machine/job/123/",
          "/machine/host/node1/plot/",
          "/machine/admin_monitor/",
      ):
        response = client.get(path)
        assert response.status_code == 200
        assert "spa-shell" in response.content.decode("utf-8")

      robots_response = client.get("/robots.txt")
      assert robots_response.status_code == 200
      assert "User-agent: *" in robots_response.content.decode("utf-8")
      assert "Disallow: /" in robots_response.content.decode("utf-8")

      csp_response = client.post(
          "/csp-report/",
          data='{"csp-report":{"document-uri":"https://example.test"}}',
          content_type="application/csp-report",
      )
      assert csp_response.status_code == 204

  def test_api_key_page_requires_login_then_supports_key_rotation(self):
    """Validate API key page auth gate, render, and key rotation action."""
    client = Client()
    redirect_response = client.get("/api-key/")
    assert redirect_response.status_code == 302
    assert "/login_prompt?next=/api-key/" in redirect_response["Location"]

    session = client.session
    session["access_token"] = "token"
    session["username"] = "webtest-user"
    session["is_staff"] = True
    session.save()

    active_key = Mock()
    active_key.key_prefix = "abc123"
    list_queryset = Mock()
    list_queryset.order_by.return_value.first.return_value = active_key
    rotated_key = Mock()
    rotated_key.key_prefix = "def456"

    with patch("hpcperfstats.site.hpcperfstats_site.views.ApiKey.objects.filter") as mock_filter, patch(
        "hpcperfstats.site.hpcperfstats_site.views.ApiKey.create_from_raw_key",
        return_value=(rotated_key, "raw-new-api-key"),
    ):
      mock_filter.side_effect = [list_queryset, Mock()]

      first_response = client.get("/api-key/")
      assert first_response.status_code == 200
      first_html = first_response.content.decode("utf-8")
      assert "HPCPerfStats API key" in first_html
      assert "Invalidate and Create New Key" in first_html
      assert "Signed in as: <strong>webtest-user</strong>" in first_html
      assert "Active key prefix:" in first_html

      rotate_response = client.post("/api-key/")
      assert rotate_response.status_code == 200
      rotate_html = rotate_response.content.decode("utf-8")
      assert "HPCPerfStats API key" in rotate_html
      assert "This key is shown only once." in rotate_html
      assert "raw-new-api-key" in rotate_html

  def test_staff_can_disable_staff_for_current_session(self):
    """Validate staff-only session demotion endpoint and resulting session state."""
    client = Client()
    session = client.session
    session["access_token"] = "token"
    session["username"] = "webtest-user"
    session["is_staff"] = True
    session.save()

    drop_response = client.post("/api/session/drop-staff/")
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
        "hpcperfstats.site.machine.api._get_cache_stats",
        return_value={"ok": True},
    ):
      staff_admin = client.get("/api/admin_monitor/?section=cache")
      assert staff_admin.status_code == 200
      staff_ingest = client.post(
          "/api/sacct/ingest/?date=2026-01-01",
          data="",
          content_type="text/plain",
      )
      assert staff_ingest.status_code == 200
      staff_invalidate = client.post(
          "/api/cache/invalidate-page/",
          data={"page_path": "/machine/jobs"},
          content_type="application/json",
      )
      assert staff_invalidate.status_code in (200, 503)

      non_staff_session = client.session
      non_staff_session["is_staff"] = False
      non_staff_session.save()

      non_staff_admin = client.get("/api/admin_monitor/?section=cache")
      assert non_staff_admin.status_code == 403
      non_staff_ingest = client.post(
          "/api/sacct/ingest/?date=2026-01-01",
          data="",
          content_type="text/plain",
      )
      assert non_staff_ingest.status_code == 403
      non_staff_invalidate = client.post(
          "/api/cache/invalidate-page/",
          data={"page_path": "/machine/jobs"},
          content_type="application/json",
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

  from hpcperfstats.site.machine import api
  from hpcperfstats.site.machine.tests.test_job_detail_staff_sample_count import (
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
