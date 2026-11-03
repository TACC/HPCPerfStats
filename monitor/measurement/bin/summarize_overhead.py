#!/usr/bin/env python3
"""Build overhead.json from a capture directory."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib.overhead_report import build_overhead_report  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-dir", required=True)
    args = parser.parse_args()
    capture_dir = Path(args.capture_dir)
    report = build_overhead_report(capture_dir)
    out = capture_dir / "overhead.json"
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
