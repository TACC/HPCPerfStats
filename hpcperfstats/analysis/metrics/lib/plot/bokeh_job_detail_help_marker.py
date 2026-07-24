"""Shared Bokeh help marker (blue ``?`` + HTML hover) for Job Detail figures.

Uses a **screen-space** Label for the glyph so it does not compete with data
coordinates, and an invisible **rect** hit target sized in **screen pixels** with
its center placed just inside the data-area corner (linear or log ranges).
"""

from __future__ import annotations

import html
import math
from typing import Any, Optional

from bokeh.models import ColumnDataSource, HoverTool, Label, LogScale


def _span_x_numeric(plot: Any):
  xs, xe = plot.x_range.start, plot.x_range.end
  span = xe - xs
  if hasattr(span, "total_seconds"):
    from pandas import Timedelta

    if span.total_seconds() == 0:
      span = Timedelta(seconds=60)
  else:
    try:
      if float(span) == 0.0:
        span = 1.0
    except (TypeError, ValueError):
      from pandas import Timedelta

      span = Timedelta(seconds=60)
  return xs, xe, span


def _span_y_numeric(plot: Any):
  ys, ye = plot.y_range.start, plot.y_range.end
  span = ye - ys
  if span == 0.0:
    span = 1.0
  return ys, ye, span


def _corner_hit_xy(plot: Any, frac_x: float = 0.05, frac_y: float = 0.12):
  """Return (hx, hy) in **data** coordinates near the top-right for hover hit."""
  xs, xe, span_x = _span_x_numeric(plot)
  ys, ye, span_y = _span_y_numeric(plot)
  x_scale = getattr(plot, "x_scale", None)
  y_scale = getattr(plot, "y_scale", None)

  if isinstance(x_scale, LogScale):
    lx = math.log10(max(float(xs), 1e-300))
    rx = math.log10(max(float(xe), 1e-300))
    hx = 10 ** (rx - frac_x * (rx - lx))
  else:
    hx = xe - frac_x * span_x

  if isinstance(y_scale, LogScale):
    ly = math.log10(max(float(ys), 1e-300))
    ry = math.log10(max(float(ye), 1e-300))
    hy = 10 ** (ry - frac_y * (ry - ly))
  else:
    hy = ye - frac_y * span_y
  return hx, hy


def add_job_detail_bokeh_help_marker(
    plot: Any,
    description: str,
    researcher_use: Optional[str] = None,
) -> None:
  """Add a top-right ``?`` with HTML hover; safe to call with empty ``description``."""
  if not description or not str(description).strip():
    return
  desc_str = str(description).strip()
  ru_str = (
      str(researcher_use).strip()
      if researcher_use is not None and str(researcher_use).strip()
      else ""
  )
  inner = html.escape(desc_str)
  if ru_str:
    inner += (
        '<hr style="margin:0.5em 0;border:0;'
        'border-top:1px solid rgba(0,0,0,0.12);"/>'
        f'<span style="color:#333;">{html.escape(ru_str)}</span>'
    )
  tip = (
      '<div style="max-width:28em; white-space:normal; font-weight:400;">'
      f"{inner}"
      "</div>"
  )

  hx, hy = _corner_hit_xy(plot)
  hit_src = ColumnDataSource(data={"hx": [hx], "hy": [hy]})
  hit = plot.rect(
      x="hx",
      y="hy",
      width=32,
      height=28,
      width_units="screen",
      height_units="screen",
      source=hit_src,
      fill_alpha=0.0,
      line_alpha=0.0,
      level="overlay",
  )
  plot.add_tools(HoverTool(renderers=[hit], tooltips=tip))

  # Data-space Label at the same corner as the hit target (screen x=-10 was
  # clipped off the left edge after stretch_width embeds).
  lab = Label(
      x=hx,
      y=hy,
      text="?",
      text_font_size="11px",
      text_color="#0d6efd",
      text_align="center",
      text_baseline="middle",
      level="overlay",
  )
  plot.add_layout(lab)
