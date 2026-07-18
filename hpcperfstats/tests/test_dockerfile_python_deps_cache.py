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


def test_hpcperfstats_base_layers_python_deps_on_pyproject():
  """Application edits must not reinstall pinned PyPI deps unless pyproject.toml changes."""
  dockerfile = (_repo_root() / "Dockerfile").read_text()
  stage = _stage_body(dockerfile, "hpcperfstats-base")

  assert re.search(
      r"COPY --chown=hpcperfstats:hpcperfstats pyproject\.toml \./",
      stage,
  )
  assert "tomllib" in stage
  assert "pip install --no-cache-dir -r /tmp/requirements.txt" in stage
  assert "pip install --no-cache-dir --no-deps ." in stage
  assert "shlex.quote" not in stage

  pyproject_copy_pos = stage.index("pyproject.toml")
  deps_install_pos = stage.index("pip install --no-cache-dir -r /tmp/requirements.txt")
  full_copy_pos = stage.index("COPY --chown=hpcperfstats:hpcperfstats . .")
  package_install_pos = stage.index("pip install --no-cache-dir --no-deps .")
  assert pyproject_copy_pos < deps_install_pos < full_copy_pos < package_install_pos


def test_pyproject_dependencies_write_valid_pip_requirements_file():
  """Comma/version pins must not be shell-quoted (regression for Django>=6.0.6,<7.0)."""
  deps = _pyproject_runtime_requirements()
  requirements_text = "\n".join(deps) + "\n"

  assert "Django>=6.0.6,<7.0" in requirements_text
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
  assert stages.index("hpcperfstats-pipeline-refresh") < stages.index("hpcperfstats-full")
  assert "COPY --from=frontend-builder" in _stage_body(dockerfile, "hpcperfstats-full")


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
