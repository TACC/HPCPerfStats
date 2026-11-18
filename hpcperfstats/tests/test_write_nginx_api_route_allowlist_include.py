"""Drift tests for nginx API route allowlist generation."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

from hpcperfstats.tests.urlconf_route_catalog import EXPECTED_ROUTE_TEMPLATES


def _repo_root() -> Path:
  return Path(__file__).resolve().parents[2]


def _load_writer():
  path = (
    _repo_root()
    / "services-conf"
    / "write_nginx_api_route_allowlist_include.py"
  )
  name = "write_nginx_api_route_allowlist_include"
  spec = importlib.util.spec_from_file_location(name, path)
  assert spec and spec.loader
  mod = importlib.util.module_from_spec(spec)
  sys.modules[name] = mod
  spec.loader.exec_module(mod)
  return mod


def test_location_heads_exact_and_anchored_regex():
  mod = _load_writer()
  heads = mod.location_heads_from_api_templates(
    [
      "api/jobs/",
      "api/jobs/<str:pk>/",
      "api/jobs/<str:pk>/plots/",
      "api/jobs/<int:n>/x.json",
      "api/jobs/filter_options/",
      "api/schema/",
      "login/",
    ]
  )
  assert heads == [
    "= /api/jobs/",
    "= /api/jobs/filter_options/",
    "= /api/schema/",
    "~ ^/api/jobs/[0-9]+/x\\.json$",
    "~ ^/api/jobs/[^/]+/$",
    "~ ^/api/jobs/[^/]+/plots/$",
  ]


def test_unknown_converter_raises():
  mod = _load_writer()
  try:
    mod.location_heads_from_api_templates(["api/x/<custom:v>/"])
  except ValueError as exc:
    assert "custom" in str(exc)
  else:
    raise AssertionError("expected ValueError for unknown converter")


def test_empty_templates_raises_on_render():
  mod = _load_writer()
  try:
    mod.render_nginx_api_route_allowlist([])
  except ValueError as exc:
    assert "empty" in str(exc).lower()
  else:
    raise AssertionError("expected ValueError for empty allowlist")


def _location_matches(head, path):
  if head.startswith("= "):
    return path == head[2:]
  return re.fullmatch(head[2:].lstrip("^").rstrip("$"), path) is not None


def test_committed_inc_matches_generator_from_catalog():
  mod = _load_writer()
  committed = (
    _repo_root() / "services-conf" / "nginx-api-route-allowlist.inc"
  ).read_text(encoding="utf-8")
  expected = mod.render_nginx_api_route_allowlist(
    mod.location_heads_from_api_templates(EXPECTED_ROUTE_TEMPLATES)
  )
  assert committed == expected


def test_all_api_catalog_templates_covered_by_locations():
  mod = _load_writer()
  heads = mod.location_heads_from_api_templates(EXPECTED_ROUTE_TEMPLATES)
  api_templates = [t for t in EXPECTED_ROUTE_TEMPLATES if t.startswith("api/")]
  assert heads and api_templates
  for template in api_templates:
    path = "/" + re.sub(r"<[^>]+>", "123", template)
    assert any(_location_matches(h, path) for h in heads), template


def test_probe_paths_under_real_api_routes_not_covered():
  """Regression: ^~ /api/session/ forwarded /api/session/new to Django."""
  mod = _load_writer()
  heads = mod.location_heads_from_api_templates(EXPECTED_ROUTE_TEMPLATES)
  for probe in (
    "/api/.env",
    "/api/session/new",
    "/api/session/properties",
    "/api/schema/x",
    "/api/jobs/1/2/3/",
    "/api/jobs/abc",
  ):
    assert not any(_location_matches(h, probe) for h in heads), probe
  # Positive controls.
  for ok in ("/api/session/", "/api/jobs/abc/", "/api/jobs/abc/plots/"):
    assert any(_location_matches(h, ok) for h in heads), ok


def test_write_include_round_trip(tmp_path):
  mod = _load_writer()
  out = tmp_path / "nginx-api-route-allowlist.inc"
  heads = mod.write_nginx_api_route_allowlist_include(
    templates=EXPECTED_ROUTE_TEMPLATES,
    out_path=out,
  )
  text = out.read_text(encoding="utf-8")
  assert "location = /api/jobs/ {" in text
  assert "location ~ ^/api/jobs/[^/]+/$ {" in text
  assert "proxy_pass http://django_server;" in text
  assert heads == mod.location_heads_from_api_templates(
    EXPECTED_ROUTE_TEMPLATES
  )
