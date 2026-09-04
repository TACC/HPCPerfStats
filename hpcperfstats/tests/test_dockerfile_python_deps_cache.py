"""Regression: hpcperfstats-* Docker stages must layer pip deps on pyproject.toml only."""

from __future__ import annotations

import re
import tomllib
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


def _pyproject_runtime_requirements() -> list[str]:
  pyproject = tomllib.loads((_repo_root() / "pyproject.toml").read_text())
  return pyproject["project"]["dependencies"]


def test_python_build_layers_python_deps_on_pyproject():
  """PyPI deps install in python-build; application COPY must not reinstall them."""
  dockerfile = (_repo_root() / "Dockerfile").read_text()
  build = _stage_body(dockerfile, "python-build")

  assert re.search(r"COPY pyproject\.toml \./", build)
  assert "tomllib" in build
  assert "-r /tmp/requirements-build.txt" in build
  assert "--constraint /tmp/requirements-mkl-src.txt" in build
  assert "-r /tmp/requirements-mkl-numpy.txt" in build
  assert "-r /tmp/requirements-mkl-numexpr.txt" in build
  assert "-r /tmp/requirements-mkl-pandas.txt" in build
  assert "-r /tmp/requirements-rest.txt" in build
  assert "shlex.quote" not in build

  pyproject_copy_pos = build.index("pyproject.toml")
  build_install_pos = build.index("-r /tmp/requirements-build.txt")
  assert pyproject_copy_pos < build_install_pos


def test_python_build_gil_pip_invokes_via_python3_m():
  """GIL deps RUNs must use python3 -m pip (bare pip is not on PATH).

  Regression: podman build failed with ``pip: command not found`` after the
  tomllib requirements slice because only pip3 was symlinked to /usr/local/bin
  and /opt/python3.14/bin was not on PATH in python-build.
  """
  build = _stage_body((_repo_root() / "Dockerfile").read_text(), "python-build")
  assert "python3 -m pip install --no-cache-dir -r /tmp/requirements-build.txt" in build
  assert "python3 -m pip download" in build
  assert "python3 -m pip install --no-cache-dir pyinstrument" in build
  assert "python3 -m pip uninstall" in build
  assert "python3 -m pip cache purge" in build
  # Strip allowed forms; remaining bare pip argv breaks the builder under default PATH.
  cleaned = build.replace("python3 -m pip ", "").replace(
      "/opt/python3.14t/bin/python3.14t -m pip ",
      "",
  )
  assert "pip install" not in cleaned
  assert "pip download" not in cleaned
  assert "pip uninstall" not in cleaned
  assert "pip cache" not in cleaned


def test_python_build_prunes_full_image_build_toolchain_keeps_cython():
  """After all pip layers, prune build/devel pkgs from image-build install log.

  Covers the full image-build ``Successfully installed`` set (not only
  meson/ninja): drop devel/backends; keep cython and MKL/OpenMP/TBB runtime.
  Rest project.dependencies (Django/Bokeh/…) do not need the pruned names.
  """
  build = _stage_body((_repo_root() / "Dockerfile").read_text(), "python-build")
  uninstall_bodies = [
      body
      for body in re.findall(
          r"^RUN /bin/bash -o pipefail -c '((?:\\'|[^'])*)'",
          build,
          flags=re.MULTILINE | re.DOTALL,
      )
      if "pip uninstall" in body
  ]
  assert len(uninstall_bodies) == 1, uninstall_bodies
  body = uninstall_bodies[0]
  for pkg in (
      "meson",
      "meson-python",
      "ninja",
      "versioneer",
      "pyproject-metadata",
      "mkl-devel",
      "mkl-include",
      "tbb-devel",
  ):
    assert re.search(rf"(^|[\s\\]){re.escape(pkg)}([\s\\]|$)", body), pkg
  # Must not uninstall cython (kept on both prefixes).
  assert not re.search(r"pip uninstall -y[^\n]*\bcython\b", body)
  assert 'command -v cython)" = "/opt/python3.14/bin/cython"' in body
  assert "test -x /opt/python3.14t/bin/cython" in body
  # Must not prune runtime MKL/OpenMP/TBB or packaging (bokeh Needs packaging).
  for keep in (
      " mkl ",
      " intel-openmp ",
      " tbb ",
      " packaging ",
      " setuptools ",
      " wheel ",
  ):
    assert keep not in f" {body.replace(chr(10), ' ')} ".replace("\\", " "), keep


