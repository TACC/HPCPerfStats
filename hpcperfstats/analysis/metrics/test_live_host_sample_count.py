"""Tests for LiveDistinctHostTimeCount expression."""

from unittest.mock import MagicMock

import pytest

from hpcperfstats.analysis.metrics.live_host_sample_count import (
    LiveDistinctHostTimeCount,
)


def _fake_pg_connection():
  conn = MagicMock()
  conn.vendor = "postgresql"

  def quote_name(name):
    return '"%s"' % str(name).replace('"', '""')

  conn.ops.quote_name = quote_name
  return conn


def test_live_distinct_host_time_count_as_sql_postgresql():
  """Emits one bind param (FQDN suffix); correlates via quoted job_data columns."""
  expr = LiveDistinctHostTimeCount(".example.com")
  sql, params = expr.as_sql(MagicMock(), _fake_pg_connection())
  assert params == [".example.com"]
  assert sql.count("%s") == 1
  inner = sql.strip()
  assert inner.startswith("(") and inner.endswith(")")
  body = inner[1:-1]
  assert 'unnest("job_data"."host_list")' in body
  assert 'h.time >= "job_data"."start_time"' in body
  assert 'h.time <= "job_data"."end_time"' in body
  assert 'from "host_data" h' in body.lower()


def test_live_distinct_host_time_count_as_sql_non_postgresql_raises():
  conn = MagicMock()
  conn.vendor = "sqlite"
  expr = LiveDistinctHostTimeCount(".tacc.utexas.edu")
  with pytest.raises(NotImplementedError) as ei:
    expr.as_sql(MagicMock(), conn)
  assert "PostgreSQL" in str(ei.value)
