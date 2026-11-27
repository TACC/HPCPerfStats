#!/usr/bin/env python3
"""CPython microbench for production parser vs bounded FeedKernel."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from e6_corpus import build_e6_payload_lines  # noqa: E402
from parse_kernel import FeedKernel  # noqa: E402

REPS = int(os.environ.get("HPCPERFSTATS_CODON_BENCH_REPS", "60"))
N_SAMPLES = int(os.environ.get("HPCPERFSTATS_CODON_BENCH_SAMPLES", "400"))


def bench_incremental(lines: list[str], reps: int) -> float:
  from hpcperfstats.dbload.lib.sync_timedb_parsing import IncrementalStatsParser

  # Warmup
  w = IncrementalStatsParser(0)
  w.feed_lines(lines)
  w.finish()
  t0 = time.perf_counter()
  for _ in range(reps):
    p = IncrementalStatsParser(0)
    p.feed_lines(lines)
    p.finish()
  return time.perf_counter() - t0


def bench_kernel(lines: list[str], reps: int) -> float:
  w = FeedKernel(0)
  w.feed_lines(lines)
  t0 = time.perf_counter()
  for _ in range(reps):
    k = FeedKernel(0)
    k.feed_lines(lines)
    _ = k.stats_len
  return time.perf_counter() - t0


def main() -> None:
  lines = build_e6_payload_lines(N_SAMPLES)
  inc_s = bench_incremental(lines, REPS)
  ker_s = bench_kernel(lines, REPS)
  ratio = inc_s / ker_s if ker_s > 0 else 0.0
  gate_125 = ratio >= 1.25
  print(
      "bench_incremental_s=%.6f bench_kernel_s=%.6f "
      "kernel_speedup_vs_incremental=%.3f gate_1p25x=%s "
      "reps=%d samples=%d"
      % (inc_s, ker_s, ratio, gate_125, REPS, N_SAMPLES),
  )


if __name__ == "__main__":
  main()
