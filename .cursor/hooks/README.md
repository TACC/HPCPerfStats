# Cursor hooks (HPCPerfStats)

Project hooks for Cursor Agent. Cursor loads `.cursor/hooks.json` from the **workspace root**.

This workspace opens at the parent folder (contains `.venv/`). Symlinks at `<workspace>/.cursor/` point here:

```bash
# From workspace root (one-time, if symlinks are missing)
ln -sf ../HPCPerfStats/.cursor/hooks.json .cursor/hooks.json
ln -sf ../HPCPerfStats/.cursor/hooks .cursor/hooks
```

## Hooks

| Event | Script | Behavior |
|-------|--------|----------|
| `stop` | `check-close-gate.py` | After code edits, if the assistant closes without required headings (including **Agent rule dispatch** with listed `*.mdc` or N/A), or lists domain rules without a matching **Read** tool call on that `.mdc` in the current turn, auto-submit `Close gate incomplete` / `Rule not read: …` follow-up (max 3 loops) |
| `postToolUse` | `check-edit-triggered-rules.py` | After `Write`/`StrReplace`/`EditNotebook`, inject context if the edited path triggers domain rules not yet Read (before first edit) |
| `postToolUse` | `check-new-rule-router.py` | After `Write`/`StrReplace` on `cursor-rules/*.mdc`, inject context if `agent-discipline-core.mdc` router omits the new rule |

## Verify

1. Cursor **Settings → Hooks** tab should list both hooks after save/reload.
2. Hooks output channel shows stdin/stdout when hooks fire.
3. Manual smoke test:

```bash
echo '{"status":"completed","loop_count":0,"transcript_path":"/path/to/transcript.jsonl"}' | \
  python3 HPCPerfStats/.cursor/hooks/check-close-gate.py
```

## Requirements

- `python3` on `PATH` (macOS default)
- Trusted workspace (project hooks do not run in untrusted workspaces)
