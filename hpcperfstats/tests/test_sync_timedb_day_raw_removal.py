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
  VERIFY_STAGE_PRE_SEAL,
  DayRawRemovalCoordinator,
  _save_manifest,
  day_removal_manifest_path,
)


def _make_closed_segment(tmp_path, arch_suffix, day):
  host = tmp_path / ("n." + arch_suffix)
  host.mkdir(parents=True, exist_ok=True)
  ts = int(datetime(day.year, day.month, day.day, 12, 0, 0).timestamp())
  seg = host / str(ts)
  seg.write_text(f"{ts} job1 cn001\nline\n")
  os.utime(seg, (ts, ts))
  return seg


def _seal_day(tmp_path, seg, day):
  tgz_dir = tmp_path / "daily"
  tgz_dir.mkdir(exist_ok=True)
  zst_key = str(
    tgz_dir / (f"{day.year:04}-{day.month:02}-{day.day:02}.tar.zst")
  )
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


def _make_coordinator(
  tmp_path, arch_suffix="cluster.integration.test", **kwargs
):
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
  tmp_path,
  monkeypatch,
):
  day = datetime(2022, 6, 2)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, _zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path)
  monkeypatch.setattr(
    cfg, "get_sync_day_close_raw_removal_max_deletes_per_pass", lambda: 0
  )
  state = coord._get_or_create_day(tar_path)
  state._verify_body()
  coord.begin_deleting(tar_path)
  deleted = coord.apply_batch_delete(tar_path)
  assert deleted == 1
  assert not seg.is_file()
  assert not os.path.isfile(tar_path)
  assert coord.phase(tar_path) == PHASE_DONE


def test_apply_batch_delete_deletes_when_only_in_paths_pending_delete(
  tmp_path,
  monkeypatch,
):
  """Production skip union includes paths_pending_delete; must not self-block delete."""
  day = datetime(2022, 6, 14)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path)
  monkeypatch.setattr(
    cfg, "get_sync_day_close_raw_removal_max_deletes_per_pass", lambda: 0
  )
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


def test_apply_batch_delete_skips_path_in_quarantine_skip_paths(
  tmp_path, monkeypatch
):
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
  monkeypatch.setattr(
    cfg, "get_sync_day_close_raw_removal_max_deletes_per_pass", lambda: 0
  )
  state = coord._get_or_create_day(tar_path)
  state._record_entry(skip_path, zst, "verified", "verified")
  with state._lock:
    state._manifest["phase"] = PHASE_DELETING
    state._manifest["verified_count"] = 1
    _save_manifest(state._manifest_path, state._manifest)
  # Live ingest overlap (handoff) - not paths_pending_delete self-block.
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


def test_apply_batch_delete_deletes_when_not_in_skip_paths(
  tmp_path, monkeypatch
):
  day = datetime(2022, 6, 3)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(
    tmp_path,
    get_quarantine_skip_paths=lambda: {"/nonexistent/active/ingest/path"},
  )
  monkeypatch.setattr(
    cfg, "get_sync_day_close_raw_removal_max_deletes_per_pass", lambda: 0
  )
  state = coord._get_or_create_day(tar_path)
  state._record_entry(str(seg), zst, "verified", "verified")
  with state._lock:
    state._manifest["phase"] = PHASE_DELETING
    state._manifest["verified_count"] = 1
    _save_manifest(state._manifest_path, state._manifest)
  deleted = state.apply_batch_delete()
  assert deleted == 1
  assert not seg.is_file()


def test_day_raw_removal_apply_batch_delete_skips_fingerprint_changed(
  tmp_path, monkeypatch
):
  day = datetime(2022, 6, 3)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, _zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path)
  monkeypatch.setattr(
    cfg, "get_sync_day_close_raw_removal_max_deletes_per_pass", lambda: 0
  )
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
  from hpcperfstats.dbload.lib.sync_timedb_day_raw_removal import (
    _save_manifest,
  )

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


def test_start_async_verify_runs_verify_only(tmp_path, monkeypatch):
  day = datetime(2022, 6, 6)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, _zst = _seal_day(tmp_path, seg, day)
  completed = []
  coord = _make_coordinator(
    tmp_path,
    on_pipeline_complete=lambda tar: completed.append(tar),
  )
  monkeypatch.setattr(
    cfg, "get_sync_day_close_raw_removal_max_deletes_per_pass", lambda: 0
  )
  coord.start_async_verify(tar_path)
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


def test_any_active_raw_removal_work_false_when_only_retryable_skips_remain(
  tmp_path,
):
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
  # F15: retryable closed raw still on disk -> leave phase; do not mark PHASE_DONE.
  assert coord.phase(tar_path) != PHASE_DONE
  assert coord.any_needs_delete_phase()
  assert not coord.any_active_raw_removal_work()
  assert coord.count_days_waiting_on_ingest() == 1
  assert seg.is_file()


def test_verification_complete_all_verified_deleted_retryable_skips_handoff(
  tmp_path,
  monkeypatch,
):
  """June-4 shape: verified deletes done; retryable skips on disk need handoff."""
  day = datetime(2026, 6, 4)
  verified_seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  retry_host = tmp_path / "n2.cluster.integration.test"
  retry_host.mkdir(parents=True, exist_ok=True)
  ts = int(datetime(day.year, day.month, day.day, 14, 0, 0).timestamp())
  retry_seg = retry_host / str(ts)
  retry_seg.write_text(f"{ts} job2 cn002\nline\n")
  os.utime(retry_seg, (ts, ts))
  tar_path, zst = _seal_day(tmp_path, verified_seg, day)
  coord = _make_coordinator(tmp_path, ingest_ready_fn=lambda _p: False)
  monkeypatch.setattr(
    cfg, "get_sync_day_close_raw_removal_max_deletes_per_pass", lambda: 0
  )
  state = coord._get_or_create_day(tar_path)
  verified_path = str(verified_seg)
  retry_path = str(retry_seg)
  state._record_entry(verified_path, zst, "verified", "verified")
  state._record_entry(
    retry_path, zst, "skipped_not_in_archive", "not_in_sealed_archive"
  )
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
  # F15: retryable raw remains -> stay verification_complete/deleting, not PHASE_DONE.
  assert coord.phase(tar_path) != PHASE_DONE
  assert retry_seg.is_file()
  assert coord.should_handoff_to_ingest(tar_path)
  handoff_paths = coord.complete_handoff_to_ingest(tar_path)
  assert retry_path in handoff_paths
  assert coord.phase(tar_path) != PHASE_DONE


def test_mixed_not_in_archive_and_quarantine_marks_done_waiting_on_ingest(
  tmp_path,
  monkeypatch,
):
  """06-07 C2: mixed retryable + quarantine must not stay verification_complete."""
  day = datetime(2026, 6, 7)
  verified_seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  retry_host = tmp_path / "n2.cluster.integration.test"
  retry_host.mkdir(parents=True, exist_ok=True)
  ts_retry = int(datetime(day.year, day.month, day.day, 14, 0, 0).timestamp())
  retry_seg = retry_host / str(ts_retry)
  retry_seg.write_text(f"{ts_retry} job2 cn002\nline\n")
  os.utime(retry_seg, (ts_retry, ts_retry))
  quar_host = tmp_path / "n3.cluster.integration.test"
  quar_host.mkdir(parents=True, exist_ok=True)
  ts_quar = int(datetime(day.year, day.month, day.day, 15, 0, 0).timestamp())
  quar_seg = quar_host / str(ts_quar)
  quar_seg.write_text(f"{ts_quar} job3 cn003\nline\n")
  os.utime(quar_seg, (ts_quar, ts_quar))
  tar_path, zst = _seal_day(tmp_path, verified_seg, day)
  coord = _make_coordinator(tmp_path, ingest_ready_fn=lambda _p: False)
  monkeypatch.setattr(
    cfg, "get_sync_day_close_raw_removal_max_deletes_per_pass", lambda: 0
  )
  state = coord._get_or_create_day(tar_path)
  verified_path = str(verified_seg)
  retry_path = str(retry_seg)
  quar_path = str(quar_seg)
  state._record_entry(verified_path, zst, "verified", "verified")
  state._record_entry(
    retry_path, zst, "skipped_not_in_archive", "not_in_sealed_archive"
  )
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
  # F15: retryable raw remains -> do not mark PHASE_DONE.
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


def test_reopen_stale_done_clears_verify_stage(tmp_path):
  """Reopen must not leave verify_stage=post_seal with phase=verifying."""
  day = datetime(2026, 5, 31)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path, ingest_ready_fn=lambda _p: False)
  state = coord._get_or_create_day(tar_path)
  state._record_entry(
    str(seg), zst, "skipped_not_in_archive", "not_in_sealed_archive"
  )
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
  tmp_path,
  monkeypatch,
):
  """F15: only-retryable remaining must not PHASE_DONE; handoff instead.

  Stale expectation of PHASE_DONE with retryables on disk conflicted with F15
  and Branch C (reclassify-under-deleting). Gate-fail paths stay deleting + handoff.
  """
  day = datetime(2022, 6, 13)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, _zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path, ingest_ready_fn=lambda _p: False)
  monkeypatch.setattr(
    cfg, "get_sync_day_close_raw_removal_max_deletes_per_pass", lambda: 0
  )
  state = coord._get_or_create_day(tar_path)
  state._verify_body()
  assert not coord.paths_pending_delete()
  coord.begin_deleting(tar_path)
  deleted = coord.apply_batch_delete(tar_path)
  assert deleted == 0
  # F15: retryable closed raw remains -> refuse PHASE_DONE.
  assert coord.phase(tar_path) != PHASE_DONE
  assert coord.phase(tar_path) == PHASE_DELETING
  assert state._needs_retry_after_ingest() or coord.should_handoff_to_ingest(
    tar_path
  )
  assert seg.is_file()


