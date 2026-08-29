from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import pytest

STARTUP_SCRIPTS = (
  "services-conf/django_startup.sh",
  "services-conf/supervisor_startup.sh",
  "services-conf/rsync_data_wrapper.sh",
  "services-conf/rsync_data.sh",
  "services-conf/rsync_data.sh.example",
)


def _repo_root() -> Path:
  return Path(__file__).resolve().parents[2]


def test_dockerfile_chmods_compose_startup_scripts():
  dockerfile = (_repo_root() / "Dockerfile").read_text()

  for script in STARTUP_SCRIPTS:
    assert script in dockerfile
    assert f"chmod +x" in dockerfile


def test_startup_scripts_are_executable_in_git_index():
  repo_root = _repo_root()
  try:
    subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
  except (FileNotFoundError, subprocess.CalledProcessError):
    pytest.skip("git repository not available (e.g. compose bind mount without .git)")
  for script in STARTUP_SCRIPTS:
    line = subprocess.check_output(
      ["git", "ls-files", "-s", script],
      cwd=repo_root,
      text=True,
    ).strip()
    mode = int(line.split()[0], 8)
    assert mode & stat.S_IXUSR, f"{script} must be executable in git index (mode {oct(mode)})"


def test_startup_scripts_are_executable_on_disk():
  repo_root = _repo_root()
  for script in STARTUP_SCRIPTS:
    path = repo_root / script
    mode = path.stat().st_mode
    assert mode & stat.S_IXUSR, f"{script} must be executable on disk (mode {oct(mode)})"
