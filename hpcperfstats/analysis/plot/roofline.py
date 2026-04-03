"""Roofline plot: arithmetic intensity vs performance (GFLOP/s) from jid_table FLOPS and memory bandwidth.

Uses the same PMC sources as SummaryPlot (AMD or Intel). Draws the roofline curve and scatter of (AI, perf) points.

AMD path requires amd64_df MBW channels (monitor enables these on AMD family 17h/19h only). Intel memory
side tries IMC types in INTEL_IMC_STATS_TYPES (SNB through SKX). Intel FLOPS use FP_ARITH when present,
else SNB/IVB-style SSE/AVX double counter proxies.
"""
import math
import numpy
from bokeh.models import ColumnDataSource, HoverTool
from bokeh.plotting import figure

from hpcperfstats.analysis.gen.utils import (
    ARM_IMC_STATS_TYPES,
    INTEL_CORE_PMC_TYPES_ORDERED,
    INTEL_FP_ARITH_ALL_EVENTS,
    INTEL_IMC_STATS_TYPES,
    INTEL_LEGACY_SSE_FLOP_EVENTS,
    new_plain_number_hover_formatter,
)
from hpcperfstats.analysis.bokeh_job_embed import figure_embed_kw
from hpcperfstats.analysis.plot.roofline_peaks import infer_cpu_roofline_peak_flops_and_bw_gbps


# Default peak specs (GFLOP/s and GB/s) when not in config; ridge = peak_flops / peak_bw
DEFAULT_PEAK_FLOPS_GF = 1000.0
DEFAULT_PEAK_BW_GB = 100.0


def _hover_tooltip_html_roofline_job():
    """Build HTML hover template with spacing between multi-point hits."""
    return """
    <div style="padding-bottom:6px; margin-bottom:6px; border-bottom:1px solid #d0d7de;">
      <div><strong>Line:</strong> Job</div>
      <div><strong>host:</strong> @host</div>
      <div><strong>AI (FLOP/byte):</strong> @ai{custom}</div>
      <div><strong>Perf (GFLOP/s):</strong> @perf{custom}</div>
      <div><strong>time:</strong> @time</div>
    </div>
  """


def _aggregate_arc(jt, typ, events, conv):
    """Get aggregate df for typ/events from arc deltas."""
    agg = jt.get_aggregate_df(typ, "arc", events, conv)
    return agg, "arc"


def _aggregate_value(jt, typ, events, conv):
    """Get aggregate df for typ/events from value samples."""
    agg = jt.get_aggregate_df(typ, "value", events, conv)
    return agg, "value"


def _merge_weighted_event_arcs(jt, intel_typ, event_weights, attempts, label):
    """Sum per-(host,time) arc-derived GF/s for events with different FLOP weights."""
    merged = None
    for ev, weight in event_weights:
        agg, src = _aggregate_arc(jt, intel_typ, [ev], 1e-9 * weight)
        attempts.append(
            f"{label}:{intel_typ} event={ev} rows={len(agg.index)} src={src}"
        )
        if agg.empty or "sum_val" not in agg.columns:
            continue
        part = agg[["host", "time", "sum_val"]].rename(
            columns={"sum_val": "flops_gf"}
        )
        if merged is None:
            merged = part
        else:
            merged = merged.merge(
                part, on=["host", "time"], how="outer", suffixes=("_x", "_y")
            )
            merged["flops_gf"] = merged["flops_gf_x"].fillna(0) + merged[
                "flops_gf_y"
            ].fillna(0)
            merged = merged[["host", "time", "flops_gf"]]
    if merged is None or merged.empty:
        return None
    if (merged["flops_gf"].fillna(0) == 0).all():
        return None
    return merged


