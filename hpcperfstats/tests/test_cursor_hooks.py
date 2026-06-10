"""Unit tests for Cursor hook helpers (.cursor/hooks/hpc_hook_lib.py)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parents[2] / ".cursor" / "hooks"
RULES_DIR = Path(__file__).resolve().parents[1] / "cursor-rules"
sys.path.insert(0, str(HOOKS_DIR))

import hpc_hook_lib as lib  # noqa: E402
from hook_task_router import ROUTER_ENTRIES, triggered_rules_for_paths  # noqa: E402

RULE_PATH = (
    "/repo/HPCPerfStats/hpcperfstats/cursor-rules/"
    "sync-timedb-archive-janitor-contract.mdc"
)
HOOK_LIB_PATH = "/repo/HPCPerfStats/.cursor/hooks/hpc_hook_lib.py"
TESTING_RULE_PATH = (
    "/repo/HPCPerfStats/hpcperfstats/cursor-rules/testing-best-practices.mdc"
)


def test_missing_close_gate_sections_detects_gaps():
  text = "## Final code review (senior engineer pass)\n\nSome review."
  missing = lib.missing_close_gate_sections(text)
  assert lib.AGENT_RULE_DISPATCH_LABEL in missing
  assert "## Post-implementation review" in missing
  assert "### Why it works" in missing


def test_agent_rule_dispatch_requires_mdc_or_na():
  empty_heading = "## Agent rule dispatch\n\n"
  missing = lib.missing_close_gate_sections(empty_heading)
  assert lib.AGENT_RULE_DISPATCH_DETAIL_LABEL in missing

  with_mdc = (
      "## Agent rule dispatch\n\n"
      "Read: plan-completion-gate.mdc, every-error-regression-test.mdc\n"
  )
  assert lib.AGENT_RULE_DISPATCH_LABEL not in lib.missing_close_gate_sections(with_mdc)

  with_na = "## Agent rule dispatch\n\nN/A — answer-only turn, no file edits.\n"
  assert lib.AGENT_RULE_DISPATCH_LABEL not in lib.missing_close_gate_sections(with_na)


def test_extract_read_rule_basenames_from_transcript():
  rows = [
      {
          "role": "assistant",
          "message": {
              "content": [
                  {
                      "type": "tool_use",
                      "name": "Read",
                      "input": {"path": RULE_PATH},
                  },
                  {
                      "type": "tool_use",
                      "name": "Read",
                      "input": {"path": "/repo/hpcperfstats/dbload/sync_timedb.py"},
                  },
              ],
          },
      },
  ]
  read_names = lib.extract_read_rule_basenames(rows)
  assert "sync-timedb-archive-janitor-contract.mdc" in read_names
  assert len(read_names) == 1


def test_domain_rule_read_issues_flags_missing_read():
  assistant_text = (
      "## Agent rule dispatch\n\n"
      "Read: sync-timedb-archive-janitor-contract.mdc\n"
  )
  rows_without_read = [
      {
          "role": "assistant",
          "message": {
              "content": [
                  {
                      "type": "tool_use",
                      "name": "Write",
                      "input": {"path": "a.py"},
                  },
              ],
          },
      },
  ]
  required = lib.domain_rules_required(assistant_text, ["a.py"])
  unread = lib.domain_rule_read_issues(required, rows_without_read)
  assert unread == ["Rule not read: sync-timedb-archive-janitor-contract.mdc"]


def test_domain_rule_read_issues_flags_read_after_first_edit():
  rows = [
      {
          "role": "assistant",
          "message": {
              "content": [
                  {
                      "type": "tool_use",
                      "name": "Write",
                      "input": {"path": HOOK_LIB_PATH},
                  },
              ],
          },
      },
      {
          "role": "assistant",
          "message": {
              "content": [
                  {
                      "type": "tool_use",
                      "name": "Read",
                      "input": {"path": TESTING_RULE_PATH},
                  },
              ],
          },
      },
  ]
  issues = lib.domain_rule_read_issues(["testing-best-practices.mdc"], rows)
  assert issues == ["Rule read after first edit/plan: testing-best-practices.mdc"]


def test_triggered_rule_dispatch_rejects_na_when_edits_trigger_rules():
  assistant_text = "## Agent rule dispatch\n\nN/A — hooks only.\n"
  issues = lib.triggered_rule_dispatch_issues(
      assistant_text,
      [HOOK_LIB_PATH],
  )
  assert issues == ["Agent rule dispatch (N/A invalid — edits triggered domain rules)"]


def test_triggered_rule_dispatch_requires_listed_rules():
  assistant_text = (
      "## Agent rule dispatch\n\n"
      "Read: plan-completion-gate.mdc\n"
  )
  issues = lib.triggered_rule_dispatch_issues(
      assistant_text,
      [HOOK_LIB_PATH],
  )
  assert "Rule not dispatched: testing-best-practices.mdc" in issues


def test_edge_cases_issues_requires_three_items():
  text = (
      "## Post-implementation review\n\n"
      "### Edge cases\n\n"
      "- one\n"
      "- two\n"
  )
  assert lib.edge_cases_issues(text) == [
      "### Edge cases (≥3 numbered/bulleted items required)",
  ]


def test_close_gate_issues_passes_when_rules_read_before_edit():
  assistant_text = (
      "## Agent rule dispatch\n\n"
      "Read: testing-best-practices.mdc\n"
      "## Final code review (senior engineer pass)\n\nok\n"
      "## Post-implementation review\n\n"
      "### Why it works\n\nok\n"
      "### Edge cases\n\n- a\n- b\n- c\n"
      "### Convention check\n\nok\n"
  )
  rows = [
      {
          "role": "assistant",
          "message": {
              "content": [
                  {
                      "type": "tool_use",
                      "name": "Read",
                      "input": {"path": TESTING_RULE_PATH},
                  },
              ],
          },
      },
      {
          "role": "assistant",
          "message": {
              "content": [
                  {
                      "type": "tool_use",
                      "name": "Write",
                      "input": {"path": HOOK_LIB_PATH},
                  },
              ],
          },
      },
  ]
  assert lib.close_gate_issues(assistant_text=assistant_text, transcript_rows=rows) == []


def test_hook_task_router_rules_exist_and_are_documented():
  core_text = (RULES_DIR / "agent-discipline-core.mdc").read_text(encoding="utf-8")
  for entry in ROUTER_ENTRIES:
    for rule in entry["rules"]:
      assert (RULES_DIR / rule).is_file(), rule
      if rule == "agent-discipline-core.mdc":
        continue
      assert rule in core_text, rule


def test_triggered_rules_for_cursor_hooks_path():
  rules = triggered_rules_for_paths([HOOK_LIB_PATH])
  assert "testing-best-practices.mdc" in rules


def test_readme_install_not_triggered_for_hooks_readme():
  rules = triggered_rules_for_paths(
      ["/repo/HPCPerfStats/.cursor/hooks/README.md"],
  )
  assert "readme-installation-sync.mdc" not in rules


def test_readme_install_triggered_for_operator_readme_paths():
  # Workspace layout: <workspace>/HPCPerfStats/README.md (git checkout README)
  hpc_rules = triggered_rules_for_paths(["/repo/HPCPerfStats/README.md"])
  root_rules = triggered_rules_for_paths(["/repo/README.md"])
  assert "readme-installation-sync.mdc" in hpc_rules
  assert "readme-installation-sync.mdc" in root_rules


def test_rule_file_needs_router_entry_for_new_mdc():
  ok, name = lib.rule_file_needs_router_entry(
      "/repo/hpcperfstats/cursor-rules/sync-timedb-foo-contract.mdc",
  )
  assert ok is True
  assert name == "sync-timedb-foo-contract.mdc"


def test_rule_file_skips_core_and_router():
  ok, _ = lib.rule_file_needs_router_entry(
      "/repo/hpcperfstats/cursor-rules/agent-discipline-core.mdc",
  )
  assert ok is False


def test_router_from_rule_path():
  rule_path = RULES_DIR / "sync-timedb-archive-janitor-contract.mdc"
  router = lib.router_from_rule_path(str(rule_path))
  assert router is not None
  assert router.name == "agent-discipline-core.mdc"
  assert router.is_file()


def _minimal_plan_markdown() -> str:
  return (
      "## Problem and facts\n\nfacts\n"
      "## Approach\n\nsteps\n"
      "## Testing\n\ntests\n"
      "## Implementation\n\nfiles\n"
      "## Cursor rules / docs sync\n\nno rule change\n"
      "## Final code review (mandatory before implementation close)\n\nreview\n"
      "---\n"
      "todos:\n"
      "  - id: post-implementation-review\n"
      "    content: review\n"
      "    status: pending\n"
  )


def test_turn_had_create_plan_detects_create_plan_tool():
  rows = [
      {
          "role": "assistant",
          "message": {
              "content": [
                  {
                      "type": "tool_use",
                      "name": "CreatePlan",
                      "input": {"plan": _minimal_plan_markdown()},
                  },
              ],
          },
      },
  ]
  assert lib.turn_had_create_plan(lib.last_turn_rows(rows)) is True
  assert lib.turn_had_closeable_work(lib.last_turn_rows(rows)) is True


def test_plan_content_issues_detects_missing_sections():
  issues = lib.plan_content_issues("## Approach\n\nonly approach")
  assert any("Problem and facts" in item for item in issues)
  assert any("post-implementation-review todo" in item for item in issues)


def test_paths_from_plan_markdown_extracts_backtick_paths():
  text = "Touch [`hpcperfstats/dbload/sync_timedb_async_day_close.py`](path)"
  paths = lib.paths_from_plan_markdown(text)
  assert "hpcperfstats/dbload/sync_timedb_async_day_close.py" in paths


def test_plan_template_read_issues_requires_read_before_create_plan():
  rows = [
      {
          "role": "assistant",
          "message": {
              "content": [
                  {
                      "type": "tool_use",
                      "name": "CreatePlan",
                      "input": {"plan": _minimal_plan_markdown()},
                  },
              ],
          },
      },
  ]
  assert lib.plan_template_read_issues(rows) == [
      "PLAN_TEMPLATE.md not read via Read tool",
  ]


def test_looks_like_plan_close_when_dispatch_present():
  assert lib.looks_like_plan_close(
      "## Agent rule dispatch\n\nRead: plan-creation-contract.mdc\n",
      had_plan=True,
  )


def test_close_gate_issues_flags_plan_content_and_reads():
  plan_md = _minimal_plan_markdown()
  assistant_text = (
      "## Agent rule dispatch\n\n"
      "Read: plan-creation-contract.mdc\n"
      "## Final code review (senior engineer pass)\n\nok\n"
      "## Post-implementation review\n\n"
      "### Why it works\n\nok\n"
      "### Edge cases\n\n- a\n- b\n- c\n"
      "### Convention check\n\nok\n"
      "\n\nPlan is ready for your review."
  )
  rows = [
      {
          "role": "assistant",
          "message": {
              "content": [
                  {
                      "type": "tool_use",
                      "name": "CreatePlan",
                      "input": {"plan": plan_md},
                  },
                  {"type": "text", "text": assistant_text},
              ],
          },
      },
  ]
  issues = lib.close_gate_issues(assistant_text=assistant_text, transcript_rows=rows)
  assert "PLAN_TEMPLATE.md not read via Read tool" in issues
  assert "Rule not read: plan-creation-contract.mdc" in issues


def test_check_close_gate_emits_followup_for_create_plan(tmp_path):
  transcript = tmp_path / "plan.jsonl"
  transcript.write_text(
      json.dumps(
          {
              "role": "user",
              "message": {"content": [{"type": "text", "text": "create a plan"}]},
          },
      )
      + "\n"
      + json.dumps(
          {
              "role": "assistant",
              "message": {
                  "content": [
                      {
                          "type": "tool_use",
                          "name": "CreatePlan",
                          "input": {"plan": "## Approach\n\nonly"},
                      },
                      {
                          "type": "text",
                          "text": "Plan is ready for your review.",
                      },
                  ],
              },
          },
      )
      + "\n",
      encoding="utf-8",
  )
  payload = {
      "status": "completed",
      "loop_count": 0,
      "transcript_path": str(transcript),
  }
  script = HOOKS_DIR / "check-close-gate.py"
  proc = subprocess.run(
      [sys.executable, str(script)],
      input=json.dumps(payload),
      capture_output=True,
      text=True,
      check=False,
  )
  assert proc.returncode == 0
  data = json.loads(proc.stdout.strip())
  assert "followup_message" in data
  assert "Close gate incomplete" in data["followup_message"]
  assert "PLAN_TEMPLATE.md" in data["followup_message"]


def test_check_edit_triggered_rules_create_plan_requires_reads(tmp_path):
  transcript = tmp_path / "plan.jsonl"
  transcript.write_text(
      json.dumps(
          {
              "role": "assistant",
              "message": {
                  "content": [
                      {
                          "type": "tool_use",
                          "name": "CreatePlan",
                          "input": {
                              "plan": (
                                  "## Implementation\n\n"
                                  "`hpcperfstats/dbload/sync_timedb_async_day_close.py`\n"
                              ),
                          },
                      },
                  ],
              },
          },
      )
      + "\n",
      encoding="utf-8",
  )
  payload = {
      "tool_name": "CreatePlan",
      "tool_input": {
          "plan": (
              "## Implementation\n\n"
              "`hpcperfstats/dbload/sync_timedb_async_day_close.py`\n"
          ),
      },
      "transcript_path": str(transcript),
  }
  script = HOOKS_DIR / "check-edit-triggered-rules.py"
  proc = subprocess.run(
      [sys.executable, str(script)],
      input=json.dumps(payload),
      capture_output=True,
      text=True,
      check=False,
  )
  assert proc.returncode == 0
  data = json.loads(proc.stdout.strip())
  assert "additional_context" in data
  assert "plan-creation-contract.mdc" in data["additional_context"]
  assert "PLAN_TEMPLATE.md" in data["additional_context"]


def test_turn_had_edits_detects_write_tool():
  rows = [
      {"role": "user", "message": {"content": [{"type": "text", "text": "fix it"}]}},
      {
          "role": "assistant",
          "message": {
              "content": [
                  {"type": "tool_use", "name": "StrReplace", "input": {"path": "a.py"}},
              ],
          },
      },
  ]
  assert lib.turn_had_edits(lib.last_turn_rows(rows)) is True


def test_check_close_gate_emits_followup_for_missing_headings(tmp_path):
  transcript = tmp_path / "t.jsonl"
  transcript.write_text(
      json.dumps(
          {
              "role": "user",
              "message": {"content": [{"type": "text", "text": "implement fix"}]},
          },
      )
      + "\n"
      + json.dumps(
          {
              "role": "assistant",
              "message": {
                  "content": [
                      {"type": "tool_use", "name": "Write", "input": {"path": "a.py"}},
                      {
                          "type": "text",
                          "text": "Fix is done and tests passed.",
                      },
                  ],
              },
          },
      )
      + "\n",
      encoding="utf-8",
  )
  payload = {
      "status": "completed",
      "loop_count": 0,
      "transcript_path": str(transcript),
  }
  script = HOOKS_DIR / "check-close-gate.py"
  proc = subprocess.run(
      [sys.executable, str(script)],
      input=json.dumps(payload),
      capture_output=True,
      text=True,
      check=False,
  )
  assert proc.returncode == 0
  data = json.loads(proc.stdout.strip())
  assert "followup_message" in data
  assert "Close gate incomplete" in data["followup_message"]


def test_check_close_gate_emits_followup_for_read_after_edit(tmp_path):
  transcript = tmp_path / "t.jsonl"
  close_text = (
      "## Agent rule dispatch\n\n"
      "Read: testing-best-practices.mdc\n\n"
      "## Final code review (senior engineer pass)\n\nok\n"
      "## Post-implementation review\n\n"
      "### Why it works\n\nok\n"
      "### Edge cases\n\n1. a\n2. b\n3. c\n"
      "### Convention check\n\nok\n"
      "\n\nImplementation is done and tests passed."
  )
  transcript.write_text(
      json.dumps(
          {
              "role": "user",
              "message": {"content": [{"type": "text", "text": "implement fix"}]},
          },
      )
      + "\n"
      + json.dumps(
          {
              "role": "assistant",
              "message": {
                  "content": [
                      {
                          "type": "tool_use",
                          "name": "Write",
                          "input": {"path": HOOK_LIB_PATH},
                      },
                  ],
              },
          },
      )
      + "\n"
      + json.dumps(
          {
              "role": "assistant",
              "message": {
                  "content": [
                      {
                          "type": "tool_use",
                          "name": "Read",
                          "input": {"path": TESTING_RULE_PATH},
                      },
                      {"type": "text", "text": close_text},
                  ],
              },
          },
      )
      + "\n",
      encoding="utf-8",
  )
  payload = {
      "status": "completed",
      "loop_count": 0,
      "transcript_path": str(transcript),
  }
  script = HOOKS_DIR / "check-close-gate.py"
  proc = subprocess.run(
      [sys.executable, str(script)],
      input=json.dumps(payload),
      capture_output=True,
      text=True,
      check=False,
  )
  assert proc.returncode == 0
  data = json.loads(proc.stdout.strip())
  assert "followup_message" in data
  assert "Rule read after first edit/plan: testing-best-practices.mdc" in data[
      "followup_message"
  ]
