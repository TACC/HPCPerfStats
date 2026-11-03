"""Regressions from the Horizon c122-018 GPU-node profile (2026-11-03).

1. Daemon CPU came from ``/proc/<pid>/schedstat``, which is the main thread only: 9.1 core-s/h
   reported against 251.7 s/h of user+system for the 19-thread daemon. Process CPU must sum the
   per-thread schedstat the sampler already records.
2. NONE/SOLO legs ran collectors the daemon failed to disable (verified ⊋ intended) and were
   still ``ok``. A leg with extra types is ``type_mismatch`` and is not usable.
"""

from __future__ import annotations

import json
from pathlib import Path

from lib import leg_summary, procsample


def _snap(leader_ns, threads, utime=0, stime=0):
    return {"pid": 7, "starttime": 1, "run_ns": leader_ns, "utime": utime, "stime": stime,
            "threads": {tid: {"comm": "hpcperfstatsd", "run_ns": ns} for tid, ns in threads.items()}}


def test_process_run_ns_delta_sums_every_thread():
    s0 = _snap(1_000, {7: 1_000, 8: 5_000, 9: 9_000})
    s1 = _snap(1_100, {7: 1_100, 8: 65_000, 10: 400})  # 9 exited, 10 started
    assert procsample.process_run_ns_delta(s0, s1) == 100 + 60_000 + 400
    # Without per-thread data on both sides, fall back to the leader (single-thread processes).
    assert procsample.process_run_ns_delta(_snap(10, {}), _snap(30, {})) == 20
    assert procsample.process_run_ns_delta(_snap(10, {7: 10}), _snap(30, {})) == 20
    assert procsample.process_run_ns_delta(_snap(None, {}), _snap(None, {})) is None
    # A thread with unreadable schedstat in either sample is skipped, never counted whole.
    assert procsample.process_run_ns_delta(_snap(0, {7: None, 8: 5}), _snap(0, {7: 50, 8: 9})) == 4


def test_cpu_delta_counts_worker_threads():
    s0 = _snap(1_000_000, {7: 1_000_000, 8: 0}, utime=0, stime=0)
    s1 = _snap(2_000_000, {7: 2_000_000, 8: 69_000_000}, utime=5, stime=2)
    delta = procsample.cpu_delta(s0, s1)
    assert delta["cpu_resolution"] == "schedstat_ns"
    assert delta["cpu_s"] == 0.07  # leader alone would be 0.001


def _write_leg(leg_dir: Path, *, intended, verified, kind="NONE"):
    leg_dir.mkdir(parents=True)
    rows = []
    for i in range(120):
        # leader 0.01 s/s, one worker 0.06 s/s => 0.07 cores = 252 core-s/h
        threads = {"7": {"comm": "hpcperfstatsd", "run_ns": i * 10_000_000},
                   "8": {"comm": "hpcperfstatsd", "run_ns": i * 60_000_000}}
        proc = {"pid": 7, "starttime": 1, "run_ns": i * 10_000_000, "utime": i * 7, "stime": 0,
                "rss_kib": 1024, "num_threads": 2, "threads": threads}
        node = {"online_cpus": 4, "interrupts": {}, "cpu": {"user": i * 7, "system": 0, "idle": i * 393}}
        rows.append(json.dumps({"t": i, "wall": 1000.0 + i, "proc": proc, "node": node, "tcp": None}))
    (leg_dir / "samples.jsonl").write_text("\n".join(rows) + "\n")
    meta = {"leg_id": leg_dir.name, "kind": kind, "block": 1, "status": "ok",
            "phases": {"settle_end": 1000.0, "quiet_end": 1120.0},
            "intended_types": intended, "verified_types": verified, "missing_types": []}
    (leg_dir / "meta.json").write_text(json.dumps(meta))


def test_leg_cpu_is_whole_process(tmp_path):
    _write_leg(tmp_path / "b1-ALL-first", intended=["host_mem"], verified=["host_mem"], kind="ALL")
    proc = leg_summary.summarize_leg(tmp_path / "b1-ALL-first")["process"]
    assert abs(proc["cpu_core_s_per_h"]["mean"] - 252.0) < 1e-6
    assert abs(proc["cpu_pct_one_core"] - 7.0) < 1e-9
    assert abs(proc["user_s_per_h"] + proc["system_s_per_h"] - 252.0) < 1e-6


def test_leg_with_types_the_daemon_kept_is_type_mismatch(tmp_path):
    _write_leg(tmp_path / "b1-NONE-", intended=[], verified=["host_mem", "nvidia_gpu"])
    summary = leg_summary.summarize_leg(tmp_path / "b1-NONE-")
    assert summary["status"] == "type_mismatch"
    assert summary["extra_types"] == ["host_mem", "nvidia_gpu"]
    _write_leg(tmp_path / "b1-SOLO-x", intended=["host_mem"], verified=["host_mem"], kind="SOLO")
    ok = leg_summary.summarize_leg(tmp_path / "b1-SOLO-x")
    assert ok["status"] == "ok" and ok["extra_types"] == []
