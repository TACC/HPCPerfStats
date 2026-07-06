"""Live host probes shared by build_message_expectations and live spot-check."""
from __future__ import annotations

import re
import socket
from pathlib import Path

PROC_STAT_CPU_RE = re.compile(r"^cpu(\d+)\s")
_NODE_MEMINFO_RE = re.compile(r"^Node \d+ (\w+):\s+(\d+)")


def _read_lines(path: str) -> list[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as fd:
            return fd.readlines()
    except OSError:
        return []


def probe_host_fqdn() -> str:
    try:
        return socket.getfqdn()
    except OSError:
        return socket.gethostname()


def probe_cpu_devices() -> list[str]:
    devs: list[str] = []
    for line in _read_lines("/proc/stat"):
        m = PROC_STAT_CPU_RE.match(line)
        if m:
            devs.append(m.group(1))
    return devs


def probe_net_devices() -> list[str]:
    net_base = Path("/sys/class/net")
    if not net_base.is_dir():
        return []
    skip = {"lo"}
    return sorted(
        p.name
        for p in net_base.iterdir()
        if p.is_dir() and p.name not in skip
    )


def probe_ib_devices() -> list[str]:
    ib_base = Path("/sys/class/infiniband")
    if not ib_base.is_dir():
        return []
    devs: list[str] = []
    for hca in sorted(ib_base.iterdir()):
        if not hca.is_dir():
            continue
        ports = hca / "ports"
        if not ports.is_dir():
            continue
        for port in sorted(ports.iterdir()):
            if port.is_dir() and port.name.isdigit():
                devs.append(f"{hca.name}.{port.name}")
    return devs


def probe_block_devices() -> list[str]:
    devs: list[str] = []
    for line in _read_lines("/proc/diskstats"):
        parts = line.split()
        if len(parts) < 3:
            continue
        major = int(parts[0])
        if major < 259 and major != 8 and major != 252:
            continue
        name = parts[2]
        if name and not name[-1].isdigit():
            devs.append(name)
    return sorted(set(devs))


def probe_numa_node_devices() -> list[str]:
    """NUMA node ids used as host_mem/host_numa dev (see mem.c, numa.c)."""
    base = Path("/sys/devices/system/node")
    if not base.is_dir():
        return []
    devs: list[str] = []
    for p in sorted(base.iterdir()):
        if not p.is_dir() or not p.name.startswith("node"):
            continue
        node_id = p.name[4:]
        if node_id.isdigit():
            devs.append(node_id)
    return devs


def default_devices_for_type(type_name: str) -> list[str] | None:
    if type_name == "host_cpu":
        cpus = probe_cpu_devices()
        return cpus if cpus else None
    if type_name == "host_net":
        return probe_net_devices()
    if type_name == "host_ib":
        return probe_ib_devices()
    if type_name == "host_block":
        return probe_block_devices()
    if type_name in ("host_mem", "host_numa"):
        nodes = probe_numa_node_devices()
        return nodes if nodes else None
    if type_name in (
        "host_vm",
        "host_ps",
        "host_vfs",
        "host_nfs",
        "host_sysv_shm",
        "host_tmpfs",
        "host_roofline_peak",
    ):
        return ["-"]
    if type_name == "host_proc":
        return None
    return None


def probe_loadavg_scaled() -> dict[str, int]:
    """Monitor host_ps stores load ×100 (see ps.c)."""
    for line in _read_lines("/proc/loadavg"):
        parts = line.split()
        if len(parts) < 3:
            continue
        out: dict[str, int] = {}
        for idx, key in enumerate(("load_1", "load_5", "load_15")):
            whole, _, frac = parts[idx].partition(".")
            try:
                out[key] = int(whole) * 100 + int((frac + "00")[:2])
            except ValueError:
                continue
        return out
    return {}


def probe_numa_mem_kb(node: str) -> dict[str, int]:
    path = f"/sys/devices/system/node/node{node}/meminfo"
    vals: dict[str, int] = {}
    for line in _read_lines(path):
        m = _NODE_MEMINFO_RE.match(line.strip())
        if not m:
            continue
        key = m.group(1)
        if key in ("MemTotal", "MemFree"):
            vals[key] = int(m.group(2))
    return vals


def probe_net_stat(iface: str, stat: str) -> int | None:
    p = Path(f"/sys/class/net/{iface}/statistics/{stat}")
    try:
        return int(p.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def probe_uptime_seconds() -> float | None:
    for line in _read_lines("/proc/uptime"):
        parts = line.split()
        if parts:
            try:
                return float(parts[0])
            except ValueError:
                return None
    return None


def probe_machine_arch() -> str | None:
    try:
        return Path("/proc/sys/kernel/arch").read_text(encoding="utf-8").strip()
    except OSError:
        import platform

        return platform.machine() or None
