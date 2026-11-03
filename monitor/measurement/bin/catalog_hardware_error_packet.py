#!/usr/bin/env python3
"""Catalog Job Detail hardware-error coverage (AUTHOR_NOTES item 5)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib.hardware_errors import catalog  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    payload = catalog()
    text = json.dumps(payload, indent=2) + "\n"
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(str(out))
    else:
        sys.stdout.write(text)
    print(payload["paper_sentence"], file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
