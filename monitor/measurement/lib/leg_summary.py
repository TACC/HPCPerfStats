"""Summarize one leg from its raw files (samples.jsonl, meta.json, fwq/, perf.csv).

Every number the report prints is recomputed here from ``raw/legs/<leg>/``; nothing comes
from the orchestrator's memory. Phases (wall-clock, from meta.json):

  settle   unit start → settle_end      startup cost; excluded from steady state
  quiet    settle_end → quiet_end       steady state: every headline process/node metric
  fwq      quiet_end  → fwq_end         FWQ on the other CPUs; daemon CPU under full load
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from . import fwq, perfstat, rapl, stats
from .procsample import CLK_TCK, process_run_ns_delta

CONTAMINATION_MIN_CORES = 0.25
_BUSY = ("user", "nice", "system", "irq", "softirq", "steal")
BURST_TOLERANCE_S = 2.0


def load_samples(path: Path) -> list[dict[str, Any]]:
    out = []
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # a torn final line from an interrupted sampler
    return out


def _window(samples: list[dict[str, Any]], lo: float | None, hi: float | None) -> list[dict[str, Any]]:
    return [s for s in samples if (lo is None or s["wall"] >= lo) and (hi is None or s["wall"] < hi)]


def _pairs(samples: list[dict[str, Any]]) -> Iterable[tuple[dict[str, Any], dict[str, Any]]]:
    return zip(samples, samples[1:])  # noqa: B905, RUF007 (py3.9 nodes)


def _proc_cpu_series(samples: list[dict[str, Any]]) -> tuple[list[tuple[float, float]], int, str]:
    """(wall, CPU-seconds in that interval) for consecutive samples of the same PID."""
    series: list[tuple[float, float]] = []
    pid_changes = 0
    resolution = "schedstat_ns"
    for a, b in _pairs(samples):
        pa, pb = a.get("proc"), b.get("proc")
        if not pa or not pb:
            continue
        if pa["pid"] != pb["pid"] or pa.get("starttime") != pb.get("starttime"):
            pid_changes += 1
            continue
        run_ns = process_run_ns_delta(pa, pb)
        if run_ns is not None:
            cpu = run_ns / 1e9
        else:
            resolution = "jiffies"
            cpu = ((pb["utime"] + pb["stime"]) - (pa["utime"] + pa["stime"])) / CLK_TCK
        dt = b["wall"] - a["wall"]
        if dt <= 0:
            continue
        series.append((a["wall"], cpu / dt))
    return series, pid_changes, resolution


def _segments(samples: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    segs: list[list[dict[str, Any]]] = []
    for s in samples:
        p = s.get("proc")
        if not p:
            continue
        if segs and segs[-1][-1]["proc"]["pid"] == p["pid"]:
            segs[-1].append(s)
        else:
            segs.append([s])
    return segs


def _rate(segs: list[list[dict[str, Any]]], getter, per: float) -> float | None:
    """Sum of per-segment counter deltas divided by covered seconds, scaled by ``per``."""
    total = 0.0
    secs = 0.0
    for seg in segs:
        if len(seg) < 2:
            continue
        a, b = getter(seg[0]), getter(seg[-1])
        if a is None or b is None:
            continue
        total += b - a
        secs += seg[-1]["wall"] - seg[0]["wall"]
    return total / secs * per if secs > 0 else None


def _median(xs: list[float]) -> float | None:
    return stats.percentile(xs, 50)


def burst_cost_model(series: list[tuple[float, float]], sample_freq: float,
                     sample_freq_slow: float, enable_slow_tier: int) -> dict[str, Any]:
    """Classify CPU bursts by schedule offset and report the median CPU-ms per class.

    A burst is a run of seconds whose CPU exceeds the idle baseline (median) by more than
    5 × MAD + 1 ms. Bursts starting within ±2 s of a ``sample_freq_slow`` boundary are full
    samples (when the slow tier is on), within ±2 s of a ``sample_freq`` boundary fast
    samples; the rest are sends. A send that coincides with a sample is counted as that
    sample, so ``ms_per_send`` covers only unaligned sends.
    """
    if not series:
        return {"ms_per_fast_sample": None, "ms_per_full_sample": None, "ms_per_send": None,
                "n_fast": 0, "n_full": 0, "n_send": 0}
    vals = [c for _, c in series]
    base = _median(vals)
    mad = _median([abs(v - base) for v in vals])
    thresh = 5 * mad + 0.001
    bursts: list[tuple[float, float]] = []
    start = None
    acc = 0.0
    prev_t = None
    for t, c in series:
        ex = c - base
        if ex > thresh:
            if start is None or (prev_t is not None and t - prev_t > 1.5):
                if start is not None:
                    bursts.append((start, acc))
                start, acc = t, 0.0
            acc += ex
            prev_t = t
        elif start is not None:
            bursts.append((start, acc))
            start, prev_t = None, None
    if start is not None:
        bursts.append((start, acc))

    def near(t: float, period: float) -> bool:
        if not period:
            return False
        r = t % period
        return min(r, period - r) <= BURST_TOLERANCE_S

    classes: dict[str, list[float]] = {"full": [], "fast": [], "send": []}
    for t, cost in bursts:
        if enable_slow_tier and near(t, sample_freq_slow):
            classes["full"].append(cost)
        elif near(t, sample_freq):
            classes["fast"].append(cost)
        else:
            classes["send"].append(cost)

    def ms(xs: list[float]) -> float | None:
        m = _median(xs)
        return None if m is None else m * 1000.0

    return {"ms_per_fast_sample": ms(classes["fast"]), "ms_per_full_sample": ms(classes["full"]),
            "ms_per_send": ms(classes["send"]), "n_fast": len(classes["fast"]),
            "n_full": len(classes["full"]), "n_send": len(classes["send"]),
            "baseline_cpu_s_per_s": base}


def _process_summary(meta: dict[str, Any], settle: list[dict[str, Any]],
                     quiet: list[dict[str, Any]], load: list[dict[str, Any]]) -> dict[str, Any] | None:
    series, pid_changes, resolution = _proc_cpu_series(quiet)
    if not series:
        return None
    cores = [c for _, c in series]
    ci = stats.mean_ci(cores)
    per_h = {k: (v * 3600 if isinstance(v, (int, float)) and k in ("mean", "lo", "hi") else v)
             for k, v in ci.items()}
    online = [s["node"].get("online_cpus") for s in quiet if s.get("node")]
    nproc = _median([float(x) for x in online if x]) or 1.0
    segs = _segments(quiet)
    cad = meta.get("cadence") or {}
    model = burst_cost_model(series, cad.get("sample_freq") or 30.0,
                             cad.get("sample_freq_slow") or 600.0, cad.get("enable_slow_tier", 1))
    pq = [s["proc"] for s in quiet if s.get("proc")]

    def mean_of(key: str, scale: float = 1.0) -> float | None:
        xs = [p[key] for p in pq if p.get(key) is not None]
        return sum(xs) / len(xs) * scale if xs else None

    def med_of(key: str) -> float | None:
        return _median([float(p[key]) for p in pq if p.get(key) is not None])

    def io(key: str):
        return lambda s: (s["proc"].get("io") or {}).get(key)

    thread_cpu: dict[str, float] = {}
    for seg in segs:
        first, last = seg[0]["proc"].get("threads") or {}, seg[-1]["proc"].get("threads") or {}
        for tid, t1 in last.items():
            t0 = first.get(tid)
            if t0 and t0.get("run_ns") is not None and t1.get("run_ns") is not None:
                thread_cpu[t1["comm"]] = thread_cpu.get(t1["comm"], 0) + t1["run_ns"] - t0["run_ns"]
    tot = sum(thread_cpu.values())
    vol = _rate(segs, lambda s: s["proc"].get("vol_ctxt"), 1.0)
    invol = _rate(segs, lambda s: s["proc"].get("invol_ctxt"), 1.0)
    tcp_segs = [[s for s in seg if s.get("tcp")] for seg in segs]
    acked_h = _rate(tcp_segs, lambda s: s["tcp"].get("bytes_acked"), 3600.0)
    send_freq = cad.get("send_freq") or 300.0

    # Startup: CPU in the settle window above what steady state would have used.
    sseries, _, _ = _proc_cpu_series(settle)
    init_cpu = None
    if sseries:
        dur = len(sseries)
        init_cpu = max(0.0, sum(c for _, c in sseries) - (ci["mean"] * dur))
    init_rss = [s["proc"]["rss_kib"] for s in settle if s.get("proc") and s["proc"].get("rss_kib")]
    lseries, _, _ = _proc_cpu_series(load)
    load_ci = stats.mean_ci([c for _, c in lseries]) if lseries else None

    return {
        "cpu_core_s_per_h": per_h,
        "cpu_resolution": resolution,
        "cpu_pct_one_core": ci["mean"] * 100.0,
        "cpu_pct_node": ci["mean"] / nproc * 100.0,
        "online_cpus": nproc,
        "user_s_per_h": _rate(segs, lambda s: s["proc"]["utime"] / CLK_TCK, 3600.0),
        "system_s_per_h": _rate(segs, lambda s: s["proc"]["stime"] / CLK_TCK, 3600.0),
        "cpu_1s_pct": {"p50": stats.percentile(cores, 50) * 100, "p95": stats.percentile(cores, 95) * 100,
                       "max": max(cores) * 100},
        **{k: model[k] for k in ("ms_per_fast_sample", "ms_per_full_sample", "ms_per_send")},
        "burst_counts": {k: model[k] for k in ("n_fast", "n_full", "n_send")},
        "thread_split": {k: v / tot for k, v in sorted(thread_cpu.items())} if tot else None,
        "wakeups_per_s": (vol or 0) + (invol or 0) if vol is not None or invol is not None else None,
        "vol_ctxt_per_s": vol,
        "invol_ctxt_per_s": invol,
        "minflt_per_s": _rate(segs, lambda s: s["proc"].get("minflt"), 1.0),
        "majflt_per_s": _rate(segs, lambda s: s["proc"].get("majflt"), 1.0),
        "rss_mib": mean_of("rss_kib", 1 / 1024),
        "pss_mib": mean_of("pss_kib", 1 / 1024),
        "uss_mib": mean_of("uss_kib", 1 / 1024),
        "rss_peak_mib": max((p["rss_kib"] for p in pq if p.get("rss_kib")), default=0) / 1024 or None,
        "vmhwm_mib": (pq[-1].get("vmhwm_kib") or 0) / 1024 or None,
        "threads": med_of("num_threads"),
        "fds": med_of("fds"),
        "perf_event_fds": med_of("perf_event_fds"),
        "read_bytes_per_h": _rate(segs, io("read_bytes"), 3600.0),
        "write_bytes_per_h": _rate(segs, io("write_bytes"), 3600.0),
        "rchar_per_h": _rate(segs, io("rchar"), 3600.0),
        "wchar_per_h": _rate(segs, io("wchar"), 3600.0),
        "syscalls_per_s": ((_rate(segs, io("syscr"), 1.0) or 0) + (_rate(segs, io("syscw"), 1.0) or 0))
        if any(s["proc"].get("io") for s in quiet if s.get("proc")) else None,
        "broker_bytes_acked_per_h": acked_h,
        "broker_bytes_received_per_h": _rate(tcp_segs, lambda s: s["tcp"].get("bytes_received"), 3600.0),
        "broker_segs_out_per_h": _rate(tcp_segs, lambda s: s["tcp"].get("segs_out"), 3600.0),
        "broker_bytes_per_send": acked_h * send_freq / 3600.0 if acked_h is not None else None,
        "broker_socket_seen": any(s.get("tcp") for s in quiet),
        "pid_changes": pid_changes,
        "init_cpu_s": init_cpu,
        "init_peak_rss_mib": max(init_rss) / 1024 if init_rss else None,
        "startup_wall_s": meta.get("startup_wall_s"),
        "cpu_core_s_per_h_under_load": ({k: (v * 3600 if k in ("mean", "lo", "hi") and v is not None else v)
                                         for k, v in load_ci.items()} if load_ci else None),
    }


def process_summary_from_samples(samples: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Whole capture as the steady window (legacy capture_daemon_overhead.sh)."""
    return _process_summary({}, [], samples, [])


