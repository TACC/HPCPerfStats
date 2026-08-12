"""
Roofline plot: arithmetic intensity vs performance (GFLOP/s) from jid_table
FLOPS and memory bandwidth.

Uses the same PMC sources as SummaryPlot (AMD or Intel). Draws the roofline
curve and scatter of (AI, perf) points.

AMD path requires amd64_df MBW channels (monitor enables these on AMD family
17h/19h only). Intel memory side tries IMC types in INTEL_IMC_STATS_TYPES (SNB
through SKX). Intel FLOPS use FP_ARITH when present, else SNB/IVB-style SSE/AVX
double counter proxies.

Attributes:
  DEFAULT_PEAK_BW_GB: Default nominal peak bandwidth (GB/s).
  DEFAULT_PEAK_FLOPS_GF: Default nominal peak FLOPS (GFLOP/s).
  GPU_ROOFLINE_BW_AXIS_LINK: Measured-axis mode for PCIe/NVLink/Xe Link.
  GPU_ROOFLINE_BW_AXIS_MEMORY: Measured-axis mode for GPU memory BW.
  GPU_ROOFLINE_TITLE_LINK: Bokeh/SPA title when link BW is used.
  GPU_ROOFLINE_TITLE_MEMORY: Bokeh/SPA title when memory BW is used.
  ROOFLINE_NOMINAL_PEAKS_INVALID_REASON: Unavailable reason for bad peaks.
  _GPU_BYTES_TO_GIB: Bytes/s to GiB/s conversion shared by mem + link axes.
  _INTEL_GPU_LINK_EVENTS: Intel PCIe + Xe Link cumulative byte events.
  _NVIDIA_GPU_LINK_DIRECTIONAL_EVENTS: NVIDIA per-direction link byte events.
"""
from __future__ import annotations

from typing import Any, Optional, Tuple

import math
import numpy
from bokeh.models import ColumnDataSource, HoverTool
from bokeh.plotting import figure

from hpcperfstats.analysis.metrics.lib.gen.utils import format_plain_decimal
from hpcperfstats.dbload.lib.monitor_naming.canonical import (
    INTEL_FP_ARITH_ALL_EVENTS,
    INTEL_LEGACY_SSE_FLOP_EVENTS,
)
from hpcperfstats.dbload.lib.monitor_naming.resolve import (
    amd_df_type_names,
    amd_pmc_type_names,
    arm_dram_bw_event_names,
    arm_est_flops_event_names,
    arm_imc_types_probe_order,
    core_pmc_types_probe_order,
    dram_cas_read_write_pairs,
    fp_ops_retired_event_names,
    hbm_cas_read_write_pairs,
    host_cpu_hw_type_names,
    imc_types_probe_order,
)
from hpcperfstats.analysis.metrics.lib.gen.imc_cas_bw import (
    agg_sum_val_to_bw_frame,
    combine_cas_bw_frames,
)
from hpcperfstats.analysis.metrics.lib.bokeh_job_embed import figure_embed_kw
from hpcperfstats.analysis.metrics.lib.plot.roofline_peaks import (
    infer_cpu_roofline_peak_flops_and_bw_gbps,
    infer_gpu_roofline_peak_flops_and_bw_gbps,
)

# Default peak specs (GFLOP/s and GB/s) when not in config; ridge = peak_flops / peak_bw
DEFAULT_PEAK_FLOPS_GF = 1000.0
DEFAULT_PEAK_BW_GB = 100.0

# GPU roofline measured-axis modes and user-facing titles.
GPU_ROOFLINE_BW_AXIS_MEMORY = "memory_bw"
GPU_ROOFLINE_BW_AXIS_LINK = "pcie_nvlink"
GPU_ROOFLINE_TITLE_MEMORY = "GPU Roofline (Memory BW)"
GPU_ROOFLINE_TITLE_LINK = "GPU Roofline (PCIe/NvLink)"

# Match link-arc and host_roofline_peak conversions (GiB/s, not decimal GB/s).
_GPU_BYTES_TO_GIB = 1 / (1024**3)

_NVIDIA_GPU_LINK_DIRECTIONAL_EVENTS = (
    "gpu_pcie_tx_bytes",
    "gpu_pcie_rx_bytes",
    "gpu_nvlink_tx_bytes",
    "gpu_nvlink_rx_bytes",
)
_INTEL_GPU_LINK_EVENTS = (
    "gpu_pcie_tx_bytes",
    "gpu_pcie_rx_bytes",
    "gpu_xe_link_tx_bytes",
    "gpu_xe_link_rx_bytes",
)


