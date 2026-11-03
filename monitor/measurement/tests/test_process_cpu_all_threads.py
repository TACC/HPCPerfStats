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


def test_leg_thread_top_ranks_threads_by_cpu(tmp_path):
    """c122-018 needed a hand-run per-tid paste to see that one DCGM thread burned 130
    core-s/h; the leg summary carries that table now (tid, comm, core-s/h, hottest first)."""
    _write_leg(tmp_path / "b1-ALL-first", intended=["host_mem"], verified=["host_mem"], kind="ALL")
    proc = leg_summary.summarize_leg(tmp_path / "b1-ALL-first")["process"]
    top = proc["thread_top"]
    assert [t["tid"] for t in top] == ["8", "7"]
    assert all(t["comm"] == "hpcperfstatsd" for t in top)
    assert abs(top[0]["core_s_per_h"] - 216.0) < 1e-6 and abs(top[1]["core_s_per_h"] - 36.0) < 1e-6
    assert abs(sum(proc["thread_split"].values()) - 1.0) < 1e-12  # by-comm view unchanged
    # Without per-thread data there is no table, not an empty or invented one.
    rows = [json.loads(line) for line in (tmp_path / "b1-ALL-first" / "samples.jsonl").read_text().splitlines()]
    for row in rows:
        row["proc"]["threads"] = {}
    (tmp_path / "b1-ALL-first" / "samples.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    assert leg_summary.summarize_leg(tmp_path / "b1-ALL-first")["process"]["thread_top"] is None


def test_leg_thread_top_is_bounded():
    threads = {str(t): {"comm": f"w{t}", "run_ns": 0} for t in range(20)}
    later = {str(t): {"comm": f"w{t}", "run_ns": (t + 1) * 1_000_000} for t in range(20)}
    seg = [{"wall": 0.0, "proc": {"pid": 1, "threads": threads}},
           {"wall": 10.0, "proc": {"pid": 1, "threads": later}}]
    top = leg_summary.thread_top([seg])
    assert len(top) == leg_summary.THREAD_TOP_N == 8
    assert top[0]["tid"] == "19" and top[-1]["tid"] == "12"


def test_report_renders_thread_top_table(tmp_path):
    from lib import profile_report

    profile = {"legs": [{"leg_id": "b1-ALL-first", "kind": "ALL", "status": "ok", "contaminated": False,
                         "thread_top": [{"tid": "8", "comm": "hpcperfstatsd", "core_s_per_h": 216.0}]}]}
    md = "\n".join(profile_report.thread_top_lines(profile))
    assert "## Daemon threads (hottest, first usable ALL leg)" in md
    assert "| 8 | hpcperfstatsd | 216 |" in md
    assert "NOT_MEASURED" in "\n".join(profile_report.thread_top_lines({"legs": []}))


def test_leg_with_types_the_daemon_kept_is_type_mismatch(tmp_path):
    _write_leg(tmp_path / "b1-NONE-", intended=[], verified=["host_mem", "nvidia_gpu"])
    summary = leg_summary.summarize_leg(tmp_path / "b1-NONE-")
    assert summary["status"] == "type_mismatch"
    assert summary["extra_types"] == ["host_mem", "nvidia_gpu"]
    _write_leg(tmp_path / "b1-SOLO-x", intended=["host_mem"], verified=["host_mem"], kind="SOLO")
    ok = leg_summary.summarize_leg(tmp_path / "b1-SOLO-x")
    assert ok["status"] == "ok" and ok["extra_types"] == []
