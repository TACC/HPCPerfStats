"""Parse /proc snapshots for daemon RSS/USS and I/O / net counters."""

from __future__ import annotations

import re
from typing import Any

_STATUS_KEYS = (
    "VmRSS",
    "VmHWM",
    "VmSize",
    "RssAnon",
    "RssFile",
    "RssShmem",
    "Threads",
)
_IO_KEYS = (
    "rchar",
    "wchar",
    "syscr",
    "syscw",
    "read_bytes",
    "write_bytes",
    "cancelled_write_bytes",
)
_SMAPS_KEYS = (
    "Rss",
    "Pss",
    "Pss_Anon",
    "Pss_File",
    "Private_Clean",
    "Private_Dirty",
    "Shared_Clean",
    "Shared_Dirty",
)
_KIB_RE = re.compile(r"^(?P<key>[A-Za-z0-9_]+):\s+(?P<value>\d+)(?:\s+kB)?\s*$")
_IO_RE = re.compile(r"^(?P<key>[A-Za-z0-9_]+):\s+(?P<value>\d+)\s*$")
_NET_RE = re.compile(
    r"^\s*(?P<iface>[^:]+):\s+"
    r"(?P<rx_bytes>\d+)\s+(?P<rx_packets>\d+)\s+\d+\s+\d+\s+\d+\s+\d+\s+\d+\s+\d+\s+"
    r"(?P<tx_bytes>\d+)\s+(?P<tx_packets>\d+)\b"
)


def parse_proc_status(text: str) -> dict[str, int]:
    """Parse ``/proc/<pid>/status`` memory fields in kiB."""
    return _parse_kib_block(text, _STATUS_KEYS)


def parse_smaps_rollup(text: str) -> dict[str, int]:
    """Parse ``/proc/<pid>/smaps_rollup``; USS is private clean+dirty."""
    parsed = _parse_kib_block(text, _SMAPS_KEYS)
    private_clean = parsed.get("Private_Clean", 0)
    private_dirty = parsed.get("Private_Dirty", 0)
    parsed["uss_kib"] = private_clean + private_dirty
    return parsed


def parse_proc_io(text: str) -> dict[str, int]:
    """Parse ``/proc/<pid>/io`` byte and syscall counters."""
    out: dict[str, int] = {}
    for raw in text.splitlines():
        match = _IO_RE.match(raw.strip())
        if match is None:
            continue
        key = match.group("key")
        if key in _IO_KEYS:
            out[key] = int(match.group("value"))
    return out


def parse_net_dev(text: str) -> dict[str, dict[str, int]]:
    """Parse ``/proc/net/dev`` per-interface byte counters."""
    ifaces: dict[str, dict[str, int]] = {}
    for raw in text.splitlines():
        match = _NET_RE.match(raw)
        if match is None:
            continue
        iface = match.group("iface").strip()
        ifaces[iface] = {
            "rx_bytes": int(match.group("rx_bytes")),
            "rx_packets": int(match.group("rx_packets")),
            "tx_bytes": int(match.group("tx_bytes")),
            "tx_packets": int(match.group("tx_packets")),
        }
    return ifaces


def delta_counters(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    """Subtract matching integer counters; missing keys are omitted."""
    out: dict[str, int] = {}
    for key, end in after.items():
        start = before.get(key)
        if start is None:
            continue
        out[key] = end - start
    return out


def delta_net(
    before: dict[str, dict[str, int]],
    after: dict[str, dict[str, int]],
    skip_loopback: bool = True,
) -> dict[str, Any]:
    """Per-interface and total rx/tx byte deltas over a capture window."""
    per_iface: dict[str, dict[str, int]] = {}
    total_rx = 0
    total_tx = 0
    for iface, end in after.items():
        if skip_loopback and iface == "lo":
            continue
        start = before.get(iface)
        if start is None:
            continue
        rx = end["rx_bytes"] - start["rx_bytes"]
        tx = end["tx_bytes"] - start["tx_bytes"]
        per_iface[iface] = {"rx_bytes": rx, "tx_bytes": tx}
        total_rx += rx
        total_tx += tx
    return {
        "per_iface": per_iface,
        "total_rx_bytes": total_rx,
        "total_tx_bytes": total_tx,
        "caveat": (
            "/proc/net/dev is namespace-wide, not process-scoped. Treat totals "
            "as an upper bound unless the node is idle except hpcperfstatsd "
            "and the admin interface is identified."
        ),
    }


def _parse_kib_block(text: str, keys: tuple[str, ...]) -> dict[str, int]:
    wanted = set(keys)
    out: dict[str, int] = {}
    for raw in text.splitlines():
        match = _KIB_RE.match(raw.strip())
        if match is None:
            continue
        key = match.group("key")
        if key in wanted:
            out[key] = int(match.group("value"))
    return out
