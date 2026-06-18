# Monitor cursor rules layout

## Always-on (6 files)

Loaded every agent turn. Keep these short; do not add more `alwaysApply: true` files without removing another.

| File | Role |
|------|------|
| `agent-discipline-core.mdc` | **Control plane** — pre-close checklist, task router, when to Read domain rules |
| `plan-completion-gate.mdc` | Close sequence (senior review + self-review) |
| `every-error-regression-test.mdc` | Regression test law for fixes |
| `monitor-workspace-contract.mdc` | Monitor-only scope; RabbitMQ/listend message contract |
| `python-venv-enforcement.mdc` | `.venv` path |
| `out-of-monitor-hpcperfstats-rules.mdc` | Authorized non-monitor work loads `hpcperfstats/cursor-rules/` |

## Domain rules (~35 files — `alwaysApply: false`)

**Mandatory when triggered**, not optional. Agents must use the **Read** tool on matching rules from the router in `agent-discipline-core.mdc` before editing trigger paths.

Notable contracts (see filename in `HPCPerfStats/monitor/cursor-rules/`):

- **`monitor-static-build-verification.mdc`** / **`monitor-dual-verify-cross-and-static.mdc`** — canonical static bundle + cross-compile gates
- **`monitor-workspace-contract.mdc`** — scope and listend consumer contract
- **`monitor-consumer-side-plan.mdc`** — secondary consumer plan when emit changes need ingest work
- **`plan-creation-contract.mdc`** / **`plan-template-enforcement.mdc`** — plan authoring; template at **`HPCPerfStats/monitor/docs/plans/PLAN_TEMPLATE.md`**

Adding a new domain rule (same task, non-optional):

1. Default `alwaysApply: false`
2. Add `globs:` when file patterns are stable
3. **Dual registration** — add trigger row in `agent-discipline-core.mdc` **and** matching `MONITOR_ROUTER_ENTRIES` in `.cursor/hooks/hook_task_router.py`
4. Do **not** duplicate close-gate or testing law (link instead)
5. Verify dual registration in **Final code review** (`plan-completion-gate.mdc`); Cursor hooks enforce it at edit time and close

## Cursor hooks

Committed under `HPCPerfStats/.cursor/` (symlink from workspace `.cursor/`). See `.cursor/hooks/README.md`.

- **`stop`** — close-gate headings after file edits **or `CreatePlan`**, rule dispatch, dual-registration when `cursor-rules/*.mdc` edited, plan body `PLAN_TEMPLATE.md` sections, Read-before-edit/plan proof
- **`postToolUse`** — `check-edit-triggered-rules.py` warns when triggered domain rules or plan reads are missing
- **`postToolUse`** — `check-new-rule-router.py` enforces dual registration for new/edited `cursor-rules/*.mdc`

Hooks auto-detect **monitor** vs **hpcperfstats** workspace profile from the `.cursor/rules` symlink target.

## Authoritative path

Edit `HPCPerfStats/monitor/cursor-rules/*.mdc` (symlinked from workspace `.cursor/rules/`).

In other checkouts without hard-links, update **both** `.cursor/rules/` and `monitor/cursor-rules/` in one commit per **`monitor-cursor-rules-sync.mdc`**.
