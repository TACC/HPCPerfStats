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
| `stop` | `check-close-gate.py` | After **file edits** or **`CreatePlan`**, if the assistant closes without required headings (including **Agent rule dispatch** with listed `*.mdc` or N/A), **rule dual-registration** when `cursor-rules/*.mdc` was edited, plan body sections (when `CreatePlan` was used), or **Read** proof for listed rules / `PLAN_TEMPLATE.md`, auto-submit `Close gate incomplete` follow-up (max 3 loops) |
| `postToolUse` | `check-edit-triggered-rules.py` | After `Write`/`StrReplace`/`EditNotebook`/`CreatePlan`, inject context if triggered domain rules or plan-authoring reads (`plan-creation-contract.mdc`, `PLAN_TEMPLATE.md`) are missing before the first closeable tool call |
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
