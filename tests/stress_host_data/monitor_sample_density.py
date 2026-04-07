"""Reference row density **R_ref** from the checked-in monitor sample file.

Uses the same parse path as ingest: ``parse_stats_lines`` → ``build_stats_dataframes``
→ ``compute_deltas_and_arc`` (see ``hpcperfstats.dbload.sync_timedb_parsing``).

**R_ref** is the number of ``host_data``-shaped rows per (host, time) snapshot after
collapse (groupby), i.e. one row per distinct (type, dev, event) after transforms.
Use **median** / **max** per snapshot to extrapolate full-scale row counts in stress
reports (e.g. ``6000 × 8640 × R_max`` order-of-magnitude).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from hpcperfstats.dbload.sync_timedb_parsing import (
    build_stats_dataframes,
    compute_deltas_and_arc,
    load_stats_file_lines,
    parse_stats_lines,
)


def default_sample_path() -> str:
  """Path to ``HPCPerfStatsdDataSample`` under ``hpcperfstats/dbload/tests/``."""
  root = Path(__file__).resolve().parents[2]
  return str(root / "hpcperfstats" / "dbload" / "tests" / "HPCPerfStatsdDataSample")


def analyze_monitor_sample_density(
    sample_path: str | None = None,
    *,
    start_idx: int = 0,
) -> dict[str, Any]:
  """Parse the sample and return per-snapshot row-count stats.

  Returns keys including:
  - ``r_median``, ``r_mean``, ``r_max``: over (host, time) groups of the collapsed frame
  - ``r_min``: minimum group size
  - ``n_groups``: number of (host, time) groups
  - ``n_rows_collapsed``: total rows after ``compute_deltas_and_arc``
  - ``by_type_top``: optional short breakdown (largest types by row count)
  - ``sample_path``: path used
  """
  path = sample_path or os.environ.get(
      "HPCPERFSTATS_STRESS_SAMPLE_PATH", ""
  ).strip() or default_sample_path()

  lines, err = load_stats_file_lines(path)
  if err or not lines:
    return {
        "sample_path": path,
        "error": err or "no lines",
        "r_median": None,
        "r_mean": None,
        "r_max": None,
        "r_min": None,
        "n_groups": 0,
        "n_rows_collapsed": 0,
        "by_type_top": [],
    }

  stats_list, proc_list = parse_stats_lines(lines, start_idx)
  stats_df, _proc_df = build_stats_dataframes(stats_list, proc_list)
  collapsed = compute_deltas_and_arc(stats_df)
  if collapsed.empty:
    return {
        "sample_path": path,
        "error": None,
        "r_median": 0,
        "r_mean": 0.0,
        "r_max": 0,
        "r_min": 0,
        "n_groups": 0,
        "n_rows_collapsed": 0,
        "by_type_top": [],
    }

  grp = collapsed.groupby(["host", "time"], observed=True).size()
  by_type = collapsed.groupby("type", observed=True).size().sort_values(ascending=False)
  top_n = 12
  by_type_top = [
      {"type": str(t), "rows": int(c)}
      for t, c in by_type.head(top_n).items()
  ]

  return {
      "sample_path": path,
      "error": None,
      "r_median": int(grp.median()),
      "r_mean": float(grp.mean()),
      "r_max": int(grp.max()),
      "r_min": int(grp.min()),
      "n_groups": int(grp.shape[0]),
      "n_rows_collapsed": int(len(collapsed)),
      "by_type_top": by_type_top,
  }
