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
