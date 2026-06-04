#!/usr/bin/env python3
"""Lint monitor emitted st_name and event key names.

Run from HPCPerfStats/monitor/:
  ../.venv/bin/python scripts/check_emitted_variable_names.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

MONITOR = Path(__file__).resolve().parents[1]
SRC = MONITOR / "src"

ST_NAME_RE = re.compile(r'\.st_name\s*=\s*"([a-z][a-z0-9_]*)"')
X_KEY_RE = re.compile(r'\bX\(([A-Za-z_][A-Za-z0-9_]*)\s*,')
STATS_SET_RE = re.compile(r'stats_set\s*\([^,]+,\s*"([A-Za-z_][A-Za-z0-9_]*)"')

FORBIDDEN_KEY_RE = re.compile(
    r"^(?:CTL\d+|CTR\d+|FIXED_CTR\d*|FIXED_CTR|MSR_|0x[0-9A-Fa-f]+|V[13]_CTL|V[13]_CTR)"
)
LEGACY_TYPE_RE = re.compile(
    r"^(?:cpu|mem|vm|ps|numa|block|net|proc|vfs|nfs|tmpfs|sysv_shm|"
    r"cpu_counter_metrics|roofline_hw_peak|amd64_|intel_[0-9]|intel_knl$|intel_pcu$|"
    r"intel_skx|intel_snb|intel_ivb|intel_hsw|intel_bdw|arm_imc$|ib$|ib_ext|ib_sw|"
    r"lnet$|opa$|llite$|mdc$|osc$|mic$)$"
)
KEY_SHAPE_RE = re.compile(r"^[a-z][a-z0-9_]*$")

SKIP_STATS_SET_FILES = frozenset({
    "host_key_alias.c",
})


def scan_file(path: Path) -> list[str]:
    errs: list[str] = []
    text = path.read_text(encoding="utf-8", errors="replace")
    rel = path.relative_to(MONITOR)

    for m in ST_NAME_RE.finditer(text):
        name = m.group(1)
        if LEGACY_TYPE_RE.match(name):
            errs.append(f"{rel}: legacy st_name `{name}`")

    for m in X_KEY_RE.finditer(text):
        key = m.group(1)
        if key in ("E", "W", "U", "C"):
            continue
        if not KEY_SHAPE_RE.match(key):
            errs.append(f"{rel}: KEYS token `{key}` not lowercase snake")
        if FORBIDDEN_KEY_RE.match(key):
            errs.append(f"{rel}: forbidden KEYS token `{key}`")

    if path.name not in SKIP_STATS_SET_FILES:
        for m in STATS_SET_RE.finditer(text):
            key = m.group(1)
            if not KEY_SHAPE_RE.match(key):
                errs.append(f"{rel}: stats_set key `{key}` not lowercase snake")
            if FORBIDDEN_KEY_RE.match(key):
                errs.append(f"{rel}: forbidden stats_set key `{key}`")

    return errs


def main() -> int:
    errs: list[str] = []
    for path in sorted(SRC.rglob("*")):
        if path.suffix not in (".c", ".h"):
            continue
        errs.extend(scan_file(path))

    if errs:
        print("check_emitted_variable_names: FAIL", file=sys.stderr)
        for e in errs:
            print(e, file=sys.stderr)
        return 1

    print("check_emitted_variable_names: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
