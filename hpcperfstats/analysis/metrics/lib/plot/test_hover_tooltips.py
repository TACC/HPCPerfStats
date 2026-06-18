"""Unit tests for hover tooltip formatting in analysis plots."""
import html
import pandas as pd
from bokeh.models import HoverTool

from hpcperfstats.analysis.metrics.lib.plot.devplot import DevPlot
from hpcperfstats.analysis.metrics.lib.plot.roofline import _build_roofline_figure
from hpcperfstats.analysis.metrics.lib.plot.summaryplot import SummaryPlot
from hpcperfstats.analysis.metrics.lib.plot.summary_metric_descriptions import (
    description_for_summary_metric,
)


def _get_series_hover_tool(plot, field_token):
  tools = [tool for tool in plot.tools if isinstance(tool, HoverTool)]
  for tool in tools:
    tt = tool.tooltips
    if isinstance(tt, str) and field_token in tt:
      return tool
  raise AssertionError(f"No HoverTool with {field_token!r} in tooltips")


class _SummaryJt:
  jid = 1
  host_list = ["h1", "h2"]


def test_summaryplot_hover_uses_html_with_separators():
  sp = SummaryPlot(_SummaryJt())
  sp.hc = {"h1": "#111111", "h2": "#222222"}
  df = pd.DataFrame({
      "time": [
          pd.Timestamp("2024-01-01 00:00:00+00:00"),
          pd.Timestamp("2024-01-01 00:00:00+00:00"),
      ],
      "host": ["h1", "h2"],
      "cpu": [1.0, 2.0],
  })

  plot = sp.plot_metric(df, "cpu", "CPU Usage [#cores]")
  hover = _get_series_hover_tool(plot, "@cpu_plain")

  assert isinstance(hover.tooltips, str)
  assert "border-bottom" in hover.tooltips
  assert "@_hover_time" in hover.tooltips
  assert "@cpu_plain" in hover.tooltips
  assert hover.formatters == {}
  assert len(hover.renderers) == 1

  help_hovers = [
      t
      for t in plot.tools
      if isinstance(t, HoverTool)
      and isinstance(t.tooltips, str)
      and "max-width:28em" in t.tooltips
  ]
  assert len(help_hovers) == 1
  tip_plain = html.unescape(help_hovers[0].tooltips)
  assert "CPU cores in use" in tip_plain
  assert description_for_summary_metric("cpu") in tip_plain


class _TypeDetailProvider:
  pass


def test_devplot_uses_value_metric_when_amd_gpu_in_type_list():
  """GPU gauge-style types use value column; amd_gpu must match nvidia_gpu/mem."""
  t0 = pd.Timestamp("2024-01-01 00:00:00+00:00")
  metric_calls = []

  class _Provider:
    def get_host_time_df(self):
      return pd.DataFrame([("h1", t0)], columns=["host", "time"])

    def get_events_units(self):
      return [("gpu_util", "%")]

    def get_type_list(self):
      return ["amd_gpu"]

    def get_aggregate_df(self, event, metric="arc"):
      metric_calls.append((event, metric))
      return pd.DataFrame(
          [("h1", t0, 50.0)], columns=["host", "time", "sum_val"]
      )

  dp = DevPlot(_Provider(), ["h1"])
  _df, _grid = dp.plot()
  assert ("gpu_util", "value") in metric_calls


def test_devplot_hover_uses_html_with_separators():
  dp = DevPlot(_TypeDetailProvider(), ["h1", "h2"])
  dp.hc = {"h1": "#333333", "h2": "#444444"}
  df = pd.DataFrame({
      "time": [
          pd.Timestamp("2024-01-01 00:00:00+00:00"),
          pd.Timestamp("2024-01-01 00:00:00+00:00"),
      ],
      "host": ["h1", "h2"],
      "MBW_CHANNEL_0": [3.0, 4.0],
  })

  plot = dp.plot_metric(df, "MBW_CHANNEL_0", "GB/s")
  hover = _get_series_hover_tool(plot, "@MBW_CHANNEL_0_plain")

  assert isinstance(hover.tooltips, str)
  assert "border-bottom" in hover.tooltips
  assert "@_hover_time" in hover.tooltips
  assert "@MBW_CHANNEL_0_plain" in hover.tooltips
  assert hover.formatters == {}
  assert len(hover.renderers) == 2


def test_roofline_job_hover_uses_html_with_separators():
  df = pd.DataFrame({
      "host": ["h1", "h2"],
      "time": [
          pd.Timestamp("2024-01-01 00:00:00+00:00"),
          pd.Timestamp("2024-01-01 00:00:00+00:00"),
      ],
      "flops_gf": [100.0, 120.0],
      "bw_gb": [10.0, 12.0],
  })

  plot = _build_roofline_figure(df, peak_flops_gf=1000.0, peak_bw_gb=100.0, title="Roofline")
  assert plot is not None

  tools = [tool for tool in plot.tools if isinstance(tool, HoverTool)]
  job_hovers = [
      hover for hover in tools
      if isinstance(hover.tooltips, str) and "@ai_plain" in hover.tooltips
  ]
  assert len(job_hovers) == 1
  hover = job_hovers[0]

  assert "border-bottom" in hover.tooltips
  assert "@perf_plain" in hover.tooltips
  assert "@host" in hover.tooltips
  assert hover.formatters == {}
