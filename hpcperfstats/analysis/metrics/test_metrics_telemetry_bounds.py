"""Regression: compute_metrics persists in-window telemetry bounds on job_data."""

import os
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from hpcperfstats.analysis.metrics.metrics import (
    _in_window_telemetry_bounds_for_job,
    _persist_metrics_batch,
)
from hpcperfstats.site.machine.models import host_data, job_data, metrics_data


def _compose_network():
  return os.environ.get("HPCPERFSTATS_COMPOSE_NETWORK", "").strip().lower() in (
      "1",
      "yes",
      "true",
  )


pytestmark = pytest.mark.skipif(
    not _compose_network(),
    reason=(
        "Requires Docker Compose network (PostgreSQL at host 'db'). "
        "Run: tests/run_db_pytest_workflow.sh"
    ),
)


@pytest.mark.django_db(transaction=True)
def test_job_data_telemetry_fields_nullable_before_metrics():
  now = timezone.now()
  j = job_data.objects.create(
      jid="telnull1",
      submit_time=now,
      start_time=now,
      end_time=now,
      username="u1",
      host_list=["h1.example.org"],
  )
  j.refresh_from_db()
  assert j.telemetry_first_time is None
  assert j.telemetry_last_time is None


@pytest.mark.django_db(transaction=True)
def test_in_window_telemetry_bounds_for_job():
  start = timezone.now() - timedelta(hours=2)
  end = timezone.now() - timedelta(hours=1)
  host = "telhost.example.org"
  j = job_data.objects.create(
      jid="telb1",
      submit_time=start,
      start_time=start,
      end_time=end,
      username="u1",
      host_list=[host],
  )
  t_first = start + timedelta(minutes=5)
  t_last = end - timedelta(minutes=5)
  host_data.objects.create(
      time=t_first,
      host=host,
      type="host_cpu",
      event="user",
      value=1.0,
  )
  host_data.objects.create(
      time=t_last,
      host=host,
      type="host_cpu",
      event="idle",
      value=0.0,
  )
  tf, tl = _in_window_telemetry_bounds_for_job(j)
  assert tf == t_first
  assert tl == t_last


@pytest.mark.django_db(transaction=True)
def test_in_window_telemetry_bounds_for_lightweight_job_ref():
  """Scheduler passes SimpleNamespace(jid=...) into compute_metrics."""
  start = timezone.now() - timedelta(hours=2)
  end = timezone.now() - timedelta(hours=1)
  host = "telref.example.org"
  jid = "telref1"
  job_data.objects.create(
      jid=jid,
      submit_time=start,
      start_time=start,
      end_time=end,
      username="u1",
      host_list=[host],
  )
  t_first = start + timedelta(minutes=5)
  t_last = end - timedelta(minutes=5)
  host_data.objects.create(
      time=t_first,
      host=host,
      type="host_cpu",
      event="user",
      value=1.0,
  )
  host_data.objects.create(
      time=t_last,
      host=host,
      type="host_cpu",
      event="idle",
      value=0.0,
  )
  tf, tl = _in_window_telemetry_bounds_for_job(SimpleNamespace(jid=jid))
  assert tf == t_first
  assert tl == t_last


@pytest.mark.django_db(transaction=True)
def test_persist_metrics_batch_writes_telemetry_bounds():
  start = timezone.now() - timedelta(hours=1)
  end = timezone.now()
  t_first = start + timedelta(minutes=2)
  t_last = end - timedelta(minutes=2)
  j = job_data.objects.create(
      jid="telpersist1",
      submit_time=start,
      start_time=start,
      end_time=end,
      username="u1",
      host_list=["h1.example.org"],
  )
  rows = [{
      "jid": j,
      "type": "job",
      "metric": "walltime",
      "units": "s",
      "value": 3600.0,
      "no_data_reason": None,
  }]
  _persist_metrics_batch(
      rows,
      distinct_time_count=10,
      telemetry_first_time=t_first,
      telemetry_last_time=t_last,
  )
  j.refresh_from_db()
  assert j.metrics_distinct_time_count == 10
  assert j.telemetry_first_time == t_first
  assert j.telemetry_last_time == t_last
  assert metrics_data.objects.filter(jid=j).count() == 1
