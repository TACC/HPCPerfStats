"""Tests for scripts/seal_open_daily_tars_to_zst.sh --seal-all.

zstd is replaced by an identity (cat) shim so the script's control flow and
member checks run on any host; GNU tar and python3 are real.
"""

from __future__ import annotations

import io
import os
import shutil
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


def _space_shims(env: dict, avail_bytes: int) -> Path:
  """Fake df free space and log every real tar call to tar_calls.log."""
  calls = env["tmp"] / "tar_calls.log"
  real_tar = shutil.which("tar")
  (env["bin"] / "df").write_text(
    f"#!/usr/bin/env bash\necho Avail\necho {avail_bytes}\n"
  )
  (env["bin"] / "tar").write_text(
    f'#!/usr/bin/env bash\necho "$*" >>"{calls}"\nexec "{real_tar}" "$@"\n'
  )
  for name in ("df", "tar"):
    (env["bin"] / name).chmod(0o755)
  return calls


@pytest.mark.machine_unit_mock
def test_reservation_uses_reserve_pct_not_a_third_of_the_tar(env: dict):
  """A day whose zst fits at --reserve-pct seals even when tar/3 would not.

  hpcperfstats01 2026-11-04: tar/3 asked 490-740 GiB for 1.4-2.2 TB days
  whose zst is ~4%, so only small days ever sealed.
  """
  day = (date.today() - timedelta(days=30)).isoformat()
  tar = env["daily"] / f"{day}.tar"
  _write_tar(tar, {"h1/1": b"x" * 30000})
  size = tar.stat().st_size
  _space_shims(env, size // 5)  # room for 10%, not for 33%
  res = _run(env, "--apply", "--seal-all")
  assert res.returncode == 0, res.stdout + res.stderr
  assert not tar.exists()
  assert "WAIT" not in res.stdout


@pytest.mark.machine_unit_mock
def test_day_waiting_for_space_is_not_listed(env: dict):
  """WAIT is decided from free space before the multi-TB tar is listed."""
  day = (date.today() - timedelta(days=30)).isoformat()
  tar = env["daily"] / f"{day}.tar"
  _write_tar(tar, {"h1/1": b"x" * 30000})
  calls = _space_shims(env, tar.stat().st_size // 5)
  res = _run(env, "--apply", "--seal-all", "--reserve-pct", "50")
  assert tar.exists()
  assert f"WAIT {day}" in res.stdout
  assert "not enough free space" in res.stdout
  listed = calls.read_text() if calls.exists() else ""
  assert str(tar) not in listed, listed


@pytest.mark.machine_unit_mock
def test_untried_days_run_before_any_retry(env: dict):
  """A RETRY must never take the slot while later tars are still untried.

  hpcperfstats01 2026-11-04: after one seal freed space the single slot
  cycled RETRY -> WAIT over the waiting days and only 10 of 54 tars were
  ever looked at.
  """
  base = date.today() - timedelta(days=30)
  big, small1, small2 = (
    (base + timedelta(days=i)).isoformat() for i in range(3)
  )
  _write_tar(env["daily"] / f"{big}.tar", {"h1/1": os.urandom(300000)})
  for day in (small1, small2):
    _write_tar(env["daily"] / f"{day}.tar", {"h1/1": b"x" * 30000})
  _space_shims(env, 100000)  # 50% of a small tar fits, of the big one not
  res = _run(env, "--apply", "--seal-all", "--jobs", "1", "--reserve-pct", "50")
  out = res.stdout
  assert f"DONE {small2}" in out, out
  assert out.index(f"SEAL {small2}") < out.index(f"RETRY {big}"), out
  assert (env["daily"] / f"{big}.tar").exists()
