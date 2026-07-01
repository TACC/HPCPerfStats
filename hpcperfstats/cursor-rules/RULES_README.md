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
- **`frontend-stack-wiring-contract.mdc`** — SPA routing, thin hooks, `response-schema-registry.ts`, and test mock layers must stay wired; see view → hook → API map and drift guards.
- **`interactive-ready-controls.mdc`** — hide or skeleton until ready; shown controls must be immediately interactive (`initialLoading`, `tableBusy`, lazy sections).
- **`compose-operator-terminal-commands.mdc`** — plans and blocked diagnostics: INI paths first, filtered service-specific logs (not raw pipeline firehose), one `docker compose exec` block per service; findings on disk per **`plan-live-disk-sync.mdc`**.
- **`operator-command-lessons-learned.mdc`** — when operator commands fail, update `compose-operator-terminal-commands.mdc` in the same task; do not repeat broken patterns.
- **`plan-live-disk-sync.mdc`** — live plan file on disk is authority; chat/CreatePlan do not count; operator discovery Completed findings vs Pending commands.
- **`package-lib-colocation.mdc`** — new library modules under flat `{parent}/lib/`; import prefixes; `.gitignore` negation so lib trees are committed; Django migrations stay under `site/lib/machine/`.
- **`deploy-ini-with-code-no-phase-zero.mdc`** — no pre-code INI redeploy; proactive read-only operator data gathering for plans.
- **`sync-timedb-change-regression-gate.mdc`** — mandatory `tests/run_sync_timedb_regression_battery.sh` before sync_timedb stall PR close; T0/T1/T2 operator verify (`docs/OPERATOR_SYNC_TIMEDB_STALL_VERIFY.md`); hooks enforce battery log citation on close.
- **`dockerignore-test-artifacts-sync.mdc`** — `.dockerignore` must exclude test-only paths; keep `test_dockerignore_test_artifacts.py` in sync when test layout changes.

Adding a new domain rule (same task, non-optional):

1. Default `alwaysApply: false`
2. Add `globs:` when file patterns are stable
3. **Dual registration** — add trigger row in `agent-discipline-core.mdc` **and** matching `ROUTER_ENTRIES` in `cursor-hooks/hook_task_router.py` (same task)
4. Do **not** duplicate close-gate or testing law (link instead)
5. Verify dual registration in **Final code review** (`plan-completion-gate.mdc`); Cursor hooks enforce it at edit time and close (`check-new-rule-router.py`, `check-close-gate.py`)

## Cursor hooks

Committed under `HPCPerfStats/cursor-hooks/` (workspace `.cursor/hooks` symlinks there). See `cursor-hooks/README.md`.

- **`preToolUse`** — `check-pre-create-plan-reads.py` denies **`CreatePlan`** until plan-authoring `*.mdc` + `PLAN_TEMPLATE.md` Read; `check-block-until-plan-disk.py` blocks other tools until live plan disk write
- **`stop`** — close-gate headings after file edits **or `CreatePlan`**, auto-triggered rule dispatch, **Read before first edit/plan**, **rule dual-registration** when `cursor-rules/*.mdc` edited, **PLAN_TEMPLATE** + **operator-discovery command shape** in the **live disk plan file** when `CreatePlan` was used (not CreatePlan tool body alone), invalid-N/A rejection, ≥3 edge-case bullets (`loop_limit: 3`)
- **`postToolUse`** — `check-edit-triggered-rules.py` warns mid-turn when an edit or **`CreatePlan`** triggers unread domain rules or missing `plan-creation-contract.mdc` / `PLAN_TEMPLATE.md` reads
- **`postToolUse`** — `check-new-rule-router.py` enforces dual registration (`agent-discipline-core.mdc` + `hook_task_router.py`) for new/edited `cursor-rules/*.mdc`

## Authoritative path

Edit `hpcperfstats/cursor-rules/*.mdc` (symlinked from workspace `.cursor/rules/`).
