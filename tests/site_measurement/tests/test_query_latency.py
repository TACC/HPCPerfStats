"""Query latency percentiles and skip-if-empty behavior."""

from __future__ import annotations

from lib.query_latency import (
    ProbeResult,
    default_placeholders,
    percentile,
    run_scenarios,
    substitute_placeholders,
    summarize_repeats,
)


def test_percentile_interpolates() -> None:
    assert percentile([10.0], 95) == 10.0
    assert percentile([10.0, 20.0, 30.0, 40.0, 50.0], 50) == 30.0
    assert percentile([], 50) is None


def test_placeholders_do_not_invent_jid() -> None:
    mapping = default_placeholders(today=__import__("datetime").date(2026, 9, 9))
    assert mapping["JID"] == ""
    assert mapping["END_7D"] == "2026-09-03"
    filled = substitute_placeholders("/api/jobs/{{JID}}/", mapping)
    assert filled == "/api/jobs//"


def test_skip_if_empty_marks_not_measured() -> None:
    scenarios = [
        {
            "name": "job_detail",
            "path": "/api/jobs/{{JID}}/",
            "query": {"id": ""},
            "skip_if_empty": ["id"],
        }
    ]
    report = run_scenarios(
        scenarios,
        base_url="https://example.test/api/",
        api_key=None,
        repeats=3,
        warmup=0,
        timeout_s=1,
        verify_tls=True,
        get_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError("should skip")),
    )
    row = report["scenarios"][0]
    assert row["skipped"] is True
    assert row["not_measured"] is True


def test_summarize_repeats_ignores_errors_for_percentiles() -> None:
    probes = [
        ProbeResult("s", "u", 200, 10.0, 1),
        ProbeResult("s", "u", 200, 30.0, 1),
        ProbeResult("s", "u", 500, 999.0, 0, error="HTTP 500"),
    ]
    stats = summarize_repeats("s", probes)
    assert stats.ok == 2
    assert stats.p50_ms == 20.0
    assert stats.max_ms == 30.0
