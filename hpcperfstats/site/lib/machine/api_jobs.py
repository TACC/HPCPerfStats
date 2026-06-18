"""Jobs-focused API view exports.

This module is a focused import surface while `api.py` remains the canonical
implementation and re-export barrel used by routing/tests.
"""

from .api import job_detail, job_list, job_list_histograms, type_detail, host_plot

__all__ = [
    "job_detail",
    "job_list",
    "job_list_histograms",
    "type_detail",
    "host_plot",
]
