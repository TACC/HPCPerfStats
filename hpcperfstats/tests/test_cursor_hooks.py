"""Unit tests for Cursor hook helpers (cursor-hooks/hpc_hook_lib.py)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parents[2] / "cursor-hooks"
RULES_DIR = Path(__file__).resolve().parents[1] / "cursor-rules"
sys.path.insert(0, str(HOOKS_DIR))

import hpc_hook_lib as lib  # noqa: E402
from hook_task_router import (  # noqa: E402
    MONITOR_ROUTER_ENTRIES,
    ROUTER_ENTRIES,
    detect_rules_profile,
    profile_rules_dir_label,
    triggered_rules_for_paths,
)

RULE_PATH = (
    "/repo/HPCPerfStats/hpcperfstats/cursor-rules/"
    "sync-timedb-archive-janitor-contract.mdc"
)
MONITOR_RULE_PATH = (
    "/repo/HPCPerfStats/monitor/cursor-rules/monitor-c-conventions.mdc"
)
MONITOR_PLAN_TEMPLATE = (
    "/repo/HPCPerfStats/monitor/docs/plans/PLAN_TEMPLATE.md"
)
MONITOR_GLOBAL_TESTING_PATH = (
    "/repo/HPCPerfStats/monitor/cursor-rules/global-testing-discipline.mdc"
)
MONITOR_CURSOR_SYNC_PATH = (
    "/repo/HPCPerfStats/monitor/cursor-rules/monitor-cursor-rules-sync.mdc"
)
HOOK_LIB_PATH = "/repo/HPCPerfStats/cursor-hooks/hpc_hook_lib.py"
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


def test_sync_timedb_regression_battery_issues_requires_citation():
  assistant_text = (
      "## Agent rule dispatch\n\nsync-timedb-change-regression-gate.mdc\n"
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
                      "name": "Write",
                      "input": {
                          "path": "HPCPerfStats/hpcperfstats/dbload/sync_timedb.py",
                      },
                  },
              ],
          },
      },
  ]
  issues = lib.sync_timedb_regression_battery_issues(
      assistant_text,
      rows,
      ["hpcperfstats/dbload/sync_timedb.py"],
  )
  assert issues
  cited = assistant_text + "\nrun_sync_timedb_regression_battery"
  assert not lib.sync_timedb_regression_battery_issues(
      cited,
      rows,
      ["hpcperfstats/dbload/sync_timedb.py"],
  )


def test_sync_timedb_plan_todo_issues_requires_battery_and_verify():
  plan = (
      "---\nname: sync stall\n"
      "todos:\n"
      "  - id: regression-battery-script\n"
      "    status: pending\n"
      "  - id: operator-stall-verify-doc\n"
      "    status: pending\n"
      "---\n\n"
      "## Problem and facts\n\nsync_timedb stall\n"
  )
  assert lib.sync_timedb_plan_todo_issues(plan) == []
  missing = lib.sync_timedb_plan_todo_issues(
      "## Problem and facts\n\nsync_timedb day_close stall\n",
  )
  assert any("regression-battery" in issue or "run-full-battery" in issue for issue in missing)
  assert any("operator-stall-verify" in issue for issue in missing)


def test_close_gate_issues_passes_when_rules_read_before_edit():
  assistant_text = (
      "## Agent rule dispatch\n\n"
      "Read: testing-best-practices.mdc, global-testing-discipline.mdc, "
      "monitor-cursor-rules-sync.mdc\n"
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
                  {
                      "type": "tool_use",
                      "name": "Read",
                      "input": {"path": MONITOR_GLOBAL_TESTING_PATH},
                  },
                  {
                      "type": "tool_use",
                      "name": "Read",
                      "input": {"path": MONITOR_CURSOR_SYNC_PATH},
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


def test_triggered_rules_for_dockerignore_path():
  rules = triggered_rules_for_paths(["/repo/HPCPerfStats/.dockerignore"])
  assert "dockerignore-test-artifacts-sync.mdc" in rules


def test_triggered_rules_sync_timedb_lib_helper_path():
  rules = triggered_rules_for_paths(
      ["hpcperfstats/dbload/lib/sync_timedb_day_close_manifest.py"],
  )
  assert "sync-timedb-archive-janitor-contract.mdc" in rules


def test_triggered_rules_stale_dbload_helper_path_not_matched():
  rules = triggered_rules_for_paths(
      ["hpcperfstats/dbload/sync_timedb_day_close_manifest.py"],
  )
  assert "sync-timedb-archive-janitor-contract.mdc" not in rules


def test_resolve_cursor_rule_path_finds_checkout_rules():
  resolved = lib.resolve_cursor_rule_path("testing-best-practices.mdc")
  assert resolved is not None
  assert resolved.name == "testing-best-practices.mdc"
  assert resolved.is_file()


def test_readme_install_not_triggered_for_hooks_readme():
  rules = triggered_rules_for_paths(
      ["/repo/HPCPerfStats/cursor-hooks/README.md"],
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


def test_rule_dual_registration_issues_flags_orphan_rule(tmp_path):
  orphan_dir = tmp_path / "cursor-rules"
  orphan_dir.mkdir()
  orphan = orphan_dir / "orphan-test-rule-contract.mdc"
  orphan.write_text(
      "---\ndescription: hook dual-registration test fixture\n---\n# Orphan\n",
      encoding="utf-8",
  )
  issues = lib.rule_dual_registration_issues([str(orphan)])
  assert any("agent-discipline-core.mdc task router" in issue for issue in issues)
  assert any("hook_task_router.py" in issue for issue in issues)


def test_rule_dual_registration_issues_passes_for_registered_rule():
  registered_path = str(RULES_DIR / "testing-best-practices.mdc")
  assert lib.rule_dual_registration_issues([registered_path]) == []


def test_rule_dual_registration_issues_skips_non_rule_paths():
  assert lib.rule_dual_registration_issues([HOOK_LIB_PATH]) == []


def _minimal_plan_markdown() -> str:
  return (
      "## Plan disk file\n\n"
      "Live path: .cursor/plans/test.plan.md\n"
      "## Operator discovery\n\n**Status:** `not needed`\n"
      "## Problem and facts\n\nfacts\n"
      "## Approach\n\nsteps\n"
      "## Testing\n\ntests\n"
      "## Implementation\n\nfiles\n"
      "## Cursor rules / docs sync\n\nno rule change\n"
      "## Final code review (mandatory before implementation close)\n\nreview\n"
      "## Post-implementation review (required before close)\n\nreview\n"
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
  assert any("Plan disk file" in item for item in issues)
  assert any("Operator discovery" in item for item in issues)
  assert any("Problem and facts" in item for item in issues)
  assert any("post-implementation-review todo" in item for item in issues)


def test_plan_content_issues_accepts_minimal_plan():
  assert lib.plan_content_issues(_minimal_plan_markdown()) == []


def test_is_live_plan_disk_path():
  assert lib.is_live_plan_disk_path("/ws/.cursor/plans/foo.plan.md")
  assert not lib.is_live_plan_disk_path("HPCPerfStats/docs/plans/PLAN_TEMPLATE.md")


def test_plan_disk_sync_issues_requires_disk_write_with_create_plan():
  rows = [
      {
          "role": "assistant",
          "message": {
              "content": [
                  {
                      "type": "tool_use",
                      "name": "CreatePlan",
                      "input": {"plan": "plan body"},
                  },
              ],
          },
      },
  ]
  issues = lib.plan_disk_sync_issues(rows)
  assert any("Plan not written to disk" in item for item in issues)


def test_plan_disk_sync_issues_passes_when_disk_write_present():
  rows = [
      {
          "role": "assistant",
          "message": {
              "content": [
                  {
                      "type": "tool_use",
                      "name": "CreatePlan",
                      "input": {"plan": "plan body"},
                  },
                  {
                      "type": "tool_use",
                      "name": "Write",
                      "input": {
                          "path": ".cursor/plans/foo.plan.md",
                          "contents": _minimal_plan_markdown(),
                      },
                  },
              ],
          },
      },
  ]
  assert lib.plan_disk_sync_issues(rows) == []


def test_suggested_live_plan_disk_path_from_name_field():
  path = lib.suggested_live_plan_disk_path(
      {"name": "startup stall follow-up"},
  )
  assert path == ".cursor/plans/startup-stall-follow-up.plan.md"


def test_check_close_gate_blocks_create_plan_without_disk_even_without_plan_ready(
    tmp_path,
):
  transcript = tmp_path / "plan-silent.jsonl"
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
                          "input": {
                              "name": "startup-stall-followup",
                              "plan": "## Approach\n\nonly",
                          },
                      },
                      {
                          "type": "text",
                          "text": "See the plan in the UI.",
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
  assert "Plan disk sync incomplete" in data["followup_message"]
  assert ".cursor/plans/startup-stall-followup.plan.md" in data["followup_message"]


def test_check_create_plan_disk_sync_injects_same_turn_write(tmp_path):
  payload = {
      "tool_name": "CreatePlan",
      "tool_input": {
          "name": "foo-bar",
          "plan": "---\nname: foo-bar\n---\n\n## Approach\n",
      },
      "transcript_path": str(tmp_path / "empty.jsonl"),
  }
  (tmp_path / "empty.jsonl").write_text("", encoding="utf-8")
  script = HOOKS_DIR / "check-create-plan-disk-sync.py"
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
  assert "PLAN DISK SYNC" in data["additional_context"]
  assert ".cursor/plans/foo-bar.plan.md" in data["additional_context"]


def test_paths_from_plan_markdown_extracts_backtick_paths():
  text = (
      "Touch [`hpcperfstats/dbload/lib/sync_timedb_day_close_manifest.py`](path)"
  )
  paths = lib.paths_from_plan_markdown(text)
  assert "hpcperfstats/dbload/lib/sync_timedb_day_close_manifest.py" in paths


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
                  {
                      "type": "tool_use",
                      "name": "Write",
                      "input": {
                          "path": ".cursor/plans/test.plan.md",
                          "contents": plan_md,
                      },
                  },
                  {"type": "text", "text": assistant_text},
              ],
          },
      },
  ]
  issues = lib.close_gate_issues(assistant_text=assistant_text, transcript_rows=rows)
  assert "PLAN_TEMPLATE.md not read via Read tool" in issues
  assert "Rule not read: plan-creation-contract.mdc" in issues
  assert "Rule not read: plan-live-disk-sync.mdc" in issues
  assert not any("Plan disk content missing" in item for item in issues)


def test_plan_authority_content_issues_validates_disk_not_create_plan(tmp_path):
  workspace = tmp_path / "workspace"
  plan_path = workspace / ".cursor" / "plans" / "gap-test.plan.md"
  plan_path.parent.mkdir(parents=True)
  plan_path.write_text("## Approach\n\nonly approach\n", encoding="utf-8")
  complete_create_plan = _minimal_plan_markdown()
  rows = [
      {
          "role": "assistant",
          "message": {
              "content": [
                  {
                      "type": "tool_use",
                      "name": "CreatePlan",
                      "input": {"plan": complete_create_plan},
                  },
                  {
                      "type": "tool_use",
                      "name": "Write",
                      "input": {
                          "path": str(plan_path),
                          "contents": "## Approach\n\nonly approach\n",
                      },
                  },
              ],
          },
      },
  ]
  issues = lib.plan_authority_content_issues(
      rows,
      workspace_roots=[str(workspace)],
  )
  assert any("Plan disk file" in item for item in issues)
  assert any("Operator discovery" in item for item in issues)
  assert any("Post-implementation review" in item for item in issues)


def test_plan_authority_content_issues_passes_complete_disk_write():
  plan_md = _minimal_plan_markdown()
  rows = [
      {
          "role": "assistant",
          "message": {
              "content": [
                  {
                      "type": "tool_use",
                      "name": "CreatePlan",
                      "input": {"plan": "## Approach\n\nstub"},
                  },
                  {
                      "type": "tool_use",
                      "name": "Write",
                      "input": {
                          "path": ".cursor/plans/test.plan.md",
                          "contents": plan_md,
                      },
                  },
              ],
          },
      },
  ]
  assert lib.plan_authority_content_issues(rows) == []


def test_extract_plan_authority_markdown_prefers_filesystem(tmp_path):
  workspace = tmp_path / "workspace"
  plan_path = workspace / ".cursor" / "plans" / "authority.plan.md"
  plan_path.parent.mkdir(parents=True)
  on_disk = "## Plan disk file\n\nfrom filesystem\n"
  plan_path.write_text(on_disk, encoding="utf-8")
  rows = [
      {
          "role": "assistant",
          "message": {
              "content": [
                  {
                      "type": "tool_use",
                      "name": "Write",
                      "input": {
                          "path": str(plan_path),
                          "contents": "## Approach\n\ntranscript only\n",
                      },
                  },
              ],
          },
      },
  ]
  assert "from filesystem" in lib.extract_plan_authority_markdown(
      rows,
      workspace_roots=[str(workspace)],
  )


def _operator_in_progress_plan_markdown() -> str:
  return _minimal_plan_markdown().replace(
      "## Operator discovery\n\n**Status:** `not needed`\n",
      (
          "## Operator discovery\n\n"
          "**Status:** `in progress`\n\n"
          "### Completed findings\n\n"
          "| # | Service | Asked for | Found | Date |\n"
          "|---|---------|-----------|-------|------|\n\n"
          "### Pending commands\n\n"
          "#### pipeline — paste manifest snapshot\n\n"
          "```bash\n"
          "docker compose exec pipeline su hpcperfstats -c 'python3 -c \""
          "from hpcperfstats.dbload.lib import conf_parser as cfg; "
          "print(cfg.get_archive_dir_path())\"'\n"
          "```\n"
      ),
  )


def test_operator_discovery_issues_requires_pending_shape_when_in_progress():
  bad = _minimal_plan_markdown().replace(
      "## Operator discovery\n\n**Status:** `not needed`\n",
      "## Operator discovery\n\n**Status:** `in progress`\n",
  )
  issues = lib.operator_discovery_issues(bad)
  assert any("Pending commands" in item for item in issues)
  assert any("Completed findings" in item for item in issues)


def test_operator_discovery_issues_accepts_valid_pending_commands():
  assert lib.operator_discovery_issues(_operator_in_progress_plan_markdown()) == []


def test_operator_discovery_accepts_compose_flags_before_subcommand():
  plan = _minimal_plan_markdown().replace(
      "## Operator discovery\n\n**Status:** `not needed`\n",
      (
          "## Operator discovery\n\n"
          "**Status:** `in progress`\n\n"
          "### Completed findings\n\n"
          "| # | Service | Asked for | Found | Date |\n"
          "|---|---------|-----------|-------|------|\n\n"
          "### Pending commands\n\n"
          "#### pipeline — filtered recover logs\n\n"
          "```bash\n"
          "docker compose -p hpcperfstats -f docker-compose.yaml logs pipeline 2>&1 | "
          "grep -E 'pool_recover' | tail -40\n"
          "```\n"
      ),
  )
  assert lib.operator_discovery_issues(plan) == []


def test_operator_discovery_rejects_multi_pipeline_blocks():
  plan = _minimal_plan_markdown().replace(
      "## Operator discovery\n\n**Status:** `not needed`\n",
      (
          "## Operator discovery\n\n"
          "**Status:** `in progress`\n\n"
          "### Completed findings\n\n"
          "| # | Service | Asked for | Found | Date |\n"
          "|---|---------|-----------|-------|------|\n\n"
          "### Pending commands\n\n"
          "#### pipeline — knobs\n\n"
          "```bash\n"
          "docker compose exec pipeline su hpcperfstats -c 'echo knobs'\n"
          "```\n\n"
          "#### pipeline — logs\n\n"
          "```bash\n"
          "docker compose logs pipeline 2>&1 | grep -E 'stall' | tail -20\n"
          "```\n"
      ),
  )
  issues = lib.operator_discovery_issues(plan)
  assert any("appears 2 times" in item for item in issues)


def test_operator_discovery_rejects_host_cd_before_compose():
  plan = _minimal_plan_markdown().replace(
      "## Operator discovery\n\n**Status:** `not needed`\n",
      (
          "## Operator discovery\n\n"
          "**Status:** `in progress`\n\n"
          "### Completed findings\n\n"
          "| # | Service | Asked for | Found | Date |\n"
          "|---|---------|-----------|-------|------|\n\n"
          "### Pending commands\n\n"
          "#### pipeline — bad host cd\n\n"
          "```bash\n"
          "cd HPCPerfStats\n"
          "docker compose logs pipeline 2>&1 | grep -E 'stall' | tail -20\n"
          "```\n"
      ),
  )
  issues = lib.operator_discovery_issues(plan)
  assert any("host cd" in item for item in issues)


def test_operator_discovery_rejects_tail_before_grep_on_logs():
  plan = _minimal_plan_markdown().replace(
      "## Operator discovery\n\n**Status:** `not needed`\n",
      (
          "## Operator discovery\n\n"
          "**Status:** `in progress`\n\n"
          "### Completed findings\n\n"
          "| # | Service | Asked for | Found | Date |\n"
          "|---|---------|-----------|-------|------|\n\n"
          "### Pending commands\n\n"
          "#### pipeline — bad --tail\n\n"
          "```bash\n"
          "docker compose logs pipeline --tail=500 2>&1 | grep -E 'stall'\n"
          "```\n"
      ),
  )
  issues = lib.operator_discovery_issues(plan)
  assert any("--tail/--since" in item for item in issues)


def test_operator_discovery_rejects_unfiltered_compose_logs():
  plan = _minimal_plan_markdown().replace(
      "## Operator discovery\n\n**Status:** `not needed`\n",
      (
          "## Operator discovery\n\n"
          "**Status:** `in progress`\n\n"
          "### Completed findings\n\n"
          "| # | Service | Asked for | Found | Date |\n"
          "|---|---------|-----------|-------|------|\n\n"
          "### Pending commands\n\n"
          "#### pipeline — firehose\n\n"
          "```bash\n"
          "docker compose logs pipeline\n"
          "```\n"
      ),
  )
  issues = lib.operator_discovery_issues(plan)
  assert any("unfiltered firehose" in item for item in issues)


def test_operator_discovery_rejects_heredoc_python_through_exec():
  plan = _minimal_plan_markdown().replace(
      "## Operator discovery\n\n**Status:** `not needed`\n",
      (
          "## Operator discovery\n\n"
          "**Status:** `in progress`\n\n"
          "### Completed findings\n\n"
          "| # | Service | Asked for | Found | Date |\n"
          "|---|---------|-----------|-------|------|\n\n"
          "### Pending commands\n\n"
          "#### pipeline — heredoc anti-pattern\n\n"
          "```bash\n"
          "docker compose exec pipeline su hpcperfstats -c 'python3 - <<EOF\n"
          "print(1)\n"
          "EOF'\n"
          "```\n"
      ),
  )
  issues = lib.operator_discovery_issues(plan)
  assert any("heredoc" in item for item in issues)


def test_operator_discovery_rejects_hardcoded_hpcperfstats_without_conf_parser():
  plan = _minimal_plan_markdown().replace(
      "## Operator discovery\n\n**Status:** `not needed`\n",
      (
          "## Operator discovery\n\n"
          "**Status:** `in progress`\n\n"
          "### Completed findings\n\n"
          "| # | Service | Asked for | Found | Date |\n"
          "|---|---------|-----------|-------|------|\n\n"
          "### Pending commands\n\n"
          "#### pipeline — hardcoded archive path\n\n"
          "```bash\n"
          "docker compose exec pipeline su hpcperfstats -c "
          "'ls /hpcperfstats/archive/i615-104/1780790218'\n"
          "```\n"
      ),
  )
  issues = lib.operator_discovery_issues(plan)
  assert any("hardcode" in item for item in issues)


def test_operator_discovery_rejects_raw_configparser_archive_dir():
  plan = _minimal_plan_markdown().replace(
      "## Operator discovery\n\n**Status:** `not needed`\n",
      (
          "## Operator discovery\n\n"
          "**Status:** `in progress`\n\n"
          "### Completed findings\n\n"
          "| # | Service | Asked for | Found | Date |\n"
          "|---|---------|-----------|-------|------|\n\n"
          "### Pending commands\n\n"
          "#### pipeline — raw ConfigParser\n\n"
          "```bash\n"
          "docker compose exec pipeline su hpcperfstats -c 'python3 -c \""
          "from configparser import ConfigParser; c=ConfigParser(); "
          "c.read('/home/hpcperfstats/hpcperfstats.ini'); "
          "print(c['PIPELINE'].get('archive_dir'))\"'\n"
          "```\n"
      ),
  )
  issues = lib.operator_discovery_issues(plan)
  assert any("conf_parser" in item for item in issues)


def test_operator_discovery_accepts_conf_parser_archive_dir():
  plan = _minimal_plan_markdown().replace(
      "## Operator discovery\n\n**Status:** `not needed`\n",
      (
          "## Operator discovery\n\n"
          "**Status:** `in progress`\n\n"
          "### Completed findings\n\n"
          "| # | Service | Asked for | Found | Date |\n"
          "|---|---------|-----------|-------|------|\n\n"
          "### Pending commands\n\n"
          "#### pipeline — conf_parser archive\n\n"
          "```bash\n"
          "docker compose exec pipeline su hpcperfstats -c 'python3 -c \""
          "from hpcperfstats.dbload.lib import conf_parser as cfg; "
          "import os; "
          "p=os.path.join(cfg.get_archive_dir_path(), 'host', '1'); "
          "print(p)\"'\n"
          "```\n"
      ),
  )
  assert lib.operator_discovery_issues(plan) == []


def test_reconstruct_live_plan_markdown_from_write():
  md = lib.reconstruct_live_plan_markdown_from_tool_input(
      "Write",
      {"contents": "# plan\n"},
  )
  assert md == "# plan\n"


def test_reconstruct_live_plan_markdown_from_str_replace(tmp_path):
  path = tmp_path / "x.plan.md"
  path.write_text("hello OLD world\n", encoding="utf-8")
  md = lib.reconstruct_live_plan_markdown_from_tool_input(
      "StrReplace",
      {"path": str(path), "old_string": "OLD", "new_string": "NEW"},
  )
  assert md == "hello NEW world\n"


def test_check_live_plan_operator_discovery_denies_hardcoded_path(tmp_path):
  plans = tmp_path / ".cursor" / "plans"
  plans.mkdir(parents=True)
  plan_path = plans / "bad.plan.md"
  bad = _minimal_plan_markdown().replace(
      "## Operator discovery\n\n**Status:** `not needed`\n",
      (
          "## Operator discovery\n\n"
          "**Status:** `in progress`\n\n"
          "### Completed findings\n\n"
          "| # | Service | Asked for | Found | Date |\n"
          "|---|---------|-----------|-------|------|\n\n"
          "### Pending commands\n\n"
          "#### pipeline — hardcoded\n\n"
          "```bash\n"
          "docker compose exec pipeline su hpcperfstats -c "
          "'ls /hpcperfstats/archive'\n"
          "```\n"
      ),
  )
  payload = {
      "tool_name": "Write",
      "tool_input": {"path": str(plan_path), "contents": bad},
  }
  script = HOOKS_DIR / "check-live-plan-operator-discovery.py"
  proc = subprocess.run(
      [sys.executable, str(script)],
      input=json.dumps(payload),
      capture_output=True,
      text=True,
      check=False,
  )
  assert proc.returncode == 0
  data = json.loads(proc.stdout.strip())
  assert data.get("permission") == "deny"
  assert "OPERATOR DISCOVERY DENY" in data.get("user_message", "")


def test_check_live_plan_operator_discovery_allows_valid_pending(tmp_path):
  plans = tmp_path / ".cursor" / "plans"
  plans.mkdir(parents=True)
  plan_path = plans / "ok.plan.md"
  payload = {
      "tool_name": "Write",
      "tool_input": {
          "path": str(plan_path),
          "contents": _operator_in_progress_plan_markdown(),
      },
  }
  script = HOOKS_DIR / "check-live-plan-operator-discovery.py"
  proc = subprocess.run(
      [sys.executable, str(script)],
      input=json.dumps(payload),
      capture_output=True,
      text=True,
      check=False,
  )
  assert proc.returncode == 0
  data = json.loads(proc.stdout.strip())
  assert data.get("permission") == "allow"



def test_operator_discovery_issues_flags_compose_blocks_outside_section():
  plan = _minimal_plan_markdown() + (
      "\n## Approach\n\n"
      "```bash\n"
      "docker compose exec pipeline bash -lc 'echo hi'\n"
      "```\n"
  )
  issues = lib.operator_discovery_issues(plan)
  assert any("Operator discovery" in item or "Operator commands" in item for item in issues)


def test_turn_create_plan_pending_disk_write():
  rows = [
      {
          "role": "assistant",
          "message": {
              "content": [
                  {
                      "type": "tool_use",
                      "name": "CreatePlan",
                      "input": {"plan": "x"},
                  },
                  {
                      "type": "tool_use",
                      "name": "Shell",
                      "input": {"command": "echo hi"},
                  },
              ],
          },
      },
  ]
  assert lib.turn_create_plan_pending_disk_write(rows) is True
  rows[0]["message"]["content"].append(
      {
          "type": "tool_use",
          "name": "Write",
          "input": {
              "path": ".cursor/plans/foo.plan.md",
              "contents": _minimal_plan_markdown(),
          },
      },
  )
  assert lib.turn_create_plan_pending_disk_write(rows) is False


def test_plan_authoring_precreate_read_issues_requires_reads():
  rows = [
      {
          "role": "assistant",
          "message": {
              "content": [
                  {
                      "type": "tool_use",
                      "name": "CreatePlan",
                      "input": {"plan": "x"},
                  },
              ],
          },
      },
  ]
  issues = lib.plan_authoring_precreate_read_issues(rows)
  assert any("compose-operator-terminal-commands" in item for item in issues)
  assert any("PLAN_TEMPLATE" in item for item in issues)


def test_check_pre_create_plan_reads_denies_without_reads(tmp_path):
  transcript = tmp_path / "pre-create.jsonl"
  transcript.write_text(
      json.dumps(
          {
              "role": "assistant",
              "message": {
                  "content": [
                      {
                          "type": "tool_use",
                          "name": "Read",
                          "input": {"path": "unrelated.txt"},
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
      "tool_input": {"name": "foo"},
      "transcript_path": str(transcript),
  }
  script = HOOKS_DIR / "check-pre-create-plan-reads.py"
  proc = subprocess.run(
      [sys.executable, str(script)],
      input=json.dumps(payload),
      capture_output=True,
      text=True,
      check=False,
  )
  assert proc.returncode == 0
  data = json.loads(proc.stdout.strip())
  assert data.get("permission") == "deny"
  assert "compose-operator-terminal-commands" in data.get("agent_message", "")


def test_check_block_until_plan_disk_denies_shell_after_create_plan(tmp_path):
  transcript = tmp_path / "block-disk.jsonl"
  transcript.write_text(
      json.dumps(
          {
              "role": "assistant",
              "message": {
                  "content": [
                      {
                          "type": "tool_use",
                          "name": "CreatePlan",
                          "input": {"name": "foo-bar", "plan": "x"},
                      },
                  ],
              },
          },
      )
      + "\n",
      encoding="utf-8",
  )
  payload = {
      "tool_name": "Shell",
      "tool_input": {"command": "echo hi"},
      "transcript_path": str(transcript),
  }
  script = HOOKS_DIR / "check-block-until-plan-disk.py"
  proc = subprocess.run(
      [sys.executable, str(script)],
      input=json.dumps(payload),
      capture_output=True,
      text=True,
      check=False,
  )
  assert proc.returncode == 0
  data = json.loads(proc.stdout.strip())
  assert data.get("permission") == "deny"
  assert ".cursor/plans/foo-bar.plan.md" in data.get("agent_message", "")


def test_check_block_until_plan_disk_allows_plan_write_after_create_plan(tmp_path):
  transcript = tmp_path / "allow-disk.jsonl"
  transcript.write_text(
      json.dumps(
          {
              "role": "assistant",
              "message": {
                  "content": [
                      {
                          "type": "tool_use",
                          "name": "CreatePlan",
                          "input": {"name": "foo-bar", "plan": "x"},
                      },
                  ],
              },
          },
      )
      + "\n",
      encoding="utf-8",
  )
  payload = {
      "tool_name": "Write",
      "tool_input": {
          "path": ".cursor/plans/foo-bar.plan.md",
          "contents": _minimal_plan_markdown(),
      },
      "transcript_path": str(transcript),
  }
  script = HOOKS_DIR / "check-block-until-plan-disk.py"
  proc = subprocess.run(
      [sys.executable, str(script)],
      input=json.dumps(payload),
      capture_output=True,
      text=True,
      check=False,
  )
  assert proc.returncode == 0
  data = json.loads(proc.stdout.strip())
  assert data.get("permission") == "allow"


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
  assert "Plan disk sync incomplete" in data["followup_message"]
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
                                  "`hpcperfstats/dbload/lib/sync_timedb_day_close_manifest.py`\n"
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
              "`hpcperfstats/dbload/lib/sync_timedb_day_close_manifest.py`\n"
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
  assert "plan-live-disk-sync.mdc" in data["additional_context"]
  assert "compose-operator-terminal-commands.mdc" in data["additional_context"]
  assert "deploy-ini-with-code-no-phase-zero.mdc" in data["additional_context"]
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


def test_domain_rule_read_issues_skips_deleted_rule():
  issues = lib.domain_rule_read_issues(
      ["sync-timedb-startup-tar-seal-contract.mdc"],
      [],
  )
  assert issues == []


def test_rule_dual_registration_skips_deleted_rule():
  deleted_path = (
      "/repo/HPCPerfStats/hpcperfstats/cursor-rules/"
      "sync-timedb-startup-tar-seal-contract.mdc"
  )
  issues = lib.rule_dual_registration_issues([deleted_path])
  assert issues == []


def test_triggered_rules_for_monitor_src_path():
  rules = triggered_rules_for_paths(["HPCPerfStats/monitor/src/stats_set.c"])
  assert "monitor-c-conventions.mdc" in rules
  assert "monitor-workspace-contract.mdc" in rules


def test_triggered_rules_for_monitor_cursor_rules_path():
  rules = triggered_rules_for_paths(
      ["HPCPerfStats/monitor/cursor-rules/plan-completion-gate.mdc"],
  )
  assert "agent-discipline-core.mdc" in rules
  assert "implementation-review-workflow.mdc" in rules


def test_resolve_cursor_rule_path_finds_monitor_rules():
  resolved = lib.resolve_cursor_rule_path("monitor-c-conventions.mdc")
  assert resolved is not None
  assert resolved.name == "monitor-c-conventions.mdc"
  assert "monitor/cursor-rules" in str(resolved).replace("\\", "/")


def test_is_plan_template_read_path_accepts_monitor_template():
  assert lib.is_plan_template_read_path(MONITOR_PLAN_TEMPLATE) is True
  assert lib.is_plan_template_read_path(
      "HPCPerfStats/docs/plans/PLAN_TEMPLATE.md",
  ) is True


def test_detect_rules_profile_monitor_symlink(tmp_path):
  cursor_dir = tmp_path / ".cursor"
  cursor_dir.mkdir()
  monitor_rules = tmp_path / "HPCPerfStats" / "monitor" / "cursor-rules"
  monitor_rules.mkdir(parents=True)
  (monitor_rules / "agent-discipline-core.mdc").write_text("---\n---\n", encoding="utf-8")
  (cursor_dir / "rules").symlink_to(monitor_rules)
  assert detect_rules_profile([str(tmp_path)]) == "monitor"
  assert profile_rules_dir_label("monitor") == "HPCPerfStats/monitor/cursor-rules"


def test_detect_rules_profile_hpcperfstats_symlink(tmp_path):
  cursor_dir = tmp_path / ".cursor"
  cursor_dir.mkdir()
  hps_rules = tmp_path / "HPCPerfStats" / "hpcperfstats" / "cursor-rules"
  hps_rules.mkdir(parents=True)
  (hps_rules / "agent-discipline-core.mdc").write_text("---\n---\n", encoding="utf-8")
  (cursor_dir / "rules").symlink_to(hps_rules)
  assert detect_rules_profile([str(tmp_path)]) == "hpcperfstats"


def test_monitor_router_entries_reference_existing_files():
  monitor_rules_dir = Path(__file__).resolve().parents[2] / "monitor" / "cursor-rules"
  for entry in MONITOR_ROUTER_ENTRIES:
    for rule in entry["rules"]:
      if rule == "out-of-monitor-hpcperfstats-rules.mdc":
        continue
      path = monitor_rules_dir / rule
      assert path.is_file(), f"missing monitor rule file: {rule} (entry {entry['id']})"
