# Cursor hooks (HPCPerfStats + monitor workspace)

This directory holds the project hooks for Cursor Agent and the Claude Code hook copy; it is for maintainers of the agent tooling.
Cursor loads `<workspace_root>/.cursor/hooks.json`, which must be a real file: Cursor refuses a project `hooks.json` that is a symlink below the workspace root (`Refusing to load Project hooks.json via symlink...`).
The authoritative source is the checked-in `HPCPerfStats/cursor-hooks/hooks.json`; copy it to `.cursor/hooks.json` after edits.

## Workspace `.cursor/` layout

From workspace root (contains `.venv/` and `HPCPerfStats/`):

```text
<workspace_root>/.cursor/
  rules/       -> symlink -> HPCPerfStats/hpcperfstats/cursor-rules   (full-stack)
            or -> symlink -> HPCPerfStats/monitor/cursor-rules       (monitor-focused)
  hooks/       -> symlink -> HPCPerfStats/cursor-hooks
  hooks.json   -> real file copy of HPCPerfStats/cursor-hooks/hooks.json
                 (commands use `.venv/bin/python3 .cursor/hooks/...`)
  plans/       -> real directory; live *.plan.md (outside git)
```

To set up a full-stack workspace once, run from the workspace root:

```bash
cd "<workspace_root>"
mkdir -p .cursor/plans
ln -sf ../HPCPerfStats/hpcperfstats/cursor-rules .cursor/rules
ln -sf ../HPCPerfStats/cursor-hooks .cursor/hooks
cp HPCPerfStats/cursor-hooks/hooks.json .cursor/hooks.json
```

Monitor-focused workspace: symlink `.cursor/rules` to `HPCPerfStats/monitor/cursor-rules` instead.

The checkout has no `HPCPerfStats/.cursor/` directory.
Hook scripts and rules live in checked-in git paths; workspace `.cursor/` holds the rules/hooks script symlinks, a copied `hooks.json`, and local Cursor metadata (`plans/`).

## Profile detection

Hooks auto-detect workspace profile from the `.cursor/rules` symlink target:

| Symlink target                  | Profile        | Rules dir label                     |
| ------------------------------- | -------------- | ----------------------------------- |
| `.../monitor/cursor-rules`      | `monitor`      | `HPCPerfStats/monitor/cursor-rules` |
| `.../hpcperfstats/cursor-rules` | `hpcperfstats` | `hpcperfstats/cursor-rules`         |

`hook_task_router.py` merges `MONITOR_ROUTER_ENTRIES` and `HPCPERFSTATS_ROUTER_ENTRIES` so authorized cross-edits can trigger rules from both trees.

Plan template accepted for Read verification: `HPCPerfStats/docs/plans/PLAN_TEMPLATE.md` (both profiles; monitor plans use its "Appendix: Monitor plans").

## Hooks

