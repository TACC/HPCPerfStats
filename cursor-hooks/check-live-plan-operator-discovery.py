#!/usr/bin/env python3
"""Cursor preToolUse — deny live-plan Write/StrReplace on Operator discovery issues."""
from __future__ import annotations

import sys
from pathlib import Path

HOOK_DIR = Path(__file__).resolve().parent
if str(HOOK_DIR) not in sys.path:
    sys.path.insert(0, str(HOOK_DIR))

from hpc_hook_lib import (  # noqa: E402
    OPERATOR_FULL_READ_REQUIRED_MDC,
    emit_allow,
    emit_deny,
    full_file_rule_read_issues,
    is_live_plan_disk_path,
    last_turn_rows,
    load_json_stdin,
    operator_discovery_issues,
    operator_discovery_needs_full_rule_reads,
    parse_transcript_lines,
    reconstruct_live_plan_markdown_from_tool_input,
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
    if tool_name not in ("Write", "StrReplace"):
        emit_allow()
        return 0

    tool_input = tool_input_dict(payload)
    path = edited_path(tool_input)
    if not path or not is_live_plan_disk_path(path):
        emit_allow()
        return 0

    markdown = reconstruct_live_plan_markdown_from_tool_input(
        tool_name,
        tool_input,
        on_disk_path=path,
    )
    if markdown is None:
        emit_allow()
        return 0

    issues = operator_discovery_issues(markdown)

    if operator_discovery_needs_full_rule_reads(markdown):
        transcript_path = payload.get("transcript_path")
        if transcript_path:
            rows = last_turn_rows(parse_transcript_lines(transcript_path))
            issues.extend(
                full_file_rule_read_issues(rows, OPERATOR_FULL_READ_REQUIRED_MDC),
            )

    if not issues:
        emit_allow()
        return 0

    emit_deny(
        "OPERATOR DISCOVERY DENY: live plan Operator discovery checks failed "
        "(compose-operator-terminal-commands.mdc / full-file Read gate). "
        "Fix before Write/StrReplace:\n- " + "\n- ".join(issues),
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[check-live-plan-operator-discovery] error: {exc}", file=sys.stderr)
        emit_allow()
        raise SystemExit(0) from exc