def test_python_build_path_includes_prefix_bins_for_cython_meson():
  """image-build console scripts (cython/meson/ninja) must be on PATH.

  Regression: numpy --no-build-isolation Meson failed with
  ``Unknown compiler(s): [['cython'], ['cython3']]`` because pip installed
  cython under /opt/python3.14/bin while python-build PATH only had
  /usr/local/bin (python3/pip3 symlinks).
  """
  build = _stage_body((_repo_root() / "Dockerfile").read_text(), "python-build")
  path_lines = [
      ln for ln in build.splitlines() if re.search(r"(^|\s)PATH=", ln)
  ]
  assert path_lines, "python-build must set PATH for prefix bin dirs"
  joined = "\n".join(path_lines)
  assert "/opt/python3.14/bin" in joined
  assert "/opt/python3.14t/bin" in joined
  # PATH must precede the GIL numpy source install that needs cython.
  path_pos = min(build.index(ln) for ln in path_lines if "/opt/python3.14/bin" in ln)
  numpy_pos = build.index("-r /tmp/requirements-mkl-numpy.txt")
  assert path_pos < numpy_pos


def test_python_build_cython_path_matches_abi_prefix():
  """GIL/FT Meson must see that ABI's cython, not the other prefix.

  Shared ENV PATH lists GIL bin before FT; without per-RUN PATH pinning, the
  FT numpy source build would pick /opt/python3.14/bin/cython.
  """
  build = _stage_body((_repo_root() / "Dockerfile").read_text(), "python-build")
  gil = build[
      build.index("COPY pyproject.toml") : build.index(
          "/opt/python3.14t/bin/python3.14t -m pip install --no-cache-dir --upgrade pip"
      )
  ]
  ft = build[build.index("/opt/python3.14t/bin/python3.14t -m pip install --no-cache-dir --upgrade pip") :]
  assert 'PATH="/opt/python3.14/bin:' in gil or "PATH=/opt/python3.14/bin:" in gil
  assert 'PATH="/opt/python3.14t/bin:' in ft or "PATH=/opt/python3.14t/bin:" in ft
  assert 'command -v cython)" = "/opt/python3.14/bin/cython"' in gil
  assert 'command -v cython)" = "/opt/python3.14t/bin/cython"' in ft
  # Assert before the numpy source install that Meson will invoke cython.
  assert gil.index('command -v cython)" = "/opt/python3.14/bin/cython"') < gil.index(
      "-r /tmp/requirements-mkl-numpy.txt"
  )
  assert ft.index('command -v cython)" = "/opt/python3.14t/bin/cython"') < ft.index(
      "-r /tmp/requirements-mkl-numpy.txt"
  )


def test_hpcperfstats_base_installs_package_no_deps_after_full_copy():
  """Runtime base copies tree then pip --no-deps only (deps already in python-build)."""
  dockerfile = (_repo_root() / "Dockerfile").read_text()
  stage = _stage_body(dockerfile, "hpcperfstats-base")

  assert "COPY --chown=hpcperfstats:hpcperfstats . ." in stage
  assert "python3 -m pip install --no-cache-dir --no-deps ." in stage
  assert "-r /tmp/requirements-build.txt" not in stage
  assert "-r /tmp/requirements-rest.txt" not in stage

  full_copy_pos = stage.index("COPY --chown=hpcperfstats:hpcperfstats . .")
  package_install_pos = stage.index(
      "python3 -m pip install --no-cache-dir --no-deps ."
  )
  assert full_copy_pos < package_install_pos


