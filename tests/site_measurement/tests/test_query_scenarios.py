"""Extended Search / Job Detail query scenario placeholders."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from lib.query_latency import default_placeholders, load_scenarios

ROOT = Path(__file__).resolve().parents[1]


def test_query_scenarios_substitute_dates_and_skip_empty_queue() -> None:
    placeholders = default_placeholders(
        jid="",
        queue="",
        today=date(2026, 9, 9),
    )
    scenarios = load_scenarios(
        str(ROOT / "scenarios" / "query_latency.json"), placeholders
    )
    names = [s["name"] for s in scenarios]
    assert "extended_search_7d" in names
    seven = next(s for s in scenarios if s["name"] == "extended_search_7d")
    assert seven["query"]["end_time__gte"] == "2026-09-03"
    detail = next(s for s in scenarios if s["name"] == "job_detail")
    assert detail["path"] == "/jobs//"
