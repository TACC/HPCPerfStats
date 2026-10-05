"""Static analysis drift guards (ruff, vulture, pre-commit config)."""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_VENV_BIN = _REPO_ROOT.parent / ".venv" / "bin"
_RUFF = _VENV_BIN / "ruff"
_VULTURE = _VENV_BIN / "vulture"
_RUFF_FORMAT_PATHS = [
  "hpcperfstats",
  "cursor-hooks",
  "scripts",
  "services-conf",
  "hpcperfstats-tools",
]


def _run(
  cmd: list[str], *, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
  return subprocess.run(
    cmd,
    cwd=cwd or _REPO_ROOT,
    check=False,
    capture_output=True,
    text=True,
  )


@pytest.mark.skipif(
  not _RUFF.is_file(), reason="ruff not installed in workspace venv"
)
def test_ruff_lint_and_format_clean():
  lint = _run([str(_RUFF), "check", *_RUFF_FORMAT_PATHS])
  assert lint.returncode == 0, lint.stdout + lint.stderr
  fmt = _run([str(_RUFF), "format", "--check", *_RUFF_FORMAT_PATHS])
  assert fmt.returncode == 0, fmt.stdout + fmt.stderr


@pytest.mark.skipif(
  not _RUFF.is_file(), reason="ruff not installed in workspace venv"
)
def test_ruff_unused_imports_and_variables_clean():
  proc = _run(
    [
      str(_RUFF),
      "check",
      *_RUFF_FORMAT_PATHS,
      "--select",
      "F401,F841,F811",
    ],
  )
  assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.skipif(
  not _RUFF.is_file(), reason="ruff not installed in workspace venv"
)
def test_ruff_format_check_clean():
  proc = _run([str(_RUFF), "format", "--check", *_RUFF_FORMAT_PATHS])
  assert proc.returncode == 0, proc.stdout + proc.stderr


def test_pyproject_ruff_select_includes_isort_and_whitespace():
  data = tomllib.loads(
    (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
  )
  select = set(data["tool"]["ruff"]["lint"]["select"])
  assert {"I", "W"}.issubset(select)


def test_pre_commit_config_exists():
  assert (_REPO_ROOT / ".pre-commit-config.yaml").is_file()


def test_pre_commit_config_includes_python_memory_leak_check():
  text = (_REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
  assert "id: python-memory-leak-check" in text
  assert "scripts/run_commit_memory_leak_check.py" in text
  assert "memray" in text.lower() or "memory-leak" in text


def test_pre_commit_config_includes_ruff_format_hook():
  text = (_REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
  assert "id: ruff-format" in text
  assert "ruff format" in text


def test_pre_commit_config_uses_full_ruff_check_not_f401_only():
  text = (_REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
  assert "id: ruff-check" in text
  assert "--select=F401" not in text
  assert "id: ruff-unused" not in text


@pytest.mark.skipif(
  not _VULTURE.is_file(), reason="vulture not installed in workspace venv"
)
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
