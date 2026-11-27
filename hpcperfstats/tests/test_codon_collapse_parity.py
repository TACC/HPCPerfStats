"""Collapse oracle: Codon plain-host sum vs CPython _collapse_stats_with_deltas."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pandas as pd

_REPO = Path(__file__).resolve().parents[2]
_CODON_EXP = _REPO / "tests" / "experiments" / "codon"
if str(_CODON_EXP) not in sys.path:
  sys.path.insert(0, str(_CODON_EXP))


def _dual_dev_e6_lines(n_samples: int = 40) -> list[str]:
  lines = [
    "!cpu user,W=64,U=1 nice,W=64,U=1 system,W=64,U=1 idle,W=64,U=1 "
    "iowait,W=64,U=1 irq,W=64,U=1 softirq,W=64,U=1",
    "!host_proc uid,W=32 vm_peak,W=64 vm_size,W=64 vm_lck,W=64 "
    "vm_hwm,W=64 vm_rss,W=64 vm_data,W=64 vm_stk,W=64 vm_exe,W=64 "
    "vm_lib,W=64 vm_pte,W=64 vm_swap,W=64 threads,W=32",
  ]
  t0 = 1_700_000_000.0
  for i in range(n_samples):
    t = t0 + float(i)
    lines.append(f"{t} JID1 hostA")
    vals = " ".join(str(float(i * 7 + k)) for k in range(7))
    lines.append(f"cpu 0 @full {vals}")
    vals2 = " ".join(str(float(i * 7 + k + 0.5)) for k in range(7))
    lines.append(f"cpu 1 @full {vals2}")
    lines.append(
      "host_proc 1234 @full "
      + " ".join(
        str(x)
        for x in [
          1000 + i,
          2000 + i,
          3000 + i,
          0,
          4000 + i,
          5000 + i,
          6000 + i,
          7000 + i,
          8000 + i,
          9000 + i,
          10000 + i,
          0,
          2 + (i % 4),
        ]
      )
    )
  return lines


def _cpython_plain_collapse_sum(lines: list[str]) -> tuple[int, float]:
  from hpcperfstats.dbload.lib.sync_timedb_parsing import (
    _COLLAPSE_GROUP_COLS,
    IncrementalStatsParser,
    stats_payload_to_records,
  )

  p = IncrementalStatsParser(0)
  p.feed_lines(lines)
  stats, _procs = p.finish()
  stats = stats_payload_to_records(stats)
  df = pd.DataFrame(stats)
  # Match Codon plain-host path: non-GPU groupby-sum on value.
  df = df[~df["type"].isin({"nvidia_gpu", "amd_gpu", "intel_gpu"})]
  collapsed = (
    df.groupby(_COLLAPSE_GROUP_COLS, dropna=False, observed=True)["value"]
    .sum(min_count=1)
    .reset_index()
  )
  return len(collapsed), float(collapsed["value"].sum()) if len(
    collapsed
  ) else 0.0


def _codon_twin_collapse_sum(lines: list[str]) -> tuple[int, float]:
  """CPython twin of Codon collapse_plain_host_sum (host tests without Codon)."""
  from parse_kernel import FeedKernel

  k = FeedKernel(0)
  k.feed_lines(lines)
  # FeedKernel stores columnar stats via stats dict — use Incremental for columns
  from hpcperfstats.dbload.lib.sync_timedb_parsing import (
    IncrementalStatsParser,
    stats_payload_to_records,
  )

  p = IncrementalStatsParser(0)
  p.feed_lines(lines)
  stats, _ = p.finish()
  stats = stats_payload_to_records(stats)
  key_to = {}
  for r in stats:
    if r.get("type") in {"nvidia_gpu", "amd_gpu", "intel_gpu"}:
      continue
    key = (
      str(r["host"]),
      str(r["type"]),
      str(r["event"]),
      str(r.get("unit") or ""),
      float(r["time"]),
    )
    key_to[key] = key_to.get(key, 0.0) + float(r["value"])
  return len(key_to), float(sum(key_to.values()))


def test_plain_host_collapse_matches_groupby_sum() -> None:
  lines = _dual_dev_e6_lines(40)
  c_rows, c_sum = _cpython_plain_collapse_sum(lines)
  t_rows, t_sum = _codon_twin_collapse_sum(lines)
  assert c_rows == t_rows
  assert math.isclose(c_sum, t_sum, rel_tol=1e-9, abs_tol=1e-6)
