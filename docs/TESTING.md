# Testing hpcperfstats

## Chat failure registry (maintainer)

To mine parent agent transcripts for failure signatures and build **`docs/chat_failure_registry.json`**:

```bash
cd HPCPerfStats
../.venv/bin/python scripts/extract_chat_failure_signatures.py \
  --transcripts-dir "$HOME/.cursor/projects/<project-id>/agent-transcripts" \
  --since 2026-01-04 --until 2026-06-04 \
  --output docs/chat_failure_registry.json
```

P0 triage notes live in **`docs/chat_failure_registry_p0_triage.json`**. Local run logs and workflow artifacts: **`test_runs/`** (gitignored; see **`test_runs/README.md`**).

## Quick start

From the project root (directory containing `pyproject.toml`):

```bash
# Install test extras once
pip install -e ".[test]"

# Unit tests only (no Django DB tests)
python scripts/run_tests.py --no-django

# Full pytest collection (unit + Django tests)
python scripts/run_tests.py
```

You can also run `pytest` directly:

```bash
# Unit-only path
PYTHONPATH=. pytest -q hpcperfstats/tests

# Django app tests (requires DB/settings)
PYTHONPATH=. pytest -q hpcperfstats/site/lib/machine/tests
```

### Python docstring + type-hint inventory gate

Every in-scope production module/class/def must pass the full Google/Napoleon surface in `python-docstring-and-typing-contract.mdc` (behavior summaries that are not name-echoes or upgrade-helper AI slop, Args/Returns/Yields, Raises when applicable, Examples with real usage on every def, module/class Attributes including private, subclass/override superclass prose, plus signature annotations). The inventory gate also rejects call-site deferrals, ``Internal helper for`` / ``Compute or apply`` templates, ``Value polymorphism`` boilerplate, and `>>> name(...)  # doctest: +SKIP` placeholder Examples. Check both repos from the HPCPerfStats checkout:

```bash
# Fail if any in-scope surface is non-compliant
../.venv/bin/python3 scripts/python_def_inventory.py --check

# Regenerate committed snapshot
../.venv/bin/python3 scripts/python_def_inventory.py --write docs/python_def_inventory.json

# Gate unit tests (fixtures + full-tree green)
../.venv/bin/python3 -m pytest -q hpcperfstats/tests/test_python_def_inventory_gate.py

# Tools package (in-tree under hpcperfstats-tools/)
../.venv/bin/python3 -m pytest -q hpcperfstats-tools/tests/test_python_def_inventory_gate.py
```

Optional bulk upgrade helper (review the diff; must not emit call-site / ellipsis-placeholder boilerplate; annotations may use `Any` with explicit Args prose; `# doctest: +SKIP` only with concrete example calls):

```bash
../.venv/bin/python3 scripts/python_def_docstring_upgrade.py --path-filter hpcperfstats/dbload --apply

# Rewrap / fill full surface to Ruff line-length (80 HPCPerfStats / 88 tools)
../.venv/bin/python3 scripts/python_def_docstring_upgrade.py --force-docs --line-length 80 --apply
../.venv/bin/python3 scripts/python_def_docstring_upgrade.py --root hpcperfstats-tools \
  --force-docs --line-length 88 --apply
```

### Operator deploy scripts (not test workflows)

**`scripts/rebuild_frontend.sh`** rebuilds the SPA with npm and copies artifacts into the running **`web`** container’s **`staticfiles_data`** volume without stopping **`pipeline`**. Use it as an **optional SPA-only hot path**. The **primary** way to land SPA after code changes is a from-scratch image rebuild (`hpcperfstats-full` / `docker compose up --build`) plus recreating **`web`**: startup [`spa_static_root_heal`](hpcperfstats/site/lib/spa_static_root_heal.py) fingerprint-syncs package frontend into the volume when `machine/index.html` sha256 diverges (see unit tests in `hpcperfstats/site/hpcperfstats_site/tests/test_spa_static_root_heal.py`).

**`scripts/rebuild_pipeline.sh`** rebuilds the shared **`hpcperfstats`** image for Python/pipeline code changes: it preserves the live **`STATIC_ROOT/frontend`** tree, stops **`pipeline`** then **`web`**, builds Docker target **`hpcperfstats-pipeline-refresh`** (skips **`frontend-builder`** / npm), and recreates **`web`** then **`pipeline`**. On **podman-compose** hosts it uses **`podman build --target`** directly because **`podman-compose build`** does not accept **`--target`**, and after stop+build it **`compose rm`s stale `web`/`pipeline` containers** (briefly stopping **`proxy`** when it was running) before **`compose up -d`** so fixed container names like **`hpcperfstats_web_1`** can be reused. On Docker/Colima it uses **`compose up --force-recreate --no-deps`** instead. It does **not** replace compose test workflows; those still use **`--skip-build`** when reusing an existing image. Static regression: **`./scripts/test_rebuild_pipeline.sh`** and **`./scripts/test_rebuild_frontend_verify.sh`**.

**`hpcperfstats/site/lib/machine/tests` on the host:** tests that need the default PostgreSQL database are **skipped** unless the environment sets **`HPCPERFSTATS_COMPOSE_NETWORK=1`** (the compose workflows `tests/run_db_pytest_workflow.sh` and `tests/run_redis_cache_pytest_workflow.sh` export this inside the `web` container). Tests that only need Django settings and mocks use **`django_db(databases=[])`** and still run on the host. Pure mocks with **no** Django DB fixture use **`@pytest.mark.machine_unit_mock`** (see **`hpcperfstats/conftest.py`**) so pytest does not auto-attach **`django_db`** or skip them for missing Compose. During pytest, Django switches the default cache to **LocMem** unless **`HPCPERFSTATS_PYTEST_LIVE_REDIS=1`** (live Redis workflow).

**Nginx static + SPA-shell contract (WSGI):** In production, nginx serves both **`/static/*`** and the SPA shells for **`/machine/*`** and **`/pub/*`**; the **`proxy`** image **`include`**s **`hps-proxy-allowed-hosts.inc`** generated from **`[DEFAULT] server=`** via **`services-conf/parse_hpcperfstats_proxy_hosts.py`** and **`write_nginx_proxy_allowed_hosts_include.py`** (see **`hpcperfstats/tests/test_write_nginx_proxy_allowed_hosts_include.py`**), forwards only an explicit allowlist of paths to Gunicorn, and returns **404** for unknown paths at the edge. **`docker-compose.yaml`** bind-mounts **`./services-conf/nginx.conf`** onto **`default.conf`** for **`proxy`**, and bind-mounts shared snippets (**`nginx-static-files.conf`**, edge/CSP/proxy-common includes) as the **only** runtime source for those files (**`proxy.Dockerfile`** does not **`COPY`** them) — **`README.md`** expects **`cp services-conf/nginx.conf.example services-conf/nginx.conf`** before **`docker compose up`** when **`proxy`** is included. Edge HSTS/framing/COOP/Permissions-Policy/Referrer-Policy live in **`nginx-edge-security-headers.inc`**; SPA CSP hashes are emitted by **`copy-next-export.mjs`** into **`nginx-csp-machine.inc`** / **`nginx-csp-pub.inc`** on the static volume; OCSP resolver generation is covered by **`hpcperfstats/tests/test_write_nginx_resolver_include.py`**. Gunicorn/Django must still not answer **`STATIC_URL`** or SPA shell routes directly. Host pytest includes **`hpcperfstats/site/hpcperfstats_site/tests/test_nginx_static_wsgi_contract.py`**, which asserts the Django **`Client`** (WSGI) does not resolve arbitrary **`STATIC_URL`** paths or SPA shell routes and snapshots the nginx allowlist snippets. For **local dev** matching that split, prefer compose with **`proxy`** or **`manage.py runserver --nostatic`** plus nginx for **`/static/`** and `/machine/*` shell delivery (see **`README.md`** and **`hpcperfstats/cursor-rules/nginx-static-url-prefix.mdc`**).

### Opt-in stress tests (massive `host_data`)

The directory **`tests/stress_host_data/`** is **outside** the default `pytest` `testpaths` (`hpcperfstats` only), so `python scripts/run_tests.py` and `pytest hpcperfstats` never collect it.

**Default way to run (Docker Compose `db` + `redis`, migrate, pytest inside `web`):**

```bash
cd HPCPerfStats   # directory with docker-compose.yaml
tests/run_stress_host_data_workflow.sh
```

The workflow sets **`HPCPERFSTATS_STRESS_HOST_DATA=1`** and **`HPCPERFSTATS_COMPOSE_NETWORK=1`** inside the container, and defaults **`HPCPERFSTATS_STRESS_HOST_DATA_ROWS`** to **400000** for a faster smoke (override for the full **34,560,000**-row case). The test drives the real **`update_metrics(..., rerun=True)`** path (readiness, pooled metrics, plot prewarm) and writes **`stress_report_<utc>.json`** under **`HPCPERFSTATS_STRESS_REPORT_DIR`** (default **`test_runs/stress/`** on the repo mount). The test passes **`timezone.localtime(job.end_time)`** into **`update_metrics`** so the job is selected under Django’s **`end_time__date`** semantics (large 1 Hz windows can otherwise miss the queryset). Row-count scaling notes: **`docs/plans/stress_row_sweep_scaling_plan.md`**. Inside the container, **`tests/run_stress_host_data_inner.sh`** falls back to **`PYTHONPATH=/home/hpcperfstats`** plus installing pytest extras only if **`pip install -e`** fails on the bind mount (e.g. cloud-sync **Errno 35**). Options: **`--skip-build`**, **`--keep-env`**; extra pytest args after `--`.

