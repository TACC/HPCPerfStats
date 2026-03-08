"""Roofline plot: arithmetic intensity vs performance (GFLOP/s) from jid_table FLOPS and memory bandwidth.

Uses the same PMC sources as SummaryPlot (AMD or Intel). Draws the roofline curve and scatter of (AI, perf) points.
"""
import math
import numpy
from bokeh.models import ColumnDataSource, HoverTool
from bokeh.plotting import figure

# Default peak specs (GFLOP/s and GB/s) when not in config; ridge = peak_flops / peak_bw
DEFAULT_PEAK_FLOPS_GF = 1000.0
DEFAULT_PEAK_BW_GB = 100.0


def _get_flops_bw_df(jt):
    """Get DataFrame with columns host, time, flops_gf, bw_gb from jid_table. Returns None if no data."""
    base = jt.get_host_time_df()
    if base.empty or not jt.host_list:
        return None

    flops_gf = None
    bw_gb = None

    # AMD: FLOPS and MBW channels
    agg_flops = jt.get_aggregate_df("amd64_pmc", "arc", ["FLOPS"], 1e-9)
    agg_bw = jt.get_aggregate_df(
        "amd64_df", "arc",
        ["MBW_CHANNEL_0", "MBW_CHANNEL_1", "MBW_CHANNEL_2", "MBW_CHANNEL_3"],
        2 / (1024 ** 3),
    )
    if not agg_flops.empty and "sum_val" in agg_flops.columns and not agg_bw.empty and "sum_val" in agg_bw.columns:
        flops_gf = agg_flops.rename(columns={"sum_val": "flops_gf"})[["host", "time", "flops_gf"]]
        bw_gb = agg_bw.rename(columns={"sum_val": "bw_gb"})[["host", "time", "bw_gb"]]

    # Intel: FP_ARITH 32b+64b and IMC CAS_READS+CAS_WRITES
    if flops_gf is None or bw_gb is None:
        fp_events = [
            "FP_ARITH_INST_RETIRED_SCALAR_DOUBLE",
            "FP_ARITH_INST_RETIRED_128B_PACKED_DOUBLE",
            "FP_ARITH_INST_RETIRED_256B_PACKED_DOUBLE",
            "FP_ARITH_INST_RETIRED_512B_PACKED_DOUBLE",
            "FP_ARITH_INST_RETIRED_SCALAR_SINGLE",
            "FP_ARITH_INST_RETIRED_128B_PACKED_SINGLE",
            "FP_ARITH_INST_RETIRED_256B_PACKED_SINGLE",
            "FP_ARITH_INST_RETIRED_512B_PACKED_SINGLE",
        ]
        agg_flops = jt.get_aggregate_df("intel_8pmc3", "arc", fp_events, 1e-9)
        agg_bw = jt.get_aggregate_df(
            "intel_skx_imc", "arc", ["CAS_READS", "CAS_WRITES"],
            64 / (1024 ** 3),
        )
        if not agg_flops.empty and "sum_val" in agg_flops.columns and not agg_bw.empty and "sum_val" in agg_bw.columns:
            flops_gf = agg_flops.rename(columns={"sum_val": "flops_gf"})[["host", "time", "flops_gf"]]
            bw_gb = agg_bw.rename(columns={"sum_val": "bw_gb"})[["host", "time", "bw_gb"]]

    if flops_gf is None or bw_gb is None:
        return None

    df = base.merge(flops_gf, on=["host", "time"], how="inner")
    df = df.merge(bw_gb, on=["host", "time"], how="inner")
    if df.empty:
        return None
    return df