def test_skip_stuck_older_day_allows_younger_delete_in_one_pass(
  tmp_path, monkeypatch
):
  day_old = datetime(2022, 6, 10)
  day_young = datetime(2022, 6, 11)
  seg_old = _make_closed_segment(tmp_path, "cluster.integration.test", day_old)
  seg_young = _make_closed_segment(
    tmp_path, "cluster.integration.test", day_young
  )
  tar_old, _zst_old = _seal_day(tmp_path, seg_old, day_old)
  tar_young, _zst_young = _seal_day(tmp_path, seg_young, day_young)
  coord = _make_coordinator(tmp_path)
  monkeypatch.setattr(
    cfg, "get_sync_day_close_raw_removal_max_deletes_per_pass", lambda: 0
  )
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
  tmp_path,
  monkeypatch,
):
  day = datetime(2022, 6, 15)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, _zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path, ingest_ready_fn=lambda _p: False)
  monkeypatch.setattr(
    cfg, "get_sync_day_close_raw_removal_max_deletes_per_pass", lambda: 0
  )
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
  state._record_entry(
    str(seg), zst, "skipped_not_in_archive", "not_in_sealed_archive"
  )
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
  state._record_entry(
    str(seg), zst, "skipped_not_in_archive", "not_in_sealed_archive"
  )
  with state._lock:
    state._manifest["phase"] = PHASE_VERIFICATION_COMPLETE
    state._manifest["skipped_count"] = 1
  _save_manifest(state._manifest_path, state._manifest)
  paths = coord.complete_handoff_to_ingest(tar_path, reason="unit_test")
  assert paths == [str(seg)]
  # F15: do not mark PHASE_DONE while retryable closed raw remains on disk.
  assert coord.phase(tar_path) == PHASE_VERIFICATION_COMPLETE
  assert handoffs == [(os.path.normpath(tar_path), [str(seg)], "unit_test")]


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
    pytest.fail(
      "handoff must not trigger full remaining-raw scan when phase=done"
    )

  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_day_raw_removal.build_remaining_raw_for_daily_tar",
    _fail_full_scan,
  )
  assert coord.should_handoff_to_ingest(tar_path)
  assert state.handoff_paths_for_ingest() == [str(seg)]


def test_should_handoff_blocked_when_phase_done_verified_on_disk(
  tmp_path, monkeypatch
):
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
    lambda *_a, **_k: pytest.fail(
      "manifest-fast handoff must not need full scan"
    ),
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
    seg.write_text(f"{ts} job1 cn001\nline\n")
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
    lambda *_a, **_k: pytest.fail(
      "manifest-fast handoff must not need full scan"
    ),
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
  tmp_path,
  monkeypatch,
):
  day = datetime(2022, 6, 22)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, _zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path, ingest_ready_fn=lambda _p: True)
  validate_calls = []

  def _fail_validate(*_a, **_k):
    validate_calls.append(True)
    raise AssertionError(
      "validate_sealed_daily_archive_for_raw_removal must not run"
    )

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


def test_discover_closed_raw_handoffs_phase_done_verified(tmp_path):
  """phase=done + verified on disk -> narrow discover (no handoff paths)."""
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
  # DE-25: a done manifest with verified raw still on disk reopens on load.
  assert coord2.phase(tar_path) == PHASE_VERIFICATION_COMPLETE
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
  assert (
    coord.kick_closed_raw_unblock(tar_path, reason="unit") == "delete_reopen"
  )
  assert verify_calls == []
  assert coord.phase(tar_path) == PHASE_DELETING


def test_branch_c_reclassify_under_deleting_upgrades_before_handoff(
  tmp_path,
  monkeypatch,
):
  """Branch C (hpcperfstats03): phase=deleting + verified drained + sticky retryable.

  F15 keeps phase=deleting while retryable_skips remain. Reclassify must run under
  deleting (not only phase=done) so membership+DB-ready skips upgrade and delete
  can finish instead of perpetual batch_delete_waiting_on_ingest handoff.
  """
  day = datetime(2026, 6, 7)
  host = tmp_path / "n.cluster.integration.test"
  host.mkdir(parents=True, exist_ok=True)
  segs = []
  for hour in (10, 11):
    ts = int(datetime(day.year, day.month, day.day, hour, 0, 0).timestamp())
    seg = host / str(ts)
    seg.write_text(f"{ts} job1 cn001\nline\n")
    os.utime(seg, (ts, ts))
    segs.append(seg)
  tar_path, zst = _seal_day(tmp_path, segs[0], day)
  with tarfile.open(tar_path, "a") as tf:
    tf.add(str(segs[1]), arcname=get_tar_member_name(str(segs[1])))
  coord = _make_coordinator(tmp_path, ingest_ready_fn=lambda _p: True)
  monkeypatch.setattr(
    cfg,
    "get_sync_day_close_raw_removal_max_deletes_per_pass",
    lambda: 0,
  )
  state = coord._get_or_create_day(tar_path)
  # Verified path already deleted (verified_not_deleted=0).
  state._record_entry(str(segs[0]), zst, "verified", "verified")
  with state._lock:
    state._manifest["entries"][str(segs[0])]["deleted"] = True
    state._manifest["verified_count"] = 1
  # Sticky retryable still on disk but now in tar + DB-ready.
  state._record_entry(
    str(segs[1]),
    zst,
    "skipped_not_in_archive",
    "not_in_sealed_archive",
  )
  with state._lock:
    state._manifest["phase"] = PHASE_DELETING
    state._manifest["verify_stage"] = VERIFY_STAGE_POST_SEAL
    state._manifest["skipped_count"] = 1
    _save_manifest(state._manifest_path, state._manifest)
  segs[0].unlink()

  assert coord.phase(tar_path) == PHASE_DELETING
  assert not coord.delete_phase_done(tar_path)
  assert segs[1].is_file()

  # INV-COPY (DL-2): the upgraded member is only in the mutable open tar
  # (tar is a superset of the sealed zst), so the first pass must not delete.
  coord.apply_batch_delete(tar_path)
  assert segs[1].is_file()
  assert not state._manifest["entries"][str(segs[1])].get("deleted")
  # After a reseal the member is in the sealed map and the delete completes.
  atomic_seal_tar_to_zst(
    tar_path,
    zst,
    num_threads=1,
    compress_level=6,
    keep_uncompressed_tar=True,
    log_fn=None,
  )
  coord.apply_batch_delete(tar_path)
  entry = state._manifest["entries"][str(segs[1])]
  assert entry["status"] == "verified", (
    "Branch C: reclassify under deleting must upgrade retryable before handoff"
  )
  assert entry.get("deleted")
  assert not segs[1].is_file()
  assert coord.delete_phase_done(tar_path)


def test_skip_only_deleting_not_in_tar_handoffs_not_freeze(tmp_path):
  """06-02/06-10 shape: deleting + skipped_not_in_archive, membership false.

  Reclassify cannot upgrade. apply_batch_delete must durable-handoff, not
  silent freeze with deleted_count=0 and no callback.
  """
  day = datetime(2026, 6, 2)
  host = tmp_path / "n.cluster.integration.test"
  host.mkdir(parents=True, exist_ok=True)
  ts0 = int(datetime(day.year, day.month, day.day, 10, 0, 0).timestamp())
  ts1 = int(datetime(day.year, day.month, day.day, 11, 0, 0).timestamp())
  seg0 = host / str(ts0)
  seg1 = host / str(ts1)
  seg0.write_text(f"{ts0} job1 cn001\nline\n")
  seg1.write_text(f"{ts1} job1 cn001\nline\n")
  os.utime(seg0, (ts0, ts0))
  os.utime(seg1, (ts1, ts1))
  tar_path, zst = _seal_day(tmp_path, seg0, day)
  handoffs = []

  def _on_handoff(tar_norm, paths, reason):
    handoffs.append((tar_norm, list(paths), reason))

  coord = _make_coordinator(
    tmp_path,
    ingest_ready_fn=lambda _p: False,
    on_handoff_to_ingest=_on_handoff,
  )
  state = coord._get_or_create_day(tar_path)
  state._record_entry(str(seg0), zst, "verified", "verified")
  with state._lock:
    state._manifest["entries"][str(seg0)]["deleted"] = True
    state._manifest["verified_count"] = 1
  state._record_entry(
    str(seg1),
    zst,
    "skipped_not_in_archive",
    "not_in_sealed_archive",
  )
  with state._lock:
    state._manifest["phase"] = PHASE_DELETING
    state._manifest["verify_stage"] = VERIFY_STAGE_POST_SEAL
    state._manifest["skipped_count"] = 1
    _save_manifest(state._manifest_path, state._manifest)
  seg0.unlink()

  deleted = coord.apply_batch_delete(tar_path)
  assert deleted == 0
  assert seg1.is_file()
  assert handoffs, "skip-only deleting must handoff when membership stays false"
  assert str(seg1) in handoffs[0][1]
  assert handoffs[0][2] == "batch_delete_waiting_on_ingest"
  assert not coord.delete_phase_done(tar_path)

  kick_handoffs = []

  def _on_kick_handoff(tar_norm, paths, reason):
    kick_handoffs.append((tar_norm, list(paths), reason))

  coord.on_handoff_to_ingest = _on_kick_handoff
  action = coord.kick_closed_raw_unblock(tar_path, reason="unit_skip_only")
  assert action == "handoff"
  assert kick_handoffs


