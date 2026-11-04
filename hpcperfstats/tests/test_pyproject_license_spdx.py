"""Regression: ``project.license`` must be an SPDX string, not a TOML table.

setuptools>=77 warns that ``license = { file = "LICENSE" }`` is deprecated and
stops supporting it after 2027-02-18. Both distributions (server and
``hpcperfstats-tools``) must use a PEP 639 SPDX expression plus
``license-files``, must not keep ``License ::`` classifiers (setuptools rejects
them alongside an expression), and must require a setuptools that supports it.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PYPROJECTS = [
  _REPO_ROOT / "pyproject.toml",
  _REPO_ROOT / "hpcperfstats-tools" / "pyproject.toml",
]


def _load(path: Path) -> dict:
  """Parse one ``pyproject.toml``.

  Args:
    path (Path): File to parse.

  Returns:
    dict: Parsed TOML document.

  Examples:
    >>> "project" in _load(_PYPROJECTS[0])
    True
  """
  with path.open("rb") as fh:
    return tomllib.load(fh)


@pytest.mark.parametrize("path", _PYPROJECTS, ids=lambda p: p.parent.name)
def test_license_is_spdx_expression(path: Path) -> None:
  """License is the SPDX id matching the shipped LGPL-2.1 text."""
  project = _load(path)["project"]
  assert project["license"] == "LGPL-2.1-only"
  assert project["license-files"] == ["LICENSE"]
  license_text = (path.parent / "LICENSE").read_text(encoding="utf-8")
  assert "GNU LESSER GENERAL PUBLIC LICENSE" in license_text
  assert "Version 2.1" in license_text


@pytest.mark.parametrize("path", _PYPROJECTS, ids=lambda p: p.parent.name)
def test_no_license_classifiers(path: Path) -> None:
  """setuptools errors when License classifiers accompany an expression."""
  classifiers = _load(path)["project"].get("classifiers", [])
  assert not [c for c in classifiers if c.startswith("License ::")]


@pytest.mark.parametrize("path", _PYPROJECTS, ids=lambda p: p.parent.name)
def test_build_requires_setuptools_with_pep639(path: Path) -> None:
  """Build backend floor must be >=77 (first setuptools with PEP 639)."""
  requires = _load(path)["build-system"]["requires"]
  pins = [r for r in requires if r.startswith("setuptools")]
  assert len(pins) == 1
  match = re.fullmatch(r"setuptools>=(\d+)(?:\.\d+)*", pins[0])
  assert match is not None
  assert int(match.group(1)) >= 77
