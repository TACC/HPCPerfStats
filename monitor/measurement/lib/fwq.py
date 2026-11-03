"""Fixed Work Quanta (FWQ, LLNL/ASC Sequoia) noise probe: build, run, parse, compare.

``src/fwq.c`` writes one ``fwq_cpu<N>.bin`` per CPU: little-endian uint64 nanoseconds per
quantum. Every quantum does the same integer work, so any extra time is a detour caused by
something else on the CPU (here: the daemon, the kernel, interrupts).
"""

from __future__ import annotations

import array
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import stats

SRC = Path(__file__).resolve().parents[1] / "src" / "fwq.c"
_FILE_RE = re.compile(r"fwq_cpu(\d+)\.bin$")


def build(out_dir: Path, cc: str = "gcc") -> Path:
    """Compile fwq.c with the host compiler (gcc is already an RPM build prerequisite)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    exe = out_dir / "fwq"
    if shutil.which(cc) is None:
        raise RuntimeError(f"{cc} not found; FWQ needs a C compiler")
    subprocess.run([cc, "-O2", "-std=gnu11", "-Wall", "-o", str(exe), str(SRC), "-lpthread"],
                   check=True, capture_output=True, text=True)
    return exe


def load_quanta(run_dir: Path) -> dict[int, list[int]]:
    """Read every per-CPU file under ``run_dir``; empty dict when absent."""
    out: dict[int, list[int]] = {}
    if not run_dir.is_dir():
        return out
    for path in sorted(run_dir.iterdir()):
        m = _FILE_RE.search(path.name)
        if not m:
            continue
        arr = array.array("Q")
        arr.frombytes(path.read_bytes())
        if sys.byteorder != "little":
            arr.byteswap()
        out[int(m.group(1))] = arr.tolist()
    return out


def summarize(quanta: dict[int, list[int]]) -> dict[str, Any] | None:
    flat = [q for vals in quanta.values() for q in vals]
    if not flat:
        return None
    lo = min(flat)
    return {"n": len(flat), "cpus": sorted(quanta), "min_ns": lo,
            "mean_ns": sum(flat) / len(flat),
            "p50_ns": stats.percentile(flat, 50), "p99_ns": stats.percentile(flat, 99),
            "p999_ns": stats.percentile(flat, 99.9), "max_ns": max(flat),
            "max_detour_ns": max(flat) - lo,
            "p99_detour_ns": stats.percentile(flat, 99) - lo}


def compare(on: dict[str, Any] | None, off: dict[str, Any] | None,
            on_quanta: dict[int, list[int]]) -> dict[str, Any] | None:
    """Mean slowdown and the fraction of ON quanta slower than the OFF p99."""
    if not on or not off:
        return None
    flat = [q for vals in on_quanta.values() for q in vals]
    above = sum(1 for q in flat if q > off["p99_ns"]) / len(flat) if flat else None
    return {"mean_slowdown_pct": (on["mean_ns"] / off["mean_ns"] - 1.0) * 100.0,
            "p99_ratio": on["p99_ns"] / off["p99_ns"],
            "p999_ratio": on["p999_ns"] / off["p999_ns"],
            "max_detour_ns_on": on["max_detour_ns"], "max_detour_ns_off": off["max_detour_ns"],
            "frac_above_off_p99": above}
