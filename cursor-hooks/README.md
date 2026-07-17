# Cursor hooks (HPCPerfStats + monitor workspace)

Project hooks for Cursor Agent. Cursor loads **`<workspace_root>/.cursor/hooks.json`**, which **symlinks** to the checked-in **`HPCPerfStats/cursor-hooks/hooks.json`**.

## Workspace `.cursor/` layout

From **workspace root** (contains `.venv/` and `HPCPerfStats/`):

```text
<workspace_root>/.cursor/
  rules/       → symlink → HPCPerfStats/hpcperfstats/cursor-rules   (full-stack)
            or → symlink → HPCPerfStats/monitor/cursor-rules       (monitor-focused)
  hooks/       → symlink → HPCPerfStats/cursor-hooks
  hooks.json   → symlink → HPCPerfStats/cursor-hooks/hooks.json
  plans/       → real directory; live *.plan.md (outside git)
```

**One-time setup** (full-stack workspace):

```bash
cd "<workspace_root>"
mkdir -p .cursor/plans
ln -sf ../HPCPerfStats/hpcperfstats/cursor-rules .cursor/rules
ln -sf ../HPCPerfStats/cursor-hooks .cursor/hooks
ln -sf ../HPCPerfStats/cursor-hooks/hooks.json .cursor/hooks.json
```

Monitor-focused workspace: symlink `.cursor/rules` to `HPCPerfStats/monitor/cursor-rules` instead.

There is **no** `HPCPerfStats/.cursor/` directory — hooks and rules live in checked-in git paths; only workspace `.cursor/` holds symlinks and local Cursor metadata (`plans/`).

## Profile detection

Hooks auto-detect workspace profile from the **`.cursor/rules` symlink target**:

| Symlink target | Profile | Rules dir label |
|----------------|---------|-----------------|
| `…/monitor/cursor-rules` | `monitor` | `HPCPerfStats/monitor/cursor-rules` |
| `…/hpcperfstats/cursor-rules` | `hpcperfstats` | `hpcperfstats/cursor-rules` |

`hook_task_router.py` merges **`MONITOR_ROUTER_ENTRIES`** and **`HPCPERFSTATS_ROUTER_ENTRIES`** so authorized cross-edits can trigger rules from both trees.

Plan template paths accepted for Read verification:

- `HPCPerfStats/monitor/docs/plans/PLAN_TEMPLATE.md` (monitor workspace)
- `HPCPerfStats/docs/plans/PLAN_TEMPLATE.md` (full-stack workspace)

## Hooks

| Event | Script | Behavior |
|-------|--------|----------|
| `preToolUse` | `record-rule-reads.py` | On **`Read`/`ReadFile`**, append cursor-rule / `PLAN_TEMPLATE.md` reads to a per-transcript ledger (`<transcript>.hpc_rule_reads.jsonl`) keyed by the turn's user-row count; **always `allow`**. Works around Cursor persisting a turn's tool calls to `transcript_path` only after the turn ends, which otherwise made the plan-write read-gates blind to this turn's Reads |
| `preToolUse` | `check-pre-create-plan-reads.py` | **`deny`** **`CreatePlan`** and **`Write`/`StrReplace`** to `.cursor/plans/*.plan.md` until `plan-creation-contract.mdc`, `plan-live-disk-sync.mdc`, `plan-template-enforcement.mdc`, `compose-operator-terminal-commands.mdc`, `deploy-ini-with-code-no-phase-zero.mdc`, and `PLAN_TEMPLATE.md` are Read this turn (transcript reads **unioned** with the current-turn ledger) |
| `preToolUse` | `check-block-until-plan-disk.py` | After **`CreatePlan`** without a same-turn disk write, **`deny`** all tools except **Read** and **Write/StrReplace** to `.cursor/plans/*.plan.md` (any same-turn live plan write before or after CreatePlan clears pending) |
| `preToolUse` | `check-live-plan-operator-discovery.py` | **`deny`** **Write/StrReplace** to `.cursor/plans/*.plan.md` when reconstructed markdown fails `operator_discovery_issues`, or when Operator discovery needs commands and `compose-operator-terminal-commands.mdc` / `operator-command-lessons-learned.mdc` were not Read **full-file** (no `limit`/`offset`) this turn (full-file transcript reads **unioned** with the current-turn ledger) |
| `stop` | `check-close-gate.py` | After **file edits**, **`CreatePlan`**, or **any live `.cursor/plans` Write/StrReplace**, require close headings (Agent rule dispatch, Final code review, Post-implementation review) and Read-before-edit proof — **always** on plan turns (no soft-phrasing escape). Also: **rule dual-registration** when `cursor-rules/*.mdc` was edited; **PLAN_TEMPLATE** + **operator-discovery** shape on the live disk file when CreatePlan was used; **Write/StrReplace to `.cursor/plans/*.plan.md`** when CreatePlan was used without disk (`plan-live-disk-sync.mdc`) |
| `postToolUse` | `check-create-plan-disk-sync.py` | After **`CreatePlan`**, inject mandatory same-turn **Write** to **`.cursor/plans/*.plan.md`** (`plan-live-disk-sync.mdc`) |
| `postToolUse` | `check-edit-triggered-rules.py` | After `Write`/`StrReplace`/`EditNotebook`/`CreatePlan`, inject context if triggered domain rules or plan-authoring reads are missing **this turn** (`last_turn_rows` only — prior-turn edits do not poison read-before-edit); flag PLAN_TEMPLATE / other plan-authority gaps on live plan disk writes |

