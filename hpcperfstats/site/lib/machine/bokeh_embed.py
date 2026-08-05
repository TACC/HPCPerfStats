"""
Small Bokeh figure helpers for SPA-embedded machine pages.
"""
from __future__ import annotations

from typing import Any

from bokeh.plotting import figure

from hpcperfstats.analysis.metrics.lib.gen.utils import set_linear_axes_plain_numeric


def new_spa_embedded_figure(
  *,
  width: Any,
  height: Any,
  title: Any | None = None,
  **kwargs: Any,
) -> Any:
    """
    Return an embed-safe Bokeh figure with toolbar/tools disabled.
    
    Args:
      width (Any): Width passed to this helper.
      height (Any): Height passed to this helper.
      title (Any | None): One of ``Any``, ``None``.
      **kwargs (Any): Extra keyword arguments forwarded to the wrapped API;
      keys and value types match that callee's signature.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> new_spa_embedded_figure(None, None, None)  # doctest: +SKIP
    """
    plot = figure(
        width=width,
        height=height,
        title=title,
        toolbar_location=None,
        tools=[],
        output_backend="canvas",
        **kwargs,
    )
    set_linear_axes_plain_numeric(plot)
    return plot
