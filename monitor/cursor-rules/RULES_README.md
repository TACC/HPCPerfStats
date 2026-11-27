# Monitor cursor rules layout

## Always-on (7 files)

Loaded every agent turn. Keep these short; do not add more `alwaysApply: true` files without removing another.

| File | Role |
|------|------|
| `agent-discipline-core.mdc` | **Control plane** — pre-close checklist, task router, when to Read domain rules |
| `plan-completion-gate.mdc` | Close sequence (awesome-code-cleanup pass + senior review + self-review) |
| `every-error-regression-test.mdc` | Regression test law for fixes |
| `monitor-workspace-contract.mdc` | Monitor-only scope (container edits allowed when `.cursor/workspace-profile` = `dual`); RabbitMQ/listend message contract |
| `python-venv-enforcement.mdc` | `.venv` path |
| `workspace-single-cursor-directory.mdc` | Exactly one `<workspace_root>/.cursor/`; no nested `.cursor/` under git paths |
| `out-of-monitor-hpcperfstats-rules.mdc` | Authorized non-monitor work loads `hpcperfstats/cursor-rules/` |

## Domain rules (59 files — `alwaysApply: false`)

**Mandatory when triggered**, not optional. Agents must use the **Read** tool on matching rules from the router in `agent-discipline-core.mdc` before editing trigger paths.

Notable contracts (see filename in `HPCPerfStats/monitor/cursor-rules/`):

- **`shared-node-affinity-monitor-contract.mdc`** — compile-time multi-jid `%jobs`/`%affinity`; full-resource unmarked fast path; no `scontrol`/`nvidia-smi`/`sched_setaffinity`
- **`monitor-static-build-verification.mdc`** / **`monitor-dual-verify-cross-and-static.mdc`** — canonical static bundle; foreign cross only for portable / multi-arch diffs (skip ARM for x86-only direct perf; skip x86 for `arm` CPU-backend-only)
- **`monitor-valgrind-cpp-linter-gate.mdc`** — plan-close Valgrind Memcheck + full-tree clang-format/clang-tidy (scripts only; no git hooks)
- **`tacc-lmod-build-environment.mdc`** — on TACC SSH hosts: Lmod `module avail`/`module load`, fix agent MODULEPATH, Stampede3 build practices
- **`monitor-build-clean-workspace.mdc`** — pre-compile clean workspace in `prepare_rpmbuild_dirs.sh`, `build_static_bundle.sh`, `cross_compile_test.sh`
- **`monitor-workspace-contract.mdc`** — scope and listend consumer contract
- **`monitor-consumer-side-plan.mdc`** — secondary consumer plan when emit changes need ingest work
- **`monitor-shm-validation-probe-parity.mdc`** — collector `dev` enumeration must match `host_live_probes.py`
- **`monitor-raw-data-format-docs.mdc`** — any change to what the daemon prints updates `docs/RAW_DATA_FORMAT.md`, the `python/hpcperfstats_raw` reader, `docs/metric_descriptions.tsv` and the generated `docs/METRICS_REFERENCE.md` in the same change; `make check` (`tests/test_raw_data_reader.sh`) fails on drift
- **`monitor-beegfs-procfs.mdc`** — BeeGFS client detect + I/O bytes from the client's storage TCP counters (no server query); capacity via `statvfs`
- **`monitor-security-hardening.mdc`** — privileged dump/shm/`dlopen`/exec/conf hardening
- **`monitor-systemd-service-contract.mdc`** — root-daemon sandbox invariants, required device access, and unit/package/test coupling
- **`monitor-runtime-path-coherence.mdc`** — default path synchronization across CLI, startup permissions, systemd, RPM, docs, and tests
- **`monitor-roofline-peak-detect.mdc`** — `host_roofline_peak` NVML/smi/DRM/EDAC gaps + allowlisted GB200/Grace identity
- **`monitor-xpum-integration.mdc`** — Intel PVC / XPU Manager (`intel_gpu`, vendored headers, libxpum dlopen)
- **`monitor-arm-direct-perf.mdc`** — Grace / aarch64 `host_cpu_hw` and `arm_aarch64_imc` by direct perf (3.1+, no PMU library) and the shared `perf_util` helper: enabled/running scaling, never pinned, sysfs names then MIDR table, D1 keys, W=48 modulo emit
- **`monitor-x86-direct-perf.mdc`** — Intel / AMD `host_cpu_hw`, IMC / HBM / DF uncore and RAPL by direct perf (3.1 Phases 2+3, no PMU library, no MSR): verified event rows, D18 load hits, sentinels, D6 sharing keys on x86, all-socket uncore sums, RAPL running totals
- **`monitor-debug-vs-symbols.mdc`** — `--enable-debug` vs `-g`; release quiet logging (startup + hourly + first-fail RMQ)
- **`plan-creation-contract.mdc`** / **`plan-template-enforcement.mdc`** — plan authoring; template at **`HPCPerfStats/monitor/docs/plans/PLAN_TEMPLATE.md`** (bug plans: RCA + Target File + Fix compression sequence + Minimally Invasive Fix + Compression metric block)
- **`grok-surgical-bug-fix-mandate.mdc`** — bug-fix / ticket / error-refactor patches are zero-expansion repairs of the root-cause block (compress that block; do not append nested conditionals; no unrelated adjacent cleanup; no new helpers unless 3+ copies or the user asks); RCA + compression metrics in chat (or PLAN_TEMPLATE metric block) before the first product-code edit; **not** always-on (description + globs + router)
- **`refactor-dedup-priorities.mdc`** — less code first, simplicity, branch reduction; bug-fix Boy Scout compresses only the failing ~30-line block
- **`monitor-docs-structure-and-style.mdc`** — monitor docs live in `monitor/docs/{how-to,explanation,reference}/` (Diataxis), start from the quadrant templates in `HPCPerfStats/docs/reference/documentation-style.md`, and a move updates `EXTRA_DIST`, doc-executing tests, router and inbound links in the same commit; for monitor paths it governs over the container `docs-structure-and-style.mdc`
- **`monitor-no-dead-code-or-stale-comments.mdc`** — C patterns the 2026-11 cleanup removed (dead statics/macros, wrapper and alias macros, unused includes/params, write-only state, duplicate conditions, stale / value-copying / planning-ID comments, non-ASCII comment glyphs) plus what to keep (contract-grepped text, KEYS, vendored headers)

