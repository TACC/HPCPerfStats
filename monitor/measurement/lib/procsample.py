"""Per-process /proc sampling for hpcperfstatsd (stdlib only; runs on any node python3).

CPU time comes from schedstat (nanoseconds, the only resolution fine enough for small
per-driver deltas), summed over ``/proc/<pid>/task/*/schedstat``: ``/proc/<pid>/schedstat``
alone is the main thread only (``process_run_ns_delta``). The user/system split comes from
``stat`` jiffies, which already cover every thread. When the
kernel has no schedstat, CPU falls back to jiffies and the delta says ``cpu_resolution:
jiffies``. Memory and I/O reuse the ``procfs`` parsers.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .procfs import parse_proc_io, parse_proc_status, parse_smaps_rollup

CLK_TCK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
PERF_EVENT_LINK = "anon_inode:[perf_event]"

# Field positions in /proc/<pid>/stat after the ``(comm)`` field (0-based from state).
_STAT_FIELDS = {"minflt": 7, "majflt": 9, "utime": 11, "stime": 12, "num_threads": 17,
                "starttime": 19}


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def parse_pid_stat(text: str) -> dict[str, Any]:
    """Parse ``/proc/<pid>/stat``; ``comm`` may hold spaces and parentheses."""
    open_paren = text.index("(")
    close_paren = text.rindex(")")
    rest = text[close_paren + 1:].split()
    out: dict[str, Any] = {"comm": text[open_paren + 1:close_paren], "state": rest[0]}
    for key, idx in _STAT_FIELDS.items():
        out[key] = int(rest[idx]) if idx < len(rest) else None
    return out


def parse_schedstat(text: str | None) -> int | None:
    """Return on-CPU nanoseconds (first field of schedstat), or None when absent."""
    if not text:
        return None
    fields = text.split()
    return int(fields[0]) if fields else None


def _status_ctxt(text: str) -> tuple[int | None, int | None]:
    vol = invol = None
    for line in text.splitlines():
        if line.startswith("voluntary_ctxt_switches:"):
            vol = int(line.split()[1])
        elif line.startswith("nonvoluntary_ctxt_switches:"):
            invol = int(line.split()[1])
    return vol, invol


def _fd_counts(pdir: Path) -> tuple[int | None, int | None]:
    try:
        names = os.listdir(pdir / "fd")
    except OSError:
        return None, None
    perf = 0
    for name in names:
        try:
            if os.readlink(pdir / "fd" / name) == PERF_EVENT_LINK:
                perf += 1
        except OSError:
            continue
    return len(names), perf


def _threads(pdir: Path) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    try:
        tids = os.listdir(pdir / "task")
    except OSError:
        return out
    for tid in tids:
        tdir = pdir / "task" / tid
        comm = (_read(tdir / "comm") or "?").strip()
        out[int(tid)] = {"comm": comm, "run_ns": parse_schedstat(_read(tdir / "schedstat"))}
    return out


def sample_process(pid: int, proc_root: Path | str = "/proc") -> dict[str, Any] | None:
    """One snapshot of a process; None when the process is gone."""
    pdir = Path(proc_root) / str(pid)
    stat_text = _read(pdir / "stat")
    if stat_text is None:
        return None
    stat = parse_pid_stat(stat_text)
    status_text = _read(pdir / "status") or ""
    status = parse_proc_status(status_text)
    vol, invol = _status_ctxt(status_text)
    smaps_text = _read(pdir / "smaps_rollup")
    smaps = parse_smaps_rollup(smaps_text) if smaps_text else {}
    io_text = _read(pdir / "io")
    fds, perf_fds = _fd_counts(pdir)
    return {
        "pid": pid,
        "comm": stat["comm"],
        "run_ns": parse_schedstat(_read(pdir / "schedstat")),
        "utime": stat["utime"],
        "stime": stat["stime"],
        "minflt": stat["minflt"],
        "majflt": stat["majflt"],
        "num_threads": stat["num_threads"],
        "starttime": stat["starttime"],
        "rss_kib": status.get("VmRSS"),
        "vmhwm_kib": status.get("VmHWM"),
        "pss_kib": smaps.get("Pss"),
        "uss_kib": smaps.get("uss_kib"),
        "vol_ctxt": vol,
        "invol_ctxt": invol,
        "io": parse_proc_io(io_text) if io_text else None,
        "fds": fds,
        "perf_event_fds": perf_fds,
        "threads": _threads(pdir),
    }


def process_run_ns_delta(s0: dict[str, Any], s1: dict[str, Any]) -> int | None:
    """On-CPU nanoseconds of the whole process between two snapshots of the same PID.

    Sums per-thread schedstat deltas; a thread born in between counts from zero and one that
    exited loses only its last interval. Without per-thread data in both snapshots this is
    the main thread's own schedstat (right for single-threaded processes only).

    Args:
        s0: Earlier ``sample_process`` snapshot.
        s1: Later snapshot of the same PID.

    Returns:
        Nanoseconds, or None when neither source is available.
    """
    t0s, t1s = s0.get("threads") or {}, s1.get("threads") or {}
    total, seen = 0, False
    for tid, t1 in t1s.items() if t0s else ():
        before = t0s.get(tid, {"run_ns": 0})["run_ns"]  # born since s0: counts from zero
        if t1.get("run_ns") is not None and before is not None:
            total, seen = total + max(0, t1["run_ns"] - before), True
    if seen:
        return total
    if s0.get("run_ns") is None or s1.get("run_ns") is None:
        return None
    return s1["run_ns"] - s0["run_ns"]


def cpu_delta(s0: dict[str, Any], s1: dict[str, Any]) -> dict[str, Any]:
    """CPU seconds between two snapshots of the same PID."""
    user_s = (s1["utime"] - s0["utime"]) / CLK_TCK
    system_s = (s1["stime"] - s0["stime"]) / CLK_TCK
    run_ns = process_run_ns_delta(s0, s1)
    if run_ns is not None:
        cpu_s = run_ns / 1e9
        resolution = "schedstat_ns"
    else:
        cpu_s = user_s + system_s
        resolution = "jiffies"
    return {"cpu_s": round(cpu_s, 9), "user_s": round(user_s, 9), "system_s": round(system_s, 9),
            "cpu_resolution": resolution}


def thread_split(s0: dict[str, Any], s1: dict[str, Any]) -> dict[str, float]:
    """Fraction of process CPU per thread name (threads sharing a comm are summed)."""
    per: dict[str, float] = {}
    for tid, t1 in s1.get("threads", {}).items():
        t0 = s0.get("threads", {}).get(tid)
        if t0 is None or t0["run_ns"] is None or t1["run_ns"] is None:
            continue
        per[t1["comm"]] = per.get(t1["comm"], 0.0) + max(0, t1["run_ns"] - t0["run_ns"])
    total = sum(per.values())
    if total <= 0:
        return {}
    return {name: ns / total for name, ns in per.items()}


def find_pid_by_comm(comm: str, proc_root: Path | str = "/proc") -> int | None:
    """Lowest PID whose ``comm`` equals ``comm`` (the main daemon process)."""
    found: list[int] = []
    for name in os.listdir(proc_root):
        if not name.isdigit():
            continue
        text = _read(Path(proc_root) / name / "comm")
        if text is not None and text.strip() == comm:
            found.append(int(name))
    return min(found) if found else None