def test_reclassify_seal_invalid_open_tar_validate_fails_uses_sealed_map(
  tmp_path,
  monkeypatch,
):
  """Open-tar member map missing: reclassify skipped_seal_invalid via sealed .zst."""
  day = datetime(2026, 8, 18)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path, ingest_ready_fn=lambda _p: True)
  state = coord._get_or_create_day(tar_path)
  state._record_entry(
    str(seg),
    zst,
    "skipped_seal_invalid",
    "seal_validation_failed",
  )
  with state._lock:
    state._manifest["phase"] = PHASE_DONE
    state._manifest["verify_stage"] = VERIFY_STAGE_POST_SEAL
    state._manifest["skipped_count"] = 1
    state._manifest["completed_at"] = time.time()
    _save_manifest(state._manifest_path, state._manifest)

  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_day_raw_removal."
    "validate_open_tar_for_raw_removal",
    lambda *_a, **_k: (False, None),
  )

  upgraded = state._reclassify_retryable_skips_on_disk()
  assert upgraded == 1
  entry = state._manifest["entries"][str(seg)]
  assert entry["status"] == "verified"
  assert state._manifest["verified_count"] == 1
  assert state._manifest["skipped_count"] == 0


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
    seg.write_text(f"{ts} job1 cn001\nline\n")
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
  state._record_entry(
    str(seg), zst, "skipped_not_in_archive", "not_in_sealed_archive"
  )
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
  monkeypatch.setattr(
    cfg, "get_sync_day_close_raw_removal_max_deletes_per_pass", lambda: 0
  )
  state = coord._get_or_create_day(tar_path)
  state._record_entry(
    str(seg), zst, "skipped_not_in_archive", "not_in_sealed_archive"
  )
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
  state._record_entry(
    str(seg), zst, "skipped_not_in_archive", "not_in_sealed_archive"
  )
  with state._lock:
    state._manifest["phase"] = PHASE_DONE
    state._manifest["verify_stage"] = VERIFY_STAGE_POST_SEAL
    state._manifest["completed_at"] = time.time()
    _save_manifest(state._manifest_path, state._manifest)

  assert (
    coord.kick_closed_raw_unblock(tar_path, reason="unit") == "delete_reopen"
  )
  assert coord.phase(tar_path) == PHASE_DELETING
  assert state._manifest["entries"][str(seg)]["status"] == "verified"


def test_has_active_raw_removal_work_false_when_only_verified_pending_delete(
  tmp_path,
):
  day = datetime(2025, 12, 3)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, _zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path)
  state = coord._get_or_create_day(tar_path)
  state._verify_body()
  assert coord.paths_pending_delete()
  assert not coord.any_active_raw_removal_work()
  assert state.has_active_raw_removal_work() is False


def test_ghost_deleted_manifest_path_on_disk_triggers_delete_retry(
  tmp_path,
  monkeypatch,
):
  day = datetime(2026, 5, 26)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, _zst = _seal_day(tmp_path, seg, day)
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


def test_ghost_deleted_manifest_path_on_disk_triggers_delete_retry_while_phase_deleting(
  tmp_path,
  monkeypatch,
):
  """H20b: ghost retry must run while phase=deleting, not only phase=done."""
  day = datetime(2026, 5, 26)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, _zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path)
  state = coord._get_or_create_day(tar_path)
  state._verify_body()
  seg_str = str(seg)
  with state._lock:
    entry = state._manifest["entries"][seg_str]
    entry["deleted"] = True
    entry["status"] = "verified"
    state._manifest["phase"] = PHASE_DELETING
    _save_manifest(state._manifest_path, state._manifest)

  assert not state.delete_phase_done()
  assert state.phase() == PHASE_DELETING
  assert state._ghost_deleted_paths_on_disk() == [seg_str]
  assert state._has_closed_raw_existing_on_disk()
  assert state.needs_ghost_delete_retry()

  deleted = state.apply_batch_delete()
  assert deleted == 1
  assert not os.path.isfile(seg_str)
  assert not state._ghost_deleted_paths_on_disk()


def test_h20c_reclassify_blocking_while_deleting_fingerprint(tmp_path):
  """H20c: blocking skip + verified_pending=0 must reclassify then unlink."""
  day = datetime(2026, 8, 17)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path, ingest_ready_fn=lambda _p: True)
  state = coord._get_or_create_day(tar_path)
  seg_str = str(seg)
  state._record_entry(
    seg_str,
    zst,
    "skipped_fingerprint_changed",
    "fingerprint_changed_before_delete",
  )
  with state._lock:
    state._manifest["phase"] = PHASE_DELETING
    _save_manifest(state._manifest_path, state._manifest)

  assert state.phase() == PHASE_DELETING
  assert state._manifest_verified_pending_count() == 0
  assert state._manifest_retryable_paths_on_disk() == []
  assert state._blocking_manifest_paths_on_disk() == [seg_str]
  assert os.path.isfile(seg_str)

  deleted = coord.apply_batch_delete(tar_path)
  assert deleted == 1
  assert not os.path.isfile(seg_str)
  entry = state._manifest["entries"][seg_str]
  assert entry.get("status") == "verified"
  assert entry.get("deleted") is True


def test_kick_closed_raw_unblock_no_deadlock_retryable_only(tmp_path):
  """Retryable-only must handoff (not begin_deleting) and stay fast (no lock re-entry)."""
  handoffs = []
  retry_day = datetime(2026, 5, 23)
  retry_seg = _make_closed_segment(
    tmp_path, "cluster.integration.test", retry_day
  )
  retry_tar_path, retry_zst = _seal_day(tmp_path, retry_seg, retry_day)
  coord = _make_coordinator(
    tmp_path,
    on_handoff_to_ingest=lambda tar_norm, paths, reason: handoffs.append(
      (tar_norm, list(paths), reason),
    ),
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
  assert (
    coord.kick_closed_raw_unblock(retry_tar_path, reason="unit") == "handoff"
  )
  assert time.time() - start < 0.5
  assert coord.phase(retry_tar_path) == PHASE_DONE
  assert handoffs and str(retry_seg) in handoffs[0][1]


def test_kick_closed_raw_unblock_empty_handoff_advances_when_has_closed(
  tmp_path,
):
  """H18: has_closed with empty handoff must reopen delete (not noop)."""
  day = datetime(2026, 5, 24)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(
    tmp_path,
    log_fn=lambda *_a, **_k: None,
    ingest_ready_fn=lambda _p: True,
  )
  state = coord._get_or_create_day(tar_path)
  # Verified-but-not-deleted: handoff_paths empty (not retryable skip),
  # has_closed True - kick must reopen delete.
  state._record_entry(str(seg), zst, "verified", "verified")
  with state._lock:
    state._manifest["phase"] = PHASE_DONE
    state._manifest["verified_count"] = 1
    _save_manifest(state._manifest_path, state._manifest)

  assert coord.has_closed_raw_on_disk(tar_path)
  assert coord.paths_for_closed_raw_handoff_requeue(tar_path) == []
  assert (
    coord.kick_closed_raw_unblock(tar_path, reason="h18_unit")
    == "delete_reopen"
  )


def test_requeue_closed_raw_skips_quarantine_and_manifested(tmp_path):
  handoffs = []
  quarantine_root = tmp_path / ".sync_timedb_unparsable_raw"
  quarantine_root.mkdir(parents=True, exist_ok=True)
  quarantine_path = quarantine_root / "host" / "9999999999"
  quarantine_path.parent.mkdir(parents=True, exist_ok=True)
  quarantine_path.write_text("bad\n")

  retry_day = datetime(2026, 5, 23)
  retry_seg = _make_closed_segment(
    tmp_path, "cluster.integration.test", retry_day
  )
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


def test_requeue_handoff_before_kick(tmp_path):
  """Handoff paths present -> kick must not run before handoff callback."""
  retry_day = datetime(2026, 5, 23)
  retry_seg = _make_closed_segment(
    tmp_path, "cluster.integration.test", retry_day
  )
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
  """VERIFICATION_COMPLETE with verified pending delete -> begin_deleting kick."""
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

  assert (
    coord.kick_closed_raw_unblock(tar_path, reason="unit") == "delete_reopen"
  )
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
  assert (
    coord.kick_closed_raw_unblock(tar_path, reason="test")
    == "quarantine_terminal"
  )


def test_has_closed_raw_false_when_manifest_done_no_on_disk(
  tmp_path, monkeypatch
):
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
  tmp_path,
  monkeypatch,
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


def test_has_active_raw_removal_work_true_when_done_with_verified_pending(
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

  assert state.has_active_raw_removal_work()
  assert coord.any_active_raw_removal_work()
  n, token = coord.blocking_startup_drain_summary()
  assert n == 1
  assert "pending_verified=1" in token


def test_apply_batch_delete_reopens_phase_done_verified_pending(tmp_path):
  """02 class: coord.apply_batch_delete must reopen phase=done verified pending."""
  day = datetime(2026, 5, 25)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path)
  state = coord._get_or_create_day(tar_path)
  seg_str = str(seg)
  state._record_entry(seg_str, zst, "verified", "verified")
  with state._lock:
    state._manifest["phase"] = PHASE_DONE
    state._manifest["verified_count"] = 1
    state._manifest["deleted_count"] = 0
    _save_manifest(state._manifest_path, state._manifest)

  assert state.phase() == PHASE_DONE
  assert state._manifest_verified_pending_count() == 1
  deleted = coord.apply_batch_delete(tar_path)
  assert deleted >= 1
  assert not os.path.isfile(seg_str)
  assert state._manifest_verified_pending_count() == 0
  entry = state._manifest["entries"][seg_str]
  assert entry.get("deleted") is True


def test_handoff_ingest_complete_triggers_delete_not_second_handoff(tmp_path):
  """RC-I: stale done all-skipped reopens verify instead of silent done."""
  day = datetime(2026, 6, 1)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path, ingest_ready_fn=lambda _p: False)
  state = coord._get_or_create_day(tar_path)
  state._record_entry(
    str(seg), zst, "skipped_not_in_archive", "not_in_sealed_archive"
  )
  with state._lock:
    state._manifest["phase"] = PHASE_DONE
    state._manifest["skipped_count"] = 1
    _save_manifest(state._manifest_path, state._manifest)

  assert state.stale_done_all_skipped_still_on_disk()
  assert coord.reopen_done_days_with_verified_on_disk() == 1
  assert state.phase() == PHASE_VERIFYING


