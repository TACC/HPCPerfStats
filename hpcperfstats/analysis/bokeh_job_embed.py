"""Bokeh figure defaults for job-detail plots embedded in the SPA (json_item).

Using stretch_width lets the browser layout control horizontal size; fixed height
keeps vertical rhythm stable in the card grid and zoom view.

Lives under ``analysis`` (not ``analysis.plot``) so callers can import without
pulling in ``plot`` package side effects / Django models at import time.
"""


def figure_embed_kw(height, **kwargs):
  """Merge sizing_mode/height with caller figure() kwargs (caller wins on duplicate keys)."""
  base = {"sizing_mode": "stretch_width", "height": height}
  base.update(kwargs)
  return base
