"""Regression tests for AsyncDayCloseCoordinator."""
from __future__ import annotations

import concurrent.futures
import os
from unittest import mock

import pytest

from hpcperfstats.dbload import sync_timedb_async_day_close as async_dc_mod


@pytest.mark.django_db(databases=[])
def test_submit_day_close_does_not_deadlock_on_manifest_touch(tmp_path, monkeypatch):
  """submit_day_close must not re-acquire ``_lock`` via ``_touch_manifest``."""
  archive_dir = str(tmp_path / "archive")
  os.makedirs(archive_dir)
  logs: list[str] = []

  def log_fn(msg, **_kwargs):
    logs.append(str(msg))

  monkeypatch.setattr(async_dc_mod.cfg, "get_sync_day_close_async_workers", lambda: 1)

  coord = async_dc_mod.AsyncDayCloseCoordinator(
      archive_data_dir=archive_dir,
      host_name_ext="",
      tgz_archive_dir=str(tmp_path / "daily"),
      local_tz=None,
      log_fn=log_fn,
      get_disqualified_daily_tars=lambda: set(),
  )
  tar_path = str(tmp_path / "daily" / "2020-01-01.tar")
  os.makedirs(os.path.dirname(tar_path), exist_ok=True)

  with mock.patch.object(coord, "_run_day_close", return_value=None):
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
      fut = pool.submit(
          coord.submit_day_close,
          tar_path,
          reason="test",
          disqualified_daily_tars=set(),
      )
      assert fut.result(timeout=2.0) is True

  assert any("async day_close submit" in line for line in logs)
  manifest = async_dc_mod._load_manifest(coord._manifest_path)
  assert manifest.get("last_progress") == "submitted"


@pytest.mark.django_db(databases=[])
def test_seal_day_build_remaining_raw_uses_archive_dir_not_tar_path(
    tmp_path, monkeypatch,
):
  """``_seal_day`` must pass ``archive_data_dir`` before ``tar_path`` to the helper."""
  archive_dir = str(tmp_path / "archive")
  daily_dir = str(tmp_path / "daily")
  os.makedirs(archive_dir)
  os.makedirs(daily_dir)
  tar_path = os.path.join(daily_dir, "2026-04-23.tar")
  open(tar_path, "wb").close()

  captured = []

  def capture_build(archive_data_dir, host_name_ext, tgz_archive_dir, tar_norm, **_kw):
    captured.append(
        (archive_data_dir, host_name_ext, tgz_archive_dir, tar_norm),
    )
    return {}

  monkeypatch.setattr(
      async_dc_mod,
      "build_remaining_raw_for_daily_tar",
      capture_build,
  )
  monkeypatch.setattr(
      async_dc_mod,
      "daily_tar_seal_calendar_eligible",
      lambda *_a, **_k: True,
  )
  monkeypatch.setattr(
      async_dc_mod,
      "atomic_seal_tar_to_zst",
      lambda *args, **kwargs: None,
  )
  monkeypatch.setattr(async_dc_mod.cfg, "get_archive_zstd_threads", lambda: 0)
  monkeypatch.setattr(async_dc_mod.cfg, "get_archive_zstd_level", lambda: 3)

  coord = async_dc_mod.AsyncDayCloseCoordinator(
      archive_data_dir=archive_dir,
      host_name_ext=".vista.tacc.utexas.edu",
      tgz_archive_dir=daily_dir,
      local_tz=None,
      log_fn=lambda *_a, **_k: None,
      get_disqualified_daily_tars=lambda: set(),
  )
  coord._seal_day(os.path.normpath(tar_path))

  assert captured == [
      (archive_dir, ".vista.tacc.utexas.edu", daily_dir, os.path.normpath(tar_path)),
  ]


@pytest.mark.django_db(databases=[])
def test_tar_drop_day_build_remaining_raw_uses_archive_dir_not_tar_path(
    tmp_path, monkeypatch,
):
  """``_tar_drop_day`` must use the same ``build_remaining_raw_for_daily_tar`` arg order."""
  archive_dir = str(tmp_path / "archive")
  daily_dir = str(tmp_path / "daily")
  os.makedirs(archive_dir)
  os.makedirs(daily_dir)
  tar_path = os.path.join(daily_dir, "2026-04-23.tar")
  zst_path = os.path.join(daily_dir, "2026-04-23.tar.zst")
  open(tar_path, "wb").close()
  open(zst_path, "wb").close()

  captured = []

  def capture_build(archive_data_dir, host_name_ext, tgz_archive_dir, tar_norm, **_kw):
    captured.append(
        (archive_data_dir, host_name_ext, tgz_archive_dir, tar_norm),
    )
    return {zst_path: []}

  monkeypatch.setattr(
      async_dc_mod,
      "build_remaining_raw_for_daily_tar",
      capture_build,
  )
  monkeypatch.setattr(
      async_dc_mod,
      "remove_verified_uncompressed_daily_tars",
      lambda *args, **kwargs: None,
  )

  coord = async_dc_mod.AsyncDayCloseCoordinator(
      archive_data_dir=archive_dir,
      host_name_ext=".vista.tacc.utexas.edu",
      tgz_archive_dir=daily_dir,
      local_tz=None,
      log_fn=lambda *_a, **_k: None,
      get_disqualified_daily_tars=lambda: set(),
  )
  coord._tar_drop_day(os.path.normpath(tar_path))

  assert captured == [
      (archive_dir, ".vista.tacc.utexas.edu", daily_dir, os.path.normpath(tar_path)),
  ]
