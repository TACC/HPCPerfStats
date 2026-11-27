#!/usr/bin/env python3
"""Claude Code port of the Cursor discipline hooks (HPCPerfStats/cursor-hooks).

Routing is NOT duplicated: it imports ``hook_task_router`` from the git checkout, so the Cursor
and Claude sides always agree on which rules a path triggers.

Subcommands (hook event in parentheses), all fail-open on internal errors:
  prompt     (UserPromptSubmit)  short reminder + task-keyword rule hints
  pre-edit   (PreToolUse)        deny Edit/Write until triggered rules (and router ``docs``,
                                 e.g. docs/SYNC_TIMEDB_STATE_MACHINE.md) were Read THIS turn
  post-edit  (PostToolUse)       rule dual-registration / hooks.json copy reminders
  stop       (Stop)              close gate: required headings + rule dispatch coverage

"This turn" = since the last real user prompt, matching Cursor's per-turn Read-before-edit proof.

Dual workspace: when ``.cursor/workspace-profile`` names ``dual``, edits under
``HPCPerfStats/monitor/`` resolve rules from ``HPCPerfStats/monitor/cursor-rules`` first (same
basename = monitor copy) and also require that tree's always-on rules, which CLAUDE.md does not
import. Without the marker, behaviour is the single-profile one (``.cursor/rules`` only).
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECKOUT = ROOT / "HPCPerfStats"
RULES = ROOT / ".cursor" / "rules"
MONITOR_RULES = CHECKOUT / "monitor" / "cursor-rules"
PROFILE_MARKER = ROOT / ".cursor" / "workspace-profile"
STATE = ROOT / ".claude" / "state"
sys.path.insert(0, str(CHECKOUT / "cursor-hooks"))

EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
MAX_STOP_BLOCKS = 3  # Cursor hooks.json loop_limit

# agent-discipline-core.mdc "Bug form / traceback / stall / timeout" + surgical-patch rows.
BUG_RE = re.compile(
  r"\b(bug|traceback|stack ?trace|stall(ed|s)?|hang|hung|timeout|time(d)? out|crash(es|ed)?|"
  r"regress(ion)?|exception|fix(es|ed)?|broken|wedge[sd]?|deadlock|zombie)\b",
  re.I,
)
BUG_RULES = (
  "bugfix-and-perf-change-playbook.mdc",
  "exhaustive-error-path-analysis.mdc",
  "logic-change-checklist.mdc",
  "grok-surgical-bug-fix-mandate.mdc",
  "refactor-dedup-priorities.mdc",
)
PLAN_RE = re.compile(r"\b(plan|\.plan\.md|cursor plan|create ?plan)\b", re.I)
PLAN_RULES = (
  "plan-creation-contract.mdc",
  "plan-live-disk-sync.mdc",
  "plan-template-enforcement.mdc",
  "compose-operator-terminal-commands.mdc",
  "deploy-ini-with-code-no-phase-zero.mdc",
  "operator-command-lessons-learned.mdc",
)
PRODUCT_PREFIXES = ("hpcperfstats/", "scripts/", "services-conf/")
# Monitor agent-discipline-core.mdc "Traceback / runtime error / test failure fix" row
# (every-error-regression-test.mdc is monitor always-on, so it is required anyway).
MONITOR_BUG_RULES = (
  "traceback-fix-discipline.mdc",
  "logic-change-checklist.mdc",
  "grok-surgical-bug-fix-mandate.mdc",
  "refactor-dedup-priorities.mdc",
)

CLOSE_HEADINGS = (
  ("Agent rule dispatch", re.compile(r"^##\s*Agent rule dispatch", re.I | re.M)),
  (
    "Final code review (senior engineer pass)",
    re.compile(r"^##\s*Final code review\s*\(senior engineer pass\)", re.I | re.M),
  ),
  ("Post-implementation review", re.compile(r"^##\s*Post-implementation review", re.I | re.M)),
)

# compose-operator-terminal-commands.mdc "Anti-pattern — heredoc SQL in a pasted block":
# copying a code block out of the Claude Code TUI indents every line by 2 spaces, so a
# heredoc terminator never matches (shell waits at `>`), and copy/paste hard-wraps long
# lines. Checked on every Stop, edits or not: operator blocks live only in the message.
SHELL_FENCE_RE = re.compile(
  r"^[ \t]*```(?:bash|sh|shell|console|zsh)[ \t]*\n(.*?)^[ \t]*```", re.M | re.S
)
HEREDOC_RE = re.compile(r"(?<!<)<<-?[ \t]*['\"]?[A-Za-z_]\w*")  # not `<<<` here-strings
# 68 + the 2-space copy indent = 70 on screen; 69-char lines wrapped on c573-002
# (2026-11-23) and dropped the measure conf server/queue lines.
PASTE_LINE_MAX = 68


# ---------------------------------------------------------------- helpers
def load_payload() -> dict:
  try:
    data = json.load(sys.stdin)
    return data if isinstance(data, dict) else {}
  except Exception:
    return {}


def emit(obj: dict) -> None:
  sys.stdout.write(json.dumps(obj))


def always_on_in(rules_dir: Path) -> list:
  """Resolved paths of ``alwaysApply: true`` rules in one rule tree."""
  out = []
  for mdc in sorted(rules_dir.glob("*.mdc")):
    head = mdc.read_text(encoding="utf-8", errors="replace")[:600]
    if re.search(r"^alwaysApply:\s*true\s*$", head, re.M):
      out.append(mdc.resolve())
  return out


def always_on_rules() -> set:
  """Rules CLAUDE.md already imports every turn (the ``.cursor/rules`` always-on set)."""
  return set(always_on_in(RULES))


def dual_workspace() -> bool:
  """True when the workspace opted into combined container + monitor work."""
  try:
    words = PROFILE_MARKER.read_text(encoding="utf-8").split()
  except OSError:
    return False
  return bool(words) and words[0].lower() == "dual"


def is_monitor_path(rel: str) -> bool:
  return rel == "monitor" or rel.startswith("monitor/")


def monitor_scope(rel: str) -> bool:
  """Monitor rules govern this path (dual workspace and a path under monitor/)."""
  return dual_workspace() and is_monitor_path(rel)


def rule_dirs_for(rel: str) -> tuple:
  """Rule trees searched for a path, most specific first."""
  return (MONITOR_RULES, RULES) if monitor_scope(rel) else (RULES,)


def resolve_rule(name: str, rel: str):
  """Resolved path of rule ``name`` for edits to ``rel``, or None if no tree has it."""
  for d in rule_dirs_for(rel):
    cand = d / name
    if cand.is_file():
      return cand.resolve()
  return None


def rel_for_router(file_path: str):
  """Workspace-relative path for routing, or None if outside the workspace / not gated."""
  try:
    p = Path(file_path).resolve()
  except OSError:
    return None
  if ROOT not in p.parents:
    return None
  rel = p.relative_to(ROOT)
  if len(rel.parts) == 1 and rel.name in ("CLAUDE.md", "CANONICAL_WORKSPACE.md"):
    return None  # workspace config/docs, no rules route here
  if rel.parts[0] in (".claude", "node_modules", ".venv", "test_runs", ".unlazy"):
    return None
  if rel.parts[0] == "HPCPerfStats":
    if len(rel.parts) < 2 or rel.parts[1] in ("test_runs", ".pytest_cache", ".ruff_cache"):
      return None  # git-ignored run artifacts inside the checkout
    return str(Path(*rel.parts[1:]))
  return str(rel)


def triggered_for(rel_path: str) -> list:
  """Resolved rule paths the Cursor router triggers for a path, limited to rules on disk.

  Monitor-scoped paths also require the monitor always-on rules (not imported by CLAUDE.md).
  """
  from hook_task_router import triggered_rules_for_paths

  names = list(triggered_rules_for_paths([rel_path]))
  out = []
  if monitor_scope(rel_path):
    out.extend(always_on_in(MONITOR_RULES))
  for name in names:
    path = resolve_rule(name, rel_path)
    if path and path not in out:
      out.append(path)
  return out


def extra_rules_for(rel: str, prompt: str) -> list:
  """Bug / plan rules a path needs on top of the router, as resolved paths."""
  names = []
  if monitor_scope(rel):
    if BUG_RE.search(prompt):
      names += MONITOR_BUG_RULES
  elif rel.startswith(PRODUCT_PREFIXES) and BUG_RE.search(prompt):
    names += BUG_RULES
  if rel.startswith(".cursor/plans/") or ".plan.md" in rel:
    names += PLAN_RULES
  return [p for p in (resolve_rule(n, rel) for n in names) if p]


def docs_for(rel_path: str) -> list:
  """Checkout-relative docs (router ``docs``) a path requires Read, limited to existing files."""
  from hook_task_router import required_docs_for_paths

  return [d for d in required_docs_for_paths([rel_path]) if (CHECKOUT / d).is_file()]


def read_rows(path: str) -> list:
  rows = []
  try:
    with open(path, encoding="utf-8") as fh:
      for line in fh:
        line = line.strip()
        if line:
          try:
            rows.append(json.loads(line))
          except ValueError:
            pass
  except OSError:
    pass
  return rows


def transcript_rows(payload: dict) -> list:
  """Rows of the transcript that issued this hook event.

  Inside a subagent, ``transcript_path`` still names the parent session, whose rows hold
  none of the subagent's Reads, so the read-before-edit and close checks could never pass.
  When the payload identifies a subagent (``agent_transcript_path``, or ``agent_id`` /
  ``agentId``), use that agent's own transcript at
  ``<session>/subagents/agent-<id>.jsonl`` when it exists; otherwise fall back to
  ``transcript_path``.
  """
  main = payload.get("transcript_path") or ""
  agent_tp = payload.get("agent_transcript_path") or ""
  agent_id = payload.get("agent_id") or payload.get("agentId") or ""
  if not agent_tp and agent_id and main.endswith(".jsonl"):
    agent_tp = str(Path(main[: -len(".jsonl")]) / "subagents" / f"agent-{agent_id}.jsonl")
  if agent_tp and Path(agent_tp).is_file():
    return read_rows(agent_tp)
  return read_rows(main)


def blocks(row: dict) -> list:
  content = (row.get("message") or {}).get("content")
  if isinstance(content, str):
    return [{"type": "text", "text": content}]
  return [b for b in (content or []) if isinstance(b, dict)]


def prompt_text(row: dict) -> str:
  if row.get("type") != "user" or row.get("isMeta") or row.get("isSidechain"):
    return ""
  parts = [b.get("text", "") for b in blocks(row) if b.get("type") == "text"]
  text = "\n".join(parts).strip()
  # Hook feedback / injected reminders are not real prompts.
  if not text or text.startswith(("<system-reminder", "Stop hook", "<local-command", "<command-")):
    return ""
  return text


def turn_rows(rows: list) -> tuple:
  """(rows since the last real user prompt, that prompt's text)."""
  start = 0
  text = ""
  for i, row in enumerate(rows):
    t = prompt_text(row)
    if t:
      start, text = i, t
  return rows[start:], text


def tool_uses(rows: list):
  for row in rows:
    if row.get("type") != "assistant":
      continue
    for b in blocks(row):
      if b.get("type") == "tool_use":
        yield b.get("name", ""), (b.get("input") or {})


def reads_this_turn(rows: list) -> set:
  """Resolved paths of ``.mdc`` files Read this turn (a same-named copy in the other tree
  does not count)."""
  out = set()
  for name, inp in tool_uses(rows):
    fp = inp.get("file_path") or ""
    if name == "Read" and fp.endswith(".mdc"):
      try:
        out.add(Path(fp).resolve())
      except OSError:
        pass
  return out


def doc_reads_this_turn(rows: list) -> set:
  """Checkout-relative, lowercased paths of checkout files Read this turn."""
  out = set()
  for name, inp in tool_uses(rows):
    fp = inp.get("file_path") or ""
    if name == "Read" and fp:
      try:
        out.add(str(Path(fp).resolve().relative_to(CHECKOUT)).lower())
      except (OSError, ValueError):
        pass
  return out


def edited_paths(rows: list) -> list:
  out = []
  for name, inp in tool_uses(rows):
    if name in EDIT_TOOLS:
      fp = inp.get("file_path") or inp.get("notebook_path") or ""
      if fp and fp not in out:
        out.append(fp)
  return out


def assistant_text(rows: list) -> str:
  return "\n".join(
    b.get("text", "")
    for row in rows
    if row.get("type") == "assistant"
    for b in blocks(row)
    if b.get("type") == "text"
  )


# ---------------------------------------------------------------- hooks
def cmd_prompt(payload: dict) -> None:
  prompt = payload.get("prompt") or ""
  lines = [
    "Workspace discipline (enforced by hooks): Read every triggered .cursor/rules/*.mdc before the "
    "first edit of a turn (PreToolUse denies otherwise), and finish edit turns with "
    "'## Agent rule dispatch', '## Final code review (senior engineer pass)', "
    "'## Post-implementation review' (>=3 edge cases). Every fix needs a regression test. "
    + (
      "Dual workspace (.cursor/workspace-profile = dual): edits under HPCPerfStats/monitor/ follow "
      "HPCPerfStats/monitor/cursor-rules (its always-on rules are Read-gated too); all other edits "
      "follow .cursor/rules."
      if dual_workspace()
      else "monitor/ is read-only."
    )
  ]
  if BUG_RE.search(prompt):
    lines.append(
      "Bug/stall/timeout task: before the first product-code patch Read: " + ", ".join(BUG_RULES)
    )
  if PLAN_RE.search(prompt):
    lines.append(
      "Plan task: live plans go to .cursor/plans/*.plan.md (disk first). Read: "
      + ", ".join(PLAN_RULES)
      + ", and HPCPerfStats/docs/plans/PLAN_TEMPLATE.md"
    )
  emit(
    {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "\n".join(lines)}}
  )


