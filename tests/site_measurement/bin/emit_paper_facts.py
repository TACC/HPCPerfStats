#!/usr/bin/env python3
"""Emit paper measurement slots from a results directory (never invents)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib.paper_facts import merge_results_dir, render_markdown  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", required=True)
    parser.add_argument("--out-json", default="")
    parser.add_argument("--out-md", default="")
    args = parser.parse_args()
    facts = merge_results_dir(Path(args.results_dir))
    markdown = render_markdown(facts)
    if args.out_json:
        Path(args.out_json).write_text(
            json.dumps(facts, indent=2) + "\n", encoding="utf-8"
        )
    if args.out_md:
        Path(args.out_md).write_text(markdown, encoding="utf-8")
    if not args.out_md:
        sys.stdout.write(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
