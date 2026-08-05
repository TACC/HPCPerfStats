"""Browser-driven end-to-end tests for Django-served web page flows."""

import threading
from contextlib import contextmanager
from wsgiref.simple_server import make_server

import pytest
from django.core.wsgi import get_wsgi_application

from hpcperfstats.tests.playwright_axe import assert_no_serious_axe_violations
from hpcperfstats.tests.public_robots_js_registry import (
    format_public_robots_txt_body,
    load_public_robots_allow_prefixes,
)

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
  """Exercise browser checks for Django routes after nginx SPA handoff."""
  with _temporary_wsgi_server() as base_url:
    with sync_playwright() as playwright:
      browser = playwright.chromium.launch(headless=True)
      page = browser.new_page()

      # Playwright follows redirects; final response for `/` is `/machine/` (404).
      redirect_probe = page.context.request.get(f"{base_url}/", max_redirects=0)
      assert redirect_probe.status == 302
      location = redirect_probe.headers.get("location") or ""
      assert "/machine/" in location

      root_final = page.goto(f"{base_url}/")
      assert root_final is not None
      assert root_final.status == 404
      assert "/machine/" in page.url

      # Production contract: WSGI no longer serves SPA shell routes.
      machine_response = page.goto(f"{base_url}/machine/")
      assert machine_response is not None
      assert machine_response.status == 404

      for path in (
          "/machine/home/",
          "/machine/jobs/",
          "/machine/job/123/",
          "/machine/job/123/cpu/",
          "/machine/year/2020/",
          "/machine/date/2024-01-15/",
          "/machine/host/node1/plot/",
          "/machine/admin_monitor/",
          "/machine/job_monitor/",
          "/pub/",
          "/pub/cluster-dashboard",
      ):
        response = page.goto(f"{base_url}{path}")
        assert response is not None
        assert response.status == 404

      robots_probe = page.context.request.get(f"{base_url}/robots.txt")
      assert robots_probe.status == 404
      prefixes = load_public_robots_allow_prefixes()
      expected = format_public_robots_txt_body(prefixes)
      assert "User-agent: *" in expected
      for prefix in prefixes:
        assert "Allow: {}".format(prefix) in expected
      assert "Disallow: /" in expected

      csp_probe = page.context.request.post(
          f"{base_url}/csp-report/",
          headers={"Content-Type": "application/csp-report"},
          data='{"csp-report": {"document-uri": "https://example.test"}}',
      )
      assert csp_probe.status == 204

      # Browsers wrap text/plain robots bodies in a shell that fails axe;
      # probe a minimal document instead so WCAG checks still exercise the harness.
      # Navigate to about:blank first so a prior Django HTML 404 CSP (no
      # unsafe-inline) does not block Playwright's axe script injection.
      page.goto("about:blank")
      page.set_content(
          "<!DOCTYPE html><html lang=\"en\"><head>"
          "<meta charset=\"utf-8\"/><title>accessibility probe</title></head>"
          "<body></body></html>",
      )
      assert_no_serious_axe_violations(page)

      browser.close()
