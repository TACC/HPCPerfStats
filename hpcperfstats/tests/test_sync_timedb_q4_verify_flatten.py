"""Q4: flattened day-raw verify-complete log helper (not H18 kick diamond)."""

from __future__ import annotations

import inspect

from hpcperfstats.dbload.lib import sync_timedb_day_raw_removal as drr
from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo


def test_log_day_raw_verify_complete_tokens():
    logs: list[str] = []
    drr._log_day_raw_verify_complete(
        lambda msg, **_k: logs.append(msg),
        label="",
        day_iso="2026-01-02",
        verified=3,
        skipped=1,
    )
    drr._log_day_raw_verify_complete(
        lambda msg, **_k: logs.append(msg),
        label="pre-seal",
        day_iso="2026-01-02",
        verified=4,
        skipped=2,
    )
    assert logs[0] == (
        "Day raw removal verify complete day=2026-01-02 verified=3 skipped=1"
    )
    assert logs[1] == (
        "Day raw removal pre-seal verify complete day=2026-01-02 "
        "verified=4 skipped=2"
    )


def test_log_day_raw_verify_complete_noop_without_logger():
    drr._log_day_raw_verify_complete(
        None,
        label="",
        day_iso="2026-01-02",
        verified=0,
        skipped=0,
    )


def test_day_close_keeps_yield_assignment_and_two_pre_seal_passes():
    source = inspect.getsource(qo._run_day_close_job)
    assert "early = _maybe_yield_disk_remaining_raw()" in source
    assert source.count("_run_pre_seal_verify()") >= 2
