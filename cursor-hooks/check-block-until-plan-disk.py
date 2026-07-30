#!/usr/bin/env python3
"""Cursor preToolUse hook — after CreatePlan, block tools until live plan disk write."""
from __future__ import annotations

import sys
from pathlib import Path

HOOK_DIR = Path(__file__).resolve().parent
if str(HOOK_DIR) not in sys.path:
    sys.path.insert(0, str(HOOK_DIR))

from hpc_hook_lib import (  # noqa: E402
    create_plan_payload_from_tool_part,
    emit_allow,
    emit_deny,
    is_allowed_tool_while_plan_disk_pending,
    iter_tool_parts,
    last_turn_rows,
    load_json_stdin,
    parse_transcript_lines,
    suggested_live_plan_disk_path,
    turn_create_plan_pending_disk_write,
)


def tool_input_dict(payload: dict) -> dict:
    tool_input = payload.get("tool_input") or {}
    return tool_input if isinstance(tool_input, dict) else {}


def main() -> int:
    payload = load_json_stdin()
    tool_name = str(payload.get("tool_name") or payload.get("tool") or "")
    if tool_name == "CreatePlan":
        emit_allow()
        return 0

    transcript_path = payload.get("transcript_path")
    if not transcript_path:
        emit_allow()
        return 0

    full_rows = parse_transcript_lines(transcript_path)
    rows = last_turn_rows(full_rows)
    if not turn_create_plan_pending_disk_write(
        rows,
        transcript_path=transcript_path,
        full_rows=full_rows,
    ):
        emit_allow()
        return 0

    tool_input = tool_input_dict(payload)
    if is_allowed_tool_while_plan_disk_pending(tool_name, tool_input):
        emit_allow()
        return 0

    suggested = ""
    for _event_idx, part in iter_tool_parts(rows):
        plan_payload = create_plan_payload_from_tool_part(part)
        if plan_payload:
            suggested = suggested_live_plan_disk_path(plan_payload)
            break

    path_hint = suggested or ".cursor/plans/<short-kebab-name>.plan.md"
    message = (
        f"{tool_name} blocked (preToolUse): This turn called CreatePlan but has not yet "
        f"Write/StrReplace `{path_hint}`. Only Read and Write/StrReplace to "
        ".cursor/plans/*.plan.md are allowed until the live disk plan exists "
        "(plan-live-disk-sync.mdc). Chat and CreatePlan do not count."
    )
    emit_deny(message)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[check-block-until-plan-disk] error: {exc}", file=sys.stderr)
        emit_allow()
        raise SystemExit(0) from exc
