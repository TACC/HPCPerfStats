"""Horizon-shaped fixtures for delta_s / collapse_s hot-path contracts."""
from __future__ import annotations

import time

import numpy as np
import pandas as pd

from hpcperfstats.dbload.lib.sync_timedb_parsing import (
    _COLLAPSE_GROUP_COLS,
    _apply_counter_deltas,
    _collapse_stats_with_deltas,
    _groupby_sum_min_count,
)


def _horizonish_frame(
    *,
    n_hosts: int = 40,
    n_times: int = 12,
    n_cpu_events: int = 20,
    n_gpu_events: int = 8,
    n_dev: int = 4,
    multi_dev_cpu: bool = False,
) -> pd.DataFrame:
  """Build a mid-size frame with optional multi-dev cpu rows for real collapse."""
  rng = np.random.default_rng(42)
  hosts = [f"c{i:04d}" for i in range(n_hosts)]
  times = np.arange(1_700_000_000, 1_700_000_000 + n_times * 10, 10)
  rows: list[tuple] = []
  for h in hosts:
    for t in times:
      for e_i in range(n_cpu_events):
        e = f"cpu_e{e_i}"
        if multi_dev_cpu:
          for d in ("0", "1"):
            rows.append(
                (
                    h, "cpu", d, e, "none", float(t),
                    float(rng.integers(0, 1_000_000)), 64, 1.0,
                ),
            )
        else:
          rows.append(
              (
                  h, "cpu", "", e, "none", float(t),
                  float(rng.integers(0, 1_000_000)), 64, 1.0,
              ),
          )
      for d in range(n_dev):
        for e_i in range(n_gpu_events):
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


def test_groupby_sum_identity_matches_full_groupby():
  """Unique gcols rows must match sum(min_count=1) without needing apply."""
  df = _horizonish_frame(multi_dev_cpu=False)
  df = _apply_counter_deltas(df)
  rest = df[df["type"] == "cpu"].copy()
  assert not rest.duplicated(_COLLAPSE_GROUP_COLS).any()
  actual = _groupby_sum_min_count(rest, _COLLAPSE_GROUP_COLS)
  expected = (
      rest.groupby(_COLLAPSE_GROUP_COLS, observed=True, sort=False)[
          ["value", "delta"]
      ]
      .sum(min_count=1)
      .reset_index()
  )
  pd.testing.assert_frame_equal(
      actual.sort_values(_COLLAPSE_GROUP_COLS).reset_index(drop=True),
      expected.sort_values(_COLLAPSE_GROUP_COLS).reset_index(drop=True),
      check_dtype=False,
  )


def test_groupby_sum_multi_dev_still_sums():
  """Non-unique gcols must still sum across devices."""
  df = _horizonish_frame(n_hosts=4, n_times=3, n_cpu_events=2, n_gpu_events=0, multi_dev_cpu=True)
  df = _apply_counter_deltas(df)
  rest = df[df["type"] == "cpu"].copy()
  assert rest.duplicated(_COLLAPSE_GROUP_COLS).any()
  actual = _groupby_sum_min_count(rest, _COLLAPSE_GROUP_COLS)
  expected = (
      rest.groupby(_COLLAPSE_GROUP_COLS, observed=True, sort=False)[
          ["value", "delta"]
      ]
      .sum(min_count=1)
      .reset_index()
  )
  pd.testing.assert_frame_equal(
      actual.sort_values(_COLLAPSE_GROUP_COLS).reset_index(drop=True),
      expected.sort_values(_COLLAPSE_GROUP_COLS).reset_index(drop=True),
      check_dtype=False,
  )


def test_apply_counter_deltas_equivalence_shuffled_order():
  """Deltas must be order-invariant after sort by counter groups + time."""
  df = _horizonish_frame(n_hosts=8, n_times=6, n_cpu_events=4, n_gpu_events=2)
  a = _apply_counter_deltas(df.copy())
  b = _apply_counter_deltas(df.sample(frac=1.0, random_state=1).reset_index(drop=True))
  keys = ["host", "type", "dev", "event", "time"]
  a = a.sort_values(keys).reset_index(drop=True)
  b = b.sort_values(keys).reset_index(drop=True)
  pd.testing.assert_series_equal(a["delta"], b["delta"], check_names=False)


def test_collapse_after_delta_preserves_gpu_dev():
  """NVIDIA rows keep per-device identity through collapse."""
  df = _horizonish_frame(n_hosts=6, n_times=4, n_cpu_events=2, n_gpu_events=3, n_dev=4)
  out = _collapse_stats_with_deltas(_apply_counter_deltas(df))
  nv = out[out["type"] == "nvidia_gpu"]
  assert not nv.empty
  assert set(nv["dev"].astype(str)) == {"0", "1", "2", "3"}


def test_delta_collapse_hotpath_smoke_timing():
  """Smoke: full delta+collapse on mid frame finishes; prints marker for gates."""
  df = _horizonish_frame()
  t0 = time.perf_counter()
  out = _collapse_stats_with_deltas(_apply_counter_deltas(df))
  elapsed = time.perf_counter() - t0
  assert not out.empty
  assert elapsed < 30.0
  print("delta_collapse_hotpath_ok")
