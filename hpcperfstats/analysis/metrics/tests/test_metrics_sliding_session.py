"""Unit tests for metrics sliding session and idle-slot fill."""

from __future__ import annotations

import threading
import time
from collections import deque
from types import SimpleNamespace

import pytest

from hpcperfstats.analysis.metrics.lib import metrics_sliding_session as mss
from hpcperfstats.analysis.metrics.lib.metrics_idle_slot_supplement import (
  pop_supplement_refs_from_ready_queue,
)


class _ReadyAsync:
  def __init__(self, value=None, exc=None):
    self._value = value
    self._exc = exc

  def ready(self):
    return True

  def get(self, timeout=None):
    del timeout
    if self._exc is not None:
      raise self._exc
    return self._value


class _SlowAsync:
  def __init__(self):
    self._ready = False
    self._value = None

  def ready(self):
    return self._ready

  def mark_ready(self, value):
    self._value = value
    self._ready = True

  def get(self, timeout=None):
    del timeout
    return self._value


@pytest.mark.machine_unit_mock
def test_should_use_metrics_sliding_session_requires_apply_async():
  assert mss.should_use_metrics_sliding_session(
      supplement_enabled=True, shared_pool=None,
  ) is False

  class _P:
    def apply_async(self, *a, **k):
      del a, k
      return None

  assert mss.should_use_metrics_sliding_session(
      supplement_enabled=True, shared_pool=_P(),
  ) is True
  assert mss.should_use_metrics_sliding_session(
      supplement_enabled=False, shared_pool=_P(),
  ) is False


@pytest.mark.machine_unit_mock
def test_metrics_idle_slots_fill_while_large_original_inflight():
  ready = deque([
      SimpleNamespace(jid="small", estimated_sample_count=10),
      SimpleNamespace(jid="huge", estimated_sample_count=90000),
  ])
  lock = threading.Lock()
  submitted = []

  class _Pool:
    def __init__(self):
      self._large = None

    def apply_async(self, fn, args):
      del fn
      # metrics: ((metrics_obj, ref),) ; prewarm: (jid,)
      if args and isinstance(args[0], tuple) and len(args[0]) == 2:
        ref = args[0][1]
        jid = getattr(ref, "jid", None)
      else:
        jid = args[0] if args else None
        submitted.append(("prewarm", jid))
        return _ReadyAsync({"jid": jid, "ok": True})
      submitted.append(jid)
      if jid == "large":
        async_r = _SlowAsync()
        self._large = async_r
        return async_r
      return _ReadyAsync({
          "jid": jid,
          "status": "ok",
          "rows": [],
          "distinct_time_count": 1,
      })

  pool = _Pool()
  primary = [SimpleNamespace(jid="large", estimated_sample_count=100000)]

  def persist(payload):
    return {
        "jid": payload["jid"],
        "ok": True,
        "status": "ok",
        "persist_s": 0.0,
    }

  def _finish_large():
    time.sleep(0.05)
    if pool._large is not None:
      pool._large.mark_ready({
          "jid": "large",
          "status": "ok",
          "rows": [],
          "distinct_time_count": 1,
      })

  t = threading.Thread(target=_finish_large)
  t.start()
  rows = mss.run_metrics_sliding_session(
      primary_refs=primary,
      metrics_obj=object(),
      shared_pool=pool,
      unwrap_fn=lambda a: a,
      persist_fn=persist,
      prewarm_worker_fn=lambda jid: {"jid": jid, "ok": True},
      inline_prewarm_fn=None,
      prewarm_mode="pipeline_required",
      max_inflight=2,
      poll_timeout_s=0.01,
      stall_timeout_s=2.0,
      ready_queue=ready,
      ready_queue_lock=lock,
      soft_max=10000,
      hard_max=80000,
      supplement_enabled=True,
      empty_supplement_sleep_s=0.0,
  )
  t.join()
  assert "small" in submitted
  assert "huge" not in submitted
  assert {r["ref"].jid for r in rows} >= {"large", "small"}


@pytest.mark.machine_unit_mock
def test_sliding_feeder_does_not_busy_spin_on_empty_supplement(monkeypatch):
  sleeps = []

  def _sleep(s):
    sleeps.append(s)
    # Advance monotonic? real sleep not needed; just record.

  monkeypatch.setattr(mss.time, "sleep", _sleep)

  class _NeverReady:
    def ready(self):
      return False

    def get(self, timeout=None):
      del timeout
      raise TimeoutError("not ready")

  calls = {"n": 0}

  class _Pool:
    def apply_async(self, fn, args):
      del fn, args
      calls["n"] += 1
      return _NeverReady()

  mss.run_metrics_sliding_session(
      primary_refs=[SimpleNamespace(jid="big", estimated_sample_count=1)],
      metrics_obj=object(),
      shared_pool=_Pool(),
      unwrap_fn=lambda a: a,
      persist_fn=lambda p: p,
      prewarm_worker_fn=lambda j: {"jid": j, "ok": True},
      inline_prewarm_fn=None,
      prewarm_mode="pipeline_required",
      max_inflight=1,
      poll_timeout_s=0.0,
      stall_timeout_s=0.15,
      ready_queue=deque(),
      ready_queue_lock=threading.Lock(),
      empty_supplement_sleep_s=0.05,
  )
  assert calls["n"] == 1
  assert any(s >= 0.05 for s in sleeps)


@pytest.mark.machine_unit_mock
def test_sliding_session_invokes_abort_with_pool(monkeypatch):
  seen = []

  def abort_fn(pool, *, context=""):
    seen.append({"pool": pool, "context": context})

  class _Pool:
    def apply_async(self, fn, args=(), kwds=None):
      del kwds
      payload = fn(*args)
      return _ReadyAsync(payload)

  monkeypatch.setattr(mss.time, "monotonic", lambda: 1000.0)
  rows = mss.run_metrics_sliding_session(
      primary_refs=[SimpleNamespace(jid="j1", estimated_sample_count=1)],
      metrics_obj=object(),
      shared_pool=_Pool(),
      unwrap_fn=lambda task: {
          "jid": task[1].jid,
          "ok": True,
          "status": "ok",
          "persisted_rows": 0,
          "distinct_time_count": 1,
          "persist_s": 0.0,
          "error_type": None,
          "error_message": None,
      },
      persist_fn=lambda payload: payload,
      prewarm_worker_fn=lambda jid: {"jid": jid, "ok": True},
      inline_prewarm_fn=None,
      prewarm_mode="pipeline_required",
      max_inflight=1,
      poll_timeout_s=0.01,
      stall_timeout_s=5.0,
      ready_queue=deque(),
      ready_queue_lock=threading.Lock(),
      abort_if_pool_dead_fn=abort_fn,
  )
  assert len(rows) == 1
  assert seen
  assert seen[0]["context"] == "metrics sliding session"


@pytest.mark.machine_unit_mock
def test_pop_supplement_rc_e_via_helper():
  q = deque([SimpleNamespace(jid="s", estimated_sample_count=5)])
  assert pop_supplement_refs_from_ready_queue(
      q, max_n=1, soft_max=10, hard_max=80,
      original_batch_still_inflight=False,
  ) == []
