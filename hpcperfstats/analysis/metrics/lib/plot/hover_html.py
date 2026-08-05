"""
Shared Bokeh hover HTML snippets for analysis plots.
"""
from __future__ import annotations

from typing import Any


def hover_tooltip_html_host_time_value(
  value_label: Any,
  value_field: Any,
) -> Any:
  """
  Build an HTML hover template with spacing between multi-point hits.
  
  Args:
    value_label (Any): Value label passed to this helper.
    value_field (Any): Value field passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> hover_tooltip_html_host_time_value(None, None)  # doctest: +SKIP
  """
  return f"""
    <div style="padding-bottom:6px; margin-bottom:6px; border-bottom:1px solid #d0d7de;">
      <div><strong>host:</strong> @host</div>
      <div><strong>time:</strong> @_hover_time</div>
      <div><strong>{value_label}:</strong> @{value_field}_plain</div>
    </div>
  """