**Operator discovery validation is content-level and blocked at write time** (not Read-only / soft warn): `hpc_hook_lib.operator_discovery_issues` / `_validate_pending_commands_subsection` reject multi `#### <same-service>` blocks, host `cd` before `docker compose`, `--tail`/`--since` on `compose logs` before `grep`, unfiltered `compose logs` firehose, heredoc `python3 - <<` through `exec`, hardcoded `/hpcperfstats/` (or `/opt/hpcperfstats_data/`) without `conf_parser` getters, and raw `ConfigParser` for `archive_dir`. When discovery needs commands, **`full_file_rule_read_issues`** also requires a full-file Read (no `limit`/`offset`) of `compose-operator-terminal-commands.mdc` and `operator-command-lessons-learned.mdc` — a `limit: N` Read does **not** count. See unit tests in `hpcperfstats/tests/test_cursor_hooks.py`.
| `postToolUse` | `check-new-rule-router.py` | After `Write`/`StrReplace` on `cursor-rules/*.mdc`, inject context if `agent-discipline-core.mdc` or `hook_task_router.py` omits the new rule |

## Verify

1. Cursor **Settings → Hooks** tab should list both hooks after save/reload.
2. Hooks output channel shows stdin/stdout when hooks fire.
3. Unit tests: `cd HPCPerfStats && ../.venv/bin/python3 -m pytest hpcperfstats/tests/test_cursor_hooks.py -q`
4. Manual smoke test:

```bash
echo '{"status":"completed","loop_count":0,"transcript_path":"/path/to/transcript.jsonl","workspace_roots":["/path/to/workspace"]}' | \
  python3 HPCPerfStats/cursor-hooks/check-close-gate.py
```

## Requirements

- `python3` on `PATH`
- Trusted workspace (project hooks do not run in untrusted workspaces)

## Dual registration

New domain rules must appear in **both**:

1. Profile **`agent-discipline-core.mdc`** task router table
2. **`hook_task_router.py`** — `MONITOR_ROUTER_ENTRIES` or `HPCPERFSTATS_ROUTER_ENTRIES`

See monitor **`RULES_README.md`** or hpcperfstats **`RULES_README.md`** for always-on caps.

## hooks.json

Edit **`HPCPerfStats/cursor-hooks/hooks.json`** only (checked in). With **`.cursor/hooks.json`** symlinked there, Cursor picks up changes immediately — no copy step. If a checkout still has a stale real file at `.cursor/hooks.json`, replace it:

```bash
ln -sf ../HPCPerfStats/cursor-hooks/hooks.json .cursor/hooks.json
```
