"""
Plot package: SummaryPlot, DevPlot for job/host metrics visualization (Bokeh).

Unavailable reasons are shown in the same placeholder UI (BokehEmbed) across all
plots.

Attributes:
  MSG_NO_HOST_MSR_DATA: Attribute.
  MSG_NO_METRIC_DATA: Attribute.
  MSG_NO_ROOFLINE_DATA: Attribute.
"""
from __future__ import annotations

# Shared messages for "plot not available" placeholder (displayed consistently in BokehEmbed)
MSG_NO_METRIC_DATA = "No metric data available for this job."
MSG_NO_HOST_MSR_DATA = "No host-level MSR data available"
MSG_NO_ROOFLINE_DATA = "No FLOPS/memory bandwidth data available for roofline."

# Import plots to run on data
from hpcperfstats.analysis.metrics.lib.plot.devplot import DevPlot
from hpcperfstats.analysis.metrics.lib.plot.roofline import (
    plot_and_reason_gpu_roofline_from_jid_table,
    plot_and_reason_roofline_from_jid_table,
    plot_gpu_roofline_from_jid_table,
    plot_roofline_from_jid_table,
)
from hpcperfstats.analysis.metrics.lib.plot.roofline_peaks import (
    infer_cpu_roofline_peak_flops_and_bw_gbps,
    infer_gpu_roofline_peak_flops_and_bw_gbps,
    lookup_roofline_cpu_peaks,
)
from hpcperfstats.analysis.metrics.lib.plot.summaryplot import SummaryPlot
from hpcperfstats.analysis.metrics.lib.plot.summaryplot import plot_and_reason_summary_from_jid_table

__all__ = [
    "SummaryPlot",
    "plot_and_reason_summary_from_jid_table",
    "DevPlot",
    "plot_roofline_from_jid_table",
    "plot_and_reason_roofline_from_jid_table",
    "plot_gpu_roofline_from_jid_table",
    "plot_and_reason_gpu_roofline_from_jid_table",
    "infer_cpu_roofline_peak_flops_and_bw_gbps",
    "infer_gpu_roofline_peak_flops_and_bw_gbps",
    "lookup_roofline_cpu_peaks",
    "MSG_NO_METRIC_DATA",
    "MSG_NO_HOST_MSR_DATA",
    "MSG_NO_ROOFLINE_DATA",
]
