"""repair_jstamp_deltas against the compose Postgres (skips off compose).

Job 79282 shape: one marked host whose ``dev=j:<jid>`` rows carry corrupt
``delta``/``arc`` (neighbour-device differences) and a correct ``value``.
"""

from __future__ import annotations

import datetime as dt
import io

import pytest
from django.core.management import call_command
from django.db import connection

JID = "990001"
HOST = "cmd-jstamp-h1.example"
OTHER = "cmd-jstamp-h2.example"
T0 = dt.datetime(2026, 11, 3, 12, 0, 0, tzinfo=dt.UTC)

pytestmark = pytest.mark.django_db(transaction=True)


def _exec(sql, params=()):
  with connection.cursor() as cur:
    cur.execute(sql, params)


@pytest.fixture
def seeded():
  """One accounted job with an affinity row and four corrupt samples."""
  _exec(
    "INSERT INTO job_data (jid, submit_time, start_time, end_time, username, "
    "host_list, nhosts, ncores) VALUES (%s, %s, %s, %s, 'u', %s, 1, 144)",
    [JID, T0, T0, T0 + dt.timedelta(minutes=3), ["cmd-jstamp-h1"]],
  )
  _exec(
    "INSERT INTO job_affinity (jid, host, cpu_list, gpu_list, "
    "cgroup_controls, reported_at, source) VALUES "
    "(%s, %s, '[\"0-143\"]', '[]', '{}', %s, 'monitor')",
    [JID, HOST, T0],
  )
  rows = []
  for i, (cpu, ib) in enumerate(
    ((100.0, 0.0), (160.0, 10.0), (280.0, 25.0), (400.0, 40.0))
  ):
    t = T0 + dt.timedelta(minutes=i)
    rows.append(
      (t, HOST, JID, "host_cpu", f"j:{JID}", "user", "cs", cpu, 9e8, 9e8)
    )
    rows.append(
      (t, HOST, JID, "host_ib", f"j:{JID}", "port_xmit_data", "B", ib, 9e8, 9e8)
    )
    rows.append(
      (t, HOST, JID, "nvidia_gpu", "0", "gpu_util", "%", 50.0, 0.0, 0.0)
    )
  # Same stamp on a host without an affinity row: not a unit, untouched.
  rows.append(
    (T0, OTHER, JID, "host_cpu", f"j:{JID}", "user", "cs", 1.0, 9e8, 9e8)
  )
  with connection.cursor() as cur:
    cur.executemany(
      "INSERT INTO host_data (time, host, jid, type, dev, event, unit, value, "
      "delta, arc) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
      rows,
    )


def _deltas(host, typ):
  with connection.cursor() as cur:
    cur.execute(
      "SELECT delta, arc FROM host_data WHERE host = %s AND type = %s "
      "ORDER BY time",
      [host, typ],
    )
    return cur.fetchall()


def _run(**opts):
  out = io.StringIO()
  call_command("repair_jstamp_deltas", jid=[JID], stdout=out, **opts)
  return out.getvalue()


def test_census_reads_job_tables_only(seeded):
  out = _run(census=True)
  assert "TOTAL days=1 jobs=1 units=1" in out


def test_dry_run_plans_but_writes_nothing(seeded):
  out = _run()
  assert "fixed=8" in out
  assert _deltas(HOST, "host_cpu")[1] == (pytest.approx(9e8),) * 2


def test_apply_recomputes_from_values_and_marks_job_dirty(seeded):
  out = _run(apply=True)
  assert "jobs_marked_dirty=1" in out
  cpu = _deltas(HOST, "host_cpu")
  assert cpu[0] == (None, None)
  assert cpu[1] == (pytest.approx(60.0), pytest.approx(1.0))
  ib = _deltas(HOST, "host_ib")
  # U=4B multiplier: (25 - 10) * 4.
  assert ib[2] == (pytest.approx(60.0), pytest.approx(1.0))
  # GPU rows and the host without an affinity row are untouched.
  assert _deltas(HOST, "nvidia_gpu")[1] == (0.0, 0.0)
  assert _deltas(OTHER, "host_cpu")[0] == (pytest.approx(9e8),) * 2
  with connection.cursor() as cur:
    cur.execute("SELECT metrics_dirty_at FROM job_data WHERE jid = %s", [JID])
    assert cur.fetchone()[0] is not None
  # Idempotent: a second pass finds nothing to change.
  assert "fixed=0" in _run(apply=True)
