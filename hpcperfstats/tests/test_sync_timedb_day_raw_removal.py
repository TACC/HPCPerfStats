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
    PHASE_DONE,
    PHASE_VERIFICATION_COMPLETE,
    PHASE_VERIFYING,
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


def test_any_blocks_startup_drain_false_when_only_retryable_skips_remain(tmp_path):
  day = datetime(2022, 6, 12)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, _zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path, ingest_ready_fn=lambda _p: False)
  state = coord._get_or_create_day(tar_path)
  state._verify_body()
  assert coord.any_needs_delete_phase()
  assert not coord.any_blocks_startup_drain()
  assert state.waiting_on_ingest_at_startup()
  assert coord.count_days_waiting_on_ingest() == 1
  state._mark_done_waiting_on_ingest()
  assert not coord.any_needs_delete_phase()
  assert not coord.any_blocks_startup_drain()
  assert coord.count_days_waiting_on_ingest() == 0
  assert seg.is_file()


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
  assert not coord.any_blocks_startup_drain()
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


def test_pipeline_verify_budget_exhausted_logs_warning(tmp_path, monkeypatch):
  day = datetime(2022, 6, 8)
  tar_path = str(tmp_path / "daily" / "2022-06-08.tar")
  (tmp_path / "daily").mkdir(exist_ok=True)
  open(tar_path, "wb").close()
  log_fn = MagicMock()
  coord = _make_coordinator(tmp_path, log_fn=log_fn)
  state = coord._get_or_create_day(tar_path)

  monkeypatch.setattr(cfg, "get_sync_day_close_raw_removal_verify_budget_seconds", lambda: 0.01)
  monkeypatch.setattr(state, "verification_complete", lambda: False)
  monkeypatch.setattr(state, "_verify_body", lambda: None)

  coord._verify_pipeline_body(state)
  messages = [str(call.args[0]) for call in log_fn.call_args_list]
  assert any("Day raw removal verify budget exhausted" in msg for msg in messages)


def test_try_finish_tar_drop_drops_tar_when_raw_gone_and_phase_done(tmp_path):
  day = datetime(2022, 6, 14)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, _zst = _seal_day(tmp_path, seg, day)
  coord = _make_coordinator(tmp_path, ingest_ready_fn=lambda _p: False)
  state = coord._get_or_create_day(tar_path)
  state._verify_body()
  state._mark_done_waiting_on_ingest()
  assert coord.phase(tar_path) == PHASE_DONE
  assert os.path.isfile(tar_path)
  seg.unlink()
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
  assert coord.phase(tar_path) == PHASE_DONE
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