**Scale / report env** (forwarded by `tests/run_stress_host_data_workflow.sh`; see `--help`): **`HPCPERFSTATS_STRESS_USE_TIME_SCALE`**, **`HPCPERFSTATS_STRESS_N_HOSTS`**, **`HPCPERFSTATS_STRESS_INTERVAL_SEC`**, **`HPCPERFSTATS_STRESS_DURATION_SEC`**, **`HPCPERFSTATS_STRESS_JID`**, **`HPCPERFSTATS_STRESS_REPORT_DIR`**, **`HPCPERFSTATS_STRESS_EXPLAIN`**, **`HPCPERFSTATS_STRESS_MANUAL_PLOT_SANITY`**, **`HPCPERFSTATS_STRESS_SAMPLE_PATH`**.

### Opt-in `update_metrics` diagnosis (mixed-scale jobs, phase timings)

Seeds two jobs ending the same local calendar day: **small** in-window `host_data` scale **100–300** rows (default **10×15 = 150**) and **large** **300–5000** rows (default **25×32 = 800**), runs **`update_metrics_for_dates`**, and asserts **`LAST_UPDATE_METRICS_DIAGNOSTICS`** phase totals when **`HPCPERFSTATS_UPDATE_METRICS_RETURN_DIAGNOSTICS=1`**.

```bash
cd HPCPerfStats   # directory with docker-compose.yaml
tests/run_update_metrics_diagnosis_workflow.sh
```

Options: **`--skip-build`**, **`--keep-env`**; extra pytest args after **`--`**. Tunables (forwarded by the workflow): **`HPCPERFSTATS_UM_DIAG_SMALL_HOSTS`**, **`HPCPERFSTATS_UM_DIAG_SMALL_STEPS`**, **`HPCPERFSTATS_UM_DIAG_LARGE_HOSTS`**, **`HPCPERFSTATS_UM_DIAG_LARGE_STEPS`**, **`METRICS_POOL_PROCESS_CAP`**.

**Artifact:** `tests/run_update_metrics_diagnosis_inner.sh` sets **`HPCPERFSTATS_UM_DIAG_JSON_OUT`** (default **`test_runs/diagnosis/update_metrics_diagnosis.json`** on the bind-mounted repo) so a copy of the JSON report is written next to the tree, not only under pytest’s temp directory. Override the path by exporting **`HPCPERFSTATS_UM_DIAG_JSON_OUT`** before invoking the workflow. Interpret results against **`docs/artifacts/update_metrics_speedup_followup.md`** (baseline snapshot, sweeps, and ranked tunables).

**Throughput / backlog tuning (production):** set absolute **`[PIPELINE] sync_ingest_pool_processes`**, **`metrics_pool_processes`** (metrics + plot/detail prewarm on the same pool), **`listend_db_ingest_pool_processes`**, and **`[PORTAL] gunicorn_workers`** / **`summary_aggregate_prefetch_max_threads`** against Django **`max_connections`**. Secondary caps/budget/overlap/`metrics_prewarm_*` gates were removed. Ready-queue depth is **`metrics_scheduler_ready_queue_target`** alone (default **100**; not `prefetch_chunks × CHUNK_SIZE`). With **`metrics_idle_slot_supplement_enabled=yes`**, the compute session uses a pool-sized sliding feeder and may fill idle slots from the ready queue using **`estimated_sample_count`** soft/hard caps (`metrics_supplement_sample_soft_max` / `metrics_supplement_sample_hard_max`, defaults **10000** / **80000**; RC-E stops when no original-batch work remains in flight). Optional **`metrics_compute_batch_max_window_s`** / **`metrics_compute_batch_max_single_job_s`** still limit how many long-window jobs share one primary compute batch; **`metrics_compute_watchdog_s`** and **`metrics_compute_total_watchdog_s`** treat metrics+prewarm as one batch wall-time unit; **`metrics_deferred_not_ready_*`** tune readiness backoff.

**Stall triage counters:** scheduler logs and diagnosis JSON now include queue + compute visibility: **`ready_enqueued_total`**, **`ready_dequeued_total`**, **`inflight_jids`**, **`attempted_total`**, **`compute_batches_total`**, **`batch_compute_exceptions_total`**, and **`per_jid_fallback_failures_total`**, in addition to split readiness/deferred counters (**`proxy_not_ready_jids`**, **`strict_not_ready_jids`**, **`strict_ready_jids`**, **`strict_cooldown_skips`**, **`deferred_not_ready_queue_size`**, **`deferred_not_ready_due_now`**, **`deferred_quarantined_jids`**). Mid-batch heartbeats add **`compute_batch_phase`**, **`compute_batch_age_s`**, **`compute_batch_completed_jids`**, and **`compute_batch_size`** on hourly **`metrics progress`** (and `compute batch heartbeat` log lines ~every 60s).

**Idle-slot fill triage:** while a large original-batch jid stays in metrics or prewarm, smaller ready-queue jobs should appear as additional in-flight work (sample estimate under soft/hard caps). If the pool idles with a non-empty ready queue of small jobs and large originals still inflight, check **`metrics_idle_slot_supplement_enabled`** and sample caps. When only supplement work remains, RC-E must stop pulling new ready-queue jobs.

**Pool terminate / prewarm stall / zombies:** `pool terminate timeout; lingering_workers=…` after `/pub` recycle must be followed by SIGKILL + zombie reap (synchronous `reset_pool_hard`). Many `STAT=Z` under `update_metrics.py [main]` with few live `[worker:metrics-pool]` processes after recycle indicates unreaped lingerers — fixed path must not background-terminate then `ensure_pool()`. Prewarm `MetricsPrewarmStallError` at partial `completed=k/n` must keep partial successes, soft-fail the rest, and recycle the pool before the next batch (do not wipe results to `[]` on a poisoned pool).

**Slow batch vs hang:** `inflight_jids ≈ ready_dequeued_total` with **`compute_batches_total=0`** / **`processed_total=0`** for a long window may be a **slow first compute batch** (metrics and/or **`pipeline_required`** prewarm under DB load), not a dead pool. Confirm **`[worker:metrics-pool]` process count** (workers are **processes**, not threads — “only one update_metrics thread in top” is a false negative), watch for `compute batch heartbeat` / rising **`compute_batch_completed_jids`**, then wait for `compute batch N … compute_batch_elapsed_s=… next_batch_cap=…`. **`metrics_compute_watchdog_s`** downshifts **`next_batch_cap`** from **metrics+prewarm `batch_wall_s`** (default total-wall INI `metrics_compute_total_watchdog_s=0` is not required for that downshift).

If progress remains zero for a long window **after** heartbeats show no advancing completed_jids and workers are dead/missing, the scheduler emits **`stall_exit_triggered=1`** and classifies the reason in **`stall_reason`**:
- **`no_ready_candidates`**: starvation at readiness/candidate stage.
- **`compute_stuck_inflight`**: queue drained but work remains inflight.
- **`compute_all_failed`**: compute keeps attempting but every job fails.

When **`stall_exit_triggered=1`**, **`update_metrics.py`** logs the stall summary, exits with code **1** (skipping the legacy post-run sleep), and **supervisord** restarts the process.

