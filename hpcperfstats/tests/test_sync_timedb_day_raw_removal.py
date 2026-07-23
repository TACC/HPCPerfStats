"""Unit tests for per-day post-seal raw removal coordinator."""

import json
import os
import tarfile
import time
from datetime import datetime
from unittest.mock import MagicMock

import pytest

import hpcperfstats.dbload.lib.conf_parser as cfg
from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
    atomic_seal_tar_to_zst,
    daily_tar_path_from_compressed,
    get_tar_member_name,
    validate_sealed_daily_archive_for_raw_removal,
)
from hpcperfstats.dbload.lib.sync_timedb_day_raw_removal import (
    PHASE_DELETING,
    PHASE_DONE,
    PHASE_VERIFICATION_COMPLETE,
    PHASE_VERIFYING,
    VERIFY_STAGE_POST_SEAL,
    DayRawRemovalCoordinator,
    _save_manifest,
    day_removal_manifest_path,
)


def _make_closed_segment(tmp_path, arch_suffix, day):
  host = tmp_path / ("n." + arch_suffix)
  host.mkdir(parents=True, exist_ok=True)
  ts = int(datetime(day.year, day.month, day.day, 12, 0, 0).timestamp())
  seg = host / str(ts)
  seg.write_text("%d job1 cn001\nline\n" % ts)
  os.utime(seg, (ts, ts))
  return seg


def _seal_day(tmp_path, seg, day):
  tgz_dir = tmp_path / "daily"
  tgz_dir.mkdir(exist_ok=True)
  zst_key = str(tgz_dir / ("%04d-%02d-%02d.tar.zst" % (day.year, day.month, day.day)))
  tar_path = daily_tar_path_from_compressed(zst_key)
  arcname = get_tar_member_name(str(seg))
  with tarfile.open(tar_path, "w") as tf:
    tf.add(str(seg), arcname=arcname)
  atomic_seal_tar_to_zst(
      tar_path,
      zst_key,
      num_threads=1,
      compress_level=6,
      keep_uncompressed_tar=True,
      log_fn=None,
  )
  assert validate_sealed_daily_archive_for_raw_removal(zst_key, log_fn=None)[0]
  return tar_path, zst_key


def _make_coordinator(tmp_path, arch_suffix="cluster.integration.test", **kwargs):
  defaults = {
      "archive_data_dir": str(tmp_path),
      "host_name_ext": arch_suffix,
      "tgz_archive_dir": str(tmp_path / "daily"),
      "log_fn": MagicMock(),
      "get_quarantine_skip_paths": lambda: set(),
      "ingest_ready_fn": lambda _p: True,
  }
  defaults.update(kwargs)
  coord = DayRawRemovalCoordinator(**defaults)
  coord.enabled = True
  return coord


def test_day_raw_removal_verifies_without_deleting(tmp_path, monkeypatch):
  day = datetime(2022, 6, 1)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, _zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path)
  state = coord._get_or_create_day(tar_path)
  state._verify_body()
  assert seg.is_file()
  assert coord.phase(tar_path) == PHASE_VERIFICATION_COMPLETE
  entry = state._manifest["entries"][str(seg)]
  assert entry["status"] == "verified"
  assert not entry.get("deleted")


def test_day_raw_removal_apply_batch_delete_removes_verified_and_tar(
    tmp_path, monkeypatch,
):
  day = datetime(2022, 6, 2)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, _zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path)
  monkeypatch.setattr(cfg, "get_sync_day_close_raw_removal_max_deletes_per_pass", lambda: 0)
  state = coord._get_or_create_day(tar_path)
  state._verify_body()
  coord.begin_deleting(tar_path)
  deleted = coord.apply_batch_delete(tar_path)
  assert deleted == 1
  assert not seg.is_file()
  assert not os.path.isfile(tar_path)
  assert coord.phase(tar_path) == PHASE_DONE


def test_apply_batch_delete_deletes_when_only_in_paths_pending_delete(
    tmp_path, monkeypatch,
):
  """Production skip union includes paths_pending_delete; must not self-block delete."""
  day = datetime(2022, 6, 14)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path)
  monkeypatch.setattr(cfg, "get_sync_day_close_raw_removal_max_deletes_per_pass", lambda: 0)
  state = coord._get_or_create_day(tar_path)
  seg_path = str(seg)
  state._record_entry(seg_path, zst, "verified", "verified")
  with state._lock:
    state._manifest["phase"] = PHASE_DELETING
    state._manifest["verified_count"] = 1
    _save_manifest(state._manifest_path, state._manifest)
  # Mirror production: get_quarantine_skip_paths unions paths_pending_delete().
  state.get_quarantine_skip_paths = lambda: set(coord.paths_pending_delete())
  log_lines = []
  state.log_fn = lambda msg, **kwargs: log_lines.append(msg)
  deleted = state.apply_batch_delete()
  assert deleted == 1
  assert not seg.is_file()
  assert not any("delete defer path=" in line for line in log_lines)


def test_apply_batch_delete_skips_path_in_quarantine_skip_paths(tmp_path, monkeypatch):
  """Verified path in quarantine skip set must not be deleted mid-chunk."""
  day = datetime(2022, 6, 3)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, zst = _seal_day(tmp_path, seg, day)
  skip_path = str(seg)
  log_lines = []
  coord = _make_coordinator(
      tmp_path,
      log_fn=lambda msg, **kwargs: log_lines.append(msg),
  )
  monkeypatch.setattr(cfg, "get_sync_day_close_raw_removal_max_deletes_per_pass", lambda: 0)
  state = coord._get_or_create_day(tar_path)
  state._record_entry(skip_path, zst, "verified", "verified")
  with state._lock:
    state._manifest["phase"] = PHASE_DELETING
    state._manifest["verified_count"] = 1
    _save_manifest(state._manifest_path, state._manifest)
  # Live ingest overlap (handoff) — not paths_pending_delete self-block.
  state.get_ingest_active_skip_paths = lambda: {skip_path}
  deleted = state.apply_batch_delete()
  assert deleted == 0
  assert seg.is_file()
  entry = state._manifest["entries"][skip_path]
  assert entry.get("delete_deferred") == "active_ingest"
  assert any(
      "delete defer" in line and "active_ingest" in line and "skip_class=" in line
      for line in log_lines
  )


def test_apply_batch_delete_deletes_when_not_in_skip_paths(tmp_path, monkeypatch):
  day = datetime(2022, 6, 3)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(
      tmp_path,
      get_quarantine_skip_paths=lambda: {"/nonexistent/active/ingest/path"},
  )
  monkeypatch.setattr(cfg, "get_sync_day_close_raw_removal_max_deletes_per_pass", lambda: 0)
  state = coord._get_or_create_day(tar_path)
  state._record_entry(str(seg), zst, "verified", "verified")
  with state._lock:
    state._manifest["phase"] = PHASE_DELETING
    state._manifest["verified_count"] = 1
    _save_manifest(state._manifest_path, state._manifest)
  deleted = state.apply_batch_delete()
  assert deleted == 1
  assert not seg.is_file()


def test_day_raw_removal_apply_batch_delete_skips_fingerprint_changed(tmp_path, monkeypatch):
  day = datetime(2022, 6, 3)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, _zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path)
  monkeypatch.setattr(cfg, "get_sync_day_close_raw_removal_max_deletes_per_pass", lambda: 0)
  state = coord._get_or_create_day(tar_path)
  state._verify_body()
  coord.begin_deleting(tar_path)
  seg.write_text("mutated content changes size\n")
  deleted = coord.apply_batch_delete(tar_path)
  assert deleted == 0
  assert seg.is_file()
  entry = state._manifest["entries"][str(seg)]
  assert entry["status"] == "skipped_fingerprint_changed"


def test_day_raw_removal_manifest_persists_under_archive_dir(tmp_path):
  day = datetime(2022, 6, 4)
  tar_path = str(tmp_path / "daily" / "2022-06-04.tar")
  (tmp_path / "daily").mkdir(exist_ok=True)
  open(tar_path, "wb").close()
  coord = _make_coordinator(tmp_path)
  state = coord._get_or_create_day(tar_path)
  state._manifest["phase"] = PHASE_VERIFYING
  from hpcperfstats.dbload.lib.sync_timedb_day_raw_removal import _save_manifest

  _save_manifest(state._manifest_path, state._manifest)
  manifest_file = day_removal_manifest_path(str(tmp_path), day.date())
  assert os.path.isfile(manifest_file)
  with open(manifest_file, encoding="utf-8") as handle:
    payload = json.load(handle)
  assert payload["tar_path"] == os.path.normpath(tar_path)


def test_day_raw_removal_start_async_verify_eventually_completes(tmp_path):
  day = datetime(2022, 6, 5)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, _zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path)
  coord.start_async_verify(tar_path)
  state = coord._get_or_create_day(tar_path)
  assert state._pipeline_future is not None
  state._pipeline_future.result(timeout=10.0)
  assert coord.verification_complete(tar_path)
  assert coord.phase(tar_path) == PHASE_VERIFICATION_COMPLETE