def test_pre_seal_verify_slices_by_paths_per_tick(tmp_path, monkeypatch):
  day = datetime(2026, 5, 22)
  host = tmp_path / "n.cluster.integration.test"
  host.mkdir(parents=True, exist_ok=True)
  segs = []
  for hour in range(5):
    ts = int(datetime(day.year, day.month, day.day, hour, 0, 0).timestamp())
    seg = host / str(ts)
    seg.write_text(f"{ts} job1 cn001\nline\n")
    os.utime(seg, (ts, ts))
    segs.append(seg)
  tgz_dir = tmp_path / "daily"
  tgz_dir.mkdir()
  tar_path = str(tgz_dir / "2026-05-22.tar")
  open(tar_path, "wb").close()
  seg_paths = [str(seg) for seg in segs]
  members = {
    get_tar_member_name(path): os.path.getsize(path) for path in seg_paths
  }
  monkeypatch.setattr(cfg, "get_sync_day_close_raw_paths_per_batch", lambda: 2)
  monkeypatch.setattr(cfg, "get_sync_archive_require_db_ingest", lambda: False)
  logs = []

  def _remaining(*_a, **_k):
    return {"host": seg_paths}

  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_day_raw_removal.build_day_scoped_closed_raw_by_gz",
    _remaining,
  )
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_day_raw_removal.build_remaining_raw_for_daily_tar",
    lambda *_a, **_k: pytest.fail("pre_seal seed must not full-scan remaining"),
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


def test_pre_seal_verify_completes_large_day_without_budget_log(
  tmp_path, monkeypatch
):
  day = datetime(2026, 6, 2)
  n_paths = 2500
  host = tmp_path / "n.cluster.integration.test"
  host.mkdir(parents=True)
  ts = int(datetime(day.year, day.month, day.day, 12, 0, 0).timestamp())
  seg_paths = []
  for i in range(n_paths):
    seg = host / str(ts + i)
    seg.write_text(f"{ts + i} job1 cn001\nline\n")
    seg_paths.append(str(seg))
  tgz_dir = tmp_path / "daily"
  tgz_dir.mkdir()
  tar_path = str(tgz_dir / "2026-06-02.tar")
  open(tar_path, "wb").close()
  members = {
    get_tar_member_name(path): os.path.getsize(path) for path in seg_paths
  }
  paths_per_tick = 1000
  monkeypatch.setattr(
    cfg, "get_sync_day_close_raw_paths_per_batch", lambda: paths_per_tick
  )
  monkeypatch.setattr(cfg, "get_sync_archive_require_db_ingest", lambda: False)
  logs = []

  def _remaining(*_a, **_k):
    return {"host": seg_paths}

  def _classify(_tar, paths, **_k):
    for path in paths:
      yield path, "verified", "ok"

  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_day_raw_removal.build_day_scoped_closed_raw_by_gz",
    _remaining,
  )
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_day_raw_removal.build_remaining_raw_for_daily_tar",
    lambda *_a, **_k: pytest.fail("pre_seal seed must not full-scan remaining"),
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


def test_rescan_exclude_skips_day_scoped_before_ingest_going(
  tmp_path, monkeypatch
):
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


def test_skip_only_apply_batch_delete_memos_day_scoped_closed_raw(
  tmp_path,
  monkeypatch,
):
  """One apply_batch_delete + handoff pass must census closed_raw at most once."""
  day = datetime(2026, 6, 2)
  host = tmp_path / "n.cluster.integration.test"
  host.mkdir(parents=True, exist_ok=True)
  ts0 = int(datetime(day.year, day.month, day.day, 10, 0, 0).timestamp())
  ts1 = int(datetime(day.year, day.month, day.day, 11, 0, 0).timestamp())
  seg0 = host / str(ts0)
  seg1 = host / str(ts1)
  seg0.write_text(f"{ts0} job1 cn001\nline\n")
  seg1.write_text(f"{ts1} job1 cn001\nline\n")
  os.utime(seg0, (ts0, ts0))
  os.utime(seg1, (ts1, ts1))
  tar_path, zst = _seal_day(tmp_path, seg0, day)
  census_calls = []

  import hpcperfstats.dbload.lib.sync_timedb_day_raw_removal as drm

  real_build = drm.build_remaining_raw_for_daily_tar

  def spy_build(*args, **kwargs):
    census_calls.append(1)
    return real_build(*args, **kwargs)

  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_day_raw_removal.build_remaining_raw_for_daily_tar",
    spy_build,
  )

  def _on_handoff(tar_norm, paths, reason):
    return None

  coord = _make_coordinator(
    tmp_path,
    ingest_ready_fn=lambda _p: False,
    on_handoff_to_ingest=_on_handoff,
  )
  state = coord._get_or_create_day(tar_path)
  state._record_entry(str(seg0), zst, "verified", "verified")
  with state._lock:
    state._manifest["entries"][str(seg0)]["deleted"] = True
    state._manifest["verified_count"] = 1
  state._record_entry(
    str(seg1),
    zst,
    "skipped_not_in_archive",
    "not_in_sealed_archive",
  )
  with state._lock:
    state._manifest["phase"] = PHASE_DELETING
    state._manifest["verify_stage"] = VERIFY_STAGE_POST_SEAL
    state._manifest["skipped_count"] = 1
    _save_manifest(state._manifest_path, state._manifest)
  seg0.unlink()

  deleted = coord.apply_batch_delete(tar_path)
  assert deleted == 0
  assert len(census_calls) <= 1, (
    f"skip-only delete/handoff must memoize day-scoped closed_raw (got {len(census_calls)} censuses)"
  )


def test_day_close_job_scoped_closed_raw_pass_memo(tmp_path, monkeypatch):
  """One day_close job must census closed_raw at most once across stages."""
  day = datetime(2026, 6, 3)
  host = tmp_path / "n.cluster.integration.test"
  host.mkdir(parents=True, exist_ok=True)
  ts0 = int(datetime(day.year, day.month, day.day, 10, 0, 0).timestamp())
  seg0 = host / str(ts0)
  seg0.write_text(f"{ts0} job1 cn001\nline\n")
  os.utime(seg0, (ts0, ts0))
  tar_path, zst = _seal_day(tmp_path, seg0, day)
  del zst
  census_calls = []

  import hpcperfstats.dbload.lib.sync_timedb_day_raw_removal as drm

  real_build = drm.build_remaining_raw_for_daily_tar

  def spy_build(*args, **kwargs):
    census_calls.append(1)
    return real_build(*args, **kwargs)

  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_day_raw_removal.build_remaining_raw_for_daily_tar",
    spy_build,
  )

  coord = _make_coordinator(
    tmp_path,
    ingest_ready_fn=lambda _p: False,
  )
  state = coord._get_or_create_day(tar_path)
  state._begin_closed_raw_pass_memo()
  try:
    state._build_remaining_raw_for_daily_tar()
    state._build_remaining_raw_for_daily_tar()
    state._has_closed_raw_existing_on_disk()
    state._remaining_raw_paths_blocking_tar_drop()
  finally:
    state._clear_closed_raw_pass_memo()
  assert len(census_calls) == 1, (
    f"job-scoped memo must census once across pre_seal/seal probes (got {len(census_calls)})"
  )


def test_manifest_fast_no_full_remaining_handoff_verification_complete(
  tmp_path,
  monkeypatch,
):
  """T1b: handoff probes after verify must not call build_remaining_raw."""
  day = datetime(2026, 7, 28)
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
    state._manifest["phase"] = PHASE_DELETING
    state._manifest["verify_stage"] = VERIFY_STAGE_POST_SEAL
    state._manifest["skipped_count"] = 1
    _save_manifest(state._manifest_path, state._manifest)

  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_day_raw_removal.build_remaining_raw_for_daily_tar",
    lambda *_a, **_k: pytest.fail("manifest-fast handoff must not full-scan"),
  )
  assert state._only_waiting_on_ingest_blocks_completion()
  assert coord.should_handoff_to_ingest(tar_path)
  assert state.handoff_paths_for_ingest() == [str(seg)]
  assert state._has_closed_raw_existing_on_disk()


