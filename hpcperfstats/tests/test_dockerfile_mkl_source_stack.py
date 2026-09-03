"""Dockerfile MKL + source-build numpy/numexpr/pandas contracts."""

from __future__ import annotations

import re
from pathlib import Path


def _repo_root() -> Path:
  return Path(__file__).resolve().parents[2]


def _stage_body(dockerfile: str, stage_name: str) -> str:
  match = re.search(
      rf"^FROM .* AS {re.escape(stage_name)}\s*\n(.*?)(?=^FROM |\Z)",
      dockerfile,
      flags=re.MULTILINE | re.DOTALL,
  )
  assert match, f"{stage_name} stage not found in Dockerfile"
  return match.group(1)


def _run_instructions(stage: str) -> list[str]:
  """Return each RUN instruction body from a Dockerfile stage."""
  return re.findall(
      r"^RUN /bin/bash -o pipefail -c '((?:\\'|[^'])*)'",
      stage,
      flags=re.MULTILINE | re.DOTALL,
  )


def _pep508_name(dep: str) -> str:
  return re.split(r"[=<>~;!@[ ]", dep, maxsplit=1)[0].strip().lower()


def test_base_apt_has_mkl_compile_tools_not_openblas_dev():
  base = _stage_body((_repo_root() / "Dockerfile").read_text(), "hpcperfstats-base")
  assert "gfortran" in base
  assert "ninja-build" in base
  assert "cmake" in base
  assert "libopenblas-dev" not in base
  assert "libatlas-base-dev" not in base


def test_mkl_source_stack_run_order_and_flags():
  """Per ABI: image-build, numpy MKL, numexpr VML via site.cfg, pandas, rest."""
  base = _stage_body((_repo_root() / "Dockerfile").read_text(), "hpcperfstats-base")
  runs = _run_instructions(base)
  assert runs, "expected RUN instructions in hpcperfstats-base"

  def _find(predicate):
    for idx, body in enumerate(runs):
      if predicate(body):
        return idx, body
    raise AssertionError("matching RUN not found")

  gil_build_i, gil_build = _find(
      lambda b: "-r /tmp/requirements-build.txt" in b
      and "python3.14t" not in b
      and "--no-binary" not in b
  )
  # Regression: one pip resolve of numpy+numexpr under --no-build-isolation fails
  # because numexpr setup imports numpy during metadata generation.
  gil_compile_i, gil_compile = _find(
      lambda b: "--no-binary numpy" in b
      and "ne.use_vml" in b
      and "--no-binary pandas" in b
      and "python3.14t" not in b
  )
  gil_rest_i, gil_rest = _find(
      lambda b: "-r /tmp/requirements-rest.txt" in b
      and "python3.14t" not in b
      and "--constraint /tmp/requirements-mkl-src.txt" in b
  )
  ft_build_i, ft_build = _find(
      lambda b: "python3.14t" in b
      and "-r /tmp/requirements-build.txt" in b
      and "--no-binary" not in b
  )
  ft_compile_i, ft_compile = _find(
      lambda b: "python3.14t" in b
      and "--no-binary numpy" in b
      and "ne.use_vml" in b
      and "--no-binary pandas" in b
  )
  ft_rest_i, ft_rest = _find(
      lambda b: "python3.14t" in b
      and "-r /tmp/requirements-rest.txt" in b
      and "--constraint /tmp/requirements-mkl-src.txt" in b
  )

  assert gil_build_i < gil_compile_i < gil_rest_i
  assert ft_build_i < ft_compile_i < ft_rest_i
  assert gil_rest_i < ft_build_i

  for build in (gil_build, ft_build):
    assert "-r /tmp/requirements-rest.txt" not in build
    assert "--no-binary" not in build

  for compile_body in (gil_compile, ft_compile):
    assert "--no-build-isolation" in compile_body
    assert "--force-reinstall" in compile_body
    assert "-Dblas=mkl" in compile_body
    assert "-Dlapack=mkl" in compile_body
    assert "-Dcpu-baseline=native" in compile_body
    assert "-Dcpu-dispatch=max" in compile_body
    assert "-march=native" in compile_body
    assert "-r /tmp/requirements-mkl-numpy.txt" in compile_body
    assert "-r /tmp/requirements-mkl-numexpr.txt" in compile_body
    assert "-r /tmp/requirements-mkl-pandas.txt" in compile_body
    # Combined three-way --no-binary must not return (numexpr needs installed numpy).
    assert "--no-binary numpy,numexpr,pandas" not in compile_body
    assert "--no-binary numexpr,pandas" not in compile_body
    assert "-r /tmp/requirements-mkl-src.txt" not in compile_body
    assert "-r /tmp/requirements-rest.txt" not in compile_body
    assert "scipy" not in compile_body
    # Regression: default show_config() returns None; mode=dicts returns the MKL map.
    assert 'show_config(mode="dicts")' in compile_body or (
        'show_config(mode=\\"dicts\\")' in compile_body
    )
    assert 'c=str(np.show_config())' not in compile_body
    # numexpr Intel VML: inject site.cfg into unpacked sdist (setup.py USE_VML).
    assert "site.cfg" in compile_body
    assert "libraries = mkl_rt" in compile_body
    assert "pip download" in compile_body
    assert "ne.use_vml" in compile_body
    # Regression: GNU ld cannot find -lmkl_rt when only libmkl_rt.so.N exists.
    assert 'libmkl_rt.so.*' in compile_body
    assert "LIBRARY_PATH" in compile_body
    assert "ln -s" in compile_body
    # mkl 2026+ has no importable Python module; discover via sysconfig data prefix.
    assert "sysconfig.get_path" in compile_body
    assert 'glob("mkl-*.pc")' in compile_body or 'glob(\\"mkl-*.pc\\")' in compile_body
    assert "libmkl_rt.so" in compile_body
    assert "mkl.h" in compile_body
    assert "import pathlib,mkl" not in compile_body
    assert "import mkl" not in compile_body
    # MKL meson flags apply to numpy install only; later steps have no -Dblas.
    numpy_marker = "-r /tmp/requirements-mkl-numpy.txt"
    pandas_marker = "-r /tmp/requirements-mkl-pandas.txt"
    numpy_pip = compile_body.index(numpy_marker)
    pandas_pip = compile_body.index(pandas_marker)
    assert numpy_pip < pandas_pip
    assert "-Dblas=mkl" in compile_body[: compile_body.index("ne.use_vml")]
    after_numpy_install = compile_body[numpy_pip + len(numpy_marker) :]
    assert "-Dblas=mkl" not in after_numpy_install
    assert "-Dlapack=mkl" not in after_numpy_install
    # Regression: pandas without --no-deps reinstalls manylinux numpy over MKL build.
    assert "--no-deps" in compile_body
    pandas_pip_idx = compile_body.index(pandas_marker)
    pandas_region = compile_body[
        compile_body.rfind("pip", 0, pandas_pip_idx) : pandas_pip_idx
        + len(pandas_marker)
    ]
    assert "--no-deps" in pandas_region
    # dateutil is a rest-layer dep (pyproject); do not install it in the compile RUN.
    assert "python-dateutil" not in compile_body
    # MKL assert must run again after pandas (not only after the numpy install).
    after_pandas = compile_body[pandas_pip_idx + len(pandas_marker) :]
    assert "show_config" in after_pandas
    assert "mkl" in after_pandas.lower()

  for rest in (gil_rest, ft_rest):
    assert "--no-binary" not in rest
    assert "--constraint /tmp/requirements-mkl-src.txt" in rest
    # pandas import needs dateutil from rest; smoke after rest install.
    assert "import pandas" in rest
    assert "pd.__version__" in rest

  assert "ldconfig" in ft_compile
  assert "mkl-gil.conf" in gil_compile
  assert "mkl-ft.conf" in ft_compile
  assert "--no-binary :all:" not in base
  assert "-m ensurepip" not in base
  assert "ensurepip --upgrade" not in base