def test_start_async_day_pipeline_alias_runs_verify_only(tmp_path, monkeypatch):
  day = datetime(2022, 6, 6)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, _zst = _seal_day(tmp_path, seg, day)
  completed = []
  coord = _make_coordinator(
      tmp_path,
      on_pipeline_complete=lambda tar: completed.append(tar),
  )
  monkeypatch.setattr(cfg, "get_sync_day_close_raw_removal_max_deletes_per_pass", lambda: 0)
  coord.start_async_day_pipeline(tar_path)
  state = coord._get_or_create_day(tar_path)
  state._pipeline_future.result(timeout=10.0)
  assert coord.phase(tar_path) == PHASE_VERIFICATION_COMPLETE
  assert seg.is_file()
  assert completed == []
  coord.begin_deleting(tar_path)
  coord.apply_batch_delete(tar_path)
  assert coord.phase(tar_path) == PHASE_DONE
  assert not seg.is_file()
  assert completed == [os.path.normpath(tar_path)]


def test_days_needing_delete_oldest_first_sorted(tmp_path):
  (tmp_path / "daily").mkdir(exist_ok=True)
  tar_old = os.path.normpath(str(tmp_path / "daily" / "2022-06-01.tar"))
  tar_new = os.path.normpath(str(tmp_path / "daily" / "2022-06-03.tar"))
  open(tar_old, "wb").close()
  open(tar_new, "wb").close()
  coord = _make_coordinator(tmp_path)
  for tar_path in (tar_new, tar_old):
    state = coord._get_or_create_day(tar_path)
    state._manifest["phase"] = PHASE_VERIFICATION_COMPLETE
  assert coord.days_needing_delete_oldest_first() == [tar_old, tar_new]


def test_raw_removal_progress_summary_reports_pending_delete(tmp_path):
  day = datetime(2022, 6, 7)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, _zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path)
  coord.start_async_verify(tar_path)
  state = coord._get_or_create_day(tar_path)
  state._pipeline_future.result(timeout=10.0)
  summary = coord.raw_removal_progress_summary(tar_path)
  assert summary["phase"] == PHASE_VERIFICATION_COMPLETE
  assert summary["verified_count"] >= 1
  assert summary["pending_delete"] >= 1


def test_done_manifest_resets_when_retryable_skips_remain_on_disk(tmp_path):
  day = datetime(2022, 6, 9)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, _zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path, ingest_ready_fn=lambda _p: False)
  state = coord._get_or_create_day(tar_path)
  state._verify_body()
  with state._lock:
    state._manifest["phase"] = PHASE_DONE
    state._manifest["completed_at"] = time.time()
  assert state.delete_phase_done()
  assert state._needs_retry_after_ingest()
  state._reset_for_reverify()
  assert state.phase() == PHASE_VERIFYING
  assert state._manifest.get("verified_count", 0) == 0
  assert seg.is_file()


def test_any_active_raw_removal_work_false_when_only_retryable_skips_remain(tmp_path):
  day = datetime(2022, 6, 12)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, _zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path, ingest_ready_fn=lambda _p: False)
  state = coord._get_or_create_day(tar_path)
  state._verify_body()
  assert coord.any_needs_delete_phase()
  assert not coord.any_active_raw_removal_work()
  assert state.waiting_on_ingest_at_startup()
  assert coord.count_days_waiting_on_ingest() == 1
  state._mark_done_waiting_on_ingest()
  # F15: retryable closed raw still on disk → leave phase; do not mark PHASE_DONE.
  assert coord.phase(tar_path) != PHASE_DONE
  assert coord.any_needs_delete_phase()
  assert not coord.any_active_raw_removal_work()
  assert coord.count_days_waiting_on_ingest() == 1
  assert seg.is_file()


def test_verification_complete_all_verified_deleted_retryable_skips_handoff(
    tmp_path, monkeypatch,
):
  """June-4 shape: verified deletes done; retryable skips on disk need handoff."""
  day = datetime(2026, 6, 4)
  verified_seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  retry_host = tmp_path / "n2.cluster.integration.test"
  retry_host.mkdir(parents=True, exist_ok=True)
  ts = int(datetime(day.year, day.month, day.day, 14, 0, 0).timestamp())
  retry_seg = retry_host / str(ts)
  retry_seg.write_text("%d job2 cn002\nline\n" % ts)
  os.utime(retry_seg, (ts, ts))
  tar_path, zst = _seal_day(tmp_path, verified_seg, day)
  coord = _make_coordinator(tmp_path, ingest_ready_fn=lambda _p: False)
  monkeypatch.setattr(cfg, "get_sync_day_close_raw_removal_max_deletes_per_pass", lambda: 0)
  state = coord._get_or_create_day(tar_path)
  verified_path = str(verified_seg)
  retry_path = str(retry_seg)
  state._record_entry(verified_path, zst, "verified", "verified")
  state._record_entry(retry_path, zst, "skipped_not_in_archive", "not_in_sealed_archive")
  with state._lock:
    state._manifest["phase"] = PHASE_VERIFICATION_COMPLETE
    state._manifest["verify_stage"] = VERIFY_STAGE_POST_SEAL
    state._manifest["verified_count"] = 1
    state._manifest["skipped_count"] = 1
    entries = state._manifest["entries"]
    entries[verified_path]["deleted"] = True
    state._manifest["deleted_count"] = 1
    _save_manifest(state._manifest_path, state._manifest)
  verified_seg.unlink()
  state.get_quarantine_skip_paths = lambda: set(coord.paths_pending_delete())
  coord.begin_deleting(tar_path)
  deleted = coord.apply_batch_delete(tar_path)
  assert deleted == 0
  # F15: retryable raw remains → stay verification_complete/deleting, not PHASE_DONE.
  assert coord.phase(tar_path) != PHASE_DONE
  assert retry_seg.is_file()
  assert coord.should_handoff_to_ingest(tar_path)
  handoff_paths = coord.complete_handoff_to_ingest(tar_path)
  assert retry_path in handoff_paths
  assert coord.phase(tar_path) != PHASE_DONE


def test_mixed_not_in_archive_and_quarantine_marks_done_waiting_on_ingest(
    tmp_path, monkeypatch,
):
  """06-07 C2: mixed retryable + quarantine must not stay verification_complete."""
  day = datetime(2026, 6, 7)
  verified_seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  retry_host = tmp_path / "n2.cluster.integration.test"
  retry_host.mkdir(parents=True, exist_ok=True)
  ts_retry = int(datetime(day.year, day.month, day.day, 14, 0, 0).timestamp())
  retry_seg = retry_host / str(ts_retry)
  retry_seg.write_text("%d job2 cn002\nline\n" % ts_retry)
  os.utime(retry_seg, (ts_retry, ts_retry))
  quar_host = tmp_path / "n3.cluster.integration.test"
  quar_host.mkdir(parents=True, exist_ok=True)
  ts_quar = int(datetime(day.year, day.month, day.day, 15, 0, 0).timestamp())
  quar_seg = quar_host / str(ts_quar)
  quar_seg.write_text("%d job3 cn003\nline\n" % ts_quar)
  os.utime(quar_seg, (ts_quar, ts_quar))
  tar_path, zst = _seal_day(tmp_path, verified_seg, day)
  coord = _make_coordinator(tmp_path, ingest_ready_fn=lambda _p: False)
  monkeypatch.setattr(cfg, "get_sync_day_close_raw_removal_max_deletes_per_pass", lambda: 0)
  state = coord._get_or_create_day(tar_path)
  verified_path = str(verified_seg)
  retry_path = str(retry_seg)
  quar_path = str(quar_seg)
  state._record_entry(verified_path, zst, "verified", "verified")
  state._record_entry(retry_path, zst, "skipped_not_in_archive", "not_in_sealed_archive")
  state._record_entry(quar_path, zst, "skipped_quarantine", "quarantine")
  with state._lock:
    state._manifest["phase"] = PHASE_VERIFICATION_COMPLETE
    state._manifest["verify_stage"] = VERIFY_STAGE_POST_SEAL
    state._manifest["verified_count"] = 1
    state._manifest["skipped_count"] = 2
    entries = state._manifest["entries"]
    entries[verified_path]["deleted"] = True
    state._manifest["deleted_count"] = 1
    _save_manifest(state._manifest_path, state._manifest)
  verified_seg.unlink()
  assert state._only_waiting_on_ingest_blocks_completion()
  coord.begin_deleting(tar_path)
  deleted = coord.apply_batch_delete(tar_path)
  assert deleted == 0
  # F15: retryable raw remains → do not mark PHASE_DONE.
  assert coord.phase(tar_path) != PHASE_DONE
  assert retry_seg.is_file()
  assert quar_seg.is_file()
  assert coord.should_handoff_to_ingest(tar_path)
  handoff_paths = coord.complete_handoff_to_ingest(tar_path)
  assert retry_path in handoff_paths
  assert quar_path not in handoff_paths
  assert coord.phase(tar_path) != PHASE_DONE


