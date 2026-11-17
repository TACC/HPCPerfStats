"""
Codon metrics kernels equal the NumPy reference (pyext required in the dev
container; skipped on hosts without the baked ``.so``).

Run: ``tests/run_dev_container_pytest.sh --ft
hpcperfstats/analysis/metrics/tests/test_codon_kernels.py -q`` with
``HPCPERFSTATS_CODON_METRICS_REQUIRED=1`` so a missing kernel fails instead of
skipping. The golden-corpus cases additionally need
``HPCPERFSTATS_METRICS_GOLDEN_DIR``.
"""

from __future__ import annotations

import gzip
import importlib.util
import json
import os
import sys
import threading
from pathlib import Path

import numpy as np
import pytest

from hpcperfstats.analysis.metrics.lib.columnar import (
  codon_bridge,
  segments as sg,
)
from hpcperfstats.analysis.metrics.lib.columnar.job_table import (
  ColumnarJobTable,
)
from hpcperfstats.analysis.metrics.lib.columnar.jobframe import NameInterner
from hpcperfstats.analysis.metrics.lib.columnar.k0_decode import (
  decode_copy_binary,
  decode_copy_binary_reference,
  encode_copy_binary,
)
from hpcperfstats.analysis.metrics.lib.columnar.summary_series import (
  build_summary_series,
)

_REPO = Path(__file__).resolve().parents[4]
_REQUIRED = os.environ.get("HPCPERFSTATS_CODON_METRICS_REQUIRED") == "1"


@pytest.fixture(scope="module")
def kern():
  codon_bridge.reset_for_tests()
  mod = codon_bridge.kernels()
  if mod is None:
    if _REQUIRED:
      pytest.fail(
        f"Codon metrics kernel required: {codon_bridge._state.reason}"
      )
    pytest.skip(
      f"Codon metrics kernel unavailable: {codon_bridge._state.reason}"
    )
  return mod


def _numpy_only(monkeypatch):
  monkeypatch.setattr(codon_bridge, "kernels", lambda: None)


def _assert_same(a, b):
  a = np.asarray(a)
  b = np.asarray(b)
  assert a.shape == b.shape, (a.shape, b.shape)
  if a.dtype.kind == "f":
    np.testing.assert_allclose(a, b, rtol=1e-9, atol=0, equal_nan=True)
  else:
    assert a.tolist() == b.tolist()


@pytest.mark.usefixtures("kern")
def test_pyext_loads_with_gil_off():
  line = codon_bridge.format_startup_line()
  assert line.startswith(
    "codon_metrics status=enabled module=hpcperfstats_codon_metrics"
  )
  gil = getattr(sys, "_is_gil_enabled", None)
  if gil is not None and not gil():
    assert "gil=off" in line


@pytest.mark.parametrize("seed", [0, 1, 2])
@pytest.mark.usefixtures("kern")
def test_group_reduce_matches_numpy_reference(seed):
  rng = np.random.default_rng(seed)
  n = 50_000
  keys = rng.integers(0, 2_000, size=n, dtype=np.int64) << 32
  keys |= rng.integers(0, 50, size=n, dtype=np.int64)
  values = rng.normal(size=n)
  values[rng.random(n) < 0.05] = np.nan
  mask = rng.random(n) < 0.8
  got = codon_bridge.group_reduce(keys, values, mask)
  want = sg._np_group_reduce(keys, values, mask)
  for g, w in zip(got, want, strict=True):
    _assert_same(g, w)
  # Unmasked and empty calls.
  for g, w in zip(
    codon_bridge.group_reduce(keys, values, None),
    sg._np_group_reduce(keys, values, None),
    strict=True,
  ):
    _assert_same(g, w)
  empty = codon_bridge.group_reduce(np.empty(0, np.int64), np.empty(0), None)
  assert all(x.shape == (0,) for x in empty)
  # Negative keys (sign-flipped radix pass) and keys above 48 bits.
  wide = keys * 4_096 - (1 << 40)
  for g, w in zip(
    codon_bridge.group_reduce(wide, values, mask),
    sg._np_group_reduce(wide, values, mask),
    strict=True,
  ):
    _assert_same(g, w)


@pytest.mark.usefixtures("kern")
def test_group_reduce_sums_in_row_order_bit_exact():
  # Row-order summation: Codon must reproduce np.add.at exactly, not just
  # within tolerance, so float cancellation patterns stay identical.
  rng = np.random.default_rng(7)
  values = rng.normal(size=10_000) * 10.0 ** rng.integers(-8, 8, size=10_000)
  keys = np.zeros(10_000, dtype=np.int64)
  _k, _c, sums, _m = codon_bridge.group_reduce(keys, values, None)
  _k, _c, ref, _m = sg._np_group_reduce(keys, values, None)
  assert sums[0] == ref[0]