| Event         | Script                                  | Behavior                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| ------------- | --------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `preToolUse`  | `record-rule-reads.py`                  | First hook. On `Read`/`ReadFile`/`CreatePlan`/`Write`/`StrReplace`, append turn-activity events to `<transcript>.hpc_turn_activity.jsonl` under `fcntl.flock` (kinds: `read`, `create_plan`, `live_plan_write`; keyed by user-row count). Always `allow`. Works around Cursor persisting tool calls to `transcript_path` only after the turn ends, and prevents parallel-Read lost updates on the old unlocked ledger                                                                                                    |
| `preToolUse`  | `check-pre-create-plan-reads.py`        | `deny` `CreatePlan` and `Write`/`StrReplace` to `.cursor/plans/*.plan.md` until `plan-creation-contract.mdc`, `plan-live-disk-sync.mdc`, `plan-template-enforcement.mdc`, `compose-operator-terminal-commands.mdc`, `deploy-ini-with-code-no-phase-zero.mdc`, and `PLAN_TEMPLATE.md` are Read this turn (transcript reads unioned with the current-turn ledger)                                                                                                                                                          |
| `preToolUse`  | `check-block-until-plan-disk.py`        | After `CreatePlan` without a same-turn disk write, `deny` all tools except Read and Write/StrReplace to `.cursor/plans/*.plan.md`. CreatePlan / live-plan Write facts are transcript OR ledger (any same-turn live plan write before or after CreatePlan clears pending). Stale on-disk `.plan.md` from a prior turn does not clear pending                                                                                                                                                                              |
| `preToolUse`  | `check-live-plan-operator-discovery.py` | `deny` Write/StrReplace to `.cursor/plans/*.plan.md` when reconstructed markdown fails `operator_discovery_issues`, or when Operator discovery needs commands and `compose-operator-terminal-commands.mdc` / `operator-command-lessons-learned.mdc` were not Read full-file (no `limit`/`offset`) this turn (full-file transcript reads unioned with the current-turn ledger)                                                                                                                                            |
| `stop`        | `check-close-gate.py`                   | After file edits, `CreatePlan`, or any live `.cursor/plans` Write/StrReplace (transcript or ledger), require close headings (Agent rule dispatch, Final code review, Post-implementation review) and Read-before-edit proof - always on plan turns (no soft-phrasing escape). Read proof and disk-sync use the turn-activity ledger when the transcript lags. Also: rule dual-registration when `cursor-rules/*.mdc` was edited; PLAN_TEMPLATE + operator-discovery shape on the live disk file when CreatePlan was used |
| `postToolUse` | `check-create-plan-disk-sync.py`        | After `CreatePlan`, inject mandatory same-turn Write to `.cursor/plans/*.plan.md` (`plan-live-disk-sync.mdc`)                                                                                                                                                                                                                                                                                                                                                                                                            |
| `postToolUse` | `check-edit-triggered-rules.py`         | After `Write`/`StrReplace`/`EditNotebook`/`CreatePlan`, inject context if triggered domain rules or plan-authoring reads are missing this turn (`last_turn_rows` unioned with ledger - prior-turn edits do not poison read-before-edit); flag PLAN_TEMPLATE / other plan-authority gaps on live plan disk writes                                                                                                                                                                                                         |
| `postToolUse` | `check-new-rule-router.py`              | After `Write`/`StrReplace` on `cursor-rules/*.mdc`, inject context if `agent-discipline-core.mdc` or `hook_task_router.py` omits the new rule                                                                                                                                                                                                                                                                                                                                                                            |

The turn-activity ledger is race-safe: parallel `preToolUse` recorders take an exclusive flock before rewriting the sidecar, and compaction drops entries from prior turns (different `user_rows`).
The hooks also read the legacy `<transcript>.hpc_rule_reads.jsonl` sidecar.
Do not treat "`.plan.md` exists on disk" alone as same-turn Write proof.

Operator discovery validation checks content and blocks at write time, not at Read time and not as a soft warning.
`hpc_hook_lib.operator_discovery_issues` / `_validate_pending_commands_subsection` recognize `podman-compose` and Docker-compatible Compose forms, and reject multi `#### <same-service>` blocks, host `cd` before Compose, `--tail`/`--since` on `compose logs` before `grep`, unfiltered `compose logs` firehose, heredoc `python3 - <<` through `exec`, hardcoded `/hpcperfstats/` (or `/opt/hpcperfstats_data/`) without `conf_parser` getters, and raw `ConfigParser` for `archive_dir`.
When discovery needs commands, `full_file_rule_read_issues` also requires a full-file Read (no `limit`/`offset`) of `compose-operator-terminal-commands.mdc` and `operator-command-lessons-learned.mdc`; a `limit: N` Read does not count.
See the unit tests in `hpcperfstats/tests/test_cursor_hooks.py`.

## Verify

1. In Cursor, the Settings -> Hooks tab lists every hook in `hooks.json` after save or reload.
2. Hooks output channel shows stdin/stdout when hooks fire.
3. Unit tests: `cd HPCPerfStats && ../.venv/bin/python3 -m pytest -p no:django hpcperfstats/tests/test_cursor_hooks.py -q`
4. Manual smoke test:

```bash
J='{"status":"completed","loop_count":0,'
J+='"transcript_path":"/path/to/transcript.jsonl",'
J+='"workspace_roots":["/path/to/workspace"]}'
echo "$J" | \
  .venv/bin/python3 HPCPerfStats/cursor-hooks/check-close-gate.py
```

## Requirements