def test_manifest_fast_no_full_remaining_delete_tar_drop(tmp_path, monkeypatch):
  """T1b: delete completion tar_drop must not full-scan when manifest terminal."""
  day = datetime(2026, 8, 3)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path, ingest_ready_fn=lambda _p: True)
  monkeypatch.setattr(
    cfg, "get_sync_day_close_raw_removal_max_deletes_per_pass", lambda: 10
  )
  state = coord._get_or_create_day(tar_path)
  state._record_entry(str(seg), zst, "verified", "verified")
  with state._lock:
    state._manifest["phase"] = PHASE_DELETING
    state._manifest["verify_stage"] = VERIFY_STAGE_POST_SEAL
    state._manifest["verified_count"] = 1
    _save_manifest(state._manifest_path, state._manifest)
  seg.unlink()
  with state._lock:
    state._manifest["entries"][str(seg)]["deleted"] = True
    state._manifest["deleted_count"] = 1
    _save_manifest(state._manifest_path, state._manifest)

  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_day_raw_removal.build_remaining_raw_for_daily_tar",
    lambda *_a, **_k: pytest.fail("delete tar_drop must not full-scan"),
  )
  deleted = coord.apply_batch_delete(tar_path)
  assert deleted == 0
  assert coord.phase(tar_path) == PHASE_DONE
  assert not os.path.isfile(tar_path)


def test_pre_seal_first_seed_uses_day_scoped_not_full_remaining(
  tmp_path,
  monkeypatch,
):
  """Oct-1 3F: first pre_seal claim must not call build_remaining_raw_for_daily_tar."""
  day = datetime(2026, 8, 16)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, _zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path, ingest_ready_fn=lambda _p: True)
  day_scoped_calls = []

  def _day_scoped(*_a, **_k):
    day_scoped_calls.append(1)
    return {"z": [str(seg)]}

  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_day_raw_removal.build_day_scoped_closed_raw_by_gz",
    _day_scoped,
  )
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_day_raw_removal.build_remaining_raw_for_daily_tar",
    lambda *_a, **_k: pytest.fail("pre_seal first seed must not full-scan"),
  )
  assert coord.run_pre_seal_verify_sync(tar_path) is True
  assert day_scoped_calls == []


def test_pre_seal_many_tar_members_no_day_scoped_collect(tmp_path, monkeypatch):
  """Oct-2 3G: large open_tar_members must not run day-scoped collect on first seed."""
  day = datetime(2026, 8, 24)
  host = tmp_path / "n.cluster.integration.test"
  host.mkdir(parents=True, exist_ok=True)
  seg_paths = []
  for hour in range(8):
    ts = int(datetime(day.year, day.month, day.day, hour, 0, 0).timestamp())
    seg = host / str(ts)
    seg.write_text(f"{ts} job1 cn001\nline\n")
    os.utime(seg, (ts, ts))
    seg_paths.append(str(seg))
  tgz_dir = tmp_path / "daily"
  tgz_dir.mkdir()
  tar_path = str(tgz_dir / "2026-08-24.tar")
  open(tar_path, "wb").close()
  members = {get_tar_member_name(p): os.path.getsize(p) for p in seg_paths}
  monkeypatch.setattr(cfg, "get_sync_archive_require_db_ingest", lambda: False)
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_day_raw_removal.ensure_daily_tar_restored_for_append",
    lambda *_a, **_k: True,
  )
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_day_raw_removal.validate_open_tar_for_raw_removal",
    lambda *_a, **_k: (True, dict(members)),
  )
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_archive_helpers.collect_stats_files_in_range",
    lambda *_a, **_k: pytest.fail(
      "3G must not day-wide collect on first pre_seal seed"
    ),
  )
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_day_raw_removal.build_day_scoped_closed_raw_by_gz",
    lambda *_a, **_k: pytest.fail(
      "3G must not day_scoped when members resolve"
    ),
  )

  def _classify(_tar, paths, **_k):
    for path in paths:
      yield path, "verified", "ok"

  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_day_raw_removal.classify_removable_raw_paths_for_open_tar",
    _classify,
  )
  coord = _make_coordinator(tmp_path)
  assert coord.run_pre_seal_verify_sync(tar_path) is True


def test_closed_raw_on_disk_manifest_fast_when_verifying_complete(
  tmp_path, monkeypatch
):
  """3H: delete/handoff probes must not full-scan when verification_complete."""
  day = datetime(2026, 8, 6)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path)
  state = coord._get_or_create_day(tar_path)
  state._record_entry(str(seg), zst, "verified", "verified")
  with state._lock:
    state._manifest["phase"] = PHASE_VERIFICATION_COMPLETE
    state._manifest["verify_stage"] = VERIFY_STAGE_POST_SEAL
    state._manifest["verified_count"] = 1
    _save_manifest(state._manifest_path, state._manifest)

  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_day_raw_removal.build_remaining_raw_for_daily_tar",
    lambda *_a, **_k: pytest.fail(
      "verification_complete closed_raw must be manifest-fast"
    ),
  )
  assert state._closed_raw_paths_on_disk() == [str(seg)]


def test_pre_seal_classify_paths_cache_skips_second_find(tmp_path, monkeypatch):
  """Pre-seal resume must not repeat build_remaining_raw when paths cached."""
  day = datetime(2026, 7, 30)
  host = tmp_path / "n.cluster.integration.test"
  host.mkdir(parents=True, exist_ok=True)
  ts0 = int(datetime(day.year, day.month, day.day, 10, 0, 0).timestamp())
  ts1 = int(datetime(day.year, day.month, day.day, 11, 0, 0).timestamp())
  seg0 = host / str(ts0)
  seg1 = host / str(ts1)
  seg0.write_text(f"{ts0} job1 cn001\nline\n")
  seg1.write_text(f"{ts1} job1 cn001\nline\n")
  os.utime(seg0, (ts0, ts0))
  os.utime(seg1, (ts1, ts1))
  tar_path, _zst = _seal_day(tmp_path, seg0, day)
  coord = _make_coordinator(tmp_path, ingest_ready_fn=lambda _p: True)
  state = coord._get_or_create_day(tar_path)
  with state._lock:
    state._manifest["pre_seal_classify_paths"] = [str(seg0), str(seg1)]
    state._manifest["pre_seal_classify_index"] = 0
    _save_manifest(state._manifest_path, state._manifest)

  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_day_raw_removal.build_day_scoped_closed_raw_by_gz",
    lambda *_a, **_k: pytest.fail("cached pre_seal must not day-scoped seed"),
  )
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_day_raw_removal.build_remaining_raw_for_daily_tar",
    lambda *_a, **_k: pytest.fail("cached pre_seal must not full-scan"),
  )
  monkeypatch.setattr(cfg, "get_sync_day_close_raw_paths_per_batch", lambda: 1)
  assert (
    coord.run_pre_seal_verify_sync(tar_path, max_classify_batches=1) is False
  )
  assert int(state._manifest.get("pre_seal_classify_index", 0)) == 1
  assert (
    coord.run_pre_seal_verify_sync(tar_path, max_classify_batches=1) is True
  )
  assert "pre_seal_classify_paths" not in state._manifest


# --- INV-COPY delete gate (A14 DL-2/DL-3) and sealed member map (T0) ---


def _two_segments(tmp_path, day):
  host = tmp_path / "n.cluster.integration.test"
  host.mkdir(parents=True, exist_ok=True)
  segs = []
  for hour in (10, 11):
    ts = int(datetime(day.year, day.month, day.day, hour, 0, 0).timestamp())
    seg = host / str(ts)
    seg.write_text(f"{ts} job1 cn001\nline {hour}\n")
    os.utime(seg, (ts, ts))
    segs.append(seg)
  return segs


def _append_member(tar_path, seg):
  with tarfile.open(tar_path, "a") as tf:
    tf.add(str(seg), arcname=get_tar_member_name(str(seg)))


def _verified_ready_state(coord, tar_path, zst, paths):
  state = coord._get_or_create_day(tar_path)
  for path in paths:
    state._record_entry(str(path), zst, "verified", "verified")
  with state._lock:
    state._manifest["phase"] = PHASE_VERIFICATION_COMPLETE
    state._manifest["verify_stage"] = VERIFY_STAGE_POST_SEAL
    _save_manifest(state._manifest_path, state._manifest)
  return state


def test_raw_delete_requires_member_in_sealed_zst(tmp_path):
  day = datetime(2026, 8, 23)
  sealed_seg, tar_only_seg = _two_segments(tmp_path, day)
  tar_path, zst = _seal_day(tmp_path, sealed_seg, day)
  _append_member(tar_path, tar_only_seg)  # tar is a strict superset of zst
  coord = _make_coordinator(tmp_path)
  state = _verified_ready_state(
    coord, tar_path, zst, [sealed_seg, tar_only_seg]
  )

  coord.apply_batch_delete(tar_path)

  assert not sealed_seg.is_file()
  assert tar_only_seg.is_file(), "only copy is the mutable open tar"
  entry = state._manifest["entries"][str(tar_only_seg)]
  assert not entry.get("deleted")
  assert entry["status"] == "skipped_not_in_archive"


def test_delete_refused_when_tar_identity_changed_since_verify(tmp_path):
  day = datetime(2026, 8, 24)
  seg_a, seg_b = _two_segments(tmp_path, day)
  tar_path, zst = _seal_day(tmp_path, seg_a, day)
  os.remove(zst)  # pre-seal: only the open tar holds the bytes
  coord = _make_coordinator(tmp_path)
  state = _verified_ready_state(coord, tar_path, zst, [seg_a])
  _append_member(tar_path, seg_b)  # tar identity changes after verify

  coord.apply_batch_delete(tar_path)

  assert seg_a.is_file()
  entry = state._manifest["entries"][str(seg_a)]
  assert entry.get("delete_deferred") == "sealed_copy_unproven"
  assert not entry.get("deleted")


