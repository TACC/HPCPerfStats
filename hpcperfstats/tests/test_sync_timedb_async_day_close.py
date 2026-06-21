"""Regression tests for AsyncDayCloseCoordinator."""
from __future__ import annotations

import concurrent.futures
import json
import os
import time
from unittest import mock

import pytest

from hpcperfstats.dbload.lib import sync_timedb_async_day_close as async_dc_mod


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


@pytest.mark.django_db(databases=[])
def test_run_day_close_raw_removal_verify_wait_times_out_and_defers(tmp_path, monkeypatch):
  archive_dir = str(tmp_path / "archive")
  daily_dir = str(tmp_path / "daily")
  os.makedirs(archive_dir)
  os.makedirs(daily_dir)
  tar_norm = os.path.normpath(os.path.join(daily_dir, "2026-04-15.tar"))
  open(tar_norm, "wb").close()
  logs: list[str] = []

  class _FakeRawCoord:
    enabled = True
    start_calls = 0

    def start_async_verify(self, _tar_path):
      self.start_calls += 1

    def verification_complete(self, _tar_path):
      return False

    def pipeline_future_done(self, _tar_path):
      return True

    def raw_removal_progress_summary(self, _tar_path):
      return {
          "phase": "verifying",
          "verified_count": 0,
          "pending_delete": 2,
          "deleted_count": 0,
      }

  fake_raw = _FakeRawCoord()
  monkeypatch.setattr(async_dc_mod.cfg, "get_sync_day_close_async_stale_seconds", lambda: 0.0)
  monkeypatch.setattr(async_dc_mod.cfg, "get_sync_day_close_async_workers", lambda: 1)
  monkeypatch.setattr(async_dc_mod.cfg, "get_sync_day_close_raw_removal_wait_seconds", lambda: 0.2)
  monkeypatch.setattr(async_dc_mod, "sleep_until_shutdown", lambda _s: None)

  coord = async_dc_mod.AsyncDayCloseCoordinator(
      archive_data_dir=archive_dir,
      host_name_ext="",
      tgz_archive_dir=daily_dir,
      local_tz=None,
      log_fn=lambda msg, **_kw: logs.append(str(msg)),
      get_disqualified_daily_tars=lambda: set(),
      day_raw_removal_coordinator=fake_raw,
  )
  with mock.patch.object(coord, "_seal_day", return_value=True):
    coord._run_day_close(tar_norm, "test")

  manifest = async_dc_mod._load_manifest(coord._manifest_path)
  entry = manifest.get("entries", {}).get(tar_norm)
  assert isinstance(entry, dict)
  assert entry.get("status") == "deferred"
  assert entry.get("detail") == "raw_removal_verify_timeout"
  assert any("verify stall" in line for line in logs)
  assert fake_raw.start_calls >= 2


@pytest.mark.django_db(databases=[])
def test_run_day_close_sets_raw_delete_pending_after_verify(tmp_path, monkeypatch):
  archive_dir = str(tmp_path / "archive")
  daily_dir = str(tmp_path / "daily")
  os.makedirs(archive_dir)
  os.makedirs(daily_dir)
  tar_norm = os.path.normpath(os.path.join(daily_dir, "2026-04-15.tar"))
  open(tar_norm, "wb").close()

  class _FakeRawCoord:
    enabled = True

    def start_async_verify(self, _tar_path):
      return None

    def verification_complete(self, _tar_path):
      return True

    def pipeline_future_done(self, _tar_path):
      return True

    def raw_removal_progress_summary(self, _tar_path):
      return {"phase": "verification_complete", "verified_count": 1, "pending_delete": 1}

  monkeypatch.setattr(async_dc_mod.cfg, "get_sync_day_close_async_workers", lambda: 1)
  monkeypatch.setattr(async_dc_mod.cfg, "get_sync_day_close_raw_removal_wait_seconds", lambda: 60.0)
  monkeypatch.setattr(async_dc_mod, "sleep_until_shutdown", lambda _s: None)

  coord = async_dc_mod.AsyncDayCloseCoordinator(
      archive_data_dir=archive_dir,
      host_name_ext="",
      tgz_archive_dir=daily_dir,
      local_tz=None,
      log_fn=lambda *_a, **_kw: None,
      get_disqualified_daily_tars=lambda: set(),
      day_raw_removal_coordinator=_FakeRawCoord(),
  )
  with mock.patch.object(coord, "_seal_day", return_value=True):
    coord._run_day_close(tar_norm, "test")

  entry = async_dc_mod._load_manifest(coord._manifest_path)["entries"][tar_norm]
  assert entry.get("status") == "raw_delete_pending"
  assert tar_norm in coord.active_or_submitted_tar_paths()


