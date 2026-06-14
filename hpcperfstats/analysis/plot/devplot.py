#!/usr/bin/env python3
"""Type-detail plot: Bokeh step plots per event for a given type (e.g. llite, cpu) using TypeDetailDataProvider.

"""
import logging
import time

log = logging.getLogger(__name__)

from bokeh.layouts import gridplot
from bokeh.models import ColumnDataSource, HoverTool, Range1d
from bokeh.models.glyphs import Step
from bokeh.palettes import d3
from bokeh.plotting import figure

from pandas import to_datetime

from hpcperfstats.analysis.gen.utils import (
    add_hover_plain_columns,
    clean_dataframe,
    non_degenerate_y_range_for_series,
    set_linear_axes_plain_numeric,
    tz_aware_bokeh_tick_formatter,
)
from hpcperfstats.analysis.plot.hover_html import hover_tooltip_html_host_time_value


class DevPlot:
  """Type-detail plot using an ORM data provider (TypeDetailDataProvider). Replaces raw connection + temp table type_detail.

    """

  def __init__(self, data_provider, host_list):
    """Store data provider and host list for plotting.

        """
    self.data_provider = data_provider
    self.host_list = host_list

  def plot_metric(self, df, event, unit=None):
    """Create one Bokeh figure with step glyphs per host for the given event (and optional unit label).

        """
    s = time.time()

    df = df[["time", "host", event]]

    y_range_start, y_range_end = non_degenerate_y_range_for_series(df[event])

    ylabel = event + " (" + (unit or "") + ")"

    plot = figure(
        width=400,
        height=150,
        x_axis_type="datetime",
        y_range=Range1d(y_range_start, y_range_end),
        y_axis_label=ylabel,
    )
    set_linear_axes_plain_numeric(plot)
    plot.xaxis.formatter = tz_aware_bokeh_tick_formatter()

    circle_renderers = []
    for h in self.host_list:
      source = ColumnDataSource(
          add_hover_plain_columns(df[df.host == h], [event], time_col="time"),
      )
      plot.add_glyph(
          source,
          Step(x="time", y=event, mode="before", line_color=self.hc[h]),
      )
      # Bokeh 3.4+: use scatter(size=...) instead of circle(size=...).
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

    # Hover shows which sample point (host) and value; no legend (identify line by hovering).
    plot.add_tools(
        HoverTool(
            tooltips=hover_tooltip_html_host_time_value(event, event),
            renderers=circle_renderers,
        )
    )
    log.debug("time to plot %s: %s", event, time.time() - s)
    return plot

  def plot(self):
    """Build host_time_df, merge aggregate per event, and return (df, gridplot of step plots).

        """
    self.hc = {}
    colors = d3["Category20"][20]
    for i, hostname in enumerate(self.host_list):
      self.hc[hostname] = colors[i % 20]

    log.debug("Host Count: %s", len(self.host_list))

    df = self.data_provider.get_host_time_df()
    event_list = self.data_provider.get_events_units()
    type_list = self.data_provider.get_type_list()

    metric = "arc"
    if type_list and (
        "mem" in type_list
        or "nvidia_gpu" in type_list
        or "amd_gpu" in type_list
    ):
      metric = "value"

    for event, unit in event_list:
      s = time.time()
      agg = self.data_provider.get_aggregate_df(event, metric=metric)
      if agg.empty or "sum_val" not in agg.columns:
        df[event] = float("nan")
      else:
        df = df.merge(agg[["host", "time", "sum_val"]],
                      on=["host", "time"],
                      how="left")
        df[event] = df["sum_val"]
        df.drop(columns=["sum_val"], inplace=True)
      if event in df.columns and df[event].isnull().values.any():
        del df[event]
      log.debug("time to compute events %s: %s", event, time.time() - s)

    df = df.reset_index()
    if not df.empty and "time" in df.columns:
      df["time"] = to_datetime(df["time"], utc=True)

    df = clean_dataframe(df)

    plots = []
    for event, unit in event_list:
      if event not in df.columns:
        continue
      plots += [self.plot_metric(df, event, unit)]

    return df, gridplot(plots, ncols=len(plots) // 4 + 1 if plots else 1)
