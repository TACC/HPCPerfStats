# Cursor hooks (HPCPerfStats + monitor workspace)

Project hooks for Cursor Agent. Cursor loads `.cursor/hooks.json` from the **workspace root**.

## Symlink setup

### Monitor-focused workspace (`.cursor/rules` → `monitor/cursor-rules/`)

From workspace root (contains `.venv/` and `HPCPerfStats/`):

```bash
ln -sf HPCPerfStats/.cursor/hooks.json .cursor/hooks.json
ln -sf HPCPerfStats/.cursor/hooks .cursor/hooks
```

### Full-stack workspace (`.cursor/rules` → `hpcperfstats/cursor-rules/`)

From workspace root:

```bash
ln -sf HPCPerfStats/.cursor/hooks.json .cursor/hooks.json
ln -sf HPCPerfStats/.cursor/hooks .cursor/hooks
```

Both layouts share the **same** hook scripts under `HPCPerfStats/.cursor/hooks/`.

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
  python3 HPCPerfStats/.cursor/hooks/check-close-gate.py
```

## Requirements

- `python3` on `PATH`
- Trusted workspace (project hooks do not run in untrusted workspaces)

## Dual registration

New domain rules must appear in **both**:

1. Profile **`agent-discipline-core.mdc`** task router table
2. **`hook_task_router.py`** — `MONITOR_ROUTER_ENTRIES` or `HPCPERFSTATS_ROUTER_ENTRIES`

See monitor **`RULES_README.md`** or hpcperfstats **`RULES_README.md`** for always-on caps.
