"""Process-scoped broker traffic from ``ss -tinpH`` tcp_info (replaces /proc/net/dev bound)."""

from __future__ import annotations

import re
import subprocess
from typing import Any

_PID_RE = re.compile(r"pid=(\d+)")
_KV_RE = re.compile(r"\b(bytes_acked|bytes_received|bytes_sent|segs_out|segs_in):(\d+)")


def _ports(addr: str) -> int | None:
    _, _, port = addr.rpartition(":")
    return int(port) if port.isdigit() else None


def socket_counters(text: str, pid: int, port: int) -> dict[str, int] | None:
    """Sum tcp_info counters over ``pid``'s sockets whose peer or local port is ``port``."""
    lines = text.splitlines()
    total: dict[str, int] = {}
    sockets = 0
    i = 0
    while i < len(lines):
        head = lines[i]
        info = lines[i + 1] if i + 1 < len(lines) and lines[i + 1][:1].isspace() else ""
        i += 2 if info else 1
        fields = head.split()
        if len(fields) < 5:
            continue
        pids = {int(p) for p in _PID_RE.findall(head)}
        if pid not in pids or port not in (_ports(fields[3]), _ports(fields[4])):
            continue
        sockets += 1
        for key, value in _KV_RE.findall(info):
            total[key] = total.get(key, 0) + int(value)
    if not sockets:
        return None
    total["sockets"] = sockets
    return total


def read_ss(port: int, timeout: float = 2.0) -> str | None:
    """Run ``ss -tinpH`` filtered to one port; None when ss is missing or fails."""
    try:
        res = subprocess.run(
            ["ss", "-tinpH", f"( sport = :{port} or dport = :{port} )"],
            capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return res.stdout if res.returncode == 0 else None


def sample(pid: int | None, port: int) -> dict[str, Any] | None:
    if pid is None:
        return None
    text = read_ss(port)
    return socket_counters(text, pid, port) if text is not None else None