def test_ghost_delete_retry_reconfirms_member_in_zst(tmp_path):
  day = datetime(2026, 8, 25)
  sealed_seg, ghost_seg = _two_segments(tmp_path, day)
  tar_path, zst = _seal_day(tmp_path, sealed_seg, day)
  _append_member(tar_path, ghost_seg)
  coord = _make_coordinator(tmp_path)
  state = _verified_ready_state(coord, tar_path, zst, [sealed_seg, ghost_seg])
  with state._lock:
    state._manifest["entries"][str(ghost_seg)]["deleted"] = True
    state._manifest["phase"] = PHASE_DONE
    _save_manifest(state._manifest_path, state._manifest)
  assert coord._get_or_create_day(tar_path).needs_ghost_delete_retry()

  coord.apply_batch_delete(tar_path)

  assert ghost_seg.is_file(), "ghost retry must not trust deleted=True status"


def test_identity_mismatch_defers_delete_and_queues_one_rebuild(
  tmp_path,
  monkeypatch,
):
  import hpcperfstats.dbload.lib.sync_timedb_sealed_member_map as smm

  day = datetime(2026, 8, 26)
  seg_a, seg_b = _two_segments(tmp_path, day)
  tar_path, zst = _seal_day(tmp_path, seg_a, day)
  _append_member(tar_path, seg_b)
  atomic_seal_tar_to_zst(
    tar_path,
    zst,
    num_threads=1,
    compress_level=6,
    keep_uncompressed_tar=True,
    log_fn=None,
  )
  calls = []
  monkeypatch.setattr(
    smm, "request_sealed_member_map_rebuild", lambda p: calls.append(p)
  )
  smm._CACHE.clear()
  st = os.stat(zst)
  os.utime(zst, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
  coord = _make_coordinator(tmp_path)
  state = _verified_ready_state(coord, tar_path, zst, [seg_a, seg_b])

  coord.apply_batch_delete(tar_path)

  assert seg_a.is_file() and seg_b.is_file()
  for seg in (seg_a, seg_b):
    entry = state._manifest["entries"][str(seg)]
    assert entry.get("delete_deferred") == "sealed_copy_unproven"
    assert entry["status"] == "verified"
  assert calls == [zst], "exactly one background rebuild per delete pass"


def test_delete_gate_never_spawns_zstd(tmp_path, monkeypatch):
  import subprocess

  day = datetime(2026, 8, 27)
  seg_a, seg_b = _two_segments(tmp_path, day)
  tar_path, zst = _seal_day(tmp_path, seg_a, day)
  _append_member(tar_path, seg_b)
  atomic_seal_tar_to_zst(
    tar_path,
    zst,
    num_threads=1,
    compress_level=6,
    keep_uncompressed_tar=True,
    log_fn=None,
  )
  coord = _make_coordinator(tmp_path)
  _verified_ready_state(coord, tar_path, zst, [seg_a, seg_b])
  spawned = []
  real_popen = subprocess.Popen

  def _spy(args, *a, **kw):
    spawned.append(" ".join(map(str, args)) if isinstance(args, list) else args)
    return real_popen(args, *a, **kw)

  monkeypatch.setattr(subprocess, "Popen", _spy)

  coord.apply_batch_delete(tar_path)

  assert not seg_a.is_file() and not seg_b.is_file()
  assert not [c for c in spawned if "zstd" in str(c)], spawned


def test_day_skip_tar_raw_never_deleted(tmp_path):
  day = datetime(2026, 10, 14)
  sealed_seg, cut_seg = _two_segments(tmp_path, day)
  tar_path, zst = _seal_day(tmp_path, sealed_seg, day)
  _append_member(tar_path, cut_seg)
  # Truncate inside the last member, like 01's 2026-10-14 (S - C = 6144).
  with open(tar_path, "r+b") as fh:
    fh.truncate(os.path.getsize(tar_path) - 2048)
  coord = _make_coordinator(tmp_path)
  _verified_ready_state(coord, tar_path, zst, [sealed_seg, cut_seg])

  coord.apply_batch_delete(tar_path)

  assert cut_seg.is_file(), "member past the cut has no other good copy"


# --- A15: day-close blockers DE-25 / DE-26 ---


def test_seal_invalid_not_terminal_phase_done_not_written(tmp_path):
  """DE-25: skipped_seal_invalid raw on disk must keep the day out of done."""
  day = datetime(2026, 8, 22)
  sealed_seg, invalid_seg = _two_segments(tmp_path, day)
  tar_path, zst = _seal_day(tmp_path, sealed_seg, day)
  coord = _make_coordinator(tmp_path)
  state = _verified_ready_state(coord, tar_path, zst, [sealed_seg])
  state._record_entry(
    str(invalid_seg), zst, "skipped_seal_invalid", "seal_invalid"
  )

  coord.apply_batch_delete(tar_path)

  assert not sealed_seg.is_file()
  assert invalid_seg.is_file()
  assert coord.phase(tar_path) != PHASE_DONE
  assert not state._all_closed_raw_terminal_or_gone()


def test_done_manifest_with_on_disk_seal_invalid_reopens(tmp_path):
  """DE-25: a frozen done manifest (older release) reopens on first load."""
  day = datetime(2026, 8, 23)
  sealed_seg, invalid_seg = _two_segments(tmp_path, day)
  tar_path, zst = _seal_day(tmp_path, sealed_seg, day)
  coord = _make_coordinator(tmp_path)
  state = coord._get_or_create_day(tar_path)
  state._record_entry(
    str(invalid_seg), zst, "skipped_seal_invalid", "seal_invalid"
  )
  with state._lock:
    state._manifest["phase"] = PHASE_DONE
    state._manifest["verify_stage"] = VERIFY_STAGE_POST_SEAL
    _save_manifest(state._manifest_path, state._manifest)

  fresh = _make_coordinator(tmp_path)  # process restart
  assert fresh.phase(tar_path) == PHASE_VERIFICATION_COMPLETE
  assert fresh._get_or_create_day(tar_path).verify_stage() == (
    VERIFY_STAGE_PRE_SEAL
  )

  # A done manifest whose remaining on-disk raw is quarantined stays done.
  with state._lock:
    state._manifest["entries"][str(invalid_seg)]["status"] = (
      "skipped_quarantine"
    )
    state._manifest["phase"] = PHASE_DONE
    _save_manifest(state._manifest_path, state._manifest)
  assert _make_coordinator(tmp_path).phase(tar_path) == PHASE_DONE


def test_seal_allowed_with_remaining_raw_past_min_age(tmp_path, monkeypatch):
  """DE-26: day_close reseals a tar that gained members while raw remains.

  Allowed because every remaining raw file is already a tar member.
  """
  from hpcperfstats.dbload.lib import (
    sync_timedb_job_reconstruct as jr,
    sync_timedb_queue_orchestrator as qo,
  )

  daily = tmp_path / "daily"
  daily.mkdir()
  tar = daily / "2020-01-06.tar"
  zst = daily / "2020-01-06.tar.zst"
  raw = tmp_path / "h" / "1578312000"
  raw.parent.mkdir()
  raw.write_text("late member, already appended")
  with tarfile.open(tar, "w") as tf:
    tf.add(str(raw), arcname=get_tar_member_name(str(raw)))
  zst.write_bytes(b"old zst")
  seal_kwargs = []
  noted = []
  monkeypatch.setattr(jr, "day_close_is_complete", lambda *a, **k: False)
  monkeypatch.setattr(jr, "day_close_min_age_elapsed", lambda *a, **k: True)
  monkeypatch.setattr(qo, "_day_close_min_age_hours", lambda: 0)

  def _seal(*_a, **k):
    seal_kwargs.append(k)
    if not k.get("only_when_no_remaining_raw"):
      zst.write_bytes(b"resealed zst with late members")

  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_archive_helpers.seal_dirty_daily_archives",
    _seal,
  )

  class _Day:
    def note_sealed_archive_changed(self):
      noted.append(1)

  class _Coord:
    def __init__(self, **_kw):
      pass

    def _get_or_create_day(self, _tar):
      return _Day()

    def phase(self, _tar):
      return "verification_complete"

    def run_pre_seal_verify_sync(self, _tar):
      return True

    def run_post_seal_verify_sync(self, _tar):
      return True

    def apply_batch_delete(self, _tar):
      return 0

    def has_closed_raw_on_disk(self, _tar):
      return True

    def remaining_raw_paths_blocking_tar_drop(self, _tar):
      return {str(zst): [str(raw)]}

  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_day_raw_removal.DayRawRemovalCoordinator",
    _Coord,
  )
  monkeypatch.setattr(
    qo,
    "reconcile_open_tar_with_sealed_zst",
    lambda *a, **k: None,
    raising=False,
  )
  outcome = qo._run_day_close_job(
    "2020-01-06",
    tgz_archive_dir=str(daily),
    archive_data_dir=str(tmp_path),
    log_fn=lambda *a, **k: None,
  )
  assert outcome == "incomplete_raw"
  assert seal_kwargs and seal_kwargs[0]["only_when_no_remaining_raw"] is False
  assert seal_kwargs[0]["keep_uncompressed_tar"] is True
  assert tar.exists(), "tar kept while raw remains"
  assert noted == [1], "a reseal invalidates the old post-seal parity"


