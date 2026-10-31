"""Segment primitives match pandas groupby / SQL aggregate semantics."""

import numpy as np
import pandas as pd
import pytest

from hpcperfstats.analysis.metrics.lib.columnar import segments as sg


def test_composite_key_round_trips_and_rejects_overflow():
  k = sg.composite_key(np.array([3, 1]), np.array([7, 9]))
  assert (k >> sg.TIME_BITS).tolist() == [3, 1]
  assert (k & ((1 << sg.TIME_BITS) - 1)).tolist() == [7, 9]
  with pytest.raises(ValueError):
    sg.composite_key(np.array([1 << 16]), np.array([0]))
  assert sg.composite_key().size == 0


def test_group_sum_matches_pandas_filtered_sum():
  rng = np.random.default_rng(1)
  keys = rng.integers(0, 50, size=2000)
  vals = rng.normal(size=2000)
  vals[rng.integers(0, 2000, size=100)] = np.nan
  mask = sg.finite_mask(vals, limit=1.5, nonnegative=True)
  k, s, c = sg.group_sum(keys, vals, mask)
  df = pd.DataFrame({"k": keys, "v": vals})
  df = df[(df.v.notna()) & (df.v.abs() < 1.5) & (df.v >= 0)]
  exp = df.groupby("k")["v"].agg(["sum", "count"]).sort_index()
  assert k.tolist() == exp.index.tolist()
  np.testing.assert_allclose(s, exp["sum"].to_numpy(), rtol=1e-12)
  assert c.tolist() == exp["count"].tolist()


def test_group_stats_count_sum_max():
  keys = np.array([2, 2, 1, 3])
  vals = np.array([1.0, 4.0, 2.0, -1.0])
  k, c, s, m = sg.group_stats(keys, vals)
  assert k.tolist() == [1, 2, 3]
  assert c.tolist() == [1, 2, 1]
  assert s.tolist() == [2.0, 5.0, -1.0]
  assert m.tolist() == [2.0, 4.0, -1.0]
  k2, c2, s2, m2 = sg.group_stats(keys, vals, np.zeros(4, dtype=bool))
  assert k2.size == 0 and c2.size == 0 and s2.size == 0 and m2.size == 0


def test_collapse_residual_copies_prefers_hardware_sum_else_job_max():
  keys = np.array([1, 1, 1, 2, 2, 3])
  dev = np.array([1, 2, -5, -1, -2, -9])
  vals = np.array([1.0, 2.0, 100.0, 5.0, 7.0, np.nan])
  mask = ~np.isnan(vals)
  k, v = sg.collapse_residual_copies(keys, dev, vals, mask)
  assert k.tolist() == [1, 2]
  assert v.tolist() == [3.0, 7.0]


def test_bucket_host_means_matches_job_arc_pandas_rules():
  # Two hosts; host 0 spans three 5-minute buckets, host 1 only one.
  rows = [
    (0, 0, 2.0),
    (0, 60, 4.0),
    (0, 400, 10.0),
    (0, 700, 20.0),
    (1, 0, 7.0),
    (1, 100, np.nan),
  ]
  host = np.array([r[0] for r in rows])
  t_us = np.array([r[1] for r in rows]) * 1_000_000
  val = np.array([r[2] for r in rows])
  hs, means = sg.bucket_host_means(host, t_us, val, 0.5)
  df = pd.DataFrame(
    {"host": host, "time": pd.to_datetime(t_us, unit="us", utc=True), "v": val}
  )
  df["bucket"] = df["time"].dt.floor("5min")
  g = df.groupby(["host", "bucket"], as_index=False)["v"].mean()
  g["v"] *= 0.5
  keep = []
  for _h, sub in g.groupby("host", sort=False):
    keep.extend(list(sub.index) if len(sub) <= 1 else list(sub.index[1:]))
  g = g.loc[keep]
  exp = g.groupby("host")["v"].mean()
  assert hs.tolist() == exp.index.tolist()
  np.testing.assert_allclose(means, exp.to_numpy(), rtol=1e-12)
  assert means.tolist() == [7.5, 3.5]


def test_bucket_host_means_nan_rows_define_presence_like_pandas():
  # Host 0: first bucket finite, second bucket all-NaN -> pandas drops the
  # first bucket and keeps a NaN row, so the host mean is NaN.
  host = np.array([0, 0, 1, 1])
  t_us = np.array([0, 400, 0, 400]) * 1_000_000
  val = np.array([3.0, np.nan, np.nan, 5.0])
  hs, means = sg.bucket_host_means(host, t_us, val, 1.0)
  assert hs.tolist() == [0, 1]
  assert np.isnan(means[0]) and means[1] == 5.0
  hs2, means2 = sg.bucket_host_means(
    np.array([], dtype=np.int64),
    np.array([], dtype=np.int64),
    np.array([], dtype=np.float64),
    1.0,
  )
  assert hs2.size == 0 and means2.size == 0


def test_join_and_lookup_keys():
  li, ri = sg.join_keys(np.array([1, 3, 5, 8]), np.array([0, 3, 8, 9]))
  assert li.tolist() == [1, 3] and ri.tolist() == [1, 2]
  li2, ri2 = sg.join_keys(np.array([1, 2]), np.array([], dtype=np.int64))
  assert li2.size == 0 and ri2.size == 0
  out = sg.lookup_keys(np.array([1, 2, 3]), np.array([3]), np.array([9.0]))
  assert np.isnan(out[0]) and np.isnan(out[1]) and out[2] == 9.0
