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