@pytest.mark.usefixtures("kern")
def test_grid_mean_and_join_match_numpy_reference():
  rng = np.random.default_rng(3)
  cells = rng.integers(0, 300, size=20_000, dtype=np.int64)
  values = rng.normal(size=20_000)
  values[::7] = np.nan
  _assert_same(
    codon_bridge.grid_mean(cells, values, 300),
    sg._np_grid_mean(cells, values, 300),
  )
  left = np.unique(rng.integers(0, 5_000, size=3_000, dtype=np.int64))
  right = np.unique(rng.integers(0, 5_000, size=3_000, dtype=np.int64))
  li, ri = codon_bridge.join_sorted(left, right)
  wli, wri = sg._np_join_keys(left, right)
  _assert_same(li, wli)
  _assert_same(ri, wri)
  li, ri = codon_bridge.join_sorted(left, np.empty(0, np.int64))
  assert li.shape == (0,) and ri.shape == (0,)


@pytest.mark.parametrize("seed", [0, 1])
@pytest.mark.usefixtures("kern")
def test_residual_collapse_matches_numpy_reference_bit_exact(seed):
  rng = np.random.default_rng(seed)
  n = 40_000
  keys = rng.integers(0, 3_000, size=n, dtype=np.int64) - 500
  dev = rng.integers(-4, 4, size=n, dtype=np.int64)
  values = rng.normal(size=n) * 10.0 ** rng.integers(-8, 12, size=n)
  values[rng.random(n) < 0.02] = np.nan
  values[rng.random(n) < 0.02] = -0.0
  for mask in (rng.random(n) < 0.8, np.zeros(n, bool), np.ones(n, bool)):
    gk, gv = codon_bridge.residual_collapse(keys, dev, values, mask)
    wk, wv = sg._np_residual_collapse(keys, dev, values, mask)
    assert gk.tolist() == wk.tolist()
    assert np.array_equal(gv, wv, equal_nan=True)
    assert np.signbit(gv).tolist() == np.signbit(wv).tolist()


@pytest.mark.usefixtures("kern")
def test_group_reduce_negative_zero_and_all_nan_groups():
  keys = np.array([1, 2, 2, 3, 3, 4], dtype=np.int64)
  values = np.array([-0.0, np.nan, np.nan, -0.0, -0.0, 5.0])
  got = codon_bridge.group_reduce(keys, values, None)
  want = sg._np_group_reduce(keys, values, None)
  for g, w in zip(got, want, strict=True):
    assert np.array_equal(g, w, equal_nan=True)
  # Sums start from +0.0 like np.add.at; maxima keep the first -0.0.
  assert np.signbit(got[2]).tolist() == np.signbit(want[2]).tolist()
  assert np.signbit(got[3]).tolist() == np.signbit(want[3]).tolist()


def _frame_index_snapshot():
  from hpcperfstats.analysis.metrics.lib.columnar import kernels as kn
  from hpcperfstats.analysis.metrics.lib.columnar.jobframe import JobFrame
  from hpcperfstats.analysis.metrics.tests.test_columnar_kernels import (
    _random_records,
  )

  out = {}
  for seed in (1, 2, 3):
    frame = JobFrame.from_records(
      "1", _random_records(seed, shared=True, n=900)
    )
    for label, idx in (
      ("job", kn.FrameIndex(frame)),
      (
        "shared",
        kn.FrameIndex(frame, shared=True, residual_types=["host_mem", "nfs"]),
      ),
      ("affinity", kn.FrameIndex(frame, gpu_affinity_devs={"h1": ["1"]})),
    ):
      cases = {
        "sql_arc": ("host_cpu", ["user", "nice"], "arc", {}),
        "sql_delta": ("host_mem", ["mem_used"], "delta", {}),
        "sql_nfs_delta": ("nfs", ["normal_read"], "delta", {}),
        "orm": ("host_mem", ["mem_used"], "value", {"mode": kn.MODE_ORM}),
        "orm_nan": (
          "host_cpu",
          None,
          "arc",
          {"mode": kn.MODE_ORM, "coalesce_zero": False},
        ),
        "orm_blank": (
          "nvidia_gpu",
          ["gpu_util", "gpu_count"],
          "value",
          {"mode": kn.MODE_ORM, "reject_dcgm_blank": True},
        ),
        "orm_by_dev": (
          "nvidia_gpu",
          ["gpu_util"],
          "value",
          {
            "mode": kn.MODE_ORM,
            "by_dev": True,
            "dev_filter": True,
            "coalesce_zero": False,
          },
        ),
      }
      for name, (typ, events, col, kw) in cases.items():
        agg = idx.sum_per_sample(typ, events, col, **kw)
        out[f"{seed}:{label}:{name}"] = (
          agg.host_id.copy(),
          agg.time_us.copy(),
          agg.sum_val.copy(),
          np.empty(0) if agg.dev_id is None else agg.dev_id.copy(),
        )
      for typ, events in (
        ("nfs", ["normal_read"]),
        ("host_mem", ["mem_used"]),
        ("host_cpu", ["user", "nice", "idle"]),
      ):
        out[f"{seed}:{label}:delta:{typ}"] = idx.delta_sum_by_event(typ, events)
      piv = idx.metrics_pivot("host_cpu", ["user", "ghost", "nice"])
      out[f"{seed}:{label}:pivot"] = (
        piv["per_host"].copy(),
        piv["cluster_value"].copy(),
        piv["cluster_arc"].copy(),
      )
  return out


