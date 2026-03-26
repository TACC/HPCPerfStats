"""Custom DRF renderers. Sanitize NaN/Inf so JSON responses are compliant."""
import math
from rest_framework.renderers import JSONRenderer


def _sanitize_float(value):
    """Return None for nan/inf so JSON encoding does not raise."""
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def sanitize_for_json(obj):
    """Recursively replace float nan/inf with None in dicts, lists, and values."""
    if isinstance(obj, dict):
        return {k: sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize_for_json(item) for item in obj]
    if isinstance(obj, float):
        return _sanitize_float(obj)
    return obj


class SafeJSONRenderer(JSONRenderer):
    """JSONRenderer that converts float nan/inf to null for JSON compliance."""

    def render(self, data, accepted_media_type=None, renderer_context=None):
        data = sanitize_for_json(data)
        return super().render(data, accepted_media_type, renderer_context)