@pytest.mark.django_db(databases=[])
def test_submit_day_close_no_resubmit_when_raw_delete_pending(tmp_path, monkeypatch):
  archive_dir = str(tmp_path / "archive")
  daily_dir = str(tmp_path / "daily")
  os.makedirs(archive_dir)
  os.makedirs(daily_dir)
  tar_norm = os.path.normpath(os.path.join(daily_dir, "2026-04-15.tar"))
  open(tar_norm, "wb").close()
  logs: list[str] = []

  monkeypatch.setattr(async_dc_mod.cfg, "get_sync_day_close_async_workers", lambda: 1)

  coord = async_dc_mod.AsyncDayCloseCoordinator(
      archive_data_dir=archive_dir,
      host_name_ext="",
      tgz_archive_dir=daily_dir,
      local_tz=None,
      log_fn=lambda msg, **_kw: logs.append(str(msg)),
      get_disqualified_daily_tars=lambda: set(),
  )
  coord._set_entry_status(tar_norm, "raw_delete_pending")
  coord._touch_manifest("raw_delete_pending", tar_norm=tar_norm)

  run_calls = {"count": 0}
  original_run = coord._run_day_close

  def counting_run(*args, **kwargs):
    run_calls["count"] += 1
    return original_run(*args, **kwargs)

  with mock.patch.object(coord, "_run_day_close", side_effect=counting_run):
    assert coord.submit_day_close(tar_norm, reason="first") is True
    assert coord.submit_day_close(tar_norm, reason="second") is True

  assert run_calls["count"] == 0
  submit_logs = [line for line in logs if "async day_close submit" in line]
  assert len(submit_logs) == 0


@pytest.mark.django_db(databases=[])
def test_stale_manifest_recovery_does_not_recover_raw_delete_pending(tmp_path, monkeypatch):
  archive_dir = str(tmp_path / "archive")
  daily_dir = str(tmp_path / "daily")
  os.makedirs(archive_dir)
  os.makedirs(daily_dir)
  tar_norm = os.path.normpath(os.path.join(daily_dir, "2026-04-15.tar"))
  manifest_file = async_dc_mod.manifest_path(archive_dir)
  stale_at = time.time() - 10_000
  with open(manifest_file, "w", encoding="utf-8") as handle:
    handle.write(
        json.dumps({
            "version": 1,
            "entries": {
                tar_norm: {
                    "tar_path": tar_norm,
                    "status": "raw_delete_pending",
                    "last_progress": "raw_delete_pending",
                    "last_progress_at": stale_at,
                    "submitted_at": stale_at,
                },
            },
        }),
    )
  monkeypatch.setattr(async_dc_mod.cfg, "get_sync_day_close_async_stale_seconds", lambda: 60.0)
  monkeypatch.setattr(async_dc_mod.cfg, "get_sync_day_close_async_workers", lambda: 1)

  coord = async_dc_mod.AsyncDayCloseCoordinator(
      archive_data_dir=archive_dir,
      host_name_ext="",
      tgz_archive_dir=daily_dir,
      local_tz=None,
      log_fn=lambda *_a, **_kw: None,
      get_disqualified_daily_tars=lambda: set(),
  )

  entry = async_dc_mod._load_manifest(coord._manifest_path)["entries"][tar_norm]
  assert entry.get("status") == "raw_delete_pending"
  assert tar_norm in coord.active_or_submitted_tar_paths()


