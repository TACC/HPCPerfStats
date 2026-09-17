"""Contracts for the host-safe Python test runner."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


def _load_runner():
  path = Path(__file__).resolve().parents[2] / "scripts" / "run_tests.py"
  spec = importlib.util.spec_from_file_location("hpcperfstats_run_tests", path)
  assert spec and spec.loader
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def test_no_django_excludes_db_markers_and_bounds_default_collection(monkeypatch):
  runner = _load_runner()
  captured = {}
  monkeypatch.setattr(sys, "argv", ["run_tests.py", "--no-django"])
  monkeypatch.setattr(
      "pytest.main",
      lambda args: captured.setdefault("args", args) or 0,
  )

  runner.main()

  assert captured["args"] == [
      "--ignore=hpcperfstats/site/lib/machine/tests",
      "-m",
      "not django_db",
      "-q",
      "hpcperfstats",
  ]


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


def test_test_login_product_module_is_not_collected_by_pytest():
  from hpcperfstats.site.lib.machine import test_login

  assert test_login.__test__ is False
