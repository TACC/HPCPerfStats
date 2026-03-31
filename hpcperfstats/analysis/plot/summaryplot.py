"""Summary plot: multi-metric step plots for a job (FLOPS, BW, CPU, etc.) using jid_table aggregate data and Bokeh.

"""
import hpcperfstats.conf_parser as cfg

import logging
import math
import time

log = logging.getLogger(__name__)

from pandas import isna as pd_isna
from pandas import to_datetime

from hpcperfstats.analysis.gen.utils import (
    INTEL_CORE_PMC_TYPES_ORDERED,
    INTEL_FP_ARITH_DOUBLE_EVENTS,
    INTEL_FP_ARITH_SINGLE_EVENTS,
    INTEL_IMC_STATS_TYPES,
    new_plain_number_hover_formatter,
    set_linear_axes_plain_numeric,
    tz_aware_bokeh_tick_formatter,
)

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
      <div><strong>{value_label}:</strong> @{value_field}{{custom}}</div>
    </div>
  """


_CAS_BW_CONV = 64 / (1024 * 1024 * 1024)


def _intel_core_tries(events, conv):
  """(typename, events, conv) rows for intel_8pmc3, intel_4pmc3, cpu_counter_metrics."""
  ev = list(events)
  return [(t, ev, conv) for t in INTEL_CORE_PMC_TYPES_ORDERED]


# One aggregate per row (fixed typename); used for AMD, fabric, etc.
_SUMMARY_SINGLE_SPECS = [
    ("amd64_pmc", "arc", ["FLOPS"], "amd_flops", 1e-9, "FLOPS32b+64b[GF]"),
    (
        "amd64_df",
        "arc",
        [
            "MBW_CHANNEL_0",
            "MBW_CHANNEL_1",
            "MBW_CHANNEL_2",
            "MBW_CHANNEL_3",
        ],
        "amd_mbw",
        2 / (1024 * 1024 * 1024),
        "DRAMBW[GB/s]",
    ),
    (
        "amd64_pmc",
        "value",
        ["INST_RETIRED"],
        "amd_instr",
        1,
        "Instructions [#/s]",
    ),
    ("amd64_pmc", "arc", ["MPERF"], "amd_mcycles", 1, "Reference Cycles [#/s]"),
    ("amd64_pmc", "arc", ["APERF"], "amd_acycles", 1, "Actual Cycles [#/s]"),
    (
        "intel_rapl",
        "arc",
        ["MSR_PKG_ENERGY_STATUS"],
        "watts",
        0.00001526,
        "[watts]",
    ),
    (
        "ib_ext",
        "arc",
        ["port_rcv_data", "port_xmit_data"],
        "ibbw",
        1 / (1024 * 1024),
        "FabricBW[MB/s]",
    ),
    (
        "llite",
        "arc",
        [
            "open",
            "close",
            "mmap",
            "fsync",
            "setattr",
            "truncate",
            "flock",
            "getattr",
            "statfs",
            "alloc_inode",
            "setxattr",
            "listxattr",
            "removexattr",
            "readdir",
            "create",
            "lookup",
            "link",
            "unlink",
            "symlink",
            "mkdir",
            "rmdir",
            "mknod",
            "rename",
        ],
        "liops",
        1,
        "LustreIOPS[#/s]",
    ),
    ("llite", "arc", ["read_bytes", "write_bytes"], "lbw",
     1 / (1024 * 1024), "LustreBW[MB/s]"),
    ("cpu", "arc", ["user", "system", "nice"], "cpu", 0.01,
     "CPU Usage [#cores]"),
    ("nvidia_gpu", "value", ["gpu_util"], "nv_gpu_util", 1, "GPU util [%]"),
    ("nvidia_gpu", "value", ["mem_used_mb"], "nv_mem_used_mb", 1, "GPU mem used [MB]"),
    (
        "nvidia_gpu",
        "value",
        ["mem_total_mb"],
        "nv_mem_total_mb",
        1,
        "GPU mem total [MB]",
    ),
    ("mem", "value", ["MemUsed"], "mem", 1 / (1024 * 1024), "MemUsed[GB]"),
]

# Metrics that may be sampled on a sparse (host, time) grid vs the union grid from
# get_host_time_df(); do not drop the column when a left-merge leaves NaN gaps.
_SUMMARY_ALLOW_PARTIAL_NULL = frozenset({
    "nv_gpu_util",
    "nv_mem_used_mb",
    "nv_mem_total_mb",
})

# Merged for scaling/context only; not rendered as its own subplot.
_SUMMARY_SKIP_PLOT_METRICS = frozenset({"nv_mem_total_mb"})

# First typename with full host/time coverage wins (same column name).
_SUMMARY_FIRST_WIN_SPECS = (
    {
        "name": "flops64b",
        "val_col": "arc",
        "label": "FLOPS64b[GF]",
        "tries": _intel_core_tries(INTEL_FP_ARITH_DOUBLE_EVENTS, 1e-9),
    },
    {
        "name": "flops32b",
        "val_col": "arc",
        "label": "FLOPS32b[GF]",
        "tries": _intel_core_tries(INTEL_FP_ARITH_SINGLE_EVENTS, 1e-9),
    },
    {
        "name": "instr",
        "val_col": "arc",
        "label": "Instructions [#/s]",
        "tries": _intel_core_tries(["INST_RETIRED"], 1),
    },
    {
        "name": "mcycles",
        "val_col": "arc",
        "label": "Reference Cycles [#/s]",
        "tries": _intel_core_tries(["MPERF"], 1),
    },
    {
        "name": "acycles",
        "val_col": "arc",
        "label": "Actual Cycles [#/s]",
        "tries": _intel_core_tries(["APERF"], 1),
    },
)


def _summary_nv_mem_used_y_range_end(df):
  """Upper y bound for GPU mem used plot: max(used, total) when total column exists."""
  if "nv_mem_used_mb" not in df.columns or "nv_mem_total_mb" not in df.columns:
    return None
  candidates = []
  for col in ("nv_mem_used_mb", "nv_mem_total_mb"):
    mx = df[col].max()
    if mx is not None and not pd_isna(mx):
      try:
        candidates.append(float(mx))
      except (TypeError, ValueError):
        pass
  if not candidates:
    return None
  return 1.1 * max(candidates)


def _summary_intel_imc_bw_tries():
  """Intel DRAM BW: first IMC type in INTEL_IMC_STATS_TYPES with usable CAS rows."""
  cas = ["CAS_READS", "CAS_WRITES"]
  return [(imc_typ, cas, _CAS_BW_CONV) for imc_typ in INTEL_IMC_STATS_TYPES]


def _merge_first_full_coverage(df, jt, column_name, val_col, tries):
  """Left-merge first (typ, events, conv) whose aggregate has no nulls on base (host, time)."""
  for typ, events, conv in tries:
    agg = jt.get_aggregate_df(typ, val_col, events, conv)
    if agg.empty or "sum_val" not in agg.columns:
      continue
    merged = df.merge(
        agg[["host", "time", "sum_val"]],
        on=["host", "time"],
        how="left",
    )
    merged[column_name] = merged["sum_val"]
    merged.drop(columns=["sum_val"], inplace=True)
    if column_name in merged.columns and merged[column_name].isnull().values.any():
      continue
    return merged
  return df


def _merge_nvidia_gpu_util_column(df, jt):
  """Left-merge ``nv_gpu_util`` from ``nvidia_gpu`` ``value``: prefer ``gpu_util``, else ``utilization``.

  Matches ``avg_gpuutil`` / job_detail GPU stats: newer monitor emits ``gpu_util``;
  older archives may only have ``utilization``.
  """
  name = "nv_gpu_util"
  for events in (["gpu_util"], ["utilization"]):
    agg = jt.get_aggregate_df("nvidia_gpu", "value", events, 1.0)
    if agg.empty or "sum_val" not in agg.columns:
      continue
    merged = df.merge(
        agg[["host", "time", "sum_val"]],
        on=["host", "time"],
        how="left",
    )
    merged[name] = merged["sum_val"]
    merged.drop(columns=["sum_val"], inplace=True)
    if name in merged.columns and merged[name].isnull().values.any():
      keep_sparse = (
          name in _SUMMARY_ALLOW_PARTIAL_NULL and merged[name].notna().any()
      )
      if not keep_sparse:
        del merged[name]
        continue
    return merged
  return df


def iter_summary_aggregate_attempts():
  """Flat (typ, val_col, events, name, conv, label) for diagnostics."""
  for typ, val, events, name, conv, label in _SUMMARY_SINGLE_SPECS:
    if name == "nv_gpu_util":
      yield "nvidia_gpu", "value", ["gpu_util"], name, conv, label
      yield "nvidia_gpu", "value", ["utilization"], name, conv, label
      continue
    yield typ, val, events, name, conv, label
  for fw in _SUMMARY_FIRST_WIN_SPECS:
    for typ, events, conv in fw["tries"]:
      yield typ, fw["val_col"], events, fw["name"], conv, fw["label"]
  for imc_typ, events, conv in _summary_intel_imc_bw_tries():
    yield imc_typ, "arc", events, "mbw", conv, "DRAMBW[GB/s]"


def _summary_metric_specs():
  """Ordered (typ, val, events, name, conv, label) for plot() second pass (plot columns only)."""
  out = list(_SUMMARY_SINGLE_SPECS)
  for fw in _SUMMARY_FIRST_WIN_SPECS:
    out.append(("", fw["val_col"], [], fw["name"], 0, fw["label"]))
  out.append(("intel_imc", "arc", [], "mbw", _CAS_BW_CONV, "DRAMBW[GB/s]"))
  return out


class SummaryPlot():
  """Builds a grid of Bokeh step plots (one per metric) from jid_table aggregate DataFrames.

    """

  def __init__(self, jt):
    """Store jid, jt, and host_list from the given jid_table (or HostDataProvider).

        """
    self.jid = jt.jid
    self.jt = jt
    self.host_list = jt.host_list

  def plot_metric(self, df, metric, label, y_range_end=None):
    """Create one Bokeh figure with step glyphs per host for the given metric column and label.

        """
    s = time.time()

    df = df[["time", "host", metric]]

    y_min_value = df[metric].min()
    if y_range_end is None or pd_isna(y_range_end):
      y_range_end = 1.1 * df[metric].max()
    y_range_start = y_min_value if y_min_value < 0 else 0
    if math.isnan(y_range_end):
      y_range_end = 0
    if math.isnan(y_range_start):
      y_range_start = 0
    if y_range_end <= y_range_start:
      # Keep a non-degenerate y-range so all-zero/all-constant series still render.
      y_range_end = y_range_start + 1

    label_text = (label or "").strip() or metric

    plot = figure(
        width=400,
        height=150,
        x_axis_type="datetime",
        y_range=Range1d(y_range_start, y_range_end),
        x_axis_label="Time",
        y_axis_label=label_text,
        title=label_text,
    )
    set_linear_axes_plain_numeric(plot)
    plot.xaxis.formatter = tz_aware_bokeh_tick_formatter()

    num_hover = new_plain_number_hover_formatter()
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
            tooltips=_hover_tooltip_html(label_text, metric),
            formatters={
                "@time": "datetime",
                f"@{metric}": num_hover,
            },
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

    df = self.jt.get_host_time_df()
    if df.empty or not self.host_list:
      raise ValueError(MSG_NO_METRIC_DATA)

    for typ, val, events, name, conv, label in _SUMMARY_SINGLE_SPECS:
      if name == "nv_gpu_util":
        continue
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
        keep_sparse = (
            name in _SUMMARY_ALLOW_PARTIAL_NULL and df[name].notna().any()
        )
        if not keep_sparse:
          del df[name]
      log.debug("time to compute %s: %s", name, time.time() - s)

    df = _merge_nvidia_gpu_util_column(df, self.jt)

    for fw in _SUMMARY_FIRST_WIN_SPECS:
      s = time.time()
      df = _merge_first_full_coverage(
          df, self.jt, fw["name"], fw["val_col"], fw["tries"])
      log.debug("time to compute %s: %s", fw["name"], time.time() - s)

    df = _merge_first_full_coverage(
        df, self.jt, "mbw", "arc", _summary_intel_imc_bw_tries())

    metrics = _summary_metric_specs()

    if 'acycles' in df.columns and 'mcycles' in df.columns:
      df["freq"] = 2.7 * df["acycles"] / df["mcycles"]
      metrics += [("freq", "arc", [], "freq", 1, "[GHz]")]
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
      if name in _SUMMARY_SKIP_PLOT_METRICS:
        continue
      if name == "freq":
        freq_max = df[name].max()
        if freq_max is None or math.isnan(freq_max) or freq_max <= 500:
          continue
      y_top = (
          _summary_nv_mem_used_y_range_end(df) if name == "nv_mem_used_mb" else None
      )
      plots += [self.plot_metric(df, name, label, y_range_end=y_top)]

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
    for typ, val_col, events, name, conv, _label in iter_summary_aggregate_attempts():
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