def cmd_pre_edit(payload: dict) -> None:
  tool_input = payload.get("tool_input") or {}
  fp = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
  rel = rel_for_router(fp) if fp else None
  if rel is None:
    return
  rows, prompt = turn_rows(transcript_rows(payload))
  needed = list(triggered_for(rel))
  needed += [p for p in extra_rules_for(rel, prompt) if p not in needed]
  skip = always_on_rules()
  done = reads_this_turn(rows)
  missing = [p for p in needed if p not in done and p not in skip]
  doc_done = doc_reads_this_turn(rows)
  missing_docs = [d for d in docs_for(rel) if d.lower() not in doc_done]
  if not missing and not missing_docs:
    return
  reason = (
    f"BLOCKED by workspace discipline hook: editing `{rel}` triggers {len(needed)} Cursor rule(s) "
    f"(plus required docs) that must be Read this turn before editing. "
    f"Unread ({len(missing) + len(missing_docs)}):\n"
    + "\n".join([f"  - {p}" for p in missing] + [f"  - {CHECKOUT / d}" for d in missing_docs])
    + "\nRead them with the Read tool (parallel calls are fine; a partial Read of a very large "
    "file counts), list them under '## Agent rule dispatch' at close, then retry the edit."
  )
  emit(
    {
      "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
      }
    }
  )