- `<workspace_root>/.venv/bin/python3` - `hooks.json` invokes hooks through the workspace venv rather than the `python3` on `PATH`, which is often system 3.9 on Linux, where plan gates fail open on `typing_extensions` import errors.
- Hook modules stay stdlib-only (no third-party imports) so direct `python3 cursor-hooks/*.py` smoke tests still work on 3.9+.
- Trusted workspace (project hooks do not run in untrusted workspaces)

## Dual registration

New domain rules must appear in both:

1. Profile `agent-discipline-core.mdc` task router table
2. `hook_task_router.py` - `MONITOR_ROUTER_ENTRIES` or `HPCPERFSTATS_ROUTER_ENTRIES`

See monitor `RULES_README.md` or hpcperfstats `RULES_README.md` for always-on caps.

## hooks.json

Edit `HPCPerfStats/cursor-hooks/hooks.json` (checked in), then re-copy to the workspace root so Cursor reloads:

```bash
cp HPCPerfStats/cursor-hooks/hooks.json .cursor/hooks.json
```

Do not `ln -sf` `.cursor/hooks.json`: Cursor does not load a symlinked project hooks config.
After the copy, confirm that Settings -> Hooks and the Hooks output channel show neither the symlink refusal nor "No project hooks configuration found".

## Claude Code hooks

The canonical Claude Code hook is the workspace file `<WORKSPACE_ROOT>/.claude/hooks/hpc_claude_hooks.py`, wired by `<WORKSPACE_ROOT>/.claude/settings.json` (system `python3`, stdlib only).
`cursor-hooks/hpc_claude_hooks.py` is a byte-identical copy of it, wired by the checked-in `.claude/settings.json` (`$CLAUDE_PROJECT_DIR/cursor-hooks/...`) for sessions opened in the checkout.
The test `<WORKSPACE_ROOT>/.claude/tests/test_checkout_hook_copy.py` fails when the two differ; resync from the workspace root with `cp .claude/hooks/hpc_claude_hooks.py HPCPerfStats/cursor-hooks/hpc_claude_hooks.py`.
The `ruff-check` and `ruff-format` pre-commit hooks exclude the copy: their py314 fixes would change its bytes and produce syntax the system `python3` cannot parse.
It imports the same router (`hook_task_router.py`) as Cursor, so both tools agree on which rules a path triggers.

| Event                                               | Subcommand  | Behavior                                                                                                                           |
| --------------------------------------------------- | ----------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `UserPromptSubmit`                                  | `prompt`    | Discipline reminder, plus bug-task and plan-task rule hints from prompt keywords                                                   |
| `PreToolUse` `Edit\|Write\|MultiEdit\|NotebookEdit` | `pre-edit`  | Deny until every triggered rule and router `docs` file was Read this turn (since the last user prompt, read from the transcript)   |
| `PostToolUse` edits                                 | `post-edit` | Remind about rule dual registration (`agent-discipline-core.mdc` and `hook_task_router.py`) and the `.cursor/hooks.json` copy step |
| `Stop`                                              | `stop`      | Close-heading gate and paste-safety check (below), capped at `MAX_STOP_BLOCKS` consecutive blocks per session                      |

The close-heading gate applies when the turn edited a gated path: the final message needs `## Agent rule dispatch` (listing every triggered non-always-on rule), `## Final code review (senior engineer pass)` and `## Post-implementation review`.
The paste-safety check runs every turn: a `bash` or `sh` fence in the final message must not contain a heredoc or a line longer than `PASTE_LINE_MAX` characters.
When `.cursor/workspace-profile` names `dual`, edits under `monitor/` resolve rules from `monitor/cursor-rules/` first and also require that tree's always-on rules.

The hook resolves the workspace root from its own location (two directories up).
In a bare clone without a workspace `.cursor/rules` tree it finds no rules and fails open: edits are allowed and internal errors only print to stderr.

Not ported (Cursor-only): CreatePlan / live `.cursor/plans` gates.

## Agent workspace templates

`agent-workspace/` holds what a new workspace needs outside the checkout; [Set up the agent workspace](../docs/how-to/develop/set-up-the-agent-workspace.md) runs its installer.

