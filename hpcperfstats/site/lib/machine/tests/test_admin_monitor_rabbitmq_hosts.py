"""Admin Monitor RabbitMQ host stats: last-7-day job_data silent census."""

from __future__ import annotations

import contextlib
from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory


@pytest.mark.machine_unit_mock
class TestRabbitmqHostsSilentCensus:
  """Merge Redis recent_host rows with last-7-day job_data FQDNs."""

  def test_appends_silent_hosts_as_gt_week_with_null_last_time(self):
    from hpcperfstats.site.lib.machine import api

    redis_row = {
      "host": "seen.example.com",
      "last_time": "2026-09-14T10:00:00+00:00",
      "age_bucket": "ok",
    }
    with patch.object(
      api,
      "_get_recent_rabbitmq_host_stats",
      return_value=[redis_row],
    ), patch.object(
      api,
      "_job_table_host_fqdns_last_7d",
      return_value=["silent.example.com", "seen.example.com"],
    ), patch.object(api, "cached_orm", side_effect=lambda _k, _t, fn: fn()):
      rows = api._get_rabbitmq_hosts_section()

    assert rows[0] == redis_row
    silent = [r for r in rows if r["host"] == "silent.example.com"]
    assert silent == [
      {
        "host": "silent.example.com",
        "last_time": None,
        "age_bucket": "gt_week",
      }
    ]

  def test_casefold_dedupes_redis_and_job_table_hosts(self):
    from hpcperfstats.site.lib.machine import api

    redis_row = {
      "host": "NODE.example.com",
      "last_time": "2026-09-14T10:00:00+00:00",
      "age_bucket": "ok",
    }
    with patch.object(
      api,
      "_get_recent_rabbitmq_host_stats",
      return_value=[redis_row],
    ), patch.object(
      api,
      "_job_table_host_fqdns_last_7d",
      return_value=["node.example.com"],
    ), patch.object(api, "cached_orm", side_effect=lambda _k, _t, fn: fn()):
      rows = api._get_rabbitmq_hosts_section()

    assert len(rows) == 1
    assert rows[0]["host"] == "NODE.example.com"

  def test_census_error_keeps_redis_rows_only(self):
    from hpcperfstats.site.lib.machine import api

    redis_row = {
      "host": "seen.example.com",
      "last_time": "2026-09-14T10:00:00+00:00",
      "age_bucket": "ok",
    }
    with patch.object(
      api,
      "_get_recent_rabbitmq_host_stats",
      return_value=[redis_row],
    ), patch.object(
      api,
      "cached_orm",
      side_effect=RuntimeError("cache/db down"),
    ):
      rows = api._get_rabbitmq_hosts_section()

    assert rows == [redis_row]

  def test_job_table_sql_uses_unnest_not_btrim(self):
    from hpcperfstats.site.lib.machine import api

    cursor = MagicMock()
    cursor.fetchall.return_value = [("c101-001",), ("c101-002.local",)]
    cursor_cm = MagicMock()
    cursor_cm.__enter__.return_value = cursor
    cursor_cm.__exit__.return_value = None

    with patch.object(api.connection, "vendor", "postgresql"), patch.object(
      api.transaction,
      "atomic",
      return_value=contextlib.nullcontext(),
    ), patch.object(
      api.connection,
      "cursor",
      return_value=cursor_cm,
    ), patch(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table.cfg.get_host_name_ext",
      return_value="local",
    ):
      hosts = api._job_table_host_fqdns_last_7d()

    sqls = [call.args[0] for call in cursor.execute.call_args_list]
    unnest_sql = next(s for s in sqls if "unnest" in s.lower())
    assert "unnest(host_list)" in unnest_sql
    assert "end_time >=" in unnest_sql
    assert "btrim" not in unnest_sql.lower()
    assert "c101-001.local" in hosts
    assert "c101-002.local" in hosts

  def test_is_node_fqdn_rejects_none_assigned_placeholder(self):
    from hpcperfstats.site.lib.machine import api

    assert api._admin_monitor_is_node_fqdn("c101-001.local") is True
    assert api._admin_monitor_is_node_fqdn("None Assigned.local") is False
    assert api._admin_monitor_is_node_fqdn(
      "none assigned.tacc.utexas.edu"
    ) is False
    assert api._admin_monitor_is_node_fqdn("None Assigned") is False
    assert api._admin_monitor_is_node_fqdn("") is False

  def test_job_table_census_skips_none_assigned_placeholder(self):
    from hpcperfstats.site.lib.machine import api

    cursor = MagicMock()
    cursor.fetchall.return_value = [
      ("c101-001",),
      ("None Assigned",),
      ("None Assigned.local",),
    ]
    cursor_cm = MagicMock()
    cursor_cm.__enter__.return_value = cursor
    cursor_cm.__exit__.return_value = None

    with patch.object(api.connection, "vendor", "postgresql"), patch.object(
      api.transaction,
      "atomic",
      return_value=contextlib.nullcontext(),
    ), patch.object(
      api.connection,
      "cursor",
      return_value=cursor_cm,
    ), patch(
      "hpcperfstats.analysis.metrics.lib.gen.jid_table.cfg.get_host_name_ext",
      return_value="local",
    ):
      hosts = api._job_table_host_fqdns_last_7d()

    assert hosts == ["c101-001.local"]
    assert not any("none assigned" in h.casefold() for h in hosts)

  def test_section_skips_none_assigned_from_redis_and_census(self):
    from hpcperfstats.site.lib.machine import api

    redis_row = {
      "host": "None Assigned.example.com",
      "last_time": "2026-09-14T10:00:00+00:00",
      "age_bucket": "ok",
    }
    real_row = {
      "host": "seen.example.com",
      "last_time": "2026-09-14T10:00:00+00:00",
      "age_bucket": "ok",
    }
    with patch.object(
      api,
      "_get_recent_rabbitmq_host_stats",
      return_value=[redis_row, real_row],
    ), patch.object(
      api,
      "_job_table_host_fqdns_last_7d",
      return_value=["None Assigned.local", "silent.example.com"],
    ), patch.object(api, "cached_orm", side_effect=lambda _k, _t, fn: fn()):
      rows = api._get_rabbitmq_hosts_section()

    hosts = [r["host"] for r in rows]
    assert hosts == ["seen.example.com", "silent.example.com"]
    assert not any("none assigned" in h.casefold() for h in hosts)

  def test_section_refresh_deletes_job_hosts_cache_key(self):
    from hpcperfstats.site.lib.machine import api

    factory = RequestFactory()
    request = factory.get(
      "/api/admin_monitor/",
      {"section": "rabbitmq_hosts", "refresh": "1"},
    )
    request.session = {"is_staff": True}

    with patch.object(api, "_require_staff", return_value=None), patch.object(
      api,
      "_get_rabbitmq_hosts_section",
      return_value=[],
    ), patch.object(api, "cache") as mock_cache:
      response = api.admin_monitor(request)

    assert response.status_code == 200
    mock_cache.delete.assert_called_with(api.KEY_ADMIN_RMQ_JOB_HOSTS_7D)

  def test_full_refresh_deletes_job_hosts_cache_key(self):
    from hpcperfstats.site.lib.machine import api

    factory = RequestFactory()
    request = factory.get("/api/admin_monitor/", {"refresh": "1"})
    request.session = {"is_staff": True}

    with patch.object(api, "_require_staff", return_value=None), patch.object(
      api,
      "cached_orm",
      return_value=[],
    ), patch.object(
      api,
      "_get_rabbitmq_hosts_section",
      return_value=[],
    ), patch.object(api, "_get_cache_stats", return_value={}), patch.object(
      api, "_get_rabbitmq_stats", return_value={}
    ), patch.object(api, "_get_timescaledb_stats", return_value={}), patch.object(
      api, "_get_xalt_jid_coverage", return_value={}
    ), patch.object(api, "compute_telemetry_health", return_value={}), patch.object(
      api, "cache"
    ) as mock_cache:
      response = api.admin_monitor(request)

    assert response.status_code == 200
    deleted = {call.args[0] for call in mock_cache.delete.call_args_list}
    assert api.KEY_ADMIN_RMQ_JOB_HOSTS_7D in deleted

  def test_rabbitmq_hosts_section_includes_silent_rows(self):
    from hpcperfstats.site.lib.machine import api

    factory = RequestFactory()
    request = factory.get("/api/admin_monitor/", {"section": "rabbitmq_hosts"})
    request.session = {"is_staff": True}
    payload = [
      {
        "host": "silent.example.com",
        "last_time": None,
        "age_bucket": "gt_week",
      }
    ]
    with patch.object(api, "_require_staff", return_value=None), patch.object(
      api,
      "_get_rabbitmq_hosts_section",
      return_value=payload,
    ):
      response = api.admin_monitor(request)

    assert response.status_code == 200
    assert response.data == {"rabbitmq_host_stats": payload}
