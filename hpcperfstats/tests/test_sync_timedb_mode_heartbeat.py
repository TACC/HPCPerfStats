"""Unit contracts R26-R30/R32 for current-mode ingest coordination."""
import json
import os
from datetime import date, timedelta

import pytest

from hpcperfstats.dbload.lib import sync_timedb_mode_heartbeat as heartbeat


class _FakeRedis:
  def __init__(self):
    self.values = {}

  def set(self, key, value, ex=None):
    self.values[key] = value
    self.expires_in = ex

  def get(self, key):
    return self.values.get(key)


def _stats_path(tmp_path, epoch):
  path = tmp_path / "host" / str(epoch)
  path.parent.mkdir(exist_ok=True)
  path.write_text("stats\n", encoding="utf-8")
  return str(path)


def test_r26_publish_uses_active_set_minimum_not_pending_minimum(tmp_path):
  """R26: only active paths determine the oldest heartbeat calendar day."""
  archive_dir = tmp_path / "archive"
  daily_archive_dir = tmp_path / "daily"
  active_paths = [
      _stats_path(tmp_path, 1_704_153_600),  # 2024-01-02 UTC-ish local day
      _stats_path(tmp_path, 1_704_326_400),
  ]
  redis_client = _FakeRedis()

  payload = heartbeat.publish_current_heartbeat(
      archive_dir=str(archive_dir),
      daily_archive_dir=str(daily_archive_dir),
      active_paths=active_paths,
      now=100.0,
      redis_client=redis_client,
  )

  assert payload["oldest_active_day"] == heartbeat.calendar_day_from_stats_path(
      active_paths[0], str(daily_archive_dir),
  ).isoformat()
  assert payload["oldest_active_day"] != "1970-01-01"
  assert payload["writer_pid"] == os.getpid()
  assert redis_client.expires_in == 600


@pytest.mark.parametrize("contents", [None, "{not-json"])
def test_r27_missing_or_corrupt_heartbeat_fails_open(tmp_path, contents):
  archive_dir = tmp_path / "archive"
  archive_dir.mkdir()
  sidecar = archive_dir / ".sync_timedb_current_heartbeat.json"
  if contents is not None:
    sidecar.write_text(contents, encoding="utf-8")

  value = heartbeat.read_current_heartbeat(
      archive_dir=str(archive_dir),
      redis_client=_FakeRedis(),
  )

  assert value is None
  assert not heartbeat.should_backlog_exit_for_current_proximity(
      next_pending_day=date(2024, 1, 2),
      heartbeat=value,
      proximity_days=2,
  )


def test_r28_sidecar_is_archive_local_and_written_atomically(tmp_path, monkeypatch):
  archive_dir = tmp_path / "archive"
  daily_archive_dir = tmp_path / "daily"
  path = _stats_path(tmp_path, 1_704_153_600)
  replaces = []
  real_replace = heartbeat.os.replace

  def recording_replace(source, destination):
    assert os.path.exists(source)
    replaces.append((source, destination))
    return real_replace(source, destination)

  monkeypatch.setattr(heartbeat.os, "replace", recording_replace)
  heartbeat.publish_current_heartbeat(
      archive_dir=str(archive_dir),
      daily_archive_dir=str(daily_archive_dir),
      active_paths=[path],
      now=100.0,
      redis_client=None,
  )

  sidecar = archive_dir / ".sync_timedb_current_heartbeat.json"
  assert sidecar.is_file()
  assert json.loads(sidecar.read_text(encoding="utf-8"))["written_at"] == 100.0
  assert replaces == [(replaces[0][0], str(sidecar))]
  assert not list(archive_dir.glob(".sync_timedb_current_heartbeat.*.tmp"))


@pytest.mark.parametrize("delta, expected", [(-3, False), (-2, True), (0, True), (2, True), (3, False)])
def test_r29_proximity_boundary_is_inclusive_calendar_days(delta, expected):
  assert heartbeat.should_backlog_exit_for_current_proximity(
      next_pending_day=date(2024, 1, 10),
      heartbeat={
          "oldest_active_day": (date(2024, 1, 10) + timedelta(days=delta)).isoformat(),
          "written_at": 100.0,
      },
      proximity_days=2,
  ) is expected


def test_r30_stale_written_at_is_ignored(tmp_path):
  archive_dir = tmp_path / "archive"
  archive_dir.mkdir()
  sidecar = archive_dir / ".sync_timedb_current_heartbeat.json"
  sidecar.write_text(
      json.dumps({"oldest_active_day": "2024-01-10", "written_at": 1.0, "writer_pid": 1}),
      encoding="utf-8",
  )

  assert heartbeat.read_current_heartbeat(
      archive_dir=str(archive_dir),
      max_age_s=10,
      now=20.0,
      redis_client=_FakeRedis(),
  ) is None


def test_r32_module_import_path_and_transient_sidecar(tmp_path):
  assert heartbeat.__name__ == "hpcperfstats.dbload.lib.sync_timedb_mode_heartbeat"
  assert ".sync_timedb_current_heartbeat.json" not in heartbeat.PERSISTENCE_ARTIFACT_REGISTRY.values()
