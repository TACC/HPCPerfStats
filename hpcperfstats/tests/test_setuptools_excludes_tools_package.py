"""Regression: monorepo client package must not join the server distribution.

``hpcperfstats-tools/hpcperfstats_tools`` lives in-tree but remains a separate
installable package. Main ``pyproject.toml`` uses ``include = ["hpcperfstats*"]``,
which would otherwise match ``hpcperfstats_tools``.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]


def _candidate_packages(repo_root: Path) -> list[str]:
  """Return top-level import names that look like setuptools packages.

  Args:
    repo_root (Path): Git checkout root containing ``pyproject.toml``.

  Returns:
    list[str]: Dotted package names under ``repo_root`` with ``__init__.py``.

  Examples:
    >>> isinstance(_candidate_packages(_REPO_ROOT), list)
    True
  """
  names: list[str] = []
  for init in repo_root.rglob("__init__.py"):
    rel = init.parent.relative_to(repo_root)
    if any(part.startswith(".") for part in rel.parts):
      continue
    names.append(".".join(rel.parts))
  return names


def test_include_glob_would_match_hpcperfstats_tools_without_exclude() -> None:
  """Prove the include glob alone would absorb the client package."""
  tools_pkg = _REPO_ROOT / "hpcperfstats-tools" / "hpcperfstats_tools"
  assert tools_pkg.is_dir(), "expected in-tree hpcperfstats-tools/hpcperfstats_tools"
  assert (tools_pkg / "__init__.py").is_file()
  assert fnmatch.fnmatch("hpcperfstats_tools", "hpcperfstats*")


def test_find_candidates_with_exclude_omit_hpcperfstats_tools() -> None:
  """Discovery with the same exclude patterns must omit hpcperfstats_tools."""
  include = ["hpcperfstats*"]
  exclude = ["hpcperfstats_tools", "hpcperfstats_tools.*"]
  found = [
      name
      for name in _candidate_packages(_REPO_ROOT)
      if any(fnmatch.fnmatch(name, pat) for pat in include)
      and not any(fnmatch.fnmatch(name, pat) for pat in exclude)
  ]
  assert "hpcperfstats_tools" not in found
  assert not any(name.startswith("hpcperfstats_tools.") for name in found)
  assert "hpcperfstats" in found


def test_pyproject_declares_hpcperfstats_tools_exclude() -> None:
  """pyproject.toml must keep the setuptools exclude for the client package."""
  text = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
  assert 'exclude = ["hpcperfstats_tools", "hpcperfstats_tools.*"]' in text
  assert 'include = ["hpcperfstats*"]' in text
