"""Unit tests for hover tooltip formatting in analysis plots."""
import pandas as pd
from bokeh.models import HoverTool

from hpcperfstats.analysis.plot.devplot import DevPlot
from hpcperfstats.analysis.plot.summaryplot import SummaryPlot


def _get_hover_tool(plot):
  tools = [tool for tool in plot.tools if isinstance(tool, HoverTool)]
  assert len(tools) == 1
  return tools[0]


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
  hover = _get_hover_tool(plot)

  assert isinstance(hover.tooltips, str)
  assert "border-bottom" in hover.tooltips
  assert "@time{%F %T}" in hover.tooltips
  assert "@cpu" in hover.tooltips
  assert hover.formatters == {"@time": "datetime"}
  assert len(hover.renderers) == len(_SummaryJt.host_list)


class _TypeDetailProvider:
  pass


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
  hover = _get_hover_tool(plot)

  assert isinstance(hover.tooltips, str)
  assert "border-bottom" in hover.tooltips
  assert "@time{%F %T}" in hover.tooltips
  assert "@MBW_CHANNEL_0" in hover.tooltips
  assert hover.formatters == {"@time": "datetime"}
  assert len(hover.renderers) == 2
