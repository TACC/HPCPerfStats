"""Stuck-cohort ingest recycle by oldest_inflight age (Sep-29 OOM telem)."""

from __future__ import annotations

import time
from types import SimpleNamespace

from hpcperfstats.dbload.lib import sync_timedb_job_store as jq
from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo
from hpcperfstats.dbload.lib.sync_timedb_job_store import SyncTimedbJobStore


def test_oldest_ingest_inflight_age_empty():
  assert qo._oldest_ingest_inflight_age_s({}) == (0.0, None)
  assert qo._oldest_ingest_inflight_age_s(None) == (0.0, None)


def test_oldest_ingest_inflight_age_picks_min_submit(monkeypatch):
  now = 1000.0
  monkeypatch.setattr(time, "monotonic", lambda: now)
  age, ident = qo._oldest_ingest_inflight_age_s(
      {"a": 900.0, "b": 800.0, "c": 950.0},
      now=now,
  )
  assert ident == "b"
  assert age == 200.0


def test_stuck_cohort_recycle_disabled_when_threshold_zero(monkeypatch):
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_ingest_stuck_inflight_recycle_s",
      lambda: 0,
  )
  gate = qo.IngestRecycleGate()
  now = 5000.0
  monkeypatch.setattr(time, "monotonic", lambda: now)
  out = qo._maybe_request_stuck_cohort_recycle(
      recycle_gate=gate,
      client=SyncTimedbJobStore(""),
      inflight={"x": SimpleNamespace()},
      claims={},
      submitted={"x": now - 99999.0},
      inflight_sizes={"x": 1},
      last_stuck_recycle_mono=0.0,
  )
  assert out == 0.0
  assert not gate.recycle_requested.is_set()


def test_stuck_cohort_recycle_triggers_and_requeues(monkeypatch):
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_ingest_stuck_inflight_recycle_s",
      lambda: 3600,
  )
  telem: list[str] = []
  monkeypatch.setattr(
      qo,
      "_emit_mem_telem_event",
      lambda event, *_a, **_k: telem.append(event),
  )
  requeued: list[str] = []
  monkeypatch.setattr(
      jq,
      "requeue_job",
      lambda *_a, **k: requeued.append(k.get("identity") or "") or True,
  )
  gate = qo.IngestRecycleGate()
  claim = jq.ClaimedJob(
      kind=jq.JOB_KIND_INGEST,
      identity="/stuck",
      owner_token="n:h:b:1",
      deadline=1e9,
      score=1.0,
  )
  now = 10000.0
  monkeypatch.setattr(time, "monotonic", lambda: now)
  out = qo._maybe_request_stuck_cohort_recycle(
      recycle_gate=gate,
      client=SyncTimedbJobStore(""),
      inflight={"/stuck": SimpleNamespace()},
      claims={"/stuck": claim},
      submitted={"/stuck": now - 4000.0},
      inflight_sizes={"/stuck": 99},
      last_stuck_recycle_mono=0.0,
  )
  assert out == now
  assert gate.recycle_requested.is_set()
  assert requeued == ["/stuck"]
  assert telem == ["stuck_cohort_recycle"]


def test_stuck_cohort_recycle_skips_when_under_threshold(monkeypatch):
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_ingest_stuck_inflight_recycle_s",
      lambda: 3600,
  )
  gate = qo.IngestRecycleGate()
  now = 10000.0
  monkeypatch.setattr(time, "monotonic", lambda: now)
  out = qo._maybe_request_stuck_cohort_recycle(
      recycle_gate=gate,
      client=SyncTimedbJobStore(""),
      inflight={"/young": SimpleNamespace()},
      claims={},
      submitted={"/young": now - 100.0},
      inflight_sizes={},
      last_stuck_recycle_mono=0.0,
  )
  assert out == 0.0
  assert not gate.recycle_requested.is_set()


def test_stuck_cohort_recycle_rate_limited(monkeypatch):
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_ingest_stuck_inflight_recycle_s",
      lambda: 3600,
  )
  gate = qo.IngestRecycleGate()
  now = 20000.0
  monkeypatch.setattr(time, "monotonic", lambda: now)
  out = qo._maybe_request_stuck_cohort_recycle(
      recycle_gate=gate,
      client=SyncTimedbJobStore(""),
      inflight={"/stuck": SimpleNamespace()},
      claims={},
      submitted={"/stuck": now - 5000.0},
      inflight_sizes={},
      last_stuck_recycle_mono=now - 100.0,
  )
  assert out == now - 100.0
  assert not gate.recycle_requested.is_set()
