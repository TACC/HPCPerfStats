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
  inner.fetchone.side_effect = [
      (5, 1, 2, 0),
      ("update_metrics.py [thread:metrics-pool]", 4, 0, 4),
      ("gunicorn: worker", 1, 1, 0),
  ]
  inner.fetchall.return_value = [
      ("update_metrics.py [thread:metrics-pool]", 4, 0, 4),
      ("gunicorn: worker", 1, 1, 0),
  ]
  cm = MagicMock()
  cm.__enter__.return_value = inner
  cm.__exit__.return_value = False
  mock_conn.cursor.return_value = cm
  path = "hpcperfstats.site.lib.machine.management.commands.pg_connection_stats.connection"
  with patch(path, mock_conn):
    out = StringIO()
    call_command("pg_connection_stats", stdout=out)
  text = out.getvalue()
  assert "total=5" in text
  assert "active=1" in text
  assert "application_name=" in text
  assert "update_metrics.py" in text


def test_pg_connection_stats_skips_non_postgresql():
  from django.core.management import call_command

  mock_conn = MagicMock()
  mock_conn.vendor = "sqlite"
  path = "hpcperfstats.site.lib.machine.management.commands.pg_connection_stats.connection"
  err = StringIO()
  with patch(path, mock_conn):
    call_command("pg_connection_stats", stderr=err)
  assert "PostgreSQL" in err.getvalue()
