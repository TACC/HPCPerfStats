"""
Jobs-focused API view exports.

This module is a focused import surface while `api.py` remains the canonical
implementation and re-export barrel used by routing/tests.
"""

from __future__ import annotations

from .api import (
  host_plot,
  job_detail,
  job_list,
  job_list_histograms,
  type_detail,
)

__all__ = [
  "host_plot",
  "job_detail",
  "job_list",
  "job_list_histograms",
  "type_detail",
]
