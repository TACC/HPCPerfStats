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
