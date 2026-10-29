"""Dead scratch files beside the daily archives are swept, nothing else is.

hpcperfstats04 2026-10-29: ``2026-08-07.tar.merge.tmp`` (36 GB, Aug 29),
``2026-08-09.tar.merge.tmp`` (36 GB) and ``2026-08-23.tar.merge.tmp`` (22 GB,
Sep 3) were still on disk. A union rebuild writes ``<tar>.merge.tmp`` and
removes it at the end or at the start of its next attempt; when the process
died and that day later closed another way, nothing ever removed it.
"""

from __future__ import annotations

import os
import time

import pytest

from hpcperfstats.dbload.lib import (
  conf_parser as cfg,
  sync_timedb_archive_scratch as scratch,
  sync_timedb_queue_orchestrator as qo,
)
from hpcperfstats.dbload.lib.file_locking import file_write_lock

DAY = "2026-08-07"
OLD = 48 * 3600


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
  monkeypatch.setattr(
    cfg, "get_sync_archive_scratch_max_idle_hours", lambda: 24
  )
  monkeypatch.setattr(scratch, "effective_remediation_mode", lambda: "apply")


def _file(daily, name, *, age_s=OLD, size=8):
  path = daily / name
  path.write_bytes(b"x" * size)
  stamp = time.time() - age_s
  os.utime(path, (stamp, stamp))
  return path


def _sweep(daily, logs=None, archive=None):
  sink = logs if logs is not None else []
  return scratch.sweep_dead_archive_scratch(
    str(daily),
    archive_dir=str(archive) if archive else "",
    log_fn=lambda msg, **_k: sink.append(msg),
  )


@pytest.mark.parametrize(
  "suffix", ["merge.tmp", "dedupe.tmp", "repair.tmp", "decomp.tmp", "zst.tmp"]
)
def test_old_scratch_file_is_removed(tmp_path, suffix):
  path = _file(tmp_path, f"{DAY}.tar.{suffix}")
  assert _sweep(tmp_path) == 1
  assert not path.exists()


def test_removal_is_logged_with_size_and_idle(tmp_path):
  _file(tmp_path, f"{DAY}.tar.merge.tmp", size=1234)
  logs: list[str] = []
  _sweep(tmp_path, logs)
  line = next(m for m in logs if "archive scratch removed" in m)
  assert f"{DAY}.tar.merge.tmp" in line and "bytes=1234" in line


def test_recently_written_scratch_is_kept(tmp_path):
  path = _file(tmp_path, f"{DAY}.tar.merge.tmp", age_s=60)
  assert _sweep(tmp_path) == 0
  assert path.exists()


def test_safety_copies_and_real_archives_are_never_touched(tmp_path):
  keep = [
    _file(tmp_path, f"{DAY}.tar"),
    _file(tmp_path, f"{DAY}.tar.zst"),
    _file(tmp_path, f"{DAY}.tar.corrupt.1788000000"),
    _file(tmp_path, f"{DAY}.tar.pre-restore.1788000000"),
    _file(tmp_path, f"{DAY}.tar.zst.corrupt.1788000000"),
    _file(tmp_path, f"{DAY}.tar.fnctl.lock"),
    _file(tmp_path, ".sync_timedb_sealed_integrity.json"),
    _file(tmp_path, "notes.merge.tmp"),
    _file(tmp_path, f"{DAY}.tar.merge.tmp.bak"),
  ]
  assert _sweep(tmp_path) == 0
  assert all(p.exists() for p in keep)


def test_symlink_named_like_scratch_is_not_followed(tmp_path):
  target = _file(tmp_path, "real-data.bin")
  link = tmp_path / f"{DAY}.tar.merge.tmp"
  link.symlink_to(target)
  stamp = time.time() - OLD
  os.utime(link, (stamp, stamp), follow_symlinks=False)
  assert _sweep(tmp_path) == 0
  assert target.exists() and link.is_symlink()


def test_scratch_is_kept_while_the_day_tar_lock_is_held(tmp_path):
  path = _file(tmp_path, f"{DAY}.tar.merge.tmp")
  with file_write_lock(str(tmp_path / f"{DAY}.tar")):
    assert _sweep(tmp_path) == 0
  assert path.exists()
  assert _sweep(tmp_path) == 1


def test_scratch_is_kept_while_the_restore_lock_is_held(tmp_path):
  path = _file(tmp_path, f"{DAY}.tar.decomp.tmp")
  with file_write_lock(str(tmp_path / f"{DAY}.tar.decomp")):
    assert _sweep(tmp_path) == 0
  assert path.exists()


