"""Static analysis drift guards (ruff, vulture, pre-commit config)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_VENV_BIN = _REPO_ROOT.parent / ".venv" / "bin"
_RUFF = _VENV_BIN / "ruff"
_VULTURE = _VENV_BIN / "vulture"


def _run(cmd: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
  return subprocess.run(
      cmd,
      cwd=cwd or _REPO_ROOT,
      check=False,
      capture_output=True,
      text=True,
  )


@pytest.mark.skipif(not _RUFF.is_file(), reason="ruff not installed in workspace venv")
def test_ruff_unused_imports_and_variables_clean():
  proc = _run(
      [
          str(_RUFF),
          "check",
          "hpcperfstats",
          "cursor-hooks",
          "scripts",
          "--select",
          "F401,F841,F811",
      ],
  )
  tools_root = _REPO_ROOT / "hpcperfstats-tools"
  if tools_root.is_dir():
    proc_tools = _run(
        [
            str(_RUFF),
            "check",
            str(tools_root),
            "--select",
            "F401,F841,F811",
        ],
    )
    assert proc_tools.returncode == 0, proc_tools.stdout + proc_tools.stderr
  assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.skipif(not _VULTURE.is_file(), reason="vulture not installed in workspace venv")
def test_vulture_no_high_confidence_dead_code():
  proc = _run(
      [
          str(_VULTURE),
          "hpcperfstats",
          "scripts/vulture_whitelist.py",
          "--min-confidence",
          "80",
      ],
  )
  assert proc.returncode == 0, proc.stdout + proc.stderr


def test_pre_commit_config_exists():
  assert (_REPO_ROOT / ".pre-commit-config.yaml").is_file()


def test_pre_commit_config_includes_python_memory_leak_check():
  text = (_REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
  assert "id: python-memory-leak-check" in text
  assert "scripts/run_commit_memory_leak_check.py" in text
  assert "memray" in text.lower() or "memory-leak" in text