def cmd_post_edit(payload: dict) -> None:
  tool_input = payload.get("tool_input") or {}
  fp = tool_input.get("file_path") or ""
  notes = []
  p = Path(fp) if fp else None
  if p and p.suffix == ".mdc" and p.parent.name == "cursor-rules":
    # Register against the edited tree's own router (monitor rules -> monitor core).
    core_path = p.parent / "agent-discipline-core.mdc"
    if not core_path.is_file():
      core_path = RULES / "agent-discipline-core.mdc"
    core = core_path.read_text(encoding="utf-8", errors="replace")
    router = (CHECKOUT / "cursor-hooks" / "hook_task_router.py").read_text(
      encoding="utf-8", errors="replace"
    )
    gaps = [
      where
      for where, text in (("agent-discipline-core.mdc", core), ("cursor-hooks/hook_task_router.py", router))
      if p.name not in text
    ]
    if gaps:
      notes.append(
        f"Rule `{p.name}` is not referenced in: {', '.join(gaps)}. Dual registration is mandatory "
        "(agent-discipline-core.mdc task router AND hook_task_router.py ROUTER_ENTRIES) in this task. "
        "Then re-run .claude/scripts/sync-cursor-rules.py."
      )
  if p and p.name == "hooks.json" and p.parent.name == "cursor-hooks":
    live = ROOT / ".cursor" / "hooks.json"
    if not live.is_file() or live.read_text() != p.read_text():
      notes.append(
        "Cursor needs a real-file copy: run `cp HPCPerfStats/cursor-hooks/hooks.json .cursor/hooks.json` "
        "from the workspace root (never symlink it)."
      )
  if notes:
    emit(
      {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "\n".join(notes)}}
    )


