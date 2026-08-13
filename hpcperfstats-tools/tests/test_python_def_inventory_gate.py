"""Mirror gate: hpcperfstats_tools package must pass docstring/hint inventory."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

TOOLS_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = TOOLS_ROOT.parent
HPCP_SCRIPTS = WORKSPACE / "HPCPerfStats" / "scripts"
INV_PATH = HPCP_SCRIPTS / "python_def_inventory.py"


def _load_inv():
  """Load inventory module from the HPCPerfStats scripts tree.

  Returns:
    module: ``python_def_inventory``.
  """
  spec = importlib.util.spec_from_file_location("python_def_inventory", INV_PATH)
  assert spec and spec.loader
  mod = importlib.util.module_from_spec(spec)
  sys.modules["python_def_inventory"] = mod
  spec.loader.exec_module(mod)
  return mod


@pytest.mark.skipif(not INV_PATH.is_file(), reason="HPCPerfStats inventory script missing")
def test_tools_package_inventory_check_green():
  """All hpcperfstats_tools defs pass the Google docstring + hint gate."""
  inv = _load_inv()
  rc = inv.main(["--root", str(TOOLS_ROOT), "--check", "--path-filter", "hpcperfstats_tools"])
  assert rc == 0
