"""
Shared helpers for machine app: job_hist (Bokeh histograms), local_timezone,
XALT containers (libset_c, xalt_data_c). Used by the REST API only; React SPA is
the only UI.

Attributes:
  local_timezone: Attribute.
"""
from __future__ import annotations

from typing import Any

import hpcperfstats.dbload.lib.conf_parser as cfg

import numpy as np
from numpy import histogram, isfinite, log
from pandas import to_numeric

from bokeh.models import Range1d

from .bokeh_embed import new_spa_embedded_figure

local_timezone = cfg.get_local_timezone()


class libset_c:
    """
    Simple container for (object_path, module_name) used in XALT libset.
    
    Attributes:
      module_name: Attribute.
      object_path: Attribute.
    """

    def __init__(self, object_path: str, module_name: Any) -> None:
        """
        Initialize a new instance.
        
        Args:
          object_path (str): String for object path.
          module_name (Any): Module name passed to this helper.
        
        Returns:
          None
        
        Examples:
          >>> libset_c("x", None)  # doctest: +SKIP
        """
        self.module_name = module_name
        self.object_path = object_path


class xalt_data_c:
    """
    Container for XALT data: exec_path list, cwd list, libset list.
    
    Attributes:
      cwd: Attribute.
      exec_path: Attribute.
      libset: Attribute.
    """

    def __init__(self) -> None:
        """
        Initialize a new instance.
        
        Returns:
          None
        
        Examples:
          >>> xalt_data_c()  # doctest: +SKIP
        """
        self.exec_path = []
        self.cwd = []
        self.libset = []


def job_hist(
  df: Any,
  metric: Any,
  label: Any,
  width: int = 600,
  height: int = 400,
  title: Any | None = None,
) -> Any:
    """
    Build a Bokeh quad histogram for the given metric column and axis label.
    
    Optional width/height allow thumbnail (e.g. 280x200) vs full (600x400)
      sizes.
    Optional title overrides the figure title (defaults to metric column name).
    Uses only finite values; handles empty, constant, and all-zero data safely.
    
    Job-list embeds omit toolbar and HoverTool: Bokeh 3.x can throw
    ``can't access property "is_valid", e is undefined`` when hit-testing /
    tool panels interact with small embedded figures (see queue vbar path).
    
    Args:
      df (Any): Df passed to this helper.
      metric (Any): Metric passed to this helper.
      label (Any): Label passed to this helper.
      width (int): Integer value for width.
      height (int): Integer value for height.
      title (Any | None): One of ``Any``, ``None``.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> job_hist(None, None, None, 0, 0, None)  # doctest: +SKIP
    """
    if metric not in df.columns:
        return None

    raw = to_numeric(df[metric], errors="coerce")
    values = np.asarray(raw, dtype=np.float64)
    values = values[isfinite(values)]
    if len(values) == 0:
        return None

    min_val = float(np.min(values))
    max_val = float(np.max(values))
    num_bins = max(3, int(5 * log(len(values))))

    if max_val <= min_val:
        low = min_val - 0.5 if min_val != 0 else 0
        high = min_val + 0.5 if min_val != 0 else 1.0
        bins = np.linspace(low, high, num_bins + 1)
    else:
        bins = np.linspace(min_val, max_val, num_bins + 1)

    hist, edges = histogram(values, bins=bins)

    # y starts at 0 so bins with count 0 are degenerate quads (height 0), not
    # inverted quads (top < bottom). bottom=1 with top=0 broke BokehJS 3.9 embed:
    # blank canvas, often no console error.
    hist_max = float(np.max(hist)) if len(hist) > 0 else 0.0
    y_min = 0.0
    y_max = (hist_max * 1.05) if hist_max > 0 else 1.0
    if y_max <= y_min:
        y_max = 1.0

    plot = new_spa_embedded_figure(
        title=title if title is not None else metric,
        height=height,
        width=width,
        y_range=(y_min, y_max),
    )
    plot.title.align = "center"
    plot.xaxis.axis_label = label
    plot.yaxis.axis_label = "# jobs"
    plot.quad(top=hist, bottom=0, left=edges[:-1], right=edges[1:])

    # Equal outer chrome (y-axis labels vs last x tick) and equal data-range pad so
    # job-list distribution thumbs are not clipped on the right (280×200 embeds).
    plot.min_border_left = 40
    plot.min_border_right = 40
    xmin = float(edges[0])
    xmax = float(edges[-1])
    x_pad = max((xmax - xmin) * 0.05, 1e-6)
    plot.x_range = Range1d(start=xmin - x_pad, end=xmax + x_pad)

    return plot