@pytest.mark.django_db(databases=[])
def test_seal_day_skips_before_seal_start_when_zst_equivalent(tmp_path, monkeypatch):
  archive_dir = str(tmp_path / "archive")
  daily_dir = str(tmp_path / "daily")
  os.makedirs(archive_dir)
  os.makedirs(daily_dir)
  tar_norm = os.path.normpath(os.path.join(daily_dir, "2026-04-15.tar"))
  zst_path = os.path.normpath(os.path.join(daily_dir, "2026-04-15.tar.zst"))
  open(tar_norm, "wb").close()
  open(zst_path, "wb").close()
  logs: list[str] = []

  monkeypatch.setattr(
      async_dc_mod,
      "daily_tar_seal_calendar_eligible",
      lambda *_a, **_k: True,
  )
  monkeypatch.setattr(
      async_dc_mod,
      "_seal_skip_existing_zst_equivalent",
      lambda *_a, **_k: (True, None),
  )
  monkeypatch.setattr(async_dc_mod.cfg, "get_archive_zstd_threads", lambda: 0)

  def fail_getsize(_path):
    raise AssertionError("getsize must not run when zst equivalent skip applies")

  monkeypatch.setattr(async_dc_mod.os.path, "getsize", fail_getsize)

  coord = async_dc_mod.AsyncDayCloseCoordinator(
      archive_data_dir=archive_dir,
      host_name_ext="",
      tgz_archive_dir=daily_dir,
      local_tz=None,
      log_fn=lambda msg, **_kw: logs.append(str(msg)),
      get_disqualified_daily_tars=lambda: set(),
  )
  assert coord._seal_day(tar_norm) is True
  assert not any("seal start" in line for line in logs)
  assert any("seal skipped (zst equivalent)" in line for line in logs)


@pytest.mark.django_db(databases=[])
def test_seal_day_skipped_still_drops_legacy_gz(tmp_path, monkeypatch):
  archive_dir = str(tmp_path / "archive")
  daily_dir = str(tmp_path / "daily")
  os.makedirs(archive_dir)
  os.makedirs(daily_dir)
  tar_norm = os.path.normpath(os.path.join(daily_dir, "2026-04-16.tar"))
  zst_path = os.path.normpath(os.path.join(daily_dir, "2026-04-16.tar.zst"))
  gz_path = os.path.normpath(os.path.join(daily_dir, "2026-04-16.tar.gz"))
  open(tar_norm, "wb").close()
  open(zst_path, "wb").close()
  open(gz_path, "wb").close()
  skip_result = (True, {"host/raw": 12})
  drop_calls = []

  def capture_drop(gz, zst, log_fn=None, **kwargs):
    drop_calls.append((gz, zst, kwargs))

  monkeypatch.setattr(
      async_dc_mod,
      "daily_tar_seal_calendar_eligible",
      lambda *_a, **_k: True,
  )
  monkeypatch.setattr(
      async_dc_mod,
      "_seal_skip_existing_zst_equivalent",
      lambda *_a, **_k: skip_result,
  )
  monkeypatch.setattr(
      async_dc_mod,
      "drop_legacy_gz_if_equivalent_to_zst",
      capture_drop,
  )
  monkeypatch.setattr(async_dc_mod.cfg, "get_archive_zstd_threads", lambda: 0)

  coord = async_dc_mod.AsyncDayCloseCoordinator(
      archive_data_dir=archive_dir,
      host_name_ext="",
      tgz_archive_dir=daily_dir,
      local_tz=None,
      log_fn=lambda msg, **_kw: None,
      get_disqualified_daily_tars=lambda: set(),
  )
  assert coord._seal_day(tar_norm) is True
  assert len(drop_calls) == 1
  assert drop_calls[0][0] == gz_path
  assert drop_calls[0][1] == zst_path
  assert drop_calls[0][2]["zst_members"] == skip_result[1]


