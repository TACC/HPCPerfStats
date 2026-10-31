#!/usr/bin/env python3
"""Claude Code hooks derived from the Cursor hooks (cursor-hooks/hooks.json).

Ports the rule-dispatch core of the Cursor hooks to Claude Code events; the
plan-file hooks (CreatePlan / live ``.cursor/plans``) have no Claude Code
equivalent and are not ported. Routing comes from ``hook_task_router.py`` so
Cursor and Claude Code share one source of truth.

Subcommands (argv[1]), wired in ``.claude/settings.json``:

  prompt     UserPromptSubmit  start a new turn: clear the Read ledger
  record     PostToolUse Read  record Read rule/doc paths this turn
  pre-edit   PreToolUse edits  deny until triggered rules/docs were Read
  post-edit  PostToolUse edits record edits; flag rules missing dual registration
  stop       Stop              block close if edited rules lack dual registration

Stdlib only; fails open on any internal error.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HOOK_DIR = Path(__file__).resolve().parent
if str(HOOK_DIR) not in sys.path:
  sys.path.insert(0, str(HOOK_DIR))

from hook_task_router import (
  all_router_docs,
  required_docs_for_paths,
  triggered_rules_for_paths,
)
from hpc_hook_lib import (
  READ_VERIFY_EXEMPT_MDC,
  cursor_rule_file_exists,
  find_hook_task_router_file,
  find_router_file,
  rule_file_needs_router_entry,
  rule_listed_in_router,
)

EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
LEDGER_DIR = Path(tempfile.gettempdir()) / "hpc_claude_hooks"


def load_payload() -> dict:
  raw = sys.stdin.read()
  try:
    data = json.loads(raw) if raw.strip() else {}
  except json.JSONDecodeError:
    return {}
  return data if isinstance(data, dict) else {}


def ledger_path(payload: dict) -> Path:
  session = str(payload.get("session_id") or "default")
  safe = "".join(c for c in session if c.isalnum() or c in "-_")[:80]
  return LEDGER_DIR / f"{safe}.json"


# Keep the parenthesised ``except``: the .claude copy of this file runs on the
# host's PATH python3, which may predate PEP 758.
# fmt: off
def read_ledger(payload: dict) -> dict:
  try:
    data = json.loads(ledger_path(payload).read_text(encoding="utf-8"))
  except (OSError, json.JSONDecodeError):
    data = {}
  return {
    "reads": list(data.get("reads") or []),
    "edits": list(data.get("edits") or []),
  }
# fmt: on


def write_ledger(payload: dict, ledger: dict) -> None:
  path = ledger_path(payload)
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(json.dumps(ledger), encoding="utf-8")


def tool_path(payload: dict) -> str:
  tool_input = payload.get("tool_input") or {}
  if not isinstance(tool_input, dict):
    return ""
  for key in ("file_path", "notebook_path", "path"):
    value = tool_input.get(key)
    if isinstance(value, str) and value.strip():
      return value
  return ""


def missing_reads(paths: list[str], reads: list[str]) -> list[str]:
  """Rules/docs the paths trigger that were not Read this turn."""
  read_names = {Path(p).name.lower() for p in reads}
  read_paths = [p.replace("\\", "/").lower() for p in reads]
  missing: list[str] = []
  for rule in triggered_rules_for_paths(paths):
    if rule.lower() in READ_VERIFY_EXEMPT_MDC:
      continue
    if not cursor_rule_file_exists(rule):
      continue
    if rule.lower() not in read_names:
      missing.append(rule)
  for doc in required_docs_for_paths(paths):
    needle = doc.lower()
    if not any(p == needle or p.endswith("/" + needle) for p in read_paths):
      missing.append(doc)
  return missing


def dual_registration_issues(path: str) -> list[str]:
  needs_entry, basename = rule_file_needs_router_entry(path)
  if not needs_entry:
    return []
  issues: list[str] = []
  router = find_router_file([], rule_path=path)
  if router is None:
    return [f"`{basename}`: agent-discipline-core.mdc not found"]
  text = router.read_text(encoding="utf-8", errors="replace")
  if not rule_listed_in_router(text, basename):
    issues.append(f"`{basename}` missing from agent-discipline-core.mdc router")
  hook_router = find_hook_task_router_file()
  if hook_router is None:
    issues.append("hook_task_router.py not found")
  elif not rule_listed_in_router(
    hook_router.read_text(encoding="utf-8", errors="replace"), basename
  ):
    issues.append(f"`{basename}` missing from hook_task_router.py")
  return issues


def emit(obj: dict) -> None:
  print(json.dumps(obj))


def cmd_prompt(payload: dict) -> None:
  write_ledger(payload, {"reads": [], "edits": []})


def cmd_record(payload: dict) -> None:
  path = tool_path(payload)
  if not path:
    return
  norm = path.replace("\\", "/")
  if norm.endswith(".mdc") or any(
    norm.lower().endswith("/" + d.lower()) for d in all_router_docs()
  ):
    ledger = read_ledger(payload)
    ledger["reads"].append(norm)
    write_ledger(payload, ledger)


def cmd_pre_edit(payload: dict) -> None:
  path = tool_path(payload)
  if not path:
    return
  ledger = read_ledger(payload)
  missing = missing_reads([path, *ledger["edits"]], ledger["reads"])
  if not missing:
    return
  emit(
    {
      "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": (
          "RULE DISPATCH (agent-discipline-core.mdc): editing "
          f"{path} triggers rules/docs not Read this turn: "
          f"{', '.join(missing)}. Read each (rules under "
          "hpcperfstats/cursor-rules/, docs checkout-relative) with the "
          "Read tool, then retry the edit."
        ),
      }
    }
  )


def cmd_post_edit(payload: dict) -> None:
  path = tool_path(payload)
  if not path:
    return
  ledger = read_ledger(payload)
  if path not in ledger["edits"]:
    ledger["edits"].append(path)
    write_ledger(payload, ledger)
  issues = dual_registration_issues(path)
  if issues:
    emit(
      {
        "hookSpecificOutput": {
          "hookEventName": "PostToolUse",
          "additionalContext": (
            "MANDATORY (agent-discipline-core.mdc): rule not dual-registered: "
            f"{'; '.join(issues)}. Add the router row in "
            "agent-discipline-core.mdc and a matching ROUTER_ENTRIES entry in "
            "cursor-hooks/hook_task_router.py (plus RULES_README.md note) in "
            "this task."
          ),
        }
      }
    )


def cmd_stop(payload: dict) -> None:
  if payload.get("stop_hook_active"):
    return
  issues: list[str] = []
  for path in read_ledger(payload)["edits"]:
    issues.extend(dual_registration_issues(path))
  if issues:
    emit(
      {
        "decision": "block",
        "reason": (
          "Rule dual registration incomplete: "
          f"{'; '.join(sorted(set(issues)))}. Fix before closing."
        ),
      }
    )


COMMANDS = {
  "prompt": cmd_prompt,
  "record": cmd_record,
  "pre-edit": cmd_pre_edit,
  "post-edit": cmd_post_edit,
  "stop": cmd_stop,
}


def main() -> int:
  command = COMMANDS.get(sys.argv[1] if len(sys.argv) > 1 else "")
  if command is None:
    print(f"usage: {sys.argv[0]} {'|'.join(COMMANDS)}", file=sys.stderr)
    return 0
  try:
    command(load_payload())
  except Exception as exc:  # fail open: a hook bug must not wedge the session
    print(f"[hpc_claude_hooks] error: {exc}", file=sys.stderr)
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