def test_numexpr_link_creates_unversioned_mkl_rt_so():
  """Regression: ld -lmkl_rt fails when pip mkl ships only libmkl_rt.so.2."""
  base = _stage_body((_repo_root() / "Dockerfile").read_text(), "hpcperfstats-base")
  assert "cannot find -lmkl_rt" not in base
  assert "libmkl_rt.so.*" in base
  assert "LIBRARY_PATH" in base
  assert "ln -s" in base


def test_show_config_assert_uses_dicts_mode_not_none_return():
  """Regression: str(np.show_config()) is 'None' even when MKL linked (numpy 2.5)."""
  base = _stage_body((_repo_root() / "Dockerfile").read_text(), "hpcperfstats-base")
  assert 'c=str(np.show_config())' not in base
  assert "mode=" in base and "dicts" in base
  assert "ne.use_vml" in base
  assert "site.cfg" in base


def test_mklroot_discovery_does_not_import_mkl_module():
  """Regression: mkl 2026.1.0 wheel has no importable ``mkl`` package (data libs only)."""
  base = _stage_body((_repo_root() / "Dockerfile").read_text(), "hpcperfstats-base")
  assert "import pathlib,mkl" not in base
  assert re.search(r"\bimport mkl\b", base) is None
  assert "sysconfig.get_path" in base
  assert "libmkl_rt.so" in base


