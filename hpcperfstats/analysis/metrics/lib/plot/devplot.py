#!/usr/bin/env python3
"""
Type-detail plot: Bokeh continuous-line plots per event for a given type (e.g.
llite, cpu), built from a ``TypeDetailSeries`` (columnar job table).

Layout matches Summary: stretch_width figures in a 2-column ``gridplot``.

Attributes:
  log: Module logger.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import numpy as np
from bokeh.layouts import gridplot
from bokeh.models import ColumnDataSource, HoverTool, Range1d
from bokeh.palettes import d3
from bokeh.plotting import figure

from hpcperfstats.analysis.metrics.lib.bokeh_job_embed import figure_embed_kw
from hpcperfstats.analysis.metrics.lib.columnar.type_detail_series import (
  TypeDetailSeries,
)
from hpcperfstats.analysis.metrics.lib.gen.utils import (
  hover_plain_columns,
  non_degenerate_y_range_for_series,
  set_linear_axes_plain_numeric,
  timestamps_as_cluster_naive,
  tz_aware_bokeh_tick_formatter,
)
from hpcperfstats.analysis.metrics.lib.job_host_data_scope import (
  show_shared_host_wide_help_footer,
)
from hpcperfstats.analysis.metrics.lib.phase_profile import (
  phase as _profile_phase,
)
from hpcperfstats.analysis.metrics.lib.plot.bokeh_job_detail_help_marker import (
  add_job_detail_bokeh_help_marker,
)
from hpcperfstats.analysis.metrics.lib.plot.hover_html import (
  hover_tooltip_html_host_time_value,
)
from hpcperfstats.analysis.metrics.lib.plot.job_detail_bokeh_plot_descriptions import (
  description_for_job_detail_bokeh_plot,
  researcher_use_for_job_detail_bokeh_plot,
)

log = logging.getLogger(__name__)


class DevPlot:
  """
  Type-detail plot from a ``TypeDetailSeries``.

  Attributes:
    series: The per-type series (base rows plus one column per event).
    host_list: Accounting hosts (line order and colors).
    is_shared_node: Shared-node signal for the residual help footer.
    hc: Host -> color map, set by ``plot``.
  """

  def __init__(
    self,
    series: TypeDetailSeries,
    host_list: Any,
    *,
    is_shared_node: bool = False,
  ) -> None:
    """
    Store the series and host list for plotting.

    Args:
      series (TypeDetailSeries): Built type-detail series.
      host_list (Any): Accounting host names.
      is_shared_node (bool): Shared-node job (help footer gating).

    Returns:
      None

    Examples:
      >>> DevPlot(series, ["h1"])  # doctest: +SKIP
    """
    self.series = series
    self.host_list = list(host_list or [])
    self.is_shared_node = bool(is_shared_node)
    self.hc: dict[str, str] = {}

  def plot_metric(self, event: Any, unit: Any | None = None) -> Any:
    """
    Create one Bokeh figure with continuous lines per host for the given event.

    Args:
      event (Any): Event column of the series frame.
      unit (Any | None): Unit label.

    Returns:
      Any: Bokeh figure.

    Examples:
      >>> DevPlot(series, ["h1"]).plot_metric("user", "%")  # doctest: +SKIP
    """
    s = time.time()
    frame = self.series.frame
    with _profile_phase("plot_series"):
      values = np.asarray(frame.columns[event], dtype=np.float64)
      host_names = np.array(frame.host_names(), dtype=object)
      times_local = timestamps_as_cluster_naive(frame.time_us)
      y_range_start, y_range_end = non_degenerate_y_range_for_series(values)

    ylabel = event + " (" + (unit or "") + ")"

    plot = figure(
      **figure_embed_kw(
        150,
        x_axis_type="datetime",
        y_range=Range1d(y_range_start, y_range_end),
        y_axis_label=ylabel,
        x_axis_label="Time",
        title="",
        min_border_left=72,
      )
    )
    set_linear_axes_plain_numeric(plot)
    plot.xaxis.formatter = tz_aware_bokeh_tick_formatter()
    plot.yaxis.axis_label_text_font_size = "9pt"
    plot.xaxis.axis_label_text_font_size = "9pt"

    circle_renderers = []
    for h in self.host_list:
      with _profile_phase("plot_series"):
        sel = np.nonzero(host_names == str(h))[0]
        order = sel[np.argsort(frame.time_us[sel], kind="stable")]
        data: dict[str, Any] = {
          "host": host_names[order].tolist(),
          "time": times_local[order],
          event: values[order],
        }
        data.update(
          hover_plain_columns(times_local[order], {event: values[order]})
        )
      source = ColumnDataSource(data=data)
      plot.line(
        x="time",
        y=event,
        source=source,
        line_color=self.hc[h],
        line_width=1.5,
      )
      circle = plot.scatter(
        x="time",
        y=event,
        source=source,
        size=4,
        marker="circle",
        color=self.hc[h],
        alpha=0.9,
      )
      circle_renderers.append(circle)

    plot.add_tools(
      HoverTool(
        tooltips=hover_tooltip_html_host_time_value(event, event),
        renderers=circle_renderers,
      )
    )
    help_key = "jobDetailPlot_type_detail_rates"
    add_job_detail_bokeh_help_marker(
      plot,
      description_for_job_detail_bokeh_plot(help_key)
      or (
        f"Time: sample timestamp (UTC). Y ({ylabel}): device-aggregated "
        f"rate or value for event {event}."
      ),
      researcher_use_for_job_detail_bokeh_plot(help_key),
      is_shared_node=show_shared_host_wide_help_footer(
        is_shared_node=self.is_shared_node,
        typ=self.series.type_name,
      ),
    )
    log.debug("time to plot %s: %s", event, time.time() - s)
    return plot

  def plot(self) -> Any:
    """
    Return ``(series, gridplot)``: one figure per kept event, two columns.

    Returns:
      Any: ``(TypeDetailSeries, GridPlot)``; the grid holds no figure when
      no event survived the series build.

    Examples:
      >>> DevPlot(series, ["h1"]).plot()  # doctest: +SKIP
    """
    colors = d3["Category20"][20]
    self.hc = {
      hostname: colors[i % 20] for i, hostname in enumerate(self.host_list)
    }
    log.debug("Host Count: %s", len(self.host_list))

    plots = []
    for event, unit in self.series.events_units:
      if event not in self.series.frame.columns:
        continue
      with _profile_phase("bokeh"):
        plots += [self.plot_metric(event, unit)]

    ncols = min(2, len(plots)) if plots else 1
    with _profile_phase("bokeh"):
      return self.series, gridplot(
        plots, ncols=ncols, sizing_mode="stretch_width"
      )
