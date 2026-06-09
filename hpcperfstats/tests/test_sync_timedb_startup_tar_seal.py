"""Unit tests for startup quiescent daily tar seal preflight."""

import json
import os
import tarfile
from datetime import date, datetime
from unittest.mock import MagicMock

import pytest

import hpcperfstats.conf_parser as cfg
from hpcperfstats.dbload.sync_timedb_archive_helpers import (
    daily_tar_path_from_compressed,
    get_tar_member_name,
)
from hpcperfstats.dbload.sync_timedb_startup_tar_seal import (
    PHASE_DONE,
    PHASE_SEALING,
    StartupTarSealPreflight,
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


def _make_quiescent_tar(tmp_path, day, *, member_seg=None):
  tgz_dir = tmp_path / "daily"
  tgz_dir.mkdir(exist_ok=True)
  zst_key = str(tgz_dir / ("%04d-%02d-%02d.tar.zst" % (day.year, day.month, day.day)))
  tar_path = daily_tar_path_from_compressed(zst_key)
  with tarfile.open(tar_path, "w") as tf:
    if member_seg is not None:
      tf.add(str(member_seg), arcname=get_tar_member_name(str(member_seg)))
    else:
      placeholder = tmp_path / "placeholder.txt"
      placeholder.write_text("placeholder\n")
      tf.add(str(placeholder), arcname="placeholder.txt")
  return tar_path, zst_key


def _make_preflight(tmp_path, arch_suffix="cluster.integration.test", **kwargs):
  import zoneinfo

  defaults = {
      "archive_data_dir": str(tmp_path),
      "host_name_ext": arch_suffix,
      "tgz_archive_dir": str(tmp_path / "daily"),
      "local_tz": zoneinfo.ZoneInfo("UTC"),
      "log_fn": MagicMock(),
      "has_active_append_for_tar": lambda _tar: False,
  }
  defaults.update(kwargs)
  preflight = StartupTarSealPreflight(**defaults)
  preflight.enabled = True
  return preflight


def test_quiescent_daily_tar_eligible_when_no_remaining_raw(tmp_path, monkeypatch):
  day = date(2022, 6, 1)
  tar_path, _zst = _make_quiescent_tar(tmp_path, day)
  preflight = _make_preflight(tmp_path)
  monkeypatch.setattr(cfg, "get_sync_startup_tar_seal_days_per_slice", lambda: 10)
  monkeypatch.setattr(cfg, "get_sync_startup_tar_seal_budget_seconds", lambda: 60.0)
  monkeypatch.setattr(cfg, "get_archive_keep_uncompressed_tar", lambda: False)

  pending = preflight._discover_pending_tar_paths()
  assert os.path.normpath(tar_path) in pending


def test_quiescent_daily_tar_skipped_when_raw_on_disk(tmp_path, monkeypatch):
  day = date(2022, 6, 2)
  seg = _make_closed_segment(tmp_path, "cluster.integration.test", day)
  tar_path, _zst = _make_quiescent_tar(tmp_path, day, member_seg=seg)
  preflight = _make_preflight(tmp_path)
  monkeypatch.setattr(cfg, "get_sync_startup_tar_seal_days_per_slice", lambda: 10)
  monkeypatch.setattr(cfg, "get_sync_startup_tar_seal_budget_seconds", lambda: 60.0)

  pending = preflight._discover_pending_tar_paths()
  assert os.path.normpath(tar_path) not in pending
  entry = preflight._manifest["entries"][os.path.normpath(tar_path)]
  assert entry["status"] == "skipped_remaining_raw"
  assert seg.is_file()


def test_startup_tar_seal_calls_atomic_seal_and_drops_tar(tmp_path, monkeypatch):
  day = date(2022, 6, 3)
  tar_path, zst_path = _make_quiescent_tar(tmp_path, day)
  preflight = _make_preflight(tmp_path)
  monkeypatch.setattr(cfg, "get_sync_startup_tar_seal_days_per_slice", lambda: 10)
  monkeypatch.setattr(cfg, "get_sync_startup_tar_seal_budget_seconds", lambda: 120.0)
  monkeypatch.setattr(cfg, "get_archive_keep_uncompressed_tar", lambda: False)
  monkeypatch.setattr(cfg, "get_archive_zstd_threads", lambda: 1)
  monkeypatch.setattr(cfg, "get_archive_zstd_level", lambda: 1)

  assert preflight._seal_slice()
  assert preflight.phase() == PHASE_DONE
  assert os.path.isfile(zst_path)
  assert not os.path.isfile(tar_path)
  entry = preflight._manifest["entries"][os.path.normpath(tar_path)]
  assert entry["status"] == "sealed"


def test_startup_tar_seal_skips_day_with_inflight_append(tmp_path, monkeypatch):
  day = date(2022, 6, 4)
  tar_path, _zst = _make_quiescent_tar(tmp_path, day)
  tar_norm = os.path.normpath(tar_path)
  preflight = _make_preflight(
      tmp_path,
      has_active_append_for_tar=lambda tar: tar == tar_norm,
  )
  monkeypatch.setattr(cfg, "get_sync_startup_tar_seal_days_per_slice", lambda: 10)
  monkeypatch.setattr(cfg, "get_sync_startup_tar_seal_budget_seconds", lambda: 60.0)

  pending = preflight._discover_pending_tar_paths()
  assert tar_norm not in pending
  entry = preflight._manifest["entries"][tar_norm]
  assert entry["status"] == "skipped_active_append"
  assert entry["reason"] == "active_append"


def test_startup_tar_seal_resumes_from_manifest(tmp_path, monkeypatch):
  day_a = date(2022, 6, 5)
  day_b = date(2022, 6, 6)
  tar_a, _ = _make_quiescent_tar(tmp_path, day_a)
  tar_b, _ = _make_quiescent_tar(tmp_path, day_b)
  preflight = _make_preflight(tmp_path)
  monkeypatch.setattr(cfg, "get_sync_startup_tar_seal_days_per_slice", lambda: 1)
  monkeypatch.setattr(cfg, "get_sync_startup_tar_seal_budget_seconds", lambda: 120.0)
  monkeypatch.setattr(cfg, "get_archive_keep_uncompressed_tar", lambda: False)
  monkeypatch.setattr(cfg, "get_archive_zstd_threads", lambda: 1)
  monkeypatch.setattr(cfg, "get_archive_zstd_level", lambda: 1)

  assert not preflight._seal_slice()
  assert preflight.phase() == PHASE_SEALING
  on_disk = manifest_path(str(tmp_path))
  with open(on_disk, encoding="utf-8") as handle:
    payload = json.load(handle)
  assert len(payload.get("pending_tar_paths", [])) == 1

  preflight2 = _make_preflight(tmp_path)
  assert preflight2._seal_slice()
  assert preflight2.phase() == PHASE_DONE
  assert not os.path.isfile(tar_a)
  assert not os.path.isfile(tar_b)


def test_startup_tar_seal_defers_calendar_today_in_grace(tmp_path, monkeypatch):
  import zoneinfo
  from hpcperfstats.dbload import sync_timedb_startup_tar_seal as tar_seal_mod

  today = datetime.now(zoneinfo.ZoneInfo("UTC")).date()
  tar_path, _zst = _make_quiescent_tar(tmp_path, today)
  preflight = _make_preflight(tmp_path, local_tz=zoneinfo.ZoneInfo("UTC"))
  monkeypatch.setattr(
      tar_seal_mod,
      "daily_tar_seal_calendar_eligible",
      lambda _tar, _tz, now=None: False,
  )

  pending = preflight._discover_pending_tar_paths()
  assert os.path.normpath(tar_path) not in pending
  entry = preflight._manifest["entries"][os.path.normpath(tar_path)]
  assert entry["status"] == "skipped_calendar_grace"


def test_manifest_persists_under_archive_dir(tmp_path):
  preflight = _make_preflight(tmp_path)
  preflight._manifest["phase"] = PHASE_SEALING
  from hpcperfstats.dbload.sync_timedb_startup_tar_seal import _save_manifest

  _save_manifest(manifest_path(str(tmp_path)), preflight._manifest)
  assert os.path.isfile(manifest_path(str(tmp_path)))


def test_seal_slice_retries_when_all_skipped_but_dirty_tar_remains(
    tmp_path, monkeypatch,
):
  day = date(2022, 6, 7)
  tar_path, _zst = _make_quiescent_tar(tmp_path, day)
  preflight = _make_preflight(
      tmp_path,
      has_active_append_for_tar=lambda _tar: True,
  )
  monkeypatch.setattr(cfg, "get_sync_startup_tar_seal_days_per_slice", lambda: 1)
  monkeypatch.setattr(cfg, "get_sync_startup_tar_seal_budget_seconds", lambda: 30.0)

  assert preflight._seal_slice() is False
  assert preflight.phase() == PHASE_SEALING
  assert os.path.isfile(tar_path)


def test_start_async_seal_resumes_when_manifest_done_but_dirty_tar_remains(
    tmp_path, monkeypatch,
):
  day = date(2022, 6, 8)
  _make_quiescent_tar(tmp_path, day)
  preflight = _make_preflight(tmp_path)
  preflight._manifest["phase"] = PHASE_DONE
  from hpcperfstats.dbload.sync_timedb_startup_tar_seal import _save_manifest

  _save_manifest(manifest_path(str(tmp_path)), preflight._manifest)
  preflight2 = _make_preflight(tmp_path)
  preflight2.start_async_seal()
  assert preflight2.phase() == PHASE_SEALING
  preflight2.shutdown(wait=True)
