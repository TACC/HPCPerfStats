"""Parse ``metric-profiler:`` lines from an ``--enable-metric-profiler`` daemon's journal.

The profiler (src/metric_profiler.c) reports cumulative totals every 120 collect cycles and
once more at shutdown (``metric_profiler_report`` in monitor.c), so the **last** report in a
leg covers the whole leg. These numbers come from an instrumented build: they rank collector
types for diagnosis and are never headline numbers.
"""

from __future__ import annotations

import re
from typing import Any

from . import driver_map

_HEAD_RE = re.compile(r"metric-profiler: cycles=(\d+)")
_TYPE_RE = re.compile(r"metric-profiler:type=(\S+) calls=(\d+) wall_ns=(\d+) cpu_ns=(\d+) wait_ns=(\d+)")
_METRIC_RE = re.compile(
    r"metric-profiler:metric=(\S+) calls=(\d+) wall_ns=(\d+) cpu_ns=(\d+) wait_ns=(\d+)")


def _per_call(calls: int, wall: int, cpu: int, wait: int) -> dict[str, Any]:
    n = calls or 1
    return {"calls": calls, "wall_ns": wall, "cpu_ns": cpu, "wait_ns": wait,
            "wall_ms_per_call": wall / n / 1e6, "cpu_ms_per_call": cpu / n / 1e6,
            "wait_ms_per_call": wait / n / 1e6}


def parse_last_report(text: str) -> dict[str, Any] | None:
    """The last cumulative report: ``{cycles, types{...}, metrics_top[...]}`` or None."""
    lines = text.splitlines()
    heads = [i for i, line in enumerate(lines) if _HEAD_RE.search(line)]
    if not heads:
        return None
    start = heads[-1]
    cycles = int(_HEAD_RE.search(lines[start]).group(1))
    types: dict[str, dict[str, Any]] = {}
    metrics: list[dict[str, Any]] = []
    for line in lines[start + 1:]:
        m = _TYPE_RE.search(line)
        if m:
            types[m.group(1)] = _per_call(*(int(x) for x in m.groups()[1:]))
            continue
        m = _METRIC_RE.search(line)
        if m:
            metrics.append({"metric": m.group(1), **_per_call(*(int(x) for x in m.groups()[1:]))})
    ordered = dict(sorted(types.items(), key=lambda kv: -kv[1]["cpu_ns"]))
    return {"cycles": cycles, "types": ordered, "metrics_top": metrics}


def group_rollup(report: dict[str, Any], caps: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Per driver group: collect CPU/wall per cycle summed over member types."""
    cycles = report["cycles"] or 1
    out: dict[str, dict[str, Any]] = {}
    for name, t in report["types"].items():
        try:
            group = driver_map.group_for_type(name, caps)
        except driver_map.UnmappedTypeError:
            group = "unmapped"
        g = out.setdefault(group, {"types": [], "cpu_ns": 0, "wall_ns": 0})
        g["types"].append(name)
        g["cpu_ns"] += t["cpu_ns"]
        g["wall_ns"] += t["wall_ns"]
    for g in out.values():
        g["cpu_ms_per_cycle"] = g["cpu_ns"] / cycles / 1e6
        g["wall_ms_per_cycle"] = g["wall_ns"] / cycles / 1e6
    return dict(sorted(out.items(), key=lambda kv: -kv[1]["cpu_ns"]))
