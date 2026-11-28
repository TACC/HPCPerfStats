# Set up the agent workspace

This page shows developers how to lay out a workspace for the HPCPerfStats checkout so Cursor and Claude Code load the project rules and hooks.
Use it when you clone the repository for development, or when an agent session edits files without asking you to read the rules first.

## Prerequisites

- Python 3.14 or later on the host, for the workspace virtualenv.
- Git access to the repository.
- Cursor, Claude Code or both.

## Lay out the workspace

The workspace is a directory one level above the git checkout.
It holds the checkout, the shared virtualenv and the Cursor metadata:

```text
<WORKSPACE_ROOT>/
  HPCPerfStats/        git checkout (pyproject.toml, docker-compose.yaml)
  .venv/               host Python virtualenv
  .cursor/
    rules/             symlink -> HPCPerfStats/hpcperfstats/cursor-rules
    hooks/             symlink -> HPCPerfStats/cursor-hooks
    hooks.json         copy of HPCPerfStats/cursor-hooks/hooks.json
    plans/             live *.plan.md files (outside git)
    workspace-profile  optional; first word dual
```

`<WORKSPACE_ROOT>`: the directory you choose for the workspace.

Scripts and hooks find the virtualenv as `../.venv` from the checkout, so the checkout directory must be named `HPCPerfStats` and sit next to `.venv`.

To create the workspace, run these from `<WORKSPACE_ROOT>`:

1. Clone the repository as `HPCPerfStats`:

   ```bash
   git clone git@github.com:TACC/HPCPerfStats.git HPCPerfStats
   ```

2. Create the virtualenv with Python 3.14 or later:

   ```bash
   python3.14 -m venv .venv
   ```

3. Link the rules and hooks, and copy the hook config:

   ```bash
   mkdir -p .cursor/plans
   ln -sf ../HPCPerfStats/hpcperfstats/cursor-rules .cursor/rules
   ln -sf ../HPCPerfStats/cursor-hooks .cursor/hooks
   cp HPCPerfStats/cursor-hooks/hooks.json .cursor/hooks.json
   ```

   Cursor refuses a symlinked `hooks.json`, so it is a copy.
   Copy it again after every change to `HPCPerfStats/cursor-hooks/hooks.json`.

4. Optional: to edit both the container code and `monitor/` from one workspace, mark it as dual:

   ```bash
   echo dual > .cursor/workspace-profile
   ```

   In a dual workspace, edits under `HPCPerfStats/monitor/` follow `HPCPerfStats/monitor/cursor-rules/` and everything else follows `.cursor/rules/`.

## Install the dev tools and git hooks

To install the Python packages and the pre-commit and pre-push hooks, run these from the checkout:

1. Install the dev extras, register the git hooks and run `npm ci` for the frontend:

   ```bash
   ./scripts/install-git-hooks.sh
   ```

   The script stops when `../.venv/bin/python3` is missing.

2. Optional: install `hpcperfstats-tools` editable when you work on the client:

   ```bash
   ../.venv/bin/pip install -e ./hpcperfstats-tools
   ```

[Run tests](run-tests.md#run-git-hooks-and-static-analysis) lists what each hook stage runs.

## Connect Cursor

Open `<WORKSPACE_ROOT>` (not the checkout) as the Cursor workspace, and trust it: project hooks do not run in an untrusted workspace.
Cursor reads `.cursor/rules/` and runs the hooks in `.cursor/hooks.json` through `.venv/bin/python3`.
[Cursor hooks](../../../cursor-hooks/README.md) describes each hook.

## Connect Claude Code

Open Claude Code in the checkout.
The checked-in `.claude/settings.json` runs `cursor-hooks/hpc_claude_hooks.py` on each prompt, edit and stop.
That hook imports the same router as the Cursor hooks (`cursor-hooks/hook_task_router.py`), so both tools ask for the same rules:

- Before an edit, it denies the change until every rule the path triggers was read in the current turn.
- At the end of a turn that edited files, it requires the close sections the rules name.
- In every final message, it rejects `bash` blocks with a heredoc or a line over 68 characters.

The hook finds the rules through `<WORKSPACE_ROOT>/.cursor/rules`, so complete the workspace layout first.
Without that directory it allows every edit.

## Commit from a shared checkout

`pre-commit` stashes every unstaged change in the checkout while its hooks run.
When several agents or editors share one checkout, an edit saved in that window can be lost.
In that setup, commit with `git isolated-commit`, which runs the commit and its hooks in a separate worktree slot next to the checkout and touches only the paths you name:

```bash
git isolated-commit -m "<MESSAGE>" -- <PATHS>
```

`<MESSAGE>`: the commit message; `<PATHS>`: every path you changed.

The helper is a `git-isolated-commit` script on your `PATH`; it is not part of this repository.
Ignored files that hooks need in the slot, such as `hpcperfstats.ini` and the frontend `node_modules`, are linked in with `git config --add isolated-commit.link <PATH>`.

## Verify the result

To check the workspace:

1. Run the hook unit tests from the checkout:

   ```bash
   ../.venv/bin/python3 -m pytest -p no:django \
     hpcperfstats/tests/test_cursor_hooks.py -q
   ```

2. In Cursor, open **Settings -> Hooks** and check that every hook in `hooks.json` is listed.

3. In Claude Code, ask for an edit to `README.md` without reading any rule; the hook denies it and names the rules to read.

## Troubleshooting

- Cursor shows `Refusing to load Project hooks.json via symlink`: `.cursor/hooks.json` is a symlink; replace it with a copy of `HPCPerfStats/cursor-hooks/hooks.json`.
- Claude Code edits files without asking for rules: `<WORKSPACE_ROOT>/.cursor/rules` is missing or the checkout is not named `HPCPerfStats`; recheck the layout.
- `install-git-hooks.sh` fails with `workspace venv missing`: create `<WORKSPACE_ROOT>/.venv` first.

## Related

- [Cursor hooks](../../../cursor-hooks/README.md)
- [Run tests](run-tests.md)
- [Documentation style](../../reference/documentation-style.md)