@pytest.mark.django_db(databases=[])
def test_seal_day_tar_absent_still_drops_legacy_gz(tmp_path, monkeypatch):
  archive_dir = str(tmp_path / "archive")
  daily_dir = str(tmp_path / "daily")
  os.makedirs(archive_dir)
  os.makedirs(daily_dir)
  tar_norm = os.path.normpath(os.path.join(daily_dir, "2026-04-17.tar"))
  zst_path = os.path.normpath(os.path.join(daily_dir, "2026-04-17.tar.zst"))
  gz_path = os.path.normpath(os.path.join(daily_dir, "2026-04-17.tar.gz"))
  open(zst_path, "wb").close()
  open(gz_path, "wb").close()
  drop_calls = []

  def capture_drop(gz, zst, log_fn=None, **kwargs):
    drop_calls.append((gz, zst, kwargs))

  monkeypatch.setattr(
      async_dc_mod,
      "dedupe_sealed_daily_archive",
      lambda *_a, **_k: None,
  )
  monkeypatch.setattr(
      async_dc_mod,
      "drop_legacy_gz_if_equivalent_to_zst",
      capture_drop,
  )

  coord = async_dc_mod.AsyncDayCloseCoordinator(
      archive_data_dir=archive_dir,
      host_name_ext="",
      tgz_archive_dir=daily_dir,
      local_tz=None,
      log_fn=lambda msg, **_kw: None,
      get_disqualified_daily_tars=lambda: set(),
  )
  assert coord._seal_day(tar_norm) is True
  assert len(drop_calls) == 1
  assert drop_calls[0][0] == gz_path


@pytest.mark.django_db(databases=[])
def test_seal_day_passes_skip_result_to_atomic_seal(tmp_path, monkeypatch):
  archive_dir = str(tmp_path / "archive")
  daily_dir = str(tmp_path / "daily")
  os.makedirs(archive_dir)
  os.makedirs(daily_dir)
  tar_norm = os.path.normpath(os.path.join(daily_dir, "2026-04-20.tar"))
  zst_path = os.path.normpath(os.path.join(daily_dir, "2026-04-20.tar.zst"))
  open(tar_norm, "wb").close()
  open(zst_path, "wb").close()
  skip_result = (False, {"host/raw": 4})
  skip_calls = {"n": 0}
  captured = {}

  def counting_skip(*_a, **_k):
    skip_calls["n"] += 1
    return skip_result

  def capture_atomic(*args, **kwargs):
    captured["skip_result"] = kwargs.get("skip_result")
    return None

  monkeypatch.setattr(
      async_dc_mod,
      "daily_tar_seal_calendar_eligible",
      lambda *_a, **_k: True,
  )
  monkeypatch.setattr(
      async_dc_mod, "_seal_skip_existing_zst_equivalent", counting_skip,
  )
  monkeypatch.setattr(async_dc_mod, "atomic_seal_tar_to_zst", capture_atomic)
  monkeypatch.setattr(
      async_dc_mod,
      "build_remaining_raw_for_daily_tar",
      lambda *_a, **_k: [],
  )
  monkeypatch.setattr(
      async_dc_mod,
      "effective_keep_uncompressed_tar",
      lambda *_a, **_k: True,
  )
  monkeypatch.setattr(async_dc_mod.cfg, "get_archive_zstd_threads", lambda: 0)
  monkeypatch.setattr(async_dc_mod.cfg, "get_archive_zstd_level", lambda: 3)
  monkeypatch.setattr(async_dc_mod.cfg, "get_sync_day_close_async_workers", lambda: 1)
  monkeypatch.setattr(
      async_dc_mod.cfg, "get_sync_startup_day_close_max_inflight", lambda: 1,
  )
  monkeypatch.setattr(
      async_dc_mod,
      "drop_legacy_gz_if_equivalent_to_zst",
      lambda *_a, **_k: None,
  )

  coord = async_dc_mod.AsyncDayCloseCoordinator(
      archive_data_dir=archive_dir,
      host_name_ext="",
      tgz_archive_dir=daily_dir,
      local_tz=None,
      log_fn=lambda msg, **_kw: None,
      get_disqualified_daily_tars=lambda: set(),
  )
  assert coord._seal_day(tar_norm) is True
  assert skip_calls["n"] == 1
  assert captured["skip_result"] == skip_result


