#!/usr/bin/env python3
"""Cursor postToolUse hook — warn when an edit or CreatePlan triggers unread rules."""
from __future__ import annotations

import sys
from pathlib import Path

HOOK_DIR = Path(__file__).resolve().parent
if str(HOOK_DIR) not in sys.path:
    sys.path.insert(0, str(HOOK_DIR))

from hpc_hook_lib import (  # noqa: E402
    PLAN_AUTHORING_REQUIRED_MDC,
    READ_VERIFY_EXEMPT_MDC,
    domain_rule_read_issues,
    edit_path_from_tool_part,
    emit_json,
    extract_plan_authority_markdown,
    extract_work_paths,
    is_live_plan_disk_path,
    load_json_stdin,
    parse_transcript_lines,
    paths_from_plan_markdown,
    plan_authority_content_issues,
    plan_content_issues,
    plan_template_read_issues,
    profile_rules_dir_label,
)
from hook_task_router import triggered_rules_for_paths  # noqa: E402


def tool_name_from_payload(payload: dict) -> str:
    return str(
        payload.get("tool_name")
        or payload.get("tool")
        or "",
    )


def plan_text_from_payload(payload: dict) -> str:
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return ""
    return str(tool_input.get("plan") or tool_input.get("content") or "")


def edited_path_from_payload(payload: dict) -> str:
    tool_input = payload.get("tool_input") or {}
    if isinstance(tool_input, dict):
        for key in ("path", "file_path", "target_file", "target_notebook"):
            value = tool_input.get(key)
            if isinstance(value, str) and value.strip():
                return value
    part = {
        "type": "tool_use",
        "name": tool_name_from_payload(payload) or "Write",
        "input": tool_input if isinstance(tool_input, dict) else {},
    }
    return edit_path_from_tool_part(part) or ""


def triggered_rules_for_payload(payload: dict) -> list[str]:
    tool_name = tool_name_from_payload(payload)
    workspace_roots = payload.get("workspace_roots") or []
    rows = parse_transcript_lines(payload.get("transcript_path") or "")
    work_paths = extract_work_paths(rows, workspace_roots)
    if tool_name == "CreatePlan":
        plan_text = plan_text_from_payload(payload)
        for plan_path in paths_from_plan_markdown(plan_text):
            if plan_path not in work_paths:
                work_paths.append(plan_path)
    elif edited_path := edited_path_from_payload(payload):
        if edited_path not in work_paths:
            work_paths.append(edited_path)

    triggered = triggered_rules_for_paths(work_paths)
    if tool_name == "CreatePlan":
        seen: dict[str, str] = {}
        for rule in [*PLAN_AUTHORING_REQUIRED_MDC, *triggered]:
            key = rule.lower()
            if key not in seen:
                seen[key] = rule
        triggered = list(seen.values())

    return [
        rule
        for rule in triggered
        if rule.lower() not in READ_VERIFY_EXEMPT_MDC
    ]


def main() -> int:
    payload = load_json_stdin()
    tool_name = tool_name_from_payload(payload)
    edited_path = edited_path_from_payload(payload)
    if tool_name != "CreatePlan" and not edited_path:
        emit_json({})
        return 0

    triggered = triggered_rules_for_payload(payload)
    transcript_path = payload.get("transcript_path")
    if not transcript_path:
        emit_json({})
        return 0

    workspace_roots = payload.get("workspace_roots") or []
    rows = parse_transcript_lines(transcript_path)
    issues = domain_rule_read_issues(triggered, rows)
    if tool_name == "CreatePlan":
        issues.extend(plan_template_read_issues(rows))
        issues.extend(plan_authority_content_issues(rows, workspace_roots))
    if edited_path and is_live_plan_disk_path(edited_path):
        tool_input = payload.get("tool_input") or {}
        inline_contents = ""
        if isinstance(tool_input, dict) and tool_name == "Write":
            inline_contents = str(tool_input.get("contents") or "")
        plan_md = inline_contents.strip() or extract_plan_authority_markdown(
            rows,
            workspace_roots,
        )
        for label in plan_content_issues(plan_md):
            issues.append(f"Plan disk content missing: {label}")

    if not issues:
        emit_json({})
        return 0

    rules_dir = profile_rules_dir_label(
        workspace_roots=payload.get("workspace_roots") or [],
    )
    action = "CreatePlan" if tool_name == "CreatePlan" else "this edit"
    emit_json(
        {
            "additional_context": (
                "RULE DISPATCH (pre-close): %s triggers domain or plan-authoring "
                f"rules that must be Read before further work. Issues: "
                f"{', '.join(issues)}. "
                f"Use the Read tool on each listed {rules_dir}/*.mdc "
                "and the workspace PLAN_TEMPLATE.md "
                "(HPCPerfStats/monitor/docs/plans/ or HPCPerfStats/docs/plans/) "
                "for plan turns, then list them under ## Agent rule dispatch at close."
            )
            % action,
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
