"""Independent-read cross-check: "0 means read and 0", not "never read".

Between two captures of one shm payload the validator reads the same kernel source the collector
reads: ``/proc/stat`` (host_cpu), IB / OPA ``ports/<p>/counters`` (host_ib, host_opa),
``/sys/block/<dev>/stat`` (host_block), ``/sys/devices/system/node/node<N>/meminfo`` (host_mem)
and the ACPI hwmon power meters (host_power_socket).

- Counters (``E`` keys) are monotonic, so a read taken after capture A was written and finished
  before collect B started must satisfy ``v_a <= r <= v_b`` (mod 2^W). The daemon stamps ``ts_b``
  after collecting, so ``t_read < ts_b`` (``t_read`` = end of the read) is necessary but not
  sufficient; the capture starts the read right after a fresh A, about one interval before B.
- Gauges cannot be bracketed: a gauge FAILs when it was emitted 0 in both captures while the
  source reads non-zero. ``host_mem`` ``dirty`` and ``writeback`` swing by MB within an interval
  (journald, the verifier's own /tmp stimulus), so for them that mismatch is a NOTE when another
  ``host_mem`` key on the same row was emitted non-zero in either capture (the meminfo parse
  runs), and a FAIL only when none was.

A collector that silently stopped reading (flat, or stuck at 0) fails this even when idle.

Every host_ib counter here is sysfs ``counters/`` on every build: the only MAD query left fills
the switch-side ``sw_*`` keys, which have no host file.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from .host_live_probes import probe_power_socket_watts
from .message_parse import schema_key_name, schema_token_is_event_counter
from .payload_parse import metric_value_at_key, row_key_names, rows_by_type

_PROC_STAT_FIELDS = ("user", "nice", "system", "idle", "iowait", "irq", "softirq")
# /sys/block/<dev>/stat field order (block.c KEYS).
_BLOCK_STAT_FIELDS = (
    "rd_ios", "rd_merges", "rd_sectors", "rd_ticks", "wr_ios", "wr_merges", "wr_sectors",
    "wr_ticks", "in_flight", "io_ticks", "time_in_queue",
)
# CN5000 hw_counters files and the host_opa key each fills (opa_sysfs.c).
_OPA_HW_COUNTERS = {
    "port_xmit_data": "TxWords", "port_rcv_data": "RxWords", "port_xmit_pkts": "TxPkt",
    "port_rcv_pkts": "RxPkt", "port_xmit_wait": "TxWait",
}
_HOST_MEM_VOLATILE_GAUGE_KEYS = frozenset({"dirty", "writeback"})
_HOST_KEY_ALIAS = Path(__file__).resolve().parents[2] / "src" / "host_key_alias.c"


def _mem_aliases() -> dict[str, str]:
    """meminfo field -> host_mem key, read from the collector's own table (host_key_alias.c)."""
    try:
        text = _HOST_KEY_ALIAS.read_text(encoding="utf-8")
    except OSError:
        return {}
    table = text.split("g_mem_aliases", 1)[-1].split("};", 1)[0]
    return dict(re.findall(r'\{"(\w+)", "(\w+)"\}', table))


def _read_proc_stat(proc_root: Path) -> dict[tuple[str, str, str], int]:
    out: dict[tuple[str, str, str], int] = {}
    try:
        lines = (proc_root / "stat").read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        parts = line.split()
        if not parts or not parts[0].startswith("cpu") or not parts[0][3:].isdigit():
            continue
        for name, val in zip(_PROC_STAT_FIELDS, parts[1:]):
            if val.isdigit():
                out[("host_cpu", parts[0][3:], name)] = int(val)
    return out


def _read_ib_counters(sys_root: Path, devs: list[str], keys: list[str]) -> dict[tuple[str, str, str], int]:
    out: dict[tuple[str, str, str], int] = {}
    for dev in devs:
        hca, _, port = dev.rpartition(".")
        if not hca or not port.isdigit():
            continue
        counters = sys_root / "class" / "infiniband" / hca / "ports" / port / "counters"
        for key in keys:
            try:
                text = (counters / key).read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if text.isdigit():
                out[("host_ib", dev, key)] = int(text)
    return out


