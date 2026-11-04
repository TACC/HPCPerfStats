"""collapse_percore_host_data date handling (no DB: queries are mocked)."""

from __future__ import annotations

import io
from unittest import mock

import pytest
from django.core.management.base import CommandError, OutputWrapper

from hpcperfstats.dbload.lib.host_data_percore_collapse import CollapsePlan
from hpcperfstats.site.lib.machine.management.commands import (
  collapse_percore_host_data as cmd_mod,
)

_BASE = {
  "apply": False,
  "host": [],
  "window_hours": 6,
  "host_batch": 8,
  "lookback_minutes": 120,
}


def _command() -> tuple[cmd_mod.Command, io.StringIO]:
  buf = io.StringIO()
  cmd = cmd_mod.Command()
  cmd.stdout = OutputWrapper(buf)
  return cmd, buf


@pytest.fixture(autouse=True)
def _postgres(monkeypatch):
  monkeypatch.setattr(
    type(cmd_mod.connection), "vendor", "postgresql", raising=False
  )


@pytest.mark.parametrize(
  "dates",
  [
    {"since": "2026-10-24", "until": None},
    {"since": None, "until": "2026-10-24"},
  ],
)
def test_one_date_without_the_other_is_rejected(dates):
  cmd, _ = _command()
  with pytest.raises(CommandError, match="both --since and --until"):
    cmd.handle(**_BASE, **dates)


def test_no_dates_walks_every_discovered_day_not_a_range():
  cmd, buf = _command()
  days = [cmd_mod._parse_day("2026-08-24"), cmd_mod._parse_day("2026-10-01")]
  with (
    mock.patch.object(cmd_mod.Command, "_discover_days", return_value=days),
    mock.patch.object(
      cmd_mod.Command, "_percore_hosts", return_value=[]
    ) as hosts,
  ):
    cmd.handle(since=None, until=None, **_BASE)
  text = buf.getvalue()
  assert "all days with per-core cpu rows (2 days)" in text
  assert hosts.call_count == 8  # 2 days x 4 six-hour windows, none in between
  assert "2026-09" not in text


def test_discovery_sql_uses_the_percore_predicate_over_the_whole_table():
  """Regression: discovery matched only type 'cpu' with a digit-leading dev.

  The monitor writes ``host_cpu``/``host_cpu_hw`` and non-numeric devs
  (``em1``, ``sda``, ``-``), so that predicate missed days the per-window
  repair predicate (``_PERCORE_WHERE``) selects.
  """
  assert cmd_mod._PERCORE_WHERE in cmd_mod._DISCOVER_DAYS_SQL
  assert "type = 'cpu'" not in cmd_mod._DISCOVER_DAYS_SQL
  assert "time >=" not in cmd_mod._DISCOVER_DAYS_SQL


def test_delete_uses_the_planning_predicate_without_a_per_row_probe():
  """Regression: the EXISTS probe for a dev='' row read every device row of a
  (host, time) per deleted row; one 8-host hour took 20+ min on hpcperfstats01.
  """
  sql = cmd_mod._DELETE_SQL
  assert "EXISTS" not in sql and " w " not in sql
  assert sql.endswith(f"AND {cmd_mod._PERCORE_WHERE}")
  # hosts, start, end, then the GPU types inside _PERCORE_WHERE
  assert sql.replace("%%", "").count("%s") == 4


def test_discover_days_binds_the_gpu_types():
  cursor = mock.MagicMock()
  cursor.fetchall.return_value = []
  conn = mock.MagicMock()
  conn.cursor.return_value.__enter__.return_value = cursor
  with (
    mock.patch.object(cmd_mod, "connection", conn),
    mock.patch.object(cmd_mod.transaction, "atomic"),
  ):
    assert cmd_mod.Command()._discover_days() == []
  cursor.execute.assert_any_call(
    cmd_mod._DISCOVER_DAYS_SQL, [list(cmd_mod.GPU_TYPES)]
  )


def _lock_error(code: str = "55P03") -> cmd_mod.OperationalError:
  cause = type("PgError", (Exception,), {"sqlstate": code})()
  err = cmd_mod.OperationalError("canceling statement due to lock timeout")
  err.__cause__ = cause
  return err


def _plan(rows: int = 3) -> CollapsePlan:
  return CollapsePlan([None] * rows, rows, rows, 0)


@pytest.mark.parametrize("code", ["55P03", "40P01"])
def test_locked_batch_is_deferred_and_retried_until_it_runs(code):
  """A window that cannot get its locks is skipped and come back to."""
  cmd, buf = _command()
  calls = []

  def migrate(start, end, hosts, apply, options):
    calls.append(hosts[0])
    if hosts[0] == "h1" and calls.count("h1") < 3:
      raise _lock_error(code)
    return _plan()

  opts = {
    **_BASE,
    "window_hours": 24,
    "host_batch": 1,
    "lock_retry_sleep_seconds": 7.0,
  }
  with (
    mock.patch.object(
      cmd_mod.Command, "_percore_hosts", return_value=["h1", "h2"]
    ),
    mock.patch.object(cmd_mod.Command, "_migrate", side_effect=migrate),
    mock.patch.object(cmd_mod.time, "sleep") as sleep,
  ):
    cmd.handle(since="2026-10-25", until="2026-10-25", **opts)
  text = buf.getvalue()
  assert calls == ["h1", "h2", "h1", "h1"]  # h2 is not held up by h1
  assert text.count(f"LOCKED sqlstate={code}") == 2
  assert "RETRY pass=1 locked_batches=1 in 7s" in text
  assert "RETRY pass=2" in text and "RETRY pass=3" not in text
  assert sleep.call_args_list == [mock.call(7.0), mock.call(7.0)]
  assert "TOTAL percore_rows=6 " in text  # both batches counted once


def test_other_database_errors_still_stop_the_command():
  cmd, _ = _command()
  with (
    mock.patch.object(cmd_mod.Command, "_percore_hosts", return_value=["h1"]),
    mock.patch.object(
      cmd_mod.Command,
      "_migrate",
      side_effect=cmd_mod.OperationalError("server closed the connection"),
    ),
    pytest.raises(cmd_mod.OperationalError),
  ):
    cmd.handle(since="2026-10-25", until="2026-10-25", **_BASE)


def test_each_window_sets_a_lock_timeout():
  cursor = mock.MagicMock()
  cursor.fetchall.return_value = []
  conn = mock.MagicMock()
  conn.cursor.return_value.__enter__.return_value = cursor
  t0 = cmd_mod._parse_day("2026-10-25")
  with (
    mock.patch.object(cmd_mod, "connection", conn),
    mock.patch.object(cmd_mod.transaction, "atomic"),
  ):
    cmd_mod.Command()._migrate(
      t0, t0, ["h"], True, {"lookback_minutes": 120, "lock_timeout_seconds": 9}
    )
  sqls = [c.args[0] for c in cursor.execute.call_args_list]
  assert "SET LOCAL lock_timeout = '9s'" in sqls