def test_pyproject_dependencies_write_valid_pip_requirements_file():
  """Comma/version pins must not be shell-quoted (regression for Django floor pins)."""
  deps = _pyproject_runtime_requirements()
  requirements_text = "\n".join(deps) + "\n"

  assert "Django>=6.1.1,<6.2" in requirements_text
  assert "'Django" not in requirements_text
  assert '"Django' not in requirements_text

  for line in requirements_text.splitlines():
    assert line
    assert line[0] not in "'\""


def test_hpcperfstats_child_stages_do_not_reinstall_python_deps():
  """hpcperfstats-full and pipeline-refresh inherit base; only collectstatic locally."""
  dockerfile = (_repo_root() / "Dockerfile").read_text()

  for stage_name in ("hpcperfstats-full", "hpcperfstats-pipeline-refresh"):
    stage = _stage_body(dockerfile, stage_name)
    assert "pip install" not in stage, stage_name
    assert "collectstatic --noinput" in stage, stage_name


def test_hpcperfstats_full_is_last_dockerfile_stage():
  """Default build target must run frontend-builder (podman-compose ignores build.target)."""
  dockerfile = (_repo_root() / "Dockerfile").read_text()
  stages = re.findall(r"^FROM .* AS (\S+)", dockerfile, flags=re.MULTILINE)

  assert stages[-1] == "hpcperfstats-full"
  assert stages.index("hpcperfstats-pipeline-refresh") < stages.index(
      "hpcperfstats-full"
  )
  assert "COPY --from=frontend-builder" in _stage_body(
      dockerfile, "hpcperfstats-full"
  )


def test_dockerfile_documents_spa_volume_fingerprint_heal_contract():
  """From-scratch rebuild lands SPA via django_startup heal, not image collectstatic alone."""
  dockerfile = (_repo_root() / "Dockerfile").read_text()
  assert "django_startup.sh" in dockerfile
  assert "spa_static_root_heal" in dockerfile
  assert "staticfiles_data" in dockerfile
  assert "fingerprint" in dockerfile.lower()
  assert "services-conf/django_startup.sh" in dockerfile
  full = _stage_body(dockerfile, "hpcperfstats-full")
  assert "masks" in full.lower() or "mask" in full.lower()
  assert "collectstatic --noinput" in full


def test_dockerfile_pins_dual_cpython_prefixes_from_python_build():
  """GIL /opt/python3.14 (+ /usr/local links) and FT /opt/python3.14t from python-build."""
  dockerfile = (_repo_root() / "Dockerfile").read_text()
  stages = re.findall(r"^FROM .* AS (\S+)", dockerfile, flags=re.MULTILINE)
  assert "python-build" in stages
  assert stages.index("python-build") < stages.index("hpcperfstats-base")
  assert stages[-1] == "hpcperfstats-full"

  assert re.search(
      r"^FROM debian:trixie AS python-build\s*$",
      dockerfile,
      flags=re.MULTILINE,
  )
  assert re.search(
      r"^FROM debian:trixie-slim AS hpcperfstats-base\s*$",
      dockerfile,
      flags=re.MULTILINE,
  )

  build = _stage_body(dockerfile, "python-build")
  assert "--prefix=/opt/python3.14" in build
  assert "--prefix=/opt/python3.14t" in build
  assert "--disable-gil" in build
  assert "--with-ensurepip=install" in build
  assert "-march=native" in build
  assert "CFLAGS" in build
  assert "/opt/hpcperfstats-ft" not in dockerfile

  base = _stage_body(dockerfile, "hpcperfstats-base")
  assert "COPY --from=python-build /opt/python3.14t /opt/python3.14t" in base
  assert "COPY --from=python-build /opt/python3.14 /opt/python3.14" in base
  assert "/opt/python3.14t/bin/python3.14t -m pip" in base
  assert "/opt/hpcperfstats-ft" not in base
  assert "-m ensurepip" not in base
  assert "ensurepip --upgrade" not in base
