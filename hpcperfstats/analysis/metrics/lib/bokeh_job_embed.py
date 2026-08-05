"""
Bokeh figure defaults for job-detail plots embedded in the SPA (json_item).

Using stretch_width lets the browser layout control horizontal size; fixed
height keeps vertical rhythm stable in the card grid and zoom view.

Lives under ``analysis`` (not ``analysis.plot``) so callers can import without
pulling in ``plot`` package side effects / Django models at import time.
"""
from __future__ import annotations

from typing import Any


def figure_embed_kw(height: Any, **kwargs: Any) -> Any:
  """
  Merge sizing_mode/height with caller figure() kwargs (caller wins on.
  
    duplicate.
  
    keys).
  
  Args:
    height (Any): Height passed to this helper.
    **kwargs (Any): Extra keyword arguments forwarded to the wrapped API; keys
    and value types match that callee's signature.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> figure_embed_kw(None)  # doctest: +SKIP
  """
  base = {"sizing_mode": "stretch_width", "height": height}
  base.update(kwargs)
  return base