def _intel_fp_arith_flops_gf(jt, attempts):
    """GFLOP/s from summed FP_ARITH_INST_RETIRED_* on Intel PMC or cpu_counter_metrics."""
    fp_events = list(INTEL_FP_ARITH_ALL_EVENTS)
    for core_typ in INTEL_CORE_PMC_TYPES_ORDERED:
        cand, cand_src = _aggregate_arc(jt, core_typ, fp_events, 1e-9)
        attempts.append(
            f"intel_fp_arith:{core_typ} rows(flops={len(cand.index)}) src={cand_src}"
        )
        if not cand.empty and "sum_val" in cand.columns:
            return cand.rename(columns={"sum_val": "flops_gf"})[
                ["host", "time", "flops_gf"]
            ]
    return None


def _intel_legacy_sse_flops_gf(jt, attempts):
    """GFLOP/s from SNB/IVB-style SSE/AVX double events when FP_ARITH is absent."""
    for core_typ in INTEL_CORE_PMC_TYPES_ORDERED:
        merged = _merge_weighted_event_arcs(
            jt,
            core_typ,
            INTEL_LEGACY_SSE_FLOP_EVENTS,
            attempts,
            "intel_legacy_sse",
        )
        if merged is not None:
            return merged
    return None


def _intel_imc_bw_gb(jt, attempts):
    """Memory bandwidth (GB/s) from first IMC type with CAS_READS+CAS_WRITES."""
    conv = 64 / (1024 ** 3)
    for imc_typ in INTEL_IMC_STATS_TYPES:
        agg_bw, bw_src = _aggregate_arc(
            jt, imc_typ, ["CAS_READS", "CAS_WRITES"], conv
        )
        attempts.append(
            f"{imc_typ} rows(bw={len(agg_bw.index)}) src(bw={bw_src})"
        )
        if not agg_bw.empty and "sum_val" in agg_bw.columns:
            return agg_bw.rename(columns={"sum_val": "bw_gb"})[
                ["host", "time", "bw_gb"]
            ]
    return None


def _arm_dcgm_flops_bw(jt, attempts):
    """Approximate ARM roofline from cpu_counter_metrics synthetic counters."""
    flops_agg, flops_src = _aggregate_arc(jt, "cpu_counter_metrics", ["ARM_EST_FLOPS"], 1e-9)
    bw_agg, bw_src = _aggregate_arc(
        jt, "cpu_counter_metrics", ["ARM_DRAM_BW_BYTES"], 1 / (1024 ** 3)
    )
    attempts.append(
        f"arm_dcgm:cpu_counter_metrics rows(flops={len(flops_agg.index)}, bw={len(bw_agg.index)}) "
        f"src(flops={flops_src}, bw={bw_src})"
    )
    if (
        flops_agg.empty
        or "sum_val" not in flops_agg.columns
        or bw_agg.empty
        or "sum_val" not in bw_agg.columns
    ):
        return None, None
    flops = flops_agg.rename(columns={"sum_val": "flops_gf"})[["host", "time", "flops_gf"]]
    bw = bw_agg.rename(columns={"sum_val": "bw_gb"})[["host", "time", "bw_gb"]]
    return flops, bw


def _arm_imc_bw_gb(jt, attempts):
    """Memory bandwidth (GB/s) from ARM IMC CAS_READS+CAS_WRITES."""
    conv = 64 / (1024 ** 3)
    for imc_typ in ARM_IMC_STATS_TYPES:
        agg_bw, bw_src = _aggregate_arc(jt, imc_typ, ["CAS_READS", "CAS_WRITES"], conv)
        attempts.append(
            f"arm_imc:{imc_typ} rows(bw={len(agg_bw.index)}) src(bw={bw_src})"
        )
        if not agg_bw.empty and "sum_val" in agg_bw.columns:
            return agg_bw.rename(columns={"sum_val": "bw_gb"})[
                ["host", "time", "bw_gb"]
            ]
    return None