def _stop_evaluate(payload: dict) -> tuple:
  """(gated edits, issues) for the current turn; ([], []) when nothing is gated."""
  rows, _ = turn_rows(transcript_rows(payload))
  gated = [(fp, rel_for_router(fp)) for fp in edited_paths(rows)]
  gated = [(fp, rel) for fp, rel in gated if rel is not None]
  if not gated:
    return [], []
  # The final message can reach the transcript after Stop fires; the event may carry it.
  text = assistant_text(rows) + "\n" + str(payload.get("last_assistant_message") or "")
  issues = []
  for label, rx in CLOSE_HEADINGS:
    if not rx.search(text):
      issues.append(f"missing heading `## {label}`")

  triggered = []
  for _, rel in gated:
    for r in triggered_for(rel):
      if r not in triggered:
        triggered.append(r)
  skip = always_on_rules()
  triggered = [r for r in triggered if r not in skip]
  # Check the LAST section: a re-sent close after a block appends to this turn's text,
  # and the first (rejected) section would otherwise be checked forever.
  m = ([None] + list(re.finditer(
    r"^##\s*Agent rule dispatch(.*?)(?=^##\s|\Z)", text, re.I | re.M | re.S
  )))[-1]
  if m:
    listed = {n.lower() for n in re.findall(r"([\w.-]+\.mdc)\b", m.group(1))}
    if triggered:
      # Monitor always-on rules must be Read but, like any always-on rule, need not be listed.
      exempt = set(always_on_in(MONITOR_RULES)) if any(monitor_scope(r) for _, r in gated) else set()
      absent = sorted({r.name for r in triggered if r not in exempt and r.name.lower() not in listed})
      if absent:
        issues.append(
          "Agent rule dispatch must list every triggered rule (N/A is invalid when edits "
          f"triggered rules); not listed: {', '.join(absent)}"
        )
      done = reads_this_turn(rows)
      unread = [str(r) for r in triggered if r not in done]
      if unread:
        issues.append(f"triggered rules never Read this turn: {', '.join(unread)}")
  doc_done = doc_reads_this_turn(rows)
  unread_docs = sorted({d for _, rel in gated for d in docs_for(rel) if d.lower() not in doc_done})
  if unread_docs:
    issues.append(f"required docs never Read this turn: {', '.join(unread_docs)}")
  m = ([None] + list(re.finditer(
    r"^##\s*Post-implementation review(.*?)(?=^##\s|\Z)", text, re.I | re.M | re.S
  )))[-1]
  if m and len(re.findall(r"^\s*(?:[-*]|\d+[.)])\s+\S", m.group(1), re.M)) < 3:
    issues.append("Post-implementation review needs >=3 edge-case bullets")
  return gated, issues


