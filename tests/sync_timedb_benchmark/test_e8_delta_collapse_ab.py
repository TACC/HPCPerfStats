"""E8 delta_s / collapse_s hold-seconds A/B (hypothesis-matched retain)."""
from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tests.sync_timedb_benchmark.screening_runner import (
    DEFAULT_E8_REPLICATES,
    build_e8_ab_manifest,
    e8_mode_enabled,
    e8_retain_candidate,
    summarize_hold_s_replicates,
    write_screening_artifact,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

pytestmark = [pytest.mark.sync_timedb_bench]


def _legacy_apply_counter_deltas(stats_df, carry=None):
  """Pre-E8 apply path (object keys, pandas wrap) for baseline arm."""
  from hpcperfstats.dbload.lib.sync_timedb_parsing import _COUNTER_GROUP_COLS

  stats_df = stats_df.sort_values(by=_COUNTER_GROUP_COLS + ["time"])
  stats_df["delta"] = stats_df.groupby(
      _COUNTER_GROUP_COLS, observed=True,
  )["value"].diff()
  if carry is not None and carry.raw:
    first = stats_df.groupby(_COUNTER_GROUP_COLS, observed=True).head(1)
    if not first.empty:
      hosts = first["host"].to_numpy()
      types = first["type"].to_numpy()
      devs = first["dev"].to_numpy()
      events = first["event"].to_numpy()
      values = first["value"].to_numpy(dtype=np.float64, copy=False)
      idxs = first.index.to_numpy()
      carry_deltas = np.full(len(first), np.nan, dtype=np.float64)
      apply_mask = np.zeros(len(first), dtype=bool)
      for i in range(len(first)):
        prev = carry.raw.get((hosts[i], types[i], devs[i], events[i]))
        if prev is None:
          continue
        prev_value = prev[0] if isinstance(prev, tuple) else prev["value"]
        carry_deltas[i] = float(values[i]) - float(prev_value)
        apply_mask[i] = True
      if apply_mask.any():
        stats_df.loc[idxs[apply_mask], "delta"] = carry_deltas[apply_mask]
  stats_df["delta"] = stats_df["delta"].mask(
      stats_df["delta"] < 0, 2 ** stats_df["wid"] + stats_df["delta"],
  )
  stats_df["delta"] = stats_df["delta"] * stats_df["mult"]
  if carry is not None:
    last = stats_df.groupby(_COUNTER_GROUP_COLS, observed=True).tail(1)
    if not last.empty:
      hosts = last["host"].to_numpy()
      types = last["type"].to_numpy()
      devs = last["dev"].to_numpy()
      events = last["event"].to_numpy()
      values = last["value"].to_numpy(dtype=np.float64, copy=False)
      wids = last["wid"].to_numpy(copy=False)
      mults = last["mult"].to_numpy(dtype=np.float64, copy=False)
      times = last["time"].to_numpy(dtype=np.float64, copy=False)
      for i in range(len(last)):
        carry.raw[(hosts[i], types[i], devs[i], events[i])] = (
            float(values[i]),
            int(wids[i]),
            float(mults[i]),
            float(times[i]),
        )
  stats_df.drop(columns=["wid", "mult"], inplace=True)
  return stats_df


def _legacy_groupby_sum_min_count(df, gcols):
  """Pre-E8 groupby sum without identity short-circuit."""
  from hpcperfstats.dbload.lib.sync_timedb_parsing import _empty_delta_arc_frame

  if df.empty:
    return _empty_delta_arc_frame()
  grouped = df.groupby(gcols, observed=True, sort=False)
  out = grouped[["value", "delta"]].sum(min_count=1)
  if "jid" in getattr(df, "columns", ()):
    out = out.join(grouped["jid"].first())
  return out.reset_index()


def _horizon_frame() -> pd.DataFrame:
  rng = np.random.default_rng(7)
  n_hosts, n_times, n_cpu, n_gpu, n_dev = 80, 16, 24, 8, 4
  hosts = [f"c{i:04d}" for i in range(n_hosts)]
  times = np.arange(1_700_000_000, 1_700_000_000 + n_times * 10, 10)
  rows = []
  for h in hosts:
    for t in times:
      for e_i in range(n_cpu):
        rows.append(
            (
                h, "cpu", "", f"cpu_e{e_i}", "none", float(t),
                float(rng.integers(0, 1_000_000)), 64, 1.0,
            ),
        )
      for d in range(n_dev):
        for e_i in range(n_gpu):
          rows.append(
              (
                  h, "nvidia_gpu", str(d), f"gpu_e{e_i}", "none", float(t),
                  float(rng.integers(0, 1_000_000)), 64, 1.0,
              ),
          )
  return pd.DataFrame(
      rows,
      columns=[
          "host", "type", "dev", "event", "unit", "time", "value", "wid", "mult",
      ],
  )


def test_e8_delta_collapse_ab(monkeypatch):
  """
  Time baseline vs candidate ``delta_s`` / ``collapse_s`` on a Horizon frame.

  Requires ``HPCPERFSTATS_SYNC_TIMEDB_E8=1``. Retain uses per-hold seconds
  (not files/s). Baseline forces legacy apply/groupby via monkeypatch.
  """
  if not e8_mode_enabled():
    pytest.fail("HPCPERFSTATS_SYNC_TIMEDB_E8=1 required for E8 study")

  from hpcperfstats.dbload.lib import sync_timedb_parsing as parsing

  replicates = int(
      os.environ.get(
          "HPCPERFSTATS_SYNC_TIMEDB_SCREEN_REPLICATES",
          str(DEFAULT_E8_REPLICATES),
      ),
  )
  replicates = max(3, replicates)
  frame = _horizon_frame()
  orig_sum = parsing._groupby_sum_min_count

  # Warm candidate path.
  parsing._collapse_stats_with_deltas(parsing._apply_counter_deltas(frame.copy()))

  base_delta_samples: list[float] = []
  cand_delta_samples: list[float] = []
  base_collapse_samples: list[float] = []
  cand_collapse_samples: list[float] = []
  for _ in range(replicates):
    d = frame.copy()
    t0 = time.perf_counter()
    _legacy_apply_counter_deltas(d)
    base_delta_samples.append(time.perf_counter() - t0)

    d = frame.copy()
    t0 = time.perf_counter()
    parsing._apply_counter_deltas(d)
    cand_delta_samples.append(time.perf_counter() - t0)

    d = _legacy_apply_counter_deltas(frame.copy())
    monkeypatch.setattr(
        parsing, "_groupby_sum_min_count", _legacy_groupby_sum_min_count,
    )
    t0 = time.perf_counter()
    parsing._collapse_stats_with_deltas(d)
    base_collapse_samples.append(time.perf_counter() - t0)
    monkeypatch.setattr(parsing, "_groupby_sum_min_count", orig_sum)

    d = parsing._apply_counter_deltas(frame.copy())
    t0 = time.perf_counter()
    parsing._collapse_stats_with_deltas(d)
    cand_collapse_samples.append(time.perf_counter() - t0)

  baseline_delta = summarize_hold_s_replicates(base_delta_samples)
  candidate_delta = summarize_hold_s_replicates(cand_delta_samples)
  baseline_collapse = summarize_hold_s_replicates(base_collapse_samples)
  candidate_collapse = summarize_hold_s_replicates(cand_collapse_samples)
  decision = e8_retain_candidate(
      baseline_delta=baseline_delta,
      candidate_delta=candidate_delta,
      baseline_collapse=baseline_collapse,
      candidate_collapse=candidate_collapse,
  )
  abi = "%s" % (getattr(sys, "version", "unknown"),)
  payload = build_e8_ab_manifest(
      baseline_delta=baseline_delta,
      candidate_delta=candidate_delta,
      baseline_collapse=baseline_collapse,
      candidate_collapse=candidate_collapse,
      retain_delta_s=decision["retain_delta_s"],
      retain_collapse_s=decision["retain_collapse_s"],
      retain=decision["retain"],
      replicates=replicates,
      python_abi=abi,
      run_id=uuid.uuid4().hex,
  )
  out = write_screening_artifact(
      payload, repo_root=REPO_ROOT, prefix="e8_delta_collapse_ab",
  )
  assert out.is_file()
  print(
      "e8_ab retain=%s retain_delta_s=%s retain_collapse_s=%s "
      "base_delta=%.4f cand_delta=%.4f base_collapse=%.4f cand_collapse=%.4f "
      "path=%s"
      % (
          decision["retain"],
          decision["retain_delta_s"],
          decision["retain_collapse_s"],
          baseline_delta["mean_s"],
          candidate_delta["mean_s"],
          baseline_collapse["mean_s"],
          candidate_collapse["mean_s"],
          out,
      ),
      flush=True,
  )
  assert "retain" in payload
