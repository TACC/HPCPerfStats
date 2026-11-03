"""Build ``profile.json`` (schema v1), ``report.md`` and ``profile_row.csv`` for one run.

Every metric slot is ``{value, unit, ci95, n, source}`` or ``{status: NOT_MEASURED, reason}``
— never 0 and never an estimate. One table, ``HEADLINE_METRICS``, names every key so the JSON,
the Markdown, the CSV row, and ``aggregate_profiles.py`` stay consistent.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import collector_matrix, fwq, leg_summary, profiler_lines, stats

PROFILE_SCHEMA_VERSION = 1
NOT_MEASURED = "NOT_MEASURED"

# key, label, unit
HEADLINE_METRICS: tuple[tuple[str, str, str], ...] = (
    ("daemon_cpu_core_s_per_h", "Daemon CPU (ALL)", "core-s/h"),
    ("daemon_cpu_core_s_per_day", "Daemon CPU (ALL)", "core-s/day"),
    ("daemon_cpu_pct_one_core", "Daemon CPU, % of one core", "%"),
    ("daemon_cpu_pct_node", "Daemon CPU, % of node capacity", "%"),
    ("daemon_user_s_per_h", "Daemon user CPU", "s/h"),
    ("daemon_system_s_per_h", "Daemon system CPU", "s/h"),
    ("daemon_cpu_1s_p50_pct", "1 s CPU p50", "% of one core"),
    ("daemon_cpu_1s_p95_pct", "1 s CPU p95", "% of one core"),
    ("daemon_cpu_1s_max_pct", "1 s CPU max", "% of one core"),
    ("cost_ms_per_fast_sample", "CPU per fast-tier sample", "CPU-ms"),
    ("cost_ms_per_full_sample", "CPU per full-tier sample", "CPU-ms"),
    ("cost_ms_per_send", "CPU per send", "CPU-ms"),
    ("daemon_rss_mib", "RSS steady state", "MiB"),
    ("daemon_pss_mib", "PSS steady state", "MiB"),
    ("daemon_uss_mib", "USS steady state", "MiB"),
    ("daemon_vmhwm_mib", "VmHWM", "MiB"),
    ("daemon_threads", "Threads", "count"),
    ("daemon_fds", "Open FDs", "count"),
    ("daemon_perf_event_fds", "perf_event FDs held", "count"),
    ("daemon_wakeups_per_s", "Context switches (vol+invol)", "/s"),
    ("daemon_minflt_per_s", "Minor faults", "/s"),
    ("daemon_majflt_per_s", "Major faults", "/s"),
    ("daemon_read_bytes_per_h", "Block read", "bytes/h"),
    ("daemon_write_bytes_per_h", "Block write", "bytes/h"),
    ("daemon_syscalls_per_s", "Read+write syscalls", "/s"),
    ("broker_bytes_acked_per_h", "Broker bytes sent (acked)", "bytes/h"),
    ("broker_bytes_received_per_h", "Broker bytes received", "bytes/h"),
    ("broker_segs_out_per_h", "Broker segments out", "segments/h"),
    ("broker_bytes_per_send", "Broker bytes per send", "bytes"),
    ("daemon_ipc", "Daemon IPC", "instructions/cycle"),
    ("daemon_cache_miss_ratio", "Daemon cache-miss ratio", "ratio"),
    ("startup_wall_s", "Startup wall time to Enabled types", "s"),
    ("startup_cpu_s", "Startup CPU above steady state", "CPU-s"),
    ("startup_peak_rss_mib", "Startup peak RSS", "MiB"),
    ("daemon_cpu_core_s_per_h_under_load", "Daemon CPU under full FWQ load", "core-s/h"),
    ("node_busy_cores_delta", "Node busy CPU, ALL − OFF", "cores"),
    ("node_irq_per_s_delta", "Interrupts, ALL − OFF", "/s"),
    ("node_softirq_per_s_delta", "Softirqs, ALL − OFF", "/s"),
    ("node_ctxt_per_s_delta", "Node context switches, ALL − OFF", "/s"),
    ("node_psi_cpu_some_avg10_delta", "PSI cpu some avg10, ALL − OFF", "%"),
    ("node_mem_used_mib_delta", "Node memory used, ALL − OFF", "MiB"),
    ("node_power_w_delta", "Package+DRAM power, ALL − OFF", "W"),
    ("harness_cpu_s_per_h", "Harness self-cost (sampler)", "core-s/h"),
    ("fwq_mean_slowdown_pct", "FWQ mean slowdown, ALL vs OFF", "%"),
    ("fwq_p99_ratio", "FWQ p99 quantum, ALL / OFF", "ratio"),
    ("fwq_p999_ratio", "FWQ p99.9 quantum, ALL / OFF", "ratio"),
    ("fwq_max_detour_us", "FWQ max detour (ALL)", "µs"),
    ("fwq_frac_above_off_p99", "FWQ quanta above OFF p99 (ALL)", "fraction"),
    ("fwq_none_slowdown_pct", "FWQ mean slowdown, NONE vs OFF", "%"),
    ("gpu_interference", "GPU-side interference", "-"),
)
CSV_KEYS = ("daemon_cpu_core_s_per_h", "daemon_cpu_pct_node", "daemon_cpu_1s_max_pct",
            "daemon_uss_mib", "daemon_rss_mib", "broker_bytes_acked_per_h",
            "node_busy_cores_delta", "node_power_w_delta", "fwq_mean_slowdown_pct",
            "fwq_frac_above_off_p99", "daemon_perf_event_fds")


def measured(value: float, unit: str, source: str, ci: dict[str, Any] | None = None,
             n: int | None = None) -> dict[str, Any]:
    ci95 = [ci["lo"], ci["hi"]] if ci and ci.get("lo") is not None else None
    return {"value": value, "unit": unit, "ci95": ci95, "n": n, "source": source}


def not_measured(reason: str) -> dict[str, Any]:
    return {"status": NOT_MEASURED, "reason": reason}


def _load_legs(run_dir: Path) -> list[dict[str, Any]]:
    legs = []
    base = run_dir / "raw" / "legs"
    if not base.is_dir():
        return legs
    for leg_dir in sorted(base.iterdir()):
        if (leg_dir / "meta.json").is_file():
            summary = leg_summary.summarize_leg(leg_dir)
            (leg_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
            legs.append(summary)
        elif (leg_dir / "summary.json").is_file():
            legs.append(json.loads((leg_dir / "summary.json").read_text()))
    return legs


def _usable(leg: dict[str, Any]) -> bool:
    """ok and not contaminated; a smoke run (`not_idle`) keeps contaminated legs, flagged."""
    return leg.get("status") == "ok" and (not leg.get("contaminated") or leg.get("smoke_run"))


def _combine(legs: list[dict[str, Any]], getter: Callable[[dict[str, Any]], Any]) -> dict[str, Any] | None:
    vals = []
    for leg in legs:
        v = getter(leg)
        if v is None:
            continue
        vals.append(v if isinstance(v, dict) else {"mean": float(v), "lo": None, "hi": None})
    return stats.combine_replicates(vals)


def _proc(key: str) -> Callable[[dict[str, Any]], Any]:
    return lambda leg: (leg.get("process") or {}).get(key)


def _node(key: str) -> Callable[[dict[str, Any]], Any]:
    return lambda leg: (leg.get("node") or {}).get(key)


def _drift(legs: list[dict[str, Any]], kind: str, getter) -> bool | None:
    cis = [getter(leg) for leg in legs if leg["kind"] == kind and _usable(leg)]
    cis = [c for c in cis if isinstance(c, dict) and c.get("lo") is not None]
    if len(cis) < 2:
        return None
    return any(not stats.ci_overlap(a, b) for i, a in enumerate(cis) for b in cis[i + 1:])


def _metrics(legs: list[dict[str, Any]], flags: dict[str, Any], gpus: list[Any]) -> dict[str, Any]:
    all_legs = [leg for leg in legs if leg["kind"] == "ALL" and _usable(leg) and leg.get("process")]
    off_legs = [leg for leg in legs if leg["kind"] == "OFF" and _usable(leg)]
    none_legs = [leg for leg in legs if leg["kind"] == "NONE" and _usable(leg) and leg.get("process")]
    m: dict[str, Any] = {}
    no_all = "no usable ALL leg" + (" (run interrupted)" if flags.get("interrupted") else "")
    src_all = "raw/legs/*ALL*/samples.jsonl"

    def put(key: str, unit: str, ci: dict[str, Any] | None, reason: str, src: str = src_all,
            scale: float = 1.0) -> None:
        if ci is None or ci.get("mean") is None:
            m[key] = not_measured(reason)
            return
        scaled = {k: (v * scale if k in ("mean", "lo", "hi") and v is not None else v)
                  for k, v in ci.items()}
        m[key] = measured(scaled["mean"], unit, src, scaled, ci.get("n"))

    units = {k: u for k, _, u in HEADLINE_METRICS}
    cpu = _combine(all_legs, _proc("cpu_core_s_per_h"))
    put("daemon_cpu_core_s_per_h", units["daemon_cpu_core_s_per_h"], cpu, no_all)
    put("daemon_cpu_core_s_per_day", "core-s/day", cpu, no_all, scale=24.0)
    simple = {
        "daemon_cpu_pct_one_core": "cpu_pct_one_core", "daemon_cpu_pct_node": "cpu_pct_node",
        "daemon_user_s_per_h": "user_s_per_h", "daemon_system_s_per_h": "system_s_per_h",
        "cost_ms_per_fast_sample": "ms_per_fast_sample", "cost_ms_per_full_sample": "ms_per_full_sample",
        "cost_ms_per_send": "ms_per_send", "daemon_rss_mib": "rss_mib", "daemon_pss_mib": "pss_mib",
        "daemon_uss_mib": "uss_mib", "daemon_vmhwm_mib": "vmhwm_mib", "daemon_threads": "threads",
        "daemon_fds": "fds", "daemon_perf_event_fds": "perf_event_fds",
        "daemon_wakeups_per_s": "wakeups_per_s", "daemon_minflt_per_s": "minflt_per_s",
        "daemon_majflt_per_s": "majflt_per_s", "daemon_read_bytes_per_h": "read_bytes_per_h",
        "daemon_write_bytes_per_h": "write_bytes_per_h", "daemon_syscalls_per_s": "syscalls_per_s",
        "startup_wall_s": "startup_wall_s", "startup_cpu_s": "init_cpu_s",
        "startup_peak_rss_mib": "init_peak_rss_mib",
        "daemon_cpu_core_s_per_h_under_load": "cpu_core_s_per_h_under_load",
    }
    for key, pkey in simple.items():
        reason = no_all if not all_legs else f"{pkey} not available in ALL legs"
        if key == "daemon_cpu_core_s_per_h_under_load" and all_legs:
            reason = "no FWQ phase ran on ALL legs"
        put(key, units[key], _combine(all_legs, _proc(pkey)), reason)
    for q in ("p50", "p95", "max"):
        put(f"daemon_cpu_1s_{q}_pct", "% of one core",
            _combine(all_legs, lambda leg, q=q: ((leg.get("process") or {}).get("cpu_1s_pct") or {}).get(q)),
            no_all)

    broker_seen = any((leg.get("process") or {}).get("broker_socket_seen") for leg in all_legs)
    for key, pkey in (("broker_bytes_acked_per_h", "broker_bytes_acked_per_h"),
                      ("broker_bytes_received_per_h", "broker_bytes_received_per_h"),
                      ("broker_segs_out_per_h", "broker_segs_out_per_h"),
                      ("broker_bytes_per_send", "broker_bytes_per_send")):
        if all_legs and not broker_seen:
            m[key] = not_measured("broker_unreachable: no AMQP socket for the daemon PID in ss")
        else:
            put(key, units[key], _combine(all_legs, _proc(pkey)), no_all)

    def perf_val(name: str):
        def g(leg):
            perf = leg.get("perf") or {}
            slot = (perf.get("derived") or {}).get(name) or {}
            return slot.get("value")
        return g
    for key, name in (("daemon_ipc", "ipc"), ("daemon_cache_miss_ratio", "cache_miss_ratio")):
        reason = flags.get("perf_reason") or "perf stat not run or events not counted"
        put(key, units[key], _combine(all_legs, perf_val(name)), reason, src="raw/legs/*ALL*/perf.csv")

    off_reason = ("off_legs_skipped" if flags.get("off_legs_skipped") else "no usable OFF leg")
    node_pairs = (("node_busy_cores_delta", "busy_cores"), ("node_irq_per_s_delta", "intr_per_s"),
                  ("node_softirq_per_s_delta", "softirq_per_s"), ("node_ctxt_per_s_delta", "ctxt_per_s"),
                  ("node_mem_used_mib_delta", "mem_used_mib"))
    for key, nkey in node_pairs:
        a, o = _combine(all_legs, _node(nkey)), _combine(off_legs, _node(nkey))
        put(key, units[key], stats.diff_ci(a, o) if a and o else None,
            off_reason if not off_legs else no_all, src="raw/legs/*/samples.jsonl (ALL − OFF)")
    psi_get = lambda leg: ((leg.get("node") or {}).get("psi") or {}).get("cpu_some_avg10")  # noqa: E731
    a, o = _combine(all_legs, psi_get), _combine(off_legs, psi_get)
    if a and o:
        put("node_psi_cpu_some_avg10_delta", "%", stats.diff_ci(a, o), "")
    else:
        m["node_psi_cpu_some_avg10_delta"] = not_measured(
            off_reason if not off_legs else "PSI not available (kernel without CONFIG_PSI or psi=0)")
    pw = lambda leg: (((leg.get("node") or {}).get("energy")) or {}).get("total_w")  # noqa: E731
    a, o = _combine(all_legs, pw), _combine(off_legs, pw)
    if a and o:
        put("node_power_w_delta", "W", stats.diff_ci(a, o), "", src="powercap/hwmon energy counters")
    else:
        m["node_power_w_delta"] = not_measured(
            off_reason if not off_legs else "no RAPL powercap or hwmon energy counters readable")
    put("harness_cpu_s_per_h", "core-s/h", _combine([leg for leg in legs if _usable(leg)],
                                                     _node("harness_cpu_s_per_h")),
        "no sampler self-cost recorded", src="sampler /proc/self/schedstat")

    on_fwq = [leg for leg in all_legs if leg.get("fwq")]
    off_fwq = [leg for leg in off_legs if leg.get("fwq")]
    none_fwq = [leg for leg in none_legs if leg.get("fwq")]
    if on_fwq and off_fwq:
        cmps = [fwq.compare(on["fwq"], off["fwq"], {}) for on in on_fwq for off in off_fwq]
        frac = [leg["fwq"].get("frac_above_off_p99") for leg in on_fwq]
        src = "raw/legs/*/fwq/fwq_cpu*.bin"
        for key, ckey in (("fwq_mean_slowdown_pct", "mean_slowdown_pct"), ("fwq_p99_ratio", "p99_ratio"),
                          ("fwq_p999_ratio", "p999_ratio")):
            put(key, units[key], stats.combine_replicates(
                [{"mean": c[ckey], "lo": None, "hi": None} for c in cmps]), "", src=src)
        put("fwq_max_detour_us", "µs", stats.combine_replicates(
            [{"mean": leg["fwq"]["max_detour_ns"] / 1000.0, "lo": None, "hi": None} for leg in on_fwq]),
            "", src=src)
        fr = [f for f in frac if f is not None]
        put("fwq_frac_above_off_p99", "fraction", stats.combine_replicates(
            [{"mean": f, "lo": None, "hi": None} for f in fr]) if fr else None,
            "FWQ quanta not compared against OFF", src=src)
    else:
        reason = off_reason if not off_fwq else "no FWQ phase on ALL legs"
        for key in ("fwq_mean_slowdown_pct", "fwq_p99_ratio", "fwq_p999_ratio", "fwq_max_detour_us",
                    "fwq_frac_above_off_p99"):
            m[key] = not_measured(reason)
    if none_fwq and off_fwq:
        cmps = [fwq.compare(n["fwq"], o["fwq"], {}) for n in none_fwq for o in off_fwq]
        put("fwq_none_slowdown_pct", "%", stats.combine_replicates(
            [{"mean": c["mean_slowdown_pct"], "lo": None, "hi": None} for c in cmps]), "",
            src="raw/legs/*/fwq/fwq_cpu*.bin")
    else:
        m["fwq_none_slowdown_pct"] = not_measured(off_reason if not off_fwq else "no FWQ on NONE legs")
    m["gpu_interference"] = not_measured("out of scope (CPU-only study)" if gpus else
                                         "no GPU on node; out of scope (CPU-only study)")
    for key, _label, _unit in HEADLINE_METRICS:
        m.setdefault(key, not_measured("not computed"))
    return m


def _attach_frac_above(legs: list[dict[str, Any]], run_dir: Path) -> None:
    """Fraction of ALL quanta above the run's pooled OFF p99 (needs the raw quanta)."""
    off = [leg for leg in legs if leg["kind"] == "OFF" and leg.get("fwq")]
    if not off:
        return
    pooled: dict[int, list[int]] = {}
    for leg in off:
        for cpu, vals in fwq.load_quanta(run_dir / "raw" / "legs" / leg["leg_id"] / "fwq").items():
            pooled.setdefault(cpu, []).extend(vals)
    off_sum = fwq.summarize(pooled)
    if off_sum is None:
        return
    for leg in legs:
        if leg["kind"] == "ALL" and leg.get("fwq"):
            q = fwq.load_quanta(run_dir / "raw" / "legs" / leg["leg_id"] / "fwq")
            cmp = fwq.compare(leg["fwq"], off_sum, q)
            if cmp:
                leg["fwq"]["frac_above_off_p99"] = cmp["frac_above_off_p99"]