def _read_uint(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return int(text) if text.isdigit() else None


def opa_counter_files(port_dir: Path, key: str) -> list[Path]:
    """Files opa_sysfs.c may read for `key`: classic counters (port_*_pkts are *_packets there),
    then the CN5000 hw_counters fallback."""
    out = [port_dir / "counters" / key, port_dir / "counters" / key.replace("_pkts", "_packets")]
    if key in _OPA_HW_COUNTERS:
        out.append(port_dir / "hw_counters" / _OPA_HW_COUNTERS[key])
    return out


def _read_opa_counters(sys_root: Path, devs: list[str], keys: list[str]) -> dict[tuple[str, str, str], int]:
    out: dict[tuple[str, str, str], int] = {}
    for dev in devs:
        hfi, _, port = dev.partition("/")
        port_dir = sys_root / "class" / "infiniband" / hfi / "ports" / port
        for key in keys:
            for path in opa_counter_files(port_dir, key):
                val = _read_uint(path)
                if val is not None:
                    out[("host_opa", dev, key)] = val
                    break
    return out


def _read_block_stat(sys_root: Path, devs: list[str]) -> dict[tuple[str, str, str], int]:
    out: dict[tuple[str, str, str], int] = {}
    for dev in devs:
        try:
            fields = (sys_root / "block" / dev / "stat").read_text(encoding="utf-8").split()
        except OSError:
            continue
        for name, val in zip(_BLOCK_STAT_FIELDS, fields):
            if val.isdigit():
                out[("host_block", dev, name)] = int(val)
    return out


def _read_node_meminfo(sys_root: Path, devs: list[str]) -> dict[tuple[str, str, str], int]:
    aliases = _mem_aliases()
    out: dict[tuple[str, str, str], int] = {}
    for dev in devs:
        path = sys_root / "devices" / "system" / "node" / f"node{dev}" / "meminfo"
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            m = re.match(r"Node \d+ (\w+):\s+(\d+)", line)
            if m and m.group(1) in aliases:
                out[("host_mem", dev, aliases[m.group(1)])] = int(m.group(2))
    return out


def _devs(rows: dict, type_name: str) -> list[str]:
    return sorted({row.dev for row in rows.get(type_name, [])})


def read_independent_sources(
    schema_by_type: dict[str, list[str]],
    body: str,
    *,
    require_tier: bool,
    allowed_tier: str | None,
    proc_root: Path = Path("/proc"),
    sys_root: Path = Path("/sys"),
) -> tuple[float, dict[tuple[str, str, str], int]]:
    """(wall time at the end of the reads, (type, dev, key) -> live value) for the types this
    layer covers."""
    out: dict[tuple[str, str, str], int] = {}
    if "host_cpu" in schema_by_type:
        out.update(_read_proc_stat(proc_root))
    rows = rows_by_type(body, schema_by_type, require_tier=require_tier, allowed_tier=allowed_tier)
    for type_name, reader in (("host_ib", _read_ib_counters), ("host_opa", _read_opa_counters)):
        if type_name in schema_by_type:
            keys = [schema_key_name(k) for k in schema_by_type[type_name]]
            out.update(reader(sys_root, _devs(rows, type_name), keys))
    if "host_block" in schema_by_type:
        out.update(_read_block_stat(sys_root, _devs(rows, "host_block")))
    if "host_mem" in schema_by_type:
        out.update(_read_node_meminfo(sys_root, _devs(rows, "host_mem")))
    if "host_power_socket" in schema_by_type:
        for (sock, key), watts in probe_power_socket_watts(str(sys_root)).items():
            out[("host_power_socket", sock, key)] = int(watts + 0.5)
    return time.time(), out


def _token(schema_keys: list[str], key: str) -> str:
    return next((tok for tok in schema_keys if schema_key_name(tok) == key), key)


def _width(token: str) -> int:
    w = [p[2:] for p in token.split(",")[1:] if p.startswith("W=")]
    return int(w[0]) if w else 64


def check_independent_reads(
    schema_by_type: dict[str, list[str]],
    body_a: str,
    body_b: str,
    ts_b: float,
    t_read: float,
    live: dict[tuple[str, str, str], int],
    *,
    require_tier: bool,
    allowed_tier: str | None,
) -> tuple[list[str], list[str]]:
    """Notes and errors: FAIL for a counter read outside [v_a, v_b] (mod 2^W), or a gauge emitted 0
    in both captures while its source reads non-zero."""
    notes: list[str] = []
    errors: list[str] = []
    if not live:
        notes.append("SKIP independent_read: no covered source on this host")
        return notes, errors
    if t_read >= ts_b:
        notes.append(f"NOTE independent_read: read at {t_read:.1f} not before sample {ts_b:.1f}; inconclusive")
        return notes, errors
    maps = []
    for body in (body_a, body_b):
        rows = rows_by_type(body, schema_by_type, require_tier=require_tier, allowed_tier=allowed_tier)
        values: dict[tuple[str, str, str], int] = {}
        for type_name in {k[0] for k in live}:
            for row in rows.get(type_name, []):
                names = row_key_names(row, schema_by_type)
                for name in names:
                    v = metric_value_at_key(row.values, names, name)
                    if v is not None:
                        values[(type_name, row.dev, name)] = v
        maps.append(values)
    checked = 0
    for key, r in sorted(live.items()):
        if key not in maps[0] or key not in maps[1]:
            continue
        va, vb = maps[0][key], maps[1][key]
        token = _token(schema_by_type[key[0]], key[2])
        checked += 1
        if not schema_token_is_event_counter(token):
            if va == 0 and vb == 0 and r > 0:
                if (
                    key[0] == "host_mem"
                    and key[2] in _HOST_MEM_VOLATILE_GAUGE_KEYS
                    and any(v and k[0] == "host_mem" and k[1] == key[1]
                            and k[2] not in _HOST_MEM_VOLATILE_GAUGE_KEYS
                            for m in maps for k, v in m.items())
                ):
                    notes.append(
                        f"NOTE independent_read {key[0]}/{key[1]} {key[2]}: emitted 0 in both"
                        f" captures, source reads {r} kB (volatile gauge; meminfo parsed on"
                        " this row)"
                    )
                else:
                    errors.append(
                        f"FAIL independent_read {key[0]}/{key[1]} {key[2]}: emitted 0 in both"
                        f" captures, source reads {r}"
                    )
            continue
        mod = 1 << _width(token)
        if (r - va) % mod + (vb - r) % mod != (vb - va) % mod:
            errors.append(
                f"FAIL independent_read {key[0]}/{key[1]} {key[2]}: live {r} outside emitted [{va}, {vb}]"
            )
    if checked and not errors:
        notes.append(f"PASS independent_read ({checked} keys)")
    elif not checked:
        notes.append("SKIP independent_read: no key with a value in both captures")
    return notes, errors
