"""Server-side Bokeh histograms serialized as ``json_item`` for public dashboard payloads.

Built during ``refresh_public_expansion_factor_artifacts`` only; HTTP handlers merge
artifacts without touching Bokeh.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

from bokeh.embed import json_item
from bokeh.models import ColumnDataSource, HoverTool, Title
from bokeh.plotting import figure

from hpcperfstats.analysis.metrics.lib.bokeh_job_embed import figure_embed_kw
from hpcperfstats.analysis.metrics.lib.gen.utils import set_linear_axes_plain_numeric


def _format_edge_plain(x: float) -> str:
  # Avoid scientific notation in bin labels (repo policy for user-visible numbers).
  if x == int(x) and abs(x) < 1e12:
    return str(int(x))
  s = f"{float(x):.6f}".rstrip("0").rstrip(".")
  return s or "0"


def build_public_expansion_factor_histogram_json_item(
    *,
    period_key: str,
    period_kind: str,
    edges: Sequence[float],
    counts: Sequence[int],
) -> Optional[Dict[str, Any]]:
  """Return a Bokeh ``json_item`` dict for the expansion-factor count histogram, or ``None``."""
  if len(edges) < 2:
    return None
  if len(counts) != len(edges):
    return None

  lefts: List[float] = []
  rights: List[float] = []
  tops: List[int] = []
  labels: List[str] = []

  for i in range(len(edges) - 1):
    lo = float(edges[i])
    hi = float(edges[i + 1])
    cnt = int(counts[i])
    lefts.append(lo)
    rights.append(hi)
    tops.append(cnt)
    labels.append(f"[{_format_edge_plain(lo)}, {_format_edge_plain(hi)})")

  overflow = int(counts[-1])
  if overflow > 0:
    last_edge = float(edges[-1])
    span = float(edges[-1] - edges[-2]) if len(edges) >= 2 else max(last_edge, 1.0)
    if not math.isfinite(span) or span <= 0:
      span = 1.0
    lefts.append(last_edge)
    rights.append(last_edge + span)
    tops.append(overflow)
    labels.append(f"≥ {_format_edge_plain(last_edge)}")

  if not lefts:
    return None

  y_max = max(tops) if tops else 0
  y_end = max(1, int(math.ceil(y_max * 1.08)) if y_max else 1)

  title = f"Expansion factor — {period_key}"
  subtitle = period_kind

  plot = figure(
      **figure_embed_kw(
          height=300,
          title=title,
          toolbar_location=None,
          tools="",
          x_axis_label="Expansion factor (histogram bin)",
          y_axis_label="Count",
      ),
  )
  plot.sizing_mode = "stretch_width"
  plot.add_layout(
      Title(text=subtitle, text_font_size="11px", align="center"),
      "above",
  )

  src = ColumnDataSource(
      data=dict(left=lefts, right=rights, top=tops, bin_label=labels),
  )
  r = plot.quad(
      left="left",
      right="right",
      top="top",
      bottom=0,
      source=src,
      fill_color="#6c757d",
      fill_alpha=0.85,
      line_color="#343a40",
      line_width=1,
      hover_fill_alpha=1.0,
      hover_fill_color="#495057",
  )
  hover = HoverTool(
      tooltips=[("Bin", "@bin_label"), ("Count", "@top")],
      mode="mouse",
      point_policy="follow_mouse",
      line_policy="nearest",
      renderers=[r],
  )
  plot.add_tools(hover)
  set_linear_axes_plain_numeric(plot)
  plot.y_range.start = 0
  plot.y_range.end = y_end
  xmin = min(lefts)
  xmax = max(rights)
  x_pad = max((xmax - xmin) * 0.02, 1e-6)
  plot.x_range.start = xmin - x_pad
  plot.x_range.end = xmax + x_pad

  return json_item(plot)
