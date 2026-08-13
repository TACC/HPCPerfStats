"""Unit tests for admin_monitor_telemetry_health payload and timeout helpers."""

from __future__ import annotations

import contextlib
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from hpcperfstats.site.lib.machine import admin_monitor_telemetry_health as th

pytestmark = pytest.mark.machine_unit_mock


def test_build_payload_reports_all_zero_and_missing_cores():
    payload = th.build_telemetry_health_payload(
        [
            {
                "type": "host_cpu",
                "event": "user",
                "row_count": 12,
                "has_nonzero": False,
            },
            {
                "type": "host_mem",
                "event": "used",
                "row_count": 12,
                "has_nonzero": True,
            },
        ],
        computed_at=datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc),
        expected_core_types=("host_cpu", "host_mem", "host_block"),
    )
    assert payload["window_hours"] == th.WINDOW_HOURS
    assert payload["timed_out"] is False
    assert payload["all_zero_events"] == [
        {"type": "host_cpu", "event": "user", "row_count": 12}
    ]
    assert payload["missing_core_types"] == ["host_block"]
    assert payload["ok_summary"]["nonzero_type_event_pairs"] == 1
    assert payload["truncated"] is False


def test_build_payload_excludes_error_names_are_caller_filtered():
    """Caller SQL excludes *error*; payload still reports only supplied rows."""
    payload = th.build_telemetry_health_payload(
        [
            {
                "type": "host_ib",
                "event": "port_xmit_data",
                "row_count": 5,
                "has_nonzero": False,
            }
        ],
        computed_at=datetime(2024, 6, 1, tzinfo=timezone.utc),
        expected_core_types=("host_cpu",),
    )
    assert payload["all_zero_events"][0]["event"] == "port_xmit_data"
    assert "host_cpu" in payload["missing_core_types"]


def test_build_payload_non_zero_value_or_arc_clears_pair():
    payload = th.build_telemetry_health_payload(
        [
            {
                "type": "host_cpu",
                "event": "user",
                "row_count": 3,
                "has_nonzero": True,
            }
        ],
        computed_at=datetime(2024, 6, 1, tzinfo=timezone.utc),
        expected_core_types=("host_cpu",),
    )
    assert payload["all_zero_events"] == []
    assert payload["missing_core_types"] == []
    assert payload["ok_summary"]["nonzero_type_event_pairs"] == 1


def test_build_payload_empty_window_lists_missing_cores_not_healthy():
    payload = th.build_telemetry_health_payload(
        [],
        computed_at=datetime(2024, 6, 1, tzinfo=timezone.utc),
        expected_core_types=("host_cpu", "host_mem"),
    )
    assert payload["all_zero_events"] == []
    assert payload["missing_core_types"] == ["host_cpu", "host_mem"]
    assert payload["ok_summary"]["nonzero_type_event_pairs"] == 0
    assert "No all-zero or missing-core anomalies" not in payload["ok_summary"][
        "scanned_note"
    ]


def test_build_payload_timeout_soft_fail_empty_lists():
    payload = th.build_telemetry_health_payload(
        [
            {
                "type": "host_cpu",
                "event": "user",
                "row_count": 99,
                "has_nonzero": False,
            }
        ],
        computed_at=datetime(2024, 6, 1, tzinfo=timezone.utc),
        timed_out=True,
        error="timed out",
    )
    assert payload["timed_out"] is True
    assert payload["all_zero_events"] == []
    assert payload["missing_core_types"] == []
    assert payload["error"] == "timed out"


def test_build_payload_truncates_all_zero_list():
    rows = [
        {
            "type": f"t{i}",
            "event": "e",
            "row_count": 1,
            "has_nonzero": False,
        }
        for i in range(5)
    ]
    payload = th.build_telemetry_health_payload(
        rows,
        computed_at=datetime(2024, 6, 1, tzinfo=timezone.utc),
        expected_core_types=(),
        all_zero_limit=3,
    )
    assert len(payload["all_zero_events"]) == 3
    assert payload["truncated"] is True


def test_is_statement_timeout_detects_pg_message():
    assert th._is_statement_timeout(
        Exception("canceling statement due to statement timeout")
    )
    assert not th._is_statement_timeout(Exception("syntax error"))


