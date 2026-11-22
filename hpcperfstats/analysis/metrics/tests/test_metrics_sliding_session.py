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
  assert (
    mss.should_use_metrics_sliding_session(
      supplement_enabled=True,
      shared_pool=None,
    )
    is False
  )

  class _P:
    def apply_async(self, *a, **k):
      del a, k
      return None

  assert (
    mss.should_use_metrics_sliding_session(
      supplement_enabled=True,
      shared_pool=_P(),
    )
    is True
  )
  assert (
    mss.should_use_metrics_sliding_session(
      supplement_enabled=False,
      shared_pool=_P(),
    )
    is False
  )


@pytest.mark.machine_unit_mock
def test_metrics_idle_slots_fill_while_large_original_inflight():
  ready = deque(
    [
      SimpleNamespace(jid="small", estimated_sample_count=10),
      SimpleNamespace(jid="huge", estimated_sample_count=90000),
    ]
  )
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
      return _ReadyAsync(
        {
          "jid": jid,
          "status": "ok",
          "rows": [],
          "distinct_time_count": 1,
        }
      )

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
      pool._large.mark_ready(
        {
          "jid": "large",
          "status": "ok",
          "rows": [],
          "distinct_time_count": 1,
        }
      )

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
def test_sliding_stall_clock_advances_only_after_persist(monkeypatch):
  """A completed worker is not progress until parent persistence returns."""
  now = [0.0]
  sleeps_after_persist = []
  persist_finished = [False]

  monkeypatch.setattr(mss.time, "monotonic", lambda: now[0])

  def _sleep(seconds):
    now[0] += max(0.1, float(seconds))
    if persist_finished[0]:
      sleeps_after_persist.append(seconds)

  monkeypatch.setattr(mss.time, "sleep", _sleep)

  class _Pool:
    def __init__(self):
      self.calls = 0

    def apply_async(self, fn, args):
      del fn, args
      self.calls += 1
      if self.calls == 1:
        return _ReadyAsync(
          {
            "jid": "ready",
            "status": "ok",
            "rows": [],
            "distinct_time_count": 1,
          }
        )
      return _SlowAsync()

  def _persist(payload):
    now[0] = 100.0
    persist_finished[0] = True
    return {
      "jid": payload["jid"],
      "ok": True,
      "status": "ok",
      "persist_s": 100.0,
    }

  mss.run_metrics_sliding_session(
    primary_refs=[
      SimpleNamespace(jid="ready", estimated_sample_count=1),
      SimpleNamespace(jid="stalled", estimated_sample_count=1),
    ],
    metrics_obj=object(),
    shared_pool=_Pool(),
    unwrap_fn=lambda args: args,
    persist_fn=_persist,
    prewarm_worker_fn=None,
    inline_prewarm_fn=None,
    prewarm_mode="inline",
    max_inflight=2,
    poll_timeout_s=0.1,
    stall_timeout_s=0.3,
    supplement_enabled=False,
    empty_supplement_sleep_s=0.0,
  )

  assert len(sleeps_after_persist) >= 3


@pytest.mark.machine_unit_mock
def test_pop_supplement_rc_e_via_helper():
  q = deque([SimpleNamespace(jid="s", estimated_sample_count=5)])
  assert (
    pop_supplement_refs_from_ready_queue(
      q,
      max_n=1,
      soft_max=10,
      hard_max=80,
      original_batch_still_inflight=False,
    )
    == []
  )


@pytest.mark.machine_unit_mock
def test_sliding_stall_in_prewarm_keeps_committed_metrics_outcome():
  """S7: a prewarm-phase stall must not report persisted metrics as failed."""

  class _Pool:
    def apply_async(self, fn, args):
      if fn is _prewarm:
        return _SlowAsync()  # never finishes
      return _ReadyAsync({"jid": args[0][1].jid, "status": "ok", "rows": []})

  def _prewarm(jid):
    return {"jid": jid, "ok": True}

  rows = mss.run_metrics_sliding_session(
    primary_refs=[SimpleNamespace(jid="p1", estimated_sample_count=5)],
    metrics_obj=object(),
    shared_pool=_Pool(),
    unwrap_fn=lambda a: a,
    persist_fn=lambda payload: {"jid": payload["jid"], "ok": True},
    prewarm_worker_fn=_prewarm,
    inline_prewarm_fn=None,
    prewarm_mode="pipeline_required",
    max_inflight=2,
    poll_timeout_s=0.05,  # long enough to harvest the ready metrics task
    stall_timeout_s=0.0,
    supplement_enabled=False,
    empty_supplement_sleep_s=0.0,
  )
  assert len(rows) == 1
  assert rows[0]["ok"] is True
  assert rows[0]["prewarm_ok"] is False
  assert rows[0]["base_outcome"]["ok"] is True


