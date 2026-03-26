"""Tests for metrics_distinct_time_count persistence (no live DB required)."""

from contextlib import nullcontext
from unittest.mock import MagicMock, patch

import pytest

from hpcperfstats.analysis.metrics import metrics as metrics_module
from hpcperfstats.analysis.metrics.metrics import _persist_metrics_batch


@pytest.fixture
def sample_metric_row():
  job = MagicMock()
  job.jid = "j1"
  return {
      "jid": job,
      "type": "cpu",
      "metric": "avg_cpuusage",
      "units": "#cores",
      "value": 1.0,
      "no_data_reason": None,
  }


def test_persist_metrics_batch_sets_metrics_distinct_time_count(sample_metric_row):
  """bulk_update job_data with metrics_distinct_time_count after metrics write."""
  job_row = MagicMock()
  job_row.jid = "j1"

  with patch.object(
      metrics_module.metrics_data.objects,
      "filter",
      return_value=MagicMock(
          only=MagicMock(return_value=[]),
      ),
  ), patch.object(
      metrics_module.metrics_data.objects,
      "bulk_create",
  ) as mock_bulk_create, patch.object(
      metrics_module.metrics_data.objects,
      "bulk_update",
  ) as mock_bulk_update, patch.object(
      metrics_module.job_data.objects,
      "filter",
      return_value=[job_row],
  ) as mock_jd_filter, patch.object(
      metrics_module.job_data.objects,
      "bulk_update",
  ) as mock_jd_bulk_update, patch.object(
      metrics_module.transaction,
      "atomic",
      MagicMock(return_value=nullcontext()),
  ):
    _persist_metrics_batch([sample_metric_row], 42)

  mock_jd_filter.assert_called()
  mock_jd_bulk_update.assert_called_once()
  bulk_args, bulk_kw = mock_jd_bulk_update.call_args
  assert bulk_args[1] == ["metrics_distinct_time_count"]
  assert job_row.metrics_distinct_time_count == 42
  mock_bulk_create.assert_called_once()
  mock_bulk_update.assert_not_called()


def test_persist_metrics_batch_skips_job_update_when_distinct_none(sample_metric_row):
  """distinct_time_count=None does not call job_data.bulk_update for sample count."""
  with patch.object(
      metrics_module.metrics_data.objects,
      "filter",
      return_value=MagicMock(
          only=MagicMock(return_value=[]),
      ),
  ), patch.object(
      metrics_module.metrics_data.objects,
      "bulk_create",
  ), patch.object(
      metrics_module.metrics_data.objects,
      "bulk_update",
  ), patch.object(
      metrics_module.job_data.objects,
      "filter",
  ) as mock_jd_filter, patch.object(
      metrics_module.job_data.objects,
      "bulk_update",
  ) as mock_jd_bulk_update, patch.object(
      metrics_module.transaction,
      "atomic",
      MagicMock(return_value=nullcontext()),
  ):
    _persist_metrics_batch([sample_metric_row], None)

  mock_jd_filter.assert_not_called()
  mock_jd_bulk_update.assert_not_called()
