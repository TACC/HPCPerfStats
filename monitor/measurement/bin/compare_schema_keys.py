#!/usr/bin/env python3
"""Diff the emitted schema keys of two overhead-profile runs (profile.json ``schema_keys``).

Exit 0 when no key was removed, 1 when the candidate dropped a key or a type (e.g. an NVLink
key under nvidia_gpu after a DCGM watch change), 2 when either run has no schema capture.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.schema_capture import diff_keys


def _keys(path: Path) -> dict[str, list[str]] | None:
    doc = json.loads(path.read_text()).get("schema_keys") or {}
    return doc.get("types") if doc.get("status") == "ok" else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base", type=Path, help="baseline profile.json")
    parser.add_argument("candidate", type=Path, help="candidate profile.json")
    parser.add_argument("--type", action="append", default=[],
                        help="limit to these collector types (repeatable)")
    args = parser.parse_args()
    base, cand = _keys(args.base), _keys(args.candidate)
    if base is None or cand is None:
        missing = [str(p) for p, k in ((args.base, base), (args.candidate, cand)) if k is None]
        print("NOT_MEASURED: no schema capture in " + ", ".join(missing))
        return 2
    if args.type:
        base = {t: k for t, k in base.items() if t in args.type}
        cand = {t: k for t, k in cand.items() if t in args.type}
    diff = diff_keys(base, cand)
    print(json.dumps(diff, indent=2))
    for name in sorted(set(base) & set(cand)):
        print(f"{name}: {len(base[name])} -> {len(cand[name])} keys")
    removed = diff["only_in_base"] or any(v["removed"] for v in diff["changed"].values())
    print("SCHEMA_KEYS_" + ("REMOVED" if removed else "OK"))
    return 1 if removed else 0


if __name__ == "__main__":
    raise SystemExit(main())
