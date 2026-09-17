"""Static contracts for rootless Podman compose workflow entrypoints."""

import os
import re
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOWS = tuple(
    sorted(
        path.relative_to(_REPO_ROOT).as_posix()
        for path in (_REPO_ROOT / "tests").glob("run_*workflow.sh")
    )
)
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
_FORBIDDEN_HOST_RUNTIME_TERMS = (
    "colima",
    "docker_host",
    "hpcperfstats_enable_local_docker",
    "system prune",
    "container prune",
    "image prune",
    "builder prune",
    "volume prune",
    "network prune",
)


def _read(relative_path: str) -> str:
    return (_REPO_ROOT / relative_path).read_text(encoding="utf-8")


@pytest.mark.parametrize("workflow_rel", _INNER_WORKFLOWS)
def test_inner_workflows_use_compose_run_inner_script(workflow_rel):
    text = _read(workflow_rel)
    assert "compose_run_inner_script" in text
    assert "compose_prepare_bind_mount" in text
    assert "web /home/hpcperfstats/tests/" not in text


def test_inner_script_pipe_does_not_use_unsupported_podman_compose_i():
    compose = _read("tests/compose_test_cmd.sh")
    assert "} | compose_test run --rm -T" in compose
    assert "compose_test run --rm -i" not in compose


def test_inner_script_expands_bare_environment_names_for_podman_compose(tmp_path):
    args_path = tmp_path / "compose-args"
    script = f"""
set -euo pipefail
source tests/compose_test_cmd.sh
EXPORTED_VALUE=present
export EXPORTED_VALUE
compose_test() {{
  printf '%s\\n' "$@" > {args_path}
  cat >/dev/null
}}
compose_run_inner_script tests/run_db_pytest_inner.sh \
  -e EXPORTED_VALUE -e UNSET_VALUE
"""
    subprocess.run(["bash", "-c", script], cwd=_REPO_ROOT, check=True)
    args = args_path.read_text(encoding="utf-8").splitlines()
    assert "EXPORTED_VALUE=present" in args
    assert "UNSET_VALUE=" not in args
    assert "EXPORTED_VALUE" not in args
    assert "UNSET_VALUE" not in args


@pytest.mark.parametrize("workflow_rel", _E2E_WORKFLOWS)
def test_e2e_workflows_use_compose_bind_mount_work_copy(workflow_rel):
    text = _read(workflow_rel)
    assert "compose_prepare_bind_mount" in text
    assert "compose_cleanup_bind_mount" in text
    assert "compose_web_repo_bind_mount_args" in text
    assert "$ROOT_DIR:/home/hpcperfstats" not in text


def test_one_canonical_rootless_podman_adapter():
    adapter = _read("scripts/lib/podman_runtime.sh")
    compose = _read("tests/compose_test_cmd.sh")
    helpers = _read("scripts/lib/compose_frontend_helpers.sh")
    assert "PODMAN=(podman)" in adapter
    assert "PODMAN_COMPOSE=(" in adapter
    assert "podman-compose" in adapter
    assert "HPCPERFSTATS_COMPOSE_PROJECT:=hpcperfstats" in adapter
    assert "HPCPERFSTATS_COMPOSE_PROJECT:=hpcperfstats" in helpers
    assert "HPCPERFSTATS_COMPOSE_PROJECT:=hpcperfstats-dev" not in helpers
    assert "--project-name" in adapter
    assert "/data/user/${USER}" in adapter
    assert "/data/podman/${USER}" in adapter
    assert "HPCPERFSTATS_LOCAL_DATA_CONTRACT" in adapter
    assert "scripts/lib/podman_runtime.sh" in compose
    assert "COMPOSE_TEST=(podman-compose" not in compose


def test_adapter_exports_all_non_dnf_state_under_data():
    adapter = _read("scripts/lib/podman_runtime.sh")
    expected_exports = (
        'XDG_CACHE_HOME="${HPCPERFSTATS_PODMAN_ROOT}/cache"',
        'TMPDIR="${HPCPERFSTATS_PODMAN_ROOT}/tmp"',
        'PIP_CACHE_DIR="${HPCPERFSTATS_HOST_CACHE}/pip"',
        'npm_config_cache="${HPCPERFSTATS_HOST_CACHE}/npm"',
        'npm_config_prefix="${HPCPERFSTATS_HOST_TOOLS}/npm"',
        'PLAYWRIGHT_BROWSERS_PATH="${HPCPERFSTATS_HOST_CACHE}/ms-playwright"',
        "NEXT_TELEMETRY_DISABLED=1",
    )
    for export_contract in expected_exports:
        assert export_contract in adapter
    assert 'HPCPERFSTATS_LOCAL_DATA_CONTRACT=1' in adapter
    assert 'HPCPERFSTATS_LOCAL_DATA_CONTRACT=0' in adapter


