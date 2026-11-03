#!/usr/bin/env python3
"""Write a compile_commands.json with one entry per source file (the first).

clang-tidy runs every entry that matches a file in one process and shares its
file cache between them. stats_buffer.c is compiled both in src/ and in tests/;
the src/ run caches the generated ./monitor_capability_slug.h relative to the
src build dir, and the tests/ run then reports "cannot open file" for it.
gcc builds both fine, so run_cpp_linter.sh lints each file once.

Usage: compile_db_dedupe.py <in compile_commands.json> <out compile_commands.json>
"""
from __future__ import annotations

import json
import os
import sys


def dedupe(entries: list[dict]) -> list[dict]:
    """Keep the first entry per absolute source path, preserving order."""
    first: dict[str, dict] = {}
    for entry in entries:
        path = os.path.normpath(os.path.join(entry.get("directory", ""), entry["file"]))
        first.setdefault(path, entry)
    return list(first.values())


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8") as src:
        entries = json.load(src)
    with open(argv[2], "w", encoding="utf-8") as out:
        json.dump(dedupe(entries), out, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
