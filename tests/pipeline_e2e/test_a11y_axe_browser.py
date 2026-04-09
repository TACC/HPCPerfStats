"""Compose-only axe scans on curated SPA routes (subset of the matrix)."""

from __future__ import annotations

import os

import pytest

try:
  from playwright.sync_api import sync_playwright
except ModuleNotFoundError:
  sync_playwright = None

from hpcperfstats.tests.playwright_axe import assert_no_serious_axe_violations

from .constants import PIPELINE_E2E_API_RAW_KEY, PIPELINE_E2E_JID


def _base_url() -> str:
  return os.environ.get(
      "HPCPERFSTATS_PIPELINE_E2E_BASE_URL",
      "http://127.0.0.1:8000",
  ).rstrip("/")


def _raw_key() -> str:
  return os.environ.get(
      "HPCPERFSTATS_PIPELINE_E2E_RAW_API_KEY",
      PIPELINE_E2E_API_RAW_KEY,
  )


# Primary UX surfaces; extend for new top-level a11y-gated flows.
_AXE_SMOKE_PATHS = (
    "/machine/",
    "/machine/jobs/",
    "/machine/home/",
)


@pytest.mark.django_db(databases=[])
def test_axe_wcag_smoke_selected_routes():
  if os.environ.get("HPCPERFSTATS_COMPOSE_NETWORK", "").strip() != "1":
    pytest.skip("Compose-only (live web).")
  if sync_playwright is None:
    pytest.skip("playwright is required")

  base = _base_url()
  raw = _raw_key()
  jid = PIPELINE_E2E_JID
  paths = (*_AXE_SMOKE_PATHS, "/machine/job/{}/".format(jid))

  with sync_playwright() as p:
    browser = p.chromium.launch()
    context = browser.new_context()
    page = context.new_page()

    def add_api_key(route):
      h = dict(route.request.headers)
      h["X-API-Key"] = raw
      route.continue_(headers=h)

    page.route("**/api/**", add_api_key)

    for path in paths:
      resp = page.goto(
          base + path,
          wait_until="domcontentloaded",
          timeout=120000,
      )
      assert resp is not None, path
      assert 200 <= resp.status <= 399, (path, resp.status)
      assert_no_serious_axe_violations(page, wait_ms=250)

    context.close()
    browser.close()