def _node_summary(quiet: list[dict[str, Any]], proc: dict[str, Any] | None) -> dict[str, Any] | None:
    nodes = [s for s in quiet if s.get("node")]
    if len(nodes) < 2:
        return None
    busy, parts = [], {k: [] for k in ("user", "system", "irq", "softirq")}
    for a, b in _pairs(nodes):
        dt = b["wall"] - a["wall"]
        ca, cb = a["node"]["cpu"], b["node"]["cpu"]
        if dt <= 0 or not ca or not cb:
            continue
        busy.append(sum(cb.get(k, 0) - ca.get(k, 0) for k in _BUSY) / CLK_TCK / dt)
        for k in parts:
            parts[k].append((cb.get(k, 0) - ca.get(k, 0)) / CLK_TCK / dt)
    first, last = nodes[0]["node"], nodes[-1]["node"]
    span = nodes[-1]["wall"] - nodes[0]["wall"]

    def rate(key: str) -> float | None:
        if first.get(key) is None or last.get(key) is None or span <= 0:
            return None
        return (last[key] - first[key]) / span

    irq_delta = {k: last["interrupts"].get(k, 0) - v for k, v in first.get("interrupts", {}).items()}
    top = sorted(irq_delta.items(), key=lambda kv: -kv[1])[:10]
    psi = None
    if first.get("psi") and last.get("psi"):
        psi = {}
        for res in ("cpu", "memory", "io"):
            some = [s["node"]["psi"][res]["some"]["avg10"] for s in nodes if s["node"].get("psi")]
            psi[f"{res}_some_avg10"] = sum(some) / len(some)
            psi[f"{res}_some_total_us_per_s"] = ((last["psi"][res]["some"]["total"]
                                                  - first["psi"][res]["some"]["total"]) / span)
    used = [(s["node"]["mem_total_kib"] - s["node"]["mem_available_kib"]) / 1024 for s in nodes
            if s["node"].get("mem_total_kib") and s["node"].get("mem_available_kib")]
    cached = [s["node"]["cached_kib"] / 1024 for s in nodes if s["node"].get("cached_kib")]
    energy = None
    e0, e1 = nodes[0].get("energy"), nodes[-1].get("energy")
    if e0 and e1 and e0.get("source") and span > 0:
        joules = rapl.energy_delta_j(e0, e1)
        energy = {"source": e0["source"], "zones_w": {k: v / span for k, v in joules.items()},
                  "total_w": sum(joules.values()) / span if joules else None}
    harness = _rate([[s for s in nodes if s.get("self")]],
                    lambda s: s["self"].get("cpu_s"), 3600.0)
    busy_ci = stats.mean_ci(busy)
    daemon_cores = (proc["cpu_core_s_per_h"]["mean"] / 3600.0) if proc else 0.0
    non_daemon = None
    if busy_ci:
        non_daemon = busy_ci["mean"] - daemon_cores - (harness or 0) / 3600.0
    return {
        "busy_cores": busy_ci,
        **{f"{k}_cores": (sum(v) / len(v) if v else None) for k, v in parts.items()},
        "intr_per_s": rate("intr_total"),
        "softirq_per_s": rate("softirq_total"),
        "ctxt_per_s": rate("ctxt"),
        "top_irqs": [{"irq": k, "per_s": v / span if span > 0 else None} for k, v in top],
        "psi": psi,
        "mem_used_mib": sum(used) / len(used) if used else None,
        "cached_mib": sum(cached) / len(cached) if cached else None,
        "energy": energy,
        "harness_cpu_s_per_h": harness,
        "non_daemon_busy_cores": non_daemon,
        "loadavg1_max": max((s["node"].get("loadavg1") or 0) for s in nodes),
    }


