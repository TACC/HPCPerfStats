#!/usr/bin/env python3
"""Type-detail plot: Bokeh continuous-line plots per event for a given type (e.g. llite, cpu).

Uses TypeDetailDataProvider. Layout matches Summary: stretch_width figures in a
2-column ``gridplot``.
"""
import logging
import time

log = logging.getLogger(__name__)

from bokeh.layouts import gridplot
from bokeh.models import ColumnDataSource, HoverTool, Range1d
from bokeh.palettes import d3
from bokeh.plotting import figure

from pandas import to_datetime

from hpcperfstats.analysis.metrics.lib.bokeh_job_embed import figure_embed_kw
from hpcperfstats.analysis.metrics.lib.gen.utils import (
    add_hover_plain_columns,
    clean_dataframe,
    non_degenerate_y_range_for_series,
    set_linear_axes_plain_numeric,
    timestamps_as_cluster_naive,
    tz_aware_bokeh_tick_formatter,
)
from hpcperfstats.analysis.metrics.lib.plot.bokeh_job_detail_help_marker import (
    add_job_detail_bokeh_help_marker,
)
from hpcperfstats.analysis.metrics.lib.plot.hover_html import hover_tooltip_html_host_time_value
from hpcperfstats.analysis.metrics.lib.plot.job_detail_bokeh_plot_descriptions import (
    description_for_job_detail_bokeh_plot,
    researcher_use_for_job_detail_bokeh_plot,
)


class DevPlot:
  """Type-detail plot using an ORM data provider (TypeDetailDataProvider).

    """

  def __init__(self, data_provider, host_list):
    """Store data provider and host list for plotting.

        """
    self.data_provider = data_provider
    self.host_list = host_list

  def plot_metric(self, df, event, unit=None):
    """Create one Bokeh figure with continuous lines per host for the given event.

        """
    s = time.time()

    df = df[["time", "host", event]].copy()
    df["time"] = timestamps_as_cluster_naive(to_datetime(df["time"], utc=True))

    y_range_start, y_range_end = non_degenerate_y_range_for_series(df[event])

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
      host_df = df[df.host == h].sort_values("time")
      source = ColumnDataSource(
          add_hover_plain_columns(host_df, [event], time_col="time"),
      )
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
        description_for_job_detail_bokeh_plot(help_key) or (
            f"Time: sample timestamp (UTC). Y ({ylabel}): device-aggregated "
            f"rate or value for event {event}."
        ),
        researcher_use_for_job_detail_bokeh_plot(help_key),
    )
    log.debug("time to plot %s: %s", event, time.time() - s)
    return plot

  def plot(self):
    """Build host_time_df, merge aggregate per event, and return (df, 2-col gridplot).

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

    ncols = min(2, len(plots)) if plots else 1
    return df, gridplot(plots, ncols=ncols, sizing_mode="stretch_width")