@pytest.mark.machine_unit_mock
def test_artifact_only_refs_skip_compute_even_as_supplements():
  """C6: an artifact-only ref is prewarmed, never sent to compute_metrics."""
  submitted = []
  slow = _SlowAsync()

  class _Pool:
    def apply_async(self, fn, args):
      if fn is _prewarm:
        submitted.append(("prewarm", args[0]))
        return _ReadyAsync({"jid": args[0], "ok": True})
      jid = args[0][1].jid
      submitted.append(("metrics", jid))
      if jid == "prim":
        return slow  # keeps the original in flight so slots take supplements
      return _ReadyAsync({"jid": jid, "status": "ok", "rows": []})

  def _prewarm(jid):
    return {"jid": jid, "ok": True}

  def _finish():
    time.sleep(0.1)
    slow.mark_ready({"jid": "prim", "status": "ok", "rows": []})

  ready = deque([SimpleNamespace(jid="sup-art", artifact_only=True)])
  t = threading.Thread(target=_finish)
  t.start()
  rows = mss.run_metrics_sliding_session(
    primary_refs=[
      SimpleNamespace(jid="prim", estimated_sample_count=5),
      SimpleNamespace(jid="prim-art", artifact_only=True),
    ],
    metrics_obj=object(),
    shared_pool=_Pool(),
    unwrap_fn=lambda a: a,
    persist_fn=lambda payload: {"jid": payload["jid"], "ok": True},
    prewarm_worker_fn=_prewarm,
    inline_prewarm_fn=None,
    prewarm_mode="pipeline_required",
    max_inflight=3,
    poll_timeout_s=0.02,
    stall_timeout_s=5.0,
    ready_queue=ready,
    ready_queue_lock=threading.Lock(),
    empty_supplement_sleep_s=0.0,
  )
  t.join()
  assert ("metrics", "prim") in submitted
  assert ("prewarm", "sup-art") in submitted  # taken as a supplement
  assert not {("metrics", "prim-art"), ("metrics", "sup-art")} & set(submitted)
  by_jid = {r["ref"].jid: r for r in rows}
  assert by_jid["sup-art"]["base_outcome"]["status"] == "artifact_only"
  assert by_jid["prim-art"]["prewarm_ok"] is True


class _EchoPool:
  """Pool whose metrics and prewarm tasks complete immediately."""

  def __init__(self, prewarm_fn):
    self.prewarm_fn = prewarm_fn
    self.metrics_jids = []

  def apply_async(self, fn, args):
    if fn is self.prewarm_fn:
      return _ReadyAsync({"jid": args[0], "ok": True})
    self.metrics_jids.append(args[0][1].jid)
    return _ReadyAsync({"jid": args[0][1].jid, "status": "ok", "rows": []})


def _continuous_session(pool, ready, lock, should_stop, **kw):
  """Run a continuous session with pass-through compute/persist."""
  return mss.run_metrics_sliding_session(
    primary_refs=[SimpleNamespace(jid="p0")],
    metrics_obj=object(),
    shared_pool=pool,
    unwrap_fn=lambda a: a,
    persist_fn=lambda payload: {"jid": payload["jid"], "ok": True},
    prewarm_worker_fn=pool.prewarm_fn,
    inline_prewarm_fn=None,
    prewarm_mode="pipeline_required",
    max_inflight=2,
    poll_timeout_s=0.02,
    ready_queue=ready,
    ready_queue_lock=lock,
    empty_supplement_sleep_s=0.01,
    continuous=True,
    should_stop=should_stop,
    **kw,
  )


