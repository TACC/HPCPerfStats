"""Skip job-detail probes when JID was not substituted."""

from __future__ import annotations

from lib.query_latency import run_scenarios


def test_missing_jid_path_is_not_measured() -> None:
    scenarios = [
        {
            "name": "job_detail",
            "path": "/jobs//",
            "query": {"light": "0"},
        }
    ]
    report = run_scenarios(
        scenarios,
        base_url="https://example.test/api/",
        api_key=None,
        repeats=1,
        warmup=0,
        timeout_s=1,
        verify_tls=True,
        get_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError("should skip")),
    )
    assert report["scenarios"][0]["not_measured"] is True
    assert "job id" in report["scenarios"][0]["reason"]