Adding a new domain rule (same task, non-optional):

1. Default `alwaysApply: false`
2. Add `globs:` when file patterns are stable
3. **Dual registration** — add trigger row in `agent-discipline-core.mdc` **and** matching `MONITOR_ROUTER_ENTRIES` in `cursor-hooks/hook_task_router.py`
4. **Copy hooks.json** — `cp HPCPerfStats/cursor-hooks/hooks.json .cursor/hooks.json` (Cursor refuses a symlinked project hooks config)
5. Do **not** duplicate close-gate or testing law (link instead)
6. Verify dual registration + hooks.json copy in **Final code review** (`plan-completion-gate.mdc`); Cursor hooks enforce dual registration at edit time and close

## Cursor hooks

Committed under `HPCPerfStats/cursor-hooks/` (workspace `.cursor/hooks` symlinks there). Workspace **`.cursor/hooks.json`** must be a **real file copy** of `HPCPerfStats/cursor-hooks/hooks.json` — Cursor refuses a symlinked project hooks config. See `cursor-hooks/README.md`.

- **`stop`** — close-gate headings after file edits **or `CreatePlan`**, rule dispatch, dual-registration when `cursor-rules/*.mdc` edited, plan body `PLAN_TEMPLATE.md` sections, Read-before-edit/plan proof
- **`postToolUse`** — `check-edit-triggered-rules.py` warns when triggered domain rules or plan reads are missing
- **`postToolUse`** — `check-new-rule-router.py` enforces dual registration for new/edited `cursor-rules/*.mdc`

Hooks auto-detect **monitor** vs **hpcperfstats** workspace profile from the `.cursor/rules` symlink target.

## Authoritative path

Edit `HPCPerfStats/monitor/cursor-rules/*.mdc` (symlinked from workspace `.cursor/rules/`).

In other checkouts without hard-links, update **both** `.cursor/rules/` and `monitor/cursor-rules/` in one commit per **`monitor-cursor-rules-sync.mdc`**.