def test_compute_telemetry_health_soft_fails_on_timeout():
    with patch.object(th.cache, "get", return_value=None), patch.object(
        th.cache, "set"
    ), patch.object(th.connection, "vendor", "postgresql"), patch.object(
        th,
        "_fetch_type_event_aggregates",
        side_effect=Exception("canceling statement due to statement timeout"),
    ):
        payload = th.compute_telemetry_health(force_refresh=True)
    assert payload["timed_out"] is True
    assert payload["all_zero_events"] == []
    assert "timed out" in (payload.get("error") or "").lower()


def test_compute_telemetry_health_timeout_logs_without_traceback():
    with patch.object(th.cache, "get", return_value=None), patch.object(
        th.cache, "set"
    ), patch.object(th.connection, "vendor", "postgresql"), patch.object(
        th,
        "_fetch_type_event_aggregates",
        side_effect=Exception("canceling statement due to statement timeout"),
    ), patch.object(th.logger, "warning") as warn:
        th.compute_telemetry_health(force_refresh=True)
    assert warn.called
    _args, kwargs = warn.call_args
    assert kwargs.get("exc_info") in (None, False)


def test_compute_telemetry_health_redis_empty_is_incomplete():
    marker = "UNIQUE_REDIS_EMPTY_EXC_DETAIL_xyz"
    with patch.object(th.cache, "get", return_value=None), patch.object(
        th.cache, "set"
    ), patch.object(th.connection, "vendor", "postgresql"), patch.object(
        th,
        "_fetch_type_event_aggregates",
        side_effect=th.EmptyRecentHostInventory(marker),
    ) as fetch:
        payload = th.compute_telemetry_health(force_refresh=True)
    assert payload["timed_out"] is True
    assert payload["all_zero_events"] == []
    assert payload["missing_core_types"] == []
    err = payload.get("error") or ""
    assert "recent_host" in err.lower()
    assert marker not in err
    assert payload["ok_summary"]["hosts_sampled"] == 0
    assert fetch.called


def test_compute_telemetry_health_generic_failure_omits_exc_text():
    marker = "UNIQUE_TELEMETRY_FAILURE_DETAIL_abc"
    with patch.object(th.cache, "get", return_value=None), patch.object(
        th.cache, "set"
    ), patch.object(th.connection, "vendor", "postgresql"), patch.object(
        th,
        "_fetch_type_event_aggregates",
        side_effect=RuntimeError(marker),
    ):
        payload = th.compute_telemetry_health(force_refresh=True)
    assert payload["timed_out"] is True
    err = payload.get("error") or ""
    assert "failed" in err.lower()
    assert "incomplete" in err.lower()
    assert marker not in err


def test_compute_telemetry_health_returns_cached():
    cached = {"window_hours": 12, "timed_out": False, "all_zero_events": []}
    with patch.object(th.cache, "get", return_value=cached):
        assert th.compute_telemetry_health() is cached


def test_merge_type_event_batches_sums_counts_and_ors_nonzero():
    merged = th._merge_type_event_batches(
        [
            [
                {
                    "type": "host_cpu",
                    "event": "user",
                    "row_count": 2,
                    "has_nonzero": False,
                },
                {
                    "type": "host_mem",
                    "event": "used",
                    "row_count": 1,
                    "has_nonzero": True,
                },
            ],
            [
                {
                    "type": "host_cpu",
                    "event": "user",
                    "row_count": 3,
                    "has_nonzero": True,
                },
            ],
        ]
    )
    by_key = {(r["type"], r["event"]): r for r in merged}
    assert by_key[("host_cpu", "user")]["row_count"] == 5
    assert by_key[("host_cpu", "user")]["has_nonzero"] is True
    assert by_key[("host_mem", "used")]["row_count"] == 1
    assert by_key[("host_mem", "used")]["has_nonzero"] is True


def test_fetch_aggregates_scopes_sql_to_redis_hosts():
    exec_log: list[tuple[str, object]] = []

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *args, **kwargs):
            return False

        def execute(self, sql, params=None):
            exec_log.append((sql, params))
            self._rows = []
            if "GROUP BY" in sql:
                self._rows = [("host_cpu", "user", 4, True)]

        def fetchall(self):
            return getattr(self, "_rows", [])

    fake_conn = MagicMock()
    fake_conn.vendor = "postgresql"
    fake_conn.alias = "default"
    fake_conn.cursor = lambda: FakeCursor()

    hosts = [f"c{i:03d}.example.com" for i in range(5)]
    with patch.object(th, "transaction") as txn, patch.object(
        th, "connection", fake_conn
    ), patch.object(th, "list_recent_host_fqdns_from_redis", return_value=hosts):
        txn.atomic.return_value = contextlib.nullcontext()
        rows, sampled = th._fetch_type_event_aggregates(
            window_hours=12,
            host_sample_limit=16,
            host_batch_size=8,
        )
    assert sampled == 5
    assert rows[0]["type"] == "host_cpu"
    group_sql = [e for e in exec_log if e[0] and "GROUP BY" in e[0]]
    assert group_sql
    assert all("host = ANY" in e[0] for e in group_sql)
    assert all(
        "FROM host_data\n        WHERE time >=" not in e[0]
        and "WHERE host = ANY" in e[0]
        for e in group_sql
    )