def plot_roofline_from_jid_table(jt, peak_flops_gf=None, peak_bw_gb=None):
    """Build roofline plot from jid_table (ORM). Returns a Bokeh figure or None if no FLOPS/BW data.

    X-axis: arithmetic intensity (FLOP/byte, log scale).
    Y-axis: performance (GFLOP/s, log scale).
    Scatter: (AI, perf) per (host, time). Roofline curve: min(peak_flops, peak_bw * AI).

    peak_flops_gf and peak_bw_gb can be provided; otherwise DEFAULT_PEAK_FLOPS_GF and DEFAULT_PEAK_BW_GB are used.
    """
    if not jt.host_list:
        return None

    df = _get_flops_bw_df(jt)
    if df is None or df.empty:
        return None

    peak_flops_gf = peak_flops_gf if peak_flops_gf is not None else DEFAULT_PEAK_FLOPS_GF
    peak_bw_gb = peak_bw_gb if peak_bw_gb is not None else DEFAULT_PEAK_BW_GB

    # Arithmetic intensity: FLOP/byte = (GFLOP/s) / (GB/s) = same ratio
    df = df.copy()
    df["bw_gb"] = df["bw_gb"].replace(0, numpy.nan)
    df["ai"] = df["flops_gf"] / df["bw_gb"]
    df = df.dropna(subset=["ai", "flops_gf"])
    df = df[df["ai"] > 0]
    df = df[df["flops_gf"] > 0]
    if df.empty:
        return None

    ai = df["ai"].values
    perf = df["flops_gf"].values
    host = df["host"].tolist()
    time_vals = df["time"].astype(str).tolist()

    # Clamp AI for plot range (avoid log(0))
    ai_min, ai_max = max(1e-3, float(ai.min())), max(1e-2, float(ai.max()))
    ridge_ai = peak_flops_gf / peak_bw_gb
    plot_ai_max = max(ai_max * 2, ridge_ai * 1.5, 10.0)
    plot_ai_min = min(ai_min / 2, 1e-2)

    # Roofline curve: from (plot_ai_min, peak_bw*plot_ai_min) to (ridge_ai, peak_flops), then flat
    n_pts = 80
    ai_curve = numpy.logspace(
        math.log10(max(plot_ai_min, 1e-4)),
        math.log10(max(ridge_ai, plot_ai_min * 1.1)),
        num=max(2, n_pts // 2),
    )
    perf_curve = peak_bw_gb * ai_curve
    ai_curve = list(ai_curve)
    perf_curve = list(perf_curve)
    ai_curve.append(ridge_ai)
    perf_curve.append(peak_flops_gf)
    flat_ai = numpy.logspace(
        math.log10(ridge_ai),
        math.log10(plot_ai_max),
        num=n_pts // 2,
    )
    ai_curve.extend(flat_ai)
    perf_curve.extend([peak_flops_gf] * len(flat_ai))

    source = ColumnDataSource(dict(ai=ai, perf=perf, host=host, time=time_vals))
    roof_source = ColumnDataSource(dict(ai=ai_curve, perf=perf_curve))

    # No legend; identify series by hovering (popup shows line name).
    hover_roof = HoverTool(
        tooltips=[("Line", "Roofline")],
        renderers=[],  # set after line is added
    )
    hover_job = HoverTool(
        tooltips=[
            ("Line", "Job"),
            ("host", "@host"),
            ("AI (FLOP/byte)", "@ai{0.4f}"),
            ("Perf (GFLOP/s)", "@perf{0.2f}"),
            ("time", "@time"),
        ],
        renderers=[],  # set after circle is added
    )
    p = figure(
        width=500,
        height=400,
        x_axis_type="log",
        y_axis_type="log",
        x_range=(plot_ai_min, plot_ai_max),
        y_range=(min(perf.min(), peak_bw_gb * plot_ai_min) * 0.5, peak_flops_gf * 1.2),
        x_axis_label="Arithmetic intensity (FLOP/byte)",
        y_axis_label="Performance (GFLOP/s)",
        title="Roofline (job)",
        tools=["pan", "wheel_zoom", "box_zoom", "reset", "save"],
    )
    r_roof = p.line("ai", "perf", source=roof_source, line_width=2, color="navy")
    r_job = p.circle("ai", "perf", source=source, size=4, alpha=0.5, color="coral")
    hover_roof.renderers = [r_roof]
    hover_job.renderers = [r_job]
    p.add_tools(hover_roof, hover_job)
    return p
