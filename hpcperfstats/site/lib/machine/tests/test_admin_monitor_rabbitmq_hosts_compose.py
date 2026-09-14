"""Compose Postgres: last-7-day job_data.host_list unnest for Admin Monitor."""

from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone

from hpcperfstats.site.lib.machine.models import job_data


@pytest.mark.django_db
def test_job_table_host_fqdns_last_7d_unnest_against_postgres():
  """Compose Postgres: DISTINCT unnest(host_list) for end_time last 7 days."""
  if connection.vendor != "postgresql":
    pytest.skip("job_data.host_list ArrayField is PostgreSQL-specific")

  from hpcperfstats.site.lib.machine import api

  now = timezone.now()
  job_data.objects.create(
    jid="rmq-silent-recent",
    submit_time=now - timedelta(hours=2),
    start_time=now - timedelta(hours=1),
    end_time=now - timedelta(minutes=30),
    runtime=1800.0,
    username="u",
    host_list=["c101-001", "c101-002.local"],
  )
  job_data.objects.create(
    jid="rmq-silent-old",
    submit_time=now - timedelta(days=10),
    start_time=now - timedelta(days=9),
    end_time=now - timedelta(days=8),
    runtime=3600.0,
    username="u",
    host_list=["old-node.local"],
  )
  hosts = api._job_table_host_fqdns_last_7d()
  assert "c101-001.local" in hosts
  assert "c101-002.local" in hosts
  assert "old-node.local" not in hosts
