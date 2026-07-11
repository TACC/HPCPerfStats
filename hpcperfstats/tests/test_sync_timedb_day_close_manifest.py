"""Regression tests for DayCloseManifestCoordinator manifest/enqueue shim."""
from __future__ import annotations

import json
import os
import time

import pytest

from hpcperfstats.dbload.lib import sync_timedb_day_close_manifest as async_dc_mod


@pytest.mark.django_db(databases=[])
def test_submit_day_close_does_not_deadlock_on_manifest_touch(tmp_path):
  """enqueue_day_close must not re-acquire ``_lock`` via ``active_or_submitted_tar_paths``."""
  import concurrent.futures

  archive_dir = str(tmp_path / "archive")
  os.makedirs(archive_dir)
  coord = async_dc_mod.DayCloseManifestCoordinator(
      archive_data_dir=archive_dir,
      host_name_ext="",
      tgz_archive_dir=str(tmp_path / "daily"),
      local_tz=None,
      log_fn=lambda *_a, **_k: None,
      get_disqualified_daily_tars=lambda: set(),
      enqueue_day_close_fn=lambda *_a, **_k: True,
  )
  tar_path = str(tmp_path / "daily" / "2020-01-01.tar")
  os.makedirs(os.path.dirname(tar_path), exist_ok=True)
  with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
    fut = pool.submit(
        coord.enqueue_day_close,
        tar_path,
        reason="test",
        disqualified_daily_tars=set(),
    )
    assert fut.result(timeout=2.0) is True


@pytest.mark.django_db(databases=[])
def test_submit_day_close_enqueues_via_janitor_fn(tmp_path):
  archive_dir = str(tmp_path / "archive")
  os.makedirs(archive_dir)
  logs: list[str] = []
  enqueued: list[tuple[str, str]] = []

  def log_fn(msg, **_kwargs):
    logs.append(str(msg))

  def enqueue_fn(tar_norm, reason):
    enqueued.append((tar_norm, reason))
    return True

  coord = async_dc_mod.DayCloseManifestCoordinator(
      archive_data_dir=archive_dir,
      host_name_ext="",
      tgz_archive_dir=str(tmp_path / "daily"),
      local_tz=None,
      log_fn=log_fn,
      get_disqualified_daily_tars=lambda: set(),
      enqueue_day_close_fn=enqueue_fn,
  )
  tar_path = str(tmp_path / "daily" / "2020-01-01.tar")
  os.makedirs(os.path.dirname(tar_path), exist_ok=True)

  assert coord.enqueue_day_close(tar_path, reason="test") is True
  assert enqueued == [(os.path.normpath(tar_path), "test")]
  assert any("day_close enqueue" in line for line in logs)
  manifest = async_dc_mod._load_manifest(coord._manifest_path)
  assert manifest["entries"][os.path.normpath(tar_path)]["status"] == "queued"


@pytest.mark.django_db(databases=[])
def test_submit_day_close_idempotent_when_queued(tmp_path):
  archive_dir = str(tmp_path / "archive")
  os.makedirs(archive_dir)
  enqueue_calls = {"n": 0}

  def enqueue_fn(_tar_norm, _reason):
    enqueue_calls["n"] += 1
    return True

  coord = async_dc_mod.DayCloseManifestCoordinator(
      archive_data_dir=archive_dir,
      host_name_ext="",
      tgz_archive_dir=str(tmp_path / "daily"),
      local_tz=None,
      log_fn=lambda *_a, **_k: None,
      get_disqualified_daily_tars=lambda: set(),
      enqueue_day_close_fn=enqueue_fn,
  )
  tar_path = os.path.normpath(str(tmp_path / "daily" / "2020-01-02.tar"))
  os.makedirs(os.path.dirname(tar_path), exist_ok=True)
  coord.enqueue_day_close(tar_path, reason="first")
  coord.enqueue_day_close(tar_path, reason="second")
  assert enqueue_calls["n"] == 1