def test_fetch_aggregates_caps_host_sample():
    exec_log: list[tuple[str, object]] = []

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *args, **kwargs):
            return False

        def execute(self, sql, params=None):
            exec_log.append((sql, params))
            self._rows = []

        def fetchall(self):
            return []

    fake_conn = MagicMock()
    fake_conn.vendor = "postgresql"
    fake_conn.alias = "default"
    fake_conn.cursor = lambda: FakeCursor()

    hosts = [f"h{i:03d}.example.com" for i in range(200)]
    with patch.object(th, "transaction") as txn, patch.object(
        th, "connection", fake_conn
    ), patch.object(th, "list_recent_host_fqdns_from_redis", return_value=hosts):
        txn.atomic.return_value = contextlib.nullcontext()
        _rows, sampled = th._fetch_type_event_aggregates(
            window_hours=12,
            host_sample_limit=th.HOST_SAMPLE_LIMIT,
            host_batch_size=th.HOST_BATCH_SIZE,
        )
    assert sampled == th.HOST_SAMPLE_LIMIT
    group_params = [
        e[1] for e in exec_log if e[0] and "GROUP BY" in e[0] and e[1] is not None
    ]
    hosts_sent = []
    for params in group_params:
        hosts_sent.extend(params[0])
    assert len(hosts_sent) == th.HOST_SAMPLE_LIMIT
    assert len(group_params) == 2  # 16 hosts / batch 8


def test_fetch_aggregates_merges_batches():
    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *args, **kwargs):
            return False

        def execute(self, sql, params=None):
            self._params = params
            self._rows = []
            if params and "GROUP BY" in sql:
                chunk = params[0]
                if chunk and chunk[0].startswith("a"):
                    self._rows = [("host_cpu", "user", 2, False)]
                else:
                    self._rows = [("host_cpu", "user", 3, True)]

        def fetchall(self):
            return getattr(self, "_rows", [])

    fake_conn = MagicMock()
    fake_conn.vendor = "postgresql"
    fake_conn.alias = "default"
    fake_conn.cursor = lambda: FakeCursor()

    hosts = [f"a{i}.example.com" for i in range(8)] + [
        f"b{i}.example.com" for i in range(8)
    ]
    with patch.object(th, "transaction") as txn, patch.object(
        th, "connection", fake_conn
    ), patch.object(th, "list_recent_host_fqdns_from_redis", return_value=hosts):
        txn.atomic.return_value = contextlib.nullcontext()
        rows, sampled = th._fetch_type_event_aggregates(
            window_hours=12,
            host_sample_limit=16,
            host_batch_size=8,
        )
    assert sampled == 16
    assert len(rows) == 1
    assert rows[0]["row_count"] == 5
    assert rows[0]["has_nonzero"] is True


def test_fetch_aggregates_raises_on_empty_redis():
    with patch.object(th, "list_recent_host_fqdns_from_redis", return_value=[]):
        with pytest.raises(th.EmptyRecentHostInventory):
            th._fetch_type_event_aggregates(window_hours=12)


def test_build_payload_includes_hosts_sampled():
    payload = th.build_telemetry_health_payload(
        [
            {
                "type": "host_cpu",
                "event": "user",
                "row_count": 1,
                "has_nonzero": True,
            }
        ],
        computed_at=datetime(2024, 6, 1, tzinfo=timezone.utc),
        expected_core_types=("host_cpu",),
        hosts_sampled=16,
    )
    assert payload["ok_summary"]["hosts_sampled"] == 16
    assert "16" in payload["ok_summary"]["scanned_note"]


def test_expected_core_types_include_host_cpu_mem():
    cores = th.expected_core_types()
    assert "host_cpu" in cores
    assert "host_mem" in cores
