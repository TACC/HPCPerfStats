"""
Roofline plot: arithmetic intensity vs performance (GFLOP/s) from the columnar
job table's FLOPS and memory-bandwidth samples.

Uses the same PMC sources as SummaryPlot (AMD or Intel). Draws the roofline
curve and scatter of (AI, perf) points. The sample selection lives in
``columnar.roofline_series`` (probe orders and unavailable reasons are
unchanged); this module owns the Bokeh figure.

AMD path requires amd64_df MBW channels (monitor enables these on AMD family
17h/19h only). Intel memory side tries IMC types in INTEL_IMC_STATS_TYPES (SNB
through SKX). Intel FLOPS use FP_ARITH when present, else SNB/IVB-style SSE/AVX
double counter proxies.

Attributes:
  DEFAULT_PEAK_FLOPS_GF: Fallback peak GFLOP/s.
  DEFAULT_PEAK_BW_GB: Fallback peak GB/s.
  GPU_ROOFLINE_BW_AXIS_MEMORY: GPU bandwidth axis tag (HBM).
  GPU_ROOFLINE_BW_AXIS_LINK: GPU bandwidth axis tag (PCIe / NVLink).
  GPU_ROOFLINE_TITLE_MEMORY: GPU figure title for the HBM axis.
  GPU_ROOFLINE_TITLE_LINK: GPU figure title for the link axis.
  _GPU_BYTES_TO_GIB: GiB/s conversion.
  _NVIDIA_GPU_LINK_DIRECTIONAL_EVENTS: NVIDIA directional link byte arcs.
  _INTEL_GPU_LINK_EVENTS: Intel PCIe + Xe Link byte arcs.
  ROOFLINE_NOMINAL_PEAKS_INVALID_REASON: Unavailable reason for bad peaks.
"""

from __future__ import annotations

import math
from typing import Any

import numpy
from bokeh.models import ColumnDataSource, HoverTool
from bokeh.plotting import figure

from hpcperfstats.analysis.metrics.lib.bokeh_job_embed import figure_embed_kw
from hpcperfstats.analysis.metrics.lib.columnar.roofline_series import (
  RooflinePoints,
  cpu_roofline_points,
  gpu_roofline_points,
)
from hpcperfstats.analysis.metrics.lib.gen.utils import format_plain_decimal
from hpcperfstats.analysis.metrics.lib.phase_profile import (
  phase as _profile_phase,
)
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


