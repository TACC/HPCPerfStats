#!/usr/bin/env python3
"""Gate oracle: campaign ledger mentions hold_seconds E6/E7 rescore."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
text = (ROOT / "docs" / "SYNC_TIMEDB_THROUGHPUT_CAMPAIGN.md").read_text(encoding="utf-8")
ok = ("hold_seconds" in text or "hold-seconds" in text) and "E6" in text and "E7" in text
if not ok:
  print("FAIL")
  sys.exit(1)
print("campaign hold_seconds ledger verification passed")
