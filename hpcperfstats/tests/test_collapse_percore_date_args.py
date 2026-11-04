"""collapse_percore_host_data date handling (no DB: queries are mocked)."""

from __future__ import annotations

import io
from unittest import mock

import pytest
from django.core.management.base import CommandError, OutputWrapper

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
