"""Regression: test-only paths must stay out of the Docker build context."""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path


def _repo_root() -> Path:
  return Path(__file__).resolve().parents[2]


def _dockerignore_patterns(repo_root: Path) -> list[str]:
  patterns: list[str] = []
  for line in (repo_root / ".dockerignore").read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#"):
      patterns.append(line)
  return patterns


def _match_dockerignore(rel_path: str, pattern: str) -> bool:
  rel = rel_path.replace("\\", "/").lstrip("./")
  pattern = pattern.strip()
  if not pattern:
    return False

  if pattern.endswith("/"):
    dir_pat = pattern[:-1]
    if dir_pat == "**":
      return True
    if "**" in dir_pat:
      prefix, _, suffix = dir_pat.partition("**/")
      if prefix and not (rel == prefix.rstrip("/") or rel.startswith(prefix)):
        return False
      if suffix:
        return (
            f"/{suffix}/" in f"/{rel}/"
            or rel.endswith(f"/{suffix}")
            or rel == suffix
        )
      return True
    if dir_pat.startswith("**/"):
      name = dir_pat[3:]
      return (
          f"/{name}/" in f"/{rel}/"
          or rel.startswith(f"{name}/")
          or rel == name
      )
    return rel == dir_pat or rel.startswith(f"{dir_pat}/")

  if fnmatch.fnmatch(rel, pattern):
    return True

  if "**" in pattern:
    regex = "^" + fnmatch.translate(pattern).replace(r"\*\*", ".*") + "$"
    if re.match(regex, rel):
      return True

  return fnmatch.fnmatch(rel.split("/")[-1], pattern)


def _is_excluded(rel_path: str, patterns: list[str]) -> bool:
  rel = rel_path.replace("\\", "/").lstrip("./")
  if rel.startswith("monitor/"):
    return True
  for pattern in patterns:
    if _match_dockerignore(rel, pattern):
      return True
  return False


def _looks_like_test_artifact(rel_path: str) -> bool:
  rel = rel_path.replace("\\", "/").lstrip("./")
  if rel.startswith("node_modules/") or "/node_modules/" in rel:
    return False
  parts = rel.split("/")
  if "tests" in parts:
    return True
  if rel.startswith("test_runs/"):
    return True
  if "/__tests__/" in f"/{rel}/":
    return True

  name = parts[-1]
  if name.endswith((".test.ts", ".test.tsx")):
    return True
  if name.startswith("test_") and name.endswith(".py"):
    return True
  if name == "conftest.py":
    return True
  if "/test-utils/" in f"/{rel}/" or "/test-fixtures/" in f"/{rel}/":
    return True
  if name in {
      "vitest.config.ts",
      "setupTests.ts",
      "axe-test-utils.ts",
      "playwright-bokeh-bundle-smoke.ts",
      "bokeh-playwright-smoke.html",
      "fix-tests-next.mjs",
      "playwright_axe.py",
  }:
    return True
  if rel.startswith("scripts/test_") and rel.endswith(".sh"):
    return True
  return False


def test_dockerignore_lists_required_test_patterns():
  content = (_repo_root() / ".dockerignore").read_text()
  required = (
      "tests/",
      "**/tests/",
      "test_runs/",
      "**/conftest.py",
      "scripts/test_*.sh",
      "**/*.test.ts",
      "**/__tests__/",
  )
  for pattern in required:
    assert pattern in content, f"missing .dockerignore pattern: {pattern}"


def test_all_test_artifacts_excluded_from_docker_build_context():
  repo_root = _repo_root()
  patterns = _dockerignore_patterns(repo_root)
  missing: list[str] = []

  for path in repo_root.rglob("*"):
    if not path.is_file():
      continue
    rel = path.relative_to(repo_root).as_posix()
    if not _looks_like_test_artifact(rel):
      continue
    if not _is_excluded(rel, patterns):
      missing.append(rel)

  assert not missing, (
      "test artifacts not covered by .dockerignore (first 20):\n"
      + "\n".join(missing[:20])
  )
