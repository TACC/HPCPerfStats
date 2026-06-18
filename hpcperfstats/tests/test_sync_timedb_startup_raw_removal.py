"""Unit tests for startup raw removal preflight (async verify, gated delete)."""

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
from hpcperfstats.dbload.lib.sync_timedb_startup_raw_removal import (
    PHASE_DONE,
    PHASE_VERIFICATION_COMPLETE,
    PHASE_VERIFYING,
    StartupRawRemovalPreflight,
    manifest_path,
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
  return zst_key


def _make_preflight(tmp_path, arch_suffix="cluster.integration.test", **kwargs):
  defaults = {
      "archive_data_dir": str(tmp_path),
      "host_name_ext": arch_suffix,
      "tgz_archive_dir": str(tmp_path / "daily"),
      "log_fn": MagicMock(),
      "get_disqualified_daily_tars": lambda: set(),
      "get_quarantine_skip_paths": lambda: set(),
      "ingest_ready_fn": lambda _p: True,
  }
  defaults.update(kwargs)
  preflight = StartupRawRemovalPreflight(**defaults)
  preflight.enabled = True
  return preflight


def test_startup_preflight_verifies_without_deleting(tmp_path, monkeypatch):
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", datetime(2022, 5, 3))
  _seal_day(tmp_path, seg, datetime(2022, 5, 3))
  preflight = _make_preflight(tmp_path)
  monkeypatch.setattr(cfg, "get_sync_startup_raw_removal_verify_days_per_slice", lambda: 10)
  monkeypatch.setattr(cfg, "get_sync_startup_raw_removal_verify_budget_seconds", lambda: 30.0)

  assert preflight._verify_slice()
  assert seg.is_file()
  assert preflight.phase() == PHASE_VERIFICATION_COMPLETE
  manifest = preflight._manifest
  entry = manifest["entries"][str(seg)]
  assert entry["status"] == "verified"
  assert not entry.get("deleted")


def test_startup_preflight_sealed_only_skips_unsealed_day(tmp_path, monkeypatch):
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", datetime(2022, 5, 4))
  (tmp_path / "daily").mkdir(exist_ok=True)
  preflight = _make_preflight(tmp_path)
  monkeypatch.setattr(cfg, "get_sync_startup_raw_removal_verify_days_per_slice", lambda: 10)
  monkeypatch.setattr(cfg, "get_sync_startup_raw_removal_verify_budget_seconds", lambda: 30.0)

  assert preflight._verify_slice()
  entry = preflight._manifest["entries"][str(seg)]
  assert entry["status"] == "skipped_seal_invalid"
  assert seg.is_file()


def test_startup_preflight_db_gate_excludes_not_head_ingested(tmp_path, monkeypatch):
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", datetime(2022, 5, 5))
  _seal_day(tmp_path, seg, datetime(2022, 5, 5))
  preflight = _make_preflight(
      tmp_path,
      ingest_ready_fn=lambda _p: False,
  )
  monkeypatch.setattr(cfg, "get_sync_archive_require_db_head_ingest", lambda: True)
  monkeypatch.setattr(cfg, "get_sync_startup_raw_removal_verify_days_per_slice", lambda: 10)
  monkeypatch.setattr(cfg, "get_sync_startup_raw_removal_verify_budget_seconds", lambda: 30.0)

  assert preflight._verify_slice()
  entry = preflight._manifest["entries"][str(seg)]
  assert entry["status"] == "skipped_not_sample_ingested"
  verified = [
      e for e in preflight._manifest["entries"].values()
      if e.get("status") == "verified"
  ]
  assert not verified


def test_apply_deletes_from_manifest_removes_verified(tmp_path, monkeypatch):
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", datetime(2022, 5, 6))
  _seal_day(tmp_path, seg, datetime(2022, 5, 6))
  preflight = _make_preflight(tmp_path)
  monkeypatch.setattr(cfg, "get_sync_startup_raw_removal_verify_days_per_slice", lambda: 10)
  monkeypatch.setattr(cfg, "get_sync_startup_raw_removal_verify_budget_seconds", lambda: 30.0)
  monkeypatch.setattr(cfg, "get_sync_startup_raw_removal_max_deletes_per_pass", lambda: 0)

  preflight._verify_slice()
  preflight.begin_deleting()
  deleted = preflight.apply_deletes_from_manifest()
  assert deleted == 1
  assert not seg.is_file()
  assert preflight.phase() == PHASE_DONE


def test_apply_deletes_skips_fingerprint_changed(tmp_path, monkeypatch):
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", datetime(2022, 5, 7))
  _seal_day(tmp_path, seg, datetime(2022, 5, 7))
  preflight = _make_preflight(tmp_path)
  monkeypatch.setattr(cfg, "get_sync_startup_raw_removal_verify_days_per_slice", lambda: 10)
  monkeypatch.setattr(cfg, "get_sync_startup_raw_removal_verify_budget_seconds", lambda: 30.0)
  monkeypatch.setattr(cfg, "get_sync_startup_raw_removal_max_deletes_per_pass", lambda: 0)

  preflight._verify_slice()
  preflight.begin_deleting()
  seg.write_text("mutated content changes size\n")
  deleted = preflight.apply_deletes_from_manifest()
  assert deleted == 0
  assert seg.is_file()
  entry = preflight._manifest["entries"][str(seg)]
  assert entry["status"] == "skipped_fingerprint_changed"


def test_verify_slice_persists_manifest_after_each_day(tmp_path, monkeypatch):
  seg_a = _make_closed_segment(tmp_path, "cluster.integration.test", datetime(2022, 5, 8))
  seg_b = _make_closed_segment(tmp_path, "cluster.integration.test", datetime(2022, 5, 9))
  _seal_day(tmp_path, seg_a, datetime(2022, 5, 8))
  _seal_day(tmp_path, seg_b, datetime(2022, 5, 9))
  preflight = _make_preflight(tmp_path)
  monkeypatch.setattr(cfg, "get_sync_startup_raw_removal_verify_days_per_slice", lambda: 1)
  monkeypatch.setattr(cfg, "get_sync_startup_raw_removal_verify_budget_seconds", lambda: 30.0)

  assert not preflight._verify_slice()
  on_disk = manifest_path(str(tmp_path))
  assert os.path.isfile(on_disk)
  import json

  with open(on_disk, encoding="utf-8") as handle:
    payload = json.load(handle)
  assert payload["entries"]
  assert len(payload.get("pending_gz_paths", [])) == 1


def test_manifest_persists_under_archive_dir(tmp_path):
  preflight = _make_preflight(tmp_path)
  preflight._manifest["phase"] = PHASE_VERIFYING
  from hpcperfstats.dbload.lib.sync_timedb_startup_raw_removal import _save_manifest

  _save_manifest(manifest_path(str(tmp_path)), preflight._manifest)
  assert os.path.isfile(manifest_path(str(tmp_path)))
