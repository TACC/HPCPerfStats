"""Node cost of rotating (multiplexing) the CPU performance counters the daemon opens.

When the open core events need more hardware counters than the CPU has (the monitor's own set,
or another perf user beside it), the kernel rotates them on a timer on every CPU. That cost is
interrupt time on every core, never the daemon's CPU, so the process numbers miss it.
c573-002 (Turin, 128 CPUs, 2026-11-23): 8 core events on 6 general counters; every leg with
x86_perf on had 0.389 more interrupt cores and 128,060 more interrupts/s than NONE, i.e.
1,000/s per CPU at 3.0 us each, about 3,900x the daemon's own x86_perf CPU.

Each block's legs with the CPU-counter group on (SOLO of that group, ALL) are paired with the
same block's NONE leg (daemon running, no collectors), so the difference is the group alone.
Uses node ``irq_cores`` and ``intr_per_s`` (all of /proc/stat's interrupts), which exist on every
architecture; x86 names the timer LOC, aarch64 numbers it.
"""

from __future__ import annotations

from typing import Any

CPU_COUNTER_GROUPS = ("x86_perf", "arm_cpu")
_KEYS = ("irq_cores", "intr_per_s", "intr_per_s_per_cpu", "us_per_interrupt")


def _usable(leg: dict[str, Any]) -> bool:
    node = leg.get("node") or {}
    return (leg.get("status") == "ok" and not leg.get("contaminated")
            and node.get("irq_cores") is not None and node.get("intr_per_s") is not None)


def _pair(on: dict[str, Any], none: dict[str, Any], online: int | None) -> dict[str, Any]:
    d_irq = on["node"]["irq_cores"] - none["node"]["irq_cores"]
    d_intr = on["node"]["intr_per_s"] - none["node"]["intr_per_s"]
    return {"leg_id": on["leg_id"], "none_leg_id": none["leg_id"],
            "irq_cores": d_irq, "intr_per_s": d_intr,
            "intr_per_s_per_cpu": d_intr / online if online else None,
            "us_per_interrupt": d_irq / d_intr * 1e6 if d_intr > 0 else None}


def _mean(pairs: list[dict[str, Any]], key: str) -> float | None:
    vals = [p[key] for p in pairs if p.get(key) is not None]
    return sum(vals) / len(vals) if vals else None


def summarize(legs: list[dict[str, Any]], online_cpus: int | None) -> dict[str, Any]:
    """``{"status": "ok", "group", "solo", "all", "pairs"}`` or ``{"status": "NOT_MEASURED", ...}``."""
    usable = [leg for leg in legs if _usable(leg)]
    group = next((g for g in CPU_COUNTER_GROUPS
                  if any(leg.get("kind") == "SOLO" and leg.get("group") == g for leg in usable)),
                 None)
    if group is None:
        return {"status": "NOT_MEASURED", "reason": "no usable SOLO leg of a CPU-counter group"}
    none_by_block = {leg["block"]: leg for leg in usable if leg.get("kind") == "NONE"}
    pairs: dict[str, list[dict[str, Any]]] = {"solo": [], "all": []}
    for leg in usable:
        none = none_by_block.get(leg.get("block"))
        if none is None:
            continue
        if leg.get("kind") == "SOLO" and leg.get("group") == group:
            pairs["solo"].append(_pair(leg, none, online_cpus))
        elif leg.get("kind") == "ALL":
            pairs["all"].append(_pair(leg, none, online_cpus))
    if not pairs["solo"]:
        return {"status": "NOT_MEASURED", "reason": f"no SOLO {group} leg with a same-block NONE"}
    out: dict[str, Any] = {"status": "ok", "group": group, "online_cpus": online_cpus}
    for kind, plist in pairs.items():
        out[kind] = {"n": len(plist), **{k: _mean(plist, k) for k in _KEYS}}
    out["pairs"] = pairs["solo"] + pairs["all"]
    return out


def _fmt(value: Any, spec: str) -> str:
    return "—" if value is None else format(value, spec)


def md_lines(rot: dict[str, Any] | None) -> list[str]:
    """Report section for ``profile.json`` ``counter_rotation``."""
    lines = ["## CPU counter rotation (kernel multiplexing, node interrupt cost)", ""]
    if not rot or rot.get("status") != "ok":
        return lines + [f"NOT_MEASURED ({(rot or {}).get('reason', 'not computed')})", ""]
    lines += [f"Group {rot['group']} on {rot.get('online_cpus')} CPUs, minus the same block's "
              "NONE leg. Charged to interrupt time on every core, not to the daemon.", "",
              "| Pairing | n | Interrupt cores | Interrupts/s | Per CPU /s | us per interrupt |",
              "| --- | --- | --- | --- | --- | --- |"]
    for kind in ("solo", "all"):
        s = rot[kind]
        lines.append(f"| {kind.upper()} - NONE | {s['n']} | {_fmt(s['irq_cores'], '.4f')} | "
                     f"{_fmt(s['intr_per_s'], ',.0f')} | {_fmt(s['intr_per_s_per_cpu'], ',.1f')} | "
                     f"{_fmt(s['us_per_interrupt'], '.2f')} |")
    return lines + [""]