PROFILE_LABEL = ("instrumented build (--enable-metric-profiler), not production; ranks collector "
                 "types for diagnosis only and never feeds a headline number")


def _collector_profile(run_dir: Path, legs: list[dict[str, Any]], planned: list[str],
                       caps: Any) -> dict[str, Any]:
    """Parse the PROFILE leg's journal (last cumulative metric-profiler report)."""
    leg = next((leg for leg in legs if leg["kind"] == "PROFILE"), None)
    if leg is None:
        if any("-PROFILE-" in lid for lid in planned):
            return not_measured("run interrupted before the PROFILE leg")
        return not_measured("--profile-collectors not requested")
    journal = run_dir / "raw" / "legs" / leg["leg_id"] / "journal.txt"
    report = profiler_lines.parse_last_report(journal.read_text()) if journal.is_file() else None
    if report is None:
        return not_measured("no metric-profiler report in the PROFILE leg journal "
                            f"(raw/legs/{leg['leg_id']}/journal.txt)")
    caps = caps if isinstance(caps, dict) and "configure" in caps else None
    return {"status": "ok", "label": PROFILE_LABEL, "cycles": report["cycles"],
            "types": report["types"], "groups": profiler_lines.group_rollup(report, caps),
            "metrics_top": report["metrics_top"],
            "source": f"raw/legs/{leg['leg_id']}/journal.txt"}