@pytest.mark.parametrize(
  "busy",
  ["archive_append_inflight_for_day", "daily_tar_restore_in_progress_for_day"],
)
def test_scratch_is_kept_while_the_day_is_busy(tmp_path, monkeypatch, busy):
  path = _file(tmp_path, f"{DAY}.tar.merge.tmp")
  monkeypatch.setattr(scratch, busy, lambda _day: True)
  assert _sweep(tmp_path) == 0
  assert path.exists()


def test_report_mode_logs_and_removes_nothing(tmp_path, monkeypatch):
  monkeypatch.setattr(scratch, "effective_remediation_mode", lambda: "report")
  path = _file(tmp_path, f"{DAY}.tar.merge.tmp")
  logs: list[str] = []
  assert _sweep(tmp_path, logs) == 0
  assert path.exists()
  assert any("archive scratch would remove" in m for m in logs)


def test_zero_idle_hours_disables_the_sweep(tmp_path, monkeypatch):
  monkeypatch.setattr(cfg, "get_sync_archive_scratch_max_idle_hours", lambda: 0)
  path = _file(tmp_path, f"{DAY}.tar.merge.tmp")
  assert _sweep(tmp_path) == 0
  assert path.exists()


def test_missing_directory_is_a_noop():
  assert scratch.sweep_dead_archive_scratch("/nonexistent/daily") == 0


EXTRACT = "hps_union_extract_ab12cd34"


def _extract_dir(daily, name=EXTRACT, *, age_s=OLD, files=3):
  """Build a union-extract staging dir whose whole tree is ``age_s`` old."""
  root = daily / name
  nested = root / "hpcperfstats" / "archive" / "c1-001"
  nested.mkdir(parents=True)
  for i in range(files):
    (nested / f"17880000{i}").write_bytes(b"y" * 100)
  stamp = time.time() - age_s
  for dirpath, dirnames, filenames in os.walk(root, topdown=False):
    for entry in (*filenames, *dirnames):
      os.utime(os.path.join(dirpath, entry), (stamp, stamp))
  os.utime(root, (stamp, stamp))
  return root


def test_old_union_extract_dir_is_removed_with_its_contents(tmp_path):
  root = _extract_dir(tmp_path, files=3)
  logs: list[str] = []
  assert _sweep(tmp_path, logs) == 1
  assert not root.exists()
  line = next(m for m in logs if "archive scratch removed" in m)
  assert EXTRACT in line and "bytes=300" in line


def test_union_extract_dir_with_a_recent_file_inside_is_kept(tmp_path):
  root = _extract_dir(tmp_path)
  fresh = next(root.rglob("17880000*"))
  os.utime(fresh, None)
  assert _sweep(tmp_path) == 0
  assert root.exists() and fresh.exists()


def test_recent_union_extract_dir_is_kept(tmp_path):
  root = _extract_dir(tmp_path, age_s=60)
  assert _sweep(tmp_path) == 0
  assert root.exists()


@pytest.mark.parametrize(
  "name",
  [
    "hps_union_extract_ab12cd34",
    "hps_pax_convert_ab12cd34",
    "hps_tar_repair_ab12cd34",
  ],
)
def test_every_staging_dir_family_is_removed(tmp_path, name):
  root = _extract_dir(tmp_path, name)
  assert _sweep(tmp_path) == 1
  assert not root.exists()


def test_other_directories_are_never_removed(tmp_path):
  keep = [
    _extract_dir(tmp_path, ".sync_timedb_sealed_members"),
    _extract_dir(tmp_path, ".sync_timedb_tar_salvage"),
    _extract_dir(tmp_path, ".sync_timedb_unparsable_raw"),
    _extract_dir(tmp_path, ".sync_timedb_migration_backup"),
    _extract_dir(tmp_path, "hps_union_extract_"),
    _extract_dir(tmp_path, "hps_pax_convert_ab12cd34.bak"),
    _extract_dir(tmp_path, "hps_other_ab12cd34"),
  ]
  assert _sweep(tmp_path) == 0
  assert all(p.exists() for p in keep)


@pytest.mark.parametrize(
  "name",
  [
    ".sync_timedb_sealed_integrity.json.tmp",
    "2026-08-22.json.tmp",
    ".atomic.k3j2h1g0.sync_timedb_job_store.json",
    ".atomic.k3j2h1g0sync_timedb_state.json",
  ],
)
def test_old_sidecar_write_temps_are_removed(tmp_path, name):
  path = _file(tmp_path, name)
  assert _sweep(tmp_path) == 1
  assert not path.exists()


