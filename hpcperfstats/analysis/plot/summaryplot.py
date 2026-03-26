"""Summary plot: multi-metric step plots for a job (FLOPS, BW, CPU, etc.) using jid_table aggregate data and Bokeh.

"""
import hpcperfstats.conf_parser as cfg

import logging
import math
import time

log = logging.getLogger(__name__)

from pandas import to_datetime

from hpcperfstats.analysis.gen.utils import tz_aware_bokeh_tick_formatter

from bokeh.layouts import gridplot
from bokeh.models import ColumnDataSource, HoverTool, Range1d
from bokeh.models.glyphs import Step
from bokeh.palettes import d3
from bokeh.plotting import figure

from hpcperfstats.analysis.plot import MSG_NO_METRIC_DATA

local_timezone = cfg.get_local_timezone()


def _hover_tooltip_html(value_label, value_field):
  """Build an HTML hover template with spacing between multi-point hits."""
  return f"""
    <div style="padding-bottom:6px; margin-bottom:6px; border-bottom:1px solid #d0d7de;">
      <div><strong>host:</strong> @host</div>
      <div><strong>time:</strong> @time{{%F %T}}</div>
      <div><strong>{value_label}:</strong> @{value_field}</div>
    </div>
  """


def _summary_metric_specs():
  """Metric specs used by summary plot and diagnostics."""
  return [
      ("amd64_pmc", "arc", ['FLOPS'], "amd_flops", 1e-9, "FLOPS32b+64b[GF]"),
      ("amd64_df", "arc",
       ['MBW_CHANNEL_0', 'MBW_CHANNEL_1', 'MBW_CHANNEL_2', 'MBW_CHANNEL_3'
        ], "amd_mbw", 2 / (1024 * 1024 * 1024), "DRAMBW[GB/s]"),
      ("amd64_pmc", "value", ['INST_RETIRED'], "amd_instr", 1, '[#/s]'),
      ("amd64_pmc", "arc", ['MPERF'], "amd_mcycles", 1, '[#/s]'),
      ("amd64_pmc", "arc", ['APERF'], "amd_acycles", 1, '[#/s]'),
      ("intel_8pmc3", "arc", [
          'FP_ARITH_INST_RETIRED_SCALAR_DOUBLE',
          'FP_ARITH_INST_RETIRED_128B_PACKED_DOUBLE',
          'FP_ARITH_INST_RETIRED_256B_PACKED_DOUBLE',
          'FP_ARITH_INST_RETIRED_512B_PACKED_DOUBLE'
      ], "flops64b", 1e-9, "FLOPS64b[GF]"),
      ("intel_8pmc3", "arc", [
          'FP_ARITH_INST_RETIRED_SCALAR_SINGLE',
          'FP_ARITH_INST_RETIRED_128B_PACKED_SINGLE',
          'FP_ARITH_INST_RETIRED_256B_PACKED_SINGLE',
          'FP_ARITH_INST_RETIRED_512B_PACKED_SINGLE'
      ], "flops32b", 1e-9, "FLOPS32b[GF]"),
      ("intel_8pmc3", "arc", ['INST_RETIRED'], "instr", 1, '[#/s]'),
      ("intel_8pmc3", "arc", ['MPERF'], "mcycles", 1, '[#/s]'),
      ("intel_8pmc3", "arc", ['APERF'], "acycles", 1, '[#/s]'),
      ("intel_rapl", "arc", ['MSR_PKG_ENERGY_STATUS'], "watts", 0.00001526,
       '[watts]'),
      ("intel_skx_imc", "arc", ['CAS_READS', 'CAS_WRITES'], "mbw",
       64 / (1024 * 1024 * 1024), "DRAMBW[GB/s]"),
      ("ib_ext", "arc", ['port_rcv_data', 'port_xmit_data'], "ibbw",
       1 / (1024 * 1024), "FabricBW[MB/s]"),
      ("llite", "arc", [
          'open', 'close', 'mmap', 'fsync', 'setattr', 'truncate', 'flock',
          'getattr', 'statfs', 'alloc_inode', 'setxattr', 'listxattr',
          'removexattr', 'readdir', 'create', 'lookup', 'link', 'unlink',
          'symlink', 'mkdir', 'rmdir', 'mknod', 'rename'
      ], "liops", 1, "LustreIOPS[#/s]"),
      ("llite", "arc", ['read_bytes', 'write_bytes'], "lbw",
       1 / (1024 * 1024), "LustreBW[MB/s]"),
      ("cpu", "arc", ['user', 'system',
                      'nice'], "cpu", 0.01, "CPU Usage [#cores]"),
      ("mem", "value", ['MemUsed'], "mem", 1 / (1024 * 1024), "MemUsed[GB]")
  ]


