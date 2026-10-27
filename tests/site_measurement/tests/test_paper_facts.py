"""Paper fact ledger never fills gaps with invented numbers."""

from __future__ import annotations

import json
from pathlib import Path

from lib.paper_facts import NOT_MEASURED, SLOTS, merge_results_dir, render_markdown


def test_empty_results_dir_is_all_not_measured(tmp_path: Path) -> None:
    facts = merge_results_dir(tmp_path)
    assert facts["_measured_count"] == 0
    assert facts["_not_measured_count"] == len(SLOTS)
    assert all(facts[slot] == NOT_MEASURED for slot in SLOTS)
    markdown = render_markdown(facts)
    assert "NOT_MEASURED" in markdown
    assert "Do not invent numbers" in markdown


def test_merge_reads_overhead_and_query_json(tmp_path: Path) -> None:
    (tmp_path / "overhead.json").write_text(
        json.dumps(
            {
                "pidstat": {"peak_cpu_pct": 3.0, "average_cpu_pct": 0.19},
                "proc": {
                    "rss_peak_kib": 19000,
                    "uss_peak_kib": 12000,
                    "io_delta": {"read_bytes": 4096, "write_bytes": 8192},
                    "net_delta": {"total_tx_bytes": 6000, "total_rx_bytes": 3000},
                },
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "query_latency.json").write_text(
        json.dumps(
            {
                "scenarios": [
                    {"name": "extended_search_7d", "p50_ms": 123.4, "not_measured": False},
                    {"name": "job_detail", "p50_ms": None, "not_measured": True},
                ]
            }
        ),
        encoding="utf-8",
    )
    facts = merge_results_dir(tmp_path)
    assert facts["overhead.cpu_peak_pct"] == 3.0
    assert facts["overhead.uss_peak_kib"] == 12000
    assert facts["query.extended_search_7d_p50_ms"] == 123.4
    assert facts["query.job_detail_p50_ms"] == NOT_MEASURED
