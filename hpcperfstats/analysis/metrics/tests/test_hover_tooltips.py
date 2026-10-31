"""Unit tests for hover tooltip formatting in analysis plots."""

import html

import pandas as pd
from bokeh.models import HoverTool

from hpcperfstats.analysis.metrics.lib.columnar.testing import (
  MockJobTable,
  frame_from_df,
  points_from_df,
)
from hpcperfstats.analysis.metrics.lib.columnar.type_detail_series import (
  TypeDetailSeries,
  build_type_detail_series,
)
from hpcperfstats.analysis.metrics.lib.plot.devplot import DevPlot
from hpcperfstats.analysis.metrics.lib.plot.roofline import (
  _build_roofline_figure,
)
from hpcperfstats.analysis.metrics.lib.plot.summary_metric_descriptions import (
  description_for_summary_metric,
)
from hpcperfstats.analysis.metrics.lib.plot.summaryplot import SummaryPlot


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
  df = pd.DataFrame(
    {
      "time": [
        pd.Timestamp("2024-01-01 00:00:00+00:00"),
        pd.Timestamp("2024-01-01 00:00:00+00:00"),
      ],
      "host": ["h1", "h2"],
      "cpu": [1.0, 2.0],
    }
  )

  plot = sp.plot_metric(frame_from_df(df), "cpu", "CPU Usage [#cores]")
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


def test_type_detail_series_uses_value_metric_when_amd_gpu_in_type_list():
  """GPU gauge-style types use the value column; amd_gpu matches nvidia_gpu/mem."""
  from hpcperfstats.analysis.metrics.lib.columnar.testing import (
    table_from_records,
  )

  t0 = pd.Timestamp("2024-01-01 00:00:00+00:00")
  table = table_from_records(
    [(t0, "h1", "amd_gpu", "gpu_util", "0", 50.0, 1.0, None)]
  )
  series = build_type_detail_series(
    table, "amd_gpu", [("gpu_util", "%")], ["amd_gpu"]
  )
  assert series.metric == "value"
  assert series.frame.columns["gpu_util"].tolist() == [50.0]
  _series, grid = DevPlot(series, ["h1"]).plot()
  assert grid is not None


def _type_detail_series(df, event):
  frame = frame_from_df(df)
  return TypeDetailSeries("amd_x86_uncore_df", frame, [(event, "GB/s")], "arc")


def test_devplot_hover_uses_html_with_separators():
  df = pd.DataFrame(
    {
      "time": [
        pd.Timestamp("2024-01-01 00:00:00+00:00"),
        pd.Timestamp("2024-01-01 00:00:00+00:00"),
      ],
      "host": ["h1", "h2"],
      "MBW_CHANNEL_0": [3.0, 4.0],
    }
  )
  dp = DevPlot(_type_detail_series(df, "MBW_CHANNEL_0"), ["h1", "h2"])
  dp.hc = {"h1": "#333333", "h2": "#444444"}

  plot = dp.plot_metric("MBW_CHANNEL_0", "GB/s")
  hover = _get_series_hover_tool(plot, "@MBW_CHANNEL_0_plain")

  assert isinstance(hover.tooltips, str)
  assert "border-bottom" in hover.tooltips
  assert "@_hover_time" in hover.tooltips
  assert "@MBW_CHANNEL_0_plain" in hover.tooltips
  assert hover.formatters == {}
  assert len(hover.renderers) == 2


def test_roofline_job_hover_uses_html_with_separators():
  df = pd.DataFrame(
    {
      "host": ["h1", "h2"],
      "time": [
        pd.Timestamp("2024-01-01 00:00:00+00:00"),
        pd.Timestamp("2024-01-01 00:00:00+00:00"),
      ],
      "flops_gf": [100.0, 120.0],
      "bw_gb": [10.0, 12.0],
    }
  )

  plot = _build_roofline_figure(
    points_from_df(df), peak_flops_gf=1000.0, peak_bw_gb=100.0, title="Roofline"
  )
  assert plot is not None

  tools = [tool for tool in plot.tools if isinstance(tool, HoverTool)]
  job_hovers = [
    hover
    for hover in tools
    if isinstance(hover.tooltips, str) and "@ai_plain" in hover.tooltips
  ]
  assert len(job_hovers) == 1
  hover = job_hovers[0]

  assert "border-bottom" in hover.tooltips
  assert "@perf_plain" in hover.tooltips
  assert "@host" in hover.tooltips
  assert "@dev_display" in hover.tooltips
  assert "device:" in hover.tooltips
  assert hover.formatters == {}


def test_roofline_job_hover_shows_device_when_dev_present():
  df = pd.DataFrame(
    {
      "host": ["h1"],
      "dev": ["gpu0"],
      "time": [pd.Timestamp("2024-01-01 00:00:00+00:00")],
      "flops_gf": [100.0],
      "bw_gb": [10.0],
    }
  )

  plot = _build_roofline_figure(
    points_from_df(df), peak_flops_gf=1000.0, peak_bw_gb=100.0, title="Roofline"
  )
  assert plot is not None
  job_source = plot.renderers[1].data_source.data
  assert job_source["dev"] == ["gpu0"]
  assert job_source["dev_display"] == ["gpu0"]


def test_roofline_roof_hover_includes_axis_units():
  """Navy theoretical line hover must expose the same axis units as the figure."""
  df = pd.DataFrame(
    {
      "host": ["h1"],
      "time": [pd.Timestamp("2024-01-01 00:00:00+00:00")],
      "flops_gf": [100.0],
      "bw_gb": [10.0],
    }
  )

  plot = _build_roofline_figure(
    points_from_df(df), peak_flops_gf=1000.0, peak_bw_gb=100.0, title="Roofline"
  )
  assert plot is not None

  tools = [tool for tool in plot.tools if isinstance(tool, HoverTool)]
  roof_hovers = [hover for hover in tools if isinstance(hover.tooltips, list)]
  assert len(roof_hovers) == 1
  tip_text = " ".join(
    f"{label} {value}" for label, value in roof_hovers[0].tooltips
  )
  assert "Roofline" in tip_text
  assert "FLOP/byte" in tip_text
  assert "GFLOP/s" in tip_text
  assert "Peak performance" in tip_text
  assert "1,000.00 GFLOP/s" in tip_text
  assert "Peak bandwidth" in tip_text
  assert "100.00 GB/s" in tip_text


def test_mock_job_table_converts_dataframes():
  """The legacy-test shim turns DataFrame mocks into columnar tables."""
  t0 = pd.Timestamp("2024-01-01 00:00:00+00:00")
  jt = MockJobTable(["h1"])
  jt.get_host_time_df.return_value = pd.DataFrame(
    [("h1", t0)], columns=["host", "time"]
  )
  jt.get_aggregate_df.return_value = pd.DataFrame(
    [("h1", t0, 2.0)], columns=["host", "time", "sum_val"]
  )
  ht = jt.get_host_time()
  agg = jt.get_aggregate("host_cpu", "arc", ["user"], 1.0)
  assert ht.host_names() == ["h1"]
  assert agg.sum_val.tolist() == [2.0]
  assert agg.time_us.tolist() == ht.time_us.tolist()