@pytest.mark.machine_unit_mock
def test_continuous_session_keeps_slots_fed_until_producer_done():
  """T2: work arriving later is taken by the same session; idle != stall."""

  def _prewarm(jid):
    return {"jid": jid, "ok": True}

  pool = _EchoPool(_prewarm)
  ready, lock, done = deque(), threading.Lock(), threading.Event()

  def _producer():
    for i in range(3):
      time.sleep(0.15)  # idle gaps longer than stall_timeout_s
      with lock:
        ready.append(SimpleNamespace(jid=f"late{i}"))
    done.set()

  t = threading.Thread(target=_producer)
  t.start()
  rows = _continuous_session(
    pool,
    ready,
    lock,
    lambda: done.is_set() and not ready,
    stall_timeout_s=0.1,
  )
  t.join()
  assert pool.metrics_jids == ["p0", "late0", "late1", "late2"]
  assert all(r["ok"] and r.get("prewarm_ok") for r in rows)
  assert not [
    r
    for r in rows
    if r["base_outcome"].get("status") == "sliding_session_stall"
  ]


@pytest.mark.machine_unit_mock
def test_continuous_session_stop_leaves_queued_work_for_later():
  """T2: should_stop() stops taking new work; queued refs stay queued."""

  def _prewarm(jid):
    return {"jid": jid, "ok": True}

  pool = _EchoPool(_prewarm)
  lock = threading.Lock()
  ready = deque([SimpleNamespace(jid="q1"), SimpleNamespace(jid="q2")])
  rows = _continuous_session(pool, ready, lock, lambda: True, stall_timeout_s=5)
  assert pool.metrics_jids == ["p0"]
  assert [r.jid for r in ready] == ["q1", "q2"]
  assert [r["ref"].jid for r in rows] == ["p0"]


# Memory-budget admission (hs04 OOM, 2026-11-21): admission was by count
# only, so a burst of large backfill jobs (each holding several copies of
# its frame) took up to 188 GB RSS and the process was OOM-killed.

# Cost of one single-host host-minute (frame plus pivot grids); linear in
# samples while the held grids stay under the pivot cache cap.
_C = mss.MemoryCostModel().cost(SimpleNamespace(estimated_sample_count=1))


class _CountdownAsync:
  """Metrics task that becomes ready after a few ``ready()`` polls."""

  def __init__(self, pool, jid, polls):
    self._pool, self._jid, self._polls = pool, jid, polls

  def ready(self):
    self._polls -= 1
    return self._polls <= 0

  def get(self, timeout=None):
    del timeout
    self._pool.inflight.discard(self._jid)
    return {"jid": self._jid, "status": "ok"}


class _TrackPool:
  """Records, for each metrics submit, which jobs were already in flight."""

  def __init__(self, polls=3):
    self.polls = polls
    self.inflight = set()
    self.submits = []

  def apply_async(self, fn, args):
    del fn
    if args and isinstance(args[0], tuple):
      jid = args[0][1].jid
      self.submits.append((jid, frozenset(self.inflight)))
      self.inflight.add(jid)
      return _CountdownAsync(self, jid, self.polls)
    return _ReadyAsync({"jid": args[0], "ok": True})


def _ref(jid, samples, **kw):
  return SimpleNamespace(jid=jid, estimated_sample_count=samples, **kw)


def _session(pool, primary=(), ready=None, **kw):
  kw.setdefault("max_inflight", 8)
  if ready is not None:
    kw.update(
      ready_queue=ready,
      ready_queue_lock=threading.Lock(),
      continuous=True,
      should_stop=lambda: not ready,
    )
  return mss.run_metrics_sliding_session(
    primary_refs=list(primary),
    metrics_obj=None,
    shared_pool=pool,
    unwrap_fn=lambda *_a: None,
    persist_fn=lambda payload: {"jid": payload["jid"], "ok": True},
    prewarm_worker_fn=lambda jid: {"jid": jid, "ok": True},
    inline_prewarm_fn=None,
    prewarm_mode="pipeline_required",
    poll_timeout_s=0.05,
    stall_timeout_s=30.0,
    empty_supplement_sleep_s=0.0,
    **kw,
  )


def _max_concurrent(pool):
  return max(len(before) + 1 for _jid, before in pool.submits)


@pytest.mark.machine_unit_mock
def test_primary_fill_respects_memory_budget():
  pool = _TrackPool()
  rows = _session(
    pool,
    primary=[_ref(j, 10) for j in "abcd"],
    memory_budget_bytes=25 * _C,
  )
  assert sorted(r["ref"].jid for r in rows) == list("abcd")
  assert _max_concurrent(pool) == 2


