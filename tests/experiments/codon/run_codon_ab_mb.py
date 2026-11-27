"""One-file megabytes-per-second gate: CPython versus the Codon file session.

Arm A is ``IncrementalStatsParser`` plus ``compute_deltas_and_arc``.
Arm B is ``parse_stats_file_codon_open`` / ``parse_stats_file_codon_next``.
Score ``input_file_bytes / elapsed_s``. Exit non-zero when Codon is not faster
or when either arm does not finish the file.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS", "1")


def _arm_a(path: Path) -> None:
  from hpcperfstats.dbload.lib.sync_timedb_parsing import (
    IncrementalStatsParser,
    compute_deltas_and_arc,
    stats_payload_to_records,
  )

  text = path.read_text(encoding="utf-8", errors="replace")
  lines = text.splitlines()
  parser = IncrementalStatsParser(0)
  parser.feed_lines(lines)
  stats, _procs = parser.finish()
  records = stats_payload_to_records(stats)
  import pandas as pd

  frame = pd.DataFrame(records) if records else pd.DataFrame()
  compute_deltas_and_arc(frame)


def _arm_b(path: Path) -> None:
  import hpcperfstats_codon_parse as codon

  opened = codon.parse_stats_file_codon_open(
    str(path), 8 * 1024 * 1024, 0, 0
  )
  if not opened:
    raise RuntimeError("codon file session did not open")
  while True:
    frame = codon.parse_stats_file_codon_next(0, 0)
    if frame is None:
      break


def main(argv: list[str]) -> int:
  if len(argv) != 2:
    print("usage: run_codon_ab_mb.py STATS_FILE", file=sys.stderr)
    return 2
  path = Path(argv[1])
  if not path.is_file():
    print(f"missing_file={path}", file=sys.stderr)
    return 2
  nbytes = path.stat().st_size
  if nbytes <= 0:
    print("empty_file", file=sys.stderr)
    return 2
  print(f"input_file_bytes={nbytes}")
  t0 = time.perf_counter()
  _arm_a(path)
  a_s = time.perf_counter() - t0
  t1 = time.perf_counter()
  _arm_b(path)
  b_s = time.perf_counter() - t1
  if a_s <= 0.0 or b_s <= 0.0:
    print("unfinished_arm", file=sys.stderr)
    return 1
  a_mb = (nbytes / a_s) / 1_000_000.0
  b_mb = (nbytes / b_s) / 1_000_000.0
  ratio = b_mb / a_mb
  print(f"arm_a_elapsed_s={a_s:.6f}")
  print(f"arm_b_elapsed_s={b_s:.6f}")
  print(f"arm_a_mb_per_s={a_mb:.6f}")
  print(f"arm_b_mb_per_s={b_mb:.6f}")
  print(f"ratio_b_over_a={ratio:.6f}")
  if ratio <= 1.0:
    print("codon_not_faster", file=sys.stderr)
    os._exit(1)
  print("CODON_MB_FASTER")
  os._exit(0)


if __name__ == "__main__":
  raise SystemExit(main(sys.argv))
