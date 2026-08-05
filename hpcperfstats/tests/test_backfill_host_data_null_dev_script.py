"""Smoke tests for scripts/backfill_host_data_null_dev.sh operator helper."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "backfill_host_data_null_dev.sh"
THROTTLE = REPO_ROOT / "scripts" / "lib" / "backfill_host_data_null_dev_throttle.sh"


@pytest.mark.machine_unit_mock
def test_backfill_host_data_null_dev_script_exists_and_executable():
  assert SCRIPT.is_file(), SCRIPT
  assert SCRIPT.stat().st_mode & 0o111, "script must be executable"
  assert not THROTTLE.exists(), "adaptive throttle helper must be removed"


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


@pytest.mark.machine_unit_mock
def test_backfill_host_data_null_dev_progress_is_chunk_catalog_not_null_row_selects():
  """Regression: progress/pickers must not SELECT/count NULL rows on host_data."""
  text = SCRIPT.read_text(encoding="utf-8")
  assert "remaining_chunks" in text
  assert "timescaledb_information.chunks" in text
  assert "SELECT count(*) FROM host_data WHERE dev IS NULL" not in text
  assert "EXISTS (" not in text
  assert "FROM host_data h" not in text


@pytest.mark.machine_unit_mock
def test_backfill_host_data_null_dev_fixed_cli_concurrency_no_load_detection():
  text = SCRIPT.read_text(encoding="utf-8")
  assert 'CONCURRENCY="${1:-30}"' in text
  assert "time >=" in text
  assert "time <" in text
  code_only = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
  assert "OFFSET" not in code_only.upper()
  assert "sleep " not in code_only
  assert "pg_stat_replication" not in code_only
  assert "pg_control_checkpoint" not in code_only
  assert "pg_wal_lsn_diff" not in code_only
  assert "pg_ls_waldir" not in code_only
  assert "maybe_adapt_concurrency" not in code_only
  assert "null_dev_eval_pressure" not in code_only
  assert "null_dev_adjust_concurrency" not in code_only
  assert "psql_vacuum_chunk" in code_only
  assert "SET statement_timeout = 0; VACUUM" not in code_only
  assert "VACUUM (ANALYZE, PARALLEL" in code_only
  assert 'VACUUM_PARALLEL="${HPCPERFSTATS_NULL_DEV_VACUUM_PARALLEL:-8}"' in text
  assert "vacuum_inflight" in code_only
  assert "drain_vacuums" in code_only
  # VACUUM is backgrounded so fill_slots is not blocked on it.
  assert "launched vacuum pid=" in text
