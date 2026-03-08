#!/usr/bin/env python3
"""Type-detail plot: Bokeh step plots per event for a given type (e.g. llite, cpu) using TypeDetailDataProvider.

"""
import math
import time

from bokeh.layouts import gridplot
from bokeh.models import ColumnDataSource, Range1d
from bokeh.models.glyphs import Step
from bokeh.palettes import d3
from bokeh.plotting import figure

from zoneinfo import ZoneInfo

import hpcperfstats.conf_parser as cfg
from hpcperfstats.analysis.gen.utils import clean_dataframe, tz_aware_bokeh_tick_formatter

_tz_cfg = cfg.get_timezone()
local_timezone = ZoneInfo(_tz_cfg) if isinstance(_tz_cfg, str) else _tz_cfg


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

    y_range_end = 1.1 * df[event].max()
    if math.isnan(y_range_end):
      y_range_end = 0

    ylabel = event + " (" + (unit or "") + ")"

    plot = figure(
        width=400,
        height=150,
        x_axis_type="datetime",
        y_range=Range1d(-0.1, y_range_end),
        y_axis_label=ylabel,
    )
    plot.xaxis.formatter = tz_aware_bokeh_tick_formatter()

    for h in self.host_list:
      source = ColumnDataSource(df[df.host == h])
      plot.add_glyph(
          source,
          Step(x="time", y=event, mode="before", line_color=self.hc[h]),
      )
    print("time to plot {0}: {1}".format(event, time.time() - s))
    return plot

  def plot(self):
    """Build host_time_df, merge aggregate per event, and return (df, gridplot of step plots).

        """
    self.hc = {}
    colors = d3["Category20"][20]
    for i, hostname in enumerate(self.host_list):
      self.hc[hostname] = colors[i % 20]

    print("Host Count:", len(self.host_list))

    df = self.data_provider.get_host_time_df()
    event_list = self.data_provider.get_events_units()
    type_list = self.data_provider.get_type_list()

    metric = "arc"
    if type_list and ("mem" in type_list or "nvidia_gpu" in type_list):
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
      print("time to compute events {0}: {1}".format(event, time.time() - s))

    df = df.reset_index()
    if not df.empty and "time" in df.columns:
      df["time"] = df["time"].dt.tz_convert(local_timezone)

    df = clean_dataframe(df)

    plots = []
    for event, unit in event_list:
      if event not in df.columns:
        continue
      plots += [self.plot_metric(df, event, unit)]

    return df, gridplot(plots, ncols=len(plots) // 4 + 1 if plots else 1)