def test_pandas_install_uses_no_deps_to_preserve_mkl_numpy():
  """Regression: pandas dep resolve replaced MKL numpy with a manylinux wheel."""
  base = _stage_body((_repo_root() / "Dockerfile").read_text(), "hpcperfstats-base")
  runs = _run_instructions(base)
  compile_runs = [
      body
      for body in runs
      if "--no-binary pandas" in body and "-r /tmp/requirements-mkl-pandas.txt" in body
  ]
  assert len(compile_runs) == 2  # GIL + free-threaded
  for body in compile_runs:
    pandas_marker = "-r /tmp/requirements-mkl-pandas.txt"
    region_start = body.rfind("pip", 0, body.index(pandas_marker))
    region = body[region_start : body.index(pandas_marker) + len(pandas_marker)]
    assert "--no-deps" in region
    assert "--no-binary pandas" in region
    after = body[body.index(pandas_marker) + len(pandas_marker) :]
    assert "python-dateutil" not in after
    assert "show_config" in after
    assert "mkl" in after.lower()


def test_dockerfile_pip_argv_has_no_hardcoded_scientific_or_mkl_pins():
  """Versions and MKL/toolchain packages come from pyproject -r files only."""
  base = _stage_body((_repo_root() / "Dockerfile").read_text(), "hpcperfstats-base")
  pip_runs = [
      body
      for body in _run_instructions(base)
      if "pip install" in body or "python3.14t -m pip" in body
  ]
  forbidden_literals = (
      "numpy==",
      "pandas==",
      "numexpr==",
      "mkl==",
      "mkl-devel==",
      "meson-python==",
      "meson==",
      "ninja==",
      "cython==",
  )
  for body in pip_runs:
    for lit in forbidden_literals:
      assert lit not in body, lit
    # Bare package names on pip argv (MKLROOT uses sysconfig, not import mkl).
    pip_lines = [
        line
        for line in body.splitlines()
        if "pip install" in line or "-m pip install" in line
    ]
    joined = "\n".join(pip_lines)
    for bare in (
        " mkl ",
        " mkl-devel ",
        " meson-python ",
        " meson ",
        " ninja ",
        " cython ",
    ):
      assert bare not in f" {joined} ", bare


def test_writer_run_payload_executes_against_real_pyproject(tmp_path, monkeypatch):
  """Extract and exec the tomllib writer so quoting regressions fail on host."""
  base = _stage_body((_repo_root() / "Dockerfile").read_text(), "hpcperfstats-base")
  runs = _run_instructions(base)
  writer = next(body for body in runs if "tomllib" in body and "image-build" in body)
  match = re.search(r'python3 -c "(.*)"\s*\Z', writer, flags=re.DOTALL)
  assert match, "writer python3 -c payload not found"
  # Bash double-quote unescape of the -c argument (\" -> ", \\ -> \).
  payload = match.group(1).encode("utf-8").decode("unicode_escape")
  out = tmp_path / "out"
  out.mkdir()
  payload = payload.replace("/tmp/requirements", str(out / "requirements"))

  repo_pyproject = (_repo_root() / "pyproject.toml").read_text()
  (tmp_path / "pyproject.toml").write_text(repo_pyproject)
  monkeypatch.chdir(tmp_path)
  exec(compile(payload, "<dockerfile-writer>", "exec"), {})

  build = (out / "requirements-build.txt").read_text().splitlines()
  src = (out / "requirements-mkl-src.txt").read_text().splitlines()
  numpy_only = (out / "requirements-mkl-numpy.txt").read_text().splitlines()
  numexpr_only = (out / "requirements-mkl-numexpr.txt").read_text().splitlines()
  pandas_only = (out / "requirements-mkl-pandas.txt").read_text().splitlines()
  after_numpy = (out / "requirements-mkl-after-numpy.txt").read_text().splitlines()
  rest = (out / "requirements-rest.txt").read_text().splitlines()
  all_deps = (out / "requirements.txt").read_text().splitlines()

  assert {_pep508_name(d) for d in src} == {"numpy", "numexpr", "pandas"}
  assert {_pep508_name(d) for d in numpy_only} == {"numpy"}
  assert {_pep508_name(d) for d in numexpr_only} == {"numexpr"}
  assert {_pep508_name(d) for d in pandas_only} == {"pandas"}
  assert {_pep508_name(d) for d in after_numpy} == {"numexpr", "pandas"}
  assert set(numpy_only) | set(after_numpy) == set(src)
  assert set(numexpr_only) | set(pandas_only) == set(after_numpy)
  assert "numpy" not in {_pep508_name(d) for d in rest}
  assert any(_pep508_name(d) == "python-dateutil" for d in rest)
  assert "bokeh==3.9.2" in rest
  assert any(_pep508_name(d) == "mkl" for d in build)
  assert any(_pep508_name(d) == "meson-python" for d in build)
  assert any(_pep508_name(d) == "setuptools" for d in build)
  assert any(_pep508_name(d) == "versioneer" for d in build)
  assert set(all_deps) == set(src) | set(rest)
  assert "scipy" not in "\n".join(all_deps).lower()