**One-shot job recalculate:** ``update_metrics.py --jid <JID>`` (also ``--jid=<JID>``) invalidates Redis/DB plot+detail artifacts and per-jid derived caches for that job, then recomputes metrics plus detail/plot artifacts and exits **0**/**1** without entering the date-range scheduler or post-run sleep. Do not combine ``--jid`` with positional date args. Host unit coverage: ``test_main_jid_*`` / ``test_parse_jid_cli_arg_forms`` in ``hpcperfstats/site/lib/machine/tests/test_update_metrics.py``.

Optional env: **`HPCPERFSTATS_STRESS_PLOT_SEC`** (with manual plot sanity), **`HPCPERFSTATS_LARGE_JOB_HOST_DATA_ROWS`**, **`HPCPERFSTATS_LARGE_JOB_TIME_BUCKETS`**, **`HPCPERFSTATS_LARGE_JOB_WINDOW_ROW_COUNT_CACHE_TTL`** (seconds; `0` disables cached window `COUNT(*)` for large-job gating), **`HPCPERFSTATS_LARGE_JOB_TIME_SQL`** (`date_bin` default; set `ntile` for legacy distinct-time + NTILE sampling on PostgreSQL 14+), **`HPCPERFSTATS_METRICS_PLOT_AGGREGATE_TIME_SLICE_S`** / **`HPCPERFSTATS_PLOT_AGGREGATE_MAX_HOST_TIME_POINTS`** (plot aggregate host×time chunks and materialised-row budget for design capacity **`5000×48×60`** host-samples; INI `metrics_plot_aggregate_time_slice_s` / `metrics_plot_aggregate_max_host_time_points`), **`HPCPERFSTATS_LIVE_DISTINCT_LEGACY_HOSTLIST`** (`1` restores `unnest(host_list)` live distinct SQL; emergency rollback only—see `get_live_distinct_use_legacy_hostlist()` docstring), **`HPCPERFSTATS_UPDATE_METRICS_MAIN_SLEEP_AFTER`** (`1`/`true`/`yes` makes the **`update_metrics.py`** script entrypoint sleep 300s (5 minutes) after a run; default is exit without sleep).

### Opt-in pipeline E2E (RabbitMQ → listend → sync_timedb → metrics → live web)

The directory **`tests/pipeline_e2e/`** is outside default `pytest` `testpaths` (`hpcperfstats` only). Tests are skipped unless **`HPCPERFSTATS_PIPELINE_E2E=1`** (the workflow sets this inside the container).

**Default entry point:**

```bash
cd HPCPerfStats   # directory with docker-compose.yaml
tests/run_pipeline_e2e_workflow.sh
```

This uses **`docker-compose -f docker-compose.yaml -f tests/docker-compose.test-overlay.yaml`**: starts **db**, **redis**, **rabbitmq**, migrates, runs phase 1 pytest (**`test_full_ingest_pipeline.py`**: publish rich synthetic monitor payloads (CPU, Intel PMC/IMC, GPU, IB/OPA/LNET, llite, …), **`listend_drain`**, in-process **`run_ingest_entire_archive_once_for_tests()`** — equivalent to **`sync_timedb once backlog`** but uses pytest-django’s **test** database and inline ingest so spawn workers do not open **`[DEFAULT] dbname`** — then **`update_metrics`** and asserts a full **`metrics_data`** catalog with most metrics numeric), then brings up **web** and runs phase 2 Playwright tests (**`test_job_detail_browser.py`**, **`test_all_endpoints_browser.py`**). Phase 2 expects **`HPCPERFSTATS_COMPOSE_NETWORK=1`**, **`HPCPERFSTATS_PIPELINE_E2E_BASE_URL=http://web:8000`**, and Playwright Chromium (installed in-container unless **`--skip-playwright-install`**). Repo-root **`conftest.py`** sets a default **`HPCPERFSTATS_INI`** only when unset so Compose’s **`/home/hpcperfstats/hpcperfstats.ini`** is not replaced inside the container.

**URL drift guard:** canonical template set lives in **`hpcperfstats/tests/urlconf_route_catalog.py`** as **`EXPECTED_ROUTE_TEMPLATES`**. **`hpcperfstats/tests/test_endpoint_route_snapshot.py`** asserts the live Django resolver matches that set. **`build_pipeline_http_endpoint_specs()`** in the same module expands every template to concrete paths; phase 2 drives each check via Playwright (**`page.goto`** for HTML/redirects, **`APIRequestContext`** for JSON APIs) with status and **`Content-Type`** checks. Adding a URL without updating the catalog + builder fails CI.

Options: **`--skip-build`**, **`--keep-env`**, **`--skip-playwright-install`** (see script **`--help`**).

### Cpuset thread-budget benchmark workflow (sync_timedb priority)

Use the helper below to print process bucket accounting (`real_time`, `normal`, `best_effort`), derive cpuset-aware `S/M/R` (archive slots come from **`sync_archive_pool_processes`** only), and generate a reduced S/M tuning matrix around the derived budget:

```bash
cd HPCPerfStats
../.venv/bin/python tests/pipeline_e2e/cpuset_budget_bench.py
```

To execute extended profiles (derived, nearby tuning, ingest-priority, and sync-overprovision) against the full pipeline workflow:

```bash
cd HPCPerfStats
../.venv/bin/python tests/pipeline_e2e/cpuset_budget_bench.py --run --skip-build
```

The runner applies per-profile overrides with `SYNC_POOL_PROCESS_CAP` and `METRICS_POOL_PROCESS_CAP`, plus overlap/overprovision env settings (`HPCPERFSTATS_PIPELINE_OVERLAP_MODE`, `SYNC_ENABLE_OVERPROVISION_MODE`, `SYNC_BUDGET_OVERCOMMIT_FACTOR`, and sync overprovision multipliers), then calls `tests/run_pipeline_e2e_workflow.sh` for each point. Archive pool size is not env-tuned here — set **`sync_archive_pool_processes`** in the image INI.
If editable install on the bind mount fails with macOS cloud-sync locking (`Errno 35`), the workflow automatically falls back to `PYTHONPATH=/home/hpcperfstats` plus minimal pytest dependencies so benchmark phases can continue.

## Testing on macOS (Docker + full suite)

### 1. Install and start Docker

- **Docker Desktop (Homebrew, Apple Silicon):** run in **Terminal.app** (or another interactive shell) so macOS can prompt for your password if needed:

  ```bash
  arch -arm64 brew update
  arch -arm64 brew install --cask docker
  ```

  If the cask fails with `sudo: a terminal is required` (for example when creating `/usr/local/cli-plugins`), complete the install from an interactive terminal, or install [Docker Desktop for Mac](https://docs.docker.com/desktop/setup/install/mac-install/) manually.

- Open **Docker** from **Applications** once so the Linux VM / engine starts (unless you use another supported backend such as Colima or OrbStack with the `docker` CLI).

- **Verify:** `docker info` should complete without errors and show a running server.

### 2. Python and frontend dependencies

From the `HPCPerfStats/` directory that contains `pyproject.toml`, use the canonical workspace virtualenv one level up:

```bash
../.venv/bin/pip install -e ".[test]"
```

For Vitest:

```bash
cd hpcperfstats/site/frontend && npm ci
```

`npm ci` / `npm install` install dependencies only (no `patch-package`); Bokeh is the stock **`@bokeh/bokehjs`** package. On any **`@bokeh/bokehjs`** version change, follow **`hpcperfstats/cursor-rules/bokeh-version-and-vendor-patch-upgrade.mdc`** (JS/Python pin sync, Vitest, Playwright Bokeh embed test).

The SPA bundles Bokeh via **`@bokeh/bokehjs`** in `package.json`; keep its version aligned with the **`bokeh==…`** pin in `pyproject.toml` so `json_item` embeds stay compatible.

**Bootswatch** (Spacelab) is imported from **`bootswatch`** in the Next.js bundle (same CSS stack as the rest of `/machine/`).

**Frontend stack:** Next.js 16 App Router (static export, Turbopack), strict TypeScript 6, TanStack Query, Orval 8 + Zod 4 (from committed `hpcperfstats/site/openapi/openapi.yaml`), React Hook Form + Zod.

**Production static export** (Docker image, `scripts/rebuild_frontend.sh` — omits test-only routes such as `bokeh-playwright-smoke/`):

```bash
cd hpcperfstats/site/frontend && npm ci && npm run build:prod
```

**Full static export** (local dev, CI before Playwright Bokeh bundle test):

```bash
cd hpcperfstats/site/frontend && npm ci && npm run build
```

See **`hpcperfstats/cursor-rules/frontend-prod-test-build-boundary.mdc`**. Test-only source lives under `hpcperfstats/site/frontend/test/` (Vitest mocks, wire-audit fixtures, Bokeh JSON fixtures).

Regenerate API client after OpenAPI changes:

```bash
cd hpcperfstats/site && python manage.py spectacular --file openapi/openapi.yaml --format openapi
cd hpcperfstats/site/frontend && npm run generate:api
pytest hpcperfstats/site/lib/machine/tests/test_openapi_schema_drift.py
```

### 3. Full compose-backed gate (Django DB, Playwright browser E2E, live Redis)

All commands below assume your current directory is **`HPCPerfStats/`** (the one with `docker-compose.yaml`).

**Test compose overlay:** Every **`tests/run_*_workflow.sh`** script sources **`tests/compose_test_cmd.sh`**, which runs **`docker-compose -f docker-compose.yaml -f tests/docker-compose.test-overlay.yaml`**. That overlay replaces the production **`hpcperfstatsdata`** bind mount (**`/opt/hpcperfstats_data/`** on the host) with a named Docker volume so local macOS/Colima machines and CI do not need operator paths under **`/opt`**. Production deploys still use **`docker-compose.app.yaml`** (or its **`.example`**) unchanged; only test workflows add the overlay.

| Step | Command | What it covers |
|------|---------|----------------|
| 1 | `tests/run_db_pytest_workflow.sh` | Resets compose volumes, builds `web`, starts **db** + **redis**, migrates dev DB, installs **Playwright/Chromium** in the container, runs **`pytest -q hpcperfstats`** (entire tree, including **`test_web_pages_browser_e2e.py`** unless you pass `--skip-browser-e2e`). |
| 2 | `tests/run_redis_cache_pytest_workflow.sh --skip-build` | Fresh compose session, **`test_redis_cache_live.py`** + archive member Redis smoke (`test_archive_members_redis_populate_single_flight_compose`) against real **Redis** (`HPCPERFSTATS_PYTEST_LIVE_REDIS=1`). |
| 3 | `tests/run_web_e2e_workflow.sh --skip-build` | Dedicated compose session: **`migrate`** + **`HPCPERFSTATS_COMPOSE_NETWORK=1`** + **`test_web_pages_e2e.py`**, **`test_web_pages_browser_e2e.py`**, **`test_nginx_static_wsgi_contract.py`** (isolated web-path parity vs step 1). |
| 4 | `tests/run_stress_host_data_workflow.sh --skip-build` | Opt-in **`host_data`** stress (`tests/stress_host_data/`): seed + **`update_metrics`**, JSON report under **`test_runs/stress/`**, default **400000** rows (override row/time-scale env vars; see section above). |
| 5 | `tests/run_pipeline_e2e_workflow.sh --skip-build` | Opt-in **full pipeline + browser** (`tests/pipeline_e2e/`): RabbitMQ ingest, **`sync_timedb once`**, **`update_metrics`**, then live **web** + Playwright endpoint matrix (see **Opt-in pipeline E2E** above). |

**Smoke orchestrator:** `tests/run_all_compose_workflows.sh` runs steps **1**, **2**, **3**, and **5** only (DB pytest, Redis live, web E2E, pipeline E2E). It does **not** include stress `host_data`, security audit, update_metrics diagnosis, or Bokeh embed browser E2E—run those scripts separately when needed.

Faster iteration after the first successful build:

```bash
tests/run_db_pytest_workflow.sh --skip-build
tests/run_redis_cache_pytest_workflow.sh --skip-build
tests/run_web_e2e_workflow.sh --skip-build
tests/run_stress_host_data_workflow.sh --skip-build
tests/run_pipeline_e2e_workflow.sh --skip-build
```

Each workflow tears down containers and **named volumes** on exit, then runs **Colima Docker cleanup** (prune unused images, build cache, volumes, and networks) unless you pass **`--keep-env`** (see per-script help). Set **`COLIMA_DOCKER_CLEANUP_SKIP=1`** to skip the prune step while still running compose teardown.

Manual cleanup from `HPCPerfStats/`:

```bash
docker-compose down -v --remove-orphans
bash tests/colima_docker_cleanup.sh
```

If a follow-up script fails with **`failed to resolve host 'db'`** inside the container, Docker networking may still be cleaning up from a previous run. Run `docker-compose down --remove-orphans` from `HPCPerfStats/` and retry, or wait a few seconds between workflows.

### 4. In-tree tools package and SPA unit tests

From `HPCPerfStats/` (git checkout; tools live at `hpcperfstats-tools/`):

```bash
cd hpcperfstats-tools && python -m pytest -q
cd ../hpcperfstats/site/frontend && npm test -- --run
```

`hpcperfstats-tools/pytest.ini` disables the Django plugin (`-p no:django`) so in-tree runs do not inherit the parent checkout’s `DJANGO_SETTINGS_MODULE`.

## Code coverage

Coverage settings live in `pyproject.toml` (`[tool.coverage.*]`). Typical commands:

```bash
# Python package (from repo root with test extras installed)
python scripts/run_tests.py --no-django --cov=hpcperfstats --cov-report=term-missing --cov-report=html

# Full tree including Django tests (needs Postgres/Redis per your settings, often via Docker)
python scripts/run_tests.py --cov=hpcperfstats --cov-report=term-missing --cov-report=html
```

HTML output is written to `htmlcov/` (gitignored by convention).

**React (Vitest + v8)** — from `hpcperfstats/site/frontend/`:

```bash
npm run test:coverage -- --run
```

Reports are written to `hpcperfstats/site/frontend/coverage/` (ignored via `hpcperfstats/site/frontend/.gitignore`).

**Bokeh embed (Vitest):** `BokehEmbed.jsx` defaults to viewport-gated embedding and a short post-`Document.idle` settle in production builds; Vitest skips deferral unless `deferEmbedUntilVisible` is set explicitly (see `src/utils/bokeh-embed-defaults.js`). Targeted runs:

```bash
cd hpcperfstats/site/frontend && npm test -- --run src/components/BokehEmbed.test.jsx src/components/HistogramThumbnails.test.jsx src/utils/bokeh-embed-defaults.test.js
```

**`hpcperfstats-tools`** (in-tree client package under this checkout):

```bash
cd hpcperfstats-tools && python -m pytest --cov=hpcperfstats_tools --cov-report=term-missing
```

## Best practices

Canonical guidance lives in **`hpcperfstats/cursor-rules/testing-best-practices.mdc`**. Summary:

### Test pyramid

| Layer | When | Runner |
|-------|------|--------|
| Unit | Pure helpers, mocked API views (`django_db(databases=[])`) | `python scripts/run_tests.py --no-django`, Vitest `npm test -- --run` |
| Integration | Postgres, Redis, Timescale | `tests/run_db_pytest_workflow.sh`, `tests/run_redis_cache_pytest_workflow.sh` |
| E2E | Browser UX, nginx, full pipeline | `tests/run_web_e2e_workflow.sh`, `tests/run_pipeline_e2e_workflow.sh` |

Prove correctness at the **narrowest** layer first; escalate only when mocks cannot exercise the contract.

### Colocation and drift guards

- **Python (general):** `test_*.py` beside modules for small leaf packages, `hpcperfstats/tests/` for cross-cutting daemons, or a dedicated `tests/` package when the subtree has many modules.
- **`analysis/metrics/`:** all pytest modules under `hpcperfstats/analysis/metrics/tests/` only — not beside `update_metrics.py` or under `lib/plot/` / `lib/gen/`.
- **Frontend:** `*.test.{ts,tsx}` colocated under `hpcperfstats/site/frontend/src/`.
- Cross-layer registries (routes, metric labels, extended-search params) need **drift tests** that fail when source and consumer lists diverge.

### Refactor for testability

Split monolithic functions when it materially reduces mock surface—lock behavior with tests before extracting helpers (`testing-best-practices.mdc` + `refactor-dedup-priorities.mdc`).

### Pre-merge matrix

| Change | Minimum run |
|--------|-------------|
| dbload/utils | `python scripts/run_tests.py --no-django` |
| Django API | Host API mock tests + `tests/run_db_pytest_workflow.sh` if DB semantics change |
| Web UI | Vitest + `tests/run_web_e2e_workflow.sh` when user-visible behavior changes |
| Metrics/ingest | Compose db pytest + pipeline E2E when payloads change |

Local git hooks: commit-stage **memray** growth smoke (`python-memory-leak-check`); tracemalloc remains offline/ad-hoc only — see **Optional memory profiling** below.
### `api.py` coverage

**100% line coverage** on `hpcperfstats.site.lib.machine.api` is enforced by host-side mock tests (LocMem cache, `django_db(databases=[])`):

- `test_api_helpers.py`
- `test_api_view_matrix.py`
- `test_api_coverage_gaps.py`
- `test_api_misc.py`
- `test_api_coverage_closure.py`

```bash
cd HPCPerfStats && PYTHONPATH=. ../.venv/bin/python -m pytest \
  hpcperfstats/site/lib/machine/tests/test_api_helpers.py \
  hpcperfstats/site/lib/machine/tests/test_api_view_matrix.py \
  hpcperfstats/site/lib/machine/tests/test_api_coverage_gaps.py \
  hpcperfstats/site/lib/machine/tests/test_api_misc.py \
  hpcperfstats/site/lib/machine/tests/test_api_coverage_closure.py \
  --cov=hpcperfstats.site.lib.machine.api \
  --cov-config=tests/coverage_api_py_line_only.ini \
  --cov-report=term-missing:skip-covered \
  --cov-fail-under=100 \
  -q
```

The dedicated `--cov-config` disables branch measurement so the gate tracks **line** coverage only (project-wide `[tool.coverage.run] branch = true` remains unchanged).

### Frontend coverage inventory (Vitest)

| Area | Status |
|------|--------|
| Pages | All pages have tests; JobDetail/JobList deepest |
| Components | All components under `src/components/` |
| Hooks | 100% (`useAsyncFetch`, `useTableSort`, `useFocusTrap`, `use-home-options`) |
| Utils | Catalog/drift guards for search params, metric labels, monitor events, robots prefixes |
| Config | `api-paths.js`, `publicRobotsAllowPrefixes.js` |

Targeted runs:

```bash
cd hpcperfstats/site/frontend
npm test -- --run src/components/PageBreadcrumbs.test.jsx
npm test -- --run src/utils/extended-search-parameters.test.js
npm run test:coverage -- --run
```

### Document history

| Date | Change |
|------|--------|
| 2026-06-05 | `api.py` line coverage complete (100% gate); removed `artifacts/api_py_coverage_baseline.md`; added `test_api_coverage_closure.py` and `tests/coverage_api_py_line_only.ini` |
| 2026-06-05 | Added best-practices section, frontend inventory, `api.py` coverage modules, new dbload/API/frontend unit tests |
| 2026-06-05 | Colima post-test cleanup: `tests/colima_docker_cleanup.sh`, `tests/colima_compose_teardown.sh`; wired into all `tests/run_*_workflow.sh` scripts |

## Test layout

| Location | Description |
|---------|-------------|
| `tests/colima_docker_cleanup.sh` | After compose workflows: prune stopped containers, unused images, build cache, volumes, and networks (Colima **`DOCKER_HOST`**). Skip with **`COLIMA_DOCKER_CLEANUP_SKIP=1`**. |
| `tests/colima_compose_teardown.sh` | Shared helper sourced by **`tests/run_*_workflow.sh`**: **`colima_compose_teardown`** runs compose **`down -v --remove-orphans`** then invokes **`colima_docker_cleanup.sh`**. |
| `tests/pip_compose_test_extras_fallback.sh` | When `pip install -e ".[test]"` fails on a bind mount, inner compose scripts source this helper so **Django 6.x** and **pytest 9+ / pytest-django 4.12+** match `pyproject.toml` (not legacy `pytest>=7` / `pytest-django>=4.5` floors). |
| `hpcperfstats/tests/test_sync_timedb_parsing_canonical.py` | Canonical stats-line ingest (semantic PMC/IMC events, no CTL/CTR eventmaps). |
| `hpcperfstats/tests/test_sync_timedb_parsing_legacy.py` | Legacy ingest path (`map_hardware_counter_vals`, hex eventmaps, KNL type aliases). |
| `hpcperfstats/tests/test_monitor_naming_resolve.py` | Dual-read probe order for `monitor_naming/resolve.py`. |
| `hpcperfstats/tests/test_monitor_analysis_typename_contract.py` | Monitor `.st_name` coverage vs `canonical.py` and roofline peak rows. |
| `hpcperfstats/tests/test_archive_compress.py` | Pure path helpers in `archive_compress.py` (detect format, tar/zst/gz siblings, member maps). |
| `hpcperfstats/tests/test_sync_timedb_startup_archive_scan.py` | Canonical startup snapshot coordinator: `begin_build`/`publish`, `wait_for_snapshot` (no `None`), deep-copy isolation, janitor blocks parallel collect (host unit). Pair with **`test_arch_no_startup_day_close_preflight_in_supervisor`** and **`test_janitor_startup_tick_discovers_and_enqueues_day_close`** in architecture/janitor tests. |
| `hpcperfstats/tests/test_sync_timedb_janitor.py` | Janitor boot **`DAY_CLOSE`** discover (`test_run_scheduled_maintenance_pass_discovers_awaiting_janitor_discover_on_startup`, `test_janitor_startup_tick_discovers_and_enqueues_day_close`), startup max inflight cap, debt budget (host unit). Quick run: `cd HPCPerfStats && ../.venv/bin/python3 -m pytest -q hpcperfstats/tests/test_sync_timedb_janitor.py -k startup`. |
| `hpcperfstats/tests/test_sync_timedb_supervisor.py` | Stall teardown: exit **124** preserved, coordinator `shutdown(wait=False)` on pool fatal (host unit). |
| `hpcperfstats/tests/test_sync_timedb_ingest_file_timeout.py` | Size-proportional `resolve_ingest_per_file_timeout_s`, long-budget WARNING (≥1800s), stall-wall vs `timeout_max_s` startup guard, `_run_ingest_timed` budget wiring (host unit). |
| `hpcperfstats/tests/test_sync_timedb_janitor.py` (`test_janitor_tick_debt_budget_excludes_scheduled_maintenance_pass`) | Janitor debt budget excludes scheduled maintenance pass duration (host unit). |
| `hpcperfstats/tests/test_sync_timedb_archive.py` (member cache) | DB-complete ingest path: identity-keyed `get_existing_archive_members_for_daily_archive` cache, `daily_archive_has_member_with_size`, ingest **Redis-first single-flight populate** (`test_ingest_sealed_path_uses_populate_not_parallel_point`, `test_ingest_sealed_single_flight_one_zstd_scan`, `test_ingest_waiters_no_local_zstd_while_lock_held`), **bad sealed day skip** (`test_classify_stream_failure_*`, `test_concurrent_waiters_no_zstd_after_day_skip`, `test_raw_stats_needs_append_false_when_day_skipped`), **degraded Redis no local scan** (`test_get_existing_archive_members_no_local_scan_when_degraded`), **Redis outage re-raise** (`test_raw_stats_path_needs_tar_append_reraises_redis_unavailable`), L1 skip LRU (`test_ingest_skipped_calendar_days_lru_not_full_clear`), sealed point lookup (`test_sealed_archive_member_has_exact_size_early_exit`), `invalidate_daily_archive_members_cache` (`test_daily_archive_members_cache_*`, `test_raw_stats_needs_append_uses_cache`, `test_single_member_early_exit_finds_match`, `test_dedupe_sealed_daily_archive_last_resort`, `test_get_existing_archive_members_uses_redis_l2`). Drift guard: **`sync-timedb-ingest-pool-io-coordination.mdc`**. |
| `hpcperfstats/tests/test_sync_timedb_archive_members_redis.py` | Redis L2 single-flight populate, incremental HASH + `complete`/`progress` signals, **stall-aware waiters** (`test_wait_for_member_waits_until_complete`, `test_wait_for_member_stalls_without_progress`, `test_redis_members_no_false_negative_until_complete`), **lock renewal on HSET flush** (`test_populate_lock_renewed_on_flush`), `dedupe_hint`, sealed-scan wiring + scan error re-raise (`test_populate_redis_members_from_sealed_scan_wires_stream_fn`, `test_stream_re_raises_archive_members_redis_unavailable_from_on_member`, `test_stream_logs_generic_failure`, `test_populate_scan_failed_includes_sealed_path`), hard-fail when Redis required (host `FakeRedis`; compose: **`test_archive_members_redis_populate_single_flight_compose`** via `run_redis_cache_pytest_workflow.sh`). |
| `hpcperfstats/tests/test_sync_timedb_supervisor.py` (stall/teardown) | `test_stall_teardown_preserves_exit_124_not_137` (imap stall must not be masked by exit 137 during `finally` finalize); `test_finalize_invalidates_members_cache` (append finalize clears member cache); **`test_sync_timedb_exits_on_redis_unavailable_during_ingest`** (fatal exit 1); **`test_parse_stats_file_payload_need_archival_false_on_day_skip`**. |
| `hpcperfstats/tests/test_sync_acct.py` | Accounting ingest (`sync_acct_from_content`, restricted queues, bulk fallback, cache notify) with mocked ORM. |
| `hpcperfstats/tests/test_listend_drain.py` | RabbitMQ drain loop (ack/nack, empty queue) with mocked pika. |
| `hpcperfstats/site/lib/machine/tests/test_update_metrics_telemetry_coverage.py` | Window-coverage readiness helpers and legacy fallback (host unit mocks). |
| `hpcperfstats/site/lib/machine/tests/test_update_metrics_telemetry_coverage_compose.py` | Compose-backed defer→ready when early `host_data` is inserted (`tests/run_db_pytest_workflow.sh`). |
| `hpcperfstats/tests/` | Non-Django unit/integration tests (config parsing, service startup/health, listend behavior, sync helpers, cache/date/print/file-locking helpers, XALT models, API key mobile checks, dbload `zstd`/row-builder helpers; Django’s `django.utils.timezone.utc` is restored in **`hpcperfstats_site/settings.py`** for Django 5+). `test_dbload/lib/dbload/lib/conf_parser.py` covers `get_effective_cores()`, `get_metrics_pool_process_count()`, default `total_cores=40`, `get_parallel_db_prefetch_max()` / `get_api_small_executor_max_workers()` (defaults **4**), DB `CONN_MAX_AGE` and PostgreSQL `OPTIONS` builders, `get_sync_ingest_pool_processes()` / `get_sync_archive_pool_processes()` (archive slots are the sole INI knob `sync_archive_pool_processes`), and `get_sync_archive_members_cache_enabled()` / `get_sync_archive_members_cache_max_entries()`. `test_pg_connection_stats_command.py` exercises the `pg_connection_stats` management command with a mocked DB connection (no live Postgres). `test_numa_topology.py`, `test_compose_cpu_layout.py`, and `test_apply_compose_numa_pinning.py` cover sysfs NUMA parsing, **single-node** topology, responsive linear CPU partitions, and `scripts/apply_compose_cpu_pinning.py --dry-run`. `test_sync_timedb_supervisor.py` exercises `run_sync_timedb_supervisor_loop` (empty-queue sleep, ingest wave, second empty sleep) with mocked multiprocessing pools and rescans. `test_sync_timedb_archive.py` includes deferred-archive / atomic-seal helpers, `tar tf` integrity checks, zstd gzip restore, tar dedupe (largest wins per path), and helpers used for scheduled archive maintenance + raw-file removal (`get_existing_archive_members_for_daily_archive`, `remove_verified_archived_raw_files`, `validate_sealed_daily_archive_for_raw_removal`); integration tests call real `zstd` and skip when `zstd` is not on `PATH`. `test_monitor_analysis_typename_contract.py` asserts monitor `stats_type.st_name` values stay aligned with `INTEL_IMC_STATS_TYPES`, `ARM_IMC_STATS_TYPES`, and `INTEL_CORE_PMC_TYPES_ORDERED` (skips if `monitor/src` is absent). |
| `hpcperfstats/analysis/metrics/tests/` | Metrics/plot/gen unit tests (summary/roofline behavior, roofline peak inference from `host_data.type` schema keys, hover tooltips, shared job-window parsing, metrics helpers, Bokeh embed sizing). `test_metrics_telemetry_bounds.py` locks `telemetry_first_time` / `telemetry_last_time` persistence during metrics batch writes. `test_per_interval_rate.py` covers node-imbalance variants (DRAM, LNET, GPU util/tensor) and GPU peak helpers. `test_job_for_metrics.py` and `test_summaryplot_no_data.py` exercise metrics/summaryplot edge cases. `test_job_metric_display_labels.py` and `test_metrics_add_arrays.py` cover label maps and small `metrics.py` helpers. `test_bokeh_job_embed.py` is a pure-Python unit test for `bokeh_job_embed.figure_embed_kw` (no Django). |
| `hpcperfstats/site/lib/machine/tests/` | Django + web tests (ORM/query/update helpers, job detail file-system llite vs NFS fallback, security headers, API/misc endpoints, SPA rendering, page and browser E2E tests). **`test_api_helpers.py`** and **`test_api_view_matrix.py`** add host-side `api.py` helper and view coverage with mocks. Includes `test_type_detail_api.py`, which asserts type-detail `host_data` ORM SQL does not reference `jid` (job scope is start/end time + accounting hosts) with `django_db(databases=[])` so it does not need a live DB. `test_api_coverage_gaps.py` hits additional `api.py` branches (cache invalidation, `host_plot`, job monitor, `sacct_ingest`, `job_detail`, `job_plots`) with mocks and locmem cache—no Postgres host required. `test_oauth2.py`, `test_cache_middleware.py`, `test_renderers.py`, and `test_update_xalt.py` cover OAuth helpers, dynamic cache TTL middleware, JSON NaN sanitization, and the XALT log script loop. `test_metrics.py` includes `test_job_metric_short_labels_cover_catalog`, which asserts every `job_metrics_catalog_entries()` metric has a matching entry in `job_metric_display_labels.JOB_METRIC_SHORT_LABELS` (parity with the frontend short-label map). `test_job_plot_artifacts.py` covers gzip-serialized Bokeh `json_item` persistence (`job_plot_artifact`), fingerprinting, and invalidation (requires Postgres like other `django_db` machine tests). **`test_public_api.py`** covers anonymous **`GET/POST /api/pub/cluster-dashboard/`** with `django_db(databases=[])` + mocks so host pytest does not need Compose Postgres. **`test_public_metrics_artifacts.py`** runs EF formula checks under **`machine_unit_mock`** off Compose; refresh/invalidate integration rows still require Postgres (`HPCPERFSTATS_COMPOSE_NETWORK=1` via `tests/run_db_pytest_workflow.sh`). `test_job_list_performance_summary.py` covers job list `performance` labels / `sort_rank` and ORM `performance_sort_rank` ordering vs `summarize_performance()`. `test_redis_cache_live.py` hits Django `RedisCache` against a live Redis when `HPCPERFSTATS_PYTEST_LIVE_REDIS=1` (`tests/run_redis_cache_pytest_workflow.sh`); otherwise those tests skip. `test_query_utils.py` includes `get_job_list_order_by` allowlist (`performance_sort_rank`; legacy `has_metrics` is rejected). **API:** `/api/jobs/` list entries expose `performance` (not `has_metrics`); sort with `order_by=performance_sort_rank` or `-performance_sort_rank`. |
| `hpcperfstats/site/lib/machine/tests/test_job_detail_staff_sample_count.py` | Staff-only `staff_metrics_distinct_time_count` on `job_detail` JSON (mocked ORM; no live DB required). |
| `hpcperfstats/site/frontend` | React SPA unit tests (Vitest): `npm test` from that directory. Vitest picks up `*.test.jsx` / `*.test.js` under `src/` (pages, components, utils), including `api.test.js` (fetch/CSRF/401 paths), `components/ExtendedSearch.test.jsx`, `normalize-job-list-histogram-entry.test.js`, `table-sort-a11y.test.js`, `job-list-route-title-context.test.js`, `Search.test.jsx` (browse-by-time **Calendar** tab first and selected by default), `JobList.test.jsx` (includes narrow-viewport Jobs/Charts tab behavior), `JobDetail.test.jsx` (job detail **Job data** inner tabs—including per-plot tabs (Summary, Heatmap, rooflines) before Bokeh assertions—progressive plot loading, and metrics tab single- vs two-table layout), **`components/LayoutPub.test.jsx`** (public `/pub/` navbar: branding, cluster label, **Login to see individual job data** → `login_prompt?next=/machine/`), **`pages/PageClusterDashboard.test.jsx`** (`PubDashboardBundleContext`, expansion-factor tab panel, yearly-before-monthly anchors, `pub-expansion-factor-*` Bokeh ids), `src/utils/jobMetricDisplayLabels.test.js` (short labels for job-level metrics table), and `useDocumentTitle.test.js`. `src/setupTests.js` stubs `HTMLElement.prototype.scrollIntoView` for jsdom. |
| `hpcperfstats-tools/tests/` | Client CLI/API helpers: `test_api_client.py`, `test_api_auth_headers.py`, `test_job_dataframe.py`, `test_sacct_gen_security.py`, plus `test_config.py` (INI `base_url`), `test_api_key_cache.py` (`~/.hpcperfstats-api` parsing), and `test_jobstats_cli.py` (`jobstats` formatting and `main()` exit codes). Run from the `hpcperfstats-tools` directory with `python -m pytest`. |

### Monitor event metadata (frontend tooltips)

When approved monitor changes add or rename `host_data.event` strings, regenerate the bundled catalog from the repo root:

```bash
python3 hpcperfstats/site/frontend/src/utils/generate-variable-metadata-monitor-events.py
```

Then follow `HPCPerfStats/hpcperfstats/cursor-rules/variable-metadata-*.mdc` for merging into `variableMetadata.js` and operator docs (`docs/regenerate_monitor_variables_catalog.py`, etc.).

## Test runners

### `scripts/run_tests.py` (default local runner)

`scripts/run_tests.py` wraps `pytest` and is the easiest default runner:

- `python scripts/run_tests.py --no-django` ignores `hpcperfstats/site/lib/machine/tests`
- `python scripts/run_tests.py` runs `pytest -v hpcperfstats` by default
- extra pytest args are forwarded (for example `python scripts/run_tests.py -k metrics`)

### `tests/run_web_e2e_workflow.sh` (compose E2E runner)

Use this for web-page E2E modules plus the nginx/WSGI route contract:

- `hpcperfstats/site/lib/machine/tests/test_web_pages_e2e.py` (**PostgreSQL on Compose**: root **`conftest.py`** applies **`django_db`** to **`site/lib/machine/tests`** by default when unset; **`pytest_collection_modifyitems`** skips Postgres-backed machine tests unless **`HPCPERFSTATS_COMPOSE_NETWORK=1`**. This workflow passes **`HPCPERFSTATS_COMPOSE_NETWORK=1`** into the **`web`** container and runs **`manage.py migrate --noinput`** before pytest.)
- `hpcperfstats/site/lib/machine/tests/test_web_pages_browser_e2e.py` (Playwright: Django stub server + **`/machine/*`** and **`/pub/*`** SPA shells return **404** from WSGI per nginx ownership contract)
- `hpcperfstats/site/hpcperfstats_site/tests/test_nginx_static_wsgi_contract.py` (**`/static/`**, **`/machine/`**, **`/pub/`** WSGI 404 contract + **`/robots.txt`** nginx static / WSGI 404 + edge HSTS/framing headers + OCSP/CSP include contracts)
- **`test_bokeh_job_list_embed_browser_e2e.py`** is **not** run here (needs CDN and optionally a **Next-built** static tree under **`hpcperfstats_site/static/frontend/`**). Run it separately—compose-backed **`pytest`** on **`web`** after **`pip install ".[test]"`** + **`playwright install chromium`**, or on the host with Playwright installed. For the bundled-Bokeh test, run **`npm run build`** (full export, not `build:prod`) in `hpcperfstats/site/frontend` first. Fixtures: `hpcperfstats/site/frontend/test/fixtures/`. See module docstring for fixture regeneration after **`job_hist`** / queue bar chart changes.

The workflow script handles Docker lifecycle and runs **`migrate`** plus those modules in one session:

```bash
tests/run_web_e2e_workflow.sh
```

Useful options:

```bash
# Seed/recreate test data before E2E tests
tests/run_web_e2e_workflow.sh --seed-cmd "python your_seed_script.py"

# Keep services/volumes running after test run
tests/run_web_e2e_workflow.sh --keep-env

# Skip image rebuild or Playwright browser install when appropriate
tests/run_web_e2e_workflow.sh --skip-build --skip-playwright-install
```

Equivalent seed environment variable:

```bash
E2E_SEED_CMD="python your_seed_script.py" tests/run_web_e2e_workflow.sh
```

### `tests/run_db_pytest_workflow.sh` (compose full Python suite, macOS / Linux)

Use this when you need **Django / Postgres** tests with `host=db` on the Compose network (for example on macOS, where host-side pytest cannot resolve the `db` hostname from `hpcperfstats.ini.example`).

From the `HPCPerfStats/` directory (where `docker-compose.yaml` lives):

```bash
tests/run_db_pytest_workflow.sh
```

The script copies `hpcperfstats.ini.example` to `hpcperfstats.ini` if the latter is missing (required for the Compose bind mount), resets compose volumes (unless `--keep-env`), starts **db** and **redis**, runs `manage.py migrate` on the dev database, installs Playwright in the container, and runs `pytest -q hpcperfstats`.

Useful options:

```bash
# Faster iteration: skip browser E2E module and Playwright install
tests/run_db_pytest_workflow.sh --skip-browser-e2e

# Skip image rebuild
tests/run_db_pytest_workflow.sh --skip-build

# Keep db/redis volumes after the run
tests/run_db_pytest_workflow.sh --keep-env

# Optional seed inside the web container after migrate
tests/run_db_pytest_workflow.sh --seed-cmd "python path/in/repo/seed.py"
# or: DB_TEST_SEED_CMD="python ..." tests/run_db_pytest_workflow.sh

# Skip migrate on the dev database (pytest still creates its own test_* DB)
tests/run_db_pytest_workflow.sh --skip-migrate

# Forward pytest arguments (use -- if an option starts with -)
tests/run_db_pytest_workflow.sh -- -k job_plot
```

**sync_timedb stall regression battery (host, mandatory before stall-related PR close):**

```bash
cd HPCPerfStats
tests/run_sync_timedb_regression_battery.sh
```

Logs to **`test_runs/day-close-loop-regression-battery-<timestamp>.log`**. Covers handoff, `archive_finalize`, chunk gate, `test_arch_*`, `ingest_stall_watchdog`, `oldest_day_unprocessed_frozen`, prewarm L1/cold-Redis (`l1_cold_redis`, `prewarm_retries`), pending-reconcile fingerprint refresh, and **`--jid` ingest-only** (`test_sync_timedb_jid.py`). See **`sync-timedb-change-regression-gate.mdc`** and **`docs/OPERATOR_SYNC_TIMEDB_STALL_VERIFY.md`** (T0/T1/T2 tiered verify on backlog sites; `--jid` uses the smoke note there instead of stall tiers).

**sync_timedb `--jid` one-shot (host unit):**

```bash
cd HPCPerfStats
../.venv/bin/python3 -m pytest -q hpcperfstats/tests/test_sync_timedb_jid.py
```

Operator CLI (pipeline image, after `job_data` exists): `sync_timedb.py --jid <JID>` — ingest-only (±1h pad around job start/end, plus one earlier and one later raw stats file per host); no archival/day-close. See **`docs/OPERATOR_SYNC_TIMEDB_STALL_VERIFY.md`** → *sync_timedb --jid smoke*.

**sync_timedb ingest archive member cache (host, no compose):** on cloud-sync checkouts prefer `scripts/run_tests.py --no-django` (direct `pytest` on ProtonDrive can hang during collection). Targeted regressions:

```bash
cd HPCPerfStats
../.venv/bin/python3 scripts/run_tests.py --no-django \
  hpcperfstats/tests/test_sync_timedb_archive_members_redis.py \
  hpcperfstats/tests/test_sync_timedb_archive.py -k "members_cache or raw_stats_needs_append or single_member or invalidate_daily or dedupe_sealed or redis_l2 or day_skip or classify_stream or ingest_sealed or concurrent_waiters" \
  hpcperfstats/tests/test_sync_timedb_janitor.py -k "dedupe_hint" \
  hpcperfstats/tests/test_zstd_cli.py -k ingest_member_scan_zstd_without_nice \
  hpcperfstats/tests/test_sync_timedb_supervisor.py -k "stall_teardown_preserves_exit_124 or finalize_invalidates_members" \
  hpcperfstats/tests/test_multiprocessing_pool_health.py -k abort_if_pool_workers_dead \
  hpcperfstats/tests/test_conf_parser.py::test_sync_pipeline_tunable_defaults_and_overrides
```

Compose smoke for the same contracts:

```bash
tests/run_db_pytest_workflow.sh -- \
  hpcperfstats/tests/test_sync_timedb_archive_members_redis.py \
  hpcperfstats/tests/test_sync_timedb_archive.py::test_daily_archive_members_cache_hit_skips_second_scan \
  hpcperfstats/tests/test_sync_timedb_supervisor.py::test_stall_teardown_preserves_exit_124_not_137 \
  hpcperfstats/tests/test_sync_timedb_supervisor.py::test_finalize_invalidates_members_cache
```

Implementation detail: `tests/run_db_pytest_inner.sh` runs inside the `web` container via `compose_run_inner_script` in `tests/compose_test_cmd.sh` (streams the inner script from the host via `bash -s` stdin, because virtiofs bind mounts such as ProtonDrive can return `Operation not permitted` when the container opens `.sh` paths on the mount). Workflows call `compose_prepare_bind_mount` before compose **build**, **up**, and **run**: when the checkout path looks like a cloud-sync mount (`*ProtonDrive*`, `*CloudStorage*`, `*iCloud*`), the repo is **rsynced** to `$HOME/.cache/hpcperfstats-compose/stable` by default (Colima shares `$HOME` into the VM; macOS `/tmp` is not bind-mountable unless you start Colima with `--mount /tmp:w` and set `COMPOSE_BIND_MOUNT_USE_TMP=1` or `COMPOSE_BIND_MOUNT_BASE_DIR=/tmp/hpcperfstats-compose`). Compose then uses `--project-directory` on that work copy so image builds do not stream the virtiofs checkout as Docker context. The same directory is bind-mounted to `/home/hpcperfstats` for pytest and for `run_web_e2e_workflow.sh` / `run_pipeline_e2e_workflow.sh`. On teardown, `test_runs/` artifacts from the work copy are rsynced back to the real checkout. `compose_run_inner_script` also overlays `hpcperfstats.ini` from the same mount so `docker-compose.app.yaml`’s `./hpcperfstats.ini` bind (still resolved on the cloud-sync checkout) does not shadow the work copy inside the container. Set `COMPOSE_BIND_MOUNT_WORK_COPY=0` to force mounting the checkout directly (for non-cloud paths or debugging). Set `COMPOSE_BIND_MOUNT_FORCE_WORK_COPY=1` to always rsync. The work copy defaults to `$HOME/.cache/hpcperfstats-compose/stable` (incremental rsync; set `COMPOSE_BIND_MOUNT_KEEP_WORKDIR=0` to delete it on teardown). Use `--skip-build` after the first successful image build; `COMPOSE_BIND_MOUNT_SKIP_BUILD=1` selects a smaller rsync set (~minutes on ProtonDrive). Extra pytest arguments are passed via a mounted temp file (`/tmp/hpcperfstats_pytest_extra_args`). The same mount pattern is used by `run_redis_cache_pytest_workflow.sh`, `run_stress_host_data_workflow.sh`, `run_update_metrics_diagnosis_workflow.sh`, `run_web_e2e_workflow.sh`, and `run_pipeline_e2e_workflow.sh`.

### `tests/run_redis_cache_pytest_workflow.sh` (live Redis cache integration)

Runs `hpcperfstats/site/lib/machine/tests/test_redis_cache_live.py` and **`hpcperfstats/tests/test_sync_timedb_archive_members_redis.py::test_archive_members_redis_populate_single_flight_compose`** inside the `web` container with **`HPCPERFSTATS_PYTEST_LIVE_REDIS=1`**, so Django keeps **`RedisCache`** against the Compose **redis** service instead of switching to `LocMemCache` during pytest.

```bash
tests/run_redis_cache_pytest_workflow.sh
```

Without that env var, the live Redis tests are **skipped** so normal `python scripts/run_tests.py` on a laptop does not require Redis.

After changing the Compose **redis** image tag, confirm the running server is Redis 8.x:

```bash
docker compose exec redis redis-cli INFO server | grep redis_version
```

`test_redis_cache_live.py` also asserts major version `>= 8` when the live Redis workflow runs.

Options: `--keep-env`, `--skip-build`. Forward pytest args the same way as the DB workflow (`-- -vv`).

## Accessibility (WCAG 2.2 AA target)

The SPA and standalone HTML pages aim for **WCAG 2.2 Level AA** for in-app flows (keyboard, screen readers, zoom, contrast). **Bokeh canvas plots** and the **OAuth provider** have inherent limits; the UI mitigates with text alternatives, landmarks, and documented manual checks.

**Automated (frontend — Vitest + jest-axe)**

`jest-axe` and `axe-core` are **devDependencies** only. They are imported from `*.test.{ts,tsx}`, [`test/vitest/setupTests.ts`](hpcperfstats/site/frontend/test/vitest/setupTests.ts), and [`test/vitest/axe-test-utils.ts`](hpcperfstats/site/frontend/test/vitest/axe-test-utils.ts); they are **not** part of the Next production bundle. The Docker **runtime** image does not run `npm install` for the app and excludes `node_modules/` and `frontend/test/` from the build context; the frontend builder uses **`npm run build:prod`**, so axe and test-only static routes are not shipped with production static assets.

Colocated page/component tests call `axeSeriousViolations()` (WCAG 2.x / 2.1 AA tag scope, asserting **no serious or critical** violations) for: `Layout` (including Extended Search open), `JobList` (including charts tab with histograms), `JobDetail`, `Search`, `PageApiKey`, `ExtendedSearch` (dialog shell), and `HistogramThumbnails` (thumbnail + open popover). New or materially changed page shells must extend this set per **`frontend-a11y-regression.mdc`**.

```bash
cd hpcperfstats/site/frontend && npm test -- --run
```

**Automated (Playwright — Python E2E)**

A **vendored** [`axe.min.js`](hpcperfstats/tests/fixtures/axe-core/axe.min.js) (pinned version in that folder’s `README.md`) is injected at test time by [`hpcperfstats/tests/playwright_axe.py`](hpcperfstats/tests/playwright_axe.py). This avoids relying on `node_modules` inside the `web` container, where Node dev dependencies are not installed.

- **Harness only:** [`hpcperfstats/site/lib/machine/tests/test_web_pages_browser_e2e.py`](hpcperfstats/site/lib/machine/tests/test_web_pages_browser_e2e.py) runs axe on a minimal empty HTML document (validates the Playwright helper after `robots.txt` checks). It does **not** scan the React SPA.
- **Real SPA axe (compose):** pipeline browser phase (`tests/run_pipeline_e2e_workflow.sh --with-browser`) — [`tests/pipeline_e2e/test_a11y_axe_browser.py`](tests/pipeline_e2e/test_a11y_axe_browser.py) scans `_AXE_SMOKE_PATHS` (`/machine/`, `/machine/jobs/`, `/machine/home/`, `/machine/api-key`, plus one job detail URL) and Extended Search open on the job list; [`tests/pipeline_e2e/test_job_detail_browser.py`](tests/pipeline_e2e/test_job_detail_browser.py) runs axe on the live job detail SPA.

**Manual / optional**

Browser extensions or an external audit pipeline remain useful for full-page audits beyond what CI runs.

**Manual spot-check (each release or major UI change)**

Treat the following as a **release gate** alongside automated tests: run through the checklist below before tagging or deploying UI-heavy changes.

- Keyboard only: Tab through navbar, **Extended search** dialog (Escape closes, focus returns), **Find Job**, tables, pagination, job detail **Job data** plot tabs.
- Screen reader: VoiceOver (Safari) or NVDA (Firefox)—**Search jobs** → job list → job detail; confirm **route focus** lands on the page heading after navigation.
- Zoom: browser **200%** on job list and job detail; tables scroll inside `.table-responsive` where needed.

**Standalone pages**

- `/machine/api-key` (React): `<main>`, confirm before key rotation, live region when a new key is shown.
- Django **admin**: branding includes a link back to `/machine/`.

## Compose CPU pinning script (Linux hosts)

On a deployment machine, regenerate optional `docker-compose.cpu-pinning.*.yaml` fragments (see **`docs/DEPLOY_CONCURRENCY_AND_NUMA.md`**):

```bash
export HPCPERFSTATS_INI=/path/to/hpcperfstats.ini
python scripts/apply_compose_cpu_pinning.py --dry-run   # preview infra + app YAML
python scripts/apply_compose_cpu_pinning.py              # writes both fragment files
python scripts/apply_compose_cpu_pinning.py --inactive   # reset fragments to services: {}
```

Unit tests: `PYTHONPATH=. pytest -q hpcperfstats/tests/test_numa_topology.py hpcperfstats/tests/test_compose_cpu_layout.py hpcperfstats/tests/test_apply_compose_numa_pinning.py`.

## Requirements

- **General**: Python 3.12+, `pip install -e ".[test]"`.
- **Django tests**: For a full run matching production hostnames (`db`, `redis`), use `tests/run_db_pytest_workflow.sh`. Host-side `python scripts/run_tests.py` needs a reachable Postgres matching `HPCPERFSTATS_INI` **`[DEFAULT]`** PostgreSQL keys (and typically `host=localhost` with a published port, not `host=db`).
- **Live Redis cache tests**: Optional; use `tests/run_redis_cache_pytest_workflow.sh` (sets `HPCPERFSTATS_PYTEST_LIVE_REDIS=1`).
- **Browser E2E tests**: Playwright/Chromium tooling (installed by the DB workflow or E2E workflow script unless skipped).

## Optional memory profiling (development)

**Commit-stage hard gate (memray):** when staged paths match
`^(hpcperfstats/|scripts/).*\.py$`, pre-commit runs
`scripts/run_commit_memory_leak_check.py` (hook id `python-memory-leak-check`).
It exercises curated **no-Django** workloads under memray, discards warm-up
iterations, and **hard-fails** if late-window mean heap growth or peak/leaked
bytes exceed fixed ceilings. Requires workspace venv with `pip install -e ".[dev]"`
(memray is in the `[dev]` extra). Exit `2` with an install hint if memray is
missing. Manual run:

```bash
cd HPCPerfStats
../.venv/bin/python3 scripts/run_commit_memory_leak_check.py
# or:
pre-commit run python-memory-leak-check --all-files
```

Set `HPCPERFSTATS_MEMORY_LEAK_CHECK_LOG=1` for per-workload measurement lines.
This is the **only** commit hard gate for memory growth — do not add a second
tracemalloc hard-fail hook alongside it.

**Offline / ad-hoc only (not hooks):** use these on a machine with DB/Redis
available (for example the Docker Compose `web` service) when investigating RSS
growth in long-lived workers or CLI jobs.

**tracemalloc** (stdlib) around a focused pytest node:

```bash
PYTHONPATH=. python -X tracemalloc=25 -m pytest -q \
  hpcperfstats/site/lib/machine/tests/test_job_plots_timeout.py \
  --tb=no 2>&1 | tail -20
```

**memray flamegraphs** when investigating a hook failure or a single test file:

```bash
PYTHONPATH=. memray run -m pytest -q hpcperfstats/site/lib/machine/tests/test_job_plots_timeout.py
memray flamegraph memray-*.bin
```

For ingestion-style workloads, run `memray` against a short `sync_timedb` invocation or a targeted dbload test module instead of the full archive scan.

**Static analysis (local git hooks):** install dev extras and register hooks from the git checkout root:

```bash
cd HPCPerfStats
pip install -e ".[dev]"
./scripts/install-git-hooks.sh
```

**Pre-commit** (staged files): Ruff `F401`/`F841`/`F811` on `hpcperfstats/`, `cursor-hooks/`, `scripts/`; ESLint on staged `hpcperfstats/site/frontend` TypeScript; python def inventory `--check`; **memray** curated memory-leak smoke (`python-memory-leak-check`) on staged `hpcperfstats/` / `scripts/` Python.

**Pre-push:** frontend `npm run typecheck` and `npm run lint:dead` (knip); `vulture hpcperfstats scripts/vulture_whitelist.py --min-confidence 80`.

**Plan close (required):** implementation plans are not done until **both** hook stages are green (`plan-creation-contract.mdc` / `PLAN_TEMPLATE.md` todo `git-hooks-pre-close`). From the git checkout root:

```bash
pre-commit run --all-files
pre-commit run --hook-stage pre-push --all-files
```

Does not require creating a commit or pushing — only running the hook suites and fixing failures.

Manual equivalents:

```bash
# Python unused imports/variables
../.venv/bin/ruff check hpcperfstats cursor-hooks scripts --select F401,F841,F811
../.venv/bin/ruff check hpcperfstats-tools --select F401,F841,F811

# Python dead symbols (high confidence)
../.venv/bin/vulture hpcperfstats scripts/vulture_whitelist.py --min-confidence 80

# Frontend
cd hpcperfstats/site/frontend
npm run typecheck
npm run lint
npm run lint:dead
```

Host pytest drift guards: `pytest -q hpcperfstats/tests/test_static_analysis.py`.

Use `ruff check … --fix` only after reviewing the diff (tests and dynamic imports can confuse static analysis).

## Security scanning (optional)

See **[`docs/SECURITY_AUDIT.md`](SECURITY_AUDIT.md)** for the latest memo and **[`docs/SECURITY_REMEDIATION_BACKLOG.md`](SECURITY_REMEDIATION_BACKLOG.md)** for follow-ups.

**Python (CVE audit on an environment):** install and run `pip-audit` against the same dependency set you ship (prefer the Docker build / production install; a raw `pip freeze` from a dev machine may include extra tools):

```bash
pip install pip-audit
pip-audit -r <(pip freeze)
```

**Production-context audit workflow (recommended):**

```bash
cd HPCPerfStats
tests/run_security_audit_workflow.sh
```

This runs `pip-audit` inside the compose `web` image and runs frontend `npm audit`.

**Frontend:**

```bash
cd hpcperfstats/site/frontend && npm audit
```

**CI cadence:** `.github/workflows/security-audit.yaml` runs this workflow weekly and on pull requests that touch dependency/security-audit files.

**CSP report endpoint:** regression tests live in `hpcperfstats/site/hpcperfstats_site/tests/test_csp_report_endpoint.py` (POST without CSRF token must succeed).

## Notes

- `pyproject.toml` exposes canonical runner paths under `[tool.hpcperfstats.testing]`: `web_e2e_runner`, `db_pytest_runner`, `redis_cache_pytest_runner`.
- Prefer the compose E2E runner for page-level tests so DB/Redis/networking match expected service topology.
