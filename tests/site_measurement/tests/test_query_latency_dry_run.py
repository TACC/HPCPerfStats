"""Dry-run skips Job Detail when JID is empty."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_dry_run_skips_job_detail_without_jid() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "bin" / "measure_query_latency.py"),
            "--base-url",
            "https://example.test/api/",
            "--dry-run",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    lines = proc.stdout.splitlines()
    assert any(line.startswith("extended_search_7d\thttp") for line in lines)
    assert any("job_detail\tSKIP" in line for line in lines)
    assert any("job_plots\tSKIP" in line for line in lines)
    assert any("extended_search_queue_7d\tSKIP" in line for line in lines)
    assert not any("/jobs/plots/" in line for line in lines)