@pytest.mark.usefixtures("kern")
def test_frame_index_reductions_codon_equal_numpy_bit_exact(monkeypatch):
  with_codon = _frame_index_snapshot()
  _numpy_only(monkeypatch)
  with_numpy = _frame_index_snapshot()
  assert set(with_codon) == set(with_numpy)
  for key, a in with_codon.items():
    b = with_numpy[key]
    if isinstance(a, dict):
      # Row-order sums: identical floats, not just within tolerance.
      assert a == b, key
      continue
    for x, y in zip(a, b, strict=True):
      assert x.shape == y.shape, key
      assert np.array_equal(x, y, equal_nan=True), key


def _random_rows(rng, n, *, hosts, with_owned):
  t0 = 1_700_000_000_000_000
  rows = []
  types = ["host_cpu", "nvidia_gpu", "host_mem", "ib_hé"]
  events = ["user", "gpu_util", "mem_used", "rx", ""]
  devs = [None, "", "0", "1", "j:42", "cpué"]
  for _i in range(n):
    rows.append(
      (
        t0 + int(rng.integers(0, 10_000)) * 1_000_000,
        f"h{int(rng.integers(0, hosts))}",
        types[int(rng.integers(0, len(types)))],
        events[int(rng.integers(0, len(events)))],
        devs[int(rng.integers(0, len(devs)))],
        *[
          None
          if rng.random() < 0.2
          else float(rng.normal() * 10.0 ** rng.integers(-3, 12))
          for _ in range(3)
        ],
        *([int(rng.random() < 0.5)] if with_owned else []),
      )
    )
  return rows


@pytest.mark.parametrize("with_owned", [False, True])
@pytest.mark.usefixtures("kern")
def test_copy_decode_matches_reference(with_owned):
  rng = np.random.default_rng(11)
  rows = _random_rows(rng, 5_000, hosts=40, with_owned=with_owned)
  raw = encode_copy_binary(rows, with_owned=with_owned)
  ni_c, ni_r = NameInterner(), NameInterner()
  got = decode_copy_binary(raw, ni_c, has_owned_column=with_owned)
  want = decode_copy_binary_reference(raw, ni_r, has_owned_column=with_owned)
  assert ni_c.hosts == ni_r.hosts
  assert ni_c.types == ni_r.types
  assert ni_c.events == ni_r.events
  assert ni_c.devs == ni_r.devs
  for name in (
    "time_us",
    "host_id",
    "type_id",
    "event_id",
    "dev_id",
    "owned",
    "value",
    "arc",
    "delta",
  ):
    g, w = getattr(got, name), getattr(want, name)
    assert g.dtype == w.dtype, name
    _assert_same(g, w)


@pytest.mark.usefixtures("kern")
def test_copy_decode_grows_name_table_past_initial_cap():
  rows = [
    (1_700_000_000_000_000 + i, f"host{i:05d}", "t", "e", None, 1.0, 1.0, 1.0)
    for i in range(5_000)
  ]
  raw = encode_copy_binary(rows)
  ni = NameInterner()
  got = decode_copy_binary(raw, ni)
  assert len(ni.hosts) == 5_000
  assert got.host_id.tolist() == list(range(5_000))


@pytest.mark.usefixtures("kern")
def test_copy_decode_empty_and_interner_continuity():
  ni = NameInterner()
  ni.host("pre")
  empty = decode_copy_binary(encode_copy_binary([]), ni)
  assert len(empty) == 0 and empty.value.dtype == np.float64
  rows = [(1_700_000_000_000_000, "pre", "t", "e", "0", 1.0, None, 2.0)]
  ch = decode_copy_binary(encode_copy_binary(rows), ni)
  assert ch.host_id.tolist() == [0]
  assert ni.hosts == ["pre"]


@pytest.mark.usefixtures("kern")
def test_copy_decode_rejects_bad_streams_without_fallback(monkeypatch):
  calls = []
  real = decode_copy_binary_reference
  monkeypatch.setattr(
    "hpcperfstats.analysis.metrics.lib.columnar.k0_decode.decode_copy_binary_reference",
    lambda *a, **k: calls.append(1) or real(*a, **k),
  )
  with pytest.raises(codon_bridge.CodonKernelError):
    decode_copy_binary(b"not a copy stream" * 3, NameInterner())
  raw = encode_copy_binary([(1, "h", "t", "e", None, 1.0, 1.0, 1.0)])
  with pytest.raises(codon_bridge.CodonKernelError):
    decode_copy_binary(raw, NameInterner(), has_owned_column=True)
  with pytest.raises(codon_bridge.CodonKernelError):
    decode_copy_binary(raw[:-6], NameInterner())
  assert calls == []


