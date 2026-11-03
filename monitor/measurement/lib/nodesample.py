"""Node-wide /proc sampling: CPU busy split, interrupts, softirqs, PSI, memory, load."""

from __future__ import annotations

from pathlib import Path
from typing import Any

_CPU_FIELDS = ("user", "nice", "system", "idle", "iowait", "irq", "softirq", "steal")


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def parse_proc_stat(text: str) -> dict[str, Any]:
    """Aggregate ``cpu`` jiffies plus ctxt / intr / procs_running from ``/proc/stat``."""
    out: dict[str, Any] = {"cpu": {}, "cpu_count": 0, "ctxt": None, "intr_total": None,
                           "procs_running": None, "softirq_total": None}
    for line in text.splitlines():
        fields = line.split()
        if not fields:
            continue
        head = fields[0]
        if head == "cpu":
            # zip() without strict=: nodes run python3.9 (noqa B905)
            out["cpu"] = {name: int(v) for name, v in zip(_CPU_FIELDS, fields[1:])}  # noqa: B905
        elif head.startswith("cpu") and head[3:].isdigit():
            out["cpu_count"] += 1
        elif head == "ctxt":
            out["ctxt"] = int(fields[1])
        elif head == "intr":
            out["intr_total"] = int(fields[1])
        elif head == "softirq":
            out["softirq_total"] = int(fields[1])
        elif head == "procs_running":
            out["procs_running"] = int(fields[1])
    return out


def parse_psi(text: str) -> dict[str, dict[str, float]]:
    """Parse one ``/proc/pressure/<resource>`` file."""
    out: dict[str, dict[str, float]] = {}
    for line in text.splitlines():
        fields = line.split()
        if not fields:
            continue
        vals: dict[str, float] = {}
        for kv in fields[1:]:
            key, _, value = kv.partition("=")
            vals[key] = int(value) if key == "total" else float(value)
        out[fields[0]] = vals
    return out


def parse_interrupts(text: str) -> dict[str, int]:
    """Per-source totals summed over CPUs from ``/proc/interrupts`` or ``/proc/softirqs``."""
    out: dict[str, int] = {}
    lines = text.splitlines()
    if not lines:
        return out
    ncpu = len(lines[0].split())
    for line in lines[1:]:
        name, sep, rest = line.partition(":")
        if not sep:
            continue
        counts = []
        for tok in rest.split()[:ncpu]:
            if not tok.isdigit():
                break
            counts.append(int(tok))
        if counts:
            out[name.strip()] = sum(counts)
    return out


def parse_meminfo(text: str) -> dict[str, int]:
    """``/proc/meminfo`` values in kiB."""
    out: dict[str, int] = {}
    for line in text.splitlines():
        key, sep, rest = line.partition(":")
        if sep and rest.split():
            out[key.strip()] = int(rest.split()[0])
    return out


def parse_cpu_list(text: str) -> list[int]:
    """Expand a kernel CPU list such as ``0-3,8``."""
    cpus: list[int] = []
    for part in text.strip().split(","):
        if not part:
            continue
        lo, _, hi = part.partition("-")
        cpus.extend(range(int(lo), int(hi or lo) + 1))
    return cpus


class NodeSampler:
    """Reads node files each call; PSI absent (kernel without CONFIG_PSI) gives ``psi: None``."""

    def __init__(self, proc_root: Path | str = "/proc", sys_root: Path | str = "/sys"):
        self.proc = Path(proc_root)
        self.sys = Path(sys_root)

    def sample(self) -> dict[str, Any]:
        stat = parse_proc_stat(_read(self.proc / "stat") or "")
        psi: dict[str, Any] | None = {}
        for res in ("cpu", "memory", "io"):
            text = _read(self.proc / "pressure" / res)
            if text is None:
                psi = None
                break
            psi[res] = parse_psi(text)
        load = (_read(self.proc / "loadavg") or "").split()
        online = _read(self.sys / "devices" / "system" / "cpu" / "online")
        mem = parse_meminfo(_read(self.proc / "meminfo") or "")
        return {
            "cpu": stat["cpu"],
            "cpu_count": stat["cpu_count"],
            "online_cpus": len(parse_cpu_list(online)) if online else stat["cpu_count"],
            "ctxt": stat["ctxt"],
            "intr_total": stat["intr_total"],
            "softirq_total": stat["softirq_total"],
            "procs_running": stat["procs_running"],
            "interrupts": parse_interrupts(_read(self.proc / "interrupts") or ""),
            "softirqs": parse_interrupts(_read(self.proc / "softirqs") or ""),
            "psi": psi,
            "loadavg1": float(load[0]) if load else None,
            "mem_total_kib": mem.get("MemTotal"),
            "mem_available_kib": mem.get("MemAvailable"),
            "cached_kib": mem.get("Cached"),
        }
