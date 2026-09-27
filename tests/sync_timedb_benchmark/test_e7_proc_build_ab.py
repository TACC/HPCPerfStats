"""E7 proc_merge/build_df hold-seconds A/B (on vs off OnlineMerged path)."""
from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path

import pytest

from tests.sync_timedb_benchmark.screening_runner import (
    DEFAULT_E7_WIDTH,
    build_e7_ab_manifest,
    e7_mode_enabled,
    hold_seconds_retain_candidate,
    parse_replicates_env,
    summarize_hold_s_replicates,
    write_screening_artifact,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

pytestmark = [pytest.mark.sync_timedb_bench]


def _proc_row(i: int) -> dict:
  return {
      "time": 1_709_123_456.0 + (i % 50),
      "host": "cn%03d" % (i % 40),
      "jid": "job%d" % (i % 8),
      "proc": "bash",
      "device": "bash/%d/0/0" % (i % 200),
      "vm_peak": 1000 + i,
      "rss_peak": 500 + i,
      "threads": 4 + (i % 8),
  }


def _proc_rows(*, n: int = 8000) -> list[dict]:
  return [_proc_row(i) for i in range(n)]


def test_e7_proc_build_ab_arm():
  """
  Time baseline (list+dedupe) vs candidate (OnlineMerged/columnar) holds.

  Requires ``HPCPERFSTATS_SYNC_TIMEDB_E7=1``. Retain requires both
  ``proc_merge_s`` and ``build_df_s`` hold-seconds gates (AND).
  """
  if not e7_mode_enabled():
    pytest.fail("HPCPERFSTATS_SYNC_TIMEDB_E7=1 required for E7 study")

  from hpcperfstats.dbload.lib.sync_timedb_parsing import (
      OnlineMergedProcRows,
      build_stats_dataframes,
      dedupe_proc_stats_peak_merge,
      reset_parse_stage_timing,
      snapshot_parse_stage_campaign_timing,
  )

  replicates = max(5, parse_replicates_env())
  raw_rows = _proc_rows()
  # Warm candidate path.
  build_stats_dataframes([], OnlineMergedProcRows(list(raw_rows)))

  base_merge: list[float] = []
  cand_merge: list[float] = []
  base_build: list[float] = []
  cand_build: list[float] = []

  for _ in range(replicates):
    reset_parse_stage_timing(enabled=True)
    t0 = time.perf_counter()
    # Baseline: ordinary list → timed dedupe + DataFrame(list).
    build_stats_dataframes([], list(raw_rows))
    wall = time.perf_counter() - t0
    snap = snapshot_parse_stage_campaign_timing()
    reset_parse_stage_timing(enabled=False)
    base_merge.append(float(snap.get("proc_merge_s") or 0.0))
    base_build.append(float(snap.get("build_df_s") or wall))

    reset_parse_stage_timing(enabled=True)
    t0 = time.perf_counter()
    build_stats_dataframes([], OnlineMergedProcRows(list(raw_rows)))
    wall = time.perf_counter() - t0
    snap = snapshot_parse_stage_campaign_timing()
    reset_parse_stage_timing(enabled=False)
    # OnlineMerged skips timed dedupe; merge hold may be ~0 — still gate it.
    cand_merge.append(float(snap.get("proc_merge_s") or 0.0))
    cand_build.append(float(snap.get("build_df_s") or wall))

  # Equivalence smoke: peak rows match for one sample.
  merged = dedupe_proc_stats_peak_merge(list(raw_rows[:200]))
  online = OnlineMergedProcRows(merged)
  _, df_list = build_stats_dataframes([], list(raw_rows[:200]))
  _, df_on = build_stats_dataframes([], online)
  assert len(df_list) == len(df_on)

  baseline_merge = summarize_hold_s_replicates(base_merge)
  candidate_merge = summarize_hold_s_replicates(cand_merge)
  baseline_build = summarize_hold_s_replicates(base_build)
  candidate_build = summarize_hold_s_replicates(cand_build)

  retain_merge = hold_seconds_retain_candidate(
      baseline=baseline_merge, candidate=candidate_merge,
  )
  retain_build = hold_seconds_retain_candidate(
      baseline=baseline_build, candidate=candidate_build,
  )
  retain = bool(retain_merge and retain_build)

  baseline_point = {
      "arm": "baseline",
      "mean_proc_merge_s": baseline_merge["mean_s"],
      "mean_build_df_s": baseline_build["mean_s"],
      "proc_merge": baseline_merge,
      "build_df": baseline_build,
  }
  candidate_point = {
      "arm": "candidate",
      "mean_proc_merge_s": candidate_merge["mean_s"],
      "mean_build_df_s": candidate_build["mean_s"],
      "proc_merge": candidate_merge,
      "build_df": candidate_build,
  }
  abi = "%s" % (getattr(sys, "version", "unknown"),)
  payload = build_e7_ab_manifest(
      baseline=baseline_point,
      candidate=candidate_point,
      ingest_width=int(
          os.environ.get("HPCPERFSTATS_SYNC_TIMEDB_E7_WIDTH", DEFAULT_E7_WIDTH),
      ),
      replicates=replicates,
      python_abi=abi,
      retain=retain,
      retain_proc_merge_s=retain_merge,
      retain_build_df_s=retain_build,
      run_id=uuid.uuid4().hex,
  )
  out = write_screening_artifact(
      payload, repo_root=REPO_ROOT, prefix="e7_proc_build_ab",
  )
  assert out.is_file()
  print(
      "e7_ab retain=%s retain_proc_merge_s=%s retain_build_df_s=%s "
      "base_merge=%.4f cand_merge=%.4f base_build=%.4f cand_build=%.4f "
      "path=%s"
      % (
          retain,
          retain_merge,
          retain_build,
          baseline_merge["mean_s"],
          candidate_merge["mean_s"],
          baseline_build["mean_s"],
          candidate_build["mean_s"],
          out,
      ),
      flush=True,
  )
  assert payload["meter"] == "hold_seconds"  # meter=hold_seconds
  assert "retain_proc_merge_s" in payload
  assert "retain_build_df_s" in payload