def test_adapter_fails_closed_on_effective_podman_storage_fallbacks():
    adapter = _read("scripts/lib/podman_runtime.sh")
    assert "{{.Store.GraphRoot}}" in adapter
    assert "{{.Store.VolumePath}}" in adapter
    assert "{{.Store.ImageCopyTmpDir}}" in adapter
    assert 'expected_graph_root="${HPCPERFSTATS_PODMAN_ROOT}/storage"' in adapter
    assert 'expected_volume_path="${HPCPERFSTATS_PODMAN_ROOT}/volumes"' in adapter
    assert 'expected_image_tmp="${HPCPERFSTATS_PODMAN_ROOT}/tmp"' in adapter
    assert ".config/containers/storage.conf" in adapter
    assert 'expected_imagestore="${HPCPERFSTATS_PODMAN_ROOT}/images"' in adapter
    assert "Effective Podman storage mismatch" in adapter
    assert 'HPCPERFSTATS_LOCAL_DATA_CONTRACT' in adapter


def test_adapter_exports_project_specific_network_name():
    adapter = _read("scripts/lib/podman_runtime.sh")
    assert "hpcperfstats_net" in adapter
    assert "hpcperfstats-test_net" in adapter
    assert "hpcperfstats-dev_net" in adapter
    assert "export HPCPERFSTATS_NETWORK_NAME" in adapter


