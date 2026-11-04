"""Grace SCF DRAM CAS verification (stdlib only; runs under the node's python3).

Used by ``bin/verify_grace_scf_imc.sh``. Pure parsing and checks, unit-tested on the dev host:

* ``likwid-perfctr`` text output (custom eventset ``CMEM_RD_DATA:SCF0,CMEM_WR_TOTAL_BYTES:SCF1``),
* ``likwid-bench`` "Data volume (Byte)",
* the DEBUG ``/dev/shm/.../schema`` + ``full`` payloads written by hpcperfstatsd,

and the verdicts: SCF units (reads are 32-byte beats, writes are bytes) measured against a known
traffic volume, and the daemon's ``arm_aarch64_imc`` deltas (64-byte CAS) across the same load.
This is a verification tool, not part of the overhead profile's headline numbers.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any

NUM = re.compile(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")


def perfctr_value(text: str, event: str, counter: str) -> float | None:
    """Last number on the table row that names ``event`` and ``counter``.

    likwid-perfctr prints rows such as ``|   CMEM_RD_DATA    |   SCF0  |  1.234567e+08 |``.

    Args:
        text: Full likwid-perfctr output.
        event: Event name in the first cell.
        counter: Counter name in the second cell.

    Returns:
        The value, or None when no such row exists.
    """
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 3 and cells[0] == event and cells[1] == counter:
            nums = NUM.findall(cells[-1])
            if nums:
                return float(nums[-1])
    return None


def perfctr_cannot_run(text: str) -> bool:
    """True when likwid-perfctr failed to load its library rather than measure.

    The static bundle installs only ``liblikwid.a``; the Lua front end needs ``liblikwid.so``
    and prints ``module 'liblikwid' not found`` (or the loader's shared-library error). That
    is a missing tool, not a units failure, so the shell driver reports NOT_RUN.

    Args:
        text: likwid-perfctr output.

    Returns:
        Whether the output is a library load failure.
    """
    return bool(re.search(r"module 'liblikwid' not found|error while loading shared libraries", text))


def bench_volume_bytes(text: str) -> int | None:
    """``Data volume (Byte):`` from likwid-bench output.

    Args:
        text: Full likwid-bench output.

    Returns:
        Bytes moved by the kernel, or None when the line is missing.
    """
    m = re.search(r"Data volume \(Byte\):\s*(\d+)", text)
    return int(m.group(1)) if m else None


def parse_schema(text: str) -> dict[str, list[str]]:
    """Map each ``!type key,opts key,opts`` schema line to its ordered key names.

    Args:
        text: DEBUG shm ``schema`` payload.

    Returns:
        {type: [key, ...]}.
    """
    out: dict[str, list[str]] = {}
    for line in text.splitlines():
        if line.startswith("!"):
            parts = line[1:].split()
            if parts:
                out[parts[0]] = [p.split(",")[0] for p in parts[1:]]
    return out


def parse_payload(text: str, schema: dict[str, list[str]]) -> dict[tuple[str, str], dict[str, int]]:
    """Parse sample rows ``type dev [@tier] v1 v2 ...`` after the ``ts jid host`` header.

    Args:
        text: DEBUG shm ``full`` payload.
        schema: Output of :func:`parse_schema`.

    Returns:
        {(type, dev): {key: value}} for types present in the schema.
    """
    rows: dict[tuple[str, str], dict[str, int]] = {}
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 3 or parts[0] not in schema:
            continue
        vals = parts[3:] if parts[2].startswith("@") else parts[2:]
        rows[(parts[0], parts[1])] = {k: int(float(v)) for k, v in zip(schema[parts[0]], vals)}  # noqa: B905
    return rows


def unit_check(volume: int, counted: float, idle: float, expect_bytes_per_unit: float,
               tol: float = 0.25, bench_secs: float = 1.0, idle_secs: float = 1.0) -> dict[str, Any]:
    """Bytes per counter unit, measured from a known volume with idle background subtracted.

    Args:
        volume: Bytes the benchmark kernel moved.
        counted: Counter total over the benchmark.
        idle: Counter total over an idle window of ``idle_secs``.
        expect_bytes_per_unit: 32 for read beats, 1 for write bytes.
        tol: Allowed relative error.
        bench_secs: Wall-clock length of the benchmark window.
        idle_secs: Wall-clock length of the idle window (idle is scaled to the bench window).

    Returns:
        Verdict dict with ``ok`` and the measured ``bytes_per_unit``.
    """
    net = counted - (idle * bench_secs / idle_secs if idle_secs > 0 else 0.0)
    if volume <= 0 or net <= 0:
        return {"ok": False, "reason": f"no signal (volume={volume}, counted={counted}, idle={idle})"}
    measured = volume / net
    return {"ok": abs(measured / expect_bytes_per_unit - 1.0) <= tol,
            "bytes_per_unit": round(measured, 3), "expected": expect_bytes_per_unit}


def daemon_delta_check(before: dict, after: dict, dev: str, key: str, volume: int,
                       other_dev: str | None, lo: float = 0.75, hi: float = 1.5) -> dict[str, Any]:
    """Check that the daemon's 64-byte CAS delta on ``dev`` accounts for a known volume.

    The sample window also covers idle time, so the ratio may exceed 1 slightly (``hi``); a 2x
    unit error must still fail, so the load has to dwarf idle traffic over the window. The other socket
    must stay below a quarter of the volume, which proves the delta is per socket.

    Args:
        before: Parsed payload before the load.
        after: Parsed payload after the load.
        dev: Device loaded (``scf0``).
        key: ``dram_cas_reads`` or ``dram_cas_writes``.
        volume: Bytes the benchmark moved.
        other_dev: The idle socket's device, or None on single-socket parts.
        lo: Minimum accepted bytes / volume.
        hi: Maximum accepted bytes / volume.

    Returns:
        Verdict dict with ``ok``, ``ratio`` and ``other_socket_ratio``.
    """
    try:
        d = after[("arm_aarch64_imc", dev)][key] - before[("arm_aarch64_imc", dev)][key]
    except KeyError as err:
        return {"ok": False, "reason": f"missing row/key {err}"}
    ratio = d * 64 / volume if volume > 0 else 0.0
    res: dict[str, Any] = {"ok": lo <= ratio <= hi, "bytes": d * 64, "volume": volume,
                           "ratio": round(ratio, 3)}
    if other_dev:
        try:
            od = after[("arm_aarch64_imc", other_dev)][key] - before[("arm_aarch64_imc", other_dev)][key]
        except KeyError as err:
            res.update(ok=False, reason=f"missing other socket row/key {err}")
            return res
        res["other_socket_ratio"] = round(od * 64 / volume, 3) if volume > 0 else None
        res["ok"] = res["ok"] and od * 64 < 0.25 * volume
    return res


def util_check(before: dict, mid: dict, after: dict, cpus: list[str], tol: float = 0.05,
               lag_floor: float = 0.85) -> dict[str, Any]:
    """Compare DCGM user util (host_cpu_hw, us) with /proc/stat user time (host_cpu, cs).

    Round-1 gate for DCGM refreshing at sample_freq: over the whole loaded window the totals must
    agree within ``tol``, and at a mid-load snapshot DCGM must not trail by a whole sample
    (ratio at least ``lag_floor``).

    Args:
        before: Parsed payload before the CPU load.
        mid: Parsed payload partway through the load.
        after: Parsed payload after the load ended.
        cpus: Loaded CPU device names.
        tol: Allowed relative error of the whole-window ratio.
        lag_floor: Minimum mid-window ratio.

    Returns:
        Verdict dict with ``ok``, ``total_ratio`` and ``mid_ratio``.
    """
    def totals(snap: dict) -> tuple[float, float]:
        hw = sum(snap[("host_cpu_hw", c)]["cpu_util_user_accum_us"] for c in cpus)
        jif = sum(snap[("host_cpu", c)]["user"] for c in cpus) * 1e4  # centiseconds -> us
        return hw, jif

    try:
        (hw0, j0), (hw1, j1), (hw2, j2) = totals(before), totals(mid), totals(after)
    except KeyError as err:
        return {"ok": False, "reason": f"missing row/key {err}"}
    if j2 - j0 <= 0 or j1 - j0 <= 0:
        return {"ok": False, "reason": "no user time on the loaded CPUs"}
    total = (hw2 - hw0) / (j2 - j0)
    midr = (hw1 - hw0) / (j1 - j0)
    return {"ok": abs(total - 1.0) <= tol and midr >= lag_floor, "total_ratio": round(total, 3),
            "mid_ratio": round(midr, 3), "cpus": len(cpus)}


def main(argv: list[str]) -> int:
    """CLI for the shell driver: ``unit``, ``loaderr``, ``rows``, ``daemon`` or ``util``; prints one JSON line.

    Args:
        argv: ``sys.argv``.

    Returns:
        0 when the verdict is ok, 1 when it is not, 2 on a usage error.
    """
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "unit" and len(argv) == 10:  # unit PERF IDLE BENCH EVENT COUNTER BPU BENCH_S IDLE_S
        perf, idle, bench = (open(p, encoding="utf-8").read() for p in argv[2:5])
        res = unit_check(bench_volume_bytes(bench) or 0, perfctr_value(perf, argv[5], argv[6]) or 0.0,
                         perfctr_value(idle, argv[5], argv[6]) or 0.0, float(argv[7]),
                         bench_secs=float(argv[8]), idle_secs=float(argv[9]))
    elif cmd == "loaderr" and len(argv) >= 3:  # loaderr PERFCTR_OUT... (exit 0 when unusable)
        res = {"ok": any(perfctr_cannot_run(open(p, encoding="utf-8").read()) for p in argv[2:])}
    elif cmd == "rows" and len(argv) == 4:  # rows SCHEMA PAYLOAD
        schema = parse_schema(open(argv[2], encoding="utf-8").read())
        rows = parse_payload(open(argv[3], encoding="utf-8").read(), schema)
        res = {"ok": any(t == "arm_aarch64_imc" for t, _ in rows),
               "rows": {f"{t} {d}": v for (t, d), v in rows.items() if t == "arm_aarch64_imc"}}
    elif cmd == "daemon" and len(argv) == 9:  # daemon SCHEMA BEFORE AFTER BENCH DEV KEY OTHER_DEV
        schema = parse_schema(open(argv[2], encoding="utf-8").read())
        before = parse_payload(open(argv[3], encoding="utf-8").read(), schema)
        after = parse_payload(open(argv[4], encoding="utf-8").read(), schema)
        res = daemon_delta_check(before, after, argv[6], argv[7],
                                 bench_volume_bytes(open(argv[5], encoding="utf-8").read()) or 0,
                                 argv[8] or None)
    elif cmd == "util" and len(argv) == 7:  # util SCHEMA BEFORE MID AFTER CPU_LIST(a-b)
        schema = parse_schema(open(argv[2], encoding="utf-8").read())
        snaps = [parse_payload(open(p, encoding="utf-8").read(), schema) for p in argv[3:6]]
        lo, _, hi = argv[6].partition("-")
        res = util_check(*snaps, [str(c) for c in range(int(lo), int(hi or lo) + 1)])
    else:
        print("usage: grace_scf_verify.py unit|loaderr|rows|daemon|util ...", file=sys.stderr)
        return 2
    print(json.dumps(res, sort_keys=True))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
