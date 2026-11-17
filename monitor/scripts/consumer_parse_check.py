#!/usr/bin/env python3
"""Feed captured shm payloads through the consumer parser and check the sentinel contract.

Run from the repo ``.venv`` (the consumer package ``hpcperfstats`` is imported read-only from the
checkout). The schema payload and the sample payloads, in capture order, form one archive stream.
Checks: every ``-n`` token parses as value ``-n`` with no delta (a sentinel never enters a rate),
no negative delta, rows parse at all, and the Codon parse matches the Python parse when the Codon
extension is built (SKIP otherwise). Exit 0 PASS, 1 FAIL, 77 when the consumer cannot be imported.

Usage: consumer_parse_check.py --schema FILE SAMPLE [SAMPLE ...] [--report FILE]
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

CHECKOUT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CHECKOUT / "monitor" / "scripts"))

from lib.row_validate import token_sentinel_code  # noqa: E402


def _sentinel_token_count(stream: str) -> int:
    count = 0
    for line in stream.splitlines():
        if line[:1].isalpha():
            count += sum(1 for tok in line.split()[2:] if token_sentinel_code(tok) is not None)
    return count


def _frame(parsing, finished):
    stats_df, _ = parsing.build_stats_dataframes(finished[0], finished[1])
    return stats_df


def check_stream(stream: str) -> tuple[list[str], list[str]]:
    """Notes and errors for one archive stream (schema payload followed by samples)."""
    from hpcperfstats.dbload.lib import sync_timedb_parsing as parsing
    from hpcperfstats.dbload.lib.sync_timedb_codon_bridge import try_codon_parse_collapse

    notes: list[str] = []
    errors: list[str] = []
    lines = stream.splitlines(keepends=True)
    parser = parsing.IncrementalStatsParser(0, None)
    parser.feed_lines(lines)
    py_df = _frame(parsing, parser.finish())
    if py_df.empty:
        return notes, ["FAIL consumer_parse: no rows parsed"]
    deltas = parsing._apply_counter_deltas(py_df.copy())
    sentinel_rows = deltas[deltas["value"] < 0]
    want = _sentinel_token_count(stream)
    if len(sentinel_rows) != want:
        errors.append(f"FAIL consumer_parse: {len(sentinel_rows)} sentinel rows for {want} tokens")
    if sentinel_rows["delta"].notna().any():
        errors.append("FAIL consumer_parse: a sentinel row carries a delta")
    negative = deltas[deltas["delta"] < 0]
    for row in negative.head(5).itertuples():
        errors.append(f"FAIL consumer_parse: negative delta {row.type}/{row.dev} {row.event} {row.delta}")
    if not errors:
        notes.append(f"PASS consumer_parse python ({len(py_df)} rows, {want} sentinel)")

    codon = try_codon_parse_collapse(list(lines), exclude=None, start_idx=0)
    if codon is None:
        notes.append("SKIP consumer_parse codon parity (extension not built)")
    else:
        cols = ["time", "host", "type", "dev", "event", "value"]
        a = py_df[cols].sort_values(cols).reset_index(drop=True)
        b = _frame(parsing, codon)[cols].sort_values(cols).reset_index(drop=True)
        if a.equals(b):
            notes.append("PASS consumer_parse codon parity")
        else:
            errors.append("FAIL consumer_parse: Codon and Python parses differ")
    return notes, errors


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--schema", type=Path, required=True)
    ap.add_argument("samples", type=Path, nargs="+")
    ap.add_argument("--report", type=Path, default=None)
    args = ap.parse_args(argv)
    sys.path.insert(0, str(CHECKOUT))
    # Pipeline knobs (bridge limit) come from the ini; default to the checked-in example.
    os.environ.setdefault("HPCPERFSTATS_INI", str(CHECKOUT / "hpcperfstats.ini.example"))
    try:
        import hpcperfstats.dbload.lib.sync_timedb_parsing  # noqa: F401
    except ImportError as exc:
        print(f"SKIP consumer_parse: consumer not importable ({exc})")
        return 77
    stream = "".join(p.read_text(encoding="utf-8") for p in (args.schema, *args.samples))
    notes, errors = check_stream(stream)
    text = "\n".join(notes + errors) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text, encoding="utf-8")
    print(text, end="")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