def test_mark_done_waiting_on_ingest_skips_phase_done_while_retryable(tmp_path):
  """F15 focused: _mark_done_waiting_on_ingest leaves phase when retryable_count>0."""
  day = datetime(2022, 7, 1)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, _zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path, ingest_ready_fn=lambda _p: False)
  state = coord._get_or_create_day(tar_path)
  state._verify_body()
  phase_before = state.phase()
  state._mark_done_waiting_on_ingest()
  assert state.phase() == phase_before
  assert state.phase() != PHASE_DONE
  assert seg.is_file()


def test_promote_phase_when_verifying_but_post_seal_complete(tmp_path):
  """05-30 stuck gate: phase=verifying + verify_stage=post_seal must promote."""
  day = datetime(2026, 5, 30)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path, ingest_ready_fn=lambda _p: False)
  state = coord._get_or_create_day(tar_path)
  state._record_entry(str(seg), zst, "skipped_not_in_archive", "not_in_sealed_archive")
  with state._lock:
    state._manifest["phase"] = PHASE_VERIFYING
    state._manifest["verify_stage"] = VERIFY_STAGE_POST_SEAL
    state._manifest["skipped_count"] = 1
    _save_manifest(state._manifest_path, state._manifest)
  assert not state.verification_complete()
  assert state.post_seal_verification_complete()
  promoted = coord.promote_phase_if_verify_stage_ahead(tar_path)
  assert promoted is True
  assert coord.verification_complete(tar_path)
  assert coord.phase(tar_path) == PHASE_VERIFICATION_COMPLETE
  assert state._only_waiting_on_ingest_blocks_completion()


def test_reopen_stale_done_clears_verify_stage(tmp_path):
  """Reopen must not leave verify_stage=post_seal with phase=verifying."""
  day = datetime(2026, 5, 31)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path, ingest_ready_fn=lambda _p: False)
  state = coord._get_or_create_day(tar_path)
  state._record_entry(str(seg), zst, "skipped_not_in_archive", "not_in_sealed_archive")
  with state._lock:
    state._manifest["phase"] = PHASE_DONE
    state._manifest["verify_stage"] = VERIFY_STAGE_POST_SEAL
    state._manifest["completed_at"] = time.time()
    state._manifest["verified_count"] = 0
    state._manifest["skipped_count"] = 1
    _save_manifest(state._manifest_path, state._manifest)
  assert state.reopen_stale_done_all_skipped()
  assert state.phase() == PHASE_VERIFYING
  assert state.verify_stage() != VERIFY_STAGE_POST_SEAL


def test_apply_batch_delete_marks_done_when_only_retryable_skips_remain(
    tmp_path, monkeypatch,
):
  day = datetime(2022, 6, 13)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, _zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path, ingest_ready_fn=lambda _p: False)
  monkeypatch.setattr(cfg, "get_sync_day_close_raw_removal_max_deletes_per_pass", lambda: 0)
  state = coord._get_or_create_day(tar_path)
  state._verify_body()
  assert not coord.paths_pending_delete()
  coord.begin_deleting(tar_path)
  deleted = coord.apply_batch_delete(tar_path)
  assert deleted == 0
  assert coord.phase(tar_path) == PHASE_DONE
  assert state._needs_retry_after_ingest()
  assert not coord.any_active_raw_removal_work()
  assert seg.is_file()


def test_skip_stuck_older_day_allows_younger_delete_in_one_pass(tmp_path, monkeypatch):
  day_old = datetime(2022, 6, 10)
  day_young = datetime(2022, 6, 11)
  seg_old = _make_closed_segment(tmp_path, "cluster.integration.test", day_old)
  seg_young = _make_closed_segment(tmp_path, "cluster.integration.test", day_young)
  tar_old, _zst_old = _seal_day(tmp_path, seg_old, day_old)
  tar_young, _zst_young = _seal_day(tmp_path, seg_young, day_young)
  coord = _make_coordinator(tmp_path)
  monkeypatch.setattr(cfg, "get_sync_day_close_raw_removal_max_deletes_per_pass", lambda: 0)
  coord._get_or_create_day(tar_old)._verify_body()
  coord._get_or_create_day(tar_young)._verify_body()
  coord.begin_deleting(tar_old)
  coord.begin_deleting(tar_young)

  original_remove = os.remove

  def _selective_remove(path):
    if os.path.normpath(str(path)) == os.path.normpath(str(seg_old)):
      raise OSError("simulated stuck delete")
    original_remove(path)

  monkeypatch.setattr(os, "remove", _selective_remove)

  pass_log = []
  for tar_path in coord.days_needing_delete_oldest_first():
    deleted = coord.apply_batch_delete(tar_path)
    pass_log.append((tar_path, deleted))
    if (
        deleted == 0
        and coord.needs_delete_phase(tar_path)
        and not coord.delete_phase_done(tar_path)
    ):
      continue

  assert pass_log[0][0] == os.path.normpath(tar_old)
  assert pass_log[0][1] == 0
  assert pass_log[1][0] == os.path.normpath(tar_young)
  assert pass_log[1][1] == 1
  assert coord.delete_phase_done(tar_young)
  assert not coord.delete_phase_done(tar_old)
  assert seg_old.is_file()
  assert not seg_young.is_file()



def test_try_finish_tar_drop_drops_tar_when_raw_gone_and_phase_done(tmp_path):
  day = datetime(2022, 6, 14)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, _zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path, ingest_ready_fn=lambda _p: False)
  state = coord._get_or_create_day(tar_path)
  state._verify_body()
  # F15: with retryable raw on disk, mark does not set PHASE_DONE.
  state._mark_done_waiting_on_ingest()
  assert coord.phase(tar_path) != PHASE_DONE
  assert os.path.isfile(tar_path)
  seg.unlink()
  # Raw gone: force done so tar-drop finish path can run (mark needs handoff paths).
  with state._lock:
    state._manifest["phase"] = PHASE_DONE
    state._manifest["completed_at"] = time.time()
    _save_manifest(state._manifest_path, state._manifest)
  assert coord.phase(tar_path) == PHASE_DONE
  assert coord.any_needs_tar_drop_finish()
  assert tar_path in coord.days_needing_tar_drop_oldest_first()
  assert coord.try_finish_tar_drop_if_ready(tar_path)
  assert not os.path.isfile(tar_path)
  assert coord.phase(tar_path) == PHASE_DONE


def test_apply_batch_delete_drops_tar_when_retryable_manifest_but_raw_gone(
    tmp_path, monkeypatch,
):
  day = datetime(2022, 6, 15)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, _zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path, ingest_ready_fn=lambda _p: False)
  monkeypatch.setattr(cfg, "get_sync_day_close_raw_removal_max_deletes_per_pass", lambda: 0)
  state = coord._get_or_create_day(tar_path)
  state._verify_body()
  seg.unlink()
  coord.begin_deleting(tar_path)
  coord.apply_batch_delete(tar_path)
  assert coord.phase(tar_path) == PHASE_DONE
  assert not os.path.isfile(tar_path)


def test_handoff_paths_when_only_not_in_sealed_archive(tmp_path):
  day = datetime(2022, 6, 20)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path, ingest_ready_fn=lambda _p: False)
  state = coord._get_or_create_day(tar_path)
  state._record_entry(str(seg), zst, "skipped_not_in_archive", "not_in_sealed_archive")
  with state._lock:
    state._manifest["phase"] = PHASE_VERIFICATION_COMPLETE
    state._manifest["skipped_count"] = 1
  _save_manifest(state._manifest_path, state._manifest)
  assert state.should_handoff_day_close_to_ingest()
  assert str(seg) in state.handoff_paths_for_ingest()


def test_complete_handoff_marks_done_and_invokes_callback(tmp_path):
  day = datetime(2022, 6, 21)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, zst = _seal_day(tmp_path, seg, day)
  handoffs = []

  def _on_handoff(tar_norm, paths, reason):
    handoffs.append((tar_norm, list(paths), reason))

  coord = _make_coordinator(
      tmp_path,
      ingest_ready_fn=lambda _p: False,
      on_handoff_to_ingest=_on_handoff,
  )
  state = coord._get_or_create_day(tar_path)
  state._record_entry(str(seg), zst, "skipped_not_in_archive", "not_in_sealed_archive")
  with state._lock:
    state._manifest["phase"] = PHASE_VERIFICATION_COMPLETE
    state._manifest["skipped_count"] = 1
  _save_manifest(state._manifest_path, state._manifest)
  paths = coord.complete_handoff_to_ingest(tar_path, reason="unit_test")
  assert paths == [str(seg)]
  # F15: do not mark PHASE_DONE while retryable closed raw remains on disk.
  assert coord.phase(tar_path) == PHASE_VERIFICATION_COMPLETE
  assert handoffs == [(os.path.normpath(tar_path), [str(seg)], "unit_test")]


