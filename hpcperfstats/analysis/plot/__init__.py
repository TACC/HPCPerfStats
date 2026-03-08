"""Plot package: SummaryPlot, DevPlot, HeatMap for job/host metrics visualization (Bokeh).

Unavailable reasons are shown in the same placeholder UI (BokehEmbed) across all plots.
"""
# Shared messages for "plot not available" placeholder (displayed consistently in BokehEmbed)
MSG_NO_METRIC_DATA = "No metric data available for this job."
MSG_NO_HOST_MSR_DATA = "No host-level MSR data available"
MSG_NO_ROOFLINE_DATA = "No FLOPS/memory bandwidth data available for roofline."

# Import plots to run on data
from hpcperfstats.analysis.plot.devplot import DevPlot
from hpcperfstats.analysis.plot.heatmap import HeatMap, plot_from_jid_table
from hpcperfstats.analysis.plot.roofline import plot_roofline_from_jid_table
from hpcperfstats.analysis.plot.summaryplot import SummaryPlot

__all__ = [
    "SummaryPlot",
    "HeatMap",
    "DevPlot",
    "plot_from_jid_table",
    "plot_roofline_from_jid_table",
    "MSG_NO_METRIC_DATA",
    "MSG_NO_HOST_MSR_DATA",
    "MSG_NO_ROOFLINE_DATA",
]
