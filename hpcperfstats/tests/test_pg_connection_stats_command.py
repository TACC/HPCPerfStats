"""Test pg_connection_stats management command with mocked DB connection."""

import os
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(scope="module", autouse=True)
def _django_setup():
  os.environ.setdefault(
      "DJANGO_SETTINGS_MODULE",
      "hpcperfstats.site.hpcperfstats_site.settings",
  )
  import django
  django.setup()


def test_pg_connection_stats_outputs_counts():
  from django.core.management import call_command

  mock_conn = MagicMock()
  mock_conn.vendor = "postgresql"
  inner = MagicMock()
  inner.fetchone.return_value = (5, 1, 2, 0)
  cm = MagicMock()
  cm.__enter__.return_value = inner
  cm.__exit__.return_value = False
  mock_conn.cursor.return_value = cm
  path = "hpcperfstats.site.machine.management.commands.pg_connection_stats.connection"
  with patch(path, mock_conn):
    out = StringIO()
    call_command("pg_connection_stats", stdout=out)
  text = out.getvalue()
  assert "total=5" in text
  assert "active=1" in text


def test_pg_connection_stats_skips_non_postgresql():
  from django.core.management import call_command

  mock_conn = MagicMock()
  mock_conn.vendor = "sqlite"
  path = "hpcperfstats.site.machine.management.commands.pg_connection_stats.connection"
  err = StringIO()
  with patch(path, mock_conn):
    call_command("pg_connection_stats", stderr=err)
  assert "PostgreSQL" in err.getvalue()