def test_discover_manifest_handoffs_reads_persisted_manifest(tmp_path):
  day = datetime(2022, 6, 22)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path, ingest_ready_fn=lambda _p: False)
  state = coord._get_or_create_day(tar_path)
  state._record_entry(str(seg), zst, "skipped_not_in_archive", "not_in_sealed_archive")
  with state._lock:
    state._manifest["phase"] = PHASE_VERIFICATION_COMPLETE
    state._manifest["skipped_count"] = 1
  _save_manifest(state._manifest_path, state._manifest)
  coord2 = _make_coordinator(tmp_path, ingest_ready_fn=lambda _p: False)
  found = coord2.discover_manifest_handoffs()
  assert len(found) == 1
  assert found[0][0] == os.path.normpath(tar_path)
  assert str(seg) in found[0][1]


def test_should_handoff_manifest_fast_when_phase_done(tmp_path, monkeypatch):
  day = datetime(2022, 5, 22)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path, ingest_ready_fn=lambda _p: False)
  state = coord._get_or_create_day(tar_path)
  state._record_entry(
      str(seg),
      zst,
      "skipped_not_in_archive",
      "not_in_sealed_archive",
  )
  with state._lock:
    state._manifest["phase"] = PHASE_DONE
    state._manifest["skipped_count"] = 1
    state._manifest["completed_at"] = time.time()
    _save_manifest(state._manifest_path, state._manifest)

  def _fail_full_scan(*_args, **_kwargs):
    pytest.fail("handoff must not trigger full remaining-raw scan when phase=done")

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_day_raw_removal.build_remaining_raw_for_daily_tar",
      _fail_full_scan,
  )
  assert coord.should_handoff_to_ingest(tar_path)
  assert state.handoff_paths_for_ingest() == [str(seg)]


def test_should_handoff_blocked_when_phase_done_verified_on_disk(tmp_path, monkeypatch):
  day = datetime(2022, 5, 23)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path, ingest_ready_fn=lambda _p: True)
  state = coord._get_or_create_day(tar_path)
  state._record_entry(str(seg), zst, "verified", "verified")
  with state._lock:
    state._manifest["phase"] = PHASE_DONE
    state._manifest["verified_count"] = 1
    state._manifest["completed_at"] = time.time()
    _save_manifest(state._manifest_path, state._manifest)

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_day_raw_removal.build_remaining_raw_for_daily_tar",
      lambda *_a, **_k: pytest.fail("manifest-fast handoff must not need full scan"),
  )
  assert not coord.should_handoff_to_ingest(tar_path)
  assert state.handoff_paths_for_ingest() == []


def test_handoff_paths_manifest_fast_many_entries(tmp_path, monkeypatch):
  day = datetime(2022, 5, 24)
  host = tmp_path / "n.cluster.integration.test"
  host.mkdir(parents=True, exist_ok=True)
  segs = []
  for hour in range(10):
    ts = int(datetime(day.year, day.month, day.day, hour, 0, 0).timestamp())
    seg = host / str(ts)
    seg.write_text("%d job1 cn001\nline\n" % ts)
    os.utime(seg, (ts, ts))
    segs.append(seg)
  tar_path, zst = _seal_day(tmp_path, segs[0], day)
  coord = _make_coordinator(tmp_path, ingest_ready_fn=lambda _p: False)
  state = coord._get_or_create_day(tar_path)
  for seg in segs:
    state._record_entry(
        str(seg),
        zst,
        "skipped_not_in_archive",
        "not_in_sealed_archive",
    )
  with state._lock:
    state._manifest["phase"] = PHASE_DONE
    state._manifest["skipped_count"] = len(segs)
    state._manifest["completed_at"] = time.time()
    _save_manifest(state._manifest_path, state._manifest)

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_day_raw_removal.build_remaining_raw_for_daily_tar",
      lambda *_a, **_k: pytest.fail("manifest-fast handoff must not need full scan"),
  )
  paths = state.handoff_paths_for_ingest()
  assert len(paths) == len(segs)
  assert set(paths) == {str(seg) for seg in segs}


def test_build_remaining_uses_snapshot_when_wired(tmp_path, monkeypatch):
  from types import SimpleNamespace

  day = datetime(2022, 5, 25)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, zst = _seal_day(tmp_path, seg, day)
  snapshot = SimpleNamespace(
      remaining_raw_by_gz={zst: [str(seg)]},
  )
  captured = {}

  def _capture_build(*args, **kwargs):
    captured["maintenance_snapshot"] = kwargs.get("maintenance_snapshot")
    return {zst: [str(seg)]}

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_day_raw_removal.build_remaining_raw_for_daily_tar",
      _capture_build,
  )
  coord = _make_coordinator(
      tmp_path,
      get_maintenance_snapshot=lambda: snapshot,
  )
  state = coord._get_or_create_day(tar_path)
  closed = state._closed_raw_paths_on_disk()
  assert closed == [str(seg)]
  assert captured["maintenance_snapshot"] is snapshot


def test_verify_uses_provided_sealed_members_without_validation_scan(
    tmp_path, monkeypatch,
):
  day = datetime(2022, 6, 22)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path, ingest_ready_fn=lambda _p: True)
  validate_calls = []

  def _fail_validate(*_a, **_k):
    validate_calls.append(True)
    raise AssertionError("validate_sealed_daily_archive_for_raw_removal must not run")

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_helpers."
      "validate_sealed_daily_archive_for_raw_removal",
      _fail_validate,
  )
  arcname = get_tar_member_name(str(seg))
  sealed_members = {arcname: seg.stat().st_size}
  coord.start_async_verify(tar_path, sealed_members=sealed_members)
  state = coord._get_or_create_day(tar_path)
  state._verify_body()
  assert validate_calls == []
  assert coord.verification_complete(tar_path)


def test_run_supervisor_delete_pass_tar_drop_before_chunk_wait():
  """Tar-drop must run even when batch delete waits on chunk_in_progress."""
  from hpcperfstats.dbload.lib.sync_timedb_day_raw_removal import (
      run_supervisor_day_raw_removal_delete_pass,
  )

  delete_tar = "/tmp/daily/2025-12-03.tar"
  tar_drop_tar = "/tmp/daily/2025-12-28.tar"
  tar_drop_calls = []
  delete_calls = []
  chunk_wait_logs = []

  class _FakeAsync:
    def reconcile_supervisor_raw_delete_pending(self, reason=""):
      del reason

    def tar_paths_raw_delete_pending(self):
      return []

  class _FakeDayRaw:
    enabled = True

    def any_needs_delete_phase(self):
      return True

    def any_needs_tar_drop_finish(self):
      return True

    def days_needing_tar_drop_oldest_first(self):
      return [tar_drop_tar]

    def oldest_day_needing_delete(self):
      return delete_tar

    def days_needing_delete_oldest_first(self):
      return [delete_tar]

    def try_finish_tar_drop_if_ready(self, tar_norm):
      tar_drop_calls.append(tar_norm)
      return False

    def phase(self, tar_norm):
      del tar_norm
      return PHASE_VERIFICATION_COMPLETE

    def begin_deleting(self, tar_norm):
      del tar_norm

    def apply_batch_delete(self, tar_norm):
      delete_calls.append(tar_norm)
      return 0

    def delete_phase_done(self, tar_norm):
      del tar_norm
      return False

    def needs_delete_phase(self, tar_norm):
      del tar_norm
      return True

  spin = run_supervisor_day_raw_removal_delete_pass(
      _FakeDayRaw(),
      _FakeAsync(),
      chunk_in_progress=True,
      finalize_day_close_delete=lambda _t: None,
      sleep_fn=lambda _s: None,
      log_chunk_wait=lambda blocking_tar, n: chunk_wait_logs.append(
          (blocking_tar, n),
      ),
  )
  assert spin is True
  assert tar_drop_calls == [tar_drop_tar]
  assert delete_calls == []
  assert chunk_wait_logs == [(delete_tar, 1)]


