# Cursor rules layout

## Always-on (5 files — ~400 lines total)

Loaded every agent turn. Keep these short; do not add more `alwaysApply: true` files without removing another.

| File | Role |
|------|------|
| `agent-discipline-core.mdc` | **Control plane** — pre-close checklist, task router, when to Read domain rules |
| `plan-completion-gate.mdc` | Close sequence (awesome-code-cleanup pass + senior review + self-review) |
| `every-error-regression-test.mdc` | Regression test law for fixes |
| `workspace-guardrails.mdc` | `monitor/` read-only (unless `.cursor/workspace-profile` = `dual`), compose wiring, fail-closed unique-copy unlink |
| `workspace-layout-and-python-env.mdc` | `.venv` path, rules directory |

## Domain rules (~70 files — `alwaysApply: false`)

**Mandatory when triggered**, not optional. Agents must use the **Read** tool on matching rules from the router in `agent-discipline-core.mdc` before editing trigger paths.

Notable contracts (see filename in `hpcperfstats/cursor-rules/`):

- **`openapi-spa-wire-validation-contract.mdc`** — OpenAPI serializers must match live Django JSON validated by Orval Zod in `parseApiResponse`; wire contract tests required (`test_*_openapi_wire_contract.py`).
- **`frontend-stack-wiring-contract.mdc`** — SPA routing, thin hooks, `response-schema-registry.ts`, and test mock layers must stay wired; see view → hook → API map and drift guards.
- **`interactive-ready-controls.mdc`** — hide or skeleton until ready; shown controls must be immediately interactive (`initialLoading`, `tableBusy`, lazy sections).
- **`rabbitmq-memory-cgroup-contract.mdc`** — compose `mem_limit` 96g + watermark absolute **strictly below** cgroup (64GiB); **`ERL_FLAGS=+MBas aobf +MBlmbcs 512 +MHlmbcs 512`**; recreate rabbitmq; never classic-queue “fix”; dual-registered with `agent-discipline-core.mdc` + `hook_task_router.py`.
- **`postgres-custom-image-and-migrate-contract.mdc`** — homemade Alpine PG18 + Timescale bake, `io_method=worker`, logical migrate (not `pg_upgrade`); dual-registered with `agent-discipline-core.mdc` + `hook_task_router.py`.
- **`podman-runtime.mdc`** — rootless `podman`/`podman-compose`, isolated
  test/development projects, project-scoped cleanup, and mandatory `/data`
  storage for non-DNF caches/tools/temp and container state; dual-registered
  with compose workflow paths in `agent-discipline-core.mdc` +
  `hook_task_router.py`.
