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
        "CPU multiprecision mix: wedge areas show the fraction of CPU floating-point "
        "work attributed to each precision lane (from job-level metrics), as a share of "
        "the total that is classified.",
        "Compare FP64 versus vectorized FP32/16 activity when tuning vectorization and "
        "numerical stability.",
    ),
    "jobDetailPlot_multiprecision_gpu": (
        "GPU multiprecision mix: wedge areas show the fraction of GPU floating-point "
        "activity by precision (from host telemetry across the job window), as a share "
        "of classified GPU FLOPS.",
        "Use it to spot FP16-heavy kernels versus FP32/64-dominated phases.",
    ),
    "summary_hardware_error_rates": (
        "Hardware error rates: each line is one counter channel summed across hosts at "
        "each sample time, using monitor counter deltas divided by elapsed time (events "
        "per second). InfiniBand, Omni-Path, and Ethernet error-style counters are "
        "included only when present in this job's schema.",
        "Spikes can flag link quality, congestion, or driver issues worth correlating "
        "with application slowdowns.",
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