def test_discover_closed_raw_handoffs_phase_done_verified(tmp_path):
  """phase=done + verified on disk → narrow discover (no handoff paths)."""
  day = datetime(2026, 5, 22)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, zst = _seal_day(tmp_path, seg, day)
  handoffs = []

  def _on_handoff(tar_norm, paths, reason):
    handoffs.append((tar_norm, list(paths), reason))

  coord = _make_coordinator(
      tmp_path,
      ingest_ready_fn=lambda _p: True,
      on_handoff_to_ingest=_on_handoff,
  )
  state = coord._get_or_create_day(tar_path)
  state._record_entry(str(seg), zst, "verified", "verified")
  with state._lock:
    state._manifest["phase"] = PHASE_DONE
    state._manifest["verified_count"] = 1
    state._manifest["completed_at"] = time.time()
    _save_manifest(state._manifest_path, state._manifest)

  coord2 = _make_coordinator(
      tmp_path,
      ingest_ready_fn=lambda _p: True,
      on_handoff_to_ingest=_on_handoff,
  )
  found = coord2.discover_closed_raw_on_disk_handoffs()
  assert len(found) == 1
  assert found[0][0] == os.path.normpath(tar_path)
  assert found[0][1] == []
  assert not coord2.should_handoff_to_ingest(tar_path)

  kick_actions = []
  original_kick = coord2.kick_closed_raw_unblock

  def _track_kick(tar_path, *, reason):
    action = original_kick(tar_path, reason=reason)
    kick_actions.append(action)
    return action

  coord2.kick_closed_raw_unblock = _track_kick
  requeued = coord2.requeue_closed_raw_paths_for_ingest(
      tar_path,
      reason="unit_closed_raw",
  )
  assert requeued == []
  assert handoffs == []
  assert kick_actions == ["delete_reopen"]
  assert coord2.phase(tar_path) == PHASE_DELETING


def test_reopen_delete_phase_from_done_verified_on_disk(tmp_path):
  day = datetime(2026, 5, 22)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, _zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path)
  state = coord._get_or_create_day(tar_path)
  state._record_entry(str(seg), _zst, "verified", "verified")
  with state._lock:
    state._manifest["phase"] = PHASE_DONE
    state._manifest["verified_count"] = 1
    _save_manifest(state._manifest_path, state._manifest)

  assert state.reopen_delete_phase_if_verified_on_disk()
  assert state.phase() == PHASE_DELETING


def test_verify_kick_noop_regression_phase_done_uses_delete_reopen(tmp_path):
  day = datetime(2026, 5, 22)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path)
  state = coord._get_or_create_day(tar_path)
  state._record_entry(str(seg), zst, "verified", "verified")
  with state._lock:
    state._manifest["phase"] = PHASE_DONE
    state._manifest["verified_count"] = 1
    _save_manifest(state._manifest_path, state._manifest)

  verify_calls = []
  original_verify = coord.start_async_verify

  def _track_verify(tar_path, **kwargs):
    verify_calls.append(tar_path)
    return original_verify(tar_path, **kwargs)

  coord.start_async_verify = _track_verify
  assert coord.kick_closed_raw_unblock(tar_path, reason="unit") == "delete_reopen"
  assert verify_calls == []
  assert coord.phase(tar_path) == PHASE_DELETING


def test_closed_raw_handoff_manifest_fast_phase_done_many_retryable_skip(
    tmp_path, monkeypatch,
):
  day = datetime(2022, 5, 22)
  host = tmp_path / "n.cluster.integration.test"
  host.mkdir(parents=True, exist_ok=True)
  segs = []
  for hour in range(10):
    ts = int(datetime(day.year, day.month, day.day, hour, 0, 0).timestamp())
    seg = host / str(ts)
    seg.write_text("%d job1 cn001\nline\n" % ts)
    os.utime(seg, (ts, ts))
    segs.append(seg)
  tar_path, zst = _seal_day(tmp_path, segs[0], day)
  coord = _make_coordinator(tmp_path, ingest_ready_fn=lambda _p: False)
  state = coord._get_or_create_day(tar_path)
  for seg in segs:
    state._record_entry(
        str(seg),
        zst,
        "skipped_not_in_archive",
        "not_in_sealed_archive",
    )
  with state._lock:
    state._manifest["phase"] = PHASE_DONE
    state._manifest["skipped_count"] = len(segs)
    state._manifest["completed_at"] = time.time()
    _save_manifest(state._manifest_path, state._manifest)

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_day_raw_removal.build_remaining_raw_for_daily_tar",
      lambda *_a, **_k: pytest.fail("manifest-fast closed raw must not full-scan when phase=done"),
  )
  coord2 = _make_coordinator(tmp_path, ingest_ready_fn=lambda _p: False)
  found = coord2.discover_closed_raw_on_disk_handoffs()
  assert len(found) == 1
  assert found[0][0] == os.path.normpath(tar_path)
  assert set(found[0][1]) == {str(seg) for seg in segs}
  assert coord2.closed_raw_paths_on_disk(tar_path) == [str(seg) for seg in segs]
  assert coord2.has_closed_raw_on_disk(tar_path)


def test_reclassify_retryable_skip_upgrades_to_verified_when_tar_member(
    tmp_path,
):
  """Reproduce hpcperfstats03 shape: phase=done + skipped_not_in_archive on disk in tar."""
  day = datetime(2026, 5, 30)
  host = tmp_path / "n.cluster.integration.test"
  host.mkdir(parents=True, exist_ok=True)
  segs = []
  for hour in (10, 11):
    ts = int(datetime(day.year, day.month, day.day, hour, 0, 0).timestamp())
    seg = host / str(ts)
    seg.write_text("%d job1 cn001\nline\n" % ts)
    os.utime(seg, (ts, ts))
    segs.append(seg)
  tar_path, zst = _seal_day(tmp_path, segs[0], day)
  with tarfile.open(tar_path, "a") as tf:
    tf.add(str(segs[1]), arcname=get_tar_member_name(str(segs[1])))
  coord = _make_coordinator(tmp_path, ingest_ready_fn=lambda _p: True)
  state = coord._get_or_create_day(tar_path)
  state._record_entry(str(segs[0]), zst, "verified", "verified")
  with state._lock:
    state._manifest["entries"][str(segs[0])]["deleted"] = True
    state._manifest["verified_count"] = 1
  state._record_entry(
      str(segs[1]),
      zst,
      "skipped_not_in_archive",
      "not_in_sealed_archive",
  )
  with state._lock:
    state._manifest["phase"] = PHASE_DONE
    state._manifest["verify_stage"] = VERIFY_STAGE_POST_SEAL
    state._manifest["completed_at"] = time.time()
    _save_manifest(state._manifest_path, state._manifest)

  upgraded = coord.reclassify_retryable_skips_after_handoff_sync(tar_path)
  assert upgraded == 1
  entry = state._manifest["entries"][str(segs[1])]
  assert entry["status"] == "verified"
  assert state._manifest["verified_count"] == 2
  assert state._manifest["skipped_count"] == 0


def test_record_entry_skip_to_verified_adjusts_counts(tmp_path):
  day = datetime(2026, 5, 30)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path)
  state = coord._get_or_create_day(tar_path)
  state._record_entry(str(seg), zst, "skipped_not_in_archive", "not_in_sealed_archive")
  assert state._manifest["skipped_count"] == 1
  assert state._manifest["verified_count"] == 0
  state._record_entry(str(seg), zst, "verified", "verified")
  assert state._manifest["skipped_count"] == 0
  assert state._manifest["verified_count"] == 1


def test_reclassify_then_batch_delete_clears_blockers(tmp_path, monkeypatch):
  day = datetime(2026, 5, 30)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path, ingest_ready_fn=lambda _p: True)
  monkeypatch.setattr(cfg, "get_sync_day_close_raw_removal_max_deletes_per_pass", lambda: 0)
  state = coord._get_or_create_day(tar_path)
  state._record_entry(str(seg), zst, "skipped_not_in_archive", "not_in_sealed_archive")
  with state._lock:
    state._manifest["phase"] = PHASE_DONE
    state._manifest["verify_stage"] = VERIFY_STAGE_POST_SEAL
    state._manifest["completed_at"] = time.time()
    _save_manifest(state._manifest_path, state._manifest)

  assert coord.reclassify_retryable_skips_after_handoff_sync(tar_path) == 1
  coord.begin_deleting(tar_path)
  deleted = coord.apply_batch_delete(tar_path)
  assert deleted == 1
  assert not seg.is_file()
  assert coord.delete_phase_done(tar_path)


def test_kick_closed_raw_reclassify_opens_delete(tmp_path):
  day = datetime(2026, 5, 30)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path, ingest_ready_fn=lambda _p: True)
  state = coord._get_or_create_day(tar_path)
  state._record_entry(str(seg), zst, "skipped_not_in_archive", "not_in_sealed_archive")
  with state._lock:
    state._manifest["phase"] = PHASE_DONE
    state._manifest["verify_stage"] = VERIFY_STAGE_POST_SEAL
    state._manifest["completed_at"] = time.time()
    _save_manifest(state._manifest_path, state._manifest)

  assert coord.kick_closed_raw_unblock(tar_path, reason="unit") == "delete_reopen"
  assert coord.phase(tar_path) == PHASE_DELETING
  assert state._manifest["entries"][str(seg)]["status"] == "verified"


