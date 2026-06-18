"""Unit tests for job_metric_display_labels (short labels for job detail table).

Catalog parity with ``job_metrics_catalog_entries()`` is asserted in
``site/lib/machine/tests/test_metrics.py`` (Django test module).
"""

from hpcperfstats.analysis.metrics.lib import job_metric_display_labels


def test_job_metric_short_labels_non_empty_strings():
  for key, label in job_metric_display_labels.JOB_METRIC_SHORT_LABELS.items():
    assert isinstance(key, str) and key
    assert isinstance(label, str) and label.strip()
