"""``perf stat -x,`` CSV parsing; unsupported or uncounted events are NOT_MEASURED."""

from __future__ import annotations

from typing import Any

DEFAULT_EVENTS = ("task-clock", "cycles", "instructions", "cache-references", "cache-misses",
                  "context-switches", "cpu-migrations", "page-faults")


def _nm(reason: str) -> dict[str, str]:
    return {"status": "NOT_MEASURED", "reason": reason}


def parse_perf_stat_csv(text: str) -> dict[str, dict[str, Any]]:
    """Map event name to ``{value, unit}`` or a NOT_MEASURED slot."""
    out: dict[str, dict[str, Any]] = {}
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        fields = line.split(",")
        if len(fields) < 3:
            continue
        raw, unit, event = fields[0].strip(), fields[1].strip(), fields[2].strip()
        if raw.startswith("<"):
            out[event] = _nm(raw)
            continue
        try:
            value: float | int = int(raw) if raw.isdigit() else float(raw)
        except ValueError:
            out[event] = _nm(f"unparsable perf value {raw!r}")
            continue
        out[event] = {"value": value, "unit": unit}
    return out


def _val(events: dict[str, dict[str, Any]], name: str) -> float | None:
    slot = events.get(name)
    return None if slot is None or "value" not in slot else float(slot["value"])


def derived_metrics(events: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """IPC and cache-miss ratio only when both inputs were counted."""
    out: dict[str, dict[str, Any]] = {}
    ins, cyc = _val(events, "instructions"), _val(events, "cycles")
    out["ipc"] = {"value": ins / cyc} if ins is not None and cyc else _nm(
        "instructions or cycles not counted")
    miss, ref = _val(events, "cache-misses"), _val(events, "cache-references")
    out["cache_miss_ratio"] = {"value": miss / ref} if miss is not None and ref else _nm(
        "cache-misses or cache-references not counted")
    return out
