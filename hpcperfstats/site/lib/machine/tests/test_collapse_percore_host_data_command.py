"""collapse_percore_host_data against the compose Postgres (skips off compose)."""

from __future__ import annotations

import datetime as dt
import io

import pytest
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext

HOST = "cmd-percore-h1"
T0 = dt.datetime(2026, 10, 24, 12, 0, 0, tzinfo=dt.UTC)
T1 = T0 + dt.timedelta(seconds=10)

pytestmark = pytest.mark.django_db

# The host-discovery scan; the planning query is "SELECT DISTINCT host, type...".
_DISCOVERY = "SELECT DISTINCT host FROM host_data"


def _insert(rows):
  with connection.cursor() as cur:
    cur.executemany(
      "INSERT INTO host_data (time, host, jid, type, dev, event, unit, value, "
      "arc, delta) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
      rows,
    )


def _rows(where=""):
  with connection.cursor() as cur:
    cur.execute(
      "SELECT time, type, dev, event, value, delta, arc FROM host_data "
      f"WHERE host = %s {where} ORDER BY type, event, dev, time",
      [HOST],
    )
    return cur.fetchall()


@pytest.fixture
def seeded():
  rows = []
  for t, vals in ((T0, (1.0, 4.0)), (T1, (2.0, 6.0))):
    for dev, value in zip(("0", "1"), vals, strict=True):
      rows.append((t, HOST, "77", "cpu", dev, "user", "cs", value, 1.0, 1.0))
  # A whole-node row already exists for T0 and must be kept as is.
  rows.append((T0, HOST, "77", "cpu", "", "user", "cs", 99.0, 9.0, 9.0))
  rows.append((T0, HOST, "77", "nvidia_gpu", "0", "gpu_util", "%", 5.0, 0, 0))
  rows.append((T0, HOST, "77", "cpu", "j:88", "user", "cs", 3.0, 0, 0))
  _insert(rows)


def _run(*, apply, hosts=None, **dates):
  out = io.StringIO()
  dates = dates or {"since": "2026-10-24", "until": "2026-10-24"}
  call_command(
    "collapse_percore_host_data",
    host=[HOST] if hosts is None else hosts,
    apply=apply,
    stdout=out,
    **dates,
  )
  return out.getvalue()


def test_every_query_runs_with_the_statement_timeout_disabled(seeded):
  """Regression: host discovery ran under the portal default and timed out."""
  with CaptureQueriesContext(connection) as ctx:
    _run(apply=False, hosts=[])
  sqls = [q["sql"] for q in ctx.captured_queries]
  disable = "SET LOCAL statement_timeout = 0"
  discovery = next(i for i, s in enumerate(sqls) if _DISCOVERY in s)
  assert disable in sqls[:discovery]
  planning = next(i for i, s in enumerate(sqls) if "AND dev = ''" in s)
  assert disable in sqls[discovery + 1 : planning]


def test_explicit_host_skips_discovery(seeded):
  with CaptureQueriesContext(connection) as ctx:
    text = _run(apply=False)
  assert not any(_DISCOVERY in q["sql"] for q in ctx.captured_queries)
  assert "hosts_with_percore=1" in text


def test_dry_run_changes_nothing(seeded):
  before = _rows()
  text = _run(apply=False)
  assert "DRY RUN" in text
  assert "percore_rows=4" in text
  assert _rows() == before


def test_apply_sums_cores_keeps_existing_and_leaves_gpu_and_stamped_rows(
  seeded,
):
  text = _run(apply=True)
  assert "APPLY" in text
  cpu = [r for r in _rows() if r[1] == "cpu"]
  by_key = {(r[0], r[2]): r for r in cpu}
  # per-core rows are gone; the stamped row and the existing row remain
  assert {dev for _t, dev in by_key} == {"", "j:88"}
  assert by_key[(T0, "")][4] == 99.0  # existing whole-node row untouched
  new = by_key[(T1, "")]
  assert new[4] == 8.0 and new[5] == 2.0  # value, delta summed
  assert abs(new[6] - 0.2) < 1e-6  # arc = delta / dt (2 / 10)
  gpu = [r for r in _rows() if r[1] == "nvidia_gpu"]
  assert [(r[2], r[4]) for r in gpu] == [("0", 5.0)]


def test_apply_twice_is_a_noop(seeded):
  _run(apply=True)
  after_first = _rows()
  text = _run(apply=True)
  assert "TOTAL percore_rows=0 " in text
  assert _rows() == after_first


def test_no_dates_discovers_days_over_the_whole_table(seeded):
  old = dt.datetime(2026, 8, 24, 3, 0, 0, tzinfo=dt.UTC)
  _insert(
    [
      (old, HOST, "77", "cpu", "0", "user", "cs", 1.0, 1.0, 1.0),
      (old, HOST, "77", "cpu", "1", "user", "cs", 2.0, 1.0, 1.0),
    ]
  )
  text = _run(apply=True, since=None, until=None)
  assert "all days with per-core cpu rows" in text
  devs = {r[2] for r in _rows() if r[1] == "cpu"}
  assert "0" not in devs and "1" not in devs
  assert any(r[0] == old and r[2] == "" for r in _rows())