class SummaryPlot():
  """Builds a grid of Bokeh step plots (one per metric) from jid_table aggregate DataFrames.

    """

  def __init__(self, jt):
    """Store jid, jt, and host_list from the given jid_table (or HostDataProvider).

        """
    self.jid = jt.jid
    self.jt = jt
    self.host_list = jt.host_list

  def plot_metric(self, df, metric, label):
    """Create one Bokeh figure with step glyphs per host for the given metric column and label.

        """
    s = time.time()

    df = df[["time", "host", metric]]

    y_range_end = 1.1 * df[metric].max()
    if math.isnan(y_range_end):
      y_range_end = 0

    plot = figure(
        width=400,
        height=150,
        x_axis_type="datetime",
        y_range=Range1d(-0.1, y_range_end),
        y_axis_label=label,
    )
    plot.xaxis.formatter = tz_aware_bokeh_tick_formatter()

    circle_renderers = []
    for h in self.host_list:
      source = ColumnDataSource(df[df.host == h])
      plot.add_glyph(
          source,
          Step(x="time", y=metric, mode="before", line_color=self.hc[h]),
      )
      # Bokeh 3.4+: use scatter(size=...) instead of circle(size=...).
      circle = plot.scatter(
          x="time",
          y=metric,
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
            tooltips=_hover_tooltip_html(label, metric),
            formatters={"@time": "datetime"},
            renderers=circle_renderers,
        )
    )
    log.debug("time to plot %s: %s", metric, time.time() - s)
    return plot

  def plot(self):
    """Build host_time_df, merge all configured metrics (amd64_pmc, intel_8pmc3, llite, cpu, mem, etc.), and return a gridplot of step plots.

        """
    self.hc = {}
    colors = d3["Category20"][20]
    for i, hostname in enumerate(self.host_list):
      self.hc[hostname] = colors[i % 20]

    log.debug("Host Count: %s", len(self.host_list))

    metrics = _summary_metric_specs()

    df = self.jt.get_host_time_df()
    if df.empty or not self.host_list:
      raise ValueError(MSG_NO_METRIC_DATA)

    for typ, val, events, name, conv, label in metrics:
      s = time.time()
      agg = self.jt.get_aggregate_df(typ, val, events, conv)
      if agg.empty or "sum_val" not in agg.columns:
        df[name] = float("nan")
      else:
        df = df.merge(agg[["host", "time", "sum_val"]],
                      on=["host", "time"],
                      how="left")
        df[name] = df["sum_val"]
        df.drop(columns=["sum_val"], inplace=True)

      if name == "amd_watts":
        log.debug("amd_watts: %s", df[name].tolist())
      if name in df.columns and df[name].isnull().values.any():
        del df[name]
      log.debug("time to compute %s: %s", name, time.time() - s)

    if 'acycles' in df.columns and 'mcycles' in df.columns:
      df["freq"] = 2.7 * df["acycles"] / df["mcycles"]
      df["cpi"] = df["acycles"] / df["instr"]
      metrics += [("freq", "arc", [], "freq", 1, "[GHz]")]
      metrics += [("cpi", "arc", [], "cpi", 1, "CPI")]
      del df["mcycles"], df["acycles"], df["instr"]

    if 'amd_acycles' in df.columns and 'amd_mcycles' in df.columns:
      del df["amd_mcycles"], df["amd_acycles"], df["amd_instr"]
    df = df.reset_index()

    df["time"] = to_datetime(df["time"], utc=True)
    df["time"] = df["time"].dt.tz_convert(local_timezone)

    plots = []
    for typ, val, events, name, conv, label in metrics:
      if name not in df.columns:
        continue
      plots += [self.plot_metric(df, name, label)]

    if not plots:
      raise ValueError(MSG_NO_METRIC_DATA)
    return gridplot(plots, ncols=len(plots) // 4 + 1)


def plot_and_reason_summary_from_jid_table(jt):
  """Build summary plot and return (figure_or_none, unavailable_reason_or_none)."""
  try:
    fig = SummaryPlot(jt).plot()
    return (fig, None)
  except Exception:
    # Build diagnostics from available aggregates.
    host_time_df = jt.get_host_time_df()
    if host_time_df.empty or not jt.host_list:
      return (None, "No hosts/timestamps found in host_data for this job/time range")

    attempts = []
    available = []
    for typ, val_col, events, name, conv, _label in _summary_metric_specs():
      try:
        agg = jt.get_aggregate_df(typ, val_col, events, conv)
      except Exception:
        attempts.append(f"{name}:{typ}:{val_col} rows(error)")
        continue
      rows = 0 if agg is None else len(agg.index)
      attempts.append(f"{name}:{typ}:{val_col} rows({rows})")
      if rows > 0 and "sum_val" in agg.columns:
        available.append(name)

    if not available:
      return (
          None,
          "Missing summary counters in host_data (no configured summary metrics had usable rows). "
          + "Attempted: "
          + "; ".join(attempts),
      )
    return (
        None,
        "Summary aggregates exist but no renderable series were produced. "
        + "Available metric aggregates: "
        + ", ".join(sorted(set(available))),
    )
