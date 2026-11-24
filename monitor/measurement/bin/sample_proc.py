#!/usr/bin/env python3
"""1 Hz sampler: daemon /proc state, node /proc state, energy, AMQP tcp_info, own cost.

Wakes on CLOCK_MONOTONIC 1 s deadlines and appends one JSON line per tick to --out. It
follows the daemon by ``comm`` (Restart=always can change the PID) and records its own CPU
time so the report can show the harness self-cost; the same sampler runs in OFF legs, so its
cost cancels out of every ON − OFF delta.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib import procsample, rapl, tcpinfo
from lib.nodesample import NodeSampler


def _self_cpu_s() -> float:
    try:
        return int(Path("/proc/self/schedstat").read_text().split()[0]) / 1e9
    except (OSError, ValueError, IndexError):
        t = os.times()
        return t.user + t.system


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--duration", type=float, required=True, help="seconds to sample")
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--comm", default="hpcperfstatsd")
    parser.add_argument("--pid", type=int, help="follow this PID instead of looking up --comm")
    parser.add_argument("--helper-comm", action="append", default=[],
                        help="also record CPU ticks of this process (e.g. nv-hostengine)")
    parser.add_argument("--amqp-port", type=int, default=5672)
    parser.add_argument("--no-tcp", action="store_true")
    parser.add_argument("--cpufreq-root", default="/sys/devices/system/cpu",
                        help="cpufreq sysfs root for scaling_cur_freq (tests pass a fake tree)")
    args = parser.parse_args()

    node = NodeSampler(cpufreq_root=args.cpufreq_root)
    pid = args.pid
    helper_pids: dict[str, int | None] = dict.fromkeys(args.helper_comm)
    ticks = 0
    start = time.monotonic()
    deadline = start
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("a", buffering=1) as fh:
        while True:
            now = time.monotonic()
            if now - start > args.duration:
                break
            if pid is None or not Path(f"/proc/{pid}").exists():
                pid = args.pid if args.pid else procsample.find_pid_by_comm(args.comm)
            proc = procsample.sample_process(pid) if pid else None
            if proc is None:
                pid = None
            # A helper that is not running (no standalone DCGM) is re-scanned every 30 ticks, so
            # the /proc walk does not run every second on most nodes.
            helpers = {}
            for comm, hpid in helper_pids.items():
                tick = procsample.sample_cpu_ticks(hpid) if hpid else None
                if tick is None and (hpid or ticks % 30 == 0):
                    hpid = procsample.find_pid_by_comm(comm)
                    tick = procsample.sample_cpu_ticks(hpid) if hpid else None
                helper_pids[comm] = tick["pid"] if tick else None
                helpers[comm] = tick
            ticks += 1
            rec = {
                "t": now,
                "wall": time.time(),
                "node": node.sample(),
                "energy": rapl.read_energy(),
                "proc": proc,
                "tcp": None if args.no_tcp or proc is None else tcpinfo.sample(pid, args.amqp_port),
                "self": {"cpu_s": _self_cpu_s()},
                "helpers": helpers,
            }
            fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
            deadline += args.interval
            delay = deadline - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                deadline = time.monotonic()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
