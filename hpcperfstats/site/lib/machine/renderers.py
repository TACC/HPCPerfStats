"""
Custom DRF renderers. Sanitize NaN/Inf so JSON responses are compliant.
"""
from __future__ import annotations

from typing import Any

import math
from rest_framework.renderers import JSONRenderer


def _sanitize_float(value: Any) -> Any:
    """
    Return None for nan/inf so JSON encoding does not raise.
    
    Args:
      value (Any): Value to inspect (typically a numeric scalar).
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _sanitize_float(None)  # doctest: +SKIP
    """
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def sanitize_for_json(obj: Any) -> Any:
    """
    Recursively replace float nan/inf with None in dicts, lists, and values.
    
    Args:
      obj (Any): Value to inspect (typically a numeric scalar).
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> sanitize_for_json(None)  # doctest: +SKIP
    """
    if isinstance(obj, dict):
        return {k: sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize_for_json(item) for item in obj]
    if isinstance(obj, tuple):
        # Bokeh ColumnDataSource map ``entries`` are often list-of-tuples; some
        # encoders mishandle tuples vs lists. Normalize to lists for JSON output.
        return [sanitize_for_json(item) for item in obj]
    if isinstance(obj, float):
        return _sanitize_float(obj)
    return obj


class SafeJSONRenderer(JSONRenderer):
    """
    JSONRenderer that converts float nan/inf to null for JSON compliance.
    """

    def render(
      self,
      data: Any,
      accepted_media_type: Any | None = None,
      renderer_context: Any | None = None,
    ) -> Any:
        """
        Render the response body.
        
        Args:
          data (Any): Value to inspect (typically a numeric scalar).
          accepted_media_type (Any | None): One of ``Any``, ``None``.
          renderer_context (Any | None): One of ``Any``, ``None``.
        
        Returns:
          Any: Value produced by this call (type depends on inputs).
        
        Examples:
          >>> SafeJSONRenderer().render(None, None, None)  # doctest: +SKIP
        """
        data = sanitize_for_json(data)
        return super().render(data, accepted_media_type, renderer_context)
