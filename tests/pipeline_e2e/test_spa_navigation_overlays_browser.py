"""SPA navigation and overlay regressions (calendar, staff menu, Extended Search help)."""
from __future__ import annotations

import os
import re

import pytest

try:
  from playwright.sync_api import sync_playwright
except ModuleNotFoundError:
  sync_playwright = None


def _base_url() -> str:
  return os.environ.get(
      "HPCPERFSTATS_PIPELINE_E2E_BASE_URL",
      "http://127.0.0.1:8000",
  ).rstrip("/")


def _raw_key() -> str:
  from .constants import PIPELINE_E2E_API_RAW_KEY

  return os.environ.get(
      "HPCPERFSTATS_PIPELINE_E2E_RAW_API_KEY",
      PIPELINE_E2E_API_RAW_KEY,
  )


@pytest.mark.django_db(databases=[])
def test_calendar_day_link_reaches_job_list():
  if os.environ.get("HPCPERFSTATS_COMPOSE_NETWORK", "").strip() != "1":
    pytest.skip("Compose-only (live web).")
  if sync_playwright is None:
    pytest.skip("playwright is required")

  base = _base_url()
  raw = _raw_key()

  with sync_playwright() as p:
    browser = p.chromium.launch()
    context = browser.new_context()
    page = context.new_page()

    def add_api_key(route):
      headers = dict(route.request.headers)
      headers["X-API-Key"] = raw
      route.continue_(headers=headers)

    page.route("**/api/**", add_api_key)
    home = page.goto(f"{base}/machine/", wait_until="domcontentloaded", timeout=180000)
    assert home is not None and home.status == 200

    day_link = page.get_by_role("link", name=re.compile(r"day 15", re.I))
    if day_link.count() == 0:
      pytest.skip("No calendar day links in home options fixture.")

    day_link.first.click()
    page.wait_for_url(re.compile(r"/machine/date/\d{4}-\d{2}-\d{2}"), timeout=60000)
    assert page.get_by_role("heading", name=re.compile(r"jobs", re.I)).count() > 0

    context.close()
    browser.close()


@pytest.mark.django_db(databases=[])
def test_staff_menu_lists_all_actions():
  if os.environ.get("HPCPERFSTATS_COMPOSE_NETWORK", "").strip() != "1":
    pytest.skip("Compose-only (live web).")
  if sync_playwright is None:
    pytest.skip("playwright is required")

  base = _base_url()
  raw = _raw_key()

  with sync_playwright() as p:
    browser = p.chromium.launch()
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()

    def add_api_key(route):
      headers = dict(route.request.headers)
      headers["X-API-Key"] = raw
      route.continue_(headers=headers)

    page.route("**/api/**", add_api_key)
    resp = page.goto(f"{base}/machine/", wait_until="domcontentloaded", timeout=180000)
    assert resp is not None and resp.status == 200

    page.get_by_role("button", name="Staff actions").click()
    for label in (
        "Job Failure Monitor",
        "HPCPerfStats Monitor",
        "Disable Staff Permissions",
        "Invalidate Cache For Page",
    ):
      item = page.get_by_role("menuitem", name=label)
      assert item.count() == 1
      assert item.first.is_visible()

    context.close()
    browser.close()


@pytest.mark.django_db(databases=[])
def test_extended_search_help_shows_definition():
  if os.environ.get("HPCPERFSTATS_COMPOSE_NETWORK", "").strip() != "1":
    pytest.skip("Compose-only (live web).")
  if sync_playwright is None:
    pytest.skip("playwright is required")

  base = _base_url()
  raw = _raw_key()

  with sync_playwright() as p:
    browser = p.chromium.launch()
    context = browser.new_context()
    page = context.new_page()

    def add_api_key(route):
      headers = dict(route.request.headers)
      headers["X-API-Key"] = raw
      route.continue_(headers=headers)

    page.route("**/api/**", add_api_key)
    resp = page.goto(f"{base}/machine/", wait_until="domcontentloaded", timeout=180000)
    assert resp is not None and resp.status == 200

    page.get_by_role("button", name=re.compile(r"extended search", re.I)).click()
    page.get_by_role("dialog", name=re.compile(r"extended search", re.I)).wait_for(
        timeout=30000,
    )
    page.get_by_role("button", name=re.compile(r"help: host", re.I)).click()
    tooltip = page.get_by_test_id("variable-info-tooltip")
    tooltip.wait_for(timeout=15000)
    assert tooltip.is_visible()
    assert re.search(r"host", tooltip.inner_text(), re.I)

    context.close()
    browser.close()
