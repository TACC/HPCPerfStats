"""Regression tests for rootless Podman rule dispatch and command parsing."""

from __future__ import annotations

import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent
REPO_ROOT = HOOKS_DIR.parent
RULES_DIR = REPO_ROOT / "hpcperfstats" / "cursor-rules"
sys.path.insert(0, str(HOOKS_DIR))

import hook_task_router  # noqa: E402
import hpc_hook_lib  # noqa: E402


def test_compose_workflow_dispatches_podman_runtime() -> None:
    """Compose workflow paths dispatch the active runtime rule only."""
    rules = hook_task_router.triggered_rules_for_paths(
        ["tests/run_db_pytest_workflow.sh"]
    )

    assert "podman-runtime.mdc" in rules
    assert "colima-docker-runtime.mdc" not in rules


def test_colima_runtime_rule_file_is_removed() -> None:
    """Retired Colima disable rule must not exist in the authoritative rules tree."""
    assert (RULES_DIR / "podman-runtime.mdc").is_file()
    assert not (RULES_DIR / "colima-docker-runtime.mdc").exists()


def test_always_on_rules_do_not_defer_compose_for_local_docker_disable() -> None:
    """Agents must run compose gates on this Podman host; no Colima opt-in escape."""
    for name in (
        "agent-discipline-core.mdc",
        "workspace-layout-and-python-env.mdc",
        "compose-required-for-data-services-changes.mdc",
        "docker-compose-non-unit-testing.mdc",
    ):
        text = (RULES_DIR / name).read_text(encoding="utf-8")
        assert "local Docker disabled" not in text
        assert "colima-docker-runtime.mdc" not in text
        assert "HPCPERFSTATS_ENABLE_LOCAL_DOCKER" not in text


def test_benchmark_workflow_does_not_require_local_docker_opt_in() -> None:
    """Throughput compose workflow is enabled; no Colima-era disable gate."""
    script = (
        REPO_ROOT / "tests" / "run_sync_timedb_benchmark_workflow.sh"
    ).read_text(encoding="utf-8")
    assert "HPCPERFSTATS_ENABLE_LOCAL_DOCKER" not in script
    assert "compose_test_cmd.sh" in script
    assert 'podman_compose_teardown "${COMPOSE_TEST[@]}"' in script


def test_podman_compose_subcommand_is_recognized() -> None:
    """Project flags before a Podman Compose command remain valid."""
    command = (
        "podman-compose -p hpcperfstats logs pipeline 2>&1 "
        "| grep -E 'status'"
    )

    assert hpc_hook_lib._bash_has_compose_subcommand(command)


def test_podman_compose_command_outside_discovery_is_rejected() -> None:
    """Podman operator commands obey the same plan placement contract."""
    plan = """\
## Operator discovery

**Status:** `not needed`

## Approach

```bash
podman-compose -p hpcperfstats exec pipeline echo hi
```
"""

    issues = hpc_hook_lib.operator_commands_outside_discovery_issues(plan)

    assert issues
