"""Contracts for the host-safe Python test runner."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path


def _load_runner():
  path = Path(__file__).resolve().parents[2] / "scripts" / "run_tests.py"
  spec = importlib.util.spec_from_file_location("hpcperfstats_run_tests", path)
  assert spec and spec.loader
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def test_no_django_excludes_db_markers_and_bounds_default_collection(
  monkeypatch,
):
  runner = _load_runner()
  captured = {}
  monkeypatch.setattr(sys, "argv", ["run_tests.py", "--no-django"])
  monkeypatch.setattr(
    "pytest.main",
    lambda args: captured.setdefault("args", args) or 0,
  )

  runner.main()

  assert captured["args"] == ["-m", "not compose_db", "-q", "hpcperfstats"]


def test_no_django_preserves_explicit_targets(monkeypatch):
  runner = _load_runner()
  captured = {}
  target = "hpcperfstats/tests/test_file_locking.py"
  monkeypatch.setattr(sys, "argv", ["run_tests.py", "--no-django", target])
  monkeypatch.setattr(
    "pytest.main",
    lambda args: captured.setdefault("args", args) or 0,
  )

  runner.main()

  assert captured["args"][-1] == target
  assert "hpcperfstats" not in captured["args"]


def test_no_django_collects_host_tests_and_drops_default_db_tests():
  """``--no-django`` keeps ``databases=[]`` tests, drops default-DB ones.

  The root conftest gives every ``hpcperfstats/tests`` item a
  ``django_db(databases=[])`` marker on the host, so the selection must key
  on ``compose_db``; a ``not django_db`` expression would drop them all.
  """
  root = Path(__file__).resolve().parents[2]
  host = (
    "hpcperfstats/tests/test_file_locking.py"
    "::test_file_write_lock_creates_corresponding_lock_file"
  )
  db = (
    "hpcperfstats/tests/test_sync_timedb_db_offload_high_confidence.py"
    "::test_reference_proc_upsert_peak_matrix_compose"
  )
  env = {
    k: v for k, v in os.environ.items() if k != "HPCPERFSTATS_COMPOSE_NETWORK"
  }
  out = subprocess.run(
    [
      sys.executable,
      "scripts/run_tests.py",
      "--no-django",
      "--collect-only",
      "-q",
      "-p",
      "no:cacheprovider",
      host,
      db,
    ],
    cwd=root,
    env=env,
    capture_output=True,
    text=True,
    check=False,
  ).stdout
  assert host in out.splitlines()
  assert db not in out.splitlines()


def test_test_login_product_module_is_not_collected_by_pytest():
  from hpcperfstats.site.lib.machine import test_login

  assert test_login.__test__ is False
