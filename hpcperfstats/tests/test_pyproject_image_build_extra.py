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
  assert {
      "mkl",
      "mkl-devel",
      "meson-python",
      "meson",
      "ninja",
      "cython",
      "setuptools",
      "wheel",
      "versioneer",
  } <= names

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
  assert any(d.startswith("setuptools>=") for d in build)
  assert "scipy" not in runtime
  assert "bokeh==3.10.0" in proj["dependencies"]


def test_image_build_extra_includes_setuptools_for_no_build_isolation():
  """Regression: numexpr sdist needs setuptools.build_meta under --no-build-isolation."""
  proj = tomllib.loads((_repo_root() / "pyproject.toml").read_text())["project"]
  build = proj["optional-dependencies"]["image-build"]
  names = {_pep508_name(d) for d in build}
  assert "setuptools" in names
  assert "wheel" in names
  # Must not rely on the base image having setuptools; image-build installs it.
  dockerfile = (_repo_root() / "Dockerfile").read_text()
  assert "--no-build-isolation" in dockerfile
  assert "setuptools" in dockerfile


def test_image_build_extra_includes_versioneer_for_pandas_meson():
  """Regression: pandas 3 meson generate_version.py imports versioneer under --no-build-isolation."""
  proj = tomllib.loads((_repo_root() / "pyproject.toml").read_text())["project"]
  build = proj["optional-dependencies"]["image-build"]
  names = {_pep508_name(d) for d in build}
  assert "versioneer" in names
  assert any("versioneer[toml]==0.29" in d for d in build)
  dockerfile = (_repo_root() / "Dockerfile").read_text()
  assert "versioneer" in dockerfile
  writer_assert = next(
      line
      for line in dockerfile.splitlines()
      if "build_names" in line and "versioneer" in line
  )
  assert "versioneer" in writer_assert
  assert "cython" in writer_assert
  assert "meson" in writer_assert
  assert "ninja" in writer_assert


def test_dockerfile_python_build_path_exposes_image_build_scripts():
  """Meson needs cython on PATH; python-build must set prefix bin PATH early."""
  dockerfile = (_repo_root() / "Dockerfile").read_text()
  # Stage-local: find PATH= after FT bake / before numpy MKL install.
  assert re.search(
      r"PATH=/usr/local/bin:/opt/zstd/bin:/opt/python3\.14/bin:/opt/python3\.14t/bin",
      dockerfile,
  )
  assert "command -v cython" in dockerfile
