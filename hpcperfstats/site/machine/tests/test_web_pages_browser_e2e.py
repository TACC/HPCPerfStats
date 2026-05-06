"""Browser-driven end-to-end tests for Django-served web page flows."""

import threading
from contextlib import contextmanager
from wsgiref.simple_server import make_server

import pytest
from django.core.wsgi import get_wsgi_application

from hpcperfstats.tests.playwright_axe import assert_no_serious_axe_violations
from hpcperfstats.site.hpcperfstats_site.public_robots_allow_paths import (
    PUBLIC_ROBOTS_ALLOW_PREFIXES,
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

      root_response = page.goto(f"{base_url}/")
      assert root_response is not None
      assert root_response.status == 302
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
          "/pub/",
          "/pub/monthly-metrics",
      ):
        response = page.goto(f"{base_url}{path}")
        assert response is not None
        assert response.status == 404

      page.goto(f"{base_url}/robots.txt")
      robots_text = page.locator("body").inner_text()
      assert "User-agent: *" in robots_text
      assert "Disallow: /" in robots_text
      for prefix in PUBLIC_ROBOTS_ALLOW_PREFIXES:
        assert "Allow: {}".format(prefix) in robots_text
      assert_no_serious_axe_violations(page)

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
