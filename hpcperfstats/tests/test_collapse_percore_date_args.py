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


def test_discovery_sql_targets_digit_leading_cpu_dev():
  assert "type = 'cpu'" in cmd_mod._DISCOVER_DAYS_SQL
  assert "dev ~ '^[0-9]'" in cmd_mod._DISCOVER_DAYS_SQL
  assert "time >=" not in cmd_mod._DISCOVER_DAYS_SQL
