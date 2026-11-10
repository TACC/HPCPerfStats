#!/usr/bin/env python3
"""Catalog differences between overhead-profile runs meant to be compared per leg.

Usage: compare_runs.py RUN_DIR RUN_DIR [...] [--out FILE.md]

Exit 0 when every must-match row (leg length, settle, FWQ, blocks, governor, daemon conf
except the broker connection) is identical and no run has ``fit_steps``; 1 otherwise.
Intended differences (fleet build, OS, hardware, collector groups) are listed, never fatal.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.run_compare import compare, load_run, to_markdown


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("runs", nargs="+", type=Path, help="run directories (with raw/)")
    parser.add_argument("--out", type=Path, help="also write the markdown catalog here")
    args = parser.parse_args()
    if len(args.runs) < 2:
        parser.error("need at least two run directories")
    try:
        result = compare([load_run(r) for r in args.runs])
    except ValueError as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 2
    text = to_markdown(result)
    print(text)
    if args.out:
        args.out.write_text(text)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
