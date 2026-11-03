"""Regression tests for sketch defects fixed by the overhead-profile plan (group G)."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKOUT = ROOT.parents[1]


def test_microbench_template_path_exists():
    out = subprocess.run(["bash", str(ROOT / "bin" / "run_microbench_on_vs_off.sh")],
                         capture_output=True, text=True, check=True).stdout
    paths = re.findall(r"(\S+\.template\.json)", out)
    assert paths, out
    for rel in paths:
        assert (ROOT / rel).is_file(), rel


def test_superseded_isolation_scripts_removed_and_swept():
    gone = ("capture_collector_isolation", "summarize_collector_isolation")
    for name in gone:
        assert not list((ROOT / "bin").glob(name + ".*"))
    scan_roots = [ROOT, CHECKOUT / "monitor" / "README.md", CHECKOUT / "docs",
                  CHECKOUT / "tests" / "site_measurement"]
    hits = []
    for base in scan_roots:
        files = [base] if base.is_file() else [p for p in base.rglob("*") if p.is_file()]
        for f in files:
            if f.suffix not in (".md", ".sh", ".py", ".txt", ".json", "") or f.name == Path(__file__).name:
                continue
            text = f.read_text(errors="replace")
            hits += [str(f) for n in gone if n in text]
    assert hits == []
    # positive control: the same scan finds a name that is still referenced
    readme = (ROOT / "RUNBOOK.md").read_text()
    assert "run_overhead_profile.sh" in readme


def test_capture_daemon_overhead_runs_new_sampler():
    text = (ROOT / "bin" / "capture_daemon_overhead.sh").read_text()
    assert "sample_proc.py" in text and "samples.jsonl" in text


def test_summarize_jitter_reports_percentiles(tmp_path):
    for label, lat in (("on", 40), ("off", 10)):
        d = tmp_path / label
        d.mkdir()
        hist = ["# Histogram"] + [f"{i:06d} {100 if i in (5, lat) else 0:06d}" for i in range(100)]
        hist.append(f"# Max Latencies: {lat + 3:05d}")
        (d / "cyclictest.txt").write_text("\n".join(hist) + "\n")
    out = tmp_path / "jitter.json"
    subprocess.run([sys.executable, str(ROOT / "bin" / "summarize_jitter.py"), "--on",
                    str(tmp_path / "on"), "--off", str(tmp_path / "off"), "--out", str(out)],
                   check=True, capture_output=True)
    doc = json.loads(out.read_text())
    assert doc["daemon_on_max_us"] == 43.0
    assert doc["daemon_on_percentiles_us"]["p50"] == 5
    assert doc["daemon_on_percentiles_us"]["p99"] == 40
    assert doc["daemon_off_percentiles_us"]["p99"] == 10
    assert doc["p99_delta_us"] == 30
