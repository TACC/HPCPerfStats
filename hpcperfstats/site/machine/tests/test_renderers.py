"""Tests for SafeJSONRenderer and sanitize_for_json."""

import json
import math

from hpcperfstats.site.machine.renderers import SafeJSONRenderer, sanitize_for_json


def test_sanitize_for_json_replaces_nan_inf():
  payload = {"x": float("nan"), "y": float("inf"), "z": 1.0, "n": [math.nan, 2.0]}
  out = sanitize_for_json(payload)
  assert out["x"] is None
  assert out["y"] is None
  assert out["z"] == 1.0
  assert out["n"][0] is None
  assert out["n"][1] == 2.0


def test_safe_json_renderer_encodes_sanitized_payload():
  renderer = SafeJSONRenderer()
  raw = renderer.render({"a": float("nan")}, renderer_context={})
  data = json.loads(raw.decode("utf-8"))
  assert data["a"] is None