- **`compose-operator-terminal-commands.mdc`** — plans and blocked diagnostics: INI paths first, filtered service-specific logs (not raw pipeline firehose), one `podman-compose -p hpcperfstats exec` block per service; update_metrics census uses `get_metrics_pool_processes()` as the thread count and `[thread:metrics-pool]`.
- **`operator-command-lessons-learned.mdc`** — when operator commands fail, update `compose-operator-terminal-commands.mdc` in the same task; do not repeat broken patterns.
- **`plan-live-disk-sync.mdc`** — live plan file on disk is authority; chat/CreatePlan do not count; operator discovery Completed findings vs Pending commands.
- **`package-lib-colocation.mdc`** — new library modules under flat `{parent}/lib/`; import prefixes; `.gitignore` negation so lib trees are committed; Django migrations stay under `site/lib/machine/`.
- **`deploy-ini-with-code-no-phase-zero.mdc`** — no pre-code INI redeploy; proactive read-only operator data gathering for plans.
- **`sync-timedb-change-regression-gate.mdc`** — mandatory `tests/run_sync_timedb_regression_battery.sh` before sync_timedb stall PR close; T0/T1/T2 operator verify (`docs/how-to/operate/verify-sync-timedb.md`); hooks enforce battery log citation on close.
- **`sync-timedb-queue-orchestrator-contract.mdc`** — greenfield `job:v1` orchestrator (exclusive flock, streaming submit, sliding-window pools, B-09 predicates); dual-registered with `agent-discipline-core.mdc` + `hook_task_router.py`.
- **`sync-timedb-state-machine-contract.mdc`** — before editing sync_timedb state-bearing code, Read the rule **and** `docs/SYNC_TIMEDB_STATE_MACHINE.md`; INV-NEG / INV-COPY change protocol, exit table, `--write-doc` + drift guard. First rule using the router **`docs`** field: `RouterEntry["docs"]` lists checkout-relative non-rule files that Cursor (`check-edit-triggered-rules.py`, close gate) and Claude Code (`hpc_claude_hooks.py` pre-edit/stop) require Read this turn, like rules. Dual-registered with `agent-discipline-core.mdc` + `hook_task_router.py`.
- **`sync-timedb-no-timers.mdc`** — no internal wall soft-kills; progress kinds + idle kill/yield; dual-registered on sync_timedb / pool / tests paths.
- **`update-metrics-batch-resilience.mdc`** — update_metrics uses bounded titled 3.14t threads only; parent persistence, DB statement-timeout boundaries, partial stall recovery, lazy Future release, and no process-pool/SIGALRM fallback.
- **`sync-timedb-anti-log-spam.mdc`** — hot wait/poll/retry/defer INFO must rate-limit with `suppressed_n=` (process-local limiter); never bare `log_print` in those loops.
- **`startup-migration-bounded-work.mdc`** — `django_startup` migrate must stay bounded (no unbounded hypertable work); production never runs `makemigrations`.
- **`migration-fail-closed-gates.mdc`** — a migration that cannot do its work yet raises (stays unapplied, retries); never NOTICE-and-skip, which Django records as applied (hpcperfstats03 `0032`/`0040`); lock contention uses bounded retry then raise; dual-registered with `agent-discipline-core.mdc` + `hook_task_router.py`.
- **`dockerignore-test-artifacts-sync.mdc`** — `.dockerignore` must exclude test-only paths; keep `test_dockerignore_test_artifacts.py` in sync when test layout changes.
- **`frontend-prod-test-build-boundary.mdc`** — `build` vs `build:prod`, `tsconfig.app`/`test`, `frontend/test/` tree, production static export exclusions.
- **`frontend-static-prod-serve-only.mdc`** — nginx may only serve web-required static assets; strip/deny config/test leftovers (`nginx-csp-*.inc`, etc.) before nginx online. Brotli/Gzip sidecars come from Python `compress_static_sidecars` after collectstatic/heal, not from `copy-next-export`.
- **`no-cdn-in-production.mdc`** — no third-party asset CDNs in production (self-host/vendor; jsDelivr only in tests); dual-registered with `agent-discipline-core.mdc` + `hook_task_router.py`.
- **`docs-structure-and-style.mdc`** — Diataxis docs tree: placement table (tutorials / how-to operate+develop / reference / explanation; records in `docs/history/`, `docs/measurements/`, `docs/plans/`), the index is `docs/index.md` (never `docs/README.md`, which the router treats as the install README), quadrant templates and P/V/S standards in `docs/reference/documentation-style.md`, edit the generator not the output, one home per fact, and every pin moves with a doc in the same commit; router entries cover every new directory before any doc moves; monitor docs follow `monitor/cursor-rules/monitor-docs-structure-and-style.mdc`; dual-registered with `agent-discipline-core.mdc` + `hook_task_router.py`.
- **`docs-current-state-only.mdc`** — every evergreen page (`README.md`, `docs/index.md`, `docs/{tutorials,how-to,reference,explanation}/**`, `monitor/docs/{how-to,explanation,reference}/**`, and the core docs at their current paths) describes current code only (no changelog/history tables, no "previously"/"no longer", no old defaults); ASCII plus degree, micro and accented proper names; records exempt, release notes exempt only from current-state wording; paste-safe bash fences; drift guard `hpcperfstats/tests/test_core_docs_drift_guard.py`. Per-doc content triggers: `readme-installation-sync.mdc`, `researcher-job-detail-doc-sync.mdc`, `deploy-concurrency-doc-sync.mdc`, `design-document-sync.mdc`, `testing-doc-sync.mdc`; all dual-registered with `agent-discipline-core.mdc` + `hook_task_router.py`.
- **`python-docstring-and-typing-contract.mdc`** — Google-style Args/Returns + signature hints for every in-scope Python `def`; hard inventory gate via `scripts/python_def_inventory.py` / `docs/python_def_inventory.json`.
- **`grok-surgical-bug-fix-mandate.mdc`** — bug-fix / ticket / error-refactor patches are zero-expansion repairs of the root-cause block (compress that block; do not append nested conditionals; no unrelated adjacent cleanup; no new helpers unless 3+ copies or the user asks); RCA + compression metrics in chat (or PLAN_TEMPLATE metric block) before the first product-code edit; **not** always-on (description + globs + router).
- **`refactor-dedup-priorities.mdc`** — less code first, no golf; bug-fix Boy Scout compresses the failing block only; dual-registered with `agent-discipline-core.mdc` + `hook_task_router.py` (surgical_bug_fix).
- **`no-dead-code-or-stale-comments.mdc`** — patterns the 2026-11 repo-wide cleanup removed (back-compat dead defs, pass-through aliases, redundant branches, value-copying / stale / planning-ID comments, rulers, non-ASCII comment glyphs, `python_def_inventory.json` drift) with Don't / Do / example rows; monitor C counterpart `monitor/cursor-rules/monitor-no-dead-code-or-stale-comments.mdc`; dual-registered with `agent-discipline-core.mdc` + `hook_task_router.py`.
- **`no-production-env-for-ini-config.mdc`** — production must not require `.env` / shell env flags for site config in INI or settings (e.g. TLS **`proxy_ssl_source`** mount); dual-registered with `agent-discipline-core.mdc` + `hook_task_router.py`.
- **`syslog-cluster-ingest.mdc`** — cluster syslog under `data_dir/logs/`; **not** supervisord programs; manual root enable + render; dual-registered with `agent-discipline-core.mdc` + `hook_task_router.py`.
- **`python-image-interpreter-contract.mdc`** — GIL `/opt/python3.14` (`/usr/local`) for web; baked `/opt/python3.14t` for `listend`/`sync_timedb`/`update_metrics`; jemalloc both ways; no INI ABI switch; dual-registered with `agent-discipline-core.mdc` + `hook_task_router.py`.
- **`shared-node-affinity-consumer-contract.mdc`** — `%jobs` / `%affinity` side tables and `dev=j:<jid>` stamp; no `host_data` UNIQUE migration; dual-registered with `agent-discipline-core.mdc` + `hook_task_router.py`.
- **`codon-python-parity-contract.mdc`** — Codon canonical for sync_timedb/listend parse and update_metrics reductions; same-task CPython/NumPy twin parity, `PARITY_FEATURES` registry + drift guards (`test_codon_parity_registry.py`); dual-registered with `agent-discipline-core.mdc` + `hook_task_router.py`.
- **`codon-sync-timedb-parse-contract.mdc`** — Codon-NumPy parse+collapse hot path, 3.14t pyext, A/B ≥1.25× vs Incremental, fail-closed CPython bridge; dual-registered with `agent-discipline-core.mdc` + `hook_task_router.py`.

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
- **`stop`** — close-gate headings after file edits **or `CreatePlan`**, auto-triggered rule dispatch, **Read before first edit/plan**, **rule dual-registration** when `cursor-rules/*.mdc` edited, **PLAN_TEMPLATE** + **operator-discovery command shape** + required todos in order (`code-cleanup` -> `git-hooks-pre-close` -> `post-implementation-review`) in the **live disk plan file** when `CreatePlan` was used (not CreatePlan tool body alone), invalid-N/A rejection, ≥3 edge-case bullets (`loop_limit: 3`)
- **`postToolUse`** — `check-edit-triggered-rules.py` warns mid-turn when an edit or **`CreatePlan`** triggers unread domain rules or missing `plan-creation-contract.mdc` / `PLAN_TEMPLATE.md` reads
- **`postToolUse`** — `check-new-rule-router.py` enforces dual registration (`agent-discipline-core.mdc` + `hook_task_router.py`) for new/edited `cursor-rules/*.mdc`

## Authoritative path

Edit `hpcperfstats/cursor-rules/*.mdc` (symlinked from workspace `.cursor/rules/`).
