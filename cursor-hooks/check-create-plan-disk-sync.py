#!/usr/bin/env python3
"""Cursor postToolUse hook — after CreatePlan, require same-turn Write to .cursor/plans/."""
from __future__ import annotations

import sys
from pathlib import Path

HOOK_DIR = Path(__file__).resolve().parent
if str(HOOK_DIR) not in sys.path:
    sys.path.insert(0, str(HOOK_DIR))

from hpc_hook_lib import (  # noqa: E402
    create_plan_disk_sync_post_tool_context,
    create_plan_payload_from_tool_part,
    emit_json,
    load_json_stdin,
    suggested_live_plan_disk_path,
)


def tool_name_from_payload(payload: dict) -> str:
    return str(payload.get("tool_name") or payload.get("tool") or "")


def main() -> int:
    payload = load_json_stdin()
    if tool_name_from_payload(payload) != "CreatePlan":
        emit_json({})
        return 0

    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}

    part = {
        "type": "tool_use",
        "name": "CreatePlan",
        "input": tool_input,
    }
    plan_payload = create_plan_payload_from_tool_part(part) or tool_input
    suggested = suggested_live_plan_disk_path(plan_payload)

    emit_json(
        {
            "additional_context": create_plan_disk_sync_post_tool_context(
                suggested,
            ),
        },
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[check-create-plan-disk-sync] error: {exc}", file=sys.stderr)
        emit_json({})
        raise SystemExit(0) from exc