def test_has_active_raw_removal_work_false_when_only_verified_pending_delete(tmp_path):
  day = datetime(2025, 12, 3)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, _zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path)
  state = coord._get_or_create_day(tar_path)
  state._verify_body()
  assert coord.paths_pending_delete()
  assert not coord.any_active_raw_removal_work()
  assert state.has_active_raw_removal_work() is False


def test_batch_delete_runs_during_chunk_when_calendar_disjoint():
  from hpcperfstats.dbload.lib.sync_timedb_day_raw_removal import (
      run_supervisor_day_raw_removal_delete_pass,
  )

  delete_tar = "/tmp/daily/2025-12-03.tar"
  delete_calls = []
  chunk_wait_logs = []

  class _FakeAsync:
    def reconcile_supervisor_raw_delete_pending(self, reason=""):
      del reason

    def tar_paths_raw_delete_pending(self):
      return []

  class _FakeDayRaw:
    enabled = True

    def any_needs_delete_phase(self):
      return True

    def any_needs_tar_drop_finish(self):
      return False

    def days_needing_tar_drop_oldest_first(self):
      return []

    def oldest_day_needing_delete(self):
      return delete_tar

    def days_needing_delete_oldest_first(self):
      return [delete_tar]

    def try_finish_tar_drop_if_ready(self, tar_norm):
      del tar_norm
      return False

    def phase(self, tar_norm):
      del tar_norm
      return PHASE_VERIFICATION_COMPLETE

    def begin_deleting(self, tar_norm):
      del tar_norm

    def apply_batch_delete(self, tar_norm):
      delete_calls.append(tar_norm)
      return 1

    def delete_phase_done(self, tar_norm):
      del tar_norm
      return False

    def needs_delete_phase(self, tar_norm):
      del tar_norm
      return True

  spin = run_supervisor_day_raw_removal_delete_pass(
      _FakeDayRaw(),
      _FakeAsync(),
      chunk_in_progress=True,
      chunk_calendar_day_hint="2026-05-26",
      finalize_day_close_delete=lambda _t: None,
      sleep_fn=lambda _s: None,
      log_chunk_wait=lambda blocking_tar, n: chunk_wait_logs.append(
          (blocking_tar, n),
      ),
  )
  assert spin is True
  assert delete_calls == [delete_tar]
  assert chunk_wait_logs == []


def test_ghost_deleted_manifest_path_on_disk_triggers_delete_retry(
    tmp_path, monkeypatch,
):
  day = datetime(2026, 5, 26)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path)
  state = coord._get_or_create_day(tar_path)
  state._verify_body()
  seg_str = str(seg)
  with state._lock:
    entry = state._manifest["entries"][seg_str]
    entry["deleted"] = True
    entry["status"] = "verified"
    state._manifest["phase"] = PHASE_DONE
    state._manifest["completed_at"] = time.time()
    _save_manifest(state._manifest_path, state._manifest)

  assert state.delete_phase_done()
  assert state._ghost_deleted_paths_on_disk() == [seg_str]
  assert not state._has_closed_raw_existing_on_disk()
  assert state.needs_ghost_delete_retry()

  deleted = state.apply_batch_delete()
  assert deleted == 1
  assert not os.path.isfile(seg_str)
  assert not state._ghost_deleted_paths_on_disk()


def test_kick_closed_raw_unblock_no_deadlock_retryable_only(tmp_path):
  """begin_deleting must not re-enter _lock (regression: kick/requeue hung ~60s+)."""
  retry_day = datetime(2026, 5, 23)
  retry_seg = _make_closed_segment(tmp_path, "cluster.integration.test", retry_day)
  retry_tar_path, retry_zst = _seal_day(tmp_path, retry_seg, retry_day)
  coord = _make_coordinator(
      tmp_path,
      log_fn=lambda *_a, **_k: None,
      ingest_ready_fn=lambda _p: False,
  )
  state = coord._get_or_create_day(retry_tar_path)
  state._record_entry(
      str(retry_seg),
      retry_zst,
      "skipped_not_in_archive",
      "not_in_sealed_archive",
  )
  with state._lock:
    state._manifest["phase"] = PHASE_DONE
    _save_manifest(state._manifest_path, state._manifest)

  start = time.time()
  assert coord.kick_closed_raw_unblock(retry_tar_path, reason="unit") == "noop"
  assert time.time() - start < 0.5
  assert coord.phase(retry_tar_path) == PHASE_DONE


def test_requeue_closed_raw_skips_quarantine_and_manifested(tmp_path):
  handoffs = []
  quarantine_root = tmp_path / ".sync_timedb_unparsable_raw"
  quarantine_root.mkdir(parents=True, exist_ok=True)
  quarantine_path = quarantine_root / "host" / "9999999999"
  quarantine_path.parent.mkdir(parents=True, exist_ok=True)
  quarantine_path.write_text("bad\n")

  retry_day = datetime(2026, 5, 23)
  retry_seg = _make_closed_segment(tmp_path, "cluster.integration.test", retry_day)
  retry_tar_path, retry_zst = _seal_day(tmp_path, retry_seg, retry_day)
  coord = _make_coordinator(
      tmp_path,
      on_handoff_to_ingest=lambda tar_norm, paths, reason: handoffs.append(
          (tar_norm, list(paths), reason),
      ),
      get_quarantine_skip_paths=lambda: {str(quarantine_path)},
      log_fn=lambda *_a, **_k: None,
  )
  state = coord._get_or_create_day(retry_tar_path)
  state._record_entry(
      str(retry_seg),
      retry_zst,
      "skipped_not_in_archive",
      "not_in_sealed_archive",
  )
  with state._lock:
    state._manifest["phase"] = PHASE_DONE
    state._manifest["skipped_count"] = 1
    state._manifest["completed_at"] = time.time()
    _save_manifest(state._manifest_path, state._manifest)

  paths = coord.paths_for_closed_raw_handoff_requeue(retry_tar_path)
  assert str(retry_seg) in paths
  assert str(quarantine_path) not in paths

  requeued = coord.requeue_closed_raw_paths_for_ingest(
      retry_tar_path,
      reason="janitor_closed_raw_submit_guard",
  )
  assert str(retry_seg) in requeued
  assert str(quarantine_path) not in requeued
  assert handoffs == [
      (
          os.path.normpath(retry_tar_path),
          [str(retry_seg)],
          "janitor_closed_raw_submit_guard",
      ),
  ]


def test_discover_closed_raw_lazy_skips_remaining_raw_for_done_days(
    tmp_path, monkeypatch,
):
  """Lazy discover must not call build_remaining_raw_for phase=done manifest days."""
  day = datetime(2026, 5, 22)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path)
  state = coord._get_or_create_day(tar_path)
  state._record_entry(str(seg), zst, "verified", "verified")
  with state._lock:
    state._manifest["phase"] = PHASE_DONE
    state._manifest["verified_count"] = 1
    _save_manifest(state._manifest_path, state._manifest)

  build_calls = {"n": 0}

  def _count_build(*_args, **_kwargs):
    build_calls["n"] += 1
    return {}

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_day_raw_removal.build_remaining_raw_for_daily_tar",
      _count_build,
  )
  found = coord.discover_closed_raw_on_disk_handoffs()
  assert len(found) == 1
  assert build_calls["n"] == 0


def test_discover_closed_raw_no_full_tree_scan(tmp_path):
  """Boot discover uses manifest-first predicates, not has_closed_raw_on_disk."""
  day = datetime(2026, 5, 22)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path)
  state = coord._get_or_create_day(tar_path)
  state._record_entry(str(seg), zst, "verified", "verified")
  with state._lock:
    state._manifest["phase"] = PHASE_DONE
    state._manifest["verified_count"] = 1
    _save_manifest(state._manifest_path, state._manifest)

  def _forbidden_full_tree(_tar_path):
    raise AssertionError("has_closed_raw_on_disk must not run during discover")

  coord.has_closed_raw_on_disk = _forbidden_full_tree
  found = coord.discover_closed_raw_on_disk_handoffs()
  assert len(found) == 1
  assert found[0][0] == os.path.normpath(tar_path)


def test_requeue_handoff_before_kick(tmp_path):
  """Handoff paths present → kick must not run before handoff callback."""
  retry_day = datetime(2026, 5, 23)
  retry_seg = _make_closed_segment(tmp_path, "cluster.integration.test", retry_day)
  retry_tar_path, retry_zst = _seal_day(tmp_path, retry_seg, retry_day)
  handoffs = []
  kick_calls = []

  def _on_handoff(tar_norm, paths, reason):
    handoffs.append((tar_norm, list(paths), reason))

  coord = _make_coordinator(
      tmp_path,
      on_handoff_to_ingest=_on_handoff,
      log_fn=lambda *_a, **_k: None,
  )
  state = coord._get_or_create_day(retry_tar_path)
  state._record_entry(
      str(retry_seg),
      retry_zst,
      "skipped_not_in_archive",
      "not_in_sealed_archive",
  )
  with state._lock:
    state._manifest["phase"] = PHASE_DONE
    _save_manifest(state._manifest_path, state._manifest)

  original_kick = coord.kick_closed_raw_unblock

  def _track_kick(tar_path, *, reason):
    kick_calls.append((tar_path, reason))
    return original_kick(tar_path, reason=reason)

  coord.kick_closed_raw_unblock = _track_kick
  requeued = coord.requeue_closed_raw_paths_for_ingest(
      retry_tar_path,
      reason="unit_handoff_first",
  )
  assert str(retry_seg) in requeued
  assert handoffs
  assert kick_calls == []


