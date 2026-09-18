"""Closed-book ingest write and file-lock telemetry contracts."""
from __future__ import annotations

import threading
import time

import pytest

from hpcperfstats.dbload import sync_timedb as st
from hpcperfstats.dbload.lib.file_locking import (
    FILE_LOCK_TELEM_KEYS,
    file_read_lock_wait,
    file_write_lock,
    reset_file_lock_timing,
    snapshot_file_lock_timing,
)
from hpcperfstats.dbload.lib.sync_timedb_archive_members_store import (
    SyncTimedbArchiveMembersStore,
)
from hpcperfstats.dbload.lib.sync_timedb_job_store import SyncTimedbJobStore
from hpcperfstats.dbload.lib.sync_timedb_store_lock_timing import (
    STORE_LOCK_TELEM_KEYS,
    reset_store_lock_timing,
    snapshot_store_lock_timing,
)


def test_ingest_write_telemetry_off_snapshot_has_no_phase_keys():
  """Default-off write telemetry must not emit phase keys."""
  st._reset_ingest_write_timing(enabled=False)
  try:
    with st._held_ingest_write_timing():
      time.sleep(0.001)
    snap = st._snapshot_ingest_write_timing()
    assert snap == {"postgres_s": snap["postgres_s"]}
    assert "orm_materialize_s" not in snap
    meta = st._merge_ingest_write_timing_into_meta({})
    assert "orm_execute_s" not in meta
    assert "orm_materialize_s" not in meta
  finally:
    st._reset_ingest_write_timing(enabled=False)


def test_ingest_write_telemetry_on_emits_all_phase_keys():
  """When enabled, every write phase key is present and sums near postgres_s."""
  st._reset_ingest_write_timing(enabled=True)
  try:
    with st._held_ingest_write_timing():
      with st._held_ingest_write_phase("orm_materialize_s"):
        time.sleep(0.02)
      with st._held_ingest_write_phase("db_execute_s"):
        time.sleep(0.03)
    snap = st._snapshot_ingest_write_timing()
    for key in st.INGEST_WRITE_PHASE_KEYS:
      assert key in snap
      assert snap[key] >= 0.0
    assert snap["orm_bulk_prep_s"] == 0.0
    assert snap["db_commit_s"] == 0.0
    phase_sum = sum(snap[key] for key in st.INGEST_WRITE_PHASE_KEYS)
    assert abs(snap["postgres_s"] - phase_sum) < 0.05
    assert snap["orm_materialize_s"] >= 0.01
    assert snap["db_execute_s"] >= 0.02
  finally:
    st._reset_ingest_write_timing(enabled=False)


def test_file_lock_telemetry_off_snapshot_empty(tmp_path):
  """Default-off lock telemetry must not emit lock wait/hold keys."""
  reset_file_lock_timing(enabled=False)
  target = tmp_path / "data.txt"
  target.write_text("ok")
  with file_write_lock(str(target), timeout_seconds=1):
    pass
  assert snapshot_file_lock_timing() == {}


def test_file_lock_telemetry_uncontended_wait_and_hold_positive(tmp_path):
  """Uncontended acquire records positive EX wait and hold when enabled."""
  reset_file_lock_timing(enabled=True)
  try:
    target = tmp_path / "data.txt"
    target.write_text("ok")
    with file_write_lock(str(target), timeout_seconds=1):
      time.sleep(0.02)
    snap = snapshot_file_lock_timing()
    for key in FILE_LOCK_TELEM_KEYS:
      assert key in snap
    assert snap["file_lock_ex_wait_s"] >= 0.0
    assert snap["file_lock_ex_hold_s"] >= 0.02
    assert snap["file_lock_sh_wait_s"] == 0.0
    assert snap["file_lock_sh_hold_s"] == 0.0
  finally:
    reset_file_lock_timing(enabled=False)


def test_file_lock_telemetry_contended_read_wait_positive(tmp_path):
  """Contended SH acquire records positive wait time when enabled."""
  reset_file_lock_timing(enabled=True)
  target = tmp_path / "data.txt"
  target.write_text("ok")
  held = threading.Event()
  release = threading.Event()

  def _hold_writer() -> None:
    with file_write_lock(str(target), timeout_seconds=2):
      held.set()
      release.wait(timeout=2)

  writer = threading.Thread(target=_hold_writer, daemon=True)
  writer.start()
  assert held.wait(timeout=1)

  try:
    release.set()
    with file_read_lock_wait(str(target), timeout_seconds=2):
      assert target.read_text() == "ok"
    snap = snapshot_file_lock_timing()
    assert snap["file_lock_sh_wait_s"] > 0.0
    assert snap["file_lock_sh_hold_s"] >= 0.0
    assert snap["file_lock_ex_wait_s"] >= 0.0
    assert snap["file_lock_ex_hold_s"] > 0.0
  finally:
    reset_file_lock_timing(enabled=False)
    writer.join(timeout=2)


