"""Horizon-shaped fixtures for delta_s / collapse_s hot-path contracts."""

from __future__ import annotations

import itertools
import time

import numpy as np
import pandas as pd

from hpcperfstats.dbload.lib.sync_timedb_parsing import (
  _COLLAPSE_GROUP_COLS,
  _COUNTER_GROUP_COLS,
  DeltaCarryState,
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
                h,
                "cpu",
                d,
                e,
                "none",
                float(t),
                float(rng.integers(0, 1_000_000)),
                64,
                1.0,
              ),
            )
        else:
          rows.append(
            (
              h,
              "cpu",
              "",
              e,
              "none",
              float(t),
              float(rng.integers(0, 1_000_000)),
              64,
              1.0,
            ),
          )
      for d in range(n_dev):
        for e_i in range(n_gpu_events):
          rows.append(
            (
              h,
              "nvidia_gpu",
              str(d),
              f"gpu_e{e_i}",
              "none",
              float(t),
              float(rng.integers(0, 1_000_000)),
              64,
              1.0,
            ),
          )
  return pd.DataFrame(
    rows,
    columns=[
      "host",
      "type",
      "dev",
      "event",
      "unit",
      "time",
      "value",
      "wid",
      "mult",
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
  df = _horizonish_frame(
    n_hosts=4, n_times=3, n_cpu_events=2, n_gpu_events=0, multi_dev_cpu=True
  )
  df = _apply_counter_deltas(df)
  rest = df[df["type"] == "cpu"].copy()
  assert rest.duplicated(_COLLAPSE_GROUP_COLS).any()
  actual = _groupby_sum_min_count(
    rest,
    _COLLAPSE_GROUP_COLS,
    assume_duplicates=True,
  )
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


def test_multi_dev_category_before_groupby_local_retain():
  """
  3b: category-before-groupby on multi_dev frames must not regress wall.

  Unique-gcols A/B is invalid for this gate (E8 non-transfer).
  """
  df = _horizonish_frame(
    n_hosts=30,
    n_times=10,
    n_cpu_events=16,
    n_gpu_events=0,
    multi_dev_cpu=True,
  )
  df = _apply_counter_deltas(df)
  rest = df[df["type"] == "cpu"].copy()
  assert rest.duplicated(_COLLAPSE_GROUP_COLS).any()

  def _object_groupby(frame):
    g = frame.copy()
    for col in _COLLAPSE_GROUP_COLS:
      g[col] = g[col].astype(object)
    return (
      g.groupby(_COLLAPSE_GROUP_COLS, observed=True, sort=False)[
        ["value", "delta"]
      ]
      .sum(min_count=1)
      .reset_index()
    )

  _ = _groupby_sum_min_count(
    rest.copy(), _COLLAPSE_GROUP_COLS, assume_duplicates=True
  )
  _ = _object_groupby(rest)

  n = 5
  base_times = []
  cand_times = []
  for _ in range(n):
    t0 = time.perf_counter()
    _object_groupby(rest)
    base_times.append(time.perf_counter() - t0)
    t0 = time.perf_counter()
    _groupby_sum_min_count(
      rest.copy(),
      _COLLAPSE_GROUP_COLS,
      assume_duplicates=True,
    )
    cand_times.append(time.perf_counter() - t0)
  base_mean = sum(base_times) / n
  cand_mean = sum(cand_times) / n
  retain = cand_mean <= base_mean * 1.05
  print(
    f"multi_dev_collapse_retain={str(retain).lower()} base_mean={base_mean:.6f} cand_mean={cand_mean:.6f}",
  )
  assert cand_mean < 30.0
  if retain:
    print("multi_dev_collapse_retain_ok")
  else:
    print("multi_dev_collapse_no_cut_no_retain")


def test_apply_counter_deltas_equivalence_shuffled_order():
  """Deltas must be order-invariant after sort by counter groups + time."""
  df = _horizonish_frame(n_hosts=8, n_times=6, n_cpu_events=4, n_gpu_events=2)
  a = _apply_counter_deltas(df.copy())
  b = _apply_counter_deltas(
    df.sample(frac=1.0, random_state=1).reset_index(drop=True)
  )
  keys = ["host", "type", "dev", "event", "time"]
  a = a.sort_values(keys).reset_index(drop=True)
  b = b.sort_values(keys).reset_index(drop=True)
  pd.testing.assert_series_equal(a["delta"], b["delta"], check_names=False)


def test_collapse_after_delta_preserves_gpu_dev():
  """NVIDIA rows keep per-device identity through collapse."""
  df = _horizonish_frame(
    n_hosts=6, n_times=4, n_cpu_events=2, n_gpu_events=3, n_dev=4
  )
  out = _collapse_stats_with_deltas(_apply_counter_deltas(df))
  nv = out[out["type"] == "nvidia_gpu"]
  assert not nv.empty
  assert set(nv["dev"].astype(str)) == {"0", "1", "2", "3"}


def test_collapse_nvidia_gpu_single_pass_matches_isin_partition():
  """Event-class map partition must match four-isin class frames."""
  from hpcperfstats.dbload.lib.sync_timedb_parsing import (
    _COLLAPSE_GROUP_COLS_WITH_DEV,
    _NVIDIA_GPU_MAX_EVENTS,
    _NVIDIA_GPU_MEAN_EVENTS,
    _NVIDIA_GPU_OR_EVENTS,
    _NVIDIA_GPU_SUM_EVENTS,
    _collapse_nvidia_gpu_vectorized,
    _groupby_sum_min_count,
    _nvidia_bitwise_or_values,
    _optional_jid_first_agg,
  )

  rng = np.random.default_rng(7)
  sum_ev = sorted(_NVIDIA_GPU_SUM_EVENTS)[:6]
  max_ev = sorted(_NVIDIA_GPU_MAX_EVENTS)
  mean_ev = sorted(_NVIDIA_GPU_MEAN_EVENTS)
  or_ev = sorted(_NVIDIA_GPU_OR_EVENTS)
  events = sum_ev + max_ev + mean_ev + or_ev
  rows = []
  for h in ("c0001", "c0002"):
    for t in (1_700_000_000.0, 1_700_000_010.0):
      for d in ("0", "1", "2", "3"):
        for e in events:
          rows.append(
            (
              h,
              "nvidia_gpu",
              d,
              e,
              "none",
              t,
              float(rng.integers(1, 1000)),
              0.0,
            ),
          )
  nv = pd.DataFrame(
    rows,
    columns=[
      "host",
      "type",
      "dev",
      "event",
      "unit",
      "time",
      "value",
      "delta",
    ],
  )
  gcols = list(_COLLAPSE_GROUP_COLS_WITH_DEV)
  actual = _collapse_nvidia_gpu_vectorized(nv.copy(), gcols)

  def _legacy_isin(frame):
    parts = []
    known = (
      _NVIDIA_GPU_SUM_EVENTS
      | _NVIDIA_GPU_MAX_EVENTS
      | _NVIDIA_GPU_MEAN_EVENTS
      | _NVIDIA_GPU_OR_EVENTS
    )
    sum_df = frame.loc[
      ~frame["event"].isin(
        _NVIDIA_GPU_MAX_EVENTS
        | _NVIDIA_GPU_MEAN_EVENTS
        | _NVIDIA_GPU_OR_EVENTS,
      )
    ]
    if not sum_df.empty:
      parts.append(_groupby_sum_min_count(sum_df, gcols))
    max_df = frame.loc[frame["event"].isin(_NVIDIA_GPU_MAX_EVENTS)]
    if not max_df.empty:
      parts.append(
        max_df.groupby(gcols, observed=True)
        .agg(
          value=("value", "max"),
          delta=("delta", "mean"),
          **_optional_jid_first_agg(max_df),
        )
        .reset_index(),
      )
    mean_df = frame.loc[frame["event"].isin(_NVIDIA_GPU_MEAN_EVENTS)]
    if not mean_df.empty:
      parts.append(
        mean_df.groupby(gcols, observed=True)
        .agg(
          value=("value", "mean"),
          delta=("delta", "mean"),
          **_optional_jid_first_agg(mean_df),
        )
        .reset_index(),
      )
    or_df = frame.loc[frame["event"].isin(_NVIDIA_GPU_OR_EVENTS)]
    if not or_df.empty:
      or_collapsed = (
        or_df.groupby(gcols, observed=True)
        .agg(
          value=("value", _nvidia_bitwise_or_values),
          delta=("delta", "sum"),
          _delta_n=("delta", "count"),
          **_optional_jid_first_agg(or_df),
        )
        .reset_index()
      )
      or_collapsed["delta"] = or_collapsed["delta"].where(
        or_collapsed["_delta_n"] > 0,
      )
      parts.append(or_collapsed.drop(columns=["_delta_n"]))
    assert known
    return pd.concat(parts, ignore_index=True)

  expected = _legacy_isin(nv.copy())
  keys = [*gcols, "event"]
  actual = actual.sort_values(keys).reset_index(drop=True)
  expected = expected.sort_values(keys).reset_index(drop=True)
  pd.testing.assert_frame_equal(
    actual,
    expected,
    check_dtype=False,
    check_categorical=False,
  )


def test_collapse_gpu_single_pass_local_retain():
  """
  Wave2: single-pass event map must not regress wall vs four ``isin`` scans.

  Prints retain marker for gates; soft-fail only if absolute wall is absurd.
  """
  from hpcperfstats.dbload.lib.sync_timedb_parsing import (
    _COLLAPSE_GROUP_COLS_WITH_DEV,
    _NVIDIA_GPU_MAX_EVENTS,
    _NVIDIA_GPU_MEAN_EVENTS,
    _NVIDIA_GPU_OR_EVENTS,
    _NVIDIA_GPU_SUM_EVENTS,
    _collapse_nvidia_gpu_vectorized,
    _groupby_sum_min_count,
    _nvidia_bitwise_or_values,
    _optional_jid_first_agg,
  )

  rng = np.random.default_rng(11)
  events = (
    sorted(_NVIDIA_GPU_SUM_EVENTS)[:10]
    + sorted(_NVIDIA_GPU_MAX_EVENTS)
    + sorted(_NVIDIA_GPU_MEAN_EVENTS)
    + sorted(_NVIDIA_GPU_OR_EVENTS)
  )
  rows = []
  for h_i in range(20):
    h = f"c{h_i:04d}"
    for t_i in range(8):
      t = float(1_700_000_000 + t_i * 10)
      for d in range(8):
        for e in events:
          rows.append(
            (
              h,
              "nvidia_gpu",
              str(d),
              e,
              "none",
              t,
              float(rng.integers(1, 10_000)),
              0.0,
            ),
          )
  nv = pd.DataFrame(
    rows,
    columns=[
      "host",
      "type",
      "dev",
      "event",
      "unit",
      "time",
      "value",
      "delta",
    ],
  )
  gcols = list(_COLLAPSE_GROUP_COLS_WITH_DEV)

  def _legacy_isin(frame):
    parts = []
    sum_df = frame.loc[
      ~frame["event"].isin(
        _NVIDIA_GPU_MAX_EVENTS
        | _NVIDIA_GPU_MEAN_EVENTS
        | _NVIDIA_GPU_OR_EVENTS,
      )
    ]
    if not sum_df.empty:
      parts.append(_groupby_sum_min_count(sum_df, gcols))
    max_df = frame.loc[frame["event"].isin(_NVIDIA_GPU_MAX_EVENTS)]
    if not max_df.empty:
      parts.append(
        max_df.groupby(gcols, observed=True)
        .agg(
          value=("value", "max"),
          delta=("delta", "mean"),
          **_optional_jid_first_agg(max_df),
        )
        .reset_index(),
      )
    mean_df = frame.loc[frame["event"].isin(_NVIDIA_GPU_MEAN_EVENTS)]
    if not mean_df.empty:
      parts.append(
        mean_df.groupby(gcols, observed=True)
        .agg(
          value=("value", "mean"),
          delta=("delta", "mean"),
          **_optional_jid_first_agg(mean_df),
        )
        .reset_index(),
      )
    or_df = frame.loc[frame["event"].isin(_NVIDIA_GPU_OR_EVENTS)]
    if not or_df.empty:
      or_collapsed = (
        or_df.groupby(gcols, observed=True)
        .agg(
          value=("value", _nvidia_bitwise_or_values),
          delta=("delta", "sum"),
          _delta_n=("delta", "count"),
          **_optional_jid_first_agg(or_df),
        )
        .reset_index()
      )
      or_collapsed["delta"] = or_collapsed["delta"].where(
        or_collapsed["_delta_n"] > 0,
      )
      parts.append(or_collapsed.drop(columns=["_delta_n"]))
    return pd.concat(parts, ignore_index=True)

  _ = _collapse_nvidia_gpu_vectorized(nv.copy(), gcols)
  _ = _legacy_isin(nv.copy())
  n = 4
  base_times = []
  cand_times = []
  for _ in range(n):
    t0 = time.perf_counter()
    _legacy_isin(nv.copy())
    base_times.append(time.perf_counter() - t0)
    t0 = time.perf_counter()
    _collapse_nvidia_gpu_vectorized(nv.copy(), gcols)
    cand_times.append(time.perf_counter() - t0)
  base_mean = sum(base_times) / n
  cand_mean = sum(cand_times) / n
  retain = cand_mean <= base_mean * 1.05
  print(
    f"collapse_gpu_retain={str(retain).lower()} base_mean={base_mean:.6f} cand_mean={cand_mean:.6f}",
  )
  assert cand_mean < 60.0
  if retain:
    print("collapse_gpu_retain_ok")
  else:
    print("collapse_gpu_no_cut_no_retain")


def test_collapse_nvidia_gpu_unique_keys_skip_per_group_or(monkeypatch):
  """Unique device keys must mask OR rows without a per-group Python reducer."""
  from hpcperfstats.dbload.lib import sync_timedb_parsing as parsing

  def _boom(_series):
    raise AssertionError("per-group OR")

  monkeypatch.setattr(parsing, "_nvidia_bitwise_or_values", _boom)
  gcols = list(parsing._COLLAPSE_GROUP_COLS_WITH_DEV)
  or_event = sorted(parsing._NVIDIA_GPU_OR_EVENTS)[0]
  max_event = sorted(parsing._NVIDIA_GPU_MAX_EVENTS)[0]
  frame = pd.DataFrame(
    [
      ("h", "nvidia_gpu", "0", or_event, "none", 1.0, np.nan, np.nan, "j1"),
      ("h", "nvidia_gpu", "0", max_event, "none", 1.0, 9.0, 1.5, "j1"),
      ("h", "nvidia_gpu", "1", or_event, "none", 1.0, 5.0, 0.0, "j1"),
    ],
    columns=[
      "host",
      "type",
      "dev",
      "event",
      "unit",
      "time",
      "value",
      "delta",
      "jid",
    ],
  )
  out = parsing._collapse_nvidia_gpu_vectorized(frame, gcols)
  or_nan = out[(out["dev"] == "0") & (out["event"] == or_event)].iloc[0]
  or_bit = out[(out["dev"] == "1") & (out["event"] == or_event)].iloc[0]
  max_row = out[out["event"] == max_event].iloc[0]
  assert or_nan["value"] == 0.0
  assert pd.isna(or_nan["delta"])
  assert or_bit["value"] == 5.0
  assert or_bit["jid"] == "j1"
  assert max_row["value"] == 9.0
  assert max_row["delta"] == 1.5


def test_collapse_nvidia_gpu_duplicate_or_still_combines_bits():
  """Repeated keys still bitwise-OR clocks_event_reasons across the rows."""
  from hpcperfstats.dbload.lib.sync_timedb_parsing import (
    _COLLAPSE_GROUP_COLS_WITH_DEV,
    _NVIDIA_GPU_OR_EVENTS,
    _collapse_nvidia_gpu_vectorized,
  )

  or_event = sorted(_NVIDIA_GPU_OR_EVENTS)[0]
  frame = pd.DataFrame(
    [
      ("h", "nvidia_gpu", "0", or_event, "none", 1.0, 1.0, 0.0),
      ("h", "nvidia_gpu", "0", or_event, "none", 1.0, 2.0, 0.0),
    ],
    columns=["host", "type", "dev", "event", "unit", "time", "value", "delta"],
  )
  out = _collapse_nvidia_gpu_vectorized(
    frame, list(_COLLAPSE_GROUP_COLS_WITH_DEV)
  )
  assert len(out) == 1
  assert out.iloc[0]["value"] == 3.0


def _apply_counter_deltas_legacy_sort_groupby(
  stats_df: pd.DataFrame,
) -> pd.DataFrame:
  """Pre-E13 sort + groupby diff reference for local retain gate only."""
  frame = stats_df.copy()
  for col in ("host", "type", "dev", "event"):
    if col in frame.columns and not isinstance(
      frame[col].dtype,
      pd.CategoricalDtype,
    ):
      frame[col] = frame[col].astype("category")
  frame = frame.sort_values(by=["host", "type", "dev", "event", "time"])
  frame["delta"] = frame.groupby(
    ["host", "type", "dev", "event"], observed=True
  )["value"].diff()
  wid = frame["wid"].to_numpy(dtype=np.float64, copy=False)
  delta = frame["delta"].to_numpy(dtype=np.float64, copy=False)
  wrap = (delta < 0) & np.isfinite(delta)
  if wrap.any():
    delta = delta.copy()
    delta[wrap] = (2.0 ** wid[wrap]) + delta[wrap]
  mult = frame["mult"].to_numpy(dtype=np.float64, copy=False)
  frame["delta"] = delta * mult
  frame.drop(columns=["wid", "mult"], inplace=True)
  return frame


def test_delta_e13_lexsort_multi_dev_retain():
  """
  E13: lexsort + vectorized diff must retain vs legacy sort/groupby on multi_dev.
  """
  df = _horizonish_frame(
    n_hosts=80,
    n_times=24,
    n_cpu_events=32,
    n_gpu_events=0,
    multi_dev_cpu=True,
  )
  keys = ["host", "type", "dev", "event", "time"]
  n = 3
  base_times = []
  cand_times = []
  for _ in range(n):
    t0 = time.perf_counter()
    _apply_counter_deltas_legacy_sort_groupby(df.copy())
    base_times.append(time.perf_counter() - t0)
    t0 = time.perf_counter()
    _apply_counter_deltas(df.copy())
    cand_times.append(time.perf_counter() - t0)
  base_mean = sum(base_times) / n
  cand_mean = sum(cand_times) / n
  retain = cand_mean <= base_mean * 1.05
  legacy = _apply_counter_deltas_legacy_sort_groupby(df.copy())
  current = _apply_counter_deltas(df.copy())
  legacy = legacy.sort_values(keys).reset_index(drop=True)
  current = current.sort_values(keys).reset_index(drop=True)
  pd.testing.assert_series_equal(
    legacy["delta"],
    current["delta"],
    check_names=False,
  )
  print(
    f"delta_e13_retain={str(retain).lower()} base_mean={base_mean:.6f} cand_mean={cand_mean:.6f}",
  )
  assert cand_mean < 60.0
  assert retain
  print("delta_wave2_retain_ok")


def _apply_counter_deltas_row_loop_ref(stats_df, carry=None):
  """Frozen pre-E16 per-row carry loops. Product code must match this."""
  for col in _COUNTER_GROUP_COLS:
    if col in stats_df.columns and not isinstance(
      stats_df[col].dtype,
      pd.CategoricalDtype,
    ):
      stats_df[col] = stats_df[col].astype("category")
  code_cols = [
    stats_df[col].cat.codes.to_numpy(dtype=np.int64, copy=False)
    for col in _COUNTER_GROUP_COLS
  ]
  sizes = tuple(int(c.max()) + 1 for c in code_cols)
  times = stats_df["time"].to_numpy(dtype=np.float64, copy=False)
  if int(np.prod(sizes, dtype=np.float64)) <= (2**63 - 1):
    gid = np.ravel_multi_index(np.vstack(code_cols), sizes)
    order = np.lexsort((times, gid))
    stats_df = stats_df.iloc[order].reset_index(drop=True)
    gid = gid[order]
    n = len(gid)
    values = stats_df["value"].to_numpy(dtype=np.float64, copy=False)
    same = np.ones(n, dtype=bool)
    if n > 1:
      same[1:] = gid[1:] == gid[:-1]
    delta = np.empty(n, dtype=np.float64)
    delta[0] = np.nan
    if n > 1:
      delta[1:] = np.where(same[1:], values[1:] - values[:-1], np.nan)
    stats_df["delta"] = delta
    is_first = np.ones(n, dtype=bool)
    if n > 1:
      is_first[1:] = gid[1:] != gid[:-1]
    is_last = np.ones(n, dtype=bool)
    if n > 1:
      is_last[:-1] = gid[:-1] != gid[1:]
  else:
    stats_df = stats_df.sort_values(by=[*_COUNTER_GROUP_COLS, "time"])
    stats_df["delta"] = stats_df.groupby(_COUNTER_GROUP_COLS, observed=True)[
      "value"
    ].diff()
    is_first = is_last = None

  if carry is not None and carry.raw:
    if is_first is not None:
      first = stats_df.loc[is_first]
    else:
      first = stats_df.groupby(_COUNTER_GROUP_COLS, observed=True).head(1)
    if not first.empty:
      hosts = first["host"].astype(object).to_numpy()
      types = first["type"].astype(object).to_numpy()
      devs = first["dev"].astype(object).to_numpy()
      events = first["event"].astype(object).to_numpy()
      values = first["value"].to_numpy(dtype=np.float64, copy=False)
      idxs = first.index.to_numpy()
      carry_deltas = np.full(len(first), np.nan, dtype=np.float64)
      apply_mask = np.zeros(len(first), dtype=bool)
      raw = carry.raw
      for i in range(len(first)):
        prev = raw.get((hosts[i], types[i], devs[i], events[i]))
        if prev is None:
          continue
        prev_value = prev[0] if isinstance(prev, tuple) else prev["value"]
        carry_deltas[i] = float(values[i]) - float(prev_value)
        apply_mask[i] = True
      if apply_mask.any():
        stats_df.loc[idxs[apply_mask], "delta"] = carry_deltas[apply_mask]

  wid = stats_df["wid"].to_numpy(dtype=np.float64, copy=False)
  delta = stats_df["delta"].to_numpy(dtype=np.float64, copy=False)
  wrap = (delta < 0) & np.isfinite(delta)
  if wrap.any():
    delta = delta.copy()
    delta[wrap] = (2.0 ** wid[wrap]) + delta[wrap]
  mult = stats_df["mult"].to_numpy(dtype=np.float64, copy=False)
  stats_df["delta"] = delta * mult

  if carry is not None:
    if is_last is not None:
      last = stats_df.loc[is_last]
    else:
      last = stats_df.groupby(_COUNTER_GROUP_COLS, observed=True).tail(1)
    if not last.empty:
      hosts = last["host"].astype(object).to_numpy()
      types = last["type"].astype(object).to_numpy()
      devs = last["dev"].astype(object).to_numpy()
      events = last["event"].astype(object).to_numpy()
      values = last["value"].to_numpy(dtype=np.float64, copy=False)
      wids = last["wid"].to_numpy(copy=False)
      mults = last["mult"].to_numpy(dtype=np.float64, copy=False)
      times_last = last["time"].to_numpy(dtype=np.float64, copy=False)
      raw = carry.raw
      for i in range(len(last)):
        raw[(hosts[i], types[i], devs[i], events[i])] = (
          float(values[i]),
          int(wids[i]),
          float(mults[i]),
          float(times_last[i]),
        )

  stats_df.drop(columns=["wid", "mult"], inplace=True)
  return stats_df


def _carry_split_frame() -> pd.DataFrame:
  """Multi-host frame with a wrap, a late group, and a seeded carry key."""
  rows = []
  for host, dev, event, samples in (
    ("h0", "0", "cas", ((10.0, 100.0), (20.0, 140.0), (30.0, 10.0))),
    ("h0", "1", "cas", ((10.0, 50.0), (20.0, 80.0))),
    ("h1", "", "cycles", ((15.0, 5.0), (25.0, 9.0), (35.0, 4.0))),
    ("h2", "0", "late", ((40.0, 7.0),)),
  ):
    for stamp, value in samples:
      rows.append((host, "cpu", dev, event, "none", stamp, value, 8, 2.0))
  return pd.DataFrame(
    rows,
    columns=[
      "host",
      "type",
      "dev",
      "event",
      "unit",
      "time",
      "value",
      "wid",
      "mult",
    ],
  )


def test_apply_counter_deltas_carry_split_matches_single_pass():
  """Chunked carry matches the frozen per-row loops, including wrap and gaps."""
  frame = _carry_split_frame()
  for n_chunks in (1, 2, 7, len(frame)):
    edges = np.linspace(0, len(frame), n_chunks + 1, dtype=int)
    parts = [
      frame.iloc[start:end]
      for start, end in itertools.pairwise(edges)
      if end > start
    ]
    ref_carry = DeltaCarryState()
    new_carry = DeltaCarryState()
    ref_carry.raw[("h0", "cpu", "0", "cas")] = (90.0, 8, 2.0, 0.0)
    new_carry.raw[("h0", "cpu", "0", "cas")] = (90.0, 8, 2.0, 0.0)
    ref_out = [
      _apply_counter_deltas_row_loop_ref(part.copy(), ref_carry)
      for part in parts
    ]
    new_out = [_apply_counter_deltas(part.copy(), new_carry) for part in parts]
    ref = pd.concat(ref_out, ignore_index=True)
    got = pd.concat(new_out, ignore_index=True)
    pd.testing.assert_frame_equal(got, ref, check_dtype=False)
    assert ref_carry.raw.keys() == new_carry.raw.keys()
    for key, ref_tuple in ref_carry.raw.items():
      got_tuple = new_carry.raw[key]
      assert len(got_tuple) == 4
      assert got_tuple[0] == ref_tuple[0]
      assert int(got_tuple[1]) == int(ref_tuple[1])
      assert got_tuple[2] == ref_tuple[2]
      assert got_tuple[3] == ref_tuple[3]
    late = got[(got["host"] == "h2") & (got["event"] == "late")]
    assert len(late) == 1
    assert pd.isna(late["delta"].iloc[0])
    wrapped = got[
      (got["host"] == "h0") & (got["dev"] == "0") & (got["time"] == 30.0)
    ]
    assert len(wrapped) == 1
    assert wrapped["delta"].iloc[0] == (2.0**8 + (10.0 - 140.0)) * 2.0


def test_delta_collapse_hotpath_smoke_timing():
  """Smoke: full delta+collapse on mid frame finishes; prints marker for gates."""
  df = _horizonish_frame()
  t0 = time.perf_counter()
  out = _collapse_stats_with_deltas(_apply_counter_deltas(df))
  elapsed = time.perf_counter() - t0
  assert not out.empty
  assert elapsed < 30.0
  print("delta_collapse_hotpath_ok")