def test_late_raw_resets_pre_seal_verify_stage(tmp_path):
  """DE-26: raw appended after pre-seal verify reopens verify (T0 identity)."""
  day = datetime(2026, 8, 28)
  seg_a, late_seg = _two_segments(tmp_path, day)
  tar_path, _zst = _seal_day(tmp_path, seg_a, day)
  coord = _make_coordinator(tmp_path)
  state = coord._get_or_create_day(tar_path)
  assert coord.run_pre_seal_verify_sync(tar_path)
  assert state.pre_seal_verification_complete()
  recorded = state._manifest.get("verified_tar_identity")
  assert recorded and recorded["size"] == os.path.getsize(tar_path)

  with state._lock:  # a finished classify cursor from the first verify
    state._manifest["pre_seal_classify_index"] = 1
    state._manifest["pre_seal_classify_paths"] = [str(seg_a)]
  _append_member(tar_path, late_seg)  # late raw: ingested + appended now
  coord._get_or_create_day(tar_path)
  assert "pre_seal_classify_index" not in state._manifest, (
    "stale cursor dropped"
  )
  assert int(state._manifest.get("reverify_cycles") or 0) == 0, (
    "late raw is progress, not a repair loop"
  )

  assert coord.phase(tar_path) == PHASE_VERIFYING
  assert state.verify_stage() == "none"
  assert not state.pre_seal_verification_complete()
  coord.run_pre_seal_verify_sync(tar_path)
  assert str(late_seg) in state._manifest["entries"], "late raw classified"


# --- A7: day-close repair paths (DE-9 / DE-10 / DE-11) ---


def _cut_inside(tar_path, member_name, keep=100):
  with tarfile.open(tar_path) as tf:
    off = [m for m in tf.getmembers() if m.name == member_name][-1].offset_data
  with open(tar_path, "r+b") as fh:
    fh.truncate(off + keep)


def test_unreadable_zst_reseals_from_tar(tmp_path):
  """DE-9: a readable tar reseals over a corrupt zst, which is kept aside."""
  from hpcperfstats.dbload.lib.archive_compress import (
    classify_daily_tar_zst_reconcile,
  )

  day = datetime(2026, 9, 30)
  seg, _ = _two_segments(tmp_path, day)
  tar_path, zst = _seal_day(tmp_path, seg, day)
  with open(zst, "wb") as fh:
    fh.write(b"\x28\xb5\x2f\xfd" + b"garbage" * 10)
  assert classify_daily_tar_zst_reconcile(
    {"x": 1}, {}, zst_exists=True, zst_readable=False
  ) == ("seal_from_tar", "zst_unreadable_reseal")
  assert (
    atomic_seal_tar_to_zst(
      tar_path,
      zst,
      num_threads=1,
      compress_level=3,
      keep_uncompressed_tar=True,
      log_fn=None,
    )
    is not None
  )
  assert validate_sealed_daily_archive_for_raw_removal(zst, log_fn=None)[0]
  corrupt = [
    p for p in os.listdir(os.path.dirname(zst)) if ".zst.corrupt." in p
  ]
  assert len(corrupt) == 1, (
    "the unreadable sealed file is kept, not overwritten"
  )


def test_phase_done_divergence_reopens(tmp_path):
  """DE-10: a done day whose tar gained members reopens and then closes."""
  day = datetime(2026, 8, 30)
  sealed_seg, late_seg = _two_segments(tmp_path, day)
  tar_path, zst = _seal_day(tmp_path, sealed_seg, day)
  _append_member(tar_path, late_seg)
  coord = _make_coordinator(tmp_path)
  state = coord._get_or_create_day(tar_path)
  state._record_entry(str(sealed_seg), zst, "verified", "verified")
  state._record_entry(
    str(late_seg), zst, "skipped_seal_invalid", "seal_invalid"
  )
  with state._lock:
    state._manifest["entries"][str(sealed_seg)]["deleted"] = True
    state._manifest["phase"] = PHASE_DONE
    _save_manifest(state._manifest_path, state._manifest)
  os.remove(str(sealed_seg))
  fresh = _make_coordinator(tmp_path)
  assert fresh.phase(tar_path) == PHASE_VERIFICATION_COMPLETE
  atomic_seal_tar_to_zst(
    tar_path,
    zst,
    num_threads=1,
    compress_level=3,
    keep_uncompressed_tar=True,
    log_fn=None,
  )
  fresh.apply_batch_delete(tar_path)
  assert not late_seg.exists(), "reclassified against the resealed zst"


def test_empty_open_tar_completes(tmp_path):
  """DE-11: an empty open tar (no members, no raw) is dropped, not looped."""
  from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo

  tar = tmp_path / "2026-07-01.tar"
  with tarfile.open(tar, "w"):
    pass
  coord = MagicMock()
  coord.has_closed_raw_on_disk.return_value = False
  assert qo._drop_empty_open_tar(coord, str(tar))
  assert not tar.exists()
  garbage = tmp_path / "2026-07-02.tar"
  garbage.write_bytes(b"tar")
  assert not qo._drop_empty_open_tar(coord, str(garbage))
  assert garbage.exists()


def test_truncated_open_tar_salvaged_to_valid_prefix(tmp_path):
  """DE-11: the 01 shape (cut inside the last member) becomes readable."""
  from hpcperfstats.dbload.lib import sync_timedb_archive_helpers as ah

  day = datetime(2026, 10, 23)
  seg_a, seg_b = _two_segments(tmp_path, day)
  tar_path, zst = _seal_day(tmp_path, seg_a, day)
  os.remove(zst)
  _append_member(tar_path, seg_b)
  _cut_inside(tar_path, get_tar_member_name(str(seg_b)), keep=10)
  result = ah.ensure_open_daily_tar_readable(tar_path, caller="t", log_fn=None)
  assert result.readable
  with tarfile.open(tar_path) as tf:
    assert [m.name for m in tf.getmembers()] == [
      get_tar_member_name(str(seg_a))
    ]


def test_no_valid_prefix_tar_renamed_corrupt(tmp_path):
  """DE-11: no valid prefix and no sealed copy: rename, never delete."""
  from hpcperfstats.dbload.lib import sync_timedb_archive_helpers as ah

  day = datetime(2026, 10, 24)
  seg_a, _ = _two_segments(tmp_path, day)
  tar_path, zst = _seal_day(tmp_path, seg_a, day)
  os.remove(zst)
  original = open(tar_path, "rb").read()
  _cut_inside(tar_path, get_tar_member_name(str(seg_a)), keep=5)
  cut_bytes = open(tar_path, "rb").read()
  res = ah.salvage_truncated_tar_prefix(tar_path, log_fn=None)
  assert res.status == "renamed_corrupt"
  assert not os.path.exists(tar_path)
  assert open(res.backup_path, "rb").read() == cut_bytes
  assert original.startswith(cut_bytes[:512])


def test_day_skip_tar_not_written_until_repaired(tmp_path):
  """A7/P1RAW: a flagged truncated tar refuses appends until salvaged."""
  from hpcperfstats.dbload.lib import sync_timedb_archive_helpers as ah
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_store import (
    SyncTimedbArchiveMembersStore,
    set_process_archive_members_store,
  )

  day = datetime(2026, 10, 5)
  seg_a, seg_b = _two_segments(tmp_path, day)
  tar_path, zst = _seal_day(tmp_path, seg_a, day)
  os.remove(zst)
  _append_member(tar_path, seg_b)
  _cut_inside(tar_path, get_tar_member_name(str(seg_b)))
  store = SyncTimedbArchiveMembersStore(str(tmp_path / "m"))
  set_process_archive_members_store(store)
  try:
    store.set_day_skip("2026-10-05", kind="open_tar_gnu_tvf_failed")
    assert ah.day_skip_blocks_tar_write(tar_path, log_fn=None)
    assert ah.salvage_truncated_tar_prefix(tar_path, log_fn=None).status == (
      "salvaged"
    )
    assert not ah.day_skip_blocks_tar_write(tar_path, log_fn=None)
    assert store.get_day_skip("2026-10-05") is None
  finally:
    set_process_archive_members_store(None)


def test_reverify_cycles_bounded(tmp_path, monkeypatch):
  """A7: repeated reopens without a passing parity surface as verify_failed."""
  from hpcperfstats.dbload.lib import (
    sync_timedb_job_reconstruct as jr,
    sync_timedb_queue_orchestrator as qo,
  )

  monkeypatch.setattr(cfg, "get_sync_noprogress_escalate_after", lambda: 2)
  day = datetime(2026, 8, 31)
  seg, _ = _two_segments(tmp_path, day)
  tar_path, _zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path)
  state = coord._get_or_create_day(tar_path)
  with state._lock:
    state._manifest["reverify_cycles"] = 3
  assert state.reverify_exhausted()
  monkeypatch.setattr(jr, "day_close_is_complete", lambda *a, **k: False)
  monkeypatch.setattr(jr, "day_close_min_age_elapsed", lambda *a, **k: True)
  monkeypatch.setattr(qo, "_day_close_min_age_hours", lambda: 0)
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_day_raw_removal.DayRawRemovalCoordinator",
    lambda **_k: coord,
  )
  outcome = qo._run_day_close_job(
    "2026-08-31",
    tgz_archive_dir=str(tmp_path / "daily"),
    archive_data_dir=str(tmp_path),
    log_fn=lambda *a, **k: None,
  )
  assert outcome == "verify_failed"
  with state._lock:
    state._manifest["reverify_cycles"] = 0
  assert not state.reverify_exhausted()


