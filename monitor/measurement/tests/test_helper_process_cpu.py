"""Helper processes the daemon drives (standalone DCGM nv-hostengine) are monitor cost.

LS6 c317-016 2026-11-24: on the first ALL leg the host engine served the daemon's GPU fields at
1.54 cores (NONE leg: 0.04 non-daemon cores). The harness counted that as outside load and
aborted. The sampler now records helper CPU ticks, the leg summary reports ``helper_cores`` and
keeps them out of ``non_daemon_busy_cores``, so such a leg is neither aborted nor silently
under-costed.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from lib import leg_summary, orchestrator, procsample

CLK = leg_summary.CLK_TCK


def _write_leg(leg_dir: Path, *, helper_cores: float, restart_at: int | None = None) -> None:
    """ALL leg on 128 CPUs: daemon 0.01 cores, helper ``helper_cores``, nothing else."""
    leg_dir.mkdir(parents=True)
    rows = []
    for i in range(120):
        helper_ticks = int(i * helper_cores * CLK)
        pid = 4242
        if restart_at is not None and i >= restart_at:
            pid, helper_ticks = 5151, int((i - restart_at) * helper_cores * CLK)
        busy = int(i * (0.01 + helper_cores) * CLK)
        proc = {"pid": 7, "starttime": 1, "run_ns": i * 10_000_000, "utime": i, "stime": 0,
                "rss_kib": 1024, "num_threads": 1,
                "threads": {"7": {"comm": "hpcperfstatsd", "run_ns": i * 10_000_000}}}
        node = {"online_cpus": 128, "interrupts": {},
                "cpu": {"user": busy, "system": 0, "idle": i * 128 * CLK - busy}}
        rows.append(json.dumps({"t": i, "wall": 1000.0 + i, "proc": proc, "node": node,
                                "tcp": None, "helpers": {
                                    "nv-hostengine": {"pid": pid, "utime": helper_ticks,
                                                      "stime": 0}}}))
    (leg_dir / "samples.jsonl").write_text("\n".join(rows) + "\n")
    meta = {"leg_id": leg_dir.name, "kind": "ALL", "block": 1, "status": "ok",
            "phases": {"settle_end": 1000.0, "quiet_end": 1120.0},
            "intended_types": ["nvidia_gpu"], "verified_types": ["nvidia_gpu"],
            "missing_types": []}
    (leg_dir / "meta.json").write_text(json.dumps(meta))


def test_host_engine_cpu_is_reported_and_not_outside_load(tmp_path: Path) -> None:
    _write_leg(tmp_path / "b1-ALL-first", helper_cores=1.54)
    s = leg_summary.summarize_leg(tmp_path / "b1-ALL-first")
    assert abs(s["node"]["helper_cores"]["nv-hostengine"] - 1.54) < 0.02
    assert abs(s["node"]["non_daemon_busy_cores"]) < 0.05
    assert s["contaminated"] is False


def test_helper_restart_is_not_counted_as_cpu(tmp_path: Path) -> None:
    """A new PID resets the tick counters; that pair is skipped, never a negative or huge jump."""
    _write_leg(tmp_path / "b1-ALL-first", helper_cores=0.5, restart_at=60)
    cores = leg_summary.summarize_leg(tmp_path / "b1-ALL-first")["node"]["helper_cores"]
    assert abs(cores["nv-hostengine"] - 0.5) < 0.02


def test_old_raw_without_helpers_reports_none(tmp_path: Path) -> None:
    _write_leg(tmp_path / "b1-ALL-first", helper_cores=0.0)
    rows = [json.loads(x) for x in (tmp_path / "b1-ALL-first" / "samples.jsonl").read_text()
            .splitlines()]
    for r in rows:
        del r["helpers"]
    (tmp_path / "b1-ALL-first" / "samples.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n")
    assert leg_summary.summarize_leg(tmp_path / "b1-ALL-first")["node"]["helper_cores"] == {}


def test_sample_cpu_ticks_reads_stat(tmp_path: Path) -> None:
    (tmp_path / "99").mkdir()
    fields = ["99", "(nv-hostengine)", "S"] + ["0"] * 10 + ["321", "45"] + ["0"] * 40
    (tmp_path / "99" / "stat").write_text(" ".join(fields) + "\n")
    assert procsample.sample_cpu_ticks(99, tmp_path) == {"pid": 99, "utime": 321, "stime": 45}
    assert procsample.sample_cpu_ticks(100, tmp_path) is None


def test_sampler_records_helpers(tmp_path: Path) -> None:
    out = tmp_path / "s.jsonl"
    script = Path(__file__).resolve().parents[1] / "bin" / "sample_proc.py"
    subprocess.run([sys.executable, str(script), "--out", str(out), "--duration", "1.5",
                    "--no-tcp", "--comm", "no-such-daemon", "--helper-comm", "no-such-helper"],
                   check=True, timeout=30)
    rows = [json.loads(x) for x in out.read_text().splitlines()]
    assert rows and all(r["helpers"] == {"no-such-helper": None} for r in rows)


def test_harness_samples_the_dcgm_host_engine() -> None:
    assert "nv-hostengine" in orchestrator.HELPER_COMMS