def build_profile(run_dir: Path) -> dict[str, Any]:
    run_dir = Path(run_dir)
    raw = run_dir / "raw"
    run = json.loads((raw / "run.json").read_text()) if (raw / "run.json").is_file() else {}
    prov = json.loads((raw / "provenance.json").read_text()) if (raw / "provenance.json").is_file() else {}
    legs = _load_legs(run_dir)
    _attach_frac_above(legs, run_dir)
    done = {leg["leg_id"] for leg in legs}
    missing = [lid for lid in run.get("planned_leg_ids", []) if lid not in done]
    flags = dict(run.get("flags") or {})
    flags["interrupted"] = bool(missing)
    if flags.get("not_idle"):
        for leg in legs:
            leg["smoke_run"] = True
    groups = run.get("groups") or {}
    matrix = collector_matrix.build_matrix(legs, groups, run.get("active_types") or [],
                                           interrupted=bool(missing))
    drift = {}
    for kind in ("OFF", "NONE", "ALL"):
        getter = _node("busy_cores") if kind == "OFF" else _proc("cpu_core_s_per_h")
        drift[kind] = bool(_drift(legs, kind, getter))
    host = prov.get("host") or {}
    profile = {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "harness_version": run.get("harness_version"),
        "run_dir": str(run_dir),
        "host": host,
        "flags": flags,
        "not_idle": flags.get("not_idle"),
        "run_complete": not missing and bool(run.get("planned_leg_ids")),
        "legs_missing": missing,
        "baseline_drift": drift,
        "metrics": _metrics(legs, flags, host.get("gpus") or []),
        "collector_matrix": matrix,
        "collector_profile": _collector_profile(
            run_dir, legs, run.get("planned_leg_ids", []),
            ((prov.get("build") or {}).get("build_capabilities"))),
        "schedule": run.get("schedule"),
        "active_types": run.get("active_types"),
        "groups": groups,
        "legs": [{k: leg.get(k) for k in ("leg_id", "kind", "block", "group", "status", "contaminated",
                                          "verified_types", "missing_types")} for leg in legs],
        "provenance": prov,
    }
    return profile