@pytest.mark.usefixtures("kern")
def test_threads_run_kernels_concurrently_with_identical_results():
  rng = np.random.default_rng(5)
  n = 200_000
  keys = rng.integers(0, 4_000, size=n, dtype=np.int64)
  values = rng.normal(size=n)
  want = sg._np_group_reduce(keys, values, None)
  rows = _random_rows(
    np.random.default_rng(9), 2_000, hosts=30, with_owned=True
  )
  raw = encode_copy_binary(rows, with_owned=True)
  want_dec = decode_copy_binary_reference(
    raw, NameInterner(), has_owned_column=True
  )
  errors: list[BaseException] = []

  def work() -> None:
    try:
      for _ in range(5):
        got = codon_bridge.group_reduce(keys, values, None)
        for g, w in zip(got, want, strict=True):
          assert np.array_equal(g, w, equal_nan=True)
        dec = decode_copy_binary(raw, NameInterner(), has_owned_column=True)
        assert np.array_equal(dec.value, want_dec.value, equal_nan=True)
        assert dec.time_us.tolist() == want_dec.time_us.tolist()
    except BaseException as exc:  # pragma: no cover - reported below
      errors.append(exc)

  threads = [threading.Thread(target=work) for _ in range(8)]
  for t in threads:
    t.start()
  for t in threads:
    t.join()
  assert errors == []


def _assert_bits(got, want):
  """Equal shape, dtype kind, values (NaN-equal) and float sign bits."""
  got = np.asarray(got)
  want = np.asarray(want)
  assert got.shape == want.shape, (got.shape, want.shape)
  assert got.dtype.kind == want.dtype.kind, (got.dtype, want.dtype)
  assert np.array_equal(got, want, equal_nan=want.dtype.kind == "f")
  if want.dtype.kind == "f":
    assert np.signbit(got).tolist() == np.signbit(want).tolist()


def _assert_same_tuple(got, want):
  assert len(got) == len(want)
  for g, w in zip(got, want, strict=True):
    _assert_bits(g, w)


def _sample_rows(rng, n, *, hosts=40, times=60):
  """Rows of one sentinel fold before the per-sample reduction.

  Kinds: valid, bridged, gap, and neither (a NULL row with code 0, which
  must not open a sample). Keys are unsorted ``host << 32 | time`` with
  repeats; ``eff`` spans 15 decades so row order changes the float sum.
  """
  keys = rng.integers(0, hosts, n, dtype=np.int64) << 32
  keys |= rng.integers(0, times, n, dtype=np.int64)
  kind = rng.integers(0, 4, n)
  valid = kind == 0
  bridged = kind == 1
  gap_row = kind == 2
  code = np.where(valid | (kind == 3), 0, -rng.integers(1, 10, n))
  eff = rng.normal(size=n) * 10.0 ** rng.integers(-6, 9, n)
  eff[kind == 3] = np.nan
  eff[rng.random(n) < 0.02] = -0.0
  return keys, valid, bridged, gap_row, code.astype(np.int64), eff


def _samples_both(keys, valid, bridged, gap_row, code, eff):
  from hpcperfstats.analysis.metrics.lib import sentinel_profile as sp

  got = codon_bridge.sentinel_samples(keys, valid, bridged, gap_row, code, eff)
  want = sp._np_sentinel_samples(keys, valid, bridged, gap_row, code, eff)
  return got, want


@pytest.mark.parametrize("seed", [0, 1, 2, 3])
@pytest.mark.usefixtures("kern")
def test_sentinel_samples_matches_numpy_twin_randomized(seed):
  rng = np.random.default_rng(100 + seed)
  for n in (1, 7, 2_000, 40_000):
    got, want = _samples_both(*_sample_rows(rng, n))
    _assert_same_tuple(got, want)
  # Wide host ids (bits above 48) force the upper radix passes.
  keys, *rest = _sample_rows(rng, 5_000, hosts=1 << 20)
  _assert_same_tuple(*_samples_both(keys, *rest))


