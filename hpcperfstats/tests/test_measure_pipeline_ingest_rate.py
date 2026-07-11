"""Tests for scripts/measure_pipeline_ingest_rate.py."""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "scripts" / "measure_pipeline_ingest_rate.py"


def _load_module():
    name = "measure_pipeline_ingest_rate"
    spec = importlib.util.spec_from_file_location(name, _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def mod():
    return _load_module()


def _ts(minutes_from_start: int, second: int = 0) -> str:
    hour = 10 + minutes_from_start // 60
    minute = minutes_from_start % 60
    return "2026-07-08T%02d:%02d:%02dZ hpcperfstats_pipeline_1 | " % (hour, minute, second)


FIXTURE_WINNING = [
    _ts(0) + "Messages consumed in the last 10 minutes: 100; messages waiting to be consumed: 0; current file unlinks (last 10 minutes): 5",
    _ts(0) + "sync_timedb: pending rescan done pending=1000 elapsed_s=1.0",
]
FIXTURE_WINNING += [
    _ts(10 + i) + (
        "ingest file path=/arch/host/%d outcome=ingested elapsed_s=1.0 "
        "ingest_ok=yes archive=yes db_skip=no size_bytes=1000 stats_rows=10"
    ) % (100 + i)
    for i in range(12)
]
FIXTURE_WINNING += [
    _ts(50) + "Pending stats file list truncated pending=900 max=2000",
    _ts(50) + "Throughput telemetry: active_workers=4 backlog=900 chunk_size=1000 bulk_create_batch=10000",
    _ts(55) + "sync_timedb: chunk ingest summary chunk=0 ingested_this_chunk=12 checkpoint_immediate_n=8 archive_deferred_n=4",
    _ts(56) + "sync_timedb: checkpoint deferred archive finalize count=4",
    _ts(60) + "Messages consumed in the last 10 minutes: 100; messages waiting to be consumed: 0; current file unlinks (last 10 minutes): 5",
]


FIXTURE_LOSING = [
    _ts(0) + "sync_timedb: pending rescan done pending=500 elapsed_s=1.0",
    _ts(10) + "Messages consumed in the last 10 minutes: 50; messages waiting to be consumed: 0; current file unlinks (last 10 minutes): 50",
    _ts(70) + "ingest file path=/arch/host/200 outcome=ingested elapsed_s=1.0 ingest_ok=yes archive=yes db_skip=no size_bytes=1000 stats_rows=10",
    _ts(71) + "ingest file path=/arch/host/201 outcome=ingested elapsed_s=1.0 ingest_ok=yes archive=yes db_skip=no size_bytes=1000 stats_rows=10",
    _ts(80) + "Pending stats file list truncated pending=550 max=2000",
    _ts(80) + "Throughput telemetry: active_workers=4 backlog=550 chunk_size=1000 bulk_create_batch=10000",
]


def test_parse_leading_rfc3339_timestamp(mod):
    line = (
        "2026-07-07T08:38:46.381101000-05:00 [update_metrics:thread:readiness-producer] "
        "metrics_deferred_coverage jid=809643 start_ok=False"
    )
    ts, body = mod._strip_log_prefix(line)
    assert ts is not None
    assert ts.year == 2026 and ts.month == 7 and ts.day == 7
    assert "[update_metrics:thread:readiness-producer]" in body


def test_parse_nanosecond_timestamp_python39_compatible(mod):
    ts = mod._parse_log_timestamp("2026-07-07T08:38:46.381101000-05:00")
    assert ts is not None
    assert ts.microsecond == 381101


def test_parse_container_pipe_timestamp(mod):
    line = (
        "hpcperfstats_pipeline_1  | 2026-07-07T08:38:46.381101000-05:00 "
        "[sync_timedb:main] sync_timedb: pending rescan done pending=1000 elapsed_s=1.0"
    )
    ts, body = mod._strip_log_prefix(line)
    assert ts is not None
    assert "pending rescan done pending=1000" in body


def test_parse_container_first_timestamp(mod):
    line = (
        "hpcperfstats_pipeline_1 2026-07-07T08:38:51.004115000-05:00 "
        "[sync_timedb:main] sync_timedb: pending rescan done pending=1000 elapsed_s=1.0"
    )
    ts, body = mod._strip_log_prefix(line)
    assert ts is not None
    assert "pending rescan done pending=1000" in body
    metrics = mod.parse_log_lines([line])
    assert metrics.backlog_rescan_samples[0][1] == 1000


def test_parse_full_ingest_and_listend(mod):
    metrics = mod.parse_log_lines(FIXTURE_WINNING)
    assert metrics.listend_unlink_sum == 10
    assert metrics.full_ingest_count == 12
    assert metrics.archive_immediate_sum == 8
    assert metrics.archive_finalize_sum == 4


def test_winning_verdict_and_eta(mod):
    outcomes = mod.analyze_lines(FIXTURE_WINNING)
    assert outcomes["verdict_full_ingest"] == "WINNING"
    assert float(outcomes["ratio_listend_over_full_ingest"]) < 1.0
    assert outcomes["backlog_at_start"] == "1000"
    assert outcomes["backlog_latest"] == "900"
    assert outcomes["backlog_drained_since_start"] == "100"
    assert outcomes["ingest_queue_depth_at_start"] == "900"
    assert outcomes["ingest_queue_depth_latest"] == "900"
    assert outcomes["eta_hours_full_ingest"] != "N/A"
    assert float(outcomes["eta_hours_full_ingest"]) > 0
    assert outcomes["estimated_finish_local"] != "N/A"
    assert outcomes["estimated_finish_basis"] in (
        "eta_hours_empirical",
        "eta_hours_full_ingest",
        "eta_hours_archive_done",
    )
    # Local stamp: YYYY-MM-DD HH:MM:SS ±HHMM
    assert re.match(
        r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [+-]\d{4}$",
        outcomes["estimated_finish_local"],
    )


def test_losing_verdict_and_eta_na(mod):
    outcomes = mod.analyze_lines(FIXTURE_LOSING)
    assert outcomes["verdict_full_ingest"] == "LOSING"
    assert float(outcomes["ratio_listend_over_full_ingest"]) > 1.0
    assert outcomes["eta_hours_full_ingest"] == "N/A"
    assert outcomes["estimated_finish_local"] == "N/A"
    assert outcomes["estimated_finish_basis"] == "N/A"


def test_db_skip_not_counted_as_full_ingest(mod):
    lines = [
        _ts(0) + "ingest file path=/arch/host/1 outcome=db_skip elapsed_s=0.1 ingest_ok=yes archive=member_exists db_skip=head_tail size_bytes=1000",
        _ts(61) + "ingest file path=/arch/host/2 outcome=ingested elapsed_s=1.0 ingest_ok=yes archive=yes db_skip=no size_bytes=1000 stats_rows=10",
    ]
    metrics = mod.parse_log_lines(lines)
    assert metrics.full_ingest_count == 1


def test_mixed_rescan_and_throughput_does_not_invent_drain(mod):
    """Prod signature: uncapped rescan + capped throughput must not invent drain."""
    lines = [
        _ts(0) + "sync_timedb: pending rescan done pending=286501 elapsed_s=63.0",
        _ts(10) + "Messages consumed in the last 10 minutes: 0; messages waiting to be consumed: 0; current file unlinks (last 10 minutes): 1",
        _ts(30) + "Throughput telemetry: active_workers=24 backlog=2000 chunk_size=1000 bulk_create_batch=10000",
        _ts(61) + "ingest file path=/arch/host/1 outcome=ingested elapsed_s=1.0 ingest_ok=yes archive=yes db_skip=no size_bytes=1000 stats_rows=10",
    ]
    outcomes = mod.analyze_lines(lines)
    assert outcomes["backlog_at_start"] == "286501"
    assert outcomes["backlog_latest"] == "286501"
    assert outcomes["backlog_drained_since_start"] == "N/A"
    assert outcomes["pct_complete_since_start"] == "N/A"
    assert outcomes["empirical_drain_per_min"] == "0.0000"
    assert outcomes["eta_hours_empirical"] == "N/A"
    assert outcomes["ingest_queue_depth_latest"] == "2000"
    assert outcomes["ingest_queue_depth_at_start"] == "2000"
    drained_fake = 286501 - 2000
    assert outcomes["backlog_drained_since_start"] != str(drained_fake)
    assert outcomes["backlog_latest"] != "2000"


def test_truncate_line_is_disk_pending_sample(mod):
    lines = [
        _ts(0) + "sync_timedb: pending rescan done pending=286501 elapsed_s=63.0",
        _ts(10) + "Messages consumed in the last 10 minutes: 0; messages waiting to be consumed: 0; current file unlinks (last 10 minutes): 1",
        _ts(40) + "Pending stats file list truncated pending=285607 max=2000",
        _ts(41) + "Throughput telemetry: active_workers=24 backlog=2000 chunk_size=1000 bulk_create_batch=10000",
        _ts(61) + "ingest file path=/arch/host/1 outcome=ingested elapsed_s=1.0 ingest_ok=yes archive=yes db_skip=no size_bytes=1000 stats_rows=10",
    ]
    outcomes = mod.analyze_lines(lines)
    assert outcomes["backlog_at_start"] == "286501"
    assert outcomes["backlog_latest"] == "285607"
    assert outcomes["backlog_drained_since_start"] == "894"
    assert outcomes["ingest_queue_depth_latest"] == "2000"
    assert float(outcomes["empirical_drain_per_min"]) > 0
    assert outcomes["eta_hours_empirical"] != "N/A"


def test_boot_only_skips_pre_boot_lines(mod):
    lines = [
        _ts(0) + "sync_timedb: pending rescan done pending=9999 elapsed_s=1.0",
        _ts(5) + "startup ingest gate cleared; ingest may begin",
        _ts(10) + "sync_timedb: pending rescan done pending=100 elapsed_s=1.0",
        _ts(10) + "Messages consumed in the last 10 minutes: 0; messages waiting to be consumed: 0; current file unlinks (last 10 minutes): 1",
        _ts(40) + "Pending stats file list truncated pending=80 max=2000",
        _ts(70) + "Throughput telemetry: active_workers=4 backlog=80 chunk_size=1000 bulk_create_batch=10000",
        _ts(71) + "ingest file path=/arch/host/1 outcome=ingested elapsed_s=1.0 ingest_ok=yes archive=yes db_skip=no size_bytes=1000 stats_rows=10",
        _ts(72) + "ingest file path=/arch/host/2 outcome=ingested elapsed_s=1.0 ingest_ok=yes archive=yes db_skip=no size_bytes=1000 stats_rows=10",
    ]
    outcomes = mod.analyze_lines(lines, boot_only=True)
    assert outcomes["backlog_at_start"] == "100"
    assert outcomes["backlog_latest"] == "80"
    assert outcomes["ingest_queue_depth_latest"] == "80"


def test_since_minutes_window(mod):
    lines = [
        _ts(0) + "sync_timedb: pending rescan done pending=1000 elapsed_s=1.0",
    ]
    lines += [
        _ts(130 + i)
        + (
            "ingest file path=/arch/host/%d outcome=ingested elapsed_s=1.0 "
            "ingest_ok=yes archive=yes db_skip=no size_bytes=1000 stats_rows=10"
        )
        % (300 + i)
        for i in range(12)
    ]
    lines.append(
        _ts(200)
        + "Messages consumed in the last 10 minutes: 0; messages waiting to be consumed: 0; current file unlinks (last 10 minutes): 1",
    )
    outcomes = mod.analyze_lines(lines, since_minutes=90)
    assert float(outcomes["window_minutes"]) == 90.0
    assert float(outcomes["sync_full_ingest_per_min"]) == pytest.approx(12 / 90.0, rel=1e-3)


def test_empty_log_raises(mod):
    with pytest.raises(ValueError, match="insufficient log window"):
        mod.analyze_lines([])


def test_no_timestamps_fallback_window(mod):
    lines = [
        "Messages consumed in the last 10 minutes: 1; messages waiting to be consumed: 0; current file unlinks (last 10 minutes): 5",
        "Messages consumed in the last 10 minutes: 1; messages waiting to be consumed: 0; current file unlinks (last 10 minutes): 5",
    ]
    outcomes = mod.analyze_lines(lines)
    assert float(outcomes["window_minutes"]) == 20.0
    assert float(outcomes["listend_closed_per_min"]) == 0.5


def test_stdout_only_key_count(mod):
    outcomes = mod.analyze_lines(FIXTURE_WINNING)
    text = mod.format_stdout(outcomes)
    lines = text.splitlines()
    assert all("=" in line for line in lines)
    assert lines[0].startswith("window_minutes=")
    assert len(lines) == 25
    assert "ingest_queue_depth_latest=" in text
    assert "ingest_queue_depth_at_start=" in text
    assert "estimated_finish_local=" in text
    assert "estimated_finish_basis=" in text


def test_cli_script_runs_from_repo(tmp_path):
    log_path = tmp_path / "pipeline.log"
    log_path.write_text("\n".join(FIXTURE_WINNING) + "\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(_SCRIPT), "--log-file", str(log_path)],
        cwd=str(_REPO),
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "verdict_full_ingest=WINNING" in proc.stdout
    assert "ERROR:" not in proc.stdout
