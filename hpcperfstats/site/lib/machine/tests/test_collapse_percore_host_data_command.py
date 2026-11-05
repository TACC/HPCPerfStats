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


def test_discovery_finds_monitor_type_names_and_non_numeric_devs(seeded):
  """Regression: discovery only matched type 'cpu' with a digit-leading dev."""
  from hpcperfstats.site.lib.machine.management.commands import (
    collapse_percore_host_data as cmd_mod,
  )

  def at(day):
    return dt.datetime(2026, 8, day, 3, 0, 0, tzinfo=dt.UTC)

  _insert(
    [
      (at(1), HOST, "77", "host_cpu", "17", "user", "cs", 1.0, 1.0, 1.0),
      (at(2), HOST, "77", "host_net", "em1", "rx_bytes", "B", 1.0, 1.0, 1.0),
      (at(3), HOST, "77", "host_vm", "-", "pgfault", "#", 1.0, 1.0, 1.0),
      # Days holding only rows that legitimately keep (or lack) a dev.
      (at(4), HOST, "77", "nvidia_gpu", "0", "gpu_util", "%", 1.0, 0, 0),
      (at(5), HOST, "77", "host_cpu", "j:88", "user", "cs", 1.0, 0, 0),
      (at(6), HOST, "77", "host_cpu", "", "user", "cs", 1.0, 0, 0),
    ]
  )
  found = {d.date() for d in cmd_mod.Command()._discover_days()}
  assert {at(1).date(), at(2).date(), at(3).date(), T0.date()} <= found
  assert not found & {at(4).date(), at(5).date(), at(6).date()}


@pytest.mark.django_db(transaction=True)
def test_locked_window_is_deferred_then_repaired_once_the_lock_is_gone():
  """Regression: a window blocked by another session's lock (compress,
  decompress, VACUUM) waited forever; now it is deferred and retried.
  """
  from unittest import mock

  from django.db import connections

  from hpcperfstats.site.lib.machine.management.commands import (
    collapse_percore_host_data as cmd_mod,
  )

  rows = []
  for t, vals in ((T0, (1.0, 4.0)), (T1, (2.0, 6.0))):
    for dev, value in zip(("0", "1"), vals, strict=True):
      rows.append((t, HOST, "77", "cpu", dev, "user", "cs", value, 1.0, 1.0))
  _insert(rows)
  other = connections.create_connection("default")
  other.set_autocommit(False)
  with other.cursor() as cur:
    cur.execute("LOCK TABLE host_data IN ACCESS EXCLUSIVE MODE")
  released = []

  def release(_seconds):
    other.rollback()
    released.append(True)

  out = io.StringIO()
  try:
    with mock.patch.object(cmd_mod.time, "sleep", side_effect=release):
      call_command(
        "collapse_percore_host_data",
        since="2026-10-24",
        until="2026-10-24",
        host=[HOST],
        apply=True,
        window_hours=24,
        lock_timeout_seconds=1,
        lock_retry_sleep_seconds=0,
        stdout=out,
      )
  finally:
    other.rollback()
    other.close()
  text = out.getvalue()
  assert "LOCKED sqlstate=55P03" in text
  assert "RETRY pass=1" in text and released
  devs = {r[2] for r in _rows() if r[1] == "cpu"}
  assert devs == {""}


@pytest.mark.django_db(transaction=True)
def test_a_lock_on_another_days_chunk_does_not_block_the_window():
  """Regression: hpcperfstats04 2026-11-05, every window LOCKED while the
  compression policy held ExclusiveLock on an unrelated old chunk, because
  the hypertable DELETE locks every chunk while planning. The DELETE now
  names the window's chunk, so only that chunk's locks matter.
  """
  from unittest import mock

  from django.db import connections

  from hpcperfstats.site.lib.machine.management.commands import (
    collapse_percore_host_data as cmd_mod,
  )

  rows = []
  for t, vals in ((T0, (1.0, 4.0)), (T1, (2.0, 6.0))):
    for dev, value in zip(("0", "1"), vals, strict=True):
      rows.append((t, HOST, "77", "cpu", dev, "user", "cs", value, 1.0, 1.0))
  old = T0 - dt.timedelta(days=5)
  rows.append((old, HOST, "77", "cpu", "", "user", "cs", 1.0, 0, 0))
  _insert(rows)
  with connection.cursor() as cur:
    cur.execute(
      "SELECT format('%%I.%%I', chunk_schema, chunk_name) "
      "FROM timescaledb_information.chunks WHERE hypertable_name = "
      "'host_data' AND range_start <= %s AND range_end > %s",
      [old, old],
    )
    (old_chunk,) = cur.fetchone()
  other = connections.create_connection("default")
  other.set_autocommit(False)
  try:
    with other.cursor() as cur:
      cur.execute(f"LOCK TABLE {old_chunk} IN EXCLUSIVE MODE")
    out = io.StringIO()
    # A deferred window would sleep before its retry pass: fail instead.
    with mock.patch.object(
      cmd_mod.time, "sleep", side_effect=AssertionError("window deferred")
    ):
      call_command(
        "collapse_percore_host_data",
        since="2026-10-24",
        until="2026-10-24",
        host=[HOST],
        apply=True,
        lock_timeout_seconds=1,
        stdout=out,
      )
  finally:
    other.rollback()
    other.close()
  assert "LOCKED" not in out.getvalue()
  assert {r[2] for r in _rows() if r[1] == "cpu"} == {""}