@pytest.mark.usefixtures("kern")
def test_sentinel_samples_edge_cases_match_numpy_twin():
  def case(keys, valid, bridged, gap_row, code, eff):
    got, want = _samples_both(
      np.asarray(keys, dtype=np.int64),
      np.asarray(valid, dtype=bool),
      np.asarray(bridged, dtype=bool),
      np.asarray(gap_row, dtype=bool),
      np.asarray(code, dtype=np.int64),
      np.asarray(eff, dtype=np.float64),
    )
    _assert_same_tuple(got, want)
    return got

  empty = case([], [], [], [], [], [])
  assert all(x.shape == (0,) for x in empty)
  # NaN rows that are neither valid, bridged nor gap open no sample.
  first, _s, _st, _c = case(
    [7, 7, 3], [0, 0, 1], [0, 0, 0], [0, 0, 0], [0, 0, 0], [np.nan, 1, 2]
  )
  assert first.tolist() == [2]
  # Bridged spread plus a gap in one sample: status gap, the gap's code wins
  # over the bridged code, and the bridged rate still adds to the sum.
  _f, sums, status, codes = case(
    [4, 4, 4], [1, 0, 0], [0, 1, 0], [0, 0, 1], [0, -3, -5], [1.5, 2.5, 99.0]
  )
  assert status.tolist() == [2] and codes.tolist() == [-5]
  assert sums.tolist() == [4.0]
  # Several gap rows: the last one in row order names the sample.
  _f, _s, _st, codes = case(
    [9, 9, 9], [0, 0, 0], [0, 0, 0], [1, 1, 1], [-1, -6, -2], [0, 0, 0]
  )
  assert codes.tolist() == [-2]
  # Negative keys go through the sign-flipped radix pass; -0.0 sums to +0.0.
  case([-5, 3, -5, 3], [1, 1, 1, 0], [0] * 4, [0] * 4, [0] * 4, [-0.0] * 4)


def _fold_rows(rng, n, *, hosts=8, minutes=120):
  host = rng.integers(0, hosts, n, dtype=np.int64) * 3 + 1
  t = rng.integers(0, minutes, n, dtype=np.int64) * 60_000_000
  status = rng.choice(np.array([0, 1, 2], dtype=np.int64), n, p=[0.7, 0.1, 0.2])
  values = rng.normal(size=n) * 10.0 ** rng.integers(-6, 9, n)
  values[rng.random(n) < 0.03] = np.nan
  values[status == 2] = np.nan
  return host, t // sg.FIVE_MIN_US, values, status


def _fold_both(host, bucket, values, status, max_gap, max_frac):
  from hpcperfstats.analysis.metrics.lib import sentinel_profile as sp

  args = (
    np.asarray(host, dtype=np.int64),
    np.asarray(bucket, dtype=np.int64),
    np.asarray(values, dtype=np.float64),
    np.asarray(status, dtype=np.int64),
    max_gap,
    max_frac,
  )
  got = codon_bridge.sentinel_fold(*args)
  want = sp._np_sentinel_fold(*args)
  _assert_same_tuple(got, want)
  return got


@pytest.mark.parametrize("seed", [0, 1, 2])
@pytest.mark.usefixtures("kern")
def test_sentinel_fold_matches_numpy_twin_randomized(seed):
  rng = np.random.default_rng(200 + seed)
  for n in (1, 30, 3_000, 50_000):
    rows = _fold_rows(rng, n)
    for max_gap, max_frac in ((3, 0.1), (0, 1.0), (40, 1.0), (1, 0.02)):
      _fold_both(*rows, max_gap, max_frac)
  # Sparse cadence: most buckets of most hosts are empty or gap-only.
  _fold_both(*_fold_rows(rng, 400, hosts=30, minutes=600), 3, 0.5)


@pytest.mark.usefixtures("kern")
def test_sentinel_fold_edge_cases_match_numpy_twin():
  v, b, g = 0, 1, 2
  per_host, *_rest = _fold_both([], [], [], [], 3, 0.1)
  assert per_host.shape == (7, 0)
  # All-gap host: every bucket NaN and flagged, nothing to fill from.
  per_host, means, gaps, _pos = _fold_both(
    [5] * 4, [0, 1, 2, 3], [np.nan] * 4, [g] * 4, 3, 1.0
  )
  assert per_host[:, 0].tolist() == [5, 0, 0, 4, 0, 3, 0]
  assert np.isnan(means).all() and gaps.all()
  # Cadence-empty interior bucket (only gap samples) between bridged and
  # valid ones: filled linearly at its bucket position.
  per_host, means, gaps, pos = _fold_both(
    [2] * 6,
    [0, 1, 2, 4, 4, 5],
    [9, 2, np.nan, np.nan, 6, 8],
    [v, b, g, g, v, v],
    3,
    1.0,
  )
  assert per_host[:, 0].tolist() == [2, 3, 1, 2, 0, 4, 1]
  assert pos.tolist() == [1.0, 2.0, 4.0, 5.0]
  assert means[[0, 2, 3]].tolist() == [2.0, 6.0, 8.0]
  assert means[1] == pytest.approx(2.0 + 4.0 / 3.0, rel=1e-12)
  assert gaps.tolist() == [False, True, False, False]
  # Chain-break gap at the host's last bucket is an edge: never filled.
  per_host, means, _g, _p = _fold_both(
    [0] * 3, [0, 1, 2], [1, 2, np.nan], [v, v, g], 3, 1.0
  )
  assert per_host[6, 0] == 0 and np.isnan(means[-1])
  # Single-bucket hosts keep their only bucket; negative buckets (times
  # before the epoch) sort ahead; a covered NaN row poisons its bucket mean.
  per_host, means, _g, pos = _fold_both(
    [7, 7, 1, 1, 1],
    [-3, -3, 4, -2, 4],
    [1, 3, np.nan, 2, 5],
    [v, v, v, v, b],
    3,
    1.0,
  )
  assert per_host[0].tolist() == [1, 7] and per_host[5].tolist() == [1, 1]
  assert pos.tolist() == [4.0, -3.0]
  assert np.isnan(means[0]) and means[1] == 2.0
  # max_gap / max_frac boundaries through the fold.
  hb = list(range(12))
  st = [v] * 12
  st[5] = st[6] = g
  vals = [float(i) for i in hb]
  assert _fold_both([0] * 12, hb, vals, st, 2, 1.0)[0][6, 0] == 2
  assert _fold_both([0] * 12, hb, vals, st, 1, 1.0)[0][6, 0] == 0
  assert _fold_both([0] * 12, hb, vals, st, 2, 2 / 11)[0][6, 0] == 2
  assert _fold_both([0] * 12, hb, vals, st, 2, 1.9 / 11)[0][6, 0] == 0


