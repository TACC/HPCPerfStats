#!/usr/bin/env python3
"""
Measure and gate thread scaling of the update_metrics Codon kernels.

``run`` (inside the dev container on ``/opt/python3.14t``, kernels loaded)
times ``group_reduce`` and the K0 COPY decoder on private per-thread data
for 1, 2, 4 and 8 threads and writes a JSON report; ``--check`` reads the
newest report and requires at least ``--min-efficiency`` of linear scaling at
the largest thread count for every kernel (plan decision D5: a lower ratio is
a kernel-allocation bug, not a reason to change the process model).

Attributes:
  DEFAULT_GLOB: Report files examined by ``--check``.
  DEFAULT_THREADS: Thread counts measured by ``run``.
  DEFAULT_MIN_EFFICIENCY: Required share of linear scaling at the largest
    thread count.
  GATE_TOKEN: Printed when ``--check`` passes.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import platform
import sys
import threading
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

DEFAULT_GLOB = "test_runs/metrics_codon_scaling_*.json"
DEFAULT_THREADS = (1, 2, 4, 8)
DEFAULT_MIN_EFFICIENCY = 0.6
GATE_TOKEN = "CODON_SCALING_GATE_OK"


def _work_items(rows: int, seed: int) -> dict[str, Callable[[], Any]]:
  """
  Build one thread's private kernel workloads.

  Args:
    rows (int): Rows per reduction / decoded tuples per stream.
    seed (int): RNG seed (distinct per thread).

  Returns:
    dict[str, Callable[[], Any]]: Kernel name to zero-argument callable.

  Examples:
    >>> sorted(_work_items(10, 0))
    ['copy_decode', 'group_reduce']
  """
  from hpcperfstats.analysis.metrics.lib.columnar import codon_bridge
  from hpcperfstats.analysis.metrics.lib.columnar.jobframe import (
    NameInterner,
  )
  from hpcperfstats.analysis.metrics.lib.columnar.k0_decode import (
    decode_copy_binary,
    encode_copy_binary,
  )

  rng = np.random.default_rng(seed)
  keys = rng.integers(0, max(1, rows // 40), size=rows, dtype=np.int64) << 32
  keys |= rng.integers(0, 40, size=rows, dtype=np.int64)
  values = rng.normal(size=rows)
  mask = rng.random(rows) < 0.9
  dec_rows = max(1, rows // 10)
  t0 = 1_700_000_000_000_000
  records = [
    (
      t0 + int(i) * 1_000_000,
      f"h{int(rng.integers(0, 64))}",
      "host_cpu",
      f"e{int(rng.integers(0, 12))}",
      str(int(rng.integers(0, 8))),
      float(rng.normal()),
      float(rng.normal()),
      None if rng.random() < 0.2 else float(rng.normal()),
    )
    for i in range(dec_rows)
  ]
  stream = encode_copy_binary(records)
  return {
    "group_reduce": lambda: codon_bridge.group_reduce(keys, values, mask),
    "copy_decode": lambda: decode_copy_binary(stream, NameInterner()),
  }


def _time_threads(n_threads: int, rows: int, reps: int, kernel: str) -> float:
  """
  Aggregate kernel calls per second across ``n_threads`` threads.

  Args:
    n_threads (int): Threads running at once (each with private data).
    rows (int): Rows per call.
    reps (int): Calls per thread.
    kernel (str): Work item name.

  Returns:
    float: Total calls per second.

  Examples:
    >>> _time_threads(1, 10, 1, "group_reduce") > 0  # doctest: +SKIP
    True
  """
  works = [_work_items(rows, seed)[kernel] for seed in range(n_threads)]
  # Warm caches, the per-thread scratch bank and Boehm heap growth on the
  # timing threads themselves: the first calls of a fresh process ran up to
  # four times slower and skewed the single-thread baseline.
  warm = [
    threading.Thread(target=lambda fn=w: [fn() for _ in range(3)])
    for w in works
  ]
  for t in warm:
    t.start()
  for t in warm:
    t.join()
  barrier = threading.Barrier(n_threads + 1)

  def body(fn: Callable[[], Any]) -> None:
    """
    Run one thread's calls after the barrier.

    Args:
      fn (Callable[[], Any]): Work item.

    Returns:
      None

    Examples:
      >>> True
      True
    """
    barrier.wait()
    for _ in range(reps):
      fn()

  threads = [threading.Thread(target=body, args=(w,)) for w in works]
  for t in threads:
    t.start()
  barrier.wait()
  start = time.perf_counter()
  for t in threads:
    t.join()
  elapsed = time.perf_counter() - start
  return (n_threads * reps) / elapsed if elapsed > 0 else float("inf")


def run_report(
  out: Path, *, threads: Sequence[int], rows: int, reps: int
) -> dict[str, Any]:
  """
  Measure every kernel at every thread count and write the JSON report.

  Args:
    out (Path): Report path.
    threads (Sequence[int]): Thread counts (must include 1).
    rows (int): Rows per call.
    reps (int): Calls per thread.

  Returns:
    dict[str, Any]: The report.

  Raises:
    SystemExit: The Codon kernels are not loaded.

  Examples:
    >>> run_report(
    ...   Path("/tmp/r.json"), threads=[1], rows=10, reps=1
    ... )  # doctest: +SKIP
  """
  from hpcperfstats.analysis.metrics.lib.columnar import codon_bridge

  if codon_bridge.kernels() is None:
    raise SystemExit(
      f"codon metrics kernels not loaded: {codon_bridge._state.reason}"
    )
  report: dict[str, Any] = {
    "created": datetime.now(UTC).isoformat(),
    "host": platform.node(),
    "python": sys.version.split()[0],
    "gil_enabled": bool(getattr(sys, "_is_gil_enabled", lambda: True)()),
    "cpu_count": os.cpu_count(),
    "rows": rows,
    "reps": reps,
    "kernels": {},
  }
  for kernel in ("group_reduce", "copy_decode"):
    rates = {}
    for n in threads:
      rates[str(n)] = _time_threads(int(n), rows, reps, kernel)
      print(
        f"codon_scaling kernel={kernel} threads={n} calls_per_s={rates[str(n)]:.1f}",
        flush=True,
      )
    base = rates["1"]
    report["kernels"][kernel] = {
      "calls_per_s": rates,
      "efficiency": {
        k: (v / base) / int(k) if base > 0 else 0.0 for k, v in rates.items()
      },
    }
  out.parent.mkdir(parents=True, exist_ok=True)
  out.write_text(json.dumps(report, indent=2), encoding="utf-8")
  print(f"codon_scaling report={out}", flush=True)
  return report


def check_reports(paths: Sequence[str], min_efficiency: float) -> int:
  """
  Validate the newest report against the scaling gate.

  Args:
    paths (Sequence[str]): Candidate report files (newest by mtime wins).
    min_efficiency (float): Required efficiency at the largest thread count.

  Returns:
    int: 0 when the gate passes, 1 otherwise.

  Examples:
    >>> check_reports([], 0.6)
    codon_scaling: no report files
    1
  """
  if not paths:
    print("codon_scaling: no report files", flush=True)
    return 1
  newest = max(paths, key=lambda p: os.path.getmtime(p))
  report = json.loads(Path(newest).read_text(encoding="utf-8"))
  if report.get("gil_enabled", True):
    print(
      f"codon_scaling: report {newest} measured with the GIL on", flush=True
    )
    return 1
  ok = True
  for kernel, data in sorted(report.get("kernels", {}).items()):
    eff = data.get("efficiency", {})
    if not eff:
      ok = False
      continue
    top = max(eff, key=int)
    value = float(eff[top])
    status = "ok" if value >= min_efficiency else "FAIL"
    print(
      f"codon_scaling kernel={kernel} threads={top} efficiency={value:.3f} "
      f"min={min_efficiency} {status}",
      flush=True,
    )
    ok = ok and value >= min_efficiency and int(top) >= 8
  if ok:
    print(f"{GATE_TOKEN} report={newest}", flush=True)
    return 0
  return 1


def main(argv: Sequence[str] | None = None) -> int:
  """
  CLI entry point (``run`` writes a report; ``--check`` validates reports).

  Args:
    argv (Sequence[str] | None): Arguments without the program name.

  Returns:
    int: Process exit code.

  Examples:
    >>> main(["--check", "--glob", "/nonexistent/*.json"])
    codon_scaling: no report files
    1
  """
  parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
  parser.add_argument("command", nargs="?", choices=["run"], default=None)
  parser.add_argument("--out", help="report JSON path (run)")
  parser.add_argument(
    "--threads", default=",".join(str(t) for t in DEFAULT_THREADS)
  )
  parser.add_argument("--rows", type=int, default=1_000_000)
  parser.add_argument("--reps", type=int, default=5)
  parser.add_argument("--check", action="store_true")
  parser.add_argument("--glob", default=DEFAULT_GLOB)
  parser.add_argument(
    "--min-efficiency", type=float, default=DEFAULT_MIN_EFFICIENCY
  )
  args = parser.parse_args(argv)
  if args.check:
    return check_reports(sorted(glob.glob(args.glob)), args.min_efficiency)
  if args.command == "run":
    threads = [int(t) for t in args.threads.split(",") if t.strip()]
    if 1 not in threads:
      threads.insert(0, 1)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = Path(args.out or f"test_runs/metrics_codon_scaling_{stamp}.json")
    run_report(out, threads=threads, rows=args.rows, reps=args.reps)
    return 0
  parser.error("use 'run' or --check")
  return 2


if __name__ == "__main__":
  sys.exit(main())
