"""FrameIndex reductions routed through the segment kernels keep their output.

``sum_per_sample``, ``delta_sum_by_event`` and ``collapse_residual_copies``
go through ``segments.group_reduce`` / ``segments.collapse_residual_copies``
(Codon on the pipeline interpreter, NumPy reference here). The legacy
oracles below are the pre-routing implementations (``np.unique`` +
``searchsorted`` + ``np.add.at``, the per-series pairwise ``.sum()`` loop
and the per-row ``eix`` comprehension), so the host run proves the routed
code returns the same arrays on representative frames. ``delta_sum_by_event``
now sums in row order, so it is compared bit-for-bit with a sequential sum
and within ``rtol=1e-12`` with the legacy pairwise total.
"""

from __future__ import annotations

import numpy as np
import pytest

from hpcperfstats.analysis.metrics.lib.columnar import (
  codon_bridge,
  kernels as kn,
  segments as sg,
)
from hpcperfstats.analysis.metrics.lib.columnar.jobframe import JobFrame
from hpcperfstats.analysis.metrics.lib.job_host_data_scope import (
  MAX_SANE_COUNTER_DELTA,
  MAX_SANE_COUNTER_RATE,
)
from hpcperfstats.analysis.metrics.tests.test_columnar_kernels import (
  _data_records,
  _random_records,
)
from hpcperfstats.lib.dcgm_blank import DCGM_FP64_BLANK

US = 1_000_000
RESIDUAL = ["host_mem", "nfs"]


@pytest.fixture(autouse=True)
def _numpy_reference(monkeypatch):
  monkeypatch.setenv(codon_bridge.CODON_METRICS_DISABLE_ENV, "1")
  codon_bridge.reset_for_tests()
  yield
  codon_bridge.reset_for_tests()


def _legacy_collapse(keys, dev, vals, mask):
  hk, _c, hs, _m = sg._np_group_reduce(keys, vals, mask & (dev >= 0))
  jk, _c, _s, jx = sg._np_group_reduce(keys, vals, mask & (dev < 0))
  only_j = ~np.isin(jk, hk)
  k = np.concatenate([hk, jk[only_j]])
  v = np.concatenate([hs, jx[only_j]])
  order = np.argsort(k, kind="stable")
  return k[order], v[order]


def _legacy_sum_per_sample(
  idx,
  typ,
  events,
  col,
  *,
  mode=kn.MODE_SQL,
  conv=1.0,
  nonnegative=False,
  reject_dcgm_blank=False,
  by_dev=False,
  dev_filter=False,
  limit=None,
  coalesce_zero=True,
):
  sids = idx.series_ids(typ, events)
  rows = idx.scoped_rows(typ, idx.rows_for_series(sids))
  if rows.size == 0:
    return kn.AggTable.zero(by_dev=by_dev)
  f = idx.frame
  vals = idx.values_at(col, rows)
  if mode == kn.MODE_SQL and limit is None:
    limit = MAX_SANE_COUNTER_DELTA if col == "delta" else MAX_SANE_COUNTER_RATE
  if mode == kn.MODE_ORM:
    keep = f.sentinel[rows] == 0
    if reject_dcgm_blank:
      keep &= ~np.isnan(vals) & (vals < DCGM_FP64_BLANK)
    if dev_filter:
      keep &= idx._dev_allowed_mask(rows)
    if not keep.any():
      return kn.AggTable.zero(by_dev=by_dev)
    rows, vals = rows[keep], vals[keep]
  valid = sg.finite_mask(vals, limit=limit, nonnegative=nonnegative)
  host = f.host_id[rows].astype(np.int64)
  tix = idx._time_index[rows]
  dev = f.dev_id[rows].astype(np.int64)
  if by_dev:
    group_key = sg.composite_key(host, dev + (1 << 15), tix)
  else:
    group_key = sg.composite_key(host, tix)
  if idx.host_wide(typ) and not by_dev:
    key_hte = sg.composite_key(host, f.series_id[rows].astype(np.int64), tix)
    k_e, v_e = _legacy_collapse(key_hte, dev, vals, valid)
    contrib_key = ((k_e >> (sg.TIME_BITS + 16)) << sg.TIME_BITS) | (
      k_e & ((1 << sg.TIME_BITS) - 1)
    )
    contrib_val = v_e
  else:
    contrib_key, contrib_val = group_key[valid], vals[valid]
  keys = np.unique(contrib_key if mode == kn.MODE_SQL else group_key)
  if keys.size == 0:
    return kn.AggTable.zero(by_dev=by_dev)
  sums = np.zeros(keys.shape[0], dtype=np.float64)
  if contrib_key.size:
    pos = np.searchsorted(keys, contrib_key)
    np.add.at(sums, pos, contrib_val)
    if not coalesce_zero:
      touched = np.zeros(keys.shape[0], dtype=bool)
      touched[pos] = True
      sums[~touched] = np.nan
  elif not coalesce_zero:
    sums[:] = np.nan
  if by_dev:
    return idx._agg_from_keys_dev(keys, sums, conv)
  return idx._agg_from_keys(keys, sums, conv)


