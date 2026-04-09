"""Phase 2: job detail SPA + plots API against live gunicorn."""
from __future__ import annotations

import os
import re

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


@pytest.mark.django_db(databases=[])
def test_job_detail_renders_and_summary_plot_payload():
  if os.environ.get("HPCPERFSTATS_COMPOSE_NETWORK", "").strip() != "1":
    pytest.skip("Compose-only (live web).")
  if sync_playwright is None:
    pytest.skip("playwright is required")

  base = _base_url()
  raw = _raw_key()
  jid = PIPELINE_E2E_JID

  with sync_playwright() as p:
    request = p.request.new_context(
        base_url=base,
        extra_http_headers={"X-API-Key": raw},
    )
    request.get("/api/session/")
    for plot_kind in ("summary_plot", "heatmap", "roofline", "gpu_roofline"):
      plots_resp = request.get(
          "/api/jobs/{}/plots/?plot={}".format(jid, plot_kind),
      )
      assert plots_resp.status == 200, plots_resp.text
      assert not re.search(r"\b\d+(?:\.\d+)?[eE][+-]?\d+\b", plots_resp.text())
      payload = plots_resp.json()
      # API may return either a nested object keyed by plot kind, or a
      # direct single-plot payload with {plot, plot_item, unavailable_reason}.
      section = payload.get(plot_kind) or payload
      assert section.get("plot_item") is not None or section.get(
          "unavailable_reason",
      ), payload
    detail_resp = request.get("/api/jobs/{}/".format(jid))
    assert detail_resp.status == 200, detail_resp.text
    assert not re.search(r"\b\d+(?:\.\d+)?[eE][+-]?\d+\b", detail_resp.text())
    request.dispose()

  with sync_playwright() as p:
    browser = p.chromium.launch()
    context = browser.new_context()
    page = context.new_page()

    def add_api_key(route):
      h = dict(route.request.headers)
      h["X-API-Key"] = raw
      route.continue_(headers=h)

    page.route("**/api/**", add_api_key)
    resp = page.goto(
        "{}/machine/job/{}/".format(base, jid),
        wait_until="domcontentloaded",
        timeout=180000,
    )
    assert resp is not None
    assert 200 <= resp.status <= 399
    assert "/machine/job/{}/".format(jid) in page.url
    # Async plot embed; WCAG-tagged axe rules (playwright_axe).
    assert_no_serious_axe_violations(page, wait_ms=750)
    context.close()
    browser.close()