def test_kick_delete_reopen_at_verification_complete(tmp_path):
  """VERIFICATION_COMPLETE with verified pending delete → begin_deleting kick."""
  day = datetime(2026, 5, 22)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path, log_fn=lambda *_a, **_k: None)
  state = coord._get_or_create_day(tar_path)
  state._record_entry(str(seg), zst, "verified", "verified")
  with state._lock:
    state._manifest["phase"] = PHASE_VERIFICATION_COMPLETE
    state._manifest["verified_count"] = 1
    _save_manifest(state._manifest_path, state._manifest)

  assert coord.kick_closed_raw_unblock(tar_path, reason="unit") == "delete_reopen"
  assert coord.phase(tar_path) == PHASE_DELETING


def test_apply_batch_delete_completion_single_scan(tmp_path, monkeypatch):
  """Completion path uses one _batch_delete_completion_context call per batch."""
  day = datetime(2026, 5, 22)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path, log_fn=lambda *_a, **_k: None)
  state = coord._get_or_create_day(tar_path)
  state._record_entry(str(seg), zst, "verified", "verified")
  with state._lock:
    state._manifest["phase"] = PHASE_DELETING
    state._manifest["verified_count"] = 1
    _save_manifest(state._manifest_path, state._manifest)

  context_calls = {"n": 0}
  original_context = state._batch_delete_completion_context

  def _count_context(entries):
    context_calls["n"] += 1
    return original_context(entries)

  monkeypatch.setattr(state, "_batch_delete_completion_context", _count_context)
  deleted = state.apply_batch_delete()
  assert deleted == 1
  assert context_calls["n"] == 1


def test_quarantine_only_manifest_not_blocking(tmp_path):
  """1578-style skipped_quarantine entries must not block DAY_CLOSE."""
  day = datetime(2022, 5, 22)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, _zst = _seal_day(tmp_path, seg, day)
  manifest_path = day_removal_manifest_path(str(tmp_path), day.date())
  fp = {"mtime": 0, "size": int(seg.stat().st_size)}
  payload = {
      "version": 1,
      "tar_path": os.path.normpath(tar_path),
      "phase": PHASE_DELETING,
      "started_at": time.time(),
      "completed_at": None,
      "verified_count": 0,
      "skipped_count": 1,
      "deleted_count": 0,
      "entries": {
          str(seg): {
              "status": "skipped_quarantine",
              "reason": "quarantine",
              **fp,
          },
      },
  }
  _save_manifest(manifest_path, payload)
  coord = _make_coordinator(tmp_path)
  assert not coord.has_closed_raw_on_disk(tar_path)
  assert coord.phase(tar_path) == PHASE_DONE
  assert coord.kick_closed_raw_unblock(tar_path, reason="test") == "quarantine_terminal"


def test_has_closed_raw_false_when_manifest_done_no_on_disk(tmp_path, monkeypatch):
  """05-26: phase=done with no manifest blockers ignores stale remaining_raw."""
  day = datetime(2022, 5, 26)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, _zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path)
  state = coord._get_or_create_day(tar_path)
  with state._lock:
    state._manifest["phase"] = PHASE_DONE
    state._manifest["completed_at"] = time.time()
    state._manifest["entries"] = {}
    _save_manifest(state._manifest_path, state._manifest)
  monkeypatch.setattr(
      state,
      "_build_remaining_raw_for_daily_tar",
      lambda: {str(_zst): [str(seg)]},
  )
  assert not coord.has_closed_raw_on_disk(tar_path)


def test_try_finish_tar_drop_quarantine_only_on_disk(tmp_path, monkeypatch):
  """05-22: quarantine-terminal manifest entries must not block tar unlink."""
  day = datetime(2022, 5, 22)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, zst = _seal_day(tmp_path, seg, day)
  manifest_path = day_removal_manifest_path(str(tmp_path), day.date())
  fp = {"mtime": 0, "size": int(seg.stat().st_size)}
  payload = {
      "version": 1,
      "tar_path": os.path.normpath(tar_path),
      "phase": PHASE_DONE,
      "started_at": time.time(),
      "completed_at": time.time(),
      "verified_count": 0,
      "skipped_count": 1,
      "deleted_count": 0,
      "entries": {
          str(seg): {
              "status": "skipped_quarantine",
              "reason": "quarantine",
              **fp,
          },
      },
  }
  _save_manifest(manifest_path, payload)
  coord = _make_coordinator(tmp_path)
  state = coord._get_or_create_day(tar_path)
  monkeypatch.setattr(
      state,
      "_build_remaining_raw_for_daily_tar",
      lambda: {str(zst): [str(seg)]},
  )
  assert os.path.isfile(tar_path)
  assert seg.is_file()
  assert coord.try_finish_tar_drop_if_ready(tar_path)
  assert not os.path.isfile(tar_path)
  assert seg.is_file()
  assert os.path.isfile(zst)


def test_try_finish_tar_drop_manifest_done_stale_accrual(tmp_path, monkeypatch):
  """05-26: manifest phase=done with clean entries ignores stale accrual."""
  day = datetime(2022, 5, 26)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path)
  state = coord._get_or_create_day(tar_path)
  with state._lock:
    state._manifest["phase"] = PHASE_DONE
    state._manifest["completed_at"] = time.time()
    state._manifest["entries"] = {}
    _save_manifest(state._manifest_path, state._manifest)
  seg.unlink()
  monkeypatch.setattr(
      state,
      "_build_remaining_raw_for_daily_tar",
      lambda: {str(zst): [str(seg)]},
  )
  assert os.path.isfile(tar_path)
  assert coord.try_finish_tar_drop_if_ready(tar_path)
  assert not os.path.isfile(tar_path)


def test_days_needing_delete_includes_done_with_verified_on_disk_after_reopen(
    tmp_path,
):
  day = datetime(2026, 5, 24)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, _zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path)
  state = coord._get_or_create_day(tar_path)
  state._record_entry(str(seg), _zst, "verified", "verified")
  with state._lock:
    state._manifest["phase"] = PHASE_DONE
    state._manifest["verified_count"] = 1
    _save_manifest(state._manifest_path, state._manifest)

  assert coord.days_needing_delete_oldest_first() == [tar_path]
  assert coord.reopen_done_days_with_verified_on_disk() == 1
  assert coord.phase(tar_path) == PHASE_DELETING
  assert coord.days_needing_delete_oldest_first() == [tar_path]


def test_any_needs_delete_phase_skips_isfile_when_no_ghost_markers(
    tmp_path, monkeypatch,
):
  day = datetime(2026, 5, 24)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path)
  state = coord._get_or_create_day(tar_path)
  state._record_entry(str(seg), zst, "verified", "verified")
  with state._lock:
    state._manifest["phase"] = PHASE_DONE
    state._manifest["verified_count"] = 1
    _save_manifest(state._manifest_path, state._manifest)

  isfile_calls = []

  def _track_isfile(path):
    isfile_calls.append(path)
    return os.path.isfile(path)

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_day_raw_removal.os.path.isfile",
      _track_isfile,
  )
  assert coord.any_needs_delete_phase()
  assert isfile_calls == []


def test_reopen_done_days_with_verified_on_disk_at_delete_pass_start(tmp_path):
  from hpcperfstats.dbload.lib.sync_timedb_day_raw_removal import (
      run_supervisor_day_raw_removal_delete_pass,
  )

  day = datetime(2026, 5, 24)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, _zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path)
  state = coord._get_or_create_day(tar_path)
  state._record_entry(str(seg), _zst, "verified", "verified")
  with state._lock:
    state._manifest["phase"] = PHASE_DONE
    state._manifest["verified_count"] = 1
    _save_manifest(state._manifest_path, state._manifest)

  batch_calls = []
  original_apply = coord.apply_batch_delete

  def _track_apply(tar_norm):
    batch_calls.append(tar_norm)
    return original_apply(tar_norm)

  coord.apply_batch_delete = _track_apply
  spin = run_supervisor_day_raw_removal_delete_pass(
      coord,
      None,
      chunk_in_progress=False,
      chunk_calendar_day_hint=None,
      finalize_day_close_delete=lambda _t: None,
      sleep_fn=lambda _s: None,
  )
  assert coord.phase(tar_path) == PHASE_DONE
  assert batch_calls == [tar_path]
  assert not os.path.isfile(str(seg))
  assert spin is False


