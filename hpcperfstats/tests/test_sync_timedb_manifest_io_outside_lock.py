"""Manifest save outside lock (contention wave manifest_io)."""
from __future__ import annotations

import os
from pathlib import Path
from unittest import mock


def test_clear_deferred_saves_manifest_outside_lock(tmp_path: Path) -> None:
  """
  clear_deferred must release the coordinator lock before disk save.
  """
  from hpcperfstats.dbload.lib.sync_timedb_day_close_manifest import (
      DayCloseManifestCoordinator,
  )
  import hpcperfstats.dbload.lib.sync_timedb_day_close_manifest as mod

  archive_dir = str(tmp_path / "archive")
  daily_dir = str(tmp_path / "daily")
  os.makedirs(archive_dir)
  os.makedirs(daily_dir)
  coord = DayCloseManifestCoordinator(
      archive_data_dir=archive_dir,
      host_name_ext="",
      tgz_archive_dir=daily_dir,
      local_tz=None,
      log_fn=lambda *_a, **_k: None,
      get_disqualified_daily_tars=lambda: set(),
  )
  tar = str(Path(daily_dir) / "2024-01-01.tar")
  with coord._lock:
    coord._manifest.setdefault("entries", {})[tar] = {
        "tar_path": tar,
        "status": "deferred",
        "detail": "waiting_on_ingest",
    }

  held = {"value": True}

  def _save(path, payload):
    del path, payload
    held["value"] = coord._lock.locked()

  with mock.patch.object(mod, "_save_manifest", side_effect=_save):
    assert coord.clear_deferred_waiting_on_ingest(tar) is True
  assert held["value"] is False