def _fill_both(means, gaps, max_gap, max_frac, pos=None):
  from hpcperfstats.analysis.metrics.lib import sentinel_profile as sp

  means = np.asarray(means, dtype=np.float64)
  gaps = np.asarray(gaps, dtype=bool)
  positions = (
    np.arange(means.shape[0], dtype=np.float64) if pos is None else pos
  )
  original = means.copy()
  got_m, got_n = codon_bridge.fill_interior_gaps(
    means, gaps, positions, max_gap, max_frac
  )
  want_m, want_n = sp._np_fill_interior_gaps(
    means, gaps, max_gap, max_frac, pos
  )
  assert got_n == want_n
  _assert_bits(got_m, want_m)
  _assert_bits(means, original)
  return got_m, got_n


@pytest.mark.parametrize("seed", [0, 1, 2, 3])
@pytest.mark.usefixtures("kern")
def test_sentinel_fill_interior_gaps_matches_numpy_twin(seed):
  rng = np.random.default_rng(300 + seed)
  for n in (1, 2, 17, 500, 5_000):
    means = rng.normal(size=n) * 10.0 ** rng.integers(-6, 9, n)
    gaps = rng.random(n) < 0.3
    means[gaps] = np.nan
    means[rng.random(n) < 0.03] = np.nan
    means[rng.random(n) < 0.01] = np.inf
    pos = np.cumsum(rng.integers(1, 4, n)).astype(np.float64)
    for max_gap, max_frac in ((3, 0.1), (1, 1.0), (50, 1.0), (0, 1.0)):
      _fill_both(means, gaps, max_gap, max_frac)
      _fill_both(means, gaps, max_gap, max_frac, pos)


@pytest.mark.usefixtures("kern")
def test_sentinel_fill_interior_gaps_boundaries_match_numpy_twin():
  nan = np.nan
  assert _fill_both([], [], 3, 0.5)[1] == 0
  ten = [float(i) for i in range(10)]
  one_gap = [i == 4 for i in range(10)]
  # max_frac boundary: 1 of 10 fills at exactly 0.1, not just under it.
  assert _fill_both(ten, one_gap, 3, 0.1)[1] == 1
  assert _fill_both(ten, one_gap, 3, 0.0999)[1] == 0
  # max_gap boundary: a run of 3 fills at max_gap 3, not at 2.
  run3 = [3 <= i <= 5 for i in range(10)]
  assert _fill_both(ten, run3, 3, 1.0)[1] == 3
  assert _fill_both(ten, run3, 2, 1.0)[1] == 0
  # Edge runs never fill; NaN and inf neighbours are not anchors.
  assert _fill_both([nan, 1.0, 2.0], [True, False, False], 3, 1.0)[1] == 0
  assert _fill_both([1.0, 2.0, nan], [False, False, True], 3, 1.0)[1] == 0
  assert _fill_both([nan, nan, 3.0], [False, True, False], 3, 1.0)[1] == 0
  assert _fill_both([np.inf, nan, 3.0], [False, True, False], 3, 1.0)[1] == 0
  assert _fill_both([nan] * 4, [True] * 4, 3, 1.0)[1] == 0
  # Uneven positions weight the interpolation by distance.
  out, n = _fill_both(
    [0.0, nan, 10.0], [False, True, False], 3, 1.0, np.array([0.0, 1.0, 4.0])
  )
  assert n == 1 and out.tolist() == [0.0, 2.5, 10.0]
  # One fillable and one oversized run: the share counts fillable buckets.
  mixed = [1.0, nan, 3.0, nan, nan, nan, nan, 8.0]
  gaps = [m != m for m in mixed]
  assert _fill_both(mixed, gaps, 2, 1 / 8)[1] == 1


