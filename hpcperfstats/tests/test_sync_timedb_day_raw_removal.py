"""Unit tests for per-day post-seal raw removal coordinator."""

import json
import os
import tarfile
import time
from datetime import datetime
from unittest.mock import MagicMock

import pytest

import hpcperfstats.conf_parser as cfg
from hpcperfstats.dbload.sync_timedb_archive_helpers import (
    atomic_seal_tar_to_zst,
    daily_tar_path_from_compressed,
    get_tar_member_name,
    validate_sealed_daily_archive_for_raw_removal,
)
from hpcperfstats.dbload.sync_timedb_day_raw_removal import (
    PHASE_DONE,
    PHASE_VERIFICATION_COMPLETE,
    PHASE_VERIFYING,
    DayRawRemovalCoordinator,
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
  from hpcperfstats.dbload.sync_timedb_day_raw_removal import _save_manifest

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


def test_start_async_day_pipeline_completes_verify_and_delete(tmp_path, monkeypatch):
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
  assert coord.phase(tar_path) == PHASE_DONE
  assert not seg.is_file()
  assert completed == [os.path.normpath(tar_path)]


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

  coord._pipeline_body(state)
  messages = [str(call.args[0]) for call in log_fn.call_args_list]
  assert any("Day raw removal verify budget exhausted" in msg for msg in messages)
