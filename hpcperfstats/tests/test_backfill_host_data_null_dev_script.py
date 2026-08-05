"""Smoke tests for scripts/backfill_host_data_null_dev.sh operator helper."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "backfill_host_data_null_dev.sh"
THROTTLE = REPO_ROOT / "scripts" / "lib" / "backfill_host_data_null_dev_throttle.sh"


def _bash_source_call(fn: str, *args: str) -> str:
  quoted = " ".join(f"'{a}'" for a in args)
  completed = subprocess.run(
      [
          "bash",
          "-c",
          f"source '{THROTTLE}' && {fn} {quoted}",
      ],
      check=False,
      capture_output=True,
      text=True,
  )
  assert completed.returncode == 0, completed.stderr
  return completed.stdout.strip()


@pytest.mark.machine_unit_mock
def test_backfill_host_data_null_dev_script_exists_and_executable():
  assert SCRIPT.is_file(), SCRIPT
  assert SCRIPT.stat().st_mode & 0o111, "script must be executable"
  assert THROTTLE.is_file(), THROTTLE


@pytest.mark.machine_unit_mock
def test_backfill_host_data_null_dev_bash_n_clean():
  for path in (SCRIPT, THROTTLE):
    completed = subprocess.run(
        ["bash", "-n", str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, f"{path}: {completed.stderr}"


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
  assert "max_concurrency must be a positive integer" in completed.stderr


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
def test_backfill_host_data_null_dev_uses_time_ranges_not_offset_paging():
  text = SCRIPT.read_text(encoding="utf-8")
  assert "time >=" in text
  assert "time <" in text
  assert "pg_stat_replication" in text
  assert "pg_control_checkpoint" in text
  assert "pg_wal_lsn_diff" in text
  assert "null_dev_adjust_concurrency" in text
  assert 'MAX_CONCURRENCY="${1:-30}"' in text
  # Executable lines (strip comments) must not use OFFSET row paging, post-VACUUM
  # sleep, or the misleading on-disk WAL sum.
  code_only = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
  assert "OFFSET" not in code_only.upper()
  assert "sleep " not in code_only
  assert "pg_ls_waldir" not in code_only
  assert "psql_vacuum_chunk" in code_only
  # VACUUM must not share a single -c with SET (implicit transaction block).
  assert "SET statement_timeout = 0; VACUUM" not in code_only
  assert '-c "SET statement_timeout = 0"' in code_only
  assert '-c "VACUUM (ANALYZE) ${chunk};"' in code_only


@pytest.mark.machine_unit_mock
def test_null_dev_eval_pressure_trips_on_lag_wal_and_disk():
  assert _bash_source_call("null_dev_eval_pressure", "0", "30", "-1", "-1", "0.70", "-1", "-1") == "0"
  assert _bash_source_call("null_dev_eval_pressure", "31", "30", "-1", "-1", "0.70", "-1", "-1") == "1"
  # Checkpoint-WAL 80 of 100 with 0.70 frac → pressure
  assert _bash_source_call("null_dev_eval_pressure", "0", "30", "80", "100", "0.70", "-1", "-1") == "1"
  assert _bash_source_call("null_dev_eval_pressure", "0", "30", "50", "100", "0.70", "-1", "-1") == "0"
  # Healthy uncheckpointed WAL under 70% of max_wal_size (~6 GiB)
  assert _bash_source_call(
      "null_dev_eval_pressure", "0", "30", "2000000000", "6442450944", "0.70", "-1", "-1"
  ) == "0"
  assert _bash_source_call("null_dev_eval_pressure", "0", "30", "-1", "-1", "0.70", "100", "200") == "1"
  assert _bash_source_call("null_dev_eval_pressure", "0", "30", "-1", "-1", "0.70", "500", "200") == "0"


@pytest.mark.machine_unit_mock
def test_null_dev_adjust_concurrency_ramp_and_backoff():
  # pressure → back off
  assert _bash_source_call("null_dev_adjust_concurrency", "4", "8", "1", "1", "0", "0", "0") == "3"
  # latency 2x+ baseline → back off
  assert _bash_source_call(
      "null_dev_adjust_concurrency", "4", "8", "1", "0", "0", "2500", "1000", "2.0", "3"
  ) == "3"
  # healthy streak met → ramp
  assert _bash_source_call(
      "null_dev_adjust_concurrency", "2", "8", "1", "0", "3", "100", "100", "2.0", "3"
  ) == "3"
  # hold when streak short
  assert _bash_source_call(
      "null_dev_adjust_concurrency", "2", "8", "1", "0", "2", "100", "100", "2.0", "3"
  ) == "2"
  # floor at min under pressure
  assert _bash_source_call("null_dev_adjust_concurrency", "1", "8", "1", "1", "0", "0", "0") == "1"


@pytest.mark.machine_unit_mock
def test_null_dev_update_baseline_ewma():
  assert _bash_source_call("null_dev_update_baseline", "0", "1000", "0.2") == "1000"
  out = _bash_source_call("null_dev_update_baseline", "1000", "2000", "0.2")
  assert out == "1200"


@pytest.mark.machine_unit_mock
def test_backfill_printf_unknown_metric_sentinel_not_treated_as_option():
  """Regression: printf '-1\\n' fails on bash (invalid option); use format + arg."""
  text = SCRIPT.read_text(encoding="utf-8")
  code_only = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
  assert "printf '-1" not in code_only
  assert "printf '%s\\n' '-1'" in code_only
  completed = subprocess.run(
      ["bash", "-c", "printf '%s\\n' '-1'"],
      check=False,
      capture_output=True,
      text=True,
  )
  assert completed.returncode == 0
  assert completed.stdout.strip() == "-1"
