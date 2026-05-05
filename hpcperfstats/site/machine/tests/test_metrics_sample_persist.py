"""Tests for metrics_distinct_time_count persistence and metrics_data upsert."""

from contextlib import nullcontext
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

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


@pytest.mark.django_db(databases=[])
def test_persist_metrics_batch_sets_metrics_distinct_time_count(sample_metric_row):
  """bulk_update job_data with metrics_distinct_time_count after metrics write."""
  job_row = MagicMock()
  job_row.jid = "j1"

  with patch.object(
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
  _rows, kwargs = mock_bulk_create.call_args
  assert kwargs["update_conflicts"] is True
  assert kwargs["unique_fields"] == ["jid", "type", "metric"]
  assert kwargs["update_fields"] == ["units", "value", "no_data_reason"]
  mock_bulk_update.assert_not_called()


@pytest.mark.django_db(databases=[])
def test_persist_metrics_batch_skips_job_update_when_distinct_none(sample_metric_row):
  """distinct_time_count=None does not call job_data.bulk_update for sample count."""
  with patch.object(
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


@pytest.mark.django_db(databases=[])
def test_persist_metrics_batch_coerces_list_metric_fields(sample_metric_row):
  """List-like type/metric/units should be coerced to stable strings."""
  row = dict(sample_metric_row)
  row["type"] = ["cpu", "agg"]
  row["metric"] = ["avg_cpuusage", "peak_cpuusage"]
  row["units"] = ["#cores", "max"]
  with patch.object(
      metrics_module.metrics_data.objects,
      "bulk_create",
  ) as mock_bulk_create, patch.object(
      metrics_module.job_data.objects,
      "filter",
      return_value=[],
  ), patch.object(
      metrics_module.transaction,
      "atomic",
      MagicMock(return_value=nullcontext()),
  ):
    _persist_metrics_batch([row], None)

  rows = mock_bulk_create.call_args.args[0]
  assert len(rows) == 1
  assert rows[0].type == "cpu,agg"
  assert rows[0].metric == "avg_cpuusage,peak_cpuusage"
  assert rows[0].units == "#cores,max"


@pytest.mark.django_db
def test_persist_metrics_batch_upserts_same_key():
  """Second persist with same (jid, type, metric) updates one row (ON CONFLICT)."""
  from hpcperfstats.site.machine.models import job_data, metrics_data

  now = timezone.now()
  j = job_data.objects.create(
      jid="upsert_metrics_row",
      submit_time=now,
      start_time=now,
      end_time=now,
      username="u1",
      host_list=["h1"],
  )
  row = {
      "jid": j,
      "type": "cpu",
      "metric": "avg_cpuusage",
      "units": "#cores",
      "value": 1.0,
      "no_data_reason": None,
  }
  _persist_metrics_batch([row], None)
  assert metrics_data.objects.filter(jid_id=j.jid).count() == 1
  row["value"] = 2.0
  _persist_metrics_batch([row], None)
  assert metrics_data.objects.filter(jid_id=j.jid).count() == 1
  assert metrics_data.objects.get(jid_id=j.jid, metric="avg_cpuusage").value == 2.0


@pytest.mark.django_db
def test_persist_metrics_batch_dedupes_duplicate_keys_in_one_batch():
  """Last row wins for duplicate (jid, type, metric) in a single batch."""
  from hpcperfstats.site.machine.models import job_data, metrics_data

  now = timezone.now()
  j = job_data.objects.create(
      jid="dedupe_metrics_batch",
      submit_time=now,
      start_time=now,
      end_time=now,
      username="u1",
      host_list=["h1"],
  )
  _persist_metrics_batch(
      [
          {
              "jid": j,
              "type": "cpu",
              "metric": "avg_cpuusage",
              "units": "#cores",
              "value": 1.0,
              "no_data_reason": None,
          },
          {
              "jid": j,
              "type": "cpu",
              "metric": "avg_cpuusage",
              "units": "#cores",
              "value": 3.0,
              "no_data_reason": None,
          },
      ],
      None,
  )
  assert metrics_data.objects.filter(jid_id=j.jid, metric="avg_cpuusage").count() == 1
  assert metrics_data.objects.get(jid_id=j.jid, metric="avg_cpuusage").value == 3.0