def test_file_lock_telemetry_exception_still_unlocks(tmp_path):
  """Exception inside the lock body must still release flock and record hold."""
  reset_file_lock_timing(enabled=True)
  target = tmp_path / "data.txt"
  target.write_text("ok")
  lock_path = tmp_path / "data.txt.fnctl.lock"

  with pytest.raises(RuntimeError, match="boom"):
    with file_write_lock(str(target), timeout_seconds=1):
      raise RuntimeError("boom")

  snap = snapshot_file_lock_timing()
  assert snap["file_lock_ex_hold_s"] >= 0.0
  assert not lock_path.exists()
  reset_file_lock_timing(enabled=False)


def test_store_lock_telemetry_off_snapshot_empty(tmp_path):
  """Default-off store lock telemetry must not emit wait/hold keys."""
  reset_store_lock_timing(enabled=False)
  store = SyncTimedbJobStore(str(tmp_path / "archive"))
  assert store.ingest_identities() == []
  assert snapshot_store_lock_timing() == {}


def test_store_lock_telemetry_job_and_members_hold_positive(tmp_path):
  """Enabled store telemetry records job and members hold time."""
  reset_store_lock_timing(enabled=True)
  try:
    job_store = SyncTimedbJobStore(str(tmp_path / "jobs"))
    with job_store._lock:
      time.sleep(0.02)
    members = SyncTimedbArchiveMembersStore(str(tmp_path / "members"))
    with members._lock:
      time.sleep(0.02)
    snap = snapshot_store_lock_timing()
    for key in STORE_LOCK_TELEM_KEYS:
      assert key in snap
    assert snap["job_store_hold_s"] >= 0.02
    assert snap["members_store_hold_s"] >= 0.02
    assert snap["job_store_wait_s"] >= 0.0
    assert snap["members_store_wait_s"] >= 0.0
  finally:
    reset_store_lock_timing(enabled=False)


def test_store_lock_telemetry_contended_wait_positive(tmp_path):
  """Contended job-store acquire records positive wait when enabled."""
  reset_store_lock_timing(enabled=True)
  store = SyncTimedbJobStore(str(tmp_path / "jobs"))
  held = threading.Event()
  release = threading.Event()

  def _hold() -> None:
    with store._lock:
      held.set()
      release.wait(timeout=2)

  owner = threading.Thread(target=_hold, daemon=True)
  owner.start()
  assert held.wait(timeout=1)
  try:
    with store._lock:
      pass
    snap = snapshot_store_lock_timing()
    assert snap["job_store_wait_s"] > 0.0
  finally:
    release.set()
    owner.join(timeout=2)
    reset_store_lock_timing(enabled=False)


def test_parse_stage_campaign_accumulates_from_worker_thread():
  """Process-global parse enable must record holds from a worker thread."""
  from hpcperfstats.dbload.lib.sync_timedb_parsing import (
      _held_parse_stage,
      reset_parse_stage_timing,
      snapshot_parse_stage_campaign_timing,
      snapshot_parse_stage_timing,
  )

  reset_parse_stage_timing(enabled=True)
  try:
    err: list[BaseException] = []

    def _worker() -> None:
      try:
        reset_parse_stage_timing()
        with _held_parse_stage("feed_s"):
          time.sleep(0.02)
      except BaseException as exc:  # noqa: BLE001 — surface in parent
        err.append(exc)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join(timeout=2)
    assert not err
    assert thread.is_alive() is False
    campaign = snapshot_parse_stage_campaign_timing()
    assert campaign.get("feed_s", 0.0) >= 0.01
    # Controller thread ContextVar stays empty; campaign holds worker time.
    assert snapshot_parse_stage_timing().get("feed_s", 0.0) == 0.0
  finally:
    reset_parse_stage_timing(enabled=False)


def test_ingest_write_campaign_accumulates_from_worker_thread():
  """Process-global write enable must record phases from a worker thread."""
  st._reset_ingest_write_timing(enabled=True)
  try:
    err: list[BaseException] = []

    def _worker() -> None:
      try:
        st._reset_ingest_write_timing()
        with st._held_ingest_write_timing():
          with st._held_ingest_write_phase("db_execute_s"):
            time.sleep(0.02)
      except BaseException as exc:  # noqa: BLE001 — surface in parent
        err.append(exc)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join(timeout=2)
    assert not err
    campaign = st._snapshot_ingest_write_campaign_timing()
    assert campaign.get("db_execute_s", 0.0) >= 0.01
    assert campaign.get("postgres_s", 0.0) >= 0.01
  finally:
    st._reset_ingest_write_timing(enabled=False)
