#!/usr/bin/env python3
"""Cursor postToolUse hook — warn when an edit triggers domain rules not yet Read."""
from __future__ import annotations

import sys
from pathlib import Path

HOOK_DIR = Path(__file__).resolve().parent
if str(HOOK_DIR) not in sys.path:
    sys.path.insert(0, str(HOOK_DIR))

from hpc_hook_lib import (  # noqa: E402
    READ_VERIFY_EXEMPT_MDC,
    domain_rule_read_issues,
    edit_path_from_tool_part,
    emit_json,
    extract_edited_paths,
    load_json_stdin,
    parse_transcript_lines,
)
from hook_task_router import triggered_rules_for_paths  # noqa: E402


def edited_path_from_payload(payload: dict) -> str:
    tool_input = payload.get("tool_input") or {}
    if isinstance(tool_input, dict):
        for key in ("path", "file_path", "target_file", "target_notebook"):
            value = tool_input.get(key)
            if isinstance(value, str) and value.strip():
                return value
    part = {
        "type": "tool_use",
        "name": payload.get("tool_name") or payload.get("tool") or "Write",
        "input": tool_input if isinstance(tool_input, dict) else {},
    }
    return edit_path_from_tool_part(part) or ""


def main() -> int:
    payload = load_json_stdin()
    edited_path = edited_path_from_payload(payload)
    if not edited_path:
        emit_json({})
        return 0

    triggered = [
        rule
        for rule in triggered_rules_for_paths([edited_path])
        if rule.lower() not in READ_VERIFY_EXEMPT_MDC
    ]
    if not triggered:
        emit_json({})
        return 0

    transcript_path = payload.get("transcript_path")
    if not transcript_path:
        emit_json({})
        return 0

    rows = parse_transcript_lines(transcript_path)
    # Include this edit in the path set so dispatch/read checks see current file.
    edited_paths = extract_edited_paths(rows)
    if edited_path not in edited_paths:
        edited_paths.append(edited_path)

    issues = domain_rule_read_issues(triggered, rows)
    if not issues:
        emit_json({})
        return 0

    emit_json(
        {
            "additional_context": (
                "RULE DISPATCH (pre-close): this edit triggers domain rules that must "
                f"be Read before further edits. Issues: {', '.join(issues)}. "
                "Use the Read tool on each listed hpcperfstats/cursor-rules/*.mdc, "
                "then list them under ## Agent rule dispatch at close."
            ),
        },
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[check-edit-triggered-rules] error: {exc}", file=sys.stderr)
        emit_json({})
        raise SystemExit(0) from exc
