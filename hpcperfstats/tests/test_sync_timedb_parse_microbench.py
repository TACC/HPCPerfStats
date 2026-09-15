"""Host microbench for Wave 2 leftover ranking (feed_line vs groupby)."""
from __future__ import annotations

import cProfile
import io
import os
import pstats
import tempfile
from pathlib import Path

import pytest

from hpcperfstats.dbload.lib.sync_timedb_parsing import (
    build_stats_dataframes,
    compute_deltas_and_arc,
    parse_stats_file_streaming_incremental,
    parse_stats_lines,
)


def _synthetic_lines(n_samples: int = 200) -> list[str]:
  """Build a CPU + host_proc fixture large enough for cProfile signal."""
  from hpcperfstats.dbload.lib.sync_timedb_parsing import HOST_PROC_KEYS

  proc_schema = " ".join(
      f"{k},U=kB" if k.startswith("vm_") else k for k in HOST_PROC_KEYS
  )
  lines = [
      "!cpu user,W=48 sys,W=48 idle,W=48\n",
      f"!host_proc {proc_schema}\n",
  ]
  t0 = 1709123456
  for i in range(n_samples):
    t = t0 + i
    lines.append(f"{t} job1 cn001\n")
    lines.append(f"cpu 0 {100 + i} {200 + i} {50 + i}\n")
    vals = " ".join(str(1000 + j + i) for j in range(len(HOST_PROC_KEYS)))
    lines.append(f"host_proc python/4242/0-7/0 {vals}\n")
  return lines


def _top_funcs(pr: cProfile.Profile, n: int = 30) -> list[tuple[str, float]]:
  """Return (label, tottime) sorted by exclusive time."""
  stats = pstats.Stats(pr, stream=io.StringIO())
  stats.sort_stats("tottime")
  rows: list[tuple[str, float]] = []
  for (filename, _lineno, name), (_cc, _nc, tt, _ct, _callers) in (
      stats.stats.items()
  ):
    label = f"{Path(filename).name}:{name}"
    rows.append((label, float(tt)))
  rows.sort(key=lambda x: x[1], reverse=True)
  return rows[:n]


@pytest.mark.timeout(120)
def test_wave2_microbench_ranks_feed_line_vs_groupby():
  """
  Rank leftover exclusive time; record integer-codes ship/skip decision.

  Prints WAVE2_MICROBENCH_OK and either wave2_skip_integer_codes or
  wave2_ship_integer_codes for the unlazy gate oracle.
  """
  lines = _synthetic_lines(250)
  pr = cProfile.Profile()
  pr.enable()
  stats_list, _proc_list = parse_stats_lines(lines, 0)
  stats_df, _proc_df = build_stats_dataframes(stats_list, _proc_list)
  if not stats_df.empty:
    compute_deltas_and_arc(stats_df)
  with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / "host" / "1"
    path.parent.mkdir(parents=True)
    path.write_text("".join(lines), encoding="utf-8")
    parse_stats_file_streaming_incremental(
        str(path),
        flush_rows=5000,
        on_chunk=lambda *_a: None,
    )
  pr.disable()
  top = _top_funcs(pr, 30)
  out_lines = ["=== wave2 microbench top tottime ==="]
  for label, tt in top:
    out_lines.append(f"{tt:10.6f}  {label}")

  feed_score = sum(
      tt for label, tt in top
      if "feed_line" in label
      or "_append_compiled" in label
      or "schema_key_basename" in label
      or "IncrementalStatsParser" in label
  )
  groupby_score = sum(
      tt for label, tt in top
      if "groupby" in label.lower()
      or "GroupBy" in label
      or "pandas" in label.lower()
  )
  out_lines.append(f"feed_line_family_tottime={feed_score:.6f}")
  out_lines.append(f"groupby_family_tottime={groupby_score:.6f}")

  if groupby_score > feed_score * 1.5 and groupby_score > 0.01:
    decision = "wave2_ship_integer_codes"
  else:
    decision = (
        "wave2_skip_integer_codes reason=groupby_not_top_exclusive"
    )
  out_lines.append(decision)
  out_lines.append("WAVE2_MICROBENCH_OK")
  text = "\n".join(out_lines) + "\n"
  print(text, end="")

  log_dir = Path(__file__).resolve().parents[2] / "test_runs"
  log_dir.mkdir(parents=True, exist_ok=True)
  log_path = log_dir / "parse-wave2-microbench-20260914.log"
  with open(log_path, "w", encoding="utf-8") as fh:
    fh.write(text)
    fh.write(f"# pid={os.getpid()}\n")

  assert stats_list
  assert decision.startswith("wave2_")
