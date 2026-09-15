"""Drift guard: inner workflow scripts must run via bash (noexec bind mounts)."""

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_INNER_WORKFLOWS = (
    "tests/run_db_pytest_workflow.sh",
    "tests/run_redis_cache_pytest_workflow.sh",
    "tests/run_stress_host_data_workflow.sh",
    "tests/run_update_metrics_diagnosis_workflow.sh",
)

_E2E_WORKFLOWS = (
    "tests/run_web_e2e_workflow.sh",
    "tests/run_pipeline_e2e_workflow.sh",
)


@pytest.mark.parametrize("workflow_rel", _INNER_WORKFLOWS)
def test_inner_workflows_use_compose_run_inner_script(workflow_rel):
  text = (_REPO_ROOT / workflow_rel).read_text(encoding="utf-8")
  assert "compose_run_inner_script" in text
  assert "compose_prepare_bind_mount" in text
  assert "compose_prepare_bind_mount" in text
  assert "web /home/hpcperfstats/tests/" not in text

@pytest.mark.parametrize("workflow_rel", _E2E_WORKFLOWS)
def test_e2e_workflows_use_compose_bind_mount_work_copy(workflow_rel):
  text = (_REPO_ROOT / workflow_rel).read_text(encoding="utf-8")
  assert "compose_prepare_bind_mount" in text
  assert "compose_cleanup_bind_mount" in text
  assert "compose_web_repo_bind_mount_args" in text
  assert '$ROOT_DIR:/home/hpcperfstats' not in text


def test_compose_work_copy_base_dir_colima_safe_default():
  text = (_REPO_ROOT / "tests/compose_test_cmd.sh").read_text(encoding="utf-8")
  assert "COMPOSE_BIND_MOUNT_BASE_DIR" in text
  assert "COMPOSE_BIND_MOUNT_USE_TMP" in text
  assert "${HOME}/.cache/hpcperfstats-compose" in text
  assert "/tmp/hpcperfstats-compose" in text


@pytest.mark.parametrize(
    "workflow_rel",
    (
        "tests/run_db_pytest_workflow.sh",
        "tests/run_redis_cache_pytest_workflow.sh",
        "tests/run_stress_host_data_workflow.sh",
        "tests/run_update_metrics_diagnosis_workflow.sh",
    ),
)
def test_workflow_args_file_under_home_cache(workflow_rel):
  """Colima cannot bind-mount macOS mktemp under /var/folders; use $HOME/.cache.

  Regression: when ARGS_FILE was plain ``mktemp``, Docker created a directory at
  the container mount path and ignored forwarded pytest args.
  """
  text = (_REPO_ROOT / workflow_rel).read_text(encoding="utf-8")
  assert "${HOME}/.cache/hpcperfstats-compose" in text
  assert "pytest-extra-args.XXXXXX" in text
  assert 'ARGS_FILE="$(mktemp)"' not in text


def test_compose_web_repo_bind_mount_args_helper():
  text = (_REPO_ROOT / "tests/compose_test_cmd.sh").read_text(encoding="utf-8")
  assert "compose_web_repo_bind_mount_args" in text


def test_compose_run_inner_script_streams_from_host_stdin():
  text = (_REPO_ROOT / "tests/compose_test_cmd.sh").read_text(encoding="utf-8")
  assert "compose_run_inner_script" in text
  assert "compose_inner_pip_install.sh" in text
  assert "compose_prepare_bind_mount" in text
  assert "compose_work_copy_base_dir" in text
  assert "hpcperfstats.ini:/home/hpcperfstats/hpcperfstats.ini:ro" in text
  assert "DOCKER_PYTEST_BIND_MOUNT=1" in text
  assert "compose_test run --rm -i" in text
  assert "web -s" in text


def test_inner_scripts_call_compose_inner_pip_install():
  for inner_rel in (
      "tests/run_db_pytest_inner.sh",
      "tests/run_redis_cache_pytest_inner.sh",
      "tests/run_stress_host_data_inner.sh",
      "tests/run_update_metrics_diagnosis_inner.sh",
  ):
    text = (_REPO_ROOT / inner_rel).read_text(encoding="utf-8")
    assert "compose_inner_pip_install" in text


def test_local_docker_disabled_by_default_in_colima_helpers():
  """Compose/Colima workflows must refuse to run without an explicit enable flag."""
  teardown = (_REPO_ROOT / "tests/colima_compose_teardown.sh").read_text(
      encoding="utf-8"
  )
  cleanup = (_REPO_ROOT / "tests/colima_docker_cleanup.sh").read_text(
      encoding="utf-8"
  )
  compose_cmd = (_REPO_ROOT / "tests/compose_test_cmd.sh").read_text(
      encoding="utf-8"
  )
  assert "hpcperfstats_require_local_docker" in teardown
  assert "HPCPERFSTATS_ENABLE_LOCAL_DOCKER" in teardown
  assert "hpcperfstats_require_local_docker" in compose_cmd
  assert "HPCPERFSTATS_ENABLE_LOCAL_DOCKER" in cleanup


@pytest.mark.parametrize(
    "workflow_rel",
    (
        "tests/run_db_pytest_workflow.sh",
        "tests/run_redis_cache_pytest_workflow.sh",
        "tests/run_web_e2e_workflow.sh",
        "tests/run_pipeline_e2e_workflow.sh",
        "tests/run_stress_host_data_workflow.sh",
        "tests/run_update_metrics_diagnosis_workflow.sh",
        "tests/run_security_audit_workflow.sh",
        "tests/run_all_compose_workflows.sh",
    ),
)
def test_compose_workflows_call_colima_export_docker_env(workflow_rel):
  """Every compose workflow must hit the local-Docker gate via colima_export_docker_env."""
  text = (_REPO_ROOT / workflow_rel).read_text(encoding="utf-8")
  assert "colima_export_docker_env" in text


def test_require_local_docker_exits_when_disabled(tmp_path):
  """Unset enable flag must exit 78 before any docker/colima probe."""
  import os
  import subprocess

  env = os.environ.copy()
  env.pop("HPCPERFSTATS_ENABLE_LOCAL_DOCKER", None)
  script = tmp_path / "probe.sh"
  script.write_text(
      "#!/usr/bin/env bash\n"
      f'. "{_REPO_ROOT / "tests/colima_compose_teardown.sh"}"\n'
      "hpcperfstats_require_local_docker\n"
      "echo SHOULD_NOT_REACH\n",
      encoding="utf-8",
  )
  proc = subprocess.run(
      ["bash", str(script)],
      capture_output=True,
      text=True,
      env=env,
      check=False,
  )
  assert proc.returncode == 78
  assert "Local Docker/Colima compose workflows are disabled" in proc.stderr
  assert "SHOULD_NOT_REACH" not in proc.stdout
