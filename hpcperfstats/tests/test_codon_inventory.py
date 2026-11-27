"""Optional gate: tier-0 Codon inventory must pass when HPCPERFSTATS_CODON=1."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_TIER0 = (
  _REPO / "tests" / "experiments" / "codon" / "inventory" / "tier0_stdlib.py"
)


@pytest.mark.skipif(
  os.environ.get("HPCPERFSTATS_CODON") != "1",
  reason="Set HPCPERFSTATS_CODON=1 to run Codon inventory gate",
)
def test_codon_tier0_stdlib_builds() -> None:
  codon = os.environ.get(
    "CODON_BIN",
    f"/data/user/{os.environ.get('USER', 'unknown')}/codon/bin/codon",
  )
  if not Path(codon).is_file():
    pytest.skip(f"Codon binary not installed at {codon}")
  out = subprocess.run(
    [
      codon,
      "build",
      "-release",
      "-o",
      "/tmp/hpcperfstats_codon_tier0",
      str(_TIER0),
    ],
    cwd=str(_TIER0.parent),
    capture_output=True,
    text=True,
    check=False,
  )
  assert out.returncode == 0, out.stderr or out.stdout
