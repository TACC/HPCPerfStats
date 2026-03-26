"""Browser-driven end-to-end tests for web page flows."""

import threading
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch
from wsgiref.simple_server import make_server

import pytest
from django.conf import settings
from django.core.wsgi import get_wsgi_application
from playwright.sync_api import sync_playwright


@contextmanager
def _temporary_wsgi_server():
  """Run a lightweight local WSGI server for browser navigation."""
  app = get_wsgi_application()
  server = make_server("127.0.0.1", 0, app)
  thread = threading.Thread(target=server.serve_forever, daemon=True)
  thread.start()
  try:
    yield f"http://127.0.0.1:{server.server_port}"
  finally:
    server.shutdown()
    thread.join(timeout=5)


@pytest.mark.django_db
def test_browser_flow_for_web_pages():
  """Exercise page flows through a real headless browser."""
  with tempfile.TemporaryDirectory() as tmpdir:
    frontend_dir = Path(tmpdir) / "frontend"
    frontend_dir.mkdir(parents=True, exist_ok=True)
    (frontend_dir / "index.html").write_text(
        """<!doctype html>
<html>
<head><title>HPCPerfStats SPA</title></head>
<body>
  <div id="root">spa-shell</div>
  <a id="job-monitor-link" href="/machine/job_monitor/" hidden>Job Monitor</a>
  <a id="admin-monitor-link" href="/machine/admin_monitor/" hidden>HPCPerfStats Monitor</a>
  <button id="disable-staff-btn" type="button" hidden>Disable staff for session</button>
  <div id="staff-message"></div>
  <div id="plot-unavailable">Plot not available</div>
  <span id="plot-error-detail" hidden>Error Detail</span>
  <button id="copy-error-detail-btn" type="button" hidden>Copy Error Detail</button>
  <script>
    (function () {
      const params = new URLSearchParams(window.location.search);
      const isStaff = params.get("staff") === "1";
      const jobMonitorLink = document.getElementById("job-monitor-link");
      const adminMonitorLink = document.getElementById("admin-monitor-link");
      const disableStaffBtn = document.getElementById("disable-staff-btn");
      const staffMessage = document.getElementById("staff-message");
      const plotErrorDetail = document.getElementById("plot-error-detail");
      const copyErrorDetailBtn = document.getElementById("copy-error-detail-btn");

      function setStaffUi(flag) {
        const shouldShow = !!flag;
        jobMonitorLink.hidden = !shouldShow;
        adminMonitorLink.hidden = !shouldShow;
        disableStaffBtn.hidden = !shouldShow;
        plotErrorDetail.hidden = !shouldShow;
        copyErrorDetailBtn.hidden = !shouldShow;
      }

      setStaffUi(isStaff);
      disableStaffBtn.addEventListener("click", function () {
        setStaffUi(false);
        staffMessage.textContent =
          "Staff access removed for this session. Log out and log back in to restore staff access.";
      });
    })();
  </script>
</body>
</html>""",
        encoding="utf-8",
    )
    active_key = Mock()
    active_key.key_prefix = "abc123"
    list_queryset = Mock()
    list_queryset.order_by.return_value.first.return_value = active_key
    update_queryset = Mock()
    update_queryset.update.return_value = 1
    rotated_key = Mock()
    rotated_key.key_prefix = "def456"

    with patch(
        "hpcperfstats.site.hpcperfstats_site.views.check_for_tokens",
        return_value=True,
    ), patch(
        "hpcperfstats.site.hpcperfstats_site.views.ApiKey.objects.filter"
    ) as mock_filter, patch(
        "hpcperfstats.site.hpcperfstats_site.views.ApiKey.create_from_raw_key",
        return_value=(rotated_key, "raw-new-api-key"),
    ), patch.object(settings, "STATICFILES_DIRS", (tmpdir,)), patch.object(
        settings,
        "ALLOWED_HOSTS",
        [*getattr(settings, "ALLOWED_HOSTS", []), "127.0.0.1", "localhost"],
    ):
      mock_filter.side_effect = [list_queryset, update_queryset]

      with _temporary_wsgi_server() as base_url:
        with sync_playwright() as playwright:
          browser = playwright.chromium.launch(headless=True)
          page = browser.new_page()

          page.goto(f"{base_url}/")
          assert "/machine/" in page.url
          assert "spa-shell" in page.locator("#root").inner_text()

          # Staff-only controls appear for staff sessions.
          page.goto(f"{base_url}/machine/?staff=1")
          assert page.get_by_role("link", name="Job Monitor").is_visible()
          assert page.get_by_role("link", name="HPCPerfStats Monitor").is_visible()
          assert page.get_by_role("button", name="Disable staff for session").is_visible()
          assert page.get_by_text("Plot not available").is_visible()
          assert page.locator("#plot-error-detail").is_visible()
          assert page.get_by_role("button", name="Copy Error Detail").is_visible()

          # Staff-only controls are absent for non-staff sessions.
          page.goto(f"{base_url}/machine/?staff=0")
          assert page.locator("#job-monitor-link").is_hidden()
          assert page.locator("#admin-monitor-link").is_hidden()
          assert page.locator("#disable-staff-btn").is_hidden()
          assert page.get_by_text("Plot not available").is_visible()
          assert page.locator("#plot-error-detail").is_hidden()
          assert page.locator("#copy-error-detail-btn").is_hidden()

          # Demoting staff hides controls and shows the informational message.
          page.goto(f"{base_url}/machine/?staff=1")
          page.get_by_role("button", name="Disable staff for session").click()
          assert page.locator("#job-monitor-link").is_hidden()
          assert page.locator("#admin-monitor-link").is_hidden()
          assert page.locator("#disable-staff-btn").is_hidden()
          assert "Staff access removed for this session." in page.locator(
              "#staff-message"
          ).inner_text()

          for path in (
              "/machine/home/",
              "/machine/jobs/",
              "/machine/job/123/",
              "/machine/host/node1/plot/",
              "/machine/admin_monitor/",
          ):
            page.goto(f"{base_url}{path}")
            assert "spa-shell" in page.locator("#root").inner_text()

          page.goto(f"{base_url}/robots.txt")
          robots_text = page.locator("body").inner_text()
          assert "User-agent: *" in robots_text
          assert "Disallow: /" in robots_text

          page.goto(f"{base_url}/api-key/")
          assert "HPCPerfStats API key" in page.locator("body").inner_text()
          page.click("button[type='submit']")
          page.wait_for_load_state("domcontentloaded")
          assert "This key is shown only once." in page.locator("body").inner_text()
          assert "raw-new-api-key" in page.locator("body").inner_text()

          status_code = page.evaluate(
              """async (baseUrl) => {
                const response = await fetch(`${baseUrl}/csp-report/`, {
                  method: "POST",
                  headers: {"Content-Type": "application/csp-report"},
                  body: JSON.stringify({"csp-report": {"document-uri": "https://example.test"}}),
                });
                return response.status;
              }""",
              base_url,
          )
          assert status_code == 403

          browser.close()
