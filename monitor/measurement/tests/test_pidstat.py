"""Pidstat parser coverage for CPU-only and combined -urd captures."""

from __future__ import annotations

from pathlib import Path

from lib.pidstat import parse_pidstat_text, summary_to_dict

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"


def test_stampede3_cpu_capture_peak_average_and_busy_spacing() -> None:
    text = (FIXTURES / "pidstat_cpu_stampede3.txt").read_text(encoding="utf-8")
    summary = parse_pidstat_text(text)
    assert summary.peak_cpu_pct == 3.0
    assert summary.average_cpu_pct == 0.19
    assert summary.average_usr_pct == 0.02
    assert summary.average_system_pct == 0.18
    assert summary.busy_cpu_pcts == [2.8, 2.9, 3.0, 2.9]
    assert summary.busy_time_labels == [
        "04:25:07 PM",
        "04:27:37 PM",
        "04:30:07 PM",
        "04:32:37 PM",
    ]
    assert summary.busy_spacing_seconds == [150, 150, 150]
    assert summary.median_busy_spacing_seconds == 150
    payload = summary_to_dict(summary)
    assert "rss" in payload["missing"]
    assert "disk_io_rates" in payload["missing"]


def test_urd_capture_exposes_rss_and_io_rates() -> None:
    text = (FIXTURES / "pidstat_urd.txt").read_text(encoding="utf-8")
    summary = parse_pidstat_text(text)
    assert summary.peak_cpu_pct == 2.6
    assert summary.peak_rss_kb == 18432
    assert summary.peak_kb_wr_s == 12.0
    assert summary.peak_kb_rd_s == 4.1
    payload = summary_to_dict(summary)
    assert payload["missing"] == []


def test_empty_pidstat_does_not_invent_numbers() -> None:
    summary = parse_pidstat_text("not a pidstat file\n")
    assert summary.samples == []
    assert summary.peak_cpu_pct is None
    assert summary.average_cpu_pct is None