@pytest.mark.django_db(databases=[])
def test_submit_day_close_skips_disqualified(tmp_path):
  archive_dir = str(tmp_path / "archive")
  os.makedirs(archive_dir)
  tar_path = os.path.normpath(str(tmp_path / "daily" / "2020-01-03.tar"))
  os.makedirs(os.path.dirname(tar_path), exist_ok=True)

  coord = async_dc_mod.DayCloseManifestCoordinator(
      archive_data_dir=archive_dir,
      host_name_ext="",
      tgz_archive_dir=str(tmp_path / "daily"),
      local_tz=None,
      log_fn=lambda *_a, **_k: None,
      get_disqualified_daily_tars=lambda: {tar_path},
      enqueue_day_close_fn=lambda *_a, **_k: True,
  )
  assert coord.enqueue_day_close(tar_path, reason="test") is False


@pytest.mark.django_db(databases=[])
def test_stale_manifest_recovery_downgrades_raw_delete_pending(tmp_path, monkeypatch):
  archive_dir = str(tmp_path / "archive")
  os.makedirs(archive_dir)
  manifest_path = async_dc_mod.manifest_path(archive_dir)
  tar_norm = os.path.normpath("/tmp/daily/2020-04-15.tar")
  payload = {
      "version": 1,
      "entries": {
          tar_norm: {
              "status": "raw_delete_pending",
              "last_progress": "raw_delete_pending",
              "last_progress_at": time.time() - 10_000,
          },
      },
  }
  with open(manifest_path, "w", encoding="utf-8") as fh:
    json.dump(payload, fh)

  monkeypatch.setattr(async_dc_mod.cfg, "get_sync_day_close_manifest_stale_seconds", lambda: 1.0)
  async_dc_mod.DayCloseManifestCoordinator(
      archive_data_dir=archive_dir,
      host_name_ext="",
      tgz_archive_dir=str(tmp_path / "daily"),
      local_tz=None,
      log_fn=lambda *_a, **_k: None,
      get_disqualified_daily_tars=lambda: set(),
  )
  entry = async_dc_mod._load_manifest(manifest_path)["entries"][tar_norm]
  assert entry["status"] == "deferred"
  assert entry.get("detail") == "legacy_raw_delete_pending"


@pytest.mark.django_db(databases=[])
def test_reconcile_supervisor_raw_delete_pending_is_noop(tmp_path):
  coord = async_dc_mod.DayCloseManifestCoordinator(
      archive_data_dir=str(tmp_path / "archive"),
      host_name_ext="",
      tgz_archive_dir=str(tmp_path / "daily"),
      local_tz=None,
      log_fn=lambda *_a, **_k: None,
      get_disqualified_daily_tars=lambda: set(),
  )
  assert coord.reconcile_supervisor_raw_delete_pending(reason="delete_pass") == 0


@pytest.mark.django_db(databases=[])
def test_tar_paths_raw_delete_pending_empty(tmp_path):
  coord = async_dc_mod.DayCloseManifestCoordinator(
      archive_data_dir=str(tmp_path / "archive"),
      host_name_ext="",
      tgz_archive_dir=str(tmp_path / "daily"),
      local_tz=None,
      log_fn=lambda *_a, **_k: None,
      get_disqualified_daily_tars=lambda: set(),
  )
  assert coord.tar_paths_raw_delete_pending() == []


@pytest.mark.django_db(databases=[])
def test_active_or_submitted_merges_manifest_and_inflight_fn(tmp_path):
  archive_dir = str(tmp_path / "archive")
  os.makedirs(archive_dir)
  tar_a = os.path.normpath("/tmp/daily/2020-01-01.tar")
  tar_b = os.path.normpath("/tmp/daily/2020-01-02.tar")
  inflight = {tar_b}

  coord = async_dc_mod.DayCloseManifestCoordinator(
      archive_data_dir=archive_dir,
      host_name_ext="",
      tgz_archive_dir=str(tmp_path / "daily"),
      local_tz=None,
      log_fn=lambda *_a, **_k: None,
      get_disqualified_daily_tars=lambda: set(),
      get_inflight_tar_paths_fn=lambda: inflight,
  )
  coord._set_entry_status(tar_a, "queued")
  assert coord.active_or_submitted_tar_paths() == {tar_a, tar_b}


