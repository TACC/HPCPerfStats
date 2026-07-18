"""Tests for host archive-members invalidate CLI (compose restart mocked)."""
from __future__ import annotations

import pytest

from hpcperfstats.dbload import invalidate_archive_members as cli
from hpcperfstats.dbload.lib.invalidate_archive_members_ops import (
    compose_argv,
    restart_pipeline_compose,
)


@pytest.fixture
def compose_dir(tmp_path):
  (tmp_path / "docker-compose.yaml").write_text("services: {}\n", encoding="utf-8")
  return tmp_path


def test_cli_all_without_yes_errors(compose_dir, monkeypatch):
  with pytest.raises(SystemExit) as exc:
    cli.main([
        "--all",
        "--compose-dir", str(compose_dir),
    ])
  assert exc.value.code == 2


def test_cli_all_and_day_mutually_exclusive(compose_dir):
  with pytest.raises(SystemExit) as exc:
    cli.main([
        "--all",
        "--day", "2026-06-08",
        "--compose-dir", str(compose_dir),
    ])
  assert exc.value.code == 2


def test_cli_dry_run_skips_restart(compose_dir, monkeypatch):
  calls = []

  class _Client:
    def scan_iter(self, match=None, count=100):
      del match, count
      return iter([])

    def delete(self, *keys):
      del keys
      return 0

  monkeypatch.setattr(cli, "_direct_redis_client", lambda url: _Client())
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.invalidate_archive_members_ops.restart_pipeline_compose",
      lambda **kwargs: calls.append(kwargs),
  )
  rc = cli.main([
      "--day", "2026-06-08",
      "--dry-run",
      "--redis-url", "redis://unused",
      "--compose-dir", str(compose_dir),
  ])
  assert rc == 0
  assert calls == []


def test_cli_no_restart_skips_compose_restart(compose_dir, monkeypatch):
  calls = []

  class _Client:
    def scan_iter(self, match=None, count=100):
      del count
      if match and "2026-06-08" in str(match):
        yield "hpcperfstats:sync_timedb:archive_members:complete:v1:2026-06-08:1:1:1:1"

    def delete(self, *keys):
      return len(keys)

  monkeypatch.setattr(cli, "_direct_redis_client", lambda url: _Client())
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.invalidate_archive_members_ops.restart_pipeline_compose",
      lambda **kwargs: calls.append(kwargs),
  )
  rc = cli.main([
      "--day", "2026-06-08",
      "--no-restart",
      "--redis-url", "redis://unused",
      "--compose-dir", str(compose_dir),
  ])
  assert rc == 0
  assert calls == []


def test_cli_success_restarts_pipeline(compose_dir, monkeypatch):
  calls = []

  class _Client:
    def scan_iter(self, match=None, count=100):
      del count
      if match and "2026-06-08" in str(match):
        yield "hpcperfstats:sync_timedb:archive_members:complete:v1:2026-06-08:1:1:1:1"

    def delete(self, *keys):
      return len(keys)

  monkeypatch.setattr(cli, "_direct_redis_client", lambda url: _Client())
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.invalidate_archive_members_ops.restart_pipeline_compose",
      lambda **kwargs: calls.append(kwargs),
  )
  rc = cli.main([
      "--day", "2026-06-08",
      "--redis-url", "redis://unused",
      "--compose-dir", str(compose_dir),
  ])
  assert rc == 0
  assert len(calls) == 1
  assert calls[0]["compose_dir"] == str(compose_dir)
  assert calls[0]["project"] == "hpcperfstats"


def test_cli_all_yes_restarts(compose_dir, monkeypatch):
  calls = []

  class _Client:
    def scan_iter(self, match=None, count=100):
      del match, count
      return iter([])

    def delete(self, *keys):
      return len(keys)

  monkeypatch.setattr(cli, "_direct_redis_client", lambda url: _Client())
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.invalidate_archive_members_ops.restart_pipeline_compose",
      lambda **kwargs: calls.append(kwargs),
  )
  rc = cli.main([
      "--all",
      "--yes",
      "--redis-url", "redis://unused",
      "--compose-dir", str(compose_dir),
  ])
  assert rc == 0
  assert len(calls) == 1


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
