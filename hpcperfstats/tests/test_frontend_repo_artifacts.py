"""Regression: frontend source artifacts required for Docker/npm builds must be git-tracked."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_FRONTEND_LIB_UTILS = "hpcperfstats/site/frontend/src/lib/utils.ts"
_GENERATED_ZOD_DIR = "hpcperfstats/site/frontend/src/api/generated-zod"


def _repo_root() -> Path:
  return Path(__file__).resolve().parents[2]


def _require_git(repo_root: Path) -> None:
  try:
    subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
  except (FileNotFoundError, subprocess.CalledProcessError) as exc:
    pytest.skip(f"git repository not available: {exc}")


def test_frontend_lib_utils_is_git_tracked():
  """Shadcn cn() helper must not be excluded by root lib/ gitignore (Docker COPY)."""
  repo_root = _repo_root()
  _require_git(repo_root)

  utils_path = repo_root / _FRONTEND_LIB_UTILS
  assert utils_path.is_file(), f"missing {_FRONTEND_LIB_UTILS}"

  check_ignore = subprocess.run(
      ["git", "check-ignore", "-q", _FRONTEND_LIB_UTILS],
      cwd=repo_root,
      capture_output=True,
      text=True,
  )
  assert check_ignore.returncode != 0, (
      f"{_FRONTEND_LIB_UTILS} is gitignored; Docker COPY omits it"
  )

  tracked = subprocess.check_output(
      ["git", "ls-files", _FRONTEND_LIB_UTILS],
      cwd=repo_root,
      text=True,
  ).strip()
  assert tracked == _FRONTEND_LIB_UTILS, (
      f"{_FRONTEND_LIB_UTILS} must be tracked in git index"
  )

  text = utils_path.read_text(encoding="utf-8")
  assert "export function cn" in text


def test_generate_api_succeeds_without_prefilled_zod():
  """Orval must generate Zod schemas before mutator parse on a clean tree."""
  repo_root = _repo_root()
  frontend = repo_root / "hpcperfstats/site/frontend"
  zod_dir = repo_root / _GENERATED_ZOD_DIR

  if shutil.which("npm") is None:
    pytest.skip("npm not on PATH")

  if not (frontend / "node_modules").is_dir():
    pytest.skip("frontend node_modules missing; run npm ci in site/frontend first")

  removed: list[Path] = []
  for ts_file in zod_dir.glob("*/*.ts"):
    ts_file.unlink()
    removed.append(ts_file)
  assert removed, "expected prefilled generated-zod/*.ts to delete for regression"

  proc = subprocess.run(
      ["npm", "run", "generate:api"],
      cwd=frontend,
      capture_output=True,
      text=True,
      timeout=120,
  )
  assert proc.returncode == 0, (
      "generate:api failed on clean generated-zod tree\n"
      f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
  )

  recreated = list(zod_dir.glob("*/*.ts"))
  assert recreated, "generate:api did not recreate generated-zod output"

  combined = proc.stdout + proc.stderr
  assert "Failed to parse provided mutator function" not in combined
  assert "Could not resolve \"./generated-zod/" not in combined
