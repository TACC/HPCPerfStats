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
