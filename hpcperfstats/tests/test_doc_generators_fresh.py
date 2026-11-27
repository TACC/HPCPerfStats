"""Committed generated docs match a re-run of their generator's ``--check``."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROFILES = ROOT / "docs" / "measurements" / "overhead_profile"

GENERATORS = {
  "metrics_reference": [
    "monitor/scripts/gen_metrics_reference.py",
    "--check",
  ],
  "monitor_naming_tables": [
    "scripts/docs/gen_monitor_naming_tables.py",
    "--check",
  ],
  "monitor_variables_consumer_map": [
    "scripts/docs/gen_monitor_variables_consumer_map.py",
    "--check",
  ],
  "overhead_profiles": [
    "monitor/measurement/bin/aggregate_profiles.py",
    "--check",
    "--out",
    str(PROFILES),
    *sorted(str(p) for p in PROFILES.glob("*/profile.json")),
  ],
}


@pytest.mark.parametrize("name", sorted(GENERATORS))
def test_generated_doc_is_fresh(name: str) -> None:
  script, *args = GENERATORS[name]
  res = subprocess.run(
    [sys.executable, str(ROOT / script), *args],
    capture_output=True,
    text=True,
    cwd=ROOT,
    timeout=120,
  )
  assert res.returncode == 0, res.stdout + res.stderr
