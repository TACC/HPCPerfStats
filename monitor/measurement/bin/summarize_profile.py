#!/usr/bin/env python3
"""Rebuild profile.json, report.md and profile_row.csv from a result directory's raw/ files."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.profile_report import write_outputs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    for path in write_outputs(args.run_dir).values():
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
