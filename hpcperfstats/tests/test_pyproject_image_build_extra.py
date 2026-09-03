"""image-build optional-dependencies stay out of runtime / host extras."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path


def _repo_root() -> Path:
  return Path(__file__).resolve().parents[2]


def _pep508_name(dep: str) -> str:
  return re.split(r"[=<>~;!@[ ]", dep, maxsplit=1)[0].strip().lower()


def test_image_build_extra_is_image_only_and_pinned():
  proj = tomllib.loads((_repo_root() / "pyproject.toml").read_text())["project"]
  build = proj["optional-dependencies"]["image-build"]
  names = {_pep508_name(d) for d in build}
  assert {"mkl", "mkl-devel", "meson-python", "meson", "ninja", "cython"} <= names

  runtime = {_pep508_name(d) for d in proj["dependencies"]}
  test_names = {
      _pep508_name(d) for d in proj["optional-dependencies"]["test"]
  }
  dev_names = {_pep508_name(d) for d in proj["optional-dependencies"]["dev"]}
  assert names.isdisjoint(runtime)
  assert names.isdisjoint(test_names)
  assert names.isdisjoint(dev_names)

  mkl_lines = [d for d in build if _pep508_name(d) in {"mkl", "mkl-devel"}]
  for line in mkl_lines:
    assert "sys_platform" in line
    assert "platform_machine" in line
    assert "linux" in line
    assert "x86_64" in line

  assert "mkl==2026.1.0" in "\n".join(mkl_lines)
  assert any("meson-python==0.20.0" in d for d in build)
  assert "scipy" not in runtime
  assert "bokeh==3.9.2" in proj["dependencies"]