@pytest.mark.django_db(databases=[])
def test_stale_manifest_recovery_on_coordinator_init(tmp_path, monkeypatch):
  archive_dir = str(tmp_path / "archive")
  daily_dir = str(tmp_path / "daily")
  os.makedirs(archive_dir)
  os.makedirs(daily_dir)
  tar_norm = os.path.normpath(os.path.join(daily_dir, "2026-04-15.tar"))
  manifest_file = async_dc_mod.manifest_path(archive_dir)
  stale_at = time.time() - 10_000
  with open(manifest_file, "w", encoding="utf-8") as handle:
    handle.write(
      json.dumps({
          "version": 1,
          "entries": {
              tar_norm: {
                  "tar_path": tar_norm,
                  "status": "raw_removal",
                  "last_progress": "raw_removal",
                  "last_progress_at": stale_at,
                  "submitted_at": stale_at,
              },
          },
      }),
    )
  logs: list[str] = []
  monkeypatch.setattr(async_dc_mod.cfg, "get_sync_day_close_async_stale_seconds", lambda: 60.0)
  monkeypatch.setattr(async_dc_mod.cfg, "get_sync_day_close_async_workers", lambda: 1)

  coord = async_dc_mod.AsyncDayCloseCoordinator(
      archive_data_dir=archive_dir,
      host_name_ext="",
      tgz_archive_dir=daily_dir,
      local_tz=None,
      log_fn=lambda msg, **_kw: logs.append(str(msg)),
      get_disqualified_daily_tars=lambda: set(),
  )

  entry = async_dc_mod._load_manifest(coord._manifest_path)["entries"][tar_norm]
  assert entry.get("status") == "deferred"
  assert entry.get("detail") == "stale_manifest_recovery"
  assert tar_norm not in coord.active_or_submitted_tar_paths()
  assert any("stale manifest recovery" in line for line in logs)


@pytest.mark.django_db(databases=[])
def test_is_complete_false_when_manifest_complete_but_raw_remains(tmp_path, monkeypatch):
  archive_dir = str(tmp_path / "archive")
  daily_dir = str(tmp_path / "daily")
  os.makedirs(archive_dir)
  os.makedirs(daily_dir)
  tar_norm = os.path.normpath(os.path.join(daily_dir, "2026-04-15.tar"))
  zst_path = os.path.normpath(os.path.join(daily_dir, "2026-04-15.tar.zst"))
  open(tar_norm, "wb").close()
  open(zst_path, "wb").close()
  raw_path = "/raw/still-present"
  monkeypatch.setattr(
      async_dc_mod,
      "build_remaining_raw_for_daily_tar",
      lambda *_a, **_k: {zst_path: [raw_path]},
  )
  coord = async_dc_mod.AsyncDayCloseCoordinator(
      archive_data_dir=archive_dir,
      host_name_ext="",
      tgz_archive_dir=daily_dir,
      local_tz=None,
      log_fn=lambda *_a, **_kw: None,
      get_disqualified_daily_tars=lambda: set(),
  )
  coord._set_entry_status(tar_norm, "complete", completed_at=time.time())
  assert coord.is_complete(tar_norm) is False


