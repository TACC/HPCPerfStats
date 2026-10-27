"""Write-phase telemetry must ferry across the parse/write stage split."""

from __future__ import annotations

import threading
import time

from hpcperfstats.dbload import sync_timedb as st
from hpcperfstats.dbload.lib import sync_timedb_ingest_write_stage as ws


def test_write_stage_returns_timing_on_payload(monkeypatch):
  """Write worker snapshots ContextVars onto payload.write_timing."""
  ws.stop_ingest_write_stage(join_timeout_s=1.0)

  def _fake_write(
    path, stats, proc_stats, *, need_archival=True, copy_unique=False, emit=""
  ):
    del path, stats, proc_stats, need_archival, copy_unique, emit
    st._reset_ingest_write_timing(enabled=True)
    st._add_ingest_postgres_s(1.25)
    st._add_ingest_write_phase("conflict_insert_s", 0.5)
    st._add_ingest_orm_rows(3)
    return ("/f", True, True)

  monkeypatch.setattr(st, "_write_stats_payload_to_db", _fake_write)
  ws.start_ingest_write_stage(write_workers=1, queue_max=2)
  try:
    st._reset_ingest_write_timing(enabled=True)
    payload = ws.IngestWritePayload(
      stats_file="/f",
      stats=None,
      proc_stats=None,
    )
    ws.submit_ingest_write_and_wait(payload)
    assert payload.ingest_ok is True
    assert payload.error is None
    timing = payload.write_timing
    assert float(timing.get("postgres_s", 0.0)) >= 1.25
    assert float(timing.get("conflict_insert_s", 0.0)) >= 0.5
    assert float(timing.get("orm_rows", 0.0)) >= 3.0
  finally:
    ws.stop_ingest_write_stage(join_timeout_s=2.0)
    st._reset_ingest_write_timing(enabled=False)


def test_submit_write_applies_timing_to_parse_thread(monkeypatch):
  """After cross-thread wait, parse-thread snapshot includes write totals."""
  ws.stop_ingest_write_stage(join_timeout_s=1.0)

  def _fake_write(
    path, stats, proc_stats, *, need_archival=True, copy_unique=False, emit=""
  ):
    del path, stats, proc_stats, need_archival, copy_unique, emit
    st._add_ingest_postgres_s(2.0)
    st._add_ingest_write_phase("copy_s", 0.75)
    return ("/f", False, True)

  monkeypatch.setattr(st, "_write_stats_payload_to_db", _fake_write)
  ws.start_ingest_write_stage(write_workers=1, queue_max=2)
  try:
    st._reset_ingest_write_timing(enabled=True)
    before = st._snapshot_ingest_write_timing()
    assert float(before.get("postgres_s", 0.0)) == 0.0
    path, archival, ok = st._submit_stats_payload_write(
      "/f",
      None,
      None,
      need_archival=False,
    )
    assert path == "/f"
    assert archival is False
    assert ok is True
    snap = st._snapshot_ingest_write_timing()
    assert float(snap.get("postgres_s", 0.0)) >= 2.0
    assert float(snap.get("copy_s", 0.0)) >= 0.75
  finally:
    ws.stop_ingest_write_stage(join_timeout_s=2.0)
    st._reset_ingest_write_timing(enabled=False)