@pytest.mark.django_db(databases=[])
def test_finalize_complete_if_filesystem(tmp_path):
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = os.path.normpath(str(daily_dir / "2026-06-01.tar"))
  zst_path = tar_path + ".zst"
  open(zst_path, "wb").close()

  coord = async_dc_mod.DayCloseManifestCoordinator(
      archive_data_dir=str(tmp_path / "archive"),
      host_name_ext="",
      tgz_archive_dir=str(daily_dir),
      local_tz=None,
      log_fn=lambda *_a, **_k: None,
      get_disqualified_daily_tars=lambda: set(),
  )
  coord._set_entry_status(tar_path, "raw_removal")
  assert coord.finalize_complete_if_filesystem(tar_path) is True
  assert coord.is_complete(tar_path) is True


@pytest.mark.django_db(databases=[])
def test_submit_day_close_respects_submit_eligible_fn(tmp_path):
  archive_dir = str(tmp_path / "archive")
  os.makedirs(archive_dir)
  tar_path = os.path.normpath(str(tmp_path / "daily" / "2020-01-04.tar"))
  os.makedirs(os.path.dirname(tar_path), exist_ok=True)
  logs: list[str] = []

  coord = async_dc_mod.DayCloseManifestCoordinator(
      archive_data_dir=archive_dir,
      host_name_ext="",
      tgz_archive_dir=str(tmp_path / "daily"),
      local_tz=None,
      log_fn=lambda msg, **_kw: logs.append(str(msg)),
      get_disqualified_daily_tars=lambda: set(),
      submit_eligible_fn=lambda _t: (False, "not_ready"),
      enqueue_day_close_fn=lambda *_a, **_k: True,
  )
  assert coord.enqueue_day_close(tar_path, reason="test") is False
  assert any("enqueue skip" in line for line in logs)


@pytest.mark.django_db(databases=[])
def test_submit_day_close_no_orphan_queued_on_enqueue_failure(tmp_path):
  archive_dir = str(tmp_path / "archive")
  os.makedirs(archive_dir)
  tar_path = os.path.normpath(str(tmp_path / "daily" / "2020-01-05.tar"))
  os.makedirs(os.path.dirname(tar_path), exist_ok=True)

  coord = async_dc_mod.DayCloseManifestCoordinator(
      archive_data_dir=archive_dir,
      host_name_ext="",
      tgz_archive_dir=str(tmp_path / "daily"),
      local_tz=None,
      log_fn=lambda *_a, **_k: None,
      get_disqualified_daily_tars=lambda: set(),
      enqueue_day_close_fn=lambda *_a, **_k: False,
  )
  assert coord.enqueue_day_close(tar_path, reason="test") is False
  with coord._lock:
    entry = coord._manifest.get("entries", {}).get(tar_path)
  assert entry is None or str(entry.get("status") or "") != "queued"


@pytest.mark.django_db(databases=[])
def test_unified_inflight_cap_counts_manifest_and_debt(tmp_path):
  archive_dir = str(tmp_path / "archive")
  os.makedirs(archive_dir)
  tar_debt = os.path.normpath(str(tmp_path / "daily" / "2020-01-01.tar"))
  tar_manifest = os.path.normpath(str(tmp_path / "daily" / "2020-01-02.tar"))
  os.makedirs(os.path.dirname(tar_debt), exist_ok=True)

  inflight = {tar_debt}

  coord = async_dc_mod.DayCloseManifestCoordinator(
      archive_data_dir=archive_dir,
      host_name_ext="",
      tgz_archive_dir=str(tmp_path / "daily"),
      local_tz=None,
      log_fn=lambda *_a, **_k: None,
      get_disqualified_daily_tars=lambda: set(),
      get_inflight_tar_paths_fn=lambda: inflight,
  )
  coord._set_entry_status(tar_manifest, "queued")
  active = coord.active_or_submitted_tar_paths()
  assert tar_debt in active
  assert tar_manifest in active
  assert len(active) == 2