@pytest.mark.machine_unit_mock
def test_take_fifo_respects_memory_budget():
  pool = _TrackPool()
  ready = deque([_ref(j, 10) for j in "abcde"])
  rows = _session(pool, ready=ready, memory_budget_bytes=35 * _C)
  assert sorted(r["ref"].jid for r in rows) == list("abcde")
  assert _max_concurrent(pool) == 3
  assert not ready


@pytest.mark.machine_unit_mock
def test_budget_admits_one_when_idle():
  pool = _TrackPool()
  rows = _session(
    pool,
    primary=[_ref("huge", 10_000), _ref("next", 10_000)],
    memory_budget_bytes=_C,
  )
  assert [r["ref"].jid for r in rows] == ["huge", "next"]
  assert pool.submits == [("huge", frozenset()), ("next", frozenset())]


@pytest.mark.machine_unit_mock
def test_budget_zero_disables():
  pool = _TrackPool()
  _session(
    pool, primary=[_ref(j, 10**9) for j in "abcd"], memory_budget_bytes=0
  )
  assert _max_concurrent(pool) == 4


@pytest.mark.machine_unit_mock
def test_artifact_only_counts_full_cost():
  compute = _ref("c", 10)
  artifact = _ref("a", 10, artifact_only=True)
  model = mss.MemoryCostModel()
  assert model.cost(artifact) == model.cost(compute)

  class _SlowPrewarmPool(_TrackPool):
    def apply_async(self, fn, args):
      if args and not isinstance(args[0], tuple):
        jid = args[0]
        self.submits.append((jid, frozenset(self.inflight)))
        self.inflight.add(jid)
        return _CountdownAsync(self, jid, self.polls)
      return super().apply_async(fn, args)

  pool = _SlowPrewarmPool()
  _session(pool, primary=[artifact, compute], memory_budget_bytes=15 * _C)
  # The artifact-only prewarm held the budget, so the compute waited.
  assert pool.submits[0] == ("a", frozenset())
  assert next(b for j, b in pool.submits if j == "c") == frozenset()


@pytest.mark.machine_unit_mock
def test_aged_backfill_never_blocks_fresh():
  old = time.monotonic() - 10_000
  q = deque(
    [
      _ref("bigb", 1, lane="backfill"),
      _ref("b2", 1, lane="backfill"),
      _ref("f1", 1),
    ]
  )
  skipped = {"bigb": old}
  taken = mss.pop_fresh_first(
    q, 8, admit=lambda r: r.jid != "bigb", skipped_since=skipped, head_wait_s=60
  )
  # Fresh still passes the aged backfill head; backfill behind it waits.
  assert [r.jid for r in taken] == ["f1"]
  assert [r.jid for r in q] == ["bigb", "b2"]
  # Before the wait has elapsed, backfill may skip ahead of it.
  q = deque([_ref("bigb", 1, lane="backfill"), _ref("b2", 1, lane="backfill")])
  skipped = {}
  taken = mss.pop_fresh_first(
    q, 8, admit=lambda r: r.jid != "bigb", skipped_since=skipped, head_wait_s=60
  )
  assert [r.jid for r in taken] == ["b2"]
  assert set(skipped) == {"bigb"}


@pytest.mark.machine_unit_mock
def test_aged_fresh_blocks_backfill():
  old = time.monotonic() - 10_000
  q = deque([_ref("bigf", 1), _ref("f2", 1), _ref("b1", 1, lane="backfill")])
  skipped = {"bigf": old, "gone": old}
  taken = mss.pop_fresh_first(
    q, 8, admit=lambda r: r.jid != "bigf", skipped_since=skipped, head_wait_s=60
  )
  assert taken == []
  assert [r.jid for r in q] == ["bigf", "f2", "b1"]
  # Entries for refs that left the queue are dropped.
  assert set(skipped) == {"bigf"}


@pytest.mark.machine_unit_mock
def test_admit_paused_on_headroom():
  pool = _TrackPool()
  calls = []

  def _no_headroom():
    calls.append(1)
    return False

  rows = _session(
    pool,
    primary=[_ref(j, 1) for j in "abc"],
    memory_budget_bytes=0,
    admit_ok=_no_headroom,
  )
  assert sorted(r["ref"].jid for r in rows) == list("abc")
  # Never two at once: with work in flight the gate refused every admit,
  # and an idle session still admitted one job at a time.
  assert _max_concurrent(pool) == 1
  assert calls
  ready = deque([_ref(j, 1) for j in "xyz"])
  pool = _TrackPool()
  _session(pool, ready=ready, admit_ok=_no_headroom)
  assert _max_concurrent(pool) == 1 and not ready