ROOFLINE_NOMINAL_PEAKS_INVALID_REASON = (
  "Roofline nominal peaks (GFLOP/s or GB/s) are non-finite or non-positive; "
  "cannot render log-scaled roofline."
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
  except TypeError, ValueError:
    return False
  return bool(
    numpy.isfinite(pf) and numpy.isfinite(pb) and pf > 0.0 and pb > 0.0
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
      <div><strong>device:</strong> @dev_display</div>
      <div><strong>AI (FLOP/byte):</strong> @ai_plain</div>
      <div><strong>Perf (GFLOP/s):</strong> @perf_plain</div>
      <div><strong>time:</strong> @time</div>
    </div>
  """


def _gpu_roofline_title_for_axis(bw_axis: str | None) -> str:
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


def _get_flops_bw_df_and_reason(jt: Any) -> Any:
  """
  CPU roofline samples: ``(points, None)`` or ``(None, reason)``.

  Args:
    jt (Any): ``ColumnarJobTable`` of the job.

  Returns:
    Any: ``(RooflinePoints | None, str | None)``.

  Examples:
    >>> _get_flops_bw_df_and_reason(jt)  # doctest: +SKIP
  """
  return cpu_roofline_points(jt)


def _get_gpu_flops_bw_df_and_reason(
  jt: Any,
) -> tuple[Any, str | None, str | None]:
  """
  GPU roofline samples: ``(points, reason, bw_axis)``.

  Args:
    jt (Any): ``ColumnarJobTable`` of the job.

  Returns:
    Tuple[Any, Optional[str], Optional[str]]: Points, unavailable reason,
    bandwidth axis (``memory_bw`` / ``pcie_nvlink``).

  Examples:
    >>> _get_gpu_flops_bw_df_and_reason(jt)  # doctest: +SKIP
  """
  return gpu_roofline_points(jt)


def _build_roofline_figure(
  points: RooflinePoints | None,
  peak_flops_gf: Any,
  peak_bw_gb: Any,
  title: Any,
  help_plot_key: str = "jobDetailPlot_roofline_cpu",
  *,
  is_shared_node: bool = False,
) -> Any:
  """
  Render a roofline figure from ``(host[, dev], time, flops_gf, bw_gb)`` points.

  Args:
    points (RooflinePoints | None): Samples.
    peak_flops_gf (Any): Peak GFLOP/s (None -> default).
    peak_bw_gb (Any): Peak GB/s (None -> default).
    title (Any): Figure title.
    help_plot_key (str): Help registry key.
    is_shared_node (bool): When True, append shared-node help footer.

  Returns:
    Any: Bokeh figure, or None without usable points.

  Examples:
    >>> _build_roofline_figure(None, None, None, "x")
  """
  with _profile_phase("bokeh"):
    return _build_roofline_figure_impl(
      points,
      peak_flops_gf,
      peak_bw_gb,
      title,
      help_plot_key,
      is_shared_node=is_shared_node,
    )


def _build_roofline_figure_impl(
  points: RooflinePoints | None,
  peak_flops_gf: Any,
  peak_bw_gb: Any,
  title: Any,
  help_plot_key: str = "jobDetailPlot_roofline_cpu",
  *,
  is_shared_node: bool = False,
) -> Any:
  """
  Figure construction behind :func:`_build_roofline_figure`.

  Args:
    points (RooflinePoints | None): Samples.
    peak_flops_gf (Any): Peak GFLOP/s (None -> default).
    peak_bw_gb (Any): Peak GB/s (None -> default).
    title (Any): Figure title.
    help_plot_key (str): Help registry key.
    is_shared_node (bool): When True, append shared-node help footer.

  Returns:
    Any: Bokeh figure, or None without usable points.

  Examples:
    >>> _build_roofline_figure_impl(None, None, None, "x")
  """
  peak_flops_gf = (
    peak_flops_gf if peak_flops_gf is not None else DEFAULT_PEAK_FLOPS_GF
  )
  peak_bw_gb = peak_bw_gb if peak_bw_gb is not None else DEFAULT_PEAK_BW_GB
  if not _nominal_roofline_peaks_valid(peak_flops_gf, peak_bw_gb):
    return None
  if points is None or points.empty:
    return None

  # Arithmetic intensity: FLOP/byte = (GFLOP/s) / (GB/s) = same ratio
  with _profile_phase("plot_series"):
    bw = numpy.where(points.bw_gb == 0, numpy.nan, points.bw_gb)
    flops = numpy.asarray(points.flops_gf, dtype=numpy.float64)
    with numpy.errstate(divide="ignore", invalid="ignore"):
      ai_all = flops / bw
    keep = (
      ~numpy.isnan(ai_all) & ~numpy.isnan(flops) & (ai_all > 0) & (flops > 0)
    )
    if not keep.any():
      return None
    ai = ai_all[keep]
    perf = flops[keep]
    host = [h for h, k in zip(points.host_names(), keep, strict=True) if k]
    time_vals = [
      str(t)
      for t, k in zip(_times_utc_text(points.time_us), keep, strict=True)
      if k
    ]
    dev_vals = [d for d, k in zip(points.dev_names(), keep, strict=True) if k]
    dev_display = [d if d else "-" for d in dev_vals]

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
    {
      "ai": ai,
      "perf": perf,
      "host": host,
      "time": time_vals,
      "dev": dev_vals,
      "dev_display": dev_display,
      "ai_plain": [format_plain_decimal(v) for v in ai],
      "perf_plain": [format_plain_decimal(v) for v in perf],
    },
  )
  roof_source = ColumnDataSource({"ai": ai_curve, "perf": perf_curve})

  peak_flops_plain = format_plain_decimal(peak_flops_gf)
  peak_bw_plain = format_plain_decimal(peak_bw_gb)
  # No legend; identify series by hovering (popup shows line name + peaks + units).
  hover_roof = HoverTool(
    tooltips=[
      ("Line", "Roofline"),
      ("Peak performance", f"{peak_flops_plain} GFLOP/s"),
      ("Peak bandwidth", f"{peak_bw_plain} GB/s"),
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
      y_range=(
        min(perf.min(), peak_bw_gb * plot_ai_min) * 0.5,
        peak_flops_gf * 1.2,
      ),
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
    is_shared_node=is_shared_node,
  )
  return p


def _times_utc_text(time_us: Any) -> list[str]:
  """
  ``str(pandas.Timestamp)`` of UTC sample times (``YYYY-MM-DD HH:MM:SS[.ffffff]+00:00``).

  Args:
    time_us (Any): int64 epoch microseconds.

  Returns:
    list[str]: Text per sample, as the hover shows it.

  Examples:
    >>> _times_utc_text(numpy.array([0]))
    ['1970-01-01 00:00:00+00:00']
  """
  from datetime import UTC, datetime

  out = []
  for t in numpy.asarray(time_us, dtype=numpy.int64).tolist():
    dt = datetime.fromtimestamp(t / 1_000_000.0, tz=UTC)
    text = dt.strftime("%Y-%m-%d %H:%M:%S")
    if dt.microsecond:
      text += f".{dt.microsecond:06d}"
    out.append(text + "+00:00")
  return out


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
    is_shared_node=False,  # CPU/GPU PMC surfaces are job-scoped
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
    is_shared_node=False,  # CPU/GPU PMC surfaces are job-scoped
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
    is_shared_node=False,  # CPU/GPU PMC surfaces are job-scoped
  )
  if fig is None:
    return (
      None,
      reason or "No valid roofline points after AI/perf filtering",
    )
  return (fig, None)


def plot_and_reason_gpu_roofline_from_jid_table(
  jt: Any,
  peak_flops_gf: Any | None = None,
  peak_bw_gb: Any | None = None,
) -> tuple[Any, str | None, str | None]:
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
    return (
      None,
      "No hosts found in host_data for this job/time range",
      None,
    )

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
    is_shared_node=False,  # CPU/GPU PMC surfaces are job-scoped
  )
  if fig is None:
    return (
      None,
      reason or "No valid GPU roofline points after AI/perf filtering",
      bw_axis,
    )
  return (fig, None, bw_axis)
