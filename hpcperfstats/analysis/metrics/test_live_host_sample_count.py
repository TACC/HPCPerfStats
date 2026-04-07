"""Tests for live host_data distinct-time expressions."""

from unittest.mock import MagicMock

import pytest

from hpcperfstats.analysis.metrics import live_host_sample_count as lhsc
from hpcperfstats.analysis.metrics.live_host_sample_count import (
    LiveDistinctHostTimeCount,
    LiveJidScopedDistinctHostTimeCount,
    live_distinct_host_time_count_expression,
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


def test_live_jid_scoped_distinct_host_time_count_as_sql_postgresql():
  expr = LiveJidScopedDistinctHostTimeCount(".example.com")
  sql, params = expr.as_sql(MagicMock(), _fake_pg_connection())
  assert params == []
  assert 'h."jid" = "job_data"."jid"' in sql
  assert 'unnest' not in sql.lower()


def test_live_distinct_host_time_count_as_sql_non_postgresql_raises():
  conn = MagicMock()
  conn.vendor = "sqlite"
  expr = LiveDistinctHostTimeCount(".tacc.utexas.edu")
  with pytest.raises(NotImplementedError) as ei:
    expr.as_sql(MagicMock(), conn)
  assert "PostgreSQL" in str(ei.value)


def test_live_distinct_factory_legacy(monkeypatch):
  monkeypatch.setenv("HPCPERFSTATS_LIVE_DISTINCT_LEGACY_HOSTLIST", "1")
  expr = live_distinct_host_time_count_expression(".x")
  assert type(expr) is LiveDistinctHostTimeCount


def test_live_distinct_factory_default_jid_scoped(monkeypatch):
  monkeypatch.delenv("HPCPERFSTATS_LIVE_DISTINCT_LEGACY_HOSTLIST", raising=False)
  expr = live_distinct_host_time_count_expression(".x")
  assert type(expr) is LiveJidScopedDistinctHostTimeCount


def test_live_distinct_factory_reloads_env_each_call(monkeypatch):
  monkeypatch.delenv("HPCPERFSTATS_LIVE_DISTINCT_LEGACY_HOSTLIST", raising=False)
  assert type(live_distinct_host_time_count_expression(".x")) is (
      LiveJidScopedDistinctHostTimeCount)
  monkeypatch.setenv("HPCPERFSTATS_LIVE_DISTINCT_LEGACY_HOSTLIST", "1")
  assert type(live_distinct_host_time_count_expression(".x")) is (
      LiveDistinctHostTimeCount)
