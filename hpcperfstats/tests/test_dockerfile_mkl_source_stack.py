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
  """Per ABI: image-build wheels, then --no-binary compile, then constrained rest."""
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
  gil_compile_i, gil_compile = _find(
      lambda b: "--no-binary numpy,numexpr,pandas" in b and "python3.14t" not in b
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
      lambda b: "python3.14t" in b and "--no-binary numpy,numexpr,pandas" in b
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
    assert "-r /tmp/requirements-mkl-src.txt" in compile_body
    assert "-r /tmp/requirements-rest.txt" not in compile_body
    assert "show_config" in compile_body
    assert "scipy" not in compile_body

  for rest in (gil_rest, ft_rest):
    assert "--no-binary" not in rest
    assert "--constraint /tmp/requirements-mkl-src.txt" in rest

  assert "ldconfig" in ft_compile
  assert "mkl-gil.conf" in gil_compile
  assert "mkl-ft.conf" in ft_compile
  assert "--no-binary :all:" not in base
  assert "-m ensurepip" not in base
  assert "ensurepip --upgrade" not in base


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
    # Bare package names on pip argv (not import mkl in python -c).
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
  rest = (out / "requirements-rest.txt").read_text().splitlines()
  all_deps = (out / "requirements.txt").read_text().splitlines()

  assert {_pep508_name(d) for d in src} == {"numpy", "numexpr", "pandas"}
  assert "numpy" not in {_pep508_name(d) for d in rest}
  assert "bokeh==3.9.2" in rest
  assert any(_pep508_name(d) == "mkl" for d in build)
  assert any(_pep508_name(d) == "meson-python" for d in build)
  assert set(all_deps) == set(src) | set(rest)
  assert "scipy" not in "\n".join(all_deps).lower()