def _legacy_delta_sum_by_event(idx, typ, events):
  out = {}
  f = idx.frame
  for sid in idx.series_ids(typ, events):
    rows = idx.scoped_rows(typ, idx.rows_for_series([sid]))
    if rows.size == 0:
      continue
    vals = f.delta[rows].astype(np.float64)
    if idx.host_wide(typ):
      mask = sg.finite_mask(vals, limit=MAX_SANE_COUNTER_DELTA)
      key = sg.composite_key(
        f.host_id[rows].astype(np.int64), idx._time_index[rows]
      )
      _k, v = _legacy_collapse(key, f.dev_id[rows].astype(np.int64), vals, mask)
      if v.size == 0:
        continue
      out[f.series[sid][1]] = float(v.sum())
    else:
      mask = ~np.isnan(vals) & (vals > -MAX_SANE_COUNTER_DELTA)
      mask &= vals < MAX_SANE_COUNTER_DELTA
      if not mask.any():
        continue
      out[f.series[sid][1]] = float(vals[mask].sum())
  return out


def _assert_agg_identical(got, want):
  assert got.host_id.tolist() == want.host_id.tolist()
  assert got.time_us.tolist() == want.time_us.tolist()
  assert (got.dev_id is None) == (want.dev_id is None)
  if got.dev_id is not None:
    assert got.dev_id.tolist() == want.dev_id.tolist()
  # Bit-identical, NaN in the same places.
  assert np.array_equal(got.sum_val, want.sum_val, equal_nan=True)


def _indexes(seed, records=_random_records):
  recs = records(seed, shared=True, n=900)
  frame = JobFrame.from_records("1", recs)
  return [
    kn.FrameIndex(frame),
    kn.FrameIndex(frame, shared=True, residual_types=RESIDUAL),
    kn.FrameIndex(frame, gpu_affinity_devs={"h1": ["1"], "h2": []}),
  ]


_CASES = [
  ("host_cpu", ["user", "nice"], "arc", {}),
  ("host_cpu", None, "value", {"nonnegative": True, "conv": 0.25}),
  ("host_mem", ["mem_used"], "value", {}),
  ("host_mem", ["mem_used"], "delta", {}),
  ("nfs", ["normal_read"], "delta", {}),
  ("nfs", ["normal_read"], "arc", {"mode": kn.MODE_ORM}),
  ("host_mem", ["mem_used"], "value", {"mode": kn.MODE_ORM}),
  (
    "host_mem",
    ["mem_used"],
    "value",
    {"mode": kn.MODE_ORM, "coalesce_zero": False},
  ),
  (
    "nvidia_gpu",
    ["gpu_util", "gpu_count"],
    "value",
    {"mode": kn.MODE_ORM, "reject_dcgm_blank": True},
  ),
  (
    "nvidia_gpu",
    ["gpu_util"],
    "value",
    {"mode": kn.MODE_ORM, "by_dev": True, "dev_filter": True},
  ),
  (
    "nvidia_gpu",
    ["gpu_util"],
    "value",
    {
      "mode": kn.MODE_ORM,
      "by_dev": True,
      "coalesce_zero": False,
      "reject_dcgm_blank": True,
    },
  ),
  (
    "host_cpu",
    ["user", "idle"],
    "arc",
    {"mode": kn.MODE_ORM, "limit": MAX_SANE_COUNTER_RATE, "nonnegative": True},
  ),
  ("host_cpu", ["ghost"], "arc", {}),
]


@pytest.mark.parametrize("seed", [1, 2, 3, 4])
def test_sum_per_sample_matches_legacy_bit_for_bit(seed):
  # The legacy oracle predates sample states, so it gets sentinel-free rows.
  for idx in _indexes(seed, _data_records):
    for typ, events, col, kw in _CASES:
      got = idx.sum_per_sample(typ, events, col, **kw)
      want = _legacy_sum_per_sample(idx, typ, events, col, **kw)
      _assert_agg_identical(got, want)


