"""Smoke tests for scripts/backfill_host_data_null_dev.sh operator helper."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "backfill_host_data_null_dev.sh"


@pytest.mark.machine_unit_mock
def test_backfill_host_data_null_dev_script_exists_and_executable():
  assert SCRIPT.is_file(), SCRIPT
  assert SCRIPT.stat().st_mode & 0o111, "script must be executable"


@pytest.mark.machine_unit_mock
def test_backfill_host_data_null_dev_bash_n_clean():
  completed = subprocess.run(
      ["bash", "-n", str(SCRIPT)],
      check=False,
      capture_output=True,
      text=True,
  )
  assert completed.returncode == 0, completed.stderr


@pytest.mark.machine_unit_mock
@pytest.mark.parametrize("bad_arg", ["0", "-1", "x"])
def test_backfill_host_data_null_dev_rejects_bad_concurrency(bad_arg):
  completed = subprocess.run(
      [str(SCRIPT), bad_arg],
      check=False,
      capture_output=True,
      text=True,
  )
  assert completed.returncode == 2
  assert "usage:" in completed.stderr
  assert "concurrency must be a positive integer" in completed.stderr