@pytest.mark.django_db(databases=[])
def test_submit_day_close_honors_submit_eligible_fn(tmp_path):
  archive_dir = str(tmp_path / "archive")
  daily_dir = str(tmp_path / "daily")
  os.makedirs(archive_dir)
  os.makedirs(daily_dir)
  tar_path = os.path.join(daily_dir, "2020-01-01.tar")
  open(tar_path, "wb").close()
  coord = async_dc_mod.AsyncDayCloseCoordinator(
      archive_data_dir=archive_dir,
      host_name_ext="",
      tgz_archive_dir=daily_dir,
      local_tz=None,
      log_fn=lambda *_a, **_kw: None,
      get_disqualified_daily_tars=lambda: set(),
      submit_eligible_fn=lambda _tar: (False, "checkpoint_incomplete"),
  )
  assert coord.submit_day_close(tar_path, reason="test") is False


@pytest.mark.django_db(databases=[])
def test_run_day_close_tail_complete_without_reseal_when_sealed_raw_gone(
    tmp_path, monkeypatch,
):
  archive_dir = str(tmp_path / "archive")
  daily_dir = str(tmp_path / "daily")
  os.makedirs(archive_dir)
  os.makedirs(daily_dir)
  tar_norm = os.path.normpath(os.path.join(daily_dir, "2026-04-15.tar"))
  zst_path = os.path.normpath(os.path.join(daily_dir, "2026-04-15.tar.zst"))
  open(tar_norm, "wb").close()
  open(zst_path, "wb").close()
  logs: list[str] = []
  seal_calls = {"count": 0}

  class _FakeRawCoord:
    enabled = True

    def try_finish_tar_drop_if_ready(self, _tar_path):
      os.remove(tar_norm)
      return True

  monkeypatch.setattr(async_dc_mod.cfg, "get_sync_day_close_async_workers", lambda: 1)
  monkeypatch.setattr(
      async_dc_mod,
      "build_remaining_raw_for_daily_tar",
      lambda *_a, **_k: {},
  )

  coord = async_dc_mod.AsyncDayCloseCoordinator(
      archive_data_dir=archive_dir,
      host_name_ext="",
      tgz_archive_dir=daily_dir,
      local_tz=None,
      log_fn=lambda msg, **_kw: logs.append(str(msg)),
      get_disqualified_daily_tars=lambda: set(),
      day_raw_removal_coordinator=_FakeRawCoord(),
  )

  def counting_seal(*_a, **_k):
    seal_calls["count"] += 1
    return True

  with mock.patch.object(coord, "_seal_day", side_effect=counting_seal):
    coord._run_day_close(tar_norm, "deferred_resubmit")

  entry = async_dc_mod._load_manifest(coord._manifest_path)["entries"][tar_norm]
  assert entry.get("status") == "complete"
  assert seal_calls["count"] == 0
  assert any("tail only" in line for line in logs)
  assert not os.path.isfile(tar_norm)


@pytest.mark.django_db(databases=[])
def test_tar_paths_raw_delete_pending_returns_sorted_pending(tmp_path):
  archive_dir = str(tmp_path / "archive")
  daily_dir = str(tmp_path / "daily")
  os.makedirs(archive_dir)
  os.makedirs(daily_dir)
  tar_a = os.path.normpath(os.path.join(daily_dir, "2026-04-10.tar"))
  tar_b = os.path.normpath(os.path.join(daily_dir, "2026-04-12.tar"))
  coord = async_dc_mod.AsyncDayCloseCoordinator(
      archive_data_dir=archive_dir,
      host_name_ext="",
      tgz_archive_dir=daily_dir,
      local_tz=None,
      log_fn=lambda *_a, **_k: None,
      get_disqualified_daily_tars=lambda: set(),
  )
  coord._set_entry_status(tar_b, "raw_delete_pending")
  coord._set_entry_status(tar_a, "raw_delete_pending")
  assert coord.tar_paths_raw_delete_pending() == [tar_a, tar_b]
