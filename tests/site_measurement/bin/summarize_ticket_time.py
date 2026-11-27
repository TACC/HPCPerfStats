#!/usr/bin/env python3
"""Summarize ticket_time.csv medians; empty minutes stay unmeasured."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from pathlib import Path


def _minutes(rows: list[dict[str, str]], want_yes: bool) -> list[float]:
    out: list[float] = []
    for row in rows:
        flag = (row.get("had_hps_packet") or "").strip().lower()
        is_yes = flag in {"yes", "y", "true", "1"}
        if is_yes != want_yes:
            continue
        raw = (row.get("review_minutes") or "").strip()
        if not raw:
            continue
        out.append(float(raw))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    with Path(args.csv).open(encoding="utf-8", newline="") as handle:
        rows = [r for r in csv.DictReader(handle) if not (r.get("ticket_id") or "").startswith("example")]
    with_hps = _minutes(rows, True)
    without = _minutes(rows, False)
    payload = {
        "n_with": len(with_hps),
        "n_without": len(without),
        "median_minutes_with": statistics.median(with_hps) if with_hps else None,
        "median_minutes_without": statistics.median(without) if without else None,
        "caveat": "Staff-reported minutes, not a controlled study, unless times were recorded as the ticket was worked.",
    }
    Path(args.out).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
