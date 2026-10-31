"""Provenance stamps written by the metrics persist (backfill lane, Stage 1)."""

from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

from hpcperfstats.analysis.metrics.lib import metrics as metrics_module


def _row(jid: str = "j1") -> dict:
  return {
    "jid": jid,
    "type": "cpu",
    "metric": "avg_cpuusage",
    "units": "#cores",
    "value": 1.0,
    "no_data_reason": None,
  }


@pytest.mark.django_db(databases=[])
def test_persist_stamps_metrics_version_and_coverage():
  """Persist writes metrics_version, metrics_coverage and clears the skip
  clock in the same bulk_update as the sample count."""
  job_row = SimpleNamespace(jid="j1")
  with (
    patch.object(metrics_module.metrics_data.objects, "bulk_create"),
    patch.object(
      metrics_module.job_data.objects, "filter", return_value=[job_row]
    ),
    patch.object(metrics_module.job_data.objects, "bulk_update") as bulk_up,
    patch.object(
      metrics_module.transaction,
      "atomic",
      MagicMock(return_value=nullcontext()),
    ),
  ):
    metrics_module._persist_metrics_batch([_row()], 7, metrics_coverage=0.5)
  fields = bulk_up.call_args.args[1]
  assert "metrics_version" in fields
  assert "metrics_skipped_since" in fields
  assert "metrics_coverage" in fields
  assert job_row.metrics_version == metrics_module.METRICS_CONTRACT_VERSION
  assert job_row.metrics_skipped_since is None
  assert job_row.metrics_coverage == 0.5


@pytest.mark.django_db(databases=[])
def test_persist_without_coverage_leaves_stored_coverage_alone():
  job_row = SimpleNamespace(jid="j1", metrics_coverage=0.25)
  with (
    patch.object(metrics_module.metrics_data.objects, "bulk_create"),
    patch.object(
      metrics_module.job_data.objects, "filter", return_value=[job_row]
    ),
    patch.object(metrics_module.job_data.objects, "bulk_update") as bulk_up,
    patch.object(
      metrics_module.transaction,
      "atomic",
      MagicMock(return_value=nullcontext()),
    ),
  ):
    metrics_module._persist_metrics_batch([_row()], 7)
  assert "metrics_coverage" not in bulk_up.call_args.args[1]
  assert job_row.metrics_coverage == 0.25
  assert job_row.metrics_version == metrics_module.METRICS_CONTRACT_VERSION


def test_provenance_stamp_fields_clamp_coverage():
  fields = metrics_module._provenance_stamp_fields(1.7)
  assert fields["metrics_coverage"] == 1.0
  assert fields["metrics_version"] == metrics_module.METRICS_CONTRACT_VERSION
  assert fields["metrics_skipped_since"] is None
  assert "metrics_coverage" not in metrics_module._provenance_stamp_fields(None)


@pytest.mark.django_db(databases=[])
def test_unwrap_carries_candidate_coverage_into_the_payload():
  """Readiness puts hosts-covering/hosts-total on the ref; the worker payload
  forwards it so persist can store it."""

  class _M:
    def compute_metrics(self, job):
      del job
      return {"rows": [_row()], "distinct_time_count": 3}

  job = SimpleNamespace(jid="j1", metrics_coverage=0.75)
  with patch.object(
    metrics_module,
    "_pg_session_statement_timeout_for_metrics_worker",
    nullcontext,
  ):
    payload = metrics_module._unwrap((_M(), job))
    plain = metrics_module._unwrap((_M(), SimpleNamespace(jid="j2")))
  assert payload["status"] == "ok"
  assert payload["metrics_coverage"] == 0.75
  assert plain["metrics_coverage"] is None


@pytest.mark.django_db(databases=[])
def test_persist_metrics_payload_forwards_coverage():
  seen = {}

  def _fake_persist(*args, **kwargs):
    seen.update(kwargs)

  with patch.object(metrics_module, "_persist_metrics_batch", _fake_persist):
    outcome = metrics_module._persist_metrics_payload(
      {
        "jid": "j1",
        "status": "ok",
        "rows": [_row()],
        "distinct_time_count": 3,
        "metrics_coverage": 0.5,
      }
    )
  assert outcome["ok"] is True
  assert seen["metrics_coverage"] == 0.5


@pytest.mark.django_db(databases=[])
def test_record_backfill_skips_sets_clock_coverage_and_consumes_dirty():
  qs = MagicMock()
  qs.update.return_value = 2
  checked_at = timezone.now()
  with patch.object(
    metrics_module.job_data.objects, "filter", return_value=qs
  ) as flt:
    stamped = metrics_module.record_backfill_skips(
      [("a", 0.5), ("b", 0.5), ("c", None)], checked_at
    )
  assert stamped == 4  # two groups x update return 2
  update_kwargs = [c.kwargs for c in qs.update.call_args_list]
  grouped = [k for k in update_kwargs if "metrics_skipped_since" in k]
  assert len(grouped) == 2
  assert any(k.get("metrics_coverage") == 0.5 for k in grouped)
  assert any("metrics_coverage" not in k for k in grouped)
  # The dirty stamp readiness looked at is consumed, later stamps are kept.
  dirty_filters = [
    c.kwargs for c in flt.call_args_list if "metrics_dirty_at__lte" in c.kwargs
  ]
  assert dirty_filters and all(
    k["metrics_dirty_at__lte"] == checked_at for k in dirty_filters
  )
  assert metrics_module.record_backfill_skips([], checked_at) == 0


@pytest.mark.django_db(databases=[])
def test_persist_metrics_failed_final_fills_missing_rows_and_stamps_version():
  exists_qs = MagicMock()
  exists_qs.exists.return_value = True
  with (
    patch.object(
      metrics_module.job_data.objects, "filter", return_value=exists_qs
    ) as flt,
    patch.object(metrics_module.metrics_data.objects, "bulk_create") as bc,
    patch.object(
      metrics_module,
      "job_metrics_catalog_entries",
      lambda: [{"type": "cpu", "metric": "avg_cpuusage", "units": "#cores"}],
    ),
    patch.object(
      metrics_module.transaction,
      "atomic",
      MagicMock(return_value=nullcontext()),
    ),
  ):
    assert metrics_module.persist_metrics_failed_final("j9", "boom") is True
  rows, kwargs = bc.call_args.args[0], bc.call_args.kwargs
  # Existing rows (including real values) are kept; only gaps are filled.
  assert kwargs["ignore_conflicts"] is True
  assert rows[0].no_data_reason == metrics_module.METRICS_FAILED_FINAL_REASON
  stamp_calls = [
    c.kwargs
    for c in exists_qs.update.call_args_list
    if "metrics_version" in c.kwargs
  ]
  assert stamp_calls[0]["metrics_version"] == (
    metrics_module.METRICS_CONTRACT_VERSION
  )
  assert stamp_calls[0]["metrics_skipped_since"] is None
  assert any("metrics_dirty_at__lte" in c.kwargs for c in flt.call_args_list)


@pytest.mark.django_db(databases=[])
def test_persist_metrics_failed_final_unknown_job_is_a_noop():
  missing = MagicMock()
  missing.exists.return_value = False
  with (
    patch.object(
      metrics_module.job_data.objects, "filter", return_value=missing
    ),
    patch.object(metrics_module.metrics_data.objects, "bulk_create") as bc,
  ):
    assert metrics_module.persist_metrics_failed_final("nope") is False
  bc.assert_not_called()
