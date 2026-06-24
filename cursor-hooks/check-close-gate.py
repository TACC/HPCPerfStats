#!/usr/bin/env python3
"""Cursor stop hook — require close-gate headings after code or plan authoring turns."""
from __future__ import annotations

import sys
from pathlib import Path

HOOK_DIR = Path(__file__).resolve().parent
if str(HOOK_DIR) not in sys.path:
    sys.path.insert(0, str(HOOK_DIR))

from hpc_hook_lib import (  # noqa: E402
    close_gate_issues,
    create_plan_payload_from_tool_part,
    emit_json,
    extract_assistant_text,
    last_turn_rows,
    load_json_stdin,
    looks_like_task_close,
    parse_transcript_lines,
    plan_disk_sync_followup_message,
    plan_disk_sync_issues,
    profile_rules_dir_label,
    suggested_live_plan_disk_path,
    turn_had_closeable_work,
    turn_had_create_plan,
    turn_had_edits,
    turn_had_live_plan_disk_write,
)


def main() -> int:
    payload = load_json_stdin()
    status = payload.get("status")
    if status != "completed":
        emit_json({})
        return 0

    loop_count = int(payload.get("loop_count") or 0)
    if loop_count >= 3:
        emit_json({})
        return 0

    transcript_path = payload.get("transcript_path")
    if not transcript_path:
        emit_json({})
        return 0

    workspace_roots = payload.get("workspace_roots") or []
    rules_dir = profile_rules_dir_label(workspace_roots=workspace_roots)

    rows = parse_transcript_lines(transcript_path)
    turn_rows = last_turn_rows(rows)
    had_edits = turn_had_edits(turn_rows)
    had_plan = turn_had_create_plan(turn_rows)

    # Hard gate: CreatePlan without a same-turn .cursor/plans/*.plan.md write always
    # blocks turn end (does not depend on "plan is ready" phrasing).
    if had_plan and not turn_had_live_plan_disk_write(turn_rows):
        disk_issues = plan_disk_sync_issues(turn_rows)
        if disk_issues:
            suggested = ""
            for row in turn_rows:
                message = row.get("message") or {}
                for part in message.get("content") or []:
                    payload = create_plan_payload_from_tool_part(part)
                    if payload:
                        suggested = suggested_live_plan_disk_path(payload)
                        break
                if suggested:
                    break
            emit_json(
                {
                    "followup_message": plan_disk_sync_followup_message(
                        disk_issues,
                        suggested_path=suggested,
                        loop_count=loop_count,
                    ),
                },
            )
            return 0

    if not turn_had_closeable_work(turn_rows):
        emit_json({})
        return 0

    assistant_text = extract_assistant_text(turn_rows)
    if not looks_like_task_close(assistant_text, had_edits=had_edits, had_plan=had_plan):
        emit_json({})
        return 0

    issues = close_gate_issues(
        assistant_text=assistant_text,
        transcript_rows=turn_rows,
        workspace_roots=workspace_roots,
    )
    if not issues:
        emit_json({})
        return 0

    work_kind = "CreatePlan and/or file edits"
    plan_extra = ""
    if had_plan:
        plan_extra = (
            "\nPlan turns also require PLAN_TEMPLATE sections in the **live disk file** "
            f"({rules_dir}/../docs/plans/PLAN_TEMPLATE.md or "
            "HPCPerfStats/docs/plans/PLAN_TEMPLATE.md — Plan disk file, Operator discovery, "
            "Problem and facts, Approach, Testing, Implementation, Cursor rules, "
            "Final code review, Post-implementation review, post-implementation-review todo), "
            "a Write/StrReplace to .cursor/plans/*.plan.md in the same turn "
            "(plan-live-disk-sync.mdc; hooks validate disk content and operator-discovery "
            "shape per compose-operator-terminal-commands.mdc, not CreatePlan tool body), "
            "Read of plan-creation-contract.mdc + plan-live-disk-sync.mdc + "
            "compose-operator-terminal-commands.mdc + deploy-ini-with-code-no-phase-zero.mdc + "
            "plan-template-enforcement.mdc + PLAN_TEMPLATE.md before CreatePlan "
            "(preToolUse denies CreatePlan otherwise), and preToolUse blocks other tools "
            "after CreatePlan until disk write.\n"
        )
    followup = (
        "Close gate incomplete (Cursor stop hook). This turn used %s but the "
        "final assistant message is missing required sections or listed rules were "
        "not Read via the Read tool.%s\n\n"
        "Add in order:\n"
        "1. ## Agent rule dispatch — list every triggered "
        f"{rules_dir}/*.mdc you Read (or N/A only when work did not "
        "trigger domain rules). Each listed domain rule needs a Read tool call on "
        "that .mdc path BEFORE the first Write/StrReplace/CreatePlan in this turn. "
        "Auto-triggered rules from edited or plan-referenced paths must appear in "
        "dispatch (always-on rules exempt).\n"
        "2. ## Final code review (senior engineer pass) — full diff/plan + workflows; "
        "when cursor-rules/*.mdc changed, confirm dual registration in "
        "agent-discipline-core.mdc and hook_task_router.py; fix gaps before close\n"
        "3. ## Post-implementation review with ### Why it works, ### Edge cases "
        "(≥3), ### Convention check\n\n"
        f"Missing now: {', '.join(issues)}\n\n"
        "Per plan-completion-gate.mdc and agent-discipline-core.mdc. Do not claim "
        "the task or plan is done until all sections are present and listed rules "
        "were Read."
    ) % (work_kind, plan_extra)
    emit_json({"followup_message": followup})
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        # Fail open: do not block agent stop on hook bugs.
        print(f"[check-close-gate] error: {exc}", file=sys.stderr)
        emit_json({})
        raise SystemExit(0) from exc
