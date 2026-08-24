"""Regression tests for archive maintenance hints v2 (debt queue + day phases)."""

import json
import os

from hpcperfstats.dbload.lib.sync_timedb_archive_maint import (
    load_archive_maint_hints,
    maint_hints_path,
    save_archive_maint_hints,
)


def test_prune_keeps_sealed_only_tar_dropped(tmp_path):
  from hpcperfstats.dbload.lib.sync_timedb_archive_maint import prune_day_phases_hints

  tar_path = str(tmp_path / "2026-06-04.tar")
  zst_path = str(tmp_path / "2026-06-04.tar.zst")
  open(zst_path, "wb").close()
  day_phases = {tar_path: "tar_dropped"}
  pruned = prune_day_phases_hints(day_phases)
  assert tar_path in pruned
  assert pruned[tar_path] == "tar_dropped"


def test_save_and_load_hints_v2_round_trip(tmp_path, monkeypatch):
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_maint._bdef.SYNC_ARCHIVE_MAINT_HINTS",
      lambda: True,
  )
  archive_dir = str(tmp_path / "archive")
  os.makedirs(archive_dir, exist_ok=True)
  tar_path = str(tmp_path / "2026-01-01.tar")
  open(tar_path, "wb").close()
  from hpcperfstats.dbload.lib.sync_timedb_archive_maint import day_phase_hint_entry

  save_archive_maint_hints(
      archive_dir,
      host_dirs={"/host/a": {"mtime": 1, "file_count": 2}},
      paths={},
      validated_days={},
      day_phases={tar_path: day_phase_hint_entry(tar_path, "sealed")},
      debt_queue=[{"kind": "raw_remove", "tar_path": "/tmp/2026-01-01.tar"}],
  )
  loaded = load_archive_maint_hints(archive_dir)
  assert loaded is not None
  assert loaded["version"] == 2
  phase = loaded["day_phases"][tar_path]
  assert phase == "sealed" or phase.get("phase") == "sealed"
  assert loaded["debt_queue"][0]["kind"] == "raw_remove"


def test_load_hints_accepts_schema_version_without_legacy_version_key(
  tmp_path, monkeypatch,
):
  """Regression: save_persistence_document pops ``version`` for schema_version.

  load_archive_maint_hints must not reject the on-disk envelope (janitor debt
  restore / day_phases) when only ``schema_version`` is present.
  """
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_maint._bdef.SYNC_ARCHIVE_MAINT_HINTS",
      lambda: True,
  )
  archive_dir = str(tmp_path / "archive")
  os.makedirs(archive_dir, exist_ok=True)
  path = maint_hints_path(archive_dir)
  with open(path, "w", encoding="utf-8") as handle:
    json.dump(
        {
            "contract_version": 8,
            "schema_version": 2,
            "host_dirs": {},
            "paths": {},
            "validated_days": {},
            "day_phases": {},
            "debt_queue": [
                {"kind": "raw_remove", "tar_path": "/tmp/2026-01-01.tar"},
                {"kind": "tar_drop", "tar_path": "/tmp/2026-01-02.tar"},
            ],
        },
        handle,
    )
  loaded = load_archive_maint_hints(archive_dir)
  assert loaded is not None
  assert "version" not in json.load(open(path, encoding="utf-8"))
  assert loaded["version"] == 2
  assert loaded["schema_version"] == 2
  assert len(loaded["debt_queue"]) == 2


def test_validated_days_hint_dropped_when_daily_tar_mtime_changes(tmp_path, monkeypatch):
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_maint._bdef.SYNC_ARCHIVE_MAINT_HINTS",
      lambda: True,
  )
  from hpcperfstats.dbload.lib.sync_timedb_archive_maint import (
      prune_validated_days_hints,
  )

  archive_dir = str(tmp_path / "archive")
  os.makedirs(archive_dir, exist_ok=True)
  daily_dir = tmp_path / "daily"
  daily_dir.mkdir()
  tar_path = daily_dir / "2026-01-01.tar"
  zst_path = daily_dir / "2026-01-01.tar.zst"
  tar_path.write_bytes(b"tar-v1")
  zst_path.write_bytes(b"zst-v1")
  st = zst_path.stat()
  tar_st = tar_path.stat()
  hints = {
      str(zst_path): {
          "mtime_ns": int(st.st_mtime_ns),
          "size": int(st.st_size),
          "tar_mtime_ns": int(tar_st.st_mtime_ns),
          "tar_size": int(tar_st.st_size),
          "ok": True,
          "member_count": 1,
          "member_byte_sum": 1,
      },
  }
  assert str(zst_path) in prune_validated_days_hints(hints)
  tar_path.write_bytes(b"tar-v2-changed")
  pruned = prune_validated_days_hints(hints)
  assert str(zst_path) not in pruned


def test_load_hints_v1_still_supported(tmp_path, monkeypatch):
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_maint._bdef.SYNC_ARCHIVE_MAINT_HINTS",
      lambda: True,
  )
  archive_dir = str(tmp_path / "archive")
  os.makedirs(archive_dir, exist_ok=True)
  with open(maint_hints_path(archive_dir), "w", encoding="utf-8") as handle:
    json.dump({
        "version": 1,
        "host_dirs": {},
        "paths": {},
        "validated_days": {},
    }, handle)
  loaded = load_archive_maint_hints(archive_dir)
  assert loaded is not None
  assert loaded["version"] == 1
