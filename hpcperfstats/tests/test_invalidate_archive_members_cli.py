"""Tests for host archive-members invalidate CLI (scripts/ + compose restart mocked)."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from hpcperfstats.dbload.lib.invalidate_archive_members_ops import (
    JOB_STORE_SNAPSHOT_RELPATH,
    MEMBERS_STORE_DIR_RELPATH,
    compose_argv,
    restart_pipeline_compose,
)

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "scripts" / "invalidate_archive_members.py"


def _load_cli():
  """Load scripts/invalidate_archive_members.py as a module (repo-root bootstrap)."""
  spec = importlib.util.spec_from_file_location(
      "invalidate_archive_members_cli",
      _SCRIPT,
  )
  assert spec is not None and spec.loader is not None
  mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)
  return mod


def _seed_archive_sidecars(tmp_path, days=("2026-06-08",)):
  """Create member-day JSON sidecars plus a job-store snapshot that must survive."""
  archive = tmp_path / "archive"
  members = archive / MEMBERS_STORE_DIR_RELPATH
  members.mkdir(parents=True)
  for day in days:
    (members / ("%s.json" % day)).write_text("{}", encoding="utf-8")
  job = archive / JOB_STORE_SNAPSHOT_RELPATH
  job.write_text("{}", encoding="utf-8")
  return archive, members, job


@pytest.fixture
def cli():
  return _load_cli()


@pytest.fixture
def compose_dir(tmp_path):
  (tmp_path / "docker-compose.yaml").write_text("services: {}\n", encoding="utf-8")
  return tmp_path


def test_script_help_imports_without_editable_install():
  """Regression: bare ``python3 scripts/...`` must not raise ModuleNotFoundError.

  Production hosts often lack ``pip install -e``; scripts bootstrap repo root onto
  ``sys.path`` (same pattern as other ``scripts/*.py`` CLIs).
  """
  env = {k: v for k, v in __import__("os").environ.items() if k != "PYTHONPATH"}
  completed = subprocess.run(
      [sys.executable, str(_SCRIPT), "--help"],
      cwd=str(_REPO),
      env=env,
      capture_output=True,
      text=True,
      check=False,
  )
  assert completed.returncode == 0, completed.stderr
  assert "ModuleNotFoundError" not in (completed.stderr or "")
  assert "TypeError" not in (completed.stderr or "")
  assert "Invalidate archive membership" in (completed.stdout or "")


def test_script_sys_path_bootstrap_inserts_repo_root():
  source = _SCRIPT.read_text(encoding="utf-8")
  assert "_REPO_ROOT" in source
  assert "sys.path.insert(0, str(_REPO_ROOT))" in source
  assert "_ensure_python_version" in source
  assert "_MIN_PY" in source


def test_cli_import_path_avoids_print_utils():
  """Regression: host CLI must not import print_utils (PEP 604 on old python3)."""
  import re

  source = _SCRIPT.read_text(encoding="utf-8")
  assert "sync_timedb_archive_members_redis" not in source
  assert "--redis-url" not in source
  assert "_direct_members_client" not in source
  assert not re.search(
      r"^\s*(from|import)\s+.*print_utils", source, flags=re.M,
  )
  assert "invalidate_archive_members_ops" in source
  ops_path = (
      _REPO / "hpcperfstats" / "dbload" / "lib" / "invalidate_archive_members_ops.py"
  )
  ops_src = ops_path.read_text(encoding="utf-8")
  assert not re.search(
      r"^\s*(from|import)\s+.*print_utils", ops_src, flags=re.M,
  )
  assert not re.search(
      r"^\s*(from|import)\s+.*conf_parser", ops_src, flags=re.M,
  )
  assert "invalidate_archive_members_sidecars" in ops_src


def test_cli_all_without_yes_errors(cli, compose_dir, tmp_path):
  archive, _members, _job = _seed_archive_sidecars(tmp_path)
  with pytest.raises(SystemExit) as exc:
    cli.main([
        "--all",
        "--archive-dir", str(archive),
        "--compose-dir", str(compose_dir),
    ])
  assert exc.value.code == 2


def test_cli_all_and_day_mutually_exclusive(cli, compose_dir, tmp_path):
  archive, _members, _job = _seed_archive_sidecars(tmp_path)
  with pytest.raises(SystemExit) as exc:
    cli.main([
        "--all",
        "--day", "2026-06-08",
        "--archive-dir", str(archive),
        "--compose-dir", str(compose_dir),
    ])
  assert exc.value.code == 2


def test_cli_dry_run_skips_restart(cli, compose_dir, tmp_path, monkeypatch):
  archive, members, job = _seed_archive_sidecars(tmp_path)
  day = members / "2026-06-08.json"
  calls = []
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.invalidate_archive_members_ops.restart_pipeline_compose",
      lambda **kwargs: calls.append(kwargs),
  )
  rc = cli.main([
      "--day", "2026-06-08",
      "--dry-run",
      "--archive-dir", str(archive),
      "--compose-dir", str(compose_dir),
  ])
  assert rc == 0
  assert calls == []
  assert day.is_file()
  assert job.is_file()


def test_cli_no_restart_skips_compose_restart(cli, compose_dir, tmp_path, monkeypatch):
  archive, members, job = _seed_archive_sidecars(tmp_path)
  day = members / "2026-06-08.json"
  calls = []
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.invalidate_archive_members_ops.restart_pipeline_compose",
      lambda **kwargs: calls.append(kwargs),
  )
  rc = cli.main([
      "--day", "2026-06-08",
      "--no-restart",
      "--archive-dir", str(archive),
      "--compose-dir", str(compose_dir),
  ])
  assert rc == 0
  assert calls == []
  assert not day.exists()
  assert job.is_file()


def test_cli_success_restarts_pipeline(cli, compose_dir, tmp_path, monkeypatch):
  archive, members, job = _seed_archive_sidecars(tmp_path)
  day = members / "2026-06-08.json"
  calls = []
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.invalidate_archive_members_ops.restart_pipeline_compose",
      lambda **kwargs: calls.append(kwargs),
  )
  rc = cli.main([
      "--day", "2026-06-08",
      "--archive-dir", str(archive),
      "--compose-dir", str(compose_dir),
  ])
  assert rc == 0
  assert len(calls) == 1
  assert calls[0]["compose_dir"] == str(compose_dir)
  assert calls[0]["project"] == "hpcperfstats"
  assert not day.exists()
  assert job.is_file()


def test_cli_all_yes_no_restart_preserves_job_store(
    cli, compose_dir, tmp_path, monkeypatch,
):
  archive, members, job = _seed_archive_sidecars(
      tmp_path, days=("2026-06-08", "2026-06-09"),
  )
  calls = []
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.invalidate_archive_members_ops.restart_pipeline_compose",
      lambda **kwargs: calls.append(kwargs),
  )
  rc = cli.main([
      "--all",
      "--yes",
      "--no-restart",
      "--archive-dir", str(archive),
      "--compose-dir", str(compose_dir),
  ])
  assert rc == 0
  assert calls == []
  assert not (members / "2026-06-08.json").exists()
  assert not (members / "2026-06-09.json").exists()
  assert job.is_file()


def test_cli_all_yes_restarts(cli, compose_dir, tmp_path, monkeypatch):
  archive, members, job = _seed_archive_sidecars(tmp_path)
  calls = []
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.invalidate_archive_members_ops.restart_pipeline_compose",
      lambda **kwargs: calls.append(kwargs),
  )
  rc = cli.main([
      "--all",
      "--yes",
      "--archive-dir", str(archive),
      "--compose-dir", str(compose_dir),
  ])
  assert rc == 0
  assert len(calls) == 1
  assert not (members / "2026-06-08.json").exists()
  assert job.is_file()


def test_restart_pipeline_compose_invokes_subprocess(tmp_path):
  (tmp_path / "docker-compose.yaml").write_text("services: {}\n", encoding="utf-8")
  seen = {}

  class _Result:
    returncode = 0
    stdout = ""
    stderr = ""

  def _run(cmd, **kwargs):
    seen["cmd"] = list(cmd)
    seen["cwd"] = kwargs.get("cwd")
    return _Result()

  restart_pipeline_compose(
      compose_dir=str(tmp_path),
      project="hpcperfstats",
      run_fn=_run,
  )
  assert seen["cmd"] == compose_argv(project="hpcperfstats") + ["restart", "pipeline"]
  assert seen["cwd"] == str(tmp_path)
