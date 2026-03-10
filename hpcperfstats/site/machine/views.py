"""Shared helpers for machine app: job_hist (Bokeh histograms), local_timezone, XALT containers (libset_c, xalt_data_c). Used by the REST API only; React SPA is the only UI."""

import os

from django.conf import settings
import hpcperfstats.conf_parser as cfg

openblas_threads = getattr(settings, "OPENBLAS_NUM_THREADS", 4)
os.environ["OPENBLAS_NUM_THREADS"] = str(openblas_threads)

import numpy as np
from bokeh.models import HoverTool
from bokeh.plotting import figure
from numpy import histogram, isfinite, log
from pandas import to_numeric

local_timezone = cfg.get_local_timezone()


class libset_c:
    """Simple container for (object_path, module_name) used in XALT libset."""

    def __init__(self, object_path, module_name):
        self.module_name = module_name
        self.object_path = object_path


class xalt_data_c:
    """Container for XALT data: exec_path list, cwd list, libset list."""

    def __init__(self):
        self.exec_path = []
        self.cwd = []
        self.libset = []


def job_hist(df, metric, label, width=600, height=400, title=None):
    """Build a Bokeh quad histogram for the given metric column and axis label.

    Optional width/height allow thumbnail (e.g. 280x200) vs full (600x400) sizes.
    Optional title overrides the figure title (defaults to metric column name).
    Uses only finite values; handles empty, constant, and all-zero data safely.
    """
    if metric not in df.columns:
        return None
    hover = HoverTool(
        tooltips=[("jobs", "@top"), ("bin", "[@left, @right]")],
        point_policy="snap_to_data",
    )
    TOOLS = ["pan,wheel_zoom,box_zoom,reset,save,box_select", hover]

    raw = to_numeric(df[metric], errors="coerce")
    values = np.asarray(raw, dtype=np.float64)
    values = values[isfinite(values)]
    if len(values) == 0:
        return None

    min_val = float(np.min(values))
    max_val = float(np.max(values))
    num_bins = max(3, int(5 * log(len(values))))

    if max_val <= min_val:
        low = min_val - 0.5 if min_val != 0 else 0
        high = min_val + 0.5 if min_val != 0 else 1.0
        bins = np.linspace(low, high, num_bins + 1)
    else:
        bins = np.linspace(min_val, max_val, num_bins + 1)

    hist, edges = histogram(values, bins=bins)

    y_min = 1
    y_max = float(np.max(hist)) if len(hist) > 0 else 1
    if y_max < y_min:
        y_max = y_min

    plot = figure(
        title=title if title is not None else metric,
        toolbar_location=None,
        height=height,
        width=width,
        y_range=(y_min, y_max),
        tools=TOOLS,
    )
    plot.xaxis.axis_label = label
    plot.yaxis.axis_label = "# jobs"
    plot.quad(top=hist, bottom=1, left=edges[:-1], right=edges[1:])

    return plot
