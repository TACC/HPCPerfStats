"""Unit tests for admin_monitor_telemetry_health payload and timeout helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

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


def test_compute_telemetry_health_returns_cached():
    cached = {"window_hours": 12, "timed_out": False, "all_zero_events": []}
    with patch.object(th.cache, "get", return_value=cached):
        assert th.compute_telemetry_health() is cached


def test_expected_core_types_include_host_cpu_mem():
    cores = th.expected_core_types()
    assert "host_cpu" in cores
    assert "host_mem" in cores
