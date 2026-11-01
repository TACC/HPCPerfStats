"""Tests for scripts/seal_open_daily_tars_to_zst.sh --seal-all.

zstd is replaced by an identity (cat) shim so the script's control flow and
member checks run on any host; GNU tar and python3 are real.
"""

from __future__ import annotations

import io
import os
import subprocess
import tarfile
from datetime import date, timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "seal_open_daily_tars_to_zst.sh"

ZSTD_SHIM = """#!/usr/bin/env bash
out=""; in=""; dec=0
while [ $# -gt 0 ]; do
  case "$1" in -dc) dec=1 ;; -o) out=$2; shift ;; -*) ;; *) in=$1 ;; esac
  shift
done
if [ "$dec" -eq 1 ]; then cat "${in:--}"; else cat "${in:--}" >"$out"; fi
"""


def _write_tar(path: Path, members: dict[str, bytes]) -> None:
  with tarfile.open(path, "w", format=tarfile.GNU_FORMAT) as tar:
    for name, data in members.items():
      info = tarfile.TarInfo(name)
      info.size = len(data)
      tar.addfile(info, io.BytesIO(data))


def _names(path: Path) -> set[str]:
  with tarfile.open(path, "r:") as tar:
    return set(tar.getnames())


@pytest.fixture
def env(tmp_path: Path) -> dict:
  bindir = tmp_path / "bin"
  bindir.mkdir()
  shim = bindir / "zstd"
  shim.write_text(ZSTD_SHIM)
  shim.chmod(0o755)
  daily = tmp_path / "daily"
  archive = tmp_path / "archive"
  daily.mkdir()
  archive.mkdir()
  return {"daily": daily, "archive": archive, "bin": bindir, "tmp": tmp_path}


def _run(env: dict, *args: str) -> subprocess.CompletedProcess:
  run_env = dict(os.environ)
  run_env["PATH"] = f"{env['bin']}:{run_env['PATH']}"
  run_env["TMPDIR"] = str(env["tmp"])
  return subprocess.run(
    [
      "bash",
      str(SCRIPT),
      "--daily",
      str(env["daily"]),
      "--archive",
      str(env["archive"]),
      "--margin-gb",
      "0",
      "--allow-running",
      "--log",
      str(env["tmp"] / "seal.log"),
      *args,
    ],
    check=False,
    capture_output=True,
    text=True,
    env=run_env,
  )


def _scenario(env: dict) -> dict[str, str]:
  daily = env["daily"]
  old = (date.today() - timedelta(days=30)).isoformat()
  kept = (date.today() - timedelta(days=20)).isoformat()
  young = date.today().isoformat()
  conflict = (date.today() - timedelta(days=10)).isoformat()
  big = {"h1/1": b"x" * 30000}
  for day in (old, kept, young, conflict):
    _write_tar(daily / f"{day}.tar", big)
  # Existing .tar.zst (identity shim) holding a member the open tar lacks.
  _write_tar(daily / f"{conflict}.tar.zst", {"h1/1": b"x", "h2/2": b"y"})
  return {"old": old, "kept": kept, "young": young, "conflict": conflict}


@pytest.mark.machine_unit_mock
def test_seal_all_flag_is_documented_and_parses():
  """--seal-all must be in usage and the script must stay bash -n clean."""
  assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0
  out = subprocess.run(
    ["bash", str(SCRIPT), "--help"], capture_output=True, text=True
  ).stdout
  assert "--seal-all" in out


@pytest.mark.machine_unit_mock
def test_default_run_skips_young_and_kept_open_days(env: dict):
  """Without --seal-all the age and --keep-open gates still hold days open."""
  days = _scenario(env)
  res = _run(env, "--apply", "--no-job-store", "--keep-open", days["kept"])
  assert res.returncode == 0, res.stdout + res.stderr
  daily = env["daily"]
  assert not (daily / f"{days['old']}.tar").exists()
  assert (daily / f"{days['kept']}.tar").exists()
  assert (daily / f"{days['young']}.tar").exists()


@pytest.mark.machine_unit_mock
def test_seal_all_seals_every_open_tar_but_keeps_member_checks(env: dict):
  """--seal-all ignores age/keep-open/job store, never the zst member check."""
  days = _scenario(env)
  # No job store snapshot on disk and no --no-job-store: --seal-all must not
  # need it.
  res = _run(env, "--apply", "--seal-all", "--keep-open", days["kept"])
  assert res.returncode == 0, res.stdout + res.stderr
  daily = env["daily"]
  for key in ("old", "kept", "young"):
    assert not (daily / f"{days[key]}.tar").exists(), key
    assert _names(daily / f"{days[key]}.tar.zst") == {"h1/1"}, key
  # Member check kept: the older .tar.zst with h2/2 must not be overwritten.
  assert (daily / f"{days['conflict']}.tar").exists()
  assert _names(daily / f"{days['conflict']}.tar.zst") == {"h1/1", "h2/2"}
  assert "pipeline reconcile" in res.stdout


@pytest.mark.machine_unit_mock
def test_seal_all_still_skips_unlistable_tar(env: dict):
  """A tar GNU tar cannot list is never sealed, even with --seal-all."""
  day = (date.today() - timedelta(days=30)).isoformat()
  bad = env["daily"] / f"{day}.tar"
  bad.write_bytes(os.urandom(40000))
  res = _run(env, "--apply", "--seal-all")
  assert bad.exists()
  assert not (env["daily"] / f"{day}.tar.zst").exists()
  assert "tar listing failed" in res.stdout