def summarize_leg(leg_dir: Path) -> dict[str, Any]:
    meta = json.loads((leg_dir / "meta.json").read_text(encoding="utf-8"))
    samples = load_samples(leg_dir / "samples.jsonl")
    ph = meta.get("phases") or {}
    settle = _window(samples, None, ph.get("settle_end"))
    quiet = _window(samples, ph.get("settle_end"), ph.get("quiet_end"))
    load = _window(samples, ph.get("quiet_end"), ph.get("fwq_end"))
    proc = _process_summary(meta, settle, quiet, load) if meta["kind"] != "OFF" else None
    node = _node_summary(quiet, proc)
    status = meta.get("status", "ok")
    # Types the daemon kept although the drop-in disabled them (e.g. a failed name lookup):
    # NONE/SOLO then measure a different collector set than their label.
    extra = sorted(set(meta.get("verified_types") or []) - set(meta.get("intended_types") or []))
    if meta["kind"] != "OFF" and proc is None and status == "ok":
        status = "no_process_samples"
    if extra and status == "ok":
        status = "type_mismatch"
    contaminated = False
    if node and node.get("non_daemon_busy_cores") is not None:
        limit = max(CONTAMINATION_MIN_CORES, 0.01 * (node_online(quiet) or 1))
        contaminated = node["non_daemon_busy_cores"] > limit
    fwq_dir = leg_dir / "fwq"
    fwq_summary = fwq.summarize(fwq.load_quanta(fwq_dir)) if fwq_dir.is_dir() else None
    perf = None
    if (leg_dir / "perf.csv").is_file():
        events = perfstat.parse_perf_stat_csv((leg_dir / "perf.csv").read_text())
        perf = {"events": events, "derived": perfstat.derived_metrics(events),
                "seconds": meta.get("perf_seconds")}
    return {
        "leg_id": meta["leg_id"], "kind": meta["kind"], "block": meta["block"],
        "group": meta.get("group"), "brackets": meta.get("brackets"),
        "sample_freq": meta.get("sample_freq"), "status": status,
        "contaminated": contaminated,
        "intended_types": meta.get("intended_types", []),
        "verified_types": meta.get("verified_types", []),
        "missing_types": meta.get("missing_types", []),
        "extra_types": extra,
        "process": proc, "node": node, "fwq": fwq_summary, "perf": perf,
        "n_samples": len(samples), "n_quiet": len(quiet),
    }


def node_online(samples: list[dict[str, Any]]) -> float | None:
    xs = [s["node"].get("online_cpus") for s in samples if s.get("node")]
    xs = [float(x) for x in xs if x]
    return _median(xs)