def _nominal_roofline_peaks_valid(peak_flops_gf: Any, peak_bw_gb: Any) -> Any:
  """
  True when nominal peaks are finite and strictly positive (ridge AI is log-.
  
    scaled).
  
  Args:
    peak_flops_gf (Any): Peak flops gf passed to this helper.
    peak_bw_gb (Any): Peak bw gb passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _nominal_roofline_peaks_valid(None, None)  # doctest: +SKIP
  """
  try:
    pf = float(peak_flops_gf)
    pb = float(peak_bw_gb)
  except (TypeError, ValueError):
    return False
  return bool(
      numpy.isfinite(pf)
      and numpy.isfinite(pb)
      and pf > 0.0
      and pb > 0.0
  )


ROOFLINE_NOMINAL_PEAKS_INVALID_REASON = (
    "Roofline nominal peaks (GFLOP/s or GB/s) are non-finite or non-positive; "
    "cannot render log-scaled roofline."
)


def _hover_tooltip_html_roofline_job() -> Any:
    """
    Build HTML hover template with spacing between multi-point hits.
    
    Returns:
      Any: Open return polymorphism from ``_hover_tooltip_html_roofline_job``:
      concrete type depends on inputs and branch (mapping, scalar, handle, or
      ``None``-like empty).
    
    Examples:
      >>> _hover_tooltip_html_roofline_job()  # doctest: +SKIP
    """
    return """
    <div style="padding-bottom:6px; margin-bottom:6px; border-bottom:1px solid #d0d7de;">
      <div><strong>Line:</strong> Job</div>
      <div><strong>host:</strong> @host</div>
      <div><strong>AI (FLOP/byte):</strong> @ai_plain</div>
      <div><strong>Perf (GFLOP/s):</strong> @perf_plain</div>
      <div><strong>time:</strong> @time</div>
    </div>
  """


def _aggregate_arc(jt: Any, typ: Any, events: Any, conv: Any) -> Any:
    """
    Get aggregate df for typ/events from arc deltas.
    
    Args:
      jt (Any): Jt passed to this helper.
      typ (Any): Typ passed to this helper.
      events (Any): Events passed to this helper.
      conv (Any): Conv passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _aggregate_arc(None, None, None, None)  # doctest: +SKIP
    """
    agg = jt.get_aggregate_df(typ, "arc", events, conv)
    return agg, "arc"


def _aggregate_value(jt: Any, typ: Any, events: Any, conv: Any) -> Any:
    """
    Get aggregate df for typ/events from value samples.
    
    Args:
      jt (Any): Jt passed to this helper.
      typ (Any): Typ passed to this helper.
      events (Any): Events passed to this helper.
      conv (Any): Conv passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _aggregate_value(None, None, None, None)  # doctest: +SKIP
    """
    agg = jt.get_aggregate_df(typ, "value", events, conv)
    return agg, "value"


def _merge_weighted_event_arcs(
  jt: Any,
  intel_typ: Any,
  event_weights: Any,
  attempts: Any,
  label: Any,
) -> Any:
    """
    Sum per-(host,time) arc-derived GF/s for events with different FLOP weights.
    
    Args:
      jt (Any): Jt passed to this helper.
      intel_typ (Any): Intel typ passed to this helper.
      event_weights (Any): Event weights passed to this helper.
      attempts (Any): Attempts passed to this helper.
      label (Any): Label passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _merge_weighted_event_arcs(None, None, None, None, None)
    """
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


def _intel_fp_arith_flops_gf(jt: Any, attempts: Any) -> Any:
    """
    GFLOP/s from summed FP_ARITH_INST_RETIRED_* on Intel PMC or host_cpu_hw.
    
    Args:
      jt (Any): Jt passed to this helper.
      attempts (Any): Attempts passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _intel_fp_arith_flops_gf(None, None)  # doctest: +SKIP
    """
    fp_events = list(INTEL_FP_ARITH_ALL_EVENTS)
    for core_typ in core_pmc_types_probe_order():
        cand, cand_src = _aggregate_arc(jt, core_typ, fp_events, 1e-9)
        attempts.append(
            f"intel_fp_arith:{core_typ} rows(flops={len(cand.index)}) src={cand_src}"
        )
        if not cand.empty and "sum_val" in cand.columns:
            return cand.rename(columns={"sum_val": "flops_gf"})[
                ["host", "time", "flops_gf"]
            ]
    return None


def _intel_legacy_sse_flops_gf(jt: Any, attempts: Any) -> Any:
    """
    GFLOP/s from SNB/IVB-style SSE/AVX double events when FP_ARITH is absent.
    
    Args:
      jt (Any): Jt passed to this helper.
      attempts (Any): Attempts passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _intel_legacy_sse_flops_gf(None, None)  # doctest: +SKIP
    """
    for core_typ in core_pmc_types_probe_order():
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