def test_sum_per_sample_orm_empty_group_zero_nan_and_negative_zero():
  recs = [
    (1 * US, "h1", "host_mem", "mem_used", "0", 1, None, None, None),
    (2 * US, "h1", "host_mem", "mem_used", "0", 1, 3.0, None, None),
    (3 * US, "h1", "host_mem", "mem_used", "0", 1, -0.0, None, None),
  ]
  idx = kn.FrameIndex(JobFrame.from_records("1", recs))
  z = idx.sum_per_sample("host_mem", None, "value", mode=kn.MODE_ORM)
  n = idx.sum_per_sample(
    "host_mem", None, "value", mode=kn.MODE_ORM, coalesce_zero=False
  )
  assert z.sum_val.tolist() == [0.0, 3.0, 0.0]
  assert np.isnan(n.sum_val[0]) and n.sum_val[1] == 3.0
  # A lone -0.0 sample sums to +0.0 (np.add.at from a 0.0 start).
  assert not np.signbit(z.sum_val[2]) and not np.signbit(n.sum_val[2])
  sql = idx.sum_per_sample("host_mem", None, "value")
  assert sql.time_us.tolist() == [2 * US, 3 * US]
  assert not np.signbit(sql.sum_val[1])


@pytest.mark.parametrize("seed", [5, 6, 7, 8])
def test_delta_sum_by_event_matches_legacy(seed):
  for idx in _indexes(seed):
    for typ, events in (
      ("nfs", ["normal_read"]),
      ("host_mem", ["mem_used", "ghost"]),
      ("host_cpu", ["user", "nice", "idle"]),
    ):
      got = idx.delta_sum_by_event(typ, events)
      want = _legacy_delta_sum_by_event(idx, typ, events)
      assert list(got) == list(want)
      for ev in want:
        np.testing.assert_allclose(got[ev], want[ev], rtol=1e-12, atol=0)


def test_delta_sum_by_event_sums_in_row_order():
  rng = np.random.default_rng(23)
  n = 400
  # Float4 values (host_data is ``real``), summed as float64.
  raw = rng.normal(size=n) * 10.0 ** rng.integers(-6, 15, size=n)
  deltas = raw.astype(np.float32).astype(np.float64)
  recs = [
    (i * US, "h1", "nfs", "normal_read", None, 1, None, None, float(d))
    for i, d in enumerate(deltas)
  ]
  seq = 0.0
  for d in deltas:
    seq += float(d)
  # The data must tell row order from NumPy's pairwise sum.
  assert seq != float(deltas.sum())
  idx = kn.FrameIndex(JobFrame.from_records("1", recs))
  assert idx.delta_sum_by_event("nfs", ["normal_read"])["normal_read"] == seq
  # Host-wide branch: one collapsed value per (host, time), then row order.
  shared = kn.FrameIndex(
    JobFrame.from_records("1", recs), shared=True, residual_types=["nfs"]
  )
  got = shared.delta_sum_by_event("nfs", ["normal_read"])
  assert got["normal_read"] == seq


def test_collapse_residual_copies_matches_legacy():
  rng = np.random.default_rng(31)
  n = 5_000
  keys = rng.integers(0, 300, size=n, dtype=np.int64)
  dev = rng.integers(-3, 3, size=n, dtype=np.int64)
  vals = rng.normal(size=n) * 1e6
  vals[rng.random(n) < 0.05] = np.nan
  mask = ~np.isnan(vals) & (rng.random(n) < 0.9)
  wk, wv = _legacy_collapse(keys, dev, vals, mask)
  for fn in (sg.collapse_residual_copies, sg._np_residual_collapse):
    gk, gv = fn(keys, dev, vals, mask)
    assert gk.tolist() == wk.tolist()
    assert np.array_equal(gv, wv, equal_nan=True)
  ek, ev = sg.collapse_residual_copies(keys, dev, vals, np.zeros(n, bool))
  assert ek.shape == (0,) and ev.shape == (0,)


@pytest.mark.parametrize("seed", [17, 18])
def test_metrics_pivot_unchanged_by_series_lookup(seed):
  recs = _random_records(seed, shared=True, n=900)
  frame = JobFrame.from_records("1", recs)
  idx = kn.FrameIndex(frame)
  events = ["idle", "user", "ghost", "nice"]
  p = idx.metrics_pivot("host_cpu", events)
  # Legacy per-row comprehension for the event index of every row.
  ev_pos = {e: i for i, e in enumerate(events)}
  rows = idx.scoped_rows(
    "host_cpu",
    idx.rows_for_series(idx.series_ids("host_cpu", events)),
    job_only=True,
  )
  eix = np.array(
    [ev_pos[frame.series[s][1]] for s in frame.series_id[rows]],
    dtype=np.int64,
  )
  owned = np.nonzero(idx.owned)[0]
  axis = np.unique(frame.time_us[owned])
  tix = np.searchsorted(axis, frame.time_us[rows])
  n_e = len(events)
  want = sg._np_grid_mean(
    tix * n_e + eix, frame.value[rows], axis.shape[0] * n_e
  ).reshape(axis.shape[0], n_e)
  assert np.array_equal(p["cluster_value"], want, equal_nan=True)
  assert p["per_host"].shape == (p["host_ids"].shape[0], axis.shape[0], n_e)
