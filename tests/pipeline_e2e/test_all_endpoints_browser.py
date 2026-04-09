"""Phase 2: every resolver template → Playwright (goto or API) + status/Content-Type."""
from __future__ import annotations

import os
from datetime import timedelta, timezone as dt_timezone

import pytest
from django.utils import timezone as django_timezone

try:
  from playwright.sync_api import sync_playwright
except ModuleNotFoundError:
  sync_playwright = None

import hpcperfstats.conf_parser as cfg
from hpcperfstats.tests.urlconf_route_catalog import (
    PipelineHttpEndpointSpec,
    build_pipeline_http_endpoint_specs,
)

from .constants import (
    PIPELINE_E2E_API_RAW_KEY,
    PIPELINE_E2E_HOST_SHORT,
    PIPELINE_E2E_JID,
    PIPELINE_E2E_USERNAME,
)


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


def _fqdn() -> str:
  ext = cfg.get_host_name_ext().strip().lstrip(".")
  return "{}.{}".format(PIPELINE_E2E_HOST_SHORT, ext)


def _assert_content_type_band(resp, spec: PipelineHttpEndpointSpec):
  if spec.content_type_substring is None:
    return
  ct = (resp.headers.get("content-type") or "").lower()
  assert spec.content_type_substring.lower() in ct, (spec.path, ct, spec)


def _assert_job_list_histograms_payload(resp, spec: PipelineHttpEndpointSpec):
  if spec.path != "/api/jobs/histograms/":
    return
  payload = resp.json()
  assert payload.get("group") == "queue", payload
  plots = payload.get("plots")
  assert isinstance(plots, list), payload
  expected_keys = {"jobs_by_queue", "cpu_hours_by_queue"}
  actual_keys = {plot.get("key") for plot in plots}
  assert expected_keys.issubset(actual_keys), payload
  for plot in plots:
    if plot.get("key") not in expected_keys:
      continue
    assert plot.get("plot_item_thumb") is not None, payload
    assert plot.get("plot_item_full") is not None, payload
    assert plot.get("plot_unavailable_reason") is None, payload


@pytest.mark.django_db(databases=[])
def test_every_configured_http_endpoint_smoke():
  if os.environ.get("HPCPERFSTATS_COMPOSE_NETWORK", "").strip() != "1":
    pytest.skip("Compose-only (live web + DB).")
  if sync_playwright is None:
    pytest.skip("playwright is required")

  base = _base_url()
  raw = _raw_key()
  jid = PIPELINE_E2E_JID
  fqdn = _fqdn()
  now = django_timezone.now()
  if now.tzinfo is None:
    now = now.replace(tzinfo=dt_timezone.utc)
  gte = (now - timedelta(days=1)).isoformat()
  lte = (now + timedelta(hours=1)).isoformat()

  specs = build_pipeline_http_endpoint_specs(
      jid=jid,
      username=PIPELINE_E2E_USERNAME,
      fqdn=fqdn,
      time_gte_iso=gte,
      time_lte_iso=lte,
  )
  goto_specs = [s for s in specs if s.use_playwright_page_goto]
  api_specs = [s for s in specs if not s.use_playwright_page_goto]

  with sync_playwright() as p:
    browser = p.chromium.launch()
    context = browser.new_context()
    page = context.new_page()

    def add_api_key(route):
      h = dict(route.request.headers)
      h["X-API-Key"] = raw
      route.continue_(headers=h)

    page.route("**/api/**", add_api_key)

    for spec in goto_specs:
      if spec.method != "GET":
        continue
      resp = page.goto(
          base + spec.path,
          wait_until="domcontentloaded",
          timeout=120000,
      )
      assert resp is not None
      assert spec.ok_status_min <= resp.status <= spec.ok_status_max, (
          spec.route_template,
          spec.path,
          resp.status,
      )
      _assert_content_type_band(resp, spec)

    context.close()
    browser.close()

  with sync_playwright() as p:
    request = p.request.new_context(
        base_url=base,
        extra_http_headers={"X-API-Key": raw},
    )
    request.get("/machine/")
    r0 = request.get("/api/session/")
    assert 200 <= r0.status < 300, r0.text

    def cookies_dict():
      return {
          c["name"]: c["value"]
          for c in request.storage_state().get("cookies", [])
      }

    def post_with_csrf(spec: PipelineHttpEndpointSpec):
      cookies = cookies_dict()
      headers = dict(spec.extra_headers or {})
      headers.setdefault("X-CSRFToken", cookies.get("csrftoken", ""))
      headers.setdefault("Referer", base + "/machine/")
      return request.post(
          spec.path,
          headers=headers,
          data=spec.post_data or "",
      )

    for spec in api_specs:
      if spec.method == "GET":
        resp = request.get(spec.path)
      else:
        if spec.csrf_post:
          resp = post_with_csrf(spec)
        else:
          resp = request.post(
              spec.path,
              headers=spec.extra_headers or {},
              data=spec.post_data or "",
          )
      assert spec.ok_status_min <= resp.status <= spec.ok_status_max, (
          spec.method,
          spec.path,
          resp.status,
          resp.text()[:500] if hasattr(resp, "text") else "",
      )
      _assert_content_type_band(resp, spec)
      _assert_job_list_histograms_payload(resp, spec)

    request.dispose()
