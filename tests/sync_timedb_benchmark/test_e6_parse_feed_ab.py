"""E6 feed_line hold-seconds A/B (hypothesis-matched retain)."""
from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path

import pytest

from tests.sync_timedb_benchmark.screening_runner import (
    DEFAULT_E6_WIDTH,
    build_e6_ab_manifest,
    e6_mode_enabled,
    hold_seconds_retain_candidate,
    parse_replicates_env,
    summarize_hold_s_replicates,
    write_screening_artifact,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

pytestmark = [pytest.mark.sync_timedb_bench]

_E6_SCHEMA_LINES = [
    "1709123456 job1 cn001\n",
    "!cpu user,W=48 sys,W=48 idle,W=48\n",
    "!host_proc vm_peak,U=kB rss_peak,U=kB threads\n",
]


def _e6_payload_lines(*, n_samples: int = 400) -> list[str]:
  """Build a mid-size feed_line corpus (schema once, then timed samples)."""
  lines = list(_E6_SCHEMA_LINES)
  t0 = 1_709_123_456
  for i in range(n_samples):
    t = t0 + i
    lines.append("%d job1 cn001\n" % t)
    lines.append("cpu 0 @full %d %d %d\n" % (10 + i, 20 + i, 30 + i))
    lines.append(
        "host_proc bash/1/0/0 @full %d %d %d\n"
        % (100 + i, 50 + i, 4 + (i % 8)),
    )
  return lines


def _legacy_feed_lines_slow(parser, lines) -> None:
  """
  Pre-cache-era feed: re-materialize schema key lists on every line.

  Used as the hold-seconds baseline arm so the candidate (live
  ``feed_lines``) is scored against the redundant-list pattern the E6
  patch targeted, while still calling the live ``feed_line`` for emit
  parity.
  """
  from hpcperfstats.dbload.lib.sync_timedb_parsing import (
      HOST_PROC_KEYS,
      _held_parse_stage,
  )

  with _held_parse_stage("feed_s"):
    for line in lines:
      # Intentional redundant list()/dict lookups — E6 baseline tax.
      _ = list(
          parser.schema.get("host_proc")
          or parser.schema.get("proc")
          or list(HOST_PROC_KEYS),
      )
      _ = list(parser.schema_fast.get("host_proc") or [])
      _ = list(parser.schema.get("cpu") or [])
      parser.feed_line(line)


def test_e6_parse_feed_ab_arm():
  """
  Time baseline vs candidate ``feed_s`` on a synthetic mid-size corpus.

  Requires ``HPCPERFSTATS_SYNC_TIMEDB_E6=1``. Retain uses hold-seconds
  (not files/s). Single-shot A/B (no sequential arm env) — both paths run
  in-process like E8.
  """
  if not e6_mode_enabled():
    pytest.fail("HPCPERFSTATS_SYNC_TIMEDB_E6=1 required for E6 study")

  from hpcperfstats.dbload.lib.sync_timedb_parsing import (
      IncrementalStatsParser,
      reset_parse_stage_timing,
      snapshot_parse_stage_campaign_timing,
  )

  replicates = max(5, parse_replicates_env())
  lines = _e6_payload_lines()
  # Warm candidate path.
  warm = IncrementalStatsParser(0)
  warm.feed_lines(list(lines))
  warm.finish()

  base_samples: list[float] = []
  cand_samples: list[float] = []
  for _ in range(replicates):
    reset_parse_stage_timing(enabled=True)
    p = IncrementalStatsParser(0)
    t0 = time.perf_counter()
    _legacy_feed_lines_slow(p, list(lines))
    p.finish()
    snap = snapshot_parse_stage_campaign_timing()
    reset_parse_stage_timing(enabled=False)
    base_samples.append(float(snap.get("feed_s") or (time.perf_counter() - t0)))

    reset_parse_stage_timing(enabled=True)
    p = IncrementalStatsParser(0)
    t0 = time.perf_counter()
    p.feed_lines(list(lines))
    p.finish()
    snap = snapshot_parse_stage_campaign_timing()
    reset_parse_stage_timing(enabled=False)
    cand_samples.append(float(snap.get("feed_s") or (time.perf_counter() - t0)))

  baseline = summarize_hold_s_replicates(base_samples)
  candidate = summarize_hold_s_replicates(cand_samples)
  # Surface feed_s-named keys for artifact consumers / gates.
  baseline_point = {
      "arm": "baseline",
      "mean_feed_s": baseline["mean_s"],
      "lower_ci_feed_s": baseline["lower_ci_s"],
      "upper_ci_feed_s": baseline["upper_ci_s"],
      **baseline,
  }
  candidate_point = {
      "arm": "candidate",
      "mean_feed_s": candidate["mean_s"],
      "lower_ci_feed_s": candidate["lower_ci_s"],
      "upper_ci_feed_s": candidate["upper_ci_s"],
      **candidate,
  }
  retain = hold_seconds_retain_candidate(
      baseline=baseline, candidate=candidate,
  )
  abi = "%s" % (getattr(sys, "version", "unknown"),)
  payload = build_e6_ab_manifest(
      baseline=baseline_point,
      candidate=candidate_point,
      ingest_width=int(
          os.environ.get("HPCPERFSTATS_SYNC_TIMEDB_E6_WIDTH", DEFAULT_E6_WIDTH),
      ),
      replicates=replicates,
      python_abi=abi,
      retain=retain,
      run_id=uuid.uuid4().hex,
  )
  out = write_screening_artifact(
      payload, repo_root=REPO_ROOT, prefix="e6_parse_feed_ab",
  )
  assert out.is_file()
  print(
      "e6_ab retain=%s meter=hold_seconds base_feed_s=%.4f cand_feed_s=%.4f "
      "path=%s"
      % (retain, baseline["mean_s"], candidate["mean_s"], out),
      flush=True,
  )
  assert payload["meter"] == "hold_seconds"  # meter=hold_seconds
  assert "retain" in payload
