"""Compose-backed regression for window-coverage metrics readiness."""

import os
from datetime import timedelta

import pytest
from django.utils import timezone

from hpcperfstats.analysis.metrics import update_metrics as um
from hpcperfstats.site.lib.machine.tests.test_update_metrics_telemetry_coverage import (
    _patch_coverage_on,
)


def _compose_network():
  return os.environ.get("HPCPERFSTATS_COMPOSE_NETWORK", "").strip().lower() in (
      "1",
      "yes",
      "true",
  )


@pytest.mark.skipif(
    not _compose_network(),
    reason=(
        "Requires Docker Compose network (PostgreSQL at host 'db'). "
        "Run: tests/run_db_pytest_workflow.sh"
    ),
)
@pytest.mark.django_db(transaction=True)
def test_window_coverage_compose_defer_until_early_sample_inserted(monkeypatch):
  """Real Postgres: tail-only host_data defers; early sample passes coverage gate."""
  _patch_coverage_on(monkeypatch)
  from hpcperfstats.site.lib.machine.models import host_data, job_data

  now = timezone.now()
  start = now - timedelta(hours=4)
  end = now - timedelta(hours=1)
  host = "covtest1.example.org"
  jid = "covgate1"
  job_data.objects.create(
      jid=jid,
      submit_time=start,
      start_time=start,
      end_time=end,
      username="u1",
      host_list=[host],
  )
  tail_time = end - timedelta(seconds=30)
  host_data.objects.create(
      time=tail_time,
      host=host,
      jid=jid,
      type="host_cpu",
      event="user",
      value=1.0,
  )
  assert um._filter_jids_with_samples_after_end([jid]) == []

  early_time = start + timedelta(minutes=1)
  host_data.objects.create(
      time=early_time,
      host=host,
      jid=jid,
      type="host_cpu",
      event="idle",
      value=0.0,
  )
  assert um._filter_jids_with_samples_after_end([jid]) == [jid]


@pytest.mark.skipif(
    not _compose_network(),
    reason=(
        "Requires Docker Compose network (PostgreSQL at host 'db'). "
        "Run: tests/run_db_pytest_workflow.sh"
    ),
)
@pytest.mark.django_db(transaction=True)
def test_cross_job_shared_host_strict_coverage_passes_job_b(monkeypatch):
  """Shared-host early sample from job A can satisfy job B start margin (by design)."""
  _patch_coverage_on(monkeypatch)
  from hpcperfstats.site.lib.machine.models import host_data, job_data

  now = timezone.now()
  shared_host = "shared.example.org"
  start_a = now - timedelta(hours=6)
  end_a = now - timedelta(hours=3)
  start_b = now - timedelta(hours=4)
  end_b = now - timedelta(hours=1)
  job_data.objects.create(
      jid="job_a",
      submit_time=start_a,
      start_time=start_a,
      end_time=end_a,
      username="u1",
      host_list=[shared_host],
  )
  job_data.objects.create(
      jid="job_b",
      submit_time=start_b,
      start_time=start_b,
      end_time=end_b,
      username="u2",
      host_list=[shared_host],
  )
  early_for_b = start_b + timedelta(minutes=1)
  host_data.objects.create(
      time=early_for_b,
      host=shared_host,
      jid="job_a",
      type="host_cpu",
      event="user",
      value=1.0,
  )
  late_for_b = end_b - timedelta(seconds=30)
  host_data.objects.create(
      time=late_for_b,
      host=shared_host,
      jid="job_b",
      type="host_cpu",
      event="idle",
      value=0.0,
  )
  assert um._filter_jids_with_samples_after_end(["job_b"]) == ["job_b"]
