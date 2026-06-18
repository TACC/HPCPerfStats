"""Tests for SafeJSONRenderer and sanitize_for_json."""

import json
import math

from hpcperfstats.site.lib.machine.renderers import SafeJSONRenderer, sanitize_for_json


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


def test_sanitize_for_json_converts_tuples_to_lists():
    """Bokeh map ``entries`` are list-of-tuples; lists survive JSON round-trips reliably."""
    payload = {"data": {"type": "map", "entries": [("x", [1]), ("y", [2.0])]}}
    out = sanitize_for_json(payload)
    assert out["data"]["entries"] == [["x", [1]], ["y", [2.0]]]
    raw = SafeJSONRenderer().render(out, renderer_context={})
    data = json.loads(raw.decode("utf-8"))
    assert data["data"]["entries"][0] == ["x", [1]]