def shell_block_issues(text: str) -> list:
  """Paste-safety issues in the shell fences of one message (empty when all are safe)."""
  issues = []
  for i, m in enumerate(SHELL_FENCE_RE.finditer(text or ""), 1):
    body = m.group(1)
    if HEREDOC_RE.search(body):
      issues.append(
        f"bash block {i} uses a heredoc (`<<`); the TUI copy indents every line by 2 "
        "spaces so the terminator never matches. Use a quoted variable built from short "
        "lines and `psql -c \"$Q\"` (compose-operator-terminal-commands.mdc, heredoc anti-pattern)"
      )
    long = [n for n, line in enumerate(body.splitlines(), 1) if len(line) > PASTE_LINE_MAX]
    if long:
      issues.append(
        f"bash block {i} has lines over {PASTE_LINE_MAX} chars (block lines "
        f"{', '.join(map(str, long[:8]))}); copy/paste hard-wraps them. Split them"
      )
  return issues


def final_message_text(payload: dict) -> str:
  """The final assistant message: the event field, else the last text block this turn."""
  msg = str(payload.get("last_assistant_message") or "")
  if msg:
    return msg
  rows, _ = turn_rows(transcript_rows(payload))
  texts = [
    b.get("text", "")
    for row in rows
    if row.get("type") == "assistant"
    for b in blocks(row)
    if b.get("type") == "text"
  ]
  return texts[-1] if texts else ""