def _get_flops_bw_df_and_reason(jt):
    """Get (df, reason) where df has host,time,flops_gf,bw_gb or None with detailed reason."""
    base = jt.get_host_time_df()
    if base.empty or not jt.host_list:
        return (None, "No hosts/timestamps found in host_data for this job/time range")

    flops_gf = None
    bw_gb = None
    attempts = []

    # AMD: FLOPS and MBW channels
    agg_flops, flops_src = _aggregate_arc(jt, "amd64_pmc", ["FLOPS"], 1e-9)
    # Sum all available DRAM channel counters; keep 0–3 for backwards
    # compatibility but include 4–7 when present so newer parts are not
    # artificially bandwidth-limited in the plot.
    amd_bw_events = [
        "MBW_CHANNEL_0",
        "MBW_CHANNEL_1",
        "MBW_CHANNEL_2",
        "MBW_CHANNEL_3",
        "MBW_CHANNEL_4",
        "MBW_CHANNEL_5",
        "MBW_CHANNEL_6",
        "MBW_CHANNEL_7",
    ]
    agg_bw, bw_src = _aggregate_arc(
        jt,
        "amd64_df",
        amd_bw_events,
        2 / (1024 ** 3),
    )
    attempts.append(
        f"amd rows(flops={len(agg_flops.index)}, bw={len(agg_bw.index)}) src(flops={flops_src}, bw={bw_src})"
    )
    if not agg_flops.empty and "sum_val" in agg_flops.columns and not agg_bw.empty and "sum_val" in agg_bw.columns:
        flops_gf = agg_flops.rename(columns={"sum_val": "flops_gf"})[["host", "time", "flops_gf"]]
        bw_gb = agg_bw.rename(columns={"sum_val": "bw_gb"})[["host", "time", "bw_gb"]]

    # Intel: FP (FP_ARITH or legacy SSE) and IMC CAS_READS+CAS_WRITES
    if flops_gf is None or bw_gb is None:
        if flops_gf is None:
            flops_gf = _intel_fp_arith_flops_gf(jt, attempts)
        if flops_gf is None:
            flops_gf = _intel_legacy_sse_flops_gf(jt, attempts)
        if bw_gb is None:
            bw_gb = _intel_imc_bw_gb(jt, attempts)
        if bw_gb is None:
            bw_gb = _arm_imc_bw_gb(jt, attempts)

    # ARM/DCGM fallback: approximate FLOPS and DRAM bytes from synthetic
    # cpu_counter_metrics events populated by the monitor.
    if flops_gf is None or bw_gb is None:
        arm_flops, arm_bw = _arm_dcgm_flops_bw(jt, attempts)
        if flops_gf is None:
            flops_gf = arm_flops
        if bw_gb is None:
            bw_gb = arm_bw

    if flops_gf is None or bw_gb is None:
        # Distinguish the common architecture-specific cases so users get a
        # clearer message about what is missing.
        attempted = "; ".join(attempts)
        reason = (
            "Missing roofline counters in host_data (need FLOPS + memory-bandwidth counters). "
            f"Attempted: {attempted}"
        )
        # Heuristic: ARM/CPU via DCGM backends emit cpu_counter_metrics but may
        # lack any IMC/DF CAS/MBW sources. Do not silently claim generic
        # missing counters when only bandwidth is absent on ARM.
        has_cpu_counter_metrics = any("cpu_counter_metrics" in a for a in attempts)
        has_any_imc_or_df = any(
            t.startswith("amd") or "imc" in t
            for t in [att.split(":")[0] for att in attempts if att]
        )
        if has_cpu_counter_metrics and not has_any_imc_or_df:
            reason = (
                "Roofline not available on this job: cpu_counter_metrics FLOPS "
                "are present (e.g. via DCGM backend), but no DRAM bandwidth "
                "source (AMD DF or Intel IMC CAS_READS/CAS_WRITES) was found "
                "in host_data for these hosts."
            )
        return (None, reason)

    df = base.merge(flops_gf, on=["host", "time"], how="inner")
    df = df.merge(bw_gb, on=["host", "time"], how="inner")
    if df.empty:
        return (
            None,
            "Roofline counters found but no overlapping host/time samples after merge",
        )
    return (df, None)


