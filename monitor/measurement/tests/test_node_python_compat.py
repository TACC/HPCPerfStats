"""Regression: the harness runs under the node's stock python3 (3.9 on EL9), not the .venv.

Ruff autofixes once introduced ``datetime.UTC``, ``itertools.pairwise`` and ``zip(strict=)``
(3.10+), which broke ``run_overhead_profile.sh --dry-run`` on EL9.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "fixtures"


def _system_python() -> str:
    for cand in ("/usr/bin/python3", "/usr/libexec/platform-python"):
        if Path(cand).exists():
            return cand
    found = shutil.which("python3")
    if not found:
        pytest.skip("no system python3")
    return found


def test_harness_modules_import_and_run_under_system_python(tmp_path):
    py = _system_python()
    code = (
        "import sys, pathlib; sys.path.insert(0, %r)\n"
        "import importlib\n"
        "for p in sorted(pathlib.Path(%r).glob('lib/*.py')):\n"
        "    importlib.import_module('lib.' + p.stem)\n"
        "from lib import nodesample, leg_summary, orchestrator\n"
        "s = nodesample.parse_proc_stat(open(%r).read()); assert s['cpu']['idle'] > 0\n"
        "assert list(leg_summary._pairs([1, 2, 3])) == [(1, 2), (2, 3)]\n"
        "print('COMPAT_OK', sys.version_info[:2])\n"
    ) % (str(ROOT), str(ROOT), str(FIX / "proc_stat_node.txt"))
    res = subprocess.run([py, "-c", code], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    assert "COMPAT_OK" in res.stdout
    dry = subprocess.run([py, str(ROOT / "bin" / "overhead_profile.py"), "--dry-run", "--rpm", "x.rpm",
                          "--seed", "1"], capture_output=True, text=True)
    assert dry.returncode == 0, dry.stderr
    assert "Projected end:" in dry.stdout
    out = tmp_path / "s.jsonl"
    smp = subprocess.run([py, str(ROOT / "bin" / "sample_proc.py"), "--pid", "1", "--duration", "1",
                          "--out", str(out), "--no-tcp"], capture_output=True, text=True)
    assert smp.returncode == 0, smp.stderr
    assert out.read_text().count("\n") >= 1
