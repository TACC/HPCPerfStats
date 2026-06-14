# Cursor rules layout

## Always-on (5 files — ~400 lines total)

Loaded every agent turn. Keep these short; do not add more `alwaysApply: true` files without removing another.

| File | Role |
|------|------|
| `agent-discipline-core.mdc` | **Control plane** — pre-close checklist, task router, when to Read domain rules |
| `plan-completion-gate.mdc` | Close sequence (senior review + self-review) |
| `every-error-regression-test.mdc` | Regression test law for fixes |
| `workspace-guardrails.mdc` | `monitor/` read-only, compose wiring |
| `workspace-layout-and-python-env.mdc` | `.venv` path, rules directory |

## Domain rules (~70 files — `alwaysApply: false`)

**Mandatory when triggered**, not optional. Agents must use the **Read** tool on matching rules from the router in `agent-discipline-core.mdc` before editing trigger paths.

Notable contracts (see filename in `hpcperfstats/cursor-rules/`):

- **`openapi-spa-wire-validation-contract.mdc`** — OpenAPI serializers must match live Django JSON validated by Orval Zod in `parseApiResponse`; wire contract tests required (`test_*_openapi_wire_contract.py`).

Adding a new domain rule (same task, non-optional):

1. Default `alwaysApply: false`
2. Add `globs:` when file patterns are stable
3. **Dual registration** — add trigger row in `agent-discipline-core.mdc` **and** matching `ROUTER_ENTRIES` in `.cursor/hooks/hook_task_router.py` (same task)
4. Do **not** duplicate close-gate or testing law (link instead)
5. Verify dual registration in **Final code review** (`plan-completion-gate.mdc`); Cursor hooks enforce it at edit time and close (`check-new-rule-router.py`, `check-close-gate.py`)

## Cursor hooks

Committed under `HPCPerfStats/.cursor/` (symlink from workspace `.cursor/`). See `.cursor/hooks/README.md`.

- **`stop`** — close-gate headings after file edits **or `CreatePlan`**, auto-triggered rule dispatch, **Read before first edit/plan**, **rule dual-registration** when `cursor-rules/*.mdc` edited, plan body `PLAN_TEMPLATE.md` sections when `CreatePlan` was used, invalid-N/A rejection, ≥3 edge-case bullets (`loop_limit: 3`)
- **`postToolUse`** — `check-edit-triggered-rules.py` warns mid-turn when an edit or **`CreatePlan`** triggers unread domain rules or missing `plan-creation-contract.mdc` / `PLAN_TEMPLATE.md` reads
- **`postToolUse`** — `check-new-rule-router.py` enforces dual registration (`agent-discipline-core.mdc` + `hook_task_router.py`) for new/edited `cursor-rules/*.mdc`

## Authoritative path

Edit `hpcperfstats/cursor-rules/*.mdc` (symlinked from workspace `.cursor/rules/`).
