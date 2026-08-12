"""Tests for summary subplot metric documentation strings."""
from hpcperfstats.analysis.metrics.lib.plot.summary_metric_descriptions import (
    SUMMARY_METRIC_DESCRIPTIONS,
    SUMMARY_METRIC_RESEARCHER_USE,
    description_for_summary_metric,
    researcher_use_for_summary_metric,
)


def test_description_for_summary_metric_known_cpu():
  text = description_for_summary_metric("cpu")
  assert "CPU cores" in text
  assert text == SUMMARY_METRIC_DESCRIPTIONS["cpu"]


def test_description_for_summary_metric_unknown_fallback():
  text = description_for_summary_metric("totally_unknown_summary_xyz")
  assert "totally_unknown_summary_xyz" in text
  assert "HPCPerfStats" in text


def test_researcher_use_for_summary_metric_cpu():
  use = researcher_use_for_summary_metric("cpu")
  assert use is not None
  assert "CPU" in use or "busy" in use


def test_researcher_use_for_summary_metric_unknown_is_none():
  assert researcher_use_for_summary_metric("totally_unknown_summary_xyz") is None


def test_description_for_summary_metric_hardware_error_rates():
  text = description_for_summary_metric("summary_hardware_error_rates")
  assert "Hardware error rates" in text
  assert "each subplot" in text
  assert "one host" in text
  assert "summed across hosts" not in text
  assert text == SUMMARY_METRIC_DESCRIPTIONS["summary_hardware_error_rates"]


def test_researcher_use_keys_are_subset_of_description_keys():
  extra = set(SUMMARY_METRIC_RESEARCHER_USE) - set(SUMMARY_METRIC_DESCRIPTIONS)
  assert not extra, f"Researcher-use keys missing from descriptions: {extra}"
