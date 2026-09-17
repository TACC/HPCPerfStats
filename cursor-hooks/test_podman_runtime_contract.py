"""Regression tests for rootless Podman rule dispatch and command parsing."""

from __future__ import annotations

import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent
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
