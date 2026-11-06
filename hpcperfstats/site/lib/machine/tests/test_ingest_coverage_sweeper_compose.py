"""Compose Timescale: per-host coverage GROUP BY with literal bounds."""

import os
from datetime import timedelta

import pytest
from django.db import DatabaseError
from django.utils import timezone

from hpcperfstats.dbload.lib import ingest_coverage as ic


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
def test_scan_host_pairs_returns_only_that_hosts_window_pairs():
  from hpcperfstats.site.lib.machine.host_data_latest import (
    latest_sample_time_by_host,
  )
  from hpcperfstats.site.lib.machine.models import host_data

  now = timezone.now().replace(microsecond=0)
  host, other = "cov1.example.org", "cov2.example.org"
  rows = [
    (now - timedelta(minutes=5), host, "host_cpu", "user", 3.0),
    (now - timedelta(minutes=4), host, "host_cpu", "nice", 0.0),
    (now - timedelta(minutes=3), host, "net_error", "rx_err", 1.0),
    (now - timedelta(minutes=2), host, "host_opa", "tx_error", 1.0),
    (now - timedelta(hours=3), host, "old_type", "old_event", 1.0),
    (now - timedelta(minutes=1), other, "nvidia_gpu", "gpu_util", 9.0),
  ]
  for t, h, typ, ev, val in rows:
    host_data.objects.create(time=t, host=h, type=typ, event=ev, value=val)
  latest = latest_sample_time_by_host(
    [host, other, "absent.example.org"], since=now - timedelta(days=7)
  )
  assert set(latest) == {host, other}
  newest = latest[host]
  got = ic.scan_host_pairs(
    host, newest - timedelta(minutes=60), newest, timeout_ms=60000
  )
  assert got == [
    ("host_cpu", "nice", 1, False),
    ("host_cpu", "user", 1, True),
  ], "own window only, error names excluded, other hosts excluded"


@pytest.mark.django_db(transaction=True)
def test_scan_host_pairs_respects_statement_timeout(monkeypatch):
  monkeypatch.setattr(
    ic,
    "SCAN_SQL",
    "SELECT 'a', 'b', 1::bigint, true FROM pg_sleep(%s::float / 1000)"
    " WHERE %s::timestamptz IS NOT NULL OR %s::timestamptz IS NULL",
  )
  now = timezone.now()
  with pytest.raises(DatabaseError, match="statement timeout"):
    ic.scan_host_pairs("2000", now, now, timeout_ms=1000)