def test_salvage_resets_entries_at_or_after_cut(tmp_path):
  """DL-11: a member dropped by the salvage cannot be deleted against it."""
  from hpcperfstats.dbload.lib import sync_timedb_archive_helpers as ah

  day = datetime(2026, 10, 15)
  seg_a, seg_b = _two_segments(tmp_path, day)
  tar_path, zst = _seal_day(tmp_path, seg_a, day)
  _append_member(tar_path, seg_b)
  coord = _make_coordinator(tmp_path)
  state = _verified_ready_state(coord, tar_path, zst, [seg_b])  # vs open tar
  _cut_inside(tar_path, get_tar_member_name(str(seg_b)))
  assert ah.salvage_truncated_tar_prefix(tar_path, log_fn=None).status == (
    "salvaged"
  )
  coord.apply_batch_delete(tar_path)
  assert seg_b.exists()
  assert state._manifest["entries"][str(seg_b)]["status"] == (
    "skipped_not_in_archive"
  )


# --- DE-13: reclassify decoupled from verified-pending; stable fp handoff ---


def test_reclassify_runs_with_delete_failed_pending(tmp_path):
  """DE-13: a verified entry stuck on delete_failed no longer blocks reclassify."""
  day = datetime(2026, 9, 1)
  seg_a, seg_b = _two_segments(tmp_path, day)
  tar_path, zst = _seal_day(tmp_path, seg_a, day)
  _append_member(tar_path, seg_b)
  atomic_seal_tar_to_zst(
    tar_path,
    zst,
    num_threads=1,
    compress_level=3,
    keep_uncompressed_tar=True,
    log_fn=None,
  )
  coord = _make_coordinator(tmp_path)
  state = _verified_ready_state(coord, tar_path, zst, [seg_a])
  with state._lock:
    state._manifest["entries"][str(seg_a)]["delete_failed"] = "EBUSY"
  state._record_entry(str(seg_b), zst, "skipped_seal_invalid", "seal_invalid")
  assert state._manifest_verified_pending_count() == 1
  assert state._reclassify_retryable_skips_on_disk() == 1
  assert state._manifest["entries"][str(seg_b)]["status"] == "verified"


def _sealed_only_day_with_skips(tmp_path, monkeypatch, *, in_zst):
  """A zst-only day (tar dropped) with retryable raw left in the manifest."""
  day = datetime(2026, 9, 4)
  seg_a, seg_b = _two_segments(tmp_path, day)
  tar_path, zst = _seal_day(tmp_path, seg_a, day)
  if in_zst:
    _append_member(tar_path, seg_b)
    atomic_seal_tar_to_zst(
      tar_path,
      zst,
      num_threads=1,
      compress_level=3,
      keep_uncompressed_tar=True,
      log_fn=None,
    )
  os.remove(tar_path)
  restores = []
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_day_raw_removal."
    "ensure_daily_tar_restored_for_append",
    lambda *a, **_k: restores.append(a[0]) or False,
  )
  coord = _make_coordinator(tmp_path)
  state = _verified_ready_state(coord, tar_path, zst, [])
  state._record_entry(
    str(seg_b), zst, "skipped_not_in_archive", "not_in_sealed_archive"
  )
  return state, tar_path, seg_b, restores


def test_reclassify_sealed_only_day_uses_zst_without_restore(
  tmp_path, monkeypatch
):
  """02 2026-10-30: day close restored 300-600 GB 09-04/09-06 to reclassify."""
  state, tar_path, seg_b, restores = _sealed_only_day_with_skips(
    tmp_path, monkeypatch, in_zst=True
  )
  assert state._reclassify_retryable_skips_on_disk() == 1
  assert state._manifest["entries"][str(seg_b)]["status"] == "verified"
  assert restores == []
  assert not os.path.exists(tar_path)


def test_reclassify_sealed_only_day_keeps_raw_missing_from_zst_skipped(
  tmp_path, monkeypatch
):
  state, tar_path, seg_b, restores = _sealed_only_day_with_skips(
    tmp_path, monkeypatch, in_zst=False
  )
  assert state._reclassify_retryable_skips_on_disk() == 0
  assert state._manifest["entries"][str(seg_b)]["status"] != "verified"
  assert restores == []
  assert not os.path.exists(tar_path)


def test_stable_fingerprint_changed_is_handed_off(tmp_path):
  """DE-13: a now-stable changed file is reclassified, not left terminal."""
  day = datetime(2026, 9, 2)
  seg_a, _ = _two_segments(tmp_path, day)
  tar_path, zst = _seal_day(tmp_path, seg_a, day)
  handed = []
  coord = _make_coordinator(
    tmp_path,
    on_handoff_to_ingest=lambda tar, paths, reason: (
      handed.extend(paths) or True
    ),
  )
  state = _verified_ready_state(coord, tar_path, zst, [])
  seg_a.write_text(seg_a.read_text() + "grew after verify\n")  # size differs
  state._record_entry(
    str(seg_a), zst, "skipped_fingerprint_changed", "fingerprint_changed"
  )
  coord.apply_batch_delete(tar_path)
  status = state._manifest["entries"][str(seg_a)]["status"]
  assert status != "skipped_fingerprint_changed"
  assert seg_a.exists(), "never deleted against a stale tar member"
  assert not state._all_closed_raw_terminal_or_gone()


# --- gap 11: production-shaped frozen day end to end; ENOSPC injection ---


def test_frozen_production_manifest_closes_end_to_end(tmp_path, monkeypatch):
  """04 2026-08-23 shape: done + tar ⊋ zst + seal_invalid raw closes.

  Real ``_run_day_close_job`` + real coordinator + real tar/zst; only the
  DB head/tail gate and min-age are stubbed.
  """
  from hpcperfstats.dbload.lib import (
    sync_timedb_job_reconstruct as jr,
    sync_timedb_queue_orchestrator as qo,
  )

  day = datetime(2026, 8, 23)
  host = tmp_path / "n.cluster.integration.test"
  host.mkdir(parents=True)
  segs = []
  for hour in (1, 2, 3, 4):
    ts = int(datetime(day.year, day.month, day.day, hour).timestamp())
    seg = host / str(ts)
    seg.write_text(f"{ts} job1 cn001\nline {hour}\n")
    os.utime(seg, (ts, ts))
    segs.append(seg)
  tar_path, zst = _seal_day(tmp_path, segs[0], day)
  _append_member(tar_path, segs[1])
  atomic_seal_tar_to_zst(
    tar_path,
    zst,
    num_threads=1,
    compress_level=3,
    keep_uncompressed_tar=True,
    log_fn=None,
  )
  for seg in segs[2:]:  # appended after the last seal: tar ⊋ zst
    _append_member(tar_path, seg)
  coord = _make_coordinator(tmp_path)
  state = coord._get_or_create_day(tar_path)
  for seg in segs[:2]:
    state._record_entry(str(seg), zst, "verified", "verified")
  for seg in segs[2:]:
    state._record_entry(str(seg), zst, "skipped_seal_invalid", "seal_invalid")
  with state._lock:
    for seg in segs[:2]:
      state._manifest["entries"][str(seg)]["deleted"] = True
    state._manifest["phase"] = PHASE_DONE
    state._manifest["verify_stage"] = VERIFY_STAGE_POST_SEAL
    _save_manifest(state._manifest_path, state._manifest)
  for seg in segs[:2]:
    seg.unlink()
  monkeypatch.setattr(
    cfg, "get_host_name_ext", lambda: "cluster.integration.test"
  )
  monkeypatch.setattr(jr, "day_close_min_age_elapsed", lambda *a, **k: True)
  monkeypatch.setattr(qo, "_day_close_min_age_hours", lambda: 0)
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_ingest_readiness."
    "stats_file_head_ingested_in_db",
    lambda _p: True,
  )
  outcomes = []
  for _claim in range(4):
    outcomes.append(
      qo._run_day_close_job(
        "2026-08-23",
        tgz_archive_dir=str(tmp_path / "daily"),
        archive_data_dir=str(tmp_path),
        log_fn=lambda *a, **k: None,
      )
    )
    if outcomes[-1] == "complete":
      break
  assert outcomes[-1] == "complete", outcomes
  assert not any(seg.exists() for seg in segs), "every raw deleted"
  members = validate_sealed_daily_archive_for_raw_removal(zst, log_fn=None)[1]
  assert members and all(
    get_tar_member_name(str(seg)) in members for seg in segs
  ), "every deleted raw is in the sealed archive (INV-COPY)"


def test_enospc_injection_append_seal_rebuild_salvage_no_loss(
  tmp_path, monkeypatch
):
  """A16: with no space, seal/rebuild/salvage change nothing on disk."""
  from hpcperfstats.dbload.lib import sync_timedb_archive_helpers as ah

  day = datetime(2026, 10, 23)
  seg_a, seg_b = _two_segments(tmp_path, day)
  tar_path, zst = _seal_day(tmp_path, seg_a, day)
  os.remove(zst)
  _append_member(tar_path, seg_b)
  monkeypatch.setattr(cfg, "get_sync_archive_min_free_bytes", lambda: 1 << 62)
  intact = open(tar_path, "rb").read()
  assert atomic_seal_tar_to_zst(
    tar_path,
    zst,
    num_threads=1,
    compress_level=3,
    keep_uncompressed_tar=True,
    log_fn=None,
  ) is None and not os.path.exists(zst)
  assert not ah.rebuild_daily_tar_member_union_in_place(
    tar_path, zst, {get_tar_member_name(str(seg_a)): 1}, log_fn=None
  )
  assert open(tar_path, "rb").read() == intact
  _cut_inside(tar_path, get_tar_member_name(str(seg_b)))
  cut = open(tar_path, "rb").read()
  assert ah.salvage_truncated_tar_prefix(tar_path, log_fn=None).status == (
    "space_denied"
  )
  assert open(tar_path, "rb").read() == cut
  assert seg_a.exists() and seg_b.exists()