def _stop_all(payload: dict) -> tuple:
  gated, issues = _stop_evaluate(payload)
  return gated, issues + shell_block_issues(final_message_text(payload))


def cmd_stop(payload: dict) -> None:
  STATE.mkdir(parents=True, exist_ok=True)
  gated, issues = _stop_all(payload)
  if issues:
    time.sleep(2.0)  # transcript may lag the final message: re-read once before blocking
    gated, issues = _stop_all(payload)
  try:  # keys only (no content): lets us see which fields this Claude Code version sends
    (STATE / "last-stop-payload-keys.json").write_text(json.dumps(sorted(payload)))
  except OSError:
    pass
  if not issues:
    return
  counter = STATE / f"stop-{payload.get('session_id', 'x')}.count"
  try:
    n = int(counter.read_text() or 0)
  except (OSError, ValueError):
    n = 0
  if n >= MAX_STOP_BLOCKS:
    counter.unlink(missing_ok=True)
    return
  counter.write_text(str(n + 1))
  emit(
    {
      "decision": "block",
      "reason": (
        (
          "Close gate incomplete (workspace discipline; plan-completion-gate.mdc). Edited: "
          + ", ".join(rel for _, rel in gated)
          + ". Fix and re-send the full close sequence:\n- "
          if gated
          else "Operator paste blocks are not paste-safe. Fix and re-send the message:\n- "
        )
        + "\n- ".join(issues)
      ),
    }
  )


def main() -> int:
  cmd = sys.argv[1] if len(sys.argv) > 1 else ""
  fn = {"prompt": cmd_prompt, "pre-edit": cmd_pre_edit, "post-edit": cmd_post_edit, "stop": cmd_stop}.get(
    cmd
  )
  if fn is None:
    return 0
  try:
    fn(load_payload())
  except Exception as exc:  # fail open, like the Cursor hooks
    print(f"[hpc_claude_hooks:{cmd}] error: {exc}", file=sys.stderr)
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
