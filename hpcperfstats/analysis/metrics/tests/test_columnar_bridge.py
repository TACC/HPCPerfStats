"""
Codon bridge contract: kernel errors hard-fail (no NumPy fallback), the
pipeline interpreter refuses to start without the baked ``.so``, and the
host keeps the NumPy reference when the kernel is absent.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import numpy as np
import pytest

from hpcperfstats.analysis.metrics.lib.columnar import (
  codon_bridge,
  segments as sg,
)
from hpcperfstats.analysis.metrics.lib.columnar.jobframe import NameInterner
from hpcperfstats.analysis.metrics.lib.columnar.k0_decode import (
  decode_copy_binary,
  encode_copy_binary,
)


@pytest.fixture(autouse=True)
def _clean_bridge(monkeypatch):
  codon_bridge.reset_for_tests()
  monkeypatch.delenv(codon_bridge.CODON_METRICS_REQUIRED_ENV, raising=False)
  monkeypatch.delenv(codon_bridge.CODON_METRICS_DIR_ENV, raising=False)
  monkeypatch.delenv(codon_bridge.CODON_METRICS_DISABLE_ENV, raising=False)
  yield
  codon_bridge.reset_for_tests()


def _install_fake(monkeypatch, **methods):
  fake = SimpleNamespace(__name__="fake_codon_metrics", **methods)
  monkeypatch.setattr(codon_bridge._state, "module", fake)
  monkeypatch.setattr(codon_bridge._state, "reason", "enabled")
  return fake


def test_kernel_exception_becomes_codon_kernel_error_without_fallback(
  monkeypatch,
):
  def boom(*_a):
    raise RuntimeError("segfault avoided")

  _install_fake(monkeypatch, group_reduce=boom)
  calls = []
  real = sg._np_group_reduce
  monkeypatch.setattr(
    sg, "_np_group_reduce", lambda *a, **k: calls.append(1) or real(*a, **k)
  )
  with pytest.raises(codon_bridge.CodonKernelError, match="group_reduce"):
    sg.group_sum(np.array([1, 2]), np.array([1.0, 2.0]))
  assert calls == []


def test_decoder_kernel_error_does_not_fall_back_to_reference(monkeypatch):
  def bad_count(*_a):
    raise ValueError("copy_count_rows: kernel error code -2")

  _install_fake(monkeypatch, copy_count_rows=bad_count, copy_decode=bad_count)
  raw = encode_copy_binary([(1, "h", "t", "e", None, 1.0, 1.0, 1.0)])
  with pytest.raises(codon_bridge.CodonKernelError):
    decode_copy_binary(raw, NameInterner())


def test_copy_decode_grows_name_table_then_gives_up(monkeypatch):
  caps = []

  def count(_buf, _owned):
    return 3

  def decode(_buf, _owned, _nrows, *rest):
    cap = rest[9]
    caps.append(cap)
    raise OverflowError("cap")

  _install_fake(monkeypatch, copy_count_rows=count, copy_decode=decode)
  with pytest.raises(codon_bridge.CodonKernelError, match="name table"):
    codon_bridge.copy_decode(b"x", has_owned=False)
  # 4096 already exceeds 4 * nrows, so exactly one attempt is made.
  assert caps == [4096]


def test_bridge_wrappers_normalise_dtypes_and_slice_outputs(monkeypatch):
  seen = {}

  def group_reduce(
    n, keys, values, has_mask, mask, scratch, ok, oc, osum, omax
  ):
    seen["dtypes"] = (keys.dtype, values.dtype, mask.dtype, has_mask)
    assert scratch.shape[0] >= 4 * n + codon_bridge.RADIX_BUCKETS
    ok[0], oc[0], osum[0], omax[0] = 7, 2, 3.0, 2.0
    return 1

  def join_sorted(nl, left, nr, right, li, ri):
    li[0], ri[0] = 0, 1
    return 1

  _install_fake(monkeypatch, group_reduce=group_reduce, join_sorted=join_sorted)
  k, c, s, m = sg.group_reduce(
    np.array([7, 7], dtype=np.int32), np.array([1, 2], dtype=np.int32)
  )
  assert seen["dtypes"] == (
    np.dtype("int64"),
    np.dtype("float64"),
    np.dtype("uint8"),
    False,
  )
  assert (k.tolist(), c.tolist(), s.tolist(), m.tolist()) == (
    [7],
    [2],
    [3.0],
    [2.0],
  )
  li, ri = sg.join_keys(np.array([1, 2]), np.array([0, 1]))
  assert li.tolist() == [0] and ri.tolist() == [1]


def test_host_without_kernel_uses_numpy_reference_and_is_not_required(
  monkeypatch, tmp_path
):
  monkeypatch.setenv(codon_bridge.CODON_METRICS_DIR_ENV, str(tmp_path))
  assert codon_bridge.kernels() is None
  assert "missing_so" in codon_bridge._state.reason
  if not sys.executable.startswith(codon_bridge.PIPELINE_INTERPRETER_PREFIX):
    assert codon_bridge.codon_metrics_required() is False
  k, s, c = sg.group_sum(np.array([2, 1, 2]), np.array([1.0, 2.0, 3.0]))
  assert (k.tolist(), s.tolist(), c.tolist()) == ([1, 2], [2.0, 4.0], [1, 2])
  line = codon_bridge.format_startup_line()
  assert line.startswith("codon_metrics status=disabled reason=missing_so")


def test_required_env_overrides_interpreter_detection(monkeypatch):
  monkeypatch.setenv(codon_bridge.CODON_METRICS_REQUIRED_ENV, "1")
  assert codon_bridge.codon_metrics_required() is True
  monkeypatch.setenv(codon_bridge.CODON_METRICS_REQUIRED_ENV, "0")
  assert codon_bridge.codon_metrics_required() is False
  monkeypatch.delenv(codon_bridge.CODON_METRICS_REQUIRED_ENV)
  monkeypatch.setattr(sys, "executable", "/opt/python3.14t/bin/python")
  assert codon_bridge.codon_metrics_required() is True


def test_require_refuses_start_when_kernel_missing(monkeypatch, tmp_path):
  monkeypatch.setenv(codon_bridge.CODON_METRICS_REQUIRED_ENV, "1")
  monkeypatch.setenv(codon_bridge.CODON_METRICS_DIR_ENV, str(tmp_path))
  logged = []
  with pytest.raises(SystemExit, match="refusing to start"):
    codon_bridge.require_codon_metrics(logged.append)
  assert logged and logged[0].startswith("codon_metrics status=disabled")
  assert "required=1" in logged[0]


def test_require_passes_when_kernel_loaded(monkeypatch):
  monkeypatch.setenv(codon_bridge.CODON_METRICS_REQUIRED_ENV, "1")
  _install_fake(
    monkeypatch,
    **{name: (lambda *a: 0) for name in codon_bridge._KERNEL_METHODS},
  )
  logged = []
  codon_bridge.require_codon_metrics(logged.append)
  assert logged[0].startswith(
    "codon_metrics status=enabled module=fake_codon_metrics"
  )


def test_update_metrics_main_refuses_before_scheduling(monkeypatch, tmp_path):
  from hpcperfstats.analysis.metrics import update_metrics as um

  monkeypatch.setenv(codon_bridge.CODON_METRICS_REQUIRED_ENV, "1")
  monkeypatch.setenv(codon_bridge.CODON_METRICS_DIR_ENV, str(tmp_path))
  monkeypatch.setattr(um, "_main_one_jid", lambda jid: pytest.fail("ran"))
  monkeypatch.setattr(
    um, "_default_metrics_date_range", lambda: pytest.fail("scheduled")
  )
  with pytest.raises(SystemExit, match="Codon metrics kernel required"):
    um.main(["update_metrics.py", "--jid", "1"], sleep_after=False)


def test_disable_env_is_test_only_switch(monkeypatch):
  monkeypatch.setenv(codon_bridge.CODON_METRICS_DISABLE_ENV, "1")
  assert codon_bridge.kernels() is None
  assert codon_bridge._state.reason == codon_bridge.CODON_METRICS_DISABLE_ENV


def _abi_group_reduce(calls):
  """Fake ``group_reduce`` with the pyext ABI, backed by the NumPy reference."""

  def group_reduce(n, keys, values, has_mask, mask, scratch, ok, oc, os_, om):
    calls.append("group_reduce")
    m = mask.view(bool) if has_mask else None
    k, c, s, x = sg._np_group_reduce(keys[:n], values[:n], m)
    g = k.shape[0]
    ok[:g], oc[:g], os_[:g], om[:g] = k, c, s, x
    return g

  return group_reduce


def _abi_residual_collapse(calls):
  """Fake ``residual_collapse`` with the pyext ABI (NumPy reference)."""

  def residual_collapse(n, keys, dev, values, mask, scratch, ok, ov):
    calls.append("residual_collapse")
    assert keys.dtype == dev.dtype == np.int64 and mask.dtype == np.uint8
    assert scratch.shape[0] >= 4 * n + codon_bridge.RADIX_BUCKETS
    k, v = sg._np_residual_collapse(keys, dev, values, mask.view(bool))
    g = k.shape[0]
    ok[:g], ov[:g] = k, v
    return g

  return residual_collapse


def test_frame_reductions_route_through_kernel_abi(monkeypatch):
  from hpcperfstats.analysis.metrics.lib.columnar import kernels as kn
  from hpcperfstats.analysis.metrics.lib.columnar.jobframe import JobFrame
  from hpcperfstats.analysis.metrics.tests.test_columnar_kernels import (
    _random_records,
  )

  frame = JobFrame.from_records("1", _random_records(4, shared=True, n=600))

  def snapshot():
    idx = kn.FrameIndex(frame, shared=True, residual_types=["host_mem", "nfs"])
    sql = idx.sum_per_sample("host_mem", ["mem_used"], "value")
    orm = idx.sum_per_sample(
      "host_cpu", None, "arc", mode=kn.MODE_ORM, coalesce_zero=False
    )
    return (
      sql.sum_val,
      orm.sum_val,
      idx.delta_sum_by_event("nfs", ["normal_read"]),
      idx.delta_sum_by_event("host_cpu", ["user", "nice"]),
    )

  monkeypatch.setenv(codon_bridge.CODON_METRICS_DISABLE_ENV, "1")
  want = snapshot()
  calls = []
  _install_fake(
    monkeypatch,
    group_reduce=_abi_group_reduce(calls),
    residual_collapse=_abi_residual_collapse(calls),
    join_sorted=lambda nl, left, nr, right, li, ri: _abi_join(
      left, right, li, ri
    ),
  )
  got = snapshot()
  assert {"group_reduce", "residual_collapse"} <= set(calls)
  for g, w in zip(got[:2], want[:2], strict=True):
    assert np.array_equal(g, w, equal_nan=True)
  assert got[2:] == want[2:]


def _abi_join(left, right, li, ri):
  a, b = sg._np_join_keys(left, right)
  li[: a.shape[0]], ri[: b.shape[0]] = a, b
  return a.shape[0]


def test_residual_collapse_kernel_error_does_not_fall_back(monkeypatch):
  def boom(*_a):
    raise RuntimeError("bad buffer")

  _install_fake(monkeypatch, residual_collapse=boom)
  calls = []
  real = sg._np_residual_collapse
  monkeypatch.setattr(
    sg,
    "_np_residual_collapse",
    lambda *a, **k: calls.append(1) or real(*a, **k),
  )
  with pytest.raises(codon_bridge.CodonKernelError, match="residual_collapse"):
    sg.collapse_residual_copies(
      np.array([1, 1]),
      np.array([0, -1]),
      np.array([1.0, 2.0]),
      np.ones(2, dtype=bool),
    )
  assert calls == []


def test_missing_residual_collapse_export_disables_kernel(
  monkeypatch, tmp_path
):
  assert "residual_collapse" in codon_bridge._KERNEL_METHODS
  (tmp_path / codon_bridge.PYEXT_SO).write_bytes(b"")
  monkeypatch.setenv(codon_bridge.CODON_METRICS_DIR_ENV, str(tmp_path))
  fake = SimpleNamespace(
    **{
      n: (lambda *a: 0)
      for n in codon_bridge._KERNEL_METHODS
      if n != "residual_collapse"
    }
  )
  monkeypatch.setattr(
    codon_bridge.importlib, "import_module", lambda _name: fake
  )
  monkeypatch.setattr(codon_bridge, "_deepbind_libomp", lambda _d: None)
  monkeypatch.setattr(sys, "path", list(sys.path))
  assert codon_bridge.kernels() is None
  assert codon_bridge._state.reason == "missing_methods residual_collapse"


def _sentinel_entry_points():
  """Public sentinel_profile calls that reach each sentinel kernel."""
  from hpcperfstats.analysis.metrics.lib import sentinel_profile as sp
  from hpcperfstats.analysis.metrics.tests import (
    test_metrics_sentinel_aggregation as agg,
  )

  jt = agg._ib_job(overrides={("c1.x", "port_rcv_data"): {20: -3}})
  return sp, {
    "sentinel_samples": lambda: sp.sentinel_samples(
      jt.index, "host_ib", list(agg._IB), "arc", [], limit=None
    ),
    "sentinel_fold": lambda: sp.sentinel_fold(
      np.array([0, 0]),
      np.array([0, 60_000_000]),
      np.array([2.0, np.nan]),
      np.array([sp.SAMPLE_VALID, sp.SAMPLE_GAP]),
      np.array([0, -3]),
      1.0,
      typename="t",
    ),
    "fill_interior_gaps": lambda: sp.fill_interior_gaps(
      np.array([1.0, np.nan, 3.0]), np.array([False, True, False]), 3, 0.5
    ),
  }


@pytest.mark.parametrize(
  "kernel", ["sentinel_samples", "sentinel_fold", "fill_interior_gaps"]
)
def test_sentinel_kernel_error_does_not_fall_back(monkeypatch, kernel):
  sp, entry = _sentinel_entry_points()

  def boom(*_a):
    raise RuntimeError("bad buffer")

  _install_fake(monkeypatch, **{kernel: boom})
  calls = []
  twin = f"_np_{kernel}"
  real = getattr(sp, twin)
  monkeypatch.setattr(
    sp, twin, lambda *a, **k: calls.append(1) or real(*a, **k)
  )
  with pytest.raises(codon_bridge.CodonKernelError, match=kernel):
    entry[kernel]()
  assert calls == []


def test_missing_sentinel_exports_disable_kernel(monkeypatch, tmp_path):
  (tmp_path / codon_bridge.PYEXT_SO).write_bytes(b"")
  monkeypatch.setenv(codon_bridge.CODON_METRICS_DIR_ENV, str(tmp_path))
  sentinel = ("sentinel_samples", "sentinel_fold", "fill_interior_gaps")
  fake = SimpleNamespace(
    **{
      n: (lambda *a: 0)
      for n in codon_bridge._KERNEL_METHODS
      if n not in sentinel
    }
  )
  monkeypatch.setattr(
    codon_bridge.importlib, "import_module", lambda _name: fake
  )
  monkeypatch.setattr(codon_bridge, "_deepbind_libomp", lambda _d: None)
  monkeypatch.setattr(sys, "path", list(sys.path))
  assert codon_bridge.kernels() is None
  assert codon_bridge._state.reason == f"missing_methods {','.join(sentinel)}"