def _build_roofline_figure(df, peak_flops_gf, peak_bw_gb, title):
    """Render a roofline figure from host,time,flops_gf,bw_gb data."""
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
    hover_num_ai = new_plain_number_hover_formatter()
    hover_num_perf = new_plain_number_hover_formatter()
    hover_job = HoverTool(
        tooltips=_hover_tooltip_html_roofline_job(),
        formatters={"@ai": hover_num_ai, "@perf": hover_num_perf},
        renderers=[],  # set after circle is added
    )
    p = figure(
        **figure_embed_kw(
            400,
            x_axis_type="log",
            y_axis_type="log",
            x_range=(plot_ai_min, plot_ai_max),
            y_range=(min(perf.min(), peak_bw_gb * plot_ai_min) * 0.5, peak_flops_gf * 1.2),
            x_axis_label="Arithmetic intensity (FLOP/byte)",
            y_axis_label="Performance (GFLOP/s)",
            title=title,
            tools=["pan", "wheel_zoom", "box_zoom", "reset", "save"],
        ),
    )
    r_roof = p.line("ai", "perf", source=roof_source, line_width=2, color="navy")
    # Bokeh 3.4+: use scatter(size=...) instead of circle(size=...).
    r_job = p.scatter(
        "ai",
        "perf",
        source=source,
        size=4,
        marker="circle",
        alpha=0.5,
        color="coral",
    )
    hover_roof.renderers = [r_roof]
    hover_job.renderers = [r_job]
    p.add_tools(hover_roof, hover_job)
    return p


def _merge_gpu_flops_bw_on_base(base, flops_agg, bw_agg):
    """Inner-join FLOPS and BW aggregates onto host/time base; None if unusable or empty."""
    if (
        flops_agg.empty
        or "sum_val" not in flops_agg.columns
        or bw_agg.empty
        or "sum_val" not in bw_agg.columns
    ):
        return None
    flops_gf = flops_agg.rename(columns={"sum_val": "flops_gf"})[
        ["host", "time", "flops_gf"]
    ]
    bw_gb = bw_agg.rename(columns={"sum_val": "bw_gb"})[["host", "time", "bw_gb"]]
    df = base.merge(flops_gf, on=["host", "time"], how="inner").merge(
        bw_gb, on=["host", "time"], how="inner"
    )
    return df if not df.empty else None


def _try_gpu_roofline_flops_bw_merge(
    jt,
    gpu_typ,
    base,
    source_tag,
    aggregate_fn,
    flops_events,
    flops_conv,
    bw_events,
    bw_conv,
):
    """One arc or value attempt for strict GPU roofline; returns (df_or_none, log_line, overlap_miss_or_none)."""
    flops_agg, flops_src = aggregate_fn(jt, gpu_typ, flops_events, flops_conv)
    bw_agg, bw_src = aggregate_fn(jt, gpu_typ, bw_events, bw_conv)
    log_line = (
        f"{gpu_typ}:{source_tag} rows(flops={len(flops_agg.index)}, bw={len(bw_agg.index)}) "
        f"src(flops={flops_src}, bw={bw_src})"
    )
    df = _merge_gpu_flops_bw_on_base(base, flops_agg, bw_agg)
    if df is not None:
        return df, log_line, None
    overlap_miss = None
    if (
        not flops_agg.empty
        and "sum_val" in flops_agg.columns
        and not bw_agg.empty
        and "sum_val" in bw_agg.columns
    ):
        overlap_miss = (
            f"{gpu_typ}: counters found in {source_tag} but no overlapping host/time samples"
        )
    return None, log_line, overlap_miss


