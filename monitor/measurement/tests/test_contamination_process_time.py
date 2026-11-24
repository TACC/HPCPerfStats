"""Contamination counts other processes (user/system time), not the daemon's interrupt cost.

2026-11-24: c492-081 (SKX, 48 CPUs, limit 0.48 cores) and c317-016 (LS6 A100) aborted on the
first ALL leg. The daemon's CPU counters rotate on a timer on every CPU (c573-002: 0.39
interrupt cores), which the old check counted as outside load. Interrupt time stays reported
(``non_daemon_busy_cores``, counter_rotation); only ``outside_process_cores`` decides.
"""

from __future__ import annotations

import json
from pathlib import Path

from lib import leg_summary

CLK = leg_summary.CLK_TCK


def _write_leg(leg_dir: Path, *, irq_cores: float, outside_user_cores: float, ncpu: int = 48) -> None:
    leg_dir.mkdir(parents=True)
    rows = []
    for i in range(120):
        daemon = 0.001
        user = int(i * (daemon + outside_user_cores) * CLK)
        irq = int(i * irq_cores * CLK)
        proc = {"pid": 7, "starttime": 1, "run_ns": int(i * daemon * 1e9), "utime": int(i * daemon * CLK),
                "stime": 0, "rss_kib": 1024, "num_threads": 1,
                "threads": {"7": {"comm": "hpcperfstatsd", "run_ns": int(i * daemon * 1e9)}}}
        node = {"online_cpus": ncpu, "interrupts": {},
                "cpu": {"user": user, "system": 0, "irq": irq, "idle": i * ncpu * CLK - user - irq}}
        rows.append(json.dumps({"t": i, "wall": 1000.0 + i, "proc": proc, "node": node, "tcp": None}))
    (leg_dir / "samples.jsonl").write_text("\n".join(rows) + "\n")
    meta = {"leg_id": leg_dir.name, "kind": "ALL", "block": 1, "status": "ok",
            "phases": {"settle_end": 1000.0, "quiet_end": 1120.0},
            "intended_types": ["host_cpu_hw"], "verified_types": ["host_cpu_hw"],
            "missing_types": []}
    (leg_dir / "meta.json").write_text(json.dumps(meta))


def test_counter_rotation_interrupts_are_not_contamination(tmp_path: Path) -> None:
    _write_leg(tmp_path / "b1-ALL-first", irq_cores=0.6, outside_user_cores=0.0)
    s = leg_summary.summarize_leg(tmp_path / "b1-ALL-first")
    assert s["contaminated"] is False
    assert abs(s["node"]["outside_process_cores"]) < 0.02
    assert s["node"]["non_daemon_busy_cores"] > 0.55   # still reported
    assert abs(s["node"]["irq_cores"] - 0.6) < 0.02


def test_an_outside_process_is_still_contamination(tmp_path: Path) -> None:
    _write_leg(tmp_path / "b1-ALL-first", irq_cores=0.0, outside_user_cores=1.0)
    s = leg_summary.summarize_leg(tmp_path / "b1-ALL-first")
    assert s["contaminated"] is True
    assert abs(s["node"]["outside_process_cores"] - 1.0) < 0.02