def test_peak_bytes_cover_float64_value_columns():
  """host_data value/arc/delta frames are float64: the build peak (~65 B/row)
  plus frame and FrameIndex (~56 B/row) exceed the old 100 B/row float32
  allowance, which under-admitted jobs by ~20% (the hs04 OOM guard)."""
  import numpy as np

  from hpcperfstats.analysis.metrics.lib.columnar.jobframe import VALUE_DTYPE

  assert np.dtype(VALUE_DTYPE) == np.float64
  assert mss.METRICS_PEAK_BYTES_PER_ROW >= 65 + 56


def test_cost_model_learns_denser_jobs_but_never_drops_below_floor():
  """Regression (hs04 OOM, 2026-11-21): a fixed 201 rows per host-minute
  (single-host golden corpus) under-costed production jobs measured at
  238-335 rows per host-minute, so admission let in about twice the
  budget. The model floors at the production p90 and learns upward."""
  model = mss.MemoryCostModel(rows_floor=300, bytes_per_row=100, grid_floor=0)
  timed = {"runtime_s": 600.0}
  assert model.rows_per_host_minute() == 300
  for _ in range(20):
    model.observe(_ref("sparse", 100, **timed), 100 * 50)
  assert model.rows_per_host_minute() == 300
  for _ in range(200):
    model.observe(_ref("dense", 100, **timed), 100 * 900)
  assert model.rows_per_host_minute() == 900
  assert model.cost(_ref("x", 10)) == 10 * 900 * 100
  untimed = mss.MemoryCostModel(rows_floor=300)
  untimed.observe(_ref("guess", 2880), 2880 * 5000)
  assert untimed.rows_per_host_minute() == 300


def _slow_prewarm_pool():
  class _Pool(_TrackPool):
    def apply_async(self, fn, args):
      if args and not isinstance(args[0], tuple):
        jid = args[0]
        self.submits.append((f"pw:{jid}", frozenset(self.inflight)))
        self.inflight.add(f"pw:{jid}")
        return _CountdownAsync(self, f"pw:{jid}", self.polls * 4)
      return super().apply_async(fn, args)

  return _Pool()


@pytest.mark.machine_unit_mock
def test_measured_frame_size_replaces_the_estimate():
  """Once a job's metrics finished, its in-flight cost is its frame rows x
  peak bytes per row, not the host-minute estimate."""
  model = mss.MemoryCostModel(rows_floor=10, bytes_per_row=10, grid_floor=0)
  refs = [_ref("a", 10, runtime_s=600.0), _ref("b", 10, runtime_s=600.0)]
  budget = 1500  # one estimate (10 x 10 x 10) fits, two do not

  def _run(observe):
    pool = _slow_prewarm_pool()
    _session(
      pool,
      primary=refs,
      memory_budget_bytes=budget,
      cost_model=model,
      observe_size=observe,
    )
    return next(before for jid, before in pool.submits if jid == "b")

  # Estimate only: b waits for a's prewarm to finish as well.
  assert _run(None) == frozenset()
  # a's frame turned out to be 5 rows (50 bytes): b starts during its prewarm.
  assert "pw:a" in _run(lambda ref: (5, 0) if ref.jid == "a" else (0, 0))


@pytest.mark.machine_unit_mock
def test_measured_rows_teach_the_session_model():
  model = mss.MemoryCostModel(rows_floor=1, bytes_per_row=1, grid_floor=0)
  _session(
    _TrackPool(),
    primary=[_ref("a", 4, runtime_s=240.0)],
    cost_model=model,
    observe_size=lambda ref: (400, 0),
  )
  assert model.rows_per_host_minute() == 100


@pytest.mark.machine_unit_mock
def test_admission_telemetry_reports_estimated_and_measured(monkeypatch):
  monkeypatch.setattr(mss, "ADMISSION_TELEMETRY_INTERVAL_S", 0.0)
  seen = []
  model = mss.MemoryCostModel(rows_floor=100, bytes_per_row=10)
  pool = _slow_prewarm_pool()
  pool.polls = 40  # metrics outlives several loop turns
  _session(
    pool,
    primary=[_ref("a", 10, runtime_s=600.0)],
    memory_budget_bytes=123,
    cost_model=model,
    observe_size=lambda ref: (7, 0),
    telemetry_fn=seen.append,
  )
  assert seen
  assert set(seen[0]) == {
    "inflight",
    "estimated_bytes",
    "measured_bytes",
    "budget_bytes",
    "rows_per_host_minute",
    "grid_bytes_per_host_minute",
  }
  assert seen[0]["estimated_bytes"] == model.cost(_ref("a", 10))
  assert seen[-1]["measured_bytes"] == 70 and seen[-1]["budget_bytes"] == 123