@pytest.mark.django_db(databases=[])
def test_active_worker_tar_paths_excludes_deferred_waiting_on_ingest(tmp_path):
  archive_dir = str(tmp_path / "archive")
  os.makedirs(archive_dir)
  tar_deferred = os.path.normpath(str(tmp_path / "daily" / "2020-01-01.tar"))
  tar_worker = os.path.normpath(str(tmp_path / "daily" / "2020-01-02.tar"))
  tar_debt_only = os.path.normpath(str(tmp_path / "daily" / "2020-01-03.tar"))
  os.makedirs(os.path.dirname(tar_deferred), exist_ok=True)

  coord = async_dc_mod.DayCloseManifestCoordinator(
      archive_data_dir=archive_dir,
      host_name_ext="",
      tgz_archive_dir=str(tmp_path / "daily"),
      local_tz=None,
      log_fn=lambda *_a, **_k: None,
      get_disqualified_daily_tars=lambda: set(),
      get_inflight_tar_paths_fn=lambda: {tar_deferred, tar_debt_only},
  )
  coord._set_entry_status(tar_deferred, "deferred", detail="waiting_on_ingest")
  coord._set_entry_status(tar_worker, "queued")

  worker_slots = coord.active_worker_tar_paths()
  assert tar_deferred not in worker_slots
  assert tar_worker in worker_slots
  assert tar_debt_only in worker_slots

  breakdown = coord.discover_inflight_breakdown()
  assert breakdown["deferred_waiting_n"] == 1
  assert breakdown["manifest_worker_slot_n"] == 1
  assert breakdown["worker_occupancy_n"] == 2
  assert breakdown["discover_cap_n"] == 1

  discover_cap = coord.active_discover_cap_tar_paths()
  assert tar_deferred not in discover_cap
  assert tar_worker in discover_cap
  assert tar_debt_only not in discover_cap


@pytest.mark.django_db(databases=[])
def test_discover_cap_excludes_debt_heap_from_inflight(tmp_path):
  archive_dir = str(tmp_path / "archive")
  os.makedirs(archive_dir)
  tar_debt = os.path.normpath(str(tmp_path / "daily" / "2020-01-03.tar"))
  tar_live = os.path.normpath(str(tmp_path / "daily" / "2020-01-04.tar"))
  os.makedirs(os.path.dirname(tar_debt), exist_ok=True)

  coord = async_dc_mod.DayCloseManifestCoordinator(
      archive_data_dir=archive_dir,
      host_name_ext="",
      tgz_archive_dir=str(tmp_path / "daily"),
      local_tz=None,
      log_fn=lambda *_a, **_k: None,
      get_disqualified_daily_tars=lambda: set(),
      get_inflight_tar_paths_fn=lambda: {tar_debt},
  )
  discover_cap = coord.active_discover_cap_tar_paths(live_worker_tars={tar_live})
  assert tar_live in discover_cap
  assert tar_debt not in discover_cap