def _fmt(slot: dict[str, Any]) -> str:
    if slot.get("status") == NOT_MEASURED:
        return f"NOT_MEASURED ({slot.get('reason')})"
    v = slot["value"]
    txt = f"{v:.4g}" if isinstance(v, float) else str(v)
    if slot.get("ci95"):
        txt += f" [{slot['ci95'][0]:.4g}, {slot['ci95'][1]:.4g}]"
    return f"{txt} {slot['unit']}"


def _md(profile: dict[str, Any]) -> str:
    host = profile.get("host") or {}
    lines = [f"# hpcperfstatsd overhead profile — {host.get('short', '?')} ({host.get('arch', '?')}, "
             f"{host.get('cpu_model', '?')})", ""]
    if profile.get("not_idle"):
        lines += [f"> **not_idle: {profile['not_idle']}** — do not cite these numbers.", ""]
    if not profile.get("run_complete"):
        lines += [f"> Partial run; missing legs: {', '.join(profile.get('legs_missing') or []) or 'n/a'}", ""]
    drift = [k for k, v in (profile.get("baseline_drift") or {}).items() if v]
    if drift:
        lines += [f"> baseline_drift for {', '.join(drift)}: comparisons involving them are not citable.", ""]
    lines += ["## Headline metrics", "", "| Metric | Value |", "| --- | --- |"]
    for key, label, _unit in HEADLINE_METRICS:
        lines.append(f"| {label} (`{key}`) | {_fmt(profile['metrics'][key])} |")
    mx = profile.get("collector_matrix") or {}
    lines += ["", "## Per-driver cost (SOLO − NONE, paired within block)", "",
              "| Driver group | Types | CPU core-s/h [95% CI] | Share of ALL | ms/fast | ms/full | "
              "USS Δ MiB | Broker Δ B/h | Wakeups Δ/s | perf FDs Δ | Init Δ CPU-s | Notes |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]

    def num(v: Any) -> str:
        return "—" if v is None else f"{v:.4g}"
    for r in mx.get("rows", []):
        if r["status"] != "ok":
            lines.append(f"| {r['group']} | {', '.join(r['types'])} | NOT_MEASURED ({r.get('reason')}) "
                         "| | | | | | | | | |")
            continue
        c = r["cpu_core_s_per_h"]
        ci = f" [{c['lo']:.4g}, {c['hi']:.4g}]" if c.get("lo") is not None else ""
        lines.append(f"| {r['group']} | {', '.join(r['types'])} | {c['mean']:.4g}{ci} | "
                     f"{num(r.get('share_of_all'))} | {num(r.get('ms_per_fast_sample'))} | "
                     f"{num(r.get('ms_per_full_sample'))} | {num(r.get('uss_mib_delta'))} | "
                     f"{num(r.get('broker_bytes_per_h_delta'))} | {num(r.get('wakeups_per_s_delta'))} | "
                     f"{num(r.get('perf_event_fds_delta'))} | {num(r.get('init_cpu_s_delta'))} | "
                     f"{'; '.join(r['notes'])} |")
    fw = mx.get("framework") or {}
    lines.append("")
    lines.append("Framework (NONE): " + (f"{fw['cpu_core_s_per_h']['mean']:.4g} core-s/h"
                                         if fw.get("status") == "ok" else f"NOT_MEASURED ({fw.get('reason')})"))
    res = mx.get("residual") or {}
    if res.get("cpu_core_s_per_h"):
        lines.append(f"Interaction residual: {res['cpu_core_s_per_h']['mean']:.4g} core-s/h"
                     + (" — large: cite per-driver numbers as approximate" if res.get("large") else ""))
    else:
        lines.append(f"Interaction residual: NOT_MEASURED ({res.get('reason')})")
    lines += ["", "Groups include each type's incidental /proc reads (decision 11). Bursts that "
              "coincide with a sample are counted as that sample, so CPU per send covers only "
              "unaligned sends.", ""]
    cp = profile.get("collector_profile") or {}
    lines += ["## Instrumented collector profile", ""]
    if cp.get("status") == "ok":
        lines += [f"> {cp['label']}", "", f"Cycles: {cp['cycles']}", "",
                  "| Type | Calls | CPU ms/call | Wall ms/call | Wait ms/call |",
                  "| --- | --- | --- | --- | --- |"]
        for name, ty in cp["types"].items():
            lines.append(f"| {name} | {ty['calls']} | {ty['cpu_ms_per_call']:.4g} | "
                         f"{ty['wall_ms_per_call']:.4g} | {ty['wait_ms_per_call']:.4g} |")
    else:
        lines.append(f"NOT_MEASURED ({cp.get('reason')})")
    lines += ["", "## Legs", "", "| Leg | Status | Contaminated | Verified types |",
              "| --- | --- | --- | --- |"]
    for leg in profile.get("legs", []):
        lines.append(f"| {leg['leg_id']} | {leg['status']} | {leg['contaminated']} | "
                     f"{', '.join(leg.get('verified_types') or [])} |")
    lines.append("")
    return "\n".join(lines)


def profile_row(profile: dict[str, Any]) -> dict[str, Any]:
    host = profile.get("host") or {}
    row: dict[str, Any] = {"host": host.get("short"), "arch": host.get("arch"),
                           "cpu_model": host.get("cpu_model"),
                           "not_idle": profile.get("not_idle") or ""}
    for key in CSV_KEYS:
        slot = profile["metrics"][key]
        row[key] = "" if slot.get("status") == NOT_MEASURED else slot["value"]
    return row


def write_outputs(run_dir: Path) -> dict[str, Path]:
    run_dir = Path(run_dir)
    profile = build_profile(run_dir)
    out = {"profile": run_dir / "profile.json", "report": run_dir / "report.md",
           "row": run_dir / "profile_row.csv"}
    out["profile"].write_text(json.dumps(profile, indent=2, default=str) + "\n")
    out["report"].write_text(_md(profile))
    row = profile_row(profile)
    with out["row"].open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(row))
        w.writeheader()
        w.writerow(row)
    return out
