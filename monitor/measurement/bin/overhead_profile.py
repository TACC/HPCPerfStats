#!/usr/bin/env python3
"""Entry point for run_overhead_profile.sh (stdlib only; runs on any node python3)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.orchestrator import main

if __name__ == "__main__":
    raise SystemExit(main())
