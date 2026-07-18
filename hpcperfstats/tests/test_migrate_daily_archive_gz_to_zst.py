"""Regression: migrate_daily_archive_gz_to_zst bootstraps Django before ORM paths."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "scripts" / "migrate_daily_archive_gz_to_zst.py"


def _load_migrate_script():
  spec = importlib.util.spec_from_file_location(
      "migrate_daily_archive_gz_to_zst",
      _SCRIPT,
  )
  assert spec is not None and spec.loader is not None
  mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)
  return mod


def test_migrate_script_source_calls_ensure_django():
  """Static guard: CLI entry must bootstrap Django (ImproperlyConfigured fix)."""
  source = _SCRIPT.read_text(encoding="utf-8")
  assert "ensure_django" in source
  assert "django_bootstrap" in source


def test_migrate_main_calls_ensure_django_before_remaining_raw(monkeypatch, tmp_path):
  """Regression: bare ``python3 ./scripts/migrate_daily_archive_gz_to_zst.py`` must
  call ensure_django before build_remaining_raw_stats_by_daily_gz (ORM import).
  """
  mod = _load_migrate_script()
  daily = tmp_path / "daily"
  daily.mkdir()
  order = []

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser._ensure_cfg_loaded",
      lambda: None,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_daily_archive_dir_path",
      lambda: str(daily),
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_archive_dir_path",
      lambda: str(tmp_path / "archive"),
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_host_name_ext",
      lambda: "example.com",
  )

  def fake_ensure():
    order.append("ensure_django")

  def fake_remaining(*_a, **_k):
    order.append("remaining_raw")
    return {}

  def fake_migrate(*_a, **_k):
    order.append("migrate")
    return {
        "converted": 0,
        "dropped_only": 0,
        "failed": 0,
        "gz_remaining": 0,
    }

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.django_bootstrap.ensure_django",
      fake_ensure,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_helpers."
      "check_archive_migration_prerequisites",
      lambda: None,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_helpers."
      "build_remaining_raw_stats_by_daily_gz",
      fake_remaining,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_helpers."
      "migrate_legacy_daily_gz_archives",
      fake_migrate,
  )

  rc = mod.main(["--daily-archive-dir", str(daily), "--dry-run"])
  assert rc == 0
  assert order[:2] == ["ensure_django", "remaining_raw"]
  assert "migrate" in order


def test_build_archive_maintenance_snapshot_ensures_django(monkeypatch):
  """Shared helper must bootstrap Django before ingest_readiness/host_data import."""
  from hpcperfstats.dbload.lib import sync_timedb_archive_maint as maint

  order = []

  def fake_ensure():
    order.append("ensure_django")

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.django_bootstrap.ensure_django",
      fake_ensure,
  )

  class _FakeReady:
    @staticmethod
    def build_head_ingest_ready_set(*_a, **_k):
      order.append("ready_set")
      return set()

  import types

  fake_mod = types.ModuleType("sync_timedb_ingest_readiness")
  fake_mod.build_head_ingest_ready_set = _FakeReady.build_head_ingest_ready_set
  monkeypatch.setitem(
      __import__("sys").modules,
      "hpcperfstats.dbload.lib.sync_timedb_ingest_readiness",
      fake_mod,
  )

  monkeypatch.setattr(maint, "load_archive_maint_hints", lambda *_a, **_k: {})
  monkeypatch.setattr(
      maint, "collect_stats_files_in_range", lambda *_a, **_k: [],
  )
  monkeypatch.setattr(
      maint,
      "collect_head_metadata_for_paths",
      lambda *_a, **_k: ({}, {}, {}),
  )
  monkeypatch.setattr(
      maint,
      "collect_gate_identities_for_paths",
      lambda *_a, **_k: ({}, {}),
  )
  monkeypatch.setattr(maint, "build_archive_mapping", lambda *_a, **_k: {})
  monkeypatch.setattr(
      maint, "remaining_raw_by_gz_from_mapping", lambda *_a, **_k: {},
  )

  snap = maint.build_archive_maintenance_snapshot(
      "/tmp/archive",
      "example.com",
      "/tmp/daily",
      build_ready_set=True,
      log_fn=None,
  )
  assert order[0] == "ensure_django"
  assert snap is not None