def _sentinel_profile_snapshot():
  """Folds and per-sample arrays of the sentinel aggregation scenarios."""
  from hpcperfstats.analysis.metrics.lib import metrics, sentinel_profile as sp
  from hpcperfstats.analysis.metrics.lib.sentinel_profile import SentinelConfig
  from hpcperfstats.analysis.metrics.tests import (
    test_metrics_sentinel_aggregation as agg,
  )

  every = dict.fromkeys(range(agg._MIN + 1), -2)
  jobs = {
    "bridged": agg._ib_job(overrides={("c1.x", "port_rcv_data"): {20: -3}}),
    "chain_break": agg._ib_job(
      overrides={("c2.x", ev): {30: -5, 31: -5, 32: -5} for ev in agg._IB}
    ),
    "static_sum_scaled": agg._ib_job(
      overrides={("c4.x", ev): every for ev in agg._IB},
      config=SentinelConfig(scale_partial_sums=True),
    ),
    "partial_scaled": agg._ib_job(
      10.0 * 61 / 64, runs=agg._partial("c1.x", -3, 3)
    ),
    "partial_gap_filled": agg._ib_job(
      10.0, runs=agg._partial("c1.x", -3, 20, start_min=20, end_min=24)
    ),
    "under_floor": agg._ib_job(
      overrides={
        (h, ev): (
          dict.fromkeys(range(agg._MIN + 1), -6)
          if h == "c4.x"
          else dict.fromkeys(range(5, 32), -5)
        )
        for h in agg._HOSTS
        for ev in agg._IB
      }
    ),
  }
  out = {}
  for name, jt in jobs.items():
    for how in ("mean", "sum"):
      value, sink = agg._fold(jt, agg=how)
      out[f"{name}:{how}"] = (value, [vars(q) for q in sink])
    for col, per_sample_sum in (("arc", True), ("value", False)):
      got = sp.sentinel_samples(
        jt.index,
        "host_ib",
        list(agg._IB),
        col,
        jt.sentinel_runs(),
        limit=None,
        per_sample_sum=per_sample_sum,
      )
      out[f"{name}:{col}:samples"] = got
      if got is not None:
        host, t, vals, status, codes = got[0]
        value, q = sp.sentinel_fold(
          host, t, vals, status, codes, 1.0, typename="host_ib"
        )
        out[f"{name}:{col}:fold"] = (value, vars(q))
  enabled_ev, running_ev, _solo = agg.pmu_sharing_event_names()
  rows = []
  for h in agg._HOSTS:
    rows += agg._counter(h, "host_cpu_hw", running_ev, 1.0, sentinels={10: -6})
    rows += agg._counter(h, "host_cpu_hw", enabled_ev, 2.0)
  out["aligned_ratio"] = metrics.Metrics()._job_pmu_sharing_pct(
    agg._table(rows), "avg_pmu_running_pct"
  )
  return out


def _assert_same_snapshot(a, b, key=""):
  if isinstance(a, np.ndarray):
    _assert_bits(a, b)
  elif isinstance(a, (tuple, list)):
    assert len(a) == len(b), key
    for x, y in zip(a, b, strict=True):
      _assert_same_snapshot(x, y, key)
  elif isinstance(a, float) and a != a:
    assert b != b, key
  else:
    assert a == b, key


@pytest.mark.usefixtures("kern")
def test_sentinel_profile_end_to_end_codon_equals_numpy(monkeypatch):
  with_codon = _sentinel_profile_snapshot()
  _numpy_only(monkeypatch)
  with_numpy = _sentinel_profile_snapshot()
  assert set(with_codon) == set(with_numpy)
  for key, a in with_codon.items():
    _assert_same_snapshot(a, with_numpy[key], key)
  # The scenarios reach every branch the kernels feed.
  assert with_numpy["bridged:mean"][1][0]["bridged"] == 1
  assert with_numpy["chain_break:mean"][1][0]["transient_codes"] == {-5: 4}
  assert with_numpy["static_sum_scaled:sum"][1][0]["hosts_scaled"] is True
  assert with_numpy["partial_gap_filled:mean"][1][0]["filled"] == 1
  assert with_numpy["under_floor:mean"][0] is None