def test_cost_prices_pivot_grids_linear_in_host_minutes():
  """Regression (hs04 OOM, 2026-11-21): job 100430 (162 hosts x 551 min,
  24.7M rows) peaked at 28.9 GiB, 1254 B/row, against a 140 B/row cost,
  almost all of it metrics pivot grids, which a per-row cost cannot see.
  Pivots hold one row per (host, sample time) pair, so the grid term
  is host-minutes x grid bytes per host-minute: ``estimated_sample_count``
  already carries ``nhosts``, and no second ``nhosts`` factor applies."""
  floor = mss.METRICS_GRID_BYTES_PER_HOST_MINUTE
  model = mss.MemoryCostModel(
    rows_floor=1, bytes_per_row=0, grid_floor=floor, pivot_cache_bytes=10**8
  )
  small = model.cost(_ref("s", 1000, nhosts=1))
  assert small == 1000 * floor * (1 + mss.PIVOTED_TYPES_PER_JOB)
  # Same host-minutes spread over 100 hosts: same grid, not 100x.
  assert model.cost(_ref("w", 1000, nhosts=100)) == small
  # The hs04 job: one grid of host-minutes x the floor, plus the cache.
  host_minutes = 162 * 551
  hs04 = mss.MemoryCostModel().cost(_ref("100430", host_minutes, nhosts=162))
  grid = host_minutes * mss.METRICS_GRID_BYTES_PER_HOST_MINUTE
  frame = host_minutes * mss.METRICS_ROWS_PER_HOST_MINUTE
  assert hs04 == int(
    frame * mss.METRICS_PEAK_BYTES_PER_ROW
    + grid
    + min(mss.PIVOT_CACHE_MAX_BYTES, grid * mss.PIVOTED_TYPES_PER_JOB)
  )


def test_cost_doubling_hosts_at_fixed_per_host_samples_doubles_grid():
  """Twice the hosts at the same samples per host is twice the
  host-minutes, so the grid term doubles; a hosts-squared term would
  quadruple it."""
  floor = mss.METRICS_GRID_BYTES_PER_HOST_MINUTE
  model = mss.MemoryCostModel(
    rows_floor=1, bytes_per_row=0, grid_floor=floor, pivot_cache_bytes=0
  )
  minutes = 551
  for nhosts in (1, 4, 81):
    one = model.cost(_ref("a", nhosts * minutes, nhosts=nhosts))
    two = model.cost(_ref("b", 2 * nhosts * minutes, nhosts=2 * nhosts))
    assert one == nhosts * minutes * floor
    assert two == 2 * one


def test_cost_model_learns_denser_grids_but_never_drops_below_floor():
  """The grid density is learned per host-minute (grid bytes / estimated
  host-minutes, no ``nhosts`` factor), so the learned p90 stays linear."""
  floor = mss.METRICS_GRID_BYTES_PER_HOST_MINUTE
  model = mss.MemoryCostModel(rows_floor=1, bytes_per_row=0, grid_floor=floor)
  timed = {"runtime_s": 600.0, "nhosts": 10}
  for _ in range(20):
    model.observe(_ref("sparse", 100, **timed), 1, 100 * (floor // 10))
  assert model.grid_bytes_per_host_minute() == floor
  for _ in range(200):
    model.observe(_ref("dense", 100, **timed), 1, 100 * (5 * floor))
  assert model.grid_bytes_per_host_minute() == 5 * floor
  wide = mss.MemoryCostModel(rows_floor=1, bytes_per_row=0, grid_floor=0)
  wide.observe(_ref("w", 100, runtime_s=600.0, nhosts=50), 1, 100 * 300)
  assert wide.grid_bytes_per_host_minute() == 300
  # Unknown grid size (0) teaches rows only.
  model.observe(_ref("rows", 100, **timed), 1, 0)
  assert model.grid_bytes_per_host_minute() == 5 * floor