@pytest.mark.django_db(databases=[])
def test_enqueue_day_close_requeues_when_manifest_queued_without_debt(tmp_path):
  archive_dir = str(tmp_path / "archive")
  os.makedirs(archive_dir)
  tar_path = os.path.normpath(str(tmp_path / "daily" / "2020-01-01.tar"))
  os.makedirs(os.path.dirname(tar_path), exist_ok=True)
  enqueued = []

  def _enqueue_fn(tar, reason=""):
    enqueued.append(tar)
    return True

  coord = async_dc_mod.DayCloseManifestCoordinator(
      archive_data_dir=archive_dir,
      host_name_ext="",
      tgz_archive_dir=str(tmp_path / "daily"),
      local_tz=None,
      log_fn=lambda *_a, **_k: None,
      get_disqualified_daily_tars=lambda: set(),
      get_inflight_tar_paths_fn=lambda: set(),
      enqueue_day_close_fn=_enqueue_fn,
  )
  coord._set_entry_status(tar_path, "queued")
  assert coord.enqueue_day_close(tar_path, reason="ghost_test")
  assert enqueued == [tar_path]


@pytest.mark.django_db(databases=[])
def test_reconcile_manifest_ghost_slot_downgrades_or_reenqueues(tmp_path):
  archive_dir = str(tmp_path / "archive")
  os.makedirs(archive_dir)
  tar_path = os.path.normpath(str(tmp_path / "daily" / "2020-01-01.tar"))
  os.makedirs(os.path.dirname(tar_path), exist_ok=True)
  reenqueued = []

  def _enqueue_fn(tar, reason=""):
    reenqueued.append((tar, reason))
    return True

  coord = async_dc_mod.DayCloseManifestCoordinator(
      archive_data_dir=archive_dir,
      host_name_ext="",
      tgz_archive_dir=str(tmp_path / "daily"),
      local_tz=None,
      log_fn=lambda *_a, **_k: None,
      get_disqualified_daily_tars=lambda: set(),
      get_inflight_tar_paths_fn=lambda: set(),
      enqueue_day_close_fn=_enqueue_fn,
  )
  coord._set_entry_status(tar_path, "queued")
  fixed = coord.reconcile_manifest_with_debt_heap(
      debt_tar_paths=set(),
      live_worker_tars=set(),
  )
  assert fixed == 1
  assert reenqueued == [(tar_path, "ghost_manifest_reconcile")]


@pytest.mark.django_db(databases=[])
def test_defer_for_ingest_handoff_creates_entry_when_missing(tmp_path):
  archive_dir = str(tmp_path / "archive")
  os.makedirs(archive_dir)
  tar_path = os.path.normpath(str(tmp_path / "daily" / "2020-01-01.tar"))
  os.makedirs(os.path.dirname(tar_path), exist_ok=True)

  coord = async_dc_mod.DayCloseManifestCoordinator(
      archive_data_dir=archive_dir,
      host_name_ext="",
      tgz_archive_dir=str(tmp_path / "daily"),
      local_tz=None,
      log_fn=lambda *_a, **_k: None,
      get_disqualified_daily_tars=lambda: set(),
  )
  coord.defer_for_ingest_handoff(tar_path)
  snap = coord.entry_progress_snapshot(tar_path)
  assert snap.get("status") == "deferred"


@pytest.mark.django_db(databases=[])
def test_clear_deferred_waiting_on_ingest_removes_entry(tmp_path):
  archive_dir = str(tmp_path / "archive")
  os.makedirs(archive_dir)
  tar_path = os.path.normpath(str(tmp_path / "daily" / "2020-01-01.tar"))
  os.makedirs(os.path.dirname(tar_path), exist_ok=True)

  coord = async_dc_mod.DayCloseManifestCoordinator(
      archive_data_dir=archive_dir,
      host_name_ext="",
      tgz_archive_dir=str(tmp_path / "daily"),
      local_tz=None,
      log_fn=lambda *_a, **_k: None,
      get_disqualified_daily_tars=lambda: set(),
  )
  coord.defer_for_ingest_handoff(tar_path)
  assert coord.clear_deferred_waiting_on_ingest(tar_path) is True
  assert coord.entry_progress_snapshot(tar_path) == {}
  assert coord.clear_deferred_waiting_on_ingest(tar_path) is False
