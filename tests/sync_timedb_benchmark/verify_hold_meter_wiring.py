#!/usr/bin/env python3
"""Gate oracle: E6/E7 A/B arms use hold_seconds retain meter."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
bad: list[str] = []
for name in ("test_e6_parse_feed_ab.py", "test_e7_proc_build_ab.py"):
  path = ROOT / "tests" / "sync_timedb_benchmark" / name
  text = path.read_text(encoding="utf-8")
  if "hold_seconds_retain_candidate" not in text:
    bad.append(f"missing_hold:{name}")
  if "meter=hold_seconds" not in text:
    bad.append(f"missing_meter_marker:{name}")
  for line in text.splitlines():
    stripped = line.strip()
    if stripped.startswith(
        "from tests.sync_timedb_benchmark.screening_runner import"
    ) and "e6_retain_candidate" in stripped:
      bad.append(f"imports_e6_retain:{name}")
      break
if bad:
  print("FAIL", bad)
  sys.exit(1)
print("e6_e7 hold meter wiring verification passed")