def _intel_imc_bw_gb(jt: Any, attempts: Any) -> Any:
    """
    Memory bandwidth (GB/s): per IMC type, sum usable dram_cas_* and hbm_cas_*.
    
      (64 B/CAS).
    
    Args:
      jt (Any): Jt passed to this helper.
      attempts (Any): Attempts passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _intel_imc_bw_gb(None, None)  # doctest: +SKIP
    """
    conv = 64 / (1024 ** 3)
    for imc_typ in imc_types_probe_order():
        dram_bw = None
        for read_ev, write_ev in dram_cas_read_write_pairs():
            agg_bw, bw_src = _aggregate_arc(
                jt, imc_typ, [read_ev, write_ev], conv
            )
            attempts.append(
                f"{imc_typ} rows(bw={len(agg_bw.index)}) "
                f"events=({read_ev},{write_ev}) src(bw={bw_src})"
            )
            dram_bw = agg_sum_val_to_bw_frame(agg_bw)
            if dram_bw is not None:
                break
        hbm_bw = None
        for read_ev, write_ev in hbm_cas_read_write_pairs():
            agg_bw, bw_src = _aggregate_arc(
                jt, imc_typ, [read_ev, write_ev], conv
            )
            attempts.append(
                f"{imc_typ} rows(hbm_bw={len(agg_bw.index)}) "
                f"events=({read_ev},{write_ev}) src(bw={bw_src})"
            )
            hbm_bw = agg_sum_val_to_bw_frame(agg_bw)
            if hbm_bw is not None:
                break
        combined = combine_cas_bw_frames(dram_bw, hbm_bw)
        if combined is not None:
            return combined
    return None


def _arm_dcgm_flops_bw(jt: Any, attempts: Any) -> Any:
    """
    Approximate ARM roofline from host_cpu_hw synthetic counters.
    
    Args:
      jt (Any): Jt passed to this helper.
      attempts (Any): Attempts passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _arm_dcgm_flops_bw(None, None)  # doctest: +SKIP
    """
    for hw_typ in host_cpu_hw_type_names():
        for flop_ev in arm_est_flops_event_names():
            flops_agg, flops_src = _aggregate_arc(jt, hw_typ, [flop_ev], 1e-9)
            bw_agg, bw_src = _aggregate_arc(
                jt, hw_typ, list(arm_dram_bw_event_names()), 1 / (1024 ** 3)
            )
            attempts.append(
                f"arm_dcgm:{hw_typ} rows(flops={len(flops_agg.index)}, "
                f"bw={len(bw_agg.index)}) src(flops={flops_src}, bw={bw_src})"
            )
            if (
                not flops_agg.empty
                and "sum_val" in flops_agg.columns
                and not bw_agg.empty
                and "sum_val" in bw_agg.columns
            ):
                flops = flops_agg.rename(columns={"sum_val": "flops_gf"})[
                    ["host", "time", "flops_gf"]
                ]
                bw = bw_agg.rename(columns={"sum_val": "bw_gb"})[
                    ["host", "time", "bw_gb"]
                ]
                return flops, bw
    return None, None
def _arm_imc_bw_gb(jt: Any, attempts: Any) -> Any:
    """
    Memory bandwidth (GB/s) from ARM IMC dram CAS events.
    
    Args:
      jt (Any): Jt passed to this helper.
      attempts (Any): Attempts passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _arm_imc_bw_gb(None, None)  # doctest: +SKIP
    """
    conv = 64 / (1024 ** 3)
    for imc_typ in arm_imc_types_probe_order():
        for read_ev, write_ev in dram_cas_read_write_pairs():
            agg_bw, bw_src = _aggregate_arc(jt, imc_typ, [read_ev, write_ev], conv)
            attempts.append(
                f"arm_imc:{imc_typ} rows(bw={len(agg_bw.index)}) src(bw={bw_src})"
            )
            if not agg_bw.empty and "sum_val" in agg_bw.columns:
                return agg_bw.rename(columns={"sum_val": "bw_gb"})[
                    ["host", "time", "bw_gb"]
                ]
    return None


