#!/usr/bin/env python3
"""Cursor preToolUse — deny CreatePlan / live-plan Write until plan-authoring rules are Read."""
from __future__ import annotations

import sys
from pathlib import Path

HOOK_DIR = Path(__file__).resolve().parent
if str(HOOK_DIR) not in sys.path:
    sys.path.insert(0, str(HOOK_DIR))

from hpc_hook_lib import (  # noqa: E402
    PLAN_AUTHORING_REQUIRED_MDC,
    emit_allow,
    emit_deny,
    is_live_plan_disk_path,
    last_turn_rows,
    load_json_stdin,
    parse_transcript_lines,
    plan_authoring_precreate_read_issues,
    profile_rules_dir_label,
)


def tool_input_dict(payload: dict) -> dict:
    tool_input = payload.get("tool_input") or {}
    return tool_input if isinstance(tool_input, dict) else {}


def edited_path(tool_input: dict) -> str:
    for key in ("path", "file_path", "target_file"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def main() -> int:
    payload = load_json_stdin()
    tool_name = str(payload.get("tool_name") or payload.get("tool") or "")
    tool_input = tool_input_dict(payload)

    if tool_name == "CreatePlan":
        gate_label = "CreatePlan"
    elif tool_name in ("Write", "StrReplace"):
        path = edited_path(tool_input)
        if not path or not is_live_plan_disk_path(path):
            emit_allow()
            return 0
        gate_label = f"{tool_name} to .cursor/plans/*.plan.md"
    else:
        emit_allow()
        return 0

    transcript_path = payload.get("transcript_path")
    if not transcript_path:
        emit_allow()
        return 0

    rows = last_turn_rows(parse_transcript_lines(transcript_path))
    issues = plan_authoring_precreate_read_issues(rows)
    if not issues:
        emit_allow()
        return 0

    rules_dir = profile_rules_dir_label(
        workspace_roots=payload.get("workspace_roots") or [],
    )
    required = ", ".join(PLAN_AUTHORING_REQUIRED_MDC)
    message = (
        f"{gate_label} blocked (preToolUse): Read plan-authoring rules via the Read tool "
        f"BEFORE this tool. Missing: {', '.join(issues)}. "
        f"Required: {required}, and {rules_dir}/../docs/plans/PLAN_TEMPLATE.md "
        "(plan-creation-contract.mdc, plan-live-disk-sync.mdc, "
        "compose-operator-terminal-commands.mdc)."
    )
    emit_deny(message)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[check-pre-create-plan-reads] error: {exc}", file=sys.stderr)
        emit_allow()
        raise SystemExit(0) from exc