def _get_gpu_flops_bw_df_and_reason(jt):
    """Get GPU roofline from arc-derived GFLOP/s and PCIe/NvLink GB/s (monitor DCGM PROF bytes)."""
    base = jt.get_host_time_df()
    if base.empty or not jt.host_list:
        return (None, "No hosts/timestamps found in host_data for this job/time range")

    attempts = []
    missing_reasons = []
    # Bandwidth axis uses ``gpu_io_link_total_bytes`` (cumulative DCGM PROF PCIe+NvLink
    # byte counters), not framebuffer proxy rates. FLOPS axis uses integrated ``gpu_flops``.
    # amd_gpu does not yet emit link bytes; nvidia_gpu only.
    _gpu_roofline_branches = (
        (
            "arc",
            _aggregate_arc,
            ["gpu_flops"],
            1e-9,
            ["gpu_io_link_total_bytes"],
            1 / (1024**3),
        ),
    )

    for gpu_typ in ("nvidia_gpu",):
        for tag, agg_fn, fe, fc, be, bc in _gpu_roofline_branches:
            df, line, miss = _try_gpu_roofline_flops_bw_merge(
                jt, gpu_typ, base, tag, agg_fn, fe, fc, be, bc
            )
            attempts.append(line)
            if df is not None:
                return (df, None)
            if miss:
                missing_reasons.append(miss)

    detail = "; ".join(missing_reasons) if missing_reasons else "; ".join(attempts)
    return (
        None,
        "Missing strict GPU roofline counters in host_data "
        "(need nvidia_gpu arc for gpu_flops and gpu_io_link_total_bytes). "
        f"Attempted: {detail}",
    )


def plot_roofline_from_jid_table(jt, peak_flops_gf=None, peak_bw_gb=None):
    """Build CPU/host roofline plot from jid_table."""
    if not jt.host_list:
        return None
    df, _reason = _get_flops_bw_df_and_reason(jt)
    if df is None or df.empty:
        return None
    inf_f, inf_b = infer_cpu_roofline_peak_flops_and_bw_gbps(jt)
    peak_flops_gf = peak_flops_gf if peak_flops_gf is not None else inf_f
    peak_bw_gb = peak_bw_gb if peak_bw_gb is not None else inf_b
    return _build_roofline_figure(
        df,
        peak_flops_gf=peak_flops_gf,
        peak_bw_gb=peak_bw_gb,
        title="CPU Roofline (job)",
    )


def plot_gpu_roofline_from_jid_table(jt, peak_flops_gf=None, peak_bw_gb=None):
    """Build GPU roofline from jid_table (GFLOP/s vs PCIe/NvLink byte-arc GB/s)."""
    if not jt.host_list:
        return None
    df, _reason = _get_gpu_flops_bw_df_and_reason(jt)
    if df is None or df.empty:
        return None
    return _build_roofline_figure(
        df,
        peak_flops_gf=peak_flops_gf,
        peak_bw_gb=peak_bw_gb,
        title="GPU Roofline (job, PCIe/NvLink bytes)",
    )


def plot_and_reason_roofline_from_jid_table(jt, peak_flops_gf=None, peak_bw_gb=None):
    """Build roofline plot and return (figure_or_none, unavailable_reason_or_none)."""
    if not jt.host_list:
        return (None, "No hosts found in host_data for this job/time range")

    df, reason = _get_flops_bw_df_and_reason(jt)
    if df is None or df.empty:
        return (None, reason)

    fig = plot_roofline_from_jid_table(
        jt, peak_flops_gf=peak_flops_gf, peak_bw_gb=peak_bw_gb
    )
    if fig is None:
        return (None, reason or "No valid roofline points after AI/perf filtering")
    return (fig, None)


def plot_and_reason_gpu_roofline_from_jid_table(jt, peak_flops_gf=None, peak_bw_gb=None):
    """Build strict GPU roofline plot and return (figure_or_none, unavailable_reason_or_none)."""
    if not jt.host_list:
        return (None, "No hosts found in host_data for this job/time range")

    df, reason = _get_gpu_flops_bw_df_and_reason(jt)
    if df is None or df.empty:
        return (None, reason)

    fig = plot_gpu_roofline_from_jid_table(
        jt, peak_flops_gf=peak_flops_gf, peak_bw_gb=peak_bw_gb
    )
    if fig is None:
        return (None, reason or "No valid GPU roofline points after AI/perf filtering")
    return (fig, None)
