"""User-facing help text for Job Detail Bokeh figures (roofline, multiprecision, overlays).

Keep ``JOB_DETAIL_BOKEH_HELP`` aligned with ``JOB_DETAIL_BOKEH_PLOT_METADATA`` in
``hpcperfstats/site/frontend/src/utils/variableMetadata.js`` (same keys, same prose).
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

HelpPair = Tuple[str, Optional[str]]

JOB_DETAIL_BOKEH_HELP: Dict[str, HelpPair] = {
    "jobDetailPlot_roofline_cpu": (
        "CPU roofline: each point is one host and time sample. Horizontal axis is "
        "arithmetic intensity (GFLOP/s divided by GB/s memory bandwidth from the same "
        "sample). Vertical axis is achieved GFLOP/s. The navy curve is the theoretical "
        "roofline for this job using inferred CPU peak FLOPS and memory bandwidth.",
        "Points below the roof indicate memory-bound or imbalance behavior; points near "
        "the ridge show compute-bound phases.",
    ),
    "jobDetailPlot_roofline_gpu": (
        "GPU roofline: each point is one host and time sample from NVIDIA DCGM-style "
        "telemetry. Horizontal axis is arithmetic intensity (GPU GFLOP/s divided by "
        "PCIe plus NVLink byte rate in GB/s). Vertical axis is GPU GFLOP/s. The navy "
        "curve is the theoretical roof for this job using inferred GPU peaks.",
        "Use this to see whether the job is communication-limited versus GPU compute "
        "relative to the link roof.",
    ),
    "jobDetailPlot_multiprecision_cpu": (
        "CPU multiprecision mix: wedge areas show each width's share of busy arithmetic "
        "rates only (idle excluded), from avg_flops64b / avg_flops32b (Intel FP_ARITH or "
        "host_cpu_hw scalar FP) and avg_arm_int16_ops / avg_arm_int8_ops when present. "
        "INT ops and FLOPS are mixed as relative busy-ops shares, not identical units.",
        "Compare FP64 versus FP32 (and INT16/INT8) busy-ops mix when tuning "
        "numerical precision; vectorization width metrics remain separate.",
    ),
    "jobDetailPlot_multiprecision_gpu": (
        "GPU multiprecision mix: wedge areas show each active pipe's share of busy GPU "
        "activity only (idle excluded). Hover shows share of busy percent. Tensor "
        "IMMA (INT8/INT4), Tensor HMMA (FP16/BF16), and Tensor DFMA (FP64) splits are "
        "preferred over lumped tensor_active when present.",
        "Use it to spot FP16-heavy kernels versus FP32/64 or tensor-pipe-dominated "
        "phases.",
    ),
    "summary_hardware_error_rates": (
        "Hardware error rates: each line is one counter channel summed across hosts at "
        "each sample time, using monitor counter deltas divided by elapsed time (events "
        "per second). InfiniBand, Omni-Path, and Ethernet error-style counters are "
        "included only when present in this job's schema.",
        "Spikes can flag link quality, congestion, or driver issues worth correlating "
        "with application slowdowns.",
    ),
    "jobDetailPlot_type_detail_rates": (
        "On-device rates: each subplot is one monitor event for this type, with one "
        "line per host over the job window. Values are device-aggregated rates "
        "(arc) or sampled values depending on the type.",
        "Use these to compare hosts and spot device-level imbalance or stalls for "
        "the selected type.",
    ),
    "typeDetailHeading_rates": (
        "Rates Aggregated over devices: time-series plots of per-host device rates "
        "for every event in this monitor type.",
        "Scan for hosts or events that diverge from the job majority.",
    ),
    "typeDetailHeading_counts": (
        "Counts Aggregated over devices and hosts: table of summed counter samples "
        "across devices and hosts at each time for this type.",
        "Use totals when comparing volume across events rather than rates.",
    ),
}


def description_for_job_detail_bokeh_plot(plot_key: str) -> str:
  """Return primary help text for ``plot_key`` or a short fallback."""
  pair = JOB_DETAIL_BOKEH_HELP.get(plot_key)
  if pair is None:
    return ""
  return pair[0]


def researcher_use_for_job_detail_bokeh_plot(plot_key: str) -> Optional[str]:
  """Return optional researcher-use line for ``plot_key``."""
  pair = JOB_DETAIL_BOKEH_HELP.get(plot_key)
  if pair is None:
    return None
  return pair[1]