def test_advance_raw_removal_blockers_starts_verify_for_verifying_manifest(
    tmp_path,
):
  day = datetime(2026, 5, 20)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, _zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path)
  state = coord._get_or_create_day(tar_path)
  with state._lock:
    state._manifest["phase"] = PHASE_VERIFYING
    _save_manifest(state._manifest_path, state._manifest)

  verify_calls = []
  original_verify = coord.start_async_verify

  def _track_verify(tar_norm, **kwargs):
    verify_calls.append(tar_norm)
    return original_verify(tar_norm, **kwargs)

  coord.start_async_verify = _track_verify
  assert coord.advance_raw_removal_blockers()
  assert verify_calls == [tar_path]


def test_has_active_raw_removal_work_true_when_done_with_verified_pending(tmp_path):
  day = datetime(2026, 5, 24)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, _zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path)
  state = coord._get_or_create_day(tar_path)
  state._record_entry(str(seg), _zst, "verified", "verified")
  with state._lock:
    state._manifest["phase"] = PHASE_DONE
    state._manifest["verified_count"] = 1
    _save_manifest(state._manifest_path, state._manifest)

  assert state.has_active_raw_removal_work()
  assert coord.any_active_raw_removal_work()
  n, token = coord.blocking_startup_drain_summary()
  assert n == 1
  assert "pending_verified=1" in token


def test_reopen_done_manifest_pending_without_files_on_disk_unblocks_gate(
    tmp_path,
):
  """Manifest-only verified pending (no isfile) must reopen and reconcile."""
  from hpcperfstats.dbload.lib.sync_timedb_day_raw_removal import (
      run_supervisor_day_raw_removal_delete_pass,
  )

  day = datetime(2026, 5, 24)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path)
  state = coord._get_or_create_day(tar_path)
  seg_str = str(seg)
  state._record_entry(seg_str, zst, "verified", "verified")
  seg.unlink()
  with state._lock:
    state._manifest["phase"] = PHASE_DONE
    state._manifest["verified_count"] = 1
    _save_manifest(state._manifest_path, state._manifest)

  assert state.has_active_raw_removal_work()
  assert state.reopen_delete_phase_if_verified_on_disk()
  assert state.phase() == PHASE_DELETING
  assert state.apply_batch_delete() == 1
  assert state._manifest_verified_pending_count() == 0
  assert not state.has_active_raw_removal_work()
  spin = run_supervisor_day_raw_removal_delete_pass(
      coord,
      None,
      chunk_in_progress=False,
      chunk_calendar_day_hint=None,
      finalize_day_close_delete=lambda _t: None,
      sleep_fn=lambda _s: None,
  )
  assert not state.has_active_raw_removal_work()
  assert spin is False


def test_handoff_ingest_complete_triggers_delete_not_second_handoff(tmp_path):
  """RC-I: stale done all-skipped reopens verify instead of silent done."""
  day = datetime(2026, 6, 1)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path, ingest_ready_fn=lambda _p: False)
  state = coord._get_or_create_day(tar_path)
  state._record_entry(str(seg), zst, "skipped_not_in_archive", "not_in_sealed_archive")
  with state._lock:
    state._manifest["phase"] = PHASE_DONE
    state._manifest["skipped_count"] = 1
    _save_manifest(state._manifest_path, state._manifest)

  assert state.stale_done_all_skipped_still_on_disk()
  assert coord.reopen_done_days_with_verified_on_disk() == 1
  assert state.phase() == PHASE_VERIFYING


def test_pre_seal_verify_slices_by_paths_per_tick(tmp_path, monkeypatch):
  day = datetime(2026, 5, 22)
  segs = [_make_closed_segment(tmp_path, "cluster.integration.test", day) for _ in range(5)]
  tgz_dir = tmp_path / "daily"
  tgz_dir.mkdir()
  tar_path = str(tgz_dir / "2026-05-22.tar")
  open(tar_path, "wb").close()
  seg_paths = [str(seg) for seg in segs]
  members = {get_tar_member_name(path): os.path.getsize(path) for path in seg_paths}
  monkeypatch.setattr(cfg, "get_archive_janitor_raw_paths_per_tick", lambda: 2)
  monkeypatch.setattr(cfg, "get_sync_archive_require_db_ingest", lambda: False)
  logs = []

  def _remaining(*_a, **_k):
    return {"host": seg_paths}

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_day_raw_removal.build_remaining_raw_for_daily_tar",
      _remaining,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_day_raw_removal.ensure_daily_tar_restored_for_append",
      lambda *_a, **_k: True,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_day_raw_removal.validate_open_tar_for_raw_removal",
      lambda *_a, **_k: (True, dict(members)),
  )
  coord = _make_coordinator(
      tmp_path,
      log_fn=lambda msg, **kw: logs.append(str(msg)),
  )
  assert coord.run_pre_seal_verify_sync(tar_path) is True
  state = coord._get_or_create_day(tar_path)
  assert "pre_seal_classify_index" not in state._manifest
  progress_logs = [line for line in logs if "classify progress" in line]
  assert len(progress_logs) == 3
  assert "verified_n=2/5" in progress_logs[0]
  assert "verified_n=5/5" in progress_logs[-1]
  assert state.pre_seal_verification_complete()
  assert not any("budget exhausted" in line for line in logs)


def test_pre_seal_verify_completes_large_day_without_budget_log(tmp_path, monkeypatch):
  day = datetime(2026, 6, 2)
  n_paths = 2500
  host = tmp_path / "n.cluster.integration.test"
  host.mkdir(parents=True)
  ts = int(datetime(day.year, day.month, day.day, 12, 0, 0).timestamp())
  seg_paths = []
  for i in range(n_paths):
    seg = host / str(ts + i)
    seg.write_text("%d job1 cn001\nline\n" % (ts + i))
    seg_paths.append(str(seg))
  tgz_dir = tmp_path / "daily"
  tgz_dir.mkdir()
  tar_path = str(tgz_dir / "2026-06-02.tar")
  open(tar_path, "wb").close()
  members = {get_tar_member_name(path): os.path.getsize(path) for path in seg_paths}
  paths_per_tick = 1000
  monkeypatch.setattr(cfg, "get_archive_janitor_raw_paths_per_tick", lambda: paths_per_tick)
  monkeypatch.setattr(cfg, "get_sync_archive_require_db_ingest", lambda: False)
  logs = []

  def _remaining(*_a, **_k):
    return {"host": seg_paths}

  def _classify(_tar, paths, **_k):
    for path in paths:
      yield path, "verified", "ok"

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_day_raw_removal.build_remaining_raw_for_daily_tar",
      _remaining,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_day_raw_removal.ensure_daily_tar_restored_for_append",
      lambda *_a, **_k: True,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_day_raw_removal.validate_open_tar_for_raw_removal",
      lambda *_a, **_k: (True, dict(members)),
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_day_raw_removal.classify_removable_raw_paths_for_open_tar",
      _classify,
  )
  coord = _make_coordinator(
      tmp_path,
      log_fn=lambda msg, **kw: logs.append(str(msg)),
  )
  assert coord.run_pre_seal_verify_sync(tar_path) is True
  state = coord._get_or_create_day(tar_path)
  assert "pre_seal_classify_index" not in state._manifest
  assert state.pre_seal_verification_complete()
  progress_logs = [line for line in logs if "classify progress" in line]
  assert len(progress_logs) == 3
  assert "verified_n=1000/2500" in progress_logs[0]
  assert "verified_n=2500/2500" in progress_logs[-1]
  assert not any("budget exhausted" in line for line in logs)


def test_rescan_exclude_skips_day_scoped_before_ingest_going(tmp_path, monkeypatch):
  """Pre-ingest: no day-scoped closed_raw census when allow flag is false."""
  day = datetime(2022, 7, 1)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, _zst = _seal_day(tmp_path, seg, day)
  day_scoped_calls = []

  def spy_day_scoped(*_a, **_k):
    day_scoped_calls.append(1)
    return {}

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_day_raw_removal.build_remaining_raw_for_daily_tar",
      spy_day_scoped,
  )
  # Prefer gating before helper: allow=False + no snapshot must short-circuit.
  coord = _make_coordinator(tmp_path)
  coord.get_allow_day_scoped_closed_raw = lambda: False
  state = coord._get_or_create_day(tar_path)
  assert state._build_remaining_raw_for_daily_tar() == {}
  assert day_scoped_calls == []
  # When allow is True, helper is invoked (spy records).
  coord.get_allow_day_scoped_closed_raw = lambda: True
  state.get_allow_day_scoped_closed_raw = lambda: True
  state._build_remaining_raw_for_daily_tar()
  assert day_scoped_calls == [1]