def _sentinel_series_snapshot():
  """Per-sample sums of the sentinel series scenarios (states and fill)."""
  from types import SimpleNamespace

  from hpcperfstats.analysis.metrics.lib import metrics
  from hpcperfstats.analysis.metrics.tests import (
    test_metrics_sentinel_aggregation as agg,
  )

  overrides = {
    ("c1.x", "port_rcv_data"): {20: -5},
    ("c2.x", "port_xmit_data"): {30: -3},
    ("c3.x", "port_rcv_data"): dict.fromkeys(range(10, 31), -5),
    ("c4.x", "port_rcv_data"): dict.fromkeys(range(agg._MIN + 1), -2),
  }
  jt = agg._ib_job(overrides=overrides)
  out = {}
  for fill in (True, False):
    t = jt.get_aggregate("host_ib", "arc", list(agg._IB), 1.0, fill=fill)
    out[f"aggregate:{fill}"] = (t.host_id, t.time_us, t.sum_val, t.filled)
  for mode in ("sql", "orm"):
    t = jt.index.sum_per_sample("host_ib", list(agg._IB), "arc", mode=mode)
    out[f"sum_per_sample:{mode}"] = (t.host_id, t.time_us, t.sum_val)
  rows = []
  for ev, rate in (("user", 50.0), ("nice", 50.0), ("idle", 100.0)):
    gaps = {"nice": {20: -5}, "idle": {40: -5}}.get(ev)
    rows += agg._counter("c1.x", "host_cpu", ev, rate, sentinels=gaps)
  out["avg_cpuusage"] = metrics.Metrics()._job_avg_cpuusage_allocated(
    agg._table(rows, hosts=("c1.x",)),
    SimpleNamespace(ncores=8, nhosts=1),
    None,
    None,
  )
  return out


@pytest.mark.usefixtures("kern")
def test_sentinel_series_sums_codon_equals_numpy(monkeypatch):
  with_codon = _sentinel_series_snapshot()
  _numpy_only(monkeypatch)
  with_numpy = _sentinel_series_snapshot()
  assert set(with_codon) == set(with_numpy)
  for key, a in with_codon.items():
    _assert_same_snapshot(a, with_numpy[key], key)
  # The scenarios reach the gap, bridge and fill branches.
  assert with_numpy["aggregate:True"][3] == 2
  assert with_numpy["aggregate:False"][3] == 0


# -- golden corpus -----------------------------------------------------------


def _corpus_dir() -> Path | None:
  raw = os.environ.get("HPCPERFSTATS_METRICS_GOLDEN_DIR") or (
    "/data/user/sharrell/hpcperfstats-metrics-golden"
  )
  d = Path(raw)
  if (d / "manifest.json").is_file():
    return d
  return None


def _load_corpus_module():
  spec = importlib.util.spec_from_file_location(
    "metrics_golden_corpus_codon_t",
    _REPO / "scripts" / "metrics_golden_corpus.py",
  )
  mod = importlib.util.module_from_spec(spec)
  sys.modules[spec.name] = mod
  spec.loader.exec_module(mod)
  return mod


def _golden_jids() -> list[str]:
  d = _corpus_dir()
  if d is None:
    return []
  manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
  jobs = manifest.get("jobs", [])
  if isinstance(jobs, dict):
    return [str(j) for j in jobs]
  return [str(j["jid"]) for j in jobs]


@pytest.mark.parametrize("jid", _golden_jids() or ["__no_corpus__"])
@pytest.mark.usefixtures("kern")
def test_golden_frames_codon_equals_numpy(monkeypatch, jid):
  d = _corpus_dir()
  if d is None:
    pytest.skip("golden corpus absent (HPCPERFSTATS_METRICS_GOLDEN_DIR)")
  corpus = _load_corpus_module()
  with gzip.open(
    d / "jobs" / f"{jid}.golden.json.gz", "rt", encoding="utf-8"
  ) as fh:
    golden = json.load(fh)
  frame = corpus.load_frame_npz(d / "jobs" / f"{jid}.frame.npz")

  def snapshot():
    jt = ColumnarJobTable.from_golden(jid, frame, golden["jid_table"])
    out: dict[str, object] = {}
    ht = jt.get_host_time()
    out["host_time"] = (ht.host_id.copy(), ht.time_us.copy())
    out["distinct"] = jt.distinct_time_count()
    for typ, events in sorted(jt.schema.items()):
      for col in ("value", "arc"):
        agg = jt.get_aggregate(typ, col, list(events), 1.0)
        out[f"agg:{typ}:{col}"] = (
          agg.host_id.copy(),
          agg.time_us.copy(),
          agg.sum_val.copy(),
        )
      piv = jt.index.metrics_pivot(typ, list(events))
      out[f"pivot:{typ}"] = (
        piv["host_ids"].copy(),
        piv["per_host"].copy(),
        piv["cluster_value"].copy(),
        piv["cluster_arc"].copy(),
      )
    try:
      summary = build_summary_series(jt)
    except ValueError:
      out["summary"] = None
    else:
      out["summary"] = {k: v.copy() for k, v in summary.frame.columns.items()}
    return out

  with_codon = snapshot()
  _numpy_only(monkeypatch)
  with_numpy = snapshot()
  assert set(with_codon) == set(with_numpy)
  for key in with_codon:
    a, b = with_codon[key], with_numpy[key]
    if a is None or isinstance(a, int):
      assert a == b, key
    elif isinstance(a, dict):
      assert set(a) == set(b), key
      for col in a:
        _assert_same(a[col], b[col])
    else:
      for x, y in zip(a, b, strict=True):
        _assert_same(x, y)
