"""Unit tests for job_metric_display_labels (short labels for job detail table).

Catalog parity with ``job_metrics_catalog_entries()`` is asserted in
``site/lib/machine/tests/test_metrics.py`` (Django test module).
"""

from hpcperfstats.analysis.metrics.lib import job_metric_display_labels


def test_job_metric_short_labels_non_empty_strings():
  for key, label in job_metric_display_labels.JOB_METRIC_SHORT_LABELS.items():
    assert isinstance(key, str) and key
    assert isinstance(label, str) and label.strip()


def test_job_watt_hours_labels_omit_gpu_when_absent():
  assert job_metric_display_labels.job_has_gpu_for_watt_hours_label(4) is True
  assert job_metric_display_labels.job_has_gpu_for_watt_hours_label(0) is False
  assert job_metric_display_labels.job_has_gpu_for_watt_hours_label(None) is False
  assert (
      job_metric_display_labels.get_job_watt_hours_short_label(False)
      == "CPU watt-hours for job"
  )
  assert (
      job_metric_display_labels.get_job_watt_hours_resources_title(False)
      == "CPU Watt Hours for Job"
  )
  assert (
      job_metric_display_labels.get_job_watt_hours_short_label(True)
      == "CPU+GPU watt-hours for job"
  )
  assert (
      job_metric_display_labels.get_job_watt_hours_resources_title(True)
      == "CPU+GPU Watt Hours for Job"
  )
