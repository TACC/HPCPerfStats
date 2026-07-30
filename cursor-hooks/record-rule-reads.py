#!/usr/bin/env python3
"""Cursor preToolUse — record turn activity (Reads / CreatePlan / live-plan Write).

Cursor persists a turn's tool calls to ``transcript_path`` only after the turn
ends, so mid-turn preToolUse gates cannot see this turn's tool parts and would
deny plan Write / mis-fire block-until / close-gate. This recorder fires first
on ``Read``/``ReadFile``/``CreatePlan``/``Write``/``StrReplace`` and appends to a
flock-protected sidecar so gates can union it with the lagging transcript. It
never blocks — always ``allow``.
"""
from __future__ import annotations

import sys
from pathlib import Path

HOOK_DIR = Path(__file__).resolve().parent
if str(HOOK_DIR) not in sys.path:
    sys.path.insert(0, str(HOOK_DIR))

from hpc_hook_lib import (  # noqa: E402
    emit_allow,
    load_json_stdin,
    record_turn_activity,
)


def tool_input_dict(payload: dict) -> dict:
    tool_input = payload.get("tool_input") or {}
    return tool_input if isinstance(tool_input, dict) else {}


def main() -> int:
    payload = load_json_stdin()
    tool_name = str(payload.get("tool_name") or payload.get("tool") or "")
    transcript_path = payload.get("transcript_path")
    if transcript_path:
        record_turn_activity(
            transcript_path,
            tool_name,
            tool_input_dict(payload),
        )
    emit_allow()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[record-rule-reads] error: {exc}", file=sys.stderr)
        emit_allow()
        raise SystemExit(0) from exc
