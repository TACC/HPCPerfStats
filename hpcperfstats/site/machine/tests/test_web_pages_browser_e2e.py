"""Browser-driven end-to-end tests for web page flows."""

import threading
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from wsgiref.simple_server import make_server

import pytest
from django.conf import settings
from django.core.wsgi import get_wsgi_application

try:
  from playwright.sync_api import sync_playwright
except ModuleNotFoundError:
  pytest.skip("playwright is required for browser E2E tests", allow_module_level=True)


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
<html lang="en">
<head><title>HPCPerfStats SPA</title></head>
<body>
<main id="machine-spa-stub-main">
  <div id="root">spa-shell</div>
  <div id="staff-actions-root" hidden>
    <button type="button" id="staff-actions-toggle" aria-label="Staff actions" aria-expanded="false" aria-haspopup="true">
      Staff actions
    </button>
    <ul id="staff-actions-menu" role="menu" hidden style="list-style:none;padding-left:0;margin:0;">
      <li><button type="button" role="menuitem" class="staff-menu-item" data-action="job_monitor">Job Failure Monitor</button></li>
      <li><button type="button" role="menuitem" class="staff-menu-item" data-action="admin_monitor">HPCPerfStats Monitor</button></li>
      <li><button type="button" role="menuitem" class="staff-menu-item" data-action="drop_staff">Disable Staff Permissions</button></li>
      <li><button type="button" role="menuitem" class="staff-menu-item" data-action="invalidate_cache">Invalidate Cache For Page</button></li>
    </ul>
  </div>
  <div id="staff-message"></div>
  <div id="plot-unavailable">Plot not available</div>
  <button type="button" id="show-plot-error-details" hidden>Show plot error details</button>
  <button id="copy-error-detail-btn" type="button" hidden>Copy error detail</button>
  <script>
    (function () {
      if (location.pathname.indexOf("/machine/api-key") !== -1) {
        document.getElementById("root").innerHTML = [
          '<main id="api-key-main">',
          "<h1>HPCPerfStats API key</h1>",
          '<a href="/machine/" class="link-primary">Back to HPCPerfStats</a>',
          "<p>Signed in as: <strong>stub-user</strong></p>",
          "<p>This key is shown only once.</p>",
          '<code id="api-key-value">raw-new-api-key</code>',
          '<button type="button" id="copy-api-key">Copy</button>',
          "</main>",
        ].join("");
        return;
      }
      const params = new URLSearchParams(window.location.search);
      const isStaff = params.get("staff") === "1";
      const staffRoot = document.getElementById("staff-actions-root");
      const staffToggle = document.getElementById("staff-actions-toggle");
      const staffMenu = document.getElementById("staff-actions-menu");
      const staffMessage = document.getElementById("staff-message");
      const showPlotErrorDetailsBtn = document.getElementById("show-plot-error-details");
      const copyErrorDetailBtn = document.getElementById("copy-error-detail-btn");

      function setStaffUi(flag) {
        const shouldShow = !!flag;
        staffRoot.hidden = !shouldShow;
        showPlotErrorDetailsBtn.hidden = !shouldShow;
        copyErrorDetailBtn.hidden = !shouldShow;
        staffMenu.hidden = true;
        staffToggle.setAttribute("aria-expanded", "false");
      }

      setStaffUi(isStaff);
      staffToggle.addEventListener("click", function () {
        const open = staffMenu.hidden;
        staffMenu.hidden = !open;
        staffToggle.setAttribute("aria-expanded", open ? "true" : "false");
      });
      staffMenu.querySelectorAll(".staff-menu-item").forEach(function (btn) {
        btn.addEventListener("click", function () {
          if (btn.getAttribute("data-action") === "drop_staff") {
            setStaffUi(false);
            staffMessage.textContent =
              "Staff access removed for this session. Log out and log back in to restore staff access.";
          }
          staffMenu.hidden = true;
          staffToggle.setAttribute("aria-expanded", "false");
        });
      });
    })();
  </script>
</main>
</body>
</html>""",
        encoding="utf-8",
    )
    with patch.object(settings, "STATICFILES_DIRS", (tmpdir,)), patch.object(
        settings,
        "ALLOWED_HOSTS",
        [*getattr(settings, "ALLOWED_HOSTS", []), "127.0.0.1", "localhost"],
    ):
      with _temporary_wsgi_server() as base_url:
        with sync_playwright() as playwright:
          browser = playwright.chromium.launch(headless=True)
          page = browser.new_page()

          page.goto(f"{base_url}/")
          assert "/machine/" in page.url
          assert "spa-shell" in page.locator("#root").inner_text()

          # Staff-only controls appear for staff sessions (mirrors SPA staff menu).
          page.goto(f"{base_url}/machine/?staff=1")
          assert page.locator("#staff-actions-root").is_visible()
          page.get_by_role("button", name="Staff actions").click()
          menuitem_labels = page.eval_on_selector_all(
              "#staff-actions-menu [role='menuitem']",
              "els => els.map((el) => el.textContent.trim())",
          )
          assert menuitem_labels == [
              "Job Failure Monitor",
              "HPCPerfStats Monitor",
              "Disable Staff Permissions",
              "Invalidate Cache For Page",
          ]
          assert page.get_by_text("Plot not available").is_visible()
          assert page.get_by_role("button", name="Show plot error details").is_visible()
          assert page.get_by_role("button", name="Copy error detail").is_visible()

          # Staff-only controls are absent for non-staff sessions.
          page.goto(f"{base_url}/machine/?staff=0")
          assert page.locator("#staff-actions-root").is_hidden()
          assert page.get_by_text("Plot not available").is_visible()
          assert page.get_by_role("button", name="Show plot error details").is_hidden()
          assert page.get_by_role("button", name="Copy error detail").is_hidden()

          # Demoting staff hides controls and shows the informational message.
          page.goto(f"{base_url}/machine/?staff=1")
          page.get_by_role("button", name="Staff actions").click()
          page.get_by_role("menuitem", name="Disable Staff Permissions").click()
          assert page.locator("#staff-actions-root").is_hidden()
          assert "Staff access removed for this session." in page.locator(
              "#staff-message"
          ).inner_text()

          for path in (
              "/machine/home/",
              "/machine/jobs/",
              "/machine/job/123/",
              "/machine/job/123/cpu/",
              "/machine/year/2020/",
              "/machine/host/node1/plot/",
              "/machine/admin_monitor/",
              "/machine/job_monitor/",
          ):
            page.goto(f"{base_url}{path}")
            assert "spa-shell" in page.locator("#root").inner_text()

          # Job detail deep link with staff query still serves SPA shell (staff-only
          # job diagnostics come from JSON; see test_job_detail_staff_sample_count).
          page.goto(f"{base_url}/machine/job/123/?staff=1")
          assert "spa-shell" in page.locator("#root").inner_text()

          page.goto(f"{base_url}/robots.txt")
          robots_text = page.locator("body").inner_text()
          assert "User-agent: *" in robots_text
          assert "Disallow: /" in robots_text

          page.goto(f"{base_url}/machine/api-key")
          body_text = page.locator("body").inner_text()
          assert "HPCPerfStats API key" in body_text
          assert "This key is shown only once." in body_text
          assert "raw-new-api-key" in body_text
          assert page.get_by_role("link", name="Back to HPCPerfStats").is_visible()

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