def test_streaming_flushes_sum_write_timing(monkeypatch):
  """Two queued flushes accumulate on the parse-thread ContextVars."""
  ws.stop_ingest_write_stage(join_timeout_s=1.0)
  calls = {"n": 0}

  def _fake_write(
    path, stats, proc_stats, *, need_archival=True, copy_unique=False, emit=""
  ):
    del path, stats, proc_stats, need_archival, copy_unique, emit
    calls["n"] += 1
    st._add_ingest_postgres_s(1.0)
    st._add_ingest_write_phase("db_execute_s", 0.25)
    return ("/f", True, True)

  monkeypatch.setattr(st, "_write_stats_payload_to_db", _fake_write)
  ws.start_ingest_write_stage(write_workers=1, queue_max=4)
  try:
    st._reset_ingest_write_timing(enabled=True)
    st._submit_stats_payload_write("/f", None, None)
    st._submit_stats_payload_write("/f", None, None)
    assert calls["n"] == 2
    snap = st._snapshot_ingest_write_timing()
    assert float(snap.get("postgres_s", 0.0)) >= 2.0
    assert float(snap.get("db_execute_s", 0.0)) >= 0.5
  finally:
    ws.stop_ingest_write_stage(join_timeout_s=2.0)
    st._reset_ingest_write_timing(enabled=False)


def test_apply_write_timing_does_not_double_campaign(monkeypatch):
  """Apply bumps per-file CVs only; campaign was already counted on write."""
  del monkeypatch
  st._reset_ingest_write_timing(enabled=True)
  try:
    st._add_ingest_postgres_s(1.0)
    campaign_mid = float(
      st._snapshot_ingest_write_campaign_timing().get("postgres_s", 0.0)
    )
    st._apply_ingest_write_timing_from_payload({"postgres_s": 5.0})
    campaign_after = float(
      st._snapshot_ingest_write_campaign_timing().get("postgres_s", 0.0)
    )
    assert campaign_after == campaign_mid
    assert (
      abs(float(st._snapshot_ingest_write_timing()["postgres_s"]) - 6.0) < 1e-9
    )
  finally:
    st._reset_ingest_write_timing(enabled=False)


def test_inline_write_does_not_double_apply_timing(monkeypatch):
  """When the write stage is down, inline timing stays single-counted."""
  ws.stop_ingest_write_stage(join_timeout_s=1.0)

  def _fake_write(
    path, stats, proc_stats, *, need_archival=True, copy_unique=False, emit=""
  ):
    del path, stats, proc_stats, need_archival, copy_unique, emit
    st._add_ingest_postgres_s(4.0)
    return ("/f", True, True)

  monkeypatch.setattr(st, "_write_stats_payload_to_db", _fake_write)
  st._reset_ingest_write_timing(enabled=True)
  try:
    st._submit_stats_payload_write("/f", None, None)
    snap = st._snapshot_ingest_write_timing()
    assert abs(float(snap.get("postgres_s", 0.0)) - 4.0) < 1e-9
  finally:
    st._reset_ingest_write_timing(enabled=False)


def test_write_timing_visible_after_cross_thread_barrier(monkeypatch):
  """Parse thread stays at zero until wait returns, then sees write totals."""
  ws.stop_ingest_write_stage(join_timeout_s=1.0)
  release = threading.Event()

  def _fake_write(
    path, stats, proc_stats, *, need_archival=True, copy_unique=False, emit=""
  ):
    del path, stats, proc_stats, need_archival, copy_unique, emit
    st._add_ingest_postgres_s(1.5)
    release.wait(timeout=2.0)
    return ("/f", True, True)

  monkeypatch.setattr(st, "_write_stats_payload_to_db", _fake_write)
  ws.start_ingest_write_stage(write_workers=1, queue_max=2)
  try:
    st._reset_ingest_write_timing(enabled=True)
    mid: dict[str, float] = {}

    def _run():
      st._submit_stats_payload_write("/f", None, None)
      mid.update(st._snapshot_ingest_write_timing())

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    deadline = time.monotonic() + 2.0
    while ws.write_queue_qsize() < 1 and time.monotonic() < deadline:
      time.sleep(0.01)
    # Still waiting on write: parse-side submit thread has not applied yet.
    # Caller thread here is the test thread (not the submit thread).
    release.set()
    t.join(timeout=3.0)
    assert float(mid.get("postgres_s", 0.0)) >= 1.5
  finally:
    ws.stop_ingest_write_stage(join_timeout_s=2.0)
    st._reset_ingest_write_timing(enabled=False)
