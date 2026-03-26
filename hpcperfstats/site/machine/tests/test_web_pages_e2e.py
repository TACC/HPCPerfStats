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
