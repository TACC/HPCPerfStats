"""Ticket CSV summarizer ignores example rows and empty minutes."""

from __future__ import annotations

import csv
from pathlib import Path

from bin.summarize_ticket_time import _minutes


def test_example_and_empty_minutes_are_skipped(tmp_path: Path) -> None:
    path = tmp_path / "t.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "ticket_id",
                "had_hps_packet",
                "review_minutes",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {"ticket_id": "example-1", "had_hps_packet": "yes", "review_minutes": "5"}
        )
        writer.writerow(
            {"ticket_id": "real-1", "had_hps_packet": "yes", "review_minutes": "12"}
        )
        writer.writerow(
            {"ticket_id": "real-2", "had_hps_packet": "yes", "review_minutes": ""}
        )
        writer.writerow(
            {"ticket_id": "real-3", "had_hps_packet": "no", "review_minutes": "40"}
        )
    with path.open(encoding="utf-8", newline="") as handle:
        rows = [
            r
            for r in csv.DictReader(handle)
            if not (r.get("ticket_id") or "").startswith("example")
        ]
    assert _minutes(rows, True) == [12.0]
    assert _minutes(rows, False) == [40.0]