def test_recent_sidecar_write_temp_is_kept(tmp_path):
  path = _file(tmp_path, ".atomic.k3j2h1g0.sync_timedb_state.json", age_s=60)
  assert _sweep(tmp_path) == 0
  assert path.exists()


def test_real_sidecars_and_data_files_are_never_removed(tmp_path):
  keep = [
    _file(tmp_path, ".sync_timedb_state.json"),
    _file(tmp_path, ".sync_timedb_job_store.json"),
    _file(tmp_path, ".sync_timedb_sealed_integrity.json"),
    _file(tmp_path, ".atomic.short.json"),
    _file(tmp_path, "atomic.k3j2h1g0.sync_timedb_state.json"),
    _file(tmp_path, "notes.json.tmp.bak"),
  ]
  assert _sweep(tmp_path) == 0
  assert all(p.exists() for p in keep)


def test_archive_dir_and_its_sidecar_dirs_are_swept_too(tmp_path):
  daily = tmp_path / "daily"
  archive = tmp_path / "archive"
  members = archive / ".sync_timedb_archive_members"
  salvage = daily / ".sync_timedb_tar_salvage"
  sealed = daily / ".sync_timedb_sealed_members"
  for d in (daily, archive, members, salvage, sealed):
    d.mkdir(parents=True, exist_ok=True)
  dead = [
    _file(archive, ".atomic.k3j2h1g0.sync_timedb_job_store.json"),
    _file(members, ".atomic.k3j2h1g0.2026-08-22.json"),
    _file(salvage, "2026-08-22.json.tmp"),
    _file(sealed, ".atomic.k3j2h1g0.2026-08-22.json"),
    _file(daily, f"{DAY}.tar.merge.tmp"),
  ]
  keep = [
    _file(members, "2026-08-22.json"),
    _file(archive, ".sync_timedb_job_store.json"),
  ]
  assert _sweep(daily, archive=archive) == len(dead)
  assert not any(p.exists() for p in dead)
  assert all(p.exists() for p in keep)


def test_unparsable_raw_and_migration_backup_dirs_are_never_entered(tmp_path):
  archive = tmp_path / "archive"
  protected = [
    archive / ".sync_timedb_unparsable_raw",
    archive / ".sync_timedb_migration_backup",
  ]
  keep = []
  for d in protected:
    d.mkdir(parents=True)
    keep.append(_file(d, ".atomic.k3j2h1g0.anything.json"))
  assert _sweep(tmp_path / "missing-daily", archive=archive) == 0
  assert all(p.exists() for p in keep)


def test_same_dir_for_daily_and_archive_is_not_swept_twice(tmp_path):
  _file(tmp_path, f"{DAY}.tar.merge.tmp")
  assert _sweep(tmp_path, archive=tmp_path) == 1


def test_symlink_named_like_an_extract_dir_is_not_followed(tmp_path):
  target = _extract_dir(tmp_path, "real-staging")
  link = tmp_path / EXTRACT
  link.symlink_to(target, target_is_directory=True)
  assert _sweep(tmp_path) == 0
  assert target.exists() and link.is_symlink()
  assert any(target.rglob("17880000*"))


def test_union_extract_dir_report_mode_and_disabled_remove_nothing(
  tmp_path, monkeypatch
):
  root = _extract_dir(tmp_path)
  monkeypatch.setattr(scratch, "effective_remediation_mode", lambda: "report")
  logs: list[str] = []
  assert _sweep(tmp_path, logs) == 0
  assert root.exists()
  assert any("archive scratch would remove" in m for m in logs)
  monkeypatch.setattr(scratch, "effective_remediation_mode", lambda: "apply")
  monkeypatch.setattr(cfg, "get_sync_archive_scratch_max_idle_hours", lambda: 0)
  assert _sweep(tmp_path) == 0
  assert root.exists()


def test_idle_hours_getter_reads_the_ini_and_floors_at_zero(monkeypatch):
  monkeypatch.undo()
  monkeypatch.setattr(cfg, "_ensure_cfg_loaded", lambda: None)
  monkeypatch.setattr(cfg, "_pipeline_getint", lambda _k: -5)
  assert cfg.get_sync_archive_scratch_max_idle_hours() == 0
  monkeypatch.setattr(cfg, "_pipeline_getint", lambda _k: 12)
  assert cfg.get_sync_archive_scratch_max_idle_hours() == 12


def test_idle_reconstruct_pass_runs_the_sweep():
  import inspect

  src = inspect.getsource(qo._idle_reconstruct_pass)
  assert "sweep_dead_archive_scratch" in src
  assert "archive_dir=archive_dir" in src
