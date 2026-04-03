"""Tests for summary subplot metric documentation strings."""
from hpcperfstats.analysis.plot.summary_metric_descriptions import (
    SUMMARY_METRIC_DESCRIPTIONS,
    description_for_summary_metric,
)


def test_description_for_summary_metric_known_cpu():
  text = description_for_summary_metric("cpu")
  assert "CPU cores" in text
  assert text == SUMMARY_METRIC_DESCRIPTIONS["cpu"]


def test_description_for_summary_metric_unknown_fallback():
  text = description_for_summary_metric("totally_unknown_summary_xyz")
  assert "totally_unknown_summary_xyz" in text
  assert "HPCPerfStats" in text
