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
- **`rabbitmq-memory-cgroup-contract.mdc`** — compose `mem_limit` 96g + watermark absolute **strictly below** cgroup (80GiB); recreate rabbitmq; never classic-queue “fix”; dual-registered with `agent-discipline-core.mdc` + `hook_task_router.py`.
- **`compose-operator-terminal-commands.mdc`** — plans and blocked diagnostics: INI paths first, filtered service-specific logs (not raw pipeline firehose), one `docker compose exec` block per service; findings on disk per **`plan-live-disk-sync.mdc`**.
- **`operator-command-lessons-learned.mdc`** — when operator commands fail, update `compose-operator-terminal-commands.mdc` in the same task; do not repeat broken patterns.
- **`plan-live-disk-sync.mdc`** — live plan file on disk is authority; chat/CreatePlan do not count; operator discovery Completed findings vs Pending commands.
- **`package-lib-colocation.mdc`** — new library modules under flat `{parent}/lib/`; import prefixes; `.gitignore` negation so lib trees are committed; Django migrations stay under `site/lib/machine/`.
- **`deploy-ini-with-code-no-phase-zero.mdc`** — no pre-code INI redeploy; proactive read-only operator data gathering for plans.
- **`sync-timedb-change-regression-gate.mdc`** — mandatory `tests/run_sync_timedb_regression_battery.sh` before sync_timedb stall PR close; T0/T1/T2 operator verify (`docs/OPERATOR_SYNC_TIMEDB_STALL_VERIFY.md`); hooks enforce battery log citation on close.
- **`sync-timedb-queue-orchestrator-contract.mdc`** — greenfield `job:v1` orchestrator (exclusive flock, streaming submit, sliding-window pools, B-09 predicates); dual-registered with `agent-discipline-core.mdc` + `hook_task_router.py`.
- **`sync-timedb-no-timers.mdc`** — no internal wall soft-kills; progress kinds + idle kill/yield; dual-registered on sync_timedb / pool / tests paths.
- **`sync-timedb-anti-log-spam.mdc`** — hot wait/poll/retry/defer INFO must rate-limit with `suppressed_n=` (process-local limiter); never bare `log_print` in those loops.
- **`startup-migration-bounded-work.mdc`** — `django_startup` migrate must stay bounded (no unbounded hypertable work); production never runs `makemigrations`.
- **`dockerignore-test-artifacts-sync.mdc`** — `.dockerignore` must exclude test-only paths; keep `test_dockerignore_test_artifacts.py` in sync when test layout changes.
- **`frontend-prod-test-build-boundary.mdc`** — `build` vs `build:prod`, `tsconfig.app`/`test`, `frontend/test/` tree, production static export exclusions.
- **`frontend-static-prod-serve-only.mdc`** — nginx may only serve web-required static assets; strip/deny config/test leftovers (`nginx-csp-*.inc`, etc.) before nginx online.
- **`python-docstring-and-typing-contract.mdc`** — Google-style Args/Returns + signature hints for every in-scope Python `def`; hard inventory gate via `scripts/python_def_inventory.py` / `docs/python_def_inventory.json`.
- **`no-production-env-for-ini-config.mdc`** — production must not require `.env` / shell env flags for site config in INI or settings (e.g. TLS **`proxy_ssl_source`** mount); dual-registered with `agent-discipline-core.mdc` + `hook_task_router.py`.
- **`python-image-interpreter-contract.mdc`** — GIL `python:3.14.7-trixie` for web; baked `/opt/python3.14t` for `listend`/`sync_timedb`/`update_metrics`; no INI ABI switch; dual-registered with `agent-discipline-core.mdc` + `hook_task_router.py`.

Adding a new domain rule (same task, non-optional):

1. Default `alwaysApply: false`
2. Add `globs:` when file patterns are stable
3. **Dual registration** — add trigger row in `agent-discipline-core.mdc` **and** matching `ROUTER_ENTRIES` in `cursor-hooks/hook_task_router.py` (same task)
4. **Copy hooks.json** — `cp HPCPerfStats/cursor-hooks/hooks.json .cursor/hooks.json` (Cursor refuses a symlinked project hooks config)
5. Do **not** duplicate close-gate or testing law (link instead)
6. Verify dual registration + hooks.json copy in **Final code review** (`plan-completion-gate.mdc`); Cursor hooks enforce dual registration at edit time and close (`check-new-rule-router.py`, `check-close-gate.py`)

## Cursor hooks

Committed under `HPCPerfStats/cursor-hooks/` (workspace `.cursor/hooks` symlinks there). Workspace **`.cursor/hooks.json`** must be a **real file copy** of `HPCPerfStats/cursor-hooks/hooks.json` (Cursor refuses a symlinked project hooks config). See `cursor-hooks/README.md`.

- **`preToolUse`** — `check-pre-create-plan-reads.py` denies **`CreatePlan`** until plan-authoring `*.mdc` + `PLAN_TEMPLATE.md` Read; `check-block-until-plan-disk.py` blocks other tools until live plan disk write
- **`stop`** — close-gate headings after file edits **or `CreatePlan`**, auto-triggered rule dispatch, **Read before first edit/plan**, **rule dual-registration** when `cursor-rules/*.mdc` edited, **PLAN_TEMPLATE** + **operator-discovery command shape** + required todos (`git-hooks-pre-close`, `post-implementation-review`) in the **live disk plan file** when `CreatePlan` was used (not CreatePlan tool body alone), invalid-N/A rejection, ≥3 edge-case bullets (`loop_limit: 3`)
- **`postToolUse`** — `check-edit-triggered-rules.py` warns mid-turn when an edit or **`CreatePlan`** triggers unread domain rules or missing `plan-creation-contract.mdc` / `PLAN_TEMPLATE.md` reads
- **`postToolUse`** — `check-new-rule-router.py` enforces dual registration (`agent-discipline-core.mdc` + `hook_task_router.py`) for new/edited `cursor-rules/*.mdc`

## Authoritative path

Edit `hpcperfstats/cursor-rules/*.mdc` (symlinked from workspace `.cursor/rules/`).
