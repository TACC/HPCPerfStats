# Cursor hooks (HPCPerfStats + monitor workspace)

Project hooks for Cursor Agent. Cursor loads **`<workspace_root>/.cursor/hooks.json`**. That file must be a **real file** (not a symlink): Cursor refuses project `hooks.json` when it is a symlink below the workspace root (`Refusing to load Project hooks.json via symlink…`). Authoritative source remains **`HPCPerfStats/cursor-hooks/hooks.json`** (checked in); copy it to `.cursor/hooks.json` after edits.

## Workspace `.cursor/` layout

From **workspace root** (contains `.venv/` and `HPCPerfStats/`):

```text
<workspace_root>/.cursor/
  rules/       → symlink → HPCPerfStats/hpcperfstats/cursor-rules   (full-stack)
            or → symlink → HPCPerfStats/monitor/cursor-rules       (monitor-focused)
  hooks/       → symlink → HPCPerfStats/cursor-hooks
  hooks.json   → real file copy of HPCPerfStats/cursor-hooks/hooks.json
  plans/       → real directory; live *.plan.md (outside git)
```

**One-time setup** (full-stack workspace):

```bash
cd "<workspace_root>"
mkdir -p .cursor/plans
ln -sf ../HPCPerfStats/hpcperfstats/cursor-rules .cursor/rules
ln -sf ../HPCPerfStats/cursor-hooks .cursor/hooks
cp HPCPerfStats/cursor-hooks/hooks.json .cursor/hooks.json
```

Monitor-focused workspace: symlink `.cursor/rules` to `HPCPerfStats/monitor/cursor-rules` instead.

There is **no** `HPCPerfStats/.cursor/` directory — hook scripts and rules live in checked-in git paths; workspace `.cursor/` holds the rules/hooks script symlinks, a **copied** `hooks.json`, and local Cursor metadata (`plans/`).

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
| `preToolUse` | `record-rule-reads.py` | **First** hook. On **`Read`/`ReadFile`/`CreatePlan`/`Write`/`StrReplace`**, append turn-activity events to `<transcript>.hpc_turn_activity.jsonl` under **`fcntl.flock`** (kinds: `read`, `create_plan`, `live_plan_write`; keyed by user-row count). Always **`allow`**. Works around Cursor persisting tool calls to `transcript_path` only after the turn ends, and prevents parallel-Read lost updates on the old unlocked ledger |
| `preToolUse` | `check-pre-create-plan-reads.py` | **`deny`** **`CreatePlan`** and **`Write`/`StrReplace`** to `.cursor/plans/*.plan.md` until `plan-creation-contract.mdc`, `plan-live-disk-sync.mdc`, `plan-template-enforcement.mdc`, `compose-operator-terminal-commands.mdc`, `deploy-ini-with-code-no-phase-zero.mdc`, and `PLAN_TEMPLATE.md` are Read this turn (transcript reads **unioned** with the current-turn ledger) |
| `preToolUse` | `check-block-until-plan-disk.py` | After **`CreatePlan`** without a same-turn disk write, **`deny`** all tools except **Read** and **Write/StrReplace** to `.cursor/plans/*.plan.md`. CreatePlan / live-plan Write facts are transcript **OR** ledger (any same-turn live plan write before or after CreatePlan clears pending). Stale on-disk `.plan.md` from a prior turn does **not** clear pending |
| `preToolUse` | `check-live-plan-operator-discovery.py` | **`deny`** **Write/StrReplace** to `.cursor/plans/*.plan.md` when reconstructed markdown fails `operator_discovery_issues`, or when Operator discovery needs commands and `compose-operator-terminal-commands.mdc` / `operator-command-lessons-learned.mdc` were not Read **full-file** (no `limit`/`offset`) this turn (full-file transcript reads **unioned** with the current-turn ledger) |
| `stop` | `check-close-gate.py` | After **file edits**, **`CreatePlan`**, or **any live `.cursor/plans` Write/StrReplace** (transcript **or** ledger), require close headings (Agent rule dispatch, Final code review, Post-implementation review) and Read-before-edit proof — **always** on plan turns (no soft-phrasing escape). Read proof and disk-sync use the turn-activity ledger when the transcript lags. Also: **rule dual-registration** when `cursor-rules/*.mdc` was edited; **PLAN_TEMPLATE** + **operator-discovery** shape on the live disk file when CreatePlan was used |
| `postToolUse` | `check-create-plan-disk-sync.py` | After **`CreatePlan`**, inject mandatory same-turn **Write** to **`.cursor/plans/*.plan.md`** (`plan-live-disk-sync.mdc`) |
| `postToolUse` | `check-edit-triggered-rules.py` | After `Write`/`StrReplace`/`EditNotebook`/`CreatePlan`, inject context if triggered domain rules or plan-authoring reads are missing **this turn** (`last_turn_rows` **unioned** with ledger — prior-turn edits do not poison read-before-edit); flag PLAN_TEMPLATE / other plan-authority gaps on live plan disk writes |
| `postToolUse` | `check-new-rule-router.py` | After `Write`/`StrReplace` on `cursor-rules/*.mdc`, inject context if `agent-discipline-core.mdc` or `hook_task_router.py` omits the new rule |

**Turn-activity ledger race notes:** parallel `preToolUse` recorders take an exclusive flock before rewriting the sidecar; entries from prior turns (different `user_rows`) are dropped on compact. Legacy `<transcript>.hpc_rule_reads.jsonl` is still **read** for compatibility. Do **not** treat “`.plan.md` exists on disk” alone as same-turn Write proof.

**Operator discovery validation is content-level and blocked at write time** (not Read-only / soft warn): `hpc_hook_lib.operator_discovery_issues` / `_validate_pending_commands_subsection` reject multi `#### <same-service>` blocks, host `cd` before `docker compose`, `--tail`/`--since` on `compose logs` before `grep`, unfiltered `compose logs` firehose, heredoc `python3 - <<` through `exec`, hardcoded `/hpcperfstats/` (or `/opt/hpcperfstats_data/`) without `conf_parser` getters, and raw `ConfigParser` for `archive_dir`. When discovery needs commands, **`full_file_rule_read_issues`** also requires a full-file Read (no `limit`/`offset`) of `compose-operator-terminal-commands.mdc` and `operator-command-lessons-learned.mdc` — a `limit: N` Read does **not** count. See unit tests in `hpcperfstats/tests/test_cursor_hooks.py`.

## Verify

1. Cursor **Settings → Hooks** tab should list both hooks after save/reload.
2. Hooks output channel shows stdin/stdout when hooks fire.
3. Unit tests: `cd HPCPerfStats && ../.venv/bin/python3 -m pytest -p no:django hpcperfstats/tests/test_cursor_hooks.py -q`
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

Edit **`HPCPerfStats/cursor-hooks/hooks.json`** (checked in), then **re-copy** to the workspace root so Cursor reloads:

```bash
cp HPCPerfStats/cursor-hooks/hooks.json .cursor/hooks.json
```

Do **not** `ln -sf` `.cursor/hooks.json` — Cursor will not load a symlinked project hooks config. After copy, confirm **Settings → Hooks** and the Hooks output channel no longer show the symlink refuse / “No project hooks configuration found” errors.
