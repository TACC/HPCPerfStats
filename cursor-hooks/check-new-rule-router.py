#!/usr/bin/env python3
"""Cursor postToolUse hook — new cursor-rules must appear in agent-discipline-core router."""
from __future__ import annotations

import sys
from pathlib import Path

HOOK_DIR = Path(__file__).resolve().parent
if str(HOOK_DIR) not in sys.path:
    sys.path.insert(0, str(HOOK_DIR))

from hpc_hook_lib import (  # noqa: E402
    emit_json,
    find_hook_task_router_file,
    find_router_file,
    load_json_stdin,
    rule_file_needs_router_entry,
    rule_listed_in_router,
)


def edited_path_from_tool(payload: dict) -> str:
    tool_input = payload.get("tool_input") or {}
    if isinstance(tool_input, dict):
        for key in ("path", "file_path", "target_file", "target_notebook"):
            value = tool_input.get(key)
            if isinstance(value, str) and value.strip():
                return value
    file_path = payload.get("file_path")
    if isinstance(file_path, str):
        return file_path
    return ""


def main() -> int:
    payload = load_json_stdin()
    path = edited_path_from_tool(payload)
    needs_entry, basename = rule_file_needs_router_entry(path)
    if not needs_entry:
        emit_json({})
        return 0

    router = find_router_file(payload.get("workspace_roots") or [], rule_path=path)
    if router is None:
        emit_json(
            {
                "additional_context": (
                    f"RULE ROUTER CHECK: created/edited `{basename}` but could not "
                    "find agent-discipline-core.mdc. Add a task-router row before "
                    "closing the task."
                ),
            },
        )
        return 0

    router_text = router.read_text(encoding="utf-8", errors="replace")
    in_core_router = rule_listed_in_router(router_text, basename)
    hook_router = find_hook_task_router_file()
    in_hook_router = False
    if hook_router is not None:
        hook_text = hook_router.read_text(encoding="utf-8", errors="replace")
        in_hook_router = rule_listed_in_router(hook_text, basename)
    if in_core_router and in_hook_router:
        emit_json({})
        return 0

    missing: list[str] = []
    if not in_core_router:
        missing.append("agent-discipline-core.mdc task router")
    if hook_router is None:
        missing.append("hook_task_router.py (file not found)")
    elif not in_hook_router:
        missing.append("hook_task_router.py ROUTER_ENTRIES")
    emit_json(
        {
            "additional_context": (
                f"MANDATORY (agent-discipline-core.mdc): new/updated rule `{basename}` "
                f"is not fully dual-registered. Missing: {', '.join(missing)}. "
                "In the same task, add a row to `agent-discipline-core.mdc` "
                "(trigger paths → Read this rule), a matching row in "
                "`cursor-hooks/hook_task_router.py` (`MONITOR_ROUTER_ENTRIES` or "
                "`HPCPERFSTATS_ROUTER_ENTRIES`), and a one-line note in "
                "`RULES_README.md` if policy changes. When dual registration is "
                "done, `cp HPCPerfStats/cursor-hooks/hooks.json .cursor/hooks.json` "
                "(real file — Cursor refuses a symlinked project hooks.json). "
                "Verify both routers + the copy in Final code review before close."
            ),
        },
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[check-new-rule-router] error: {exc}", file=sys.stderr)
        emit_json({})
        raise SystemExit(0) from exc