| File                         | Installed as                                         |
| ---------------------------- | ---------------------------------------------------- |
| `install-agent-workspace.sh` | None (run it; `--dry-run` previews)                  |
| `isolated_commit.py`         | `~/.local/bin/git-isolated-commit` (symlink)         |
| `sync-cursor-rules.py`       | `<WORKSPACE_ROOT>/.claude/scripts/` (copy)           |
| `settings.json.template`     | `<WORKSPACE_ROOT>/.claude/settings.json` (if absent) |
| `CLAUDE.md.template`         | `<WORKSPACE_ROOT>/CLAUDE.md` (if absent)             |

`isolated_commit.py`, `sync-cursor-rules.py` and `settings.json.template` are byte copies of the maintainer's workspace files, which hold no personal paths.
`hpcperfstats/tests/test_agent_workspace_templates.py` fails when a copy differs from its canonical file on the same host, and skips the check when the canonical file is absent.
Both Python files run under the system `python3`, so ruff excludes `agent-workspace/` (pre-commit and `pyproject.toml`) and the test parses them as Python 3.9.
The sync script finds the workspace root two directories above its own file, so it is installed as a copy; `isolated_commit.py` finds the checkout from the current directory, so a symlink works.

## Maintainer tooling

These gates and scripts serve the agent workflow; the developer test commands are in [Run tests](../docs/how-to/develop/run-tests.md).

### Python docstring and type-hint inventory gate

Every in-scope production module, class and def must pass the full Google/Napoleon surface in `python-docstring-and-typing-contract.mdc`: behavior summaries that are not name-echoes or upgrade-helper AI slop, Args/Returns/Yields, Raises when applicable, Examples with real usage on every def, module and class Attributes (including private), subclass and override superclass prose, and signature annotations.
The gate also rejects call-site deferrals, `Internal helper for` / `Compute or apply` templates, `Value polymorphism` boilerplate, and `>>> name(...)  # doctest: +SKIP` placeholder Examples.
It runs as a commit-stage hook; check both packages from the checkout:

```bash
# Fail if any in-scope surface is non-compliant
../.venv/bin/python3 scripts/python_def_inventory.py --check

# Regenerate committed snapshot
../.venv/bin/python3 scripts/python_def_inventory.py --write \
  docs/python_def_inventory.json

# Gate unit tests (fixtures + full-tree green)
../.venv/bin/python3 -m pytest -q \
  hpcperfstats/tests/test_python_def_inventory_gate.py

# Tools package (in-tree under hpcperfstats-tools/)
../.venv/bin/python3 -m pytest -q \
  hpcperfstats-tools/tests/test_python_def_inventory_gate.py
```

The optional bulk upgrade helper must not emit call-site or ellipsis-placeholder boilerplate; annotations may use `Any` with explicit Args prose, and `# doctest: +SKIP` only with concrete example calls.
Review its diff:

```bash
../.venv/bin/python3 scripts/python_def_docstring_upgrade.py \
  --path-filter hpcperfstats/dbload --apply

# Rewrap / fill full surface to Ruff line-length (80 - main repo and
# hpcperfstats-tools)
../.venv/bin/python3 scripts/python_def_docstring_upgrade.py \
  --force-docs --line-length 80 --apply
../.venv/bin/python3 scripts/python_def_docstring_upgrade.py \
  --root hpcperfstats-tools \
  --force-docs --line-length 80 --apply
```

### Plan close hook stages

A plan is not done until both hook stages are green (`plan-creation-contract.mdc`, `docs/plans/PLAN_TEMPLATE.md` todo `git-hooks-pre-close`).
Run `pre-commit run --all-files` and `pre-commit run --hook-stage pre-push --all-files` from the checkout; no commit or push is needed, only the suites and the fixes.

### Chat failure registry

`scripts/extract_chat_failure_signatures.py` mines agent transcripts for failure signatures into a registry JSON for triage.
It writes to the script's default `--output` path (or `--output PATH`); the file is generated, not committed.

```bash
T="$HOME/.cursor/projects/<PROJECT_ID>/agent-transcripts"
../.venv/bin/python3 scripts/extract_chat_failure_signatures.py \
  --transcripts-dir "$T" --since 2026-01-04 --until 2026-06-04
```

`<PROJECT_ID>` is the Cursor project directory name.
