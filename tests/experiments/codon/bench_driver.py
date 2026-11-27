"""Microbench driver (Codon: codon run -release bench_driver.py)."""

import time

from e6_corpus import build_e6_payload_lines
from parse_kernel import FeedKernel

N_SAMPLES = 400
REPS = 80

lines = build_e6_payload_lines(N_SAMPLES)
warm = FeedKernel(0)
warm.feed_lines(lines)

t0 = time.perf_counter()
for _ in range(REPS):
  k = FeedKernel(0)
  k.feed_lines(lines)
  _ = k.stats_len
elapsed = time.perf_counter() - t0
print(
    "codon_driver_elapsed_s="
    + str(elapsed)
    + " reps="
    + str(REPS)
    + " samples="
    + str(N_SAMPLES),
)