@pytest.mark.parametrize(
    ("project", "network"),
    (
        ("hpcperfstats-test", "hpcperfstats-test_net"),
        ("hpcperfstats-dev", "hpcperfstats-dev_net"),
    ),
)
def test_adapter_source_exports_effective_data_contract(project, network):
    env = os.environ.copy()
    for inherited_name in (
        "HPCPERFSTATS_HOST_ROOT",
        "HPCPERFSTATS_PODMAN_ROOT",
        "HPCPERFSTATS_HOST_CACHE",
        "HPCPERFSTATS_HOST_TOOLS",
        "HPCPERFSTATS_LOCAL_DATA_CONTRACT",
        "XDG_CACHE_HOME",
        "TMPDIR",
        "PIP_CACHE_DIR",
        "npm_config_cache",
        "npm_config_prefix",
        "PLAYWRIGHT_BROWSERS_PATH",
    ):
        env.pop(inherited_name, None)
    env.update(USER="contract-user", HPCPERFSTATS_COMPOSE_PROJECT=project)
    command = (
        ". scripts/lib/podman_runtime.sh; "
        "printf '%s\\n' \"$XDG_CACHE_HOME\" \"$TMPDIR\" \"$PIP_CACHE_DIR\" "
        "\"$npm_config_cache\" \"$npm_config_prefix\" \"$PLAYWRIGHT_BROWSERS_PATH\" "
        "\"$NEXT_TELEMETRY_DISABLED\" \"$HPCPERFSTATS_NETWORK_NAME\" "
        "\"$HPCPERFSTATS_LOCAL_DATA_CONTRACT\""
    )
    result = subprocess.run(
        ["/bin/bash", "-c", command],
        cwd=_REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.splitlines() == [
        "/data/podman/contract-user/cache",
        "/data/podman/contract-user/tmp",
        "/data/user/contract-user/cache/pip",
        "/data/user/contract-user/cache/npm",
        "/data/user/contract-user/tools/npm",
        "/data/user/contract-user/cache/ms-playwright",
        "1",
        network,
        "1",
    ]


def test_production_compose_project_skips_developer_data_layout():
    """Operator rebuilds use project hpcperfstats without /data/user/$USER."""
    env = os.environ.copy()
    for inherited_name in (
        "HPCPERFSTATS_HOST_ROOT",
        "HPCPERFSTATS_PODMAN_ROOT",
        "HPCPERFSTATS_HOST_CACHE",
        "HPCPERFSTATS_HOST_TOOLS",
        "HPCPERFSTATS_LOCAL_DATA_CONTRACT",
        "XDG_CACHE_HOME",
        "TMPDIR",
        "PIP_CACHE_DIR",
        "npm_config_cache",
        "npm_config_prefix",
        "PLAYWRIGHT_BROWSERS_PATH",
        "_PODMAN_RUNTIME_VERIFIED",
    ):
        env.pop(inherited_name, None)
    env.update(USER="root", HPCPERFSTATS_COMPOSE_PROJECT="hpcperfstats")
    command = (
        ". scripts/lib/podman_runtime.sh; "
        "printf '%s\\n' \"$HPCPERFSTATS_LOCAL_DATA_CONTRACT\" "
        "\"$HPCPERFSTATS_NETWORK_NAME\" \"${HPCPERFSTATS_HOST_CACHE-UNSET}\" "
        "\"$TMPDIR\"; "
        "podman_runtime_require; "
        "printf 'require=%s\\n' \"$?\""
    )
    result = subprocess.run(
        ["/bin/bash", "-c", command],
        cwd=_REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.splitlines() == [
        "0",
        "hpcperfstats_net",
        "UNSET",
        "/tmp",
        "require=0",
    ]
    assert "Required writable /data runtime directory" not in result.stderr


def test_workflow_sources_have_no_retired_or_global_runtime_operations():
    paths = list((_REPO_ROOT / "tests").glob("*.sh")) + [
        _REPO_ROOT / "scripts/lib/podman_runtime.sh"
    ]
    source = "\n".join(path.read_text(encoding="utf-8").lower() for path in paths)
    assert not [term for term in _FORBIDDEN_HOST_RUNTIME_TERMS if term in source]


def test_compose_uses_fixed_test_project_and_project_scoped_teardown():
    compose = _read("tests/compose_test_cmd.sh")
    teardown = _read("tests/podman_compose_teardown.sh")
    assert "HPCPERFSTATS_COMPOSE_DEVELOPMENT:-0" in compose
    assert "HPCPERFSTATS_COMPOSE_PROJECT=hpcperfstats-test" in compose
    assert 'podman_compose_teardown "${COMPOSE_TEST[@]}"' in teardown
    assert "down -v --remove-orphans" in teardown
    assert '"${PODMAN[@]}" volume ls' in teardown
    assert '"${HPCPERFSTATS_COMPOSE_PROJECT}_"*' in teardown
    assert '"${PODMAN[@]}" volume rm "$volume_name"' in teardown
    assert "prune" not in teardown.lower()


def test_compose_test_source_resets_an_inherited_development_project():
    env = os.environ.copy()
    env.update(HPCPERFSTATS_COMPOSE_PROJECT="hpcperfstats-dev")
    env.pop("HPCPERFSTATS_COMPOSE_DEVELOPMENT", None)
    result = subprocess.run(
        [
            "/bin/bash",
            "-c",
            ". tests/compose_test_cmd.sh; printf '%s' \"$HPCPERFSTATS_COMPOSE_PROJECT\"",
        ],
        cwd=_REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout == "hpcperfstats-test"


def test_compose_runs_from_work_copy_without_unsupported_project_directory():
    compose = _read("tests/compose_test_cmd.sh")
    teardown = _read("tests/podman_compose_teardown.sh")

    assert "--project-directory" not in compose
    assert "--project-directory" not in teardown
    assert 'local project_dir="${COMPOSE_BIND_MOUNT_DIR:-$repo_root}"' in compose
    assert '(cd "$project_dir" && "${COMPOSE_TEST[@]}" "$@")' in compose
    assert '(cd "$project_dir" && "$@" down -v --remove-orphans)' in teardown


def test_workflows_use_project_scoped_podman_ids_not_compose_ps_services():
    compose = _read("tests/compose_test_cmd.sh")
    workflows = (
        "tests/run_db_pytest_workflow.sh",
        "tests/run_redis_cache_pytest_workflow.sh",
        "tests/run_web_e2e_workflow.sh",
        "tests/run_pipeline_e2e_workflow.sh",
        "tests/run_update_metrics_diagnosis_workflow.sh",
        "tests/run_stress_host_data_workflow.sh",
        "tests/run_podman_development_stack_verify.sh",
    )

    assert "compose_service_container_id()" in compose
    assert '"${HPCPERFSTATS_COMPOSE_PROJECT}_${service}_1"' in compose
    for workflow in workflows:
        source = _read(workflow)
        assert "compose_test ps -q " not in source
        assert "compose_service_container_id" in source


def test_container_workflow_python_commands_use_canonical_python3():
    scripts = (
        "tests/compose_inner_pip_install.sh",
        "tests/pip_compose_test_extras_fallback.sh",
        "tests/run_db_pytest_inner.sh",
        "tests/run_redis_cache_pytest_inner.sh",
        "tests/run_stress_host_data_inner.sh",
        "tests/run_update_metrics_diagnosis_inner.sh",
        "tests/run_web_e2e_workflow.sh",
        "tests/run_pipeline_e2e_workflow.sh",
    )
    bare_python = re.compile(r"(?<![A-Za-z0-9_.-])python(?=\s)")
    bare_pip_install = re.compile(r"(?<!-m )\bpip\s+install")

    for script in scripts:
        source = _read(script)
        assert not bare_python.search(source), script
        assert not bare_pip_install.search(source), script


def test_db_browser_workflow_installs_playwright_with_bind_mount_fallback():
    source = _read("tests/run_db_pytest_inner.sh")

    browser_block = source.split(
        'if [[ "${DOCKER_PYTEST_SKIP_BROWSER:-0}" != "1" ]]; then',
        1,
    )[1].split("fi", 1)[0]
    assert 'python3 -m pip install -q "playwright>=1.60.0"' in browser_block
    assert "python3 -m playwright install --with-deps chromium" in browser_block


def test_db_inner_isolates_migrated_bytecode_and_bootstraps_test_tools():
    source = _read("tests/run_db_pytest_inner.sh")

    assert 'export USER="${USER:-$(id -un)}"' in source
    assert 'export PYTHONPYCACHEPREFIX="${XDG_CACHE_HOME:-/tmp}/python-pycache"' in source
    assert "command -v git" in source
    assert "apt-get install -y --no-install-recommends git" in source


def test_container_gate_does_not_run_shared_compose_project_concurrently():
    gate = _read(
        ".unlazy/linux-rootless-podman-migration/gates/leaf-6-container-e2e.md"
    )

    assert gate.count("CHECK:") == 1
    assert gate.count("tests/run_all_compose_workflows.sh") == 1
    assert (
        "tests/run_all_compose_workflows.sh test_runs/linux_podman_all_compose.md && "
        "tests/run_podman_development_stack_verify.sh && "
        "tests/verify_podman_project_isolation.sh"
    ) in gate
    assert (
        "tests/run_podman_development_stack_verify.sh && "
        "tests/verify_podman_project_isolation.sh"
    ) in gate


@pytest.mark.parametrize("workflow_rel", _WORKFLOWS)
def test_every_compose_workflow_uses_podman_adapter(workflow_rel):
    text = _read(workflow_rel)
    assert "podman_runtime_require" in text or "compose_test_cmd.sh" in text
    assert "podman_compose_teardown" in text or workflow_rel in {
        "tests/run_all_compose_workflows.sh",
        "tests/run_bokeh_browser_workflow.sh",
        "tests/run_podman_development_stack_verify.sh",
        "tests/verify_podman_project_isolation.sh",
    }


@pytest.mark.parametrize("workflow_rel", _INNER_WORKFLOWS)
def test_workflow_host_scratch_uses_data_contract(workflow_rel):
    text = _read(workflow_rel)
    assert "${HPCPERFSTATS_HOST_TMP}" in text
    assert "${HOME}" not in text
    assert 'mktemp "/tmp' not in text


def test_compose_helpers_preserve_oci_filenames_and_explicit_argv():
    compose = _read("tests/compose_test_cmd.sh")
    assert "docker-compose.yaml" in compose
    assert "docker-compose.test-overlay.yaml" in compose
    assert '"${PODMAN_COMPOSE[@]}"' in compose
    assert '"${PODMAN[@]}" inspect' in compose


def test_pipeline_browser_navigation_phase_runs_by_default():
    pipeline = _read("tests/run_pipeline_e2e_workflow.sh")
    browser_context = _read("tests/pipeline_e2e/browser_context.py")
    overlay = _read("tests/docker-compose.test-overlay.yaml")
    assert "WITH_BROWSER=1" in pipeline
    assert "--skip-browser" in pipeline
    assert "test_spa_navigation_overlays_browser.py" in pipeline
    assert "compose_test up -d web proxy" in pipeline
    assert (
        "HPCPERFSTATS_PIPELINE_E2E_BASE_URL=https://servername.domain.edu"
        in pipeline
    )
    assert "- servername.domain.edu" in overlay
    assert "export HPCPERFSTATS_HTTP_PORT=18080" in pipeline
    assert "export HPCPERFSTATS_HTTPS_PORT=18443" in pipeline
    assert "export HPCPERFSTATS_AMQP_PORT=15673" in pipeline
    assert "HPCPERFSTATS_HTTP_PORT:-" not in pipeline
    assert "HPCPERFSTATS_HTTPS_PORT:-" not in pipeline
    assert "HPCPERFSTATS_AMQP_PORT:-15673" not in pipeline
    assert '-e "PLAYWRIGHT_BROWSERS_PATH=${PLAYWRIGHT_BROWSERS_PATH}"' in pipeline
    assert "-e PLAYWRIGHT_BROWSERS_PATH \\" not in pipeline
    assert '"${PLAYWRIGHT_BROWSERS_PATH}:${PLAYWRIGHT_BROWSERS_PATH}:rw"' in pipeline
    assert 'urlopen("http://127.0.0.1:8000/"' in pipeline
    assert "except urllib.error.HTTPError as exc" in pipeline
    assert "if exc.code >= 500:" in pipeline
    assert 'urlopen("http://127.0.0.1:8000/machine/"' not in pipeline
    assert browser_context.count("ignore_https_errors=True") == 2


def test_security_audit_is_fail_closed_for_both_tools():
    security = _read("tests/run_security_audit_workflow.sh")
    assert "--skip-build" in security
    assert "pip-audit" in security
    assert "python3 -m pip install --disable-pip-version-check pip-audit" in security
    assert "python3 -m pip_audit" in security
    assert '"pip install ' not in security
    assert "npm audit" in security
    assert "skipping npm audit" not in security.lower()
    assert "command -v npm" not in security


def test_full_orchestrator_runs_complete_matrix_without_recursion():
    orchestrator = _read("tests/run_all_compose_workflows.sh")
    required_workflows = (
        "run_db_pytest_workflow.sh",
        "run_redis_cache_pytest_workflow.sh",
        "run_web_e2e_workflow.sh",
        "run_pipeline_e2e_workflow.sh",
        "run_update_metrics_diagnosis_workflow.sh",
        "run_stress_host_data_workflow.sh",
        "run_security_audit_workflow.sh",
        "run_bokeh_browser_workflow.sh",
    )
    for workflow in required_workflows:
        assert orchestrator.count(workflow) == 2
    assert "HPCPERFSTATS_STRESS_HOST_DATA_ROWS=400000" in orchestrator
    assert orchestrator.count("run_all_compose_workflows.sh") == 1
    assert 'run_step "run_all_compose_workflows.sh"' not in orchestrator
    assert "tee -a" in orchestrator


@pytest.mark.parametrize(
    "relative_path",
    (
        "tests/run_bokeh_browser_workflow.sh",
        "tests/run_podman_development_stack_verify.sh",
        "tests/verify_podman_project_isolation.sh",
    ),
)
def test_planned_future_workflows_exist_and_are_bash_syntax_clean(relative_path):
    path = _REPO_ROOT / relative_path
    assert path.is_file()
    subprocess.run(["/bin/bash", "-n", str(path)], check=True)


def test_project_isolation_verifier_has_unique_positive_control_and_bounded_cleanup():
    verifier = _read("tests/verify_podman_project_isolation.sh")
    assert "/proc/sys/kernel/random/uuid" in verifier
    assert '"${PODMAN[@]}" volume exists' in verifier
    assert '"${PODMAN[@]}" volume create' in verifier
    assert '"${PODMAN[@]}" volume rm' in verifier
    assert "podman_compose_teardown" in verifier
    assert "development_ids_before" in verifier
    assert "development_ids_after" in verifier
    assert "HPCPERFSTATS_NETWORK_NAME=hpcperfstats-dev_net" in verifier


def test_development_verifier_checks_health_and_unprivileged_ports():
    verifier = _read("tests/run_podman_development_stack_verify.sh")
    assert "HPCPERFSTATS_HTTP_PORT=8080" in verifier
    assert "HPCPERFSTATS_HTTPS_PORT=8443" in verifier
    assert "HPCPERFSTATS_SYSLOG_PORT=1514" in verifier
    assert "HPCPERFSTATS_AMQP_PORT=5673" in verifier
    assert "healthy|running" in verifier
    assert "published_port" in verifier


def test_standalone_teardown_uses_committed_overlay_fallback():
    teardown = _read("tests/podman_compose_teardown.sh")
    assert "docker-compose.test-overlay.yaml.example" in teardown
    assert '${BASH_SOURCE[0]}" == "$0' in teardown


def test_future_workflows_do_not_grow_home_or_root_tmp_or_globally_prune():
    source = "\n".join(
        _read(relative_path).lower()
        for relative_path in (
            "tests/run_bokeh_browser_workflow.sh",
            "tests/run_podman_development_stack_verify.sh",
            "tests/verify_podman_project_isolation.sh",
        )
    )
    assert "${home}" not in source
    assert "$home" not in source
    assert "mktemp /tmp" not in source
    assert " prune" not in source
    assert "/proc/sys/kernel/random/uuid" in source


def test_all_owned_shell_scripts_are_bash_syntax_clean():
    paths = list((_REPO_ROOT / "tests").glob("*.sh")) + [
        _REPO_ROOT / "scripts/lib/podman_runtime.sh"
    ]
    subprocess.run(["/bin/bash", "-n", *map(str, paths)], check=True)