def _get_flops_bw_df_and_reason(jt: Any) -> Any:
    """
    Get (df, reason) where df has host,time,flops_gf,bw_gb or None with.
    
      detailed.
    
      reason.
    
    Args:
      jt (Any): Jt passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _get_flops_bw_df_and_reason(None)  # doctest: +SKIP
    """
    base = jt.get_host_time_df()
    if base.empty or not jt.host_list:
        return (None, "No hosts/timestamps found in host_data for this job/time range")

    flops_gf = None
    bw_gb = None
    attempts = []

    # AMD: fp_ops_retired and DF channel BW (family dram_chan*_bytes or historical MBW)
    from hpcperfstats.dbload.lib.monitor_naming.canonical import AMD_DF_STATS_TYPES
    from hpcperfstats.dbload.lib.monitor_naming.resolve import amd_df_bw_event_conv_tries

    for pmc_typ in amd_pmc_type_names():
        for flop_ev in fp_ops_retired_event_names():
            agg_flops, flops_src = _aggregate_arc(jt, pmc_typ, [flop_ev], 1e-9)
            for df_typ in amd_df_type_names():
                tries = (
                    amd_df_bw_event_conv_tries()[:1]
                    if df_typ in AMD_DF_STATS_TYPES
                    else amd_df_bw_event_conv_tries()[::-1]
                )
                for bw_events, bw_conv in tries:
                    agg_bw, bw_src = _aggregate_arc(
                        jt,
                        df_typ,
                        list(bw_events),
                        bw_conv,
                    )
                    attempts.append(
                        f"amd rows(flops={len(agg_flops.index)}, bw={len(agg_bw.index)}) "
                        f"src(flops={flops_src}, bw={bw_src})"
                    )
                    if (
                        not agg_flops.empty
                        and "sum_val" in agg_flops.columns
                        and not agg_bw.empty
                        and "sum_val" in agg_bw.columns
                    ):
                        flops_gf = agg_flops.rename(columns={"sum_val": "flops_gf"})[
                            ["host", "time", "flops_gf"]
                        ]
                        bw_gb = agg_bw.rename(columns={"sum_val": "bw_gb"})[
                            ["host", "time", "bw_gb"]
                        ]
                        break
                if flops_gf is not None and bw_gb is not None:
                    break
            if flops_gf is not None and bw_gb is not None:
                break
        if flops_gf is not None and bw_gb is not None:
            break

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
        has_cpu_counter_metrics = any(
            t in a for t in host_cpu_hw_type_names() for a in attempts
        )
        has_any_imc_or_df = any(
            t.startswith("amd") or "imc" in t
            for t in [att.split(":")[0] for att in attempts if att]
        )
        if has_cpu_counter_metrics and not has_any_imc_or_df:
            reason = (
                "Roofline not available on this job: cpu_counter_metrics FLOPS "
                "are present (e.g. via DCGM backend), but no DRAM bandwidth "
                "source (AMD DF or Intel/ARM DRAM CAS counters) was found "
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


def _build_roofline_figure(
  df: Any,
  peak_flops_gf: Any,
  peak_bw_gb: Any,
  title: Any,
  help_plot_key: str = "jobDetailPlot_roofline_cpu",
) -> Any:
    """
    Render a roofline figure from host,time,flops_gf,bw_gb data.
    
    Args:
      df (Any): Df passed to this helper.
      peak_flops_gf (Any): Peak flops gf passed to this helper.
      peak_bw_gb (Any): Peak bw gb passed to this helper.
      title (Any): Title passed to this helper.
      help_plot_key (str): String for help plot key.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _build_roofline_figure(None, None, None, None, "x")  # doctest: +SKIP
    """
    peak_flops_gf = peak_flops_gf if peak_flops_gf is not None else DEFAULT_PEAK_FLOPS_GF
    peak_bw_gb = peak_bw_gb if peak_bw_gb is not None else DEFAULT_PEAK_BW_GB
    if not _nominal_roofline_peaks_valid(peak_flops_gf, peak_bw_gb):
        return None

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
    if not (numpy.isfinite(ridge_ai) and ridge_ai > 0.0):
        return None
    plot_ai_max = max(ai_max * 2, ridge_ai * 1.5, 10.0)
    plot_ai_min = min(ai_min / 2, 1e-2)

    # Roofline curve: from (plot_ai_min, peak_bw*plot_ai_min) to (ridge_ai, peak_flops), then flat
    n_pts = 80
    seg1_lo = max(float(plot_ai_min), 1e-4)
    seg1_hi = max(float(ridge_ai), seg1_lo * 1.01)
    ai_curve = numpy.logspace(
        math.log10(seg1_lo),
        math.log10(seg1_hi),
        num=max(2, n_pts // 2),
    )
    perf_curve = peak_bw_gb * ai_curve
    ai_curve = list(ai_curve)
    perf_curve = list(perf_curve)
    ai_curve.append(ridge_ai)
    perf_curve.append(peak_flops_gf)
    flat_lo = float(ridge_ai)
    flat_hi = float(plot_ai_max)
    if flat_hi <= flat_lo:
        flat_hi = flat_lo * (1.0 + 1e-6)
    flat_ai = numpy.logspace(
        math.log10(flat_lo),
        math.log10(flat_hi),
        num=max(2, n_pts // 2),
    )
    ai_curve.extend(flat_ai)
    perf_curve.extend([peak_flops_gf] * len(flat_ai))

    source = ColumnDataSource(
        dict(
            ai=ai,
            perf=perf,
            host=host,
            time=time_vals,
            ai_plain=[format_plain_decimal(v) for v in ai],
            perf_plain=[format_plain_decimal(v) for v in perf],
        ),
    )
    roof_source = ColumnDataSource(dict(ai=ai_curve, perf=perf_curve))

    # No legend; identify series by hovering (popup shows line name + axis units).
    hover_roof = HoverTool(
        tooltips=[
            ("Line", "Roofline"),
            ("X", "Arithmetic intensity (FLOP/byte)"),
            ("Y", "Performance (GFLOP/s)"),
        ],
        renderers=[],  # set after line is added
    )
    hover_job = HoverTool(
        tooltips=_hover_tooltip_html_roofline_job(),
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
    from hpcperfstats.analysis.metrics.lib.plot.bokeh_job_detail_help_marker import (
        add_job_detail_bokeh_help_marker,
    )
    from hpcperfstats.analysis.metrics.lib.plot.job_detail_bokeh_plot_descriptions import (
        description_for_job_detail_bokeh_plot,
        researcher_use_for_job_detail_bokeh_plot,
    )

    add_job_detail_bokeh_help_marker(
        p,
        description_for_job_detail_bokeh_plot(help_plot_key),
        researcher_use_for_job_detail_bokeh_plot(help_plot_key),
    )
    return p


def _merge_gpu_flops_bw_on_base(base: Any, flops_agg: Any, bw_agg: Any) -> Any:
    """
    Inner-join FLOPS and BW aggregates onto host/time base; None if unusable or.
    
      empty.
    
    Args:
      base (Any): Base passed to this helper.
      flops_agg (Any): Flops agg passed to this helper.
      bw_agg (Any): Bw agg passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _merge_gpu_flops_bw_on_base(None, None, None)  # doctest: +SKIP
    """
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


def _gpu_roofline_df_has_usable_bw(df: Any) -> bool:
    """
    True when *df* has at least one finite positive ``bw_gb`` sample.

    Args:
      df (Any): Merged FLOPS/BW frame, or None.

    Returns:
      bool: Whether the frame can drive a roofline scatter.

    Examples:
      >>> import pandas as pd
      >>> _gpu_roofline_df_has_usable_bw(
      ...     pd.DataFrame({"bw_gb": [0.0, 1.5], "flops_gf": [1.0, 2.0]})
      ... )
      True
    """
    if df is None or getattr(df, "empty", True):
        return False
    if "bw_gb" not in df.columns:
        return False
    bw = df["bw_gb"]
    return bool(((bw > 0) & numpy.isfinite(bw)).any())


def _try_gpu_roofline_flops_bw_merge(
  jt: Any,
  gpu_typ: Any,
  base: Any,
  source_tag: Any,
  flops_aggregate_fn: Any,
  flops_events: Any,
  flops_conv: Any,
  bw_aggregate_fn: Any,
  bw_events: Any,
  bw_conv: Any,
) -> Tuple[Any, str, Optional[str]]:
    """
    One FLOPS+BW attempt for GPU roofline.

    Args:
      jt (Any): Job ``jid_table`` with aggregate helpers.
      gpu_typ (Any): ``host_data.type`` (for example ``nvidia_gpu``).
      base (Any): Host/time base DataFrame.
      source_tag (Any): Short label for attempt logs (for example ``mem_value``).
      flops_aggregate_fn (Any): Arc/value aggregator for FLOPS events.
      flops_events (Any): FLOPS event name list.
      flops_conv (Any): Multiplier applied to FLOPS aggregates.
      bw_aggregate_fn (Any): Arc/value aggregator for bandwidth events.
      bw_events (Any): Bandwidth event name list.
      bw_conv (Any): Multiplier applied to bandwidth aggregates.

    Returns:
      Tuple[Any, str, Optional[str]]: ``(df_or_none, log_line,
      overlap_miss_or_none)``.

    Examples:
      >>> _try_gpu_roofline_flops_bw_merge(  # doctest: +SKIP
      ...     None, "nvidia_gpu", None, "mem_value",
      ...     _aggregate_arc, ["gpu_flops"], 1e-9,
      ...     _aggregate_value, ["gpu_mem_bw_bytes_rate"], _GPU_BYTES_TO_GIB,
      ... )
    """
    flops_agg, flops_src = flops_aggregate_fn(
        jt, gpu_typ, flops_events, flops_conv
    )
    bw_agg, bw_src = bw_aggregate_fn(jt, gpu_typ, bw_events, bw_conv)
    log_line = (
        f"{gpu_typ}:{source_tag} "
        f"rows(flops={len(flops_agg.index)}, bw={len(bw_agg.index)}) "
        f"src(flops={flops_src}, bw={bw_src})"
    )
    df = _merge_gpu_flops_bw_on_base(base, flops_agg, bw_agg)
    if df is not None and _gpu_roofline_df_has_usable_bw(df):
        return df, log_line, None
    overlap_miss = None
    if (
        not flops_agg.empty
        and "sum_val" in flops_agg.columns
        and not bw_agg.empty
        and "sum_val" in bw_agg.columns
    ):
        if df is not None and not _gpu_roofline_df_has_usable_bw(df):
            overlap_miss = (
                f"{gpu_typ}: {source_tag} overlapped but BW samples were "
                "non-positive"
            )
        else:
            overlap_miss = (
                f"{gpu_typ}: counters found in {source_tag} but no overlapping "
                "host/time samples"
            )
    return None, log_line, overlap_miss


def _try_gpu_roofline_axis_attempt(
  jt: Any,
  base: Any,
  attempts: list[str],
  missing_reasons: list[str],
  gpu_typ: str,
  source_tag: str,
  flops_fn: Any,
  bw_fn: Any,
  bw_events: Any,
  bw_axis: str,
) -> Optional[Tuple[Any, Optional[str], Optional[str]]]:
  """
  Run one FLOPS+BW probe and record attempt/miss logs.

  Args:
    jt (Any): Job ``jid_table`` with aggregate helpers.
    base (Any): Host/time base DataFrame.
    attempts (list[str]): Mutable list of attempt log lines.
    missing_reasons (list[str]): Mutable list of overlap/miss reasons.
    gpu_typ (str): ``host_data.type`` name.
    source_tag (str): Short label for attempt logs.
    flops_fn (Any): FLOPS aggregator (``_aggregate_arc`` / ``_aggregate_value``).
    bw_fn (Any): Bandwidth aggregator.
    bw_events (Any): Bandwidth event name sequence.
    bw_axis (str): ``memory_bw`` or ``pcie_nvlink`` when this attempt wins.

  Returns:
    Optional[Tuple[Any, Optional[str], Optional[str]]]: Success triple
    ``(df, None, bw_axis)``, or None to continue probing.

  Examples:
    >>> _try_gpu_roofline_axis_attempt(  # doctest: +SKIP
    ...     None, None, [], [], "nvidia_gpu", "mem_value",
    ...     _aggregate_arc, _aggregate_value, ["gpu_mem_bw_bytes_rate"],
    ...     GPU_ROOFLINE_BW_AXIS_MEMORY,
    ... )
  """
  df, line, miss = _try_gpu_roofline_flops_bw_merge(
      jt,
      gpu_typ,
      base,
      source_tag,
      flops_fn,
      ["gpu_flops"],
      1e-9,
      bw_fn,
      list(bw_events),
      _GPU_BYTES_TO_GIB,
  )
  attempts.append(line)
  if df is not None:
    return (df, None, bw_axis)
  if miss:
    missing_reasons.append(miss)

  # Estimated FLOP/s rate (same family as gpu_mem_bw_bytes_rate) when the
  # cumulative gpu_flops arc is absent from host_data.
  rate_tag = f"{source_tag}_flops_rate"
  df, line, miss = _try_gpu_roofline_flops_bw_merge(
      jt,
      gpu_typ,
      base,
      rate_tag,
      _aggregate_value,
      ["gpu_flops_rate"],
      1e-9,
      bw_fn,
      list(bw_events),
      _GPU_BYTES_TO_GIB,
  )
  attempts.append(line)
  if df is not None:
    return (df, None, bw_axis)
  if miss:
    missing_reasons.append(miss)
  return None


def _get_gpu_flops_bw_df_and_reason(
  jt: Any,
) -> Tuple[Any, Optional[str], Optional[str]]:
    """
    Build GPU roofline samples: prefer memory BW, else PCIe/NVLink/Xe Link.

    Prefer ``gpu_flops`` arc, else ``gpu_flops_rate`` (value). Prefer
    ``gpu_mem_bw_bytes_rate`` (value; same estimated rate Summary uses)
    when usable overlapping samples exist on ``nvidia_gpu`` then ``amd_gpu``.
    Otherwise try link bytes: ``gpu_io_link_total_bytes``, then NVIDIA
    directional PCIe/NVLink arcs, then Intel PCIe+Xe Link (when FLOPS exist).
    Bandwidth and peaks use GiB/s via ``_GPU_BYTES_TO_GIB``.

    Args:
      jt (Any): Job ``jid_table`` with host list and aggregates.

    Returns:
      Tuple[Any, Optional[str], Optional[str]]: ``(df_or_none,
      unavailable_reason_or_none, bw_axis_or_none)`` where *bw_axis* is
      ``memory_bw`` or ``pcie_nvlink``.

    Examples:
      >>> _get_gpu_flops_bw_df_and_reason(None)  # doctest: +SKIP
    """
    base = jt.get_host_time_df()
    if base.empty or not jt.host_list:
        return (
            None,
            "No hosts/timestamps found in host_data for this job/time range",
            None,
        )

    attempts: list[str] = []
    missing_reasons: list[str] = []

    # 1) Memory BW estimate (nvidia then amd). AMD has no link fallback.
    for gpu_typ in ("nvidia_gpu", "amd_gpu"):
        hit = _try_gpu_roofline_axis_attempt(
            jt,
            base,
            attempts,
            missing_reasons,
            gpu_typ,
            "mem_value",
            _aggregate_arc,
            _aggregate_value,
            ["gpu_mem_bw_bytes_rate"],
            GPU_ROOFLINE_BW_AXIS_MEMORY,
        )
        if hit is not None:
            return hit

    # 2) NVIDIA aggregate PROF PCIe+NVLink bytes.
    hit = _try_gpu_roofline_axis_attempt(
        jt,
        base,
        attempts,
        missing_reasons,
        "nvidia_gpu",
        "link_arc_total",
        _aggregate_arc,
        _aggregate_arc,
        ["gpu_io_link_total_bytes"],
        GPU_ROOFLINE_BW_AXIS_LINK,
    )
    if hit is not None:
        return hit

    # 3) NVIDIA directional link bytes (archives / missing aggregate key).
    hit = _try_gpu_roofline_axis_attempt(
        jt,
        base,
        attempts,
        missing_reasons,
        "nvidia_gpu",
        "link_arc_directional",
        _aggregate_arc,
        _aggregate_arc,
        _NVIDIA_GPU_LINK_DIRECTIONAL_EVENTS,
        GPU_ROOFLINE_BW_AXIS_LINK,
    )
    if hit is not None:
        return hit

    # 4) Intel PCIe + Xe Link when FLOPS also exist on intel_gpu.
    hit = _try_gpu_roofline_axis_attempt(
        jt,
        base,
        attempts,
        missing_reasons,
        "intel_gpu",
        "link_arc_intel",
        _aggregate_arc,
        _aggregate_arc,
        _INTEL_GPU_LINK_EVENTS,
        GPU_ROOFLINE_BW_AXIS_LINK,
    )
    if hit is not None:
        return hit

    detail = "; ".join(missing_reasons) if missing_reasons else "; ".join(attempts)
    return (
        None,
        "Missing strict GPU roofline counters in host_data "
        "(need gpu_flops or gpu_flops_rate plus gpu_mem_bw_bytes_rate or "
        "PCIe/NVLink/Xe Link bytes on nvidia_gpu, amd_gpu, or intel_gpu). "
        f"Attempted: {detail}",
        None,
    )


def _gpu_roofline_title_for_axis(bw_axis: Optional[str]) -> str:
    """
    Return the Bokeh figure title for the selected GPU bandwidth axis.

    Args:
      bw_axis (Optional[str]): ``memory_bw``, ``pcie_nvlink``, or None.

    Returns:
      str: User-facing GPU roofline title.

    Examples:
      >>> _gpu_roofline_title_for_axis(GPU_ROOFLINE_BW_AXIS_MEMORY)
      'GPU Roofline (Memory BW)'
    """
    if bw_axis == GPU_ROOFLINE_BW_AXIS_MEMORY:
        return GPU_ROOFLINE_TITLE_MEMORY
    return GPU_ROOFLINE_TITLE_LINK


def plot_roofline_from_jid_table(
  jt: Any,
  peak_flops_gf: Any | None = None,
  peak_bw_gb: Any | None = None,
) -> Any:
    """
    Build CPU/host roofline plot from jid_table.
    
    Args:
      jt (Any): Jt passed to this helper.
      peak_flops_gf (Any | None): One of ``Any``, ``None``.
      peak_bw_gb (Any | None): One of ``Any``, ``None``.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> plot_roofline_from_jid_table(None, None, None)  # doctest: +SKIP
    """
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
        help_plot_key="jobDetailPlot_roofline_cpu",
    )


def plot_gpu_roofline_from_jid_table(
  jt: Any,
  peak_flops_gf: Any | None = None,
  peak_bw_gb: Any | None = None,
) -> Any:
    """
    Build GPU roofline from jid_table (memory BW preferred, else PCIe/NvLink).

    Args:
      jt (Any): Job ``jid_table`` with host list and aggregates.
      peak_flops_gf (Any | None): Explicit peak GFLOP/s, or inferred.
      peak_bw_gb (Any | None): Explicit peak GB/s, or inferred for *bw_axis*.

    Returns:
      Any: Bokeh figure, or None when samples are missing.

    Examples:
      >>> plot_gpu_roofline_from_jid_table(None)  # doctest: +SKIP
    """
    if not jt.host_list:
        return None
    df, _reason, bw_axis = _get_gpu_flops_bw_df_and_reason(jt)
    if df is None or df.empty:
        return None
    inf_f, inf_b = infer_gpu_roofline_peak_flops_and_bw_gbps(jt, bw_axis=bw_axis)
    use_peak_flops = peak_flops_gf if peak_flops_gf is not None else inf_f
    use_peak_bw = peak_bw_gb if peak_bw_gb is not None else inf_b
    return _build_roofline_figure(
        df,
        peak_flops_gf=use_peak_flops,
        peak_bw_gb=use_peak_bw,
        title=_gpu_roofline_title_for_axis(bw_axis),
        help_plot_key="jobDetailPlot_roofline_gpu",
    )


def plot_and_reason_roofline_from_jid_table(
  jt: Any,
  peak_flops_gf: Any | None = None,
  peak_bw_gb: Any | None = None,
) -> Any:
    """
    Build roofline plot and return (figure_or_none, unavailable_reason_or_none).
    
    Args:
      jt (Any): Jt passed to this helper.
      peak_flops_gf (Any | None): One of ``Any``, ``None``.
      peak_bw_gb (Any | None): One of ``Any``, ``None``.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> plot_and_reason_roofline_from_jid_table(None, None, None)
    """
    if not jt.host_list:
        return (None, "No hosts found in host_data for this job/time range")

    df, reason = _get_flops_bw_df_and_reason(jt)
    if df is None or df.empty:
        return (None, reason)

    inf_f, inf_b = infer_cpu_roofline_peak_flops_and_bw_gbps(jt)
    use_peak_flops = peak_flops_gf if peak_flops_gf is not None else inf_f
    use_peak_bw = peak_bw_gb if peak_bw_gb is not None else inf_b
    eff_flops = (
        use_peak_flops if use_peak_flops is not None else DEFAULT_PEAK_FLOPS_GF
    )
    eff_bw = use_peak_bw if use_peak_bw is not None else DEFAULT_PEAK_BW_GB
    if not _nominal_roofline_peaks_valid(eff_flops, eff_bw):
        return (None, ROOFLINE_NOMINAL_PEAKS_INVALID_REASON)
    fig = _build_roofline_figure(
        df,
        peak_flops_gf=use_peak_flops,
        peak_bw_gb=use_peak_bw,
        title="CPU Roofline (job)",
        help_plot_key="jobDetailPlot_roofline_cpu",
    )
    if fig is None:
        return (None, reason or "No valid roofline points after AI/perf filtering")
    return (fig, None)


def plot_and_reason_gpu_roofline_from_jid_table(
  jt: Any,
  peak_flops_gf: Any | None = None,
  peak_bw_gb: Any | None = None,
) -> Tuple[Any, Optional[str], Optional[str]]:
    """
    Build GPU roofline and return figure, reason, and bandwidth axis mode.

    Args:
      jt (Any): Job ``jid_table`` with host list and aggregates.
      peak_flops_gf (Any | None): Explicit peak GFLOP/s, or inferred.
      peak_bw_gb (Any | None): Explicit peak GB/s, or inferred for *bw_axis*.

    Returns:
      Tuple[Any, Optional[str], Optional[str]]: ``(figure_or_none,
      unavailable_reason_or_none, bw_axis_or_none)``.

    Examples:
      >>> plot_and_reason_gpu_roofline_from_jid_table(None)  # doctest: +SKIP
    """
    if not jt.host_list:
        return (None, "No hosts found in host_data for this job/time range", None)

    df, reason, bw_axis = _get_gpu_flops_bw_df_and_reason(jt)
    if df is None or df.empty:
        return (None, reason, None)

    inf_f, inf_b = infer_gpu_roofline_peak_flops_and_bw_gbps(jt, bw_axis=bw_axis)
    use_peak_flops = peak_flops_gf if peak_flops_gf is not None else inf_f
    use_peak_bw = peak_bw_gb if peak_bw_gb is not None else inf_b
    eff_flops = (
        use_peak_flops if use_peak_flops is not None else DEFAULT_PEAK_FLOPS_GF
    )
    eff_bw = use_peak_bw if use_peak_bw is not None else DEFAULT_PEAK_BW_GB
    if not _nominal_roofline_peaks_valid(eff_flops, eff_bw):
        return (None, ROOFLINE_NOMINAL_PEAKS_INVALID_REASON, bw_axis)
    fig = _build_roofline_figure(
        df,
        peak_flops_gf=use_peak_flops,
        peak_bw_gb=use_peak_bw,
        title=_gpu_roofline_title_for_axis(bw_axis),
        help_plot_key="jobDetailPlot_roofline_gpu",
    )
    if fig is None:
        return (
            None,
            reason or "No valid GPU roofline points after AI/perf filtering",
            bw_axis,
        )
    return (fig, None, bw_axis)
