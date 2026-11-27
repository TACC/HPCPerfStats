# Run tests

This page shows how to run the HPCPerfStats test suites, from the host unit run to the full podman-compose matrix.
It is for developers working in the git checkout; use it when you change code, tests or test workflows.

The test files, markers and layers are in [Test layout](../../reference/test-layout.md).
Local run logs and workflow artifacts go to `test_runs/` (gitignored; see `test_runs/README.md`).

## Prerequisites

Run every command from the git checkout `HPCPerfStats/` (the directory with `pyproject.toml` and `docker-compose.yaml`).
The host Python environment is the workspace virtualenv one level up, `../.venv`, built with Python 3.14+.

- Install the test extras once: `../.venv/bin/pip install -e ".[test]"`.
  Static analysis and the memory gate need `../.venv/bin/pip install -e ".[dev]"` (memray is in the `[dev]` extra).
  Do not install `.[image-build]` into the host `.venv`.

- Compose-backed tests run with unprivileged rootless Podman and `podman-compose`.
  The shared adapter `scripts/lib/podman_runtime.sh` fails closed unless every container storage, cache and temp path is under `/data`.
  Set up Podman as in [Install HPCPerfStats](../../tutorials/install-hpcperfstats.md), then verify it:

  ```bash
  F='{{.Host.Security.Rootless}} {{.Store.GraphRoot}}'
  F="$F {{.Store.VolumePath}}"
  podman info --format "$F"
  podman-compose version
  ```

  Expect rootless `true` and graph, image, volume, cache and temp paths below `/data/podman/$USER`.
  Host npm, pip, Playwright, XDG and temporary paths resolve below `/data/user/$USER`.

- Frontend tests need `npm ci` in `hpcperfstats/site/frontend`.

- Browser end-to-end tests need Playwright and Chromium; the DB and web workflows install them in the container unless you skip them.

## Run the host suite

`scripts/run_tests.py` wraps `pytest` and is the default local runner:

```bash
# Tests that need no database (deselects compose_db tests)
../.venv/bin/python3 scripts/run_tests.py --no-django

# Full pytest collection (default-DB tests skip on the host)
../.venv/bin/python3 scripts/run_tests.py
```

- `--no-django` passes `-m "not compose_db"`, defaults collection to `hpcperfstats` and uses quiet output.
  It runs only the tests that need no Postgres, Valkey or compose, so a green run is not a full check.
- With no options the runner runs `pytest -v hpcperfstats`.
- Extra arguments go to pytest, for example `python scripts/run_tests.py -k metrics`.

You can also run `pytest` directly:

```bash
# Unit-only path
../.venv/bin/python3 -m pytest -q hpcperfstats/tests

# Django app tests (requires DB/settings)
../.venv/bin/python3 -m pytest -q \
  hpcperfstats/site/lib/machine/tests
```

Host `pytest` needs no Postgres: tests that need the default PostgreSQL database are marked `compose_db` and skip unless `HPCPERFSTATS_COMPOSE_NETWORK=1`, which the compose workflows set inside `web`.
The markers are in [Test layout](../../reference/test-layout.md#markers-and-environment-gates).

## Run the complete check

A full check runs every runner below, in this order.
`scripts/run_tests.py --no-django` alone is not one: it deselects every test that needs the default database.

```bash
# 1. Host suite (default-DB tests skip on the host)
../.venv/bin/python3 scripts/run_tests.py
# 2. Frontend unit tests, then the full static export that the
#    Bokeh browser workflow serves
(cd hpcperfstats/site/frontend && npm ci && npm test -- --run)
(cd hpcperfstats/site/frontend && npm run build)
# 3. hpcperfstats-tools package
(cd hpcperfstats-tools && ../../.venv/bin/python3 -m pytest -q)
# 4. Compose workflows, logged to
#    test_runs/test_run_log_podman_compose.md
tests/run_all_compose_workflows.sh
# 5. Git hook suites (commit stage, then push stage)
../.venv/bin/pre-commit run --all-files
../.venv/bin/pre-commit run --hook-stage pre-push --all-files
```

`hpcperfstats-tools/pytest.ini` disables the Django plugin (`-p no:django`), so the tools run does not inherit the checkout's `DJANGO_SETTINGS_MODULE`.
Monitor (C) tests are separate: see `monitor/README.md`.

## Run the compose workflows

`tests/run_all_compose_workflows.sh` (`[log-path] [--skip-build]`) runs the eight workflows in this order and logs to `test_runs/test_run_log_podman_compose.md`:

- `tests/run_db_pytest_workflow.sh`: Resets compose volumes, builds `web`, starts `db` and `valkey`, migrates the dev DB, installs Playwright/Chromium, runs `pytest -q hpcperfstats` (including `test_web_pages_browser_e2e.py`)
- `tests/run_valkey_cache_pytest_workflow.sh`: `test_valkey_cache_live.py` against real Valkey (`HPCPERFSTATS_PYTEST_LIVE_VALKEY=1`)
- `tests/run_web_e2e_workflow.sh`: `migrate`, then `test_web_pages_e2e.py`, `test_web_pages_browser_e2e.py` and `test_nginx_static_wsgi_contract.py` with `HPCPERFSTATS_COMPOSE_NETWORK=1`
- `tests/run_pipeline_e2e_workflow.sh`: Opt-in full pipeline and browser run (`tests/pipeline_e2e/`): RabbitMQ ingest, `sync_timedb once`, `update_metrics`, then live `web` and the Playwright endpoint matrix
- `tests/run_update_metrics_diagnosis_workflow.sh`: Mixed-scale `update_metrics` phase timings ([Diagnose update_metrics](diagnose-update-metrics.md))
- `tests/run_stress_host_data_workflow.sh`: 400,000-row `host_data` orchestrator smoke (`tests/stress_host_data/`)
- `tests/run_security_audit_workflow.sh`: pip-audit and npm audit ([Scan dependencies](#scan-dependencies))
- `tests/run_bokeh_browser_workflow.sh`: `test_bokeh_job_list_embed_browser_e2e.py` against bundled Bokeh and the Next-built tree under `hpcperfstats_site/static/frontend/`; run it after `npm run build` (full export)

Faster iteration after the first successful build:

```bash
tests/run_db_pytest_workflow.sh --skip-build
tests/run_valkey_cache_pytest_workflow.sh --skip-build
tests/run_web_e2e_workflow.sh --skip-build
tests/run_pipeline_e2e_workflow.sh --skip-build
tests/run_stress_host_data_workflow.sh --skip-build
```

Prefer the compose web runner for page-level tests, so DB, Valkey and networking match the service topology.

### Test compose overlay

Every workflow sources `tests/compose_test_cmd.sh`.
It verifies rootless Podman, creates missing local files from their `.example` templates (for example `hpcperfstats.ini` from `hpcperfstats.ini.example`, needed for the bind mount), and runs `podman-compose --project-name hpcperfstats-test` with the base file and the gitignored test overlay, copied from `tests/docker-compose.test-overlay.yaml.example`.

- The overlay replaces production binds with isolated test volumes and fixture TLS: `test_proxy_ssl_source` binds `./tests/fixtures/proxy-ssl`, and the ram volumes become plain `test_staticfiles_ram` / `test_media_ram` volumes.
- Production uses project `hpcperfstats`; the long-running local development stack uses `hpcperfstats-dev`.
- Inner scripts such as `tests/run_db_pytest_inner.sh` stream into `web` through `compose_run_inner_script`.
  Extra pytest arguments pass through a workflow-managed temporary file.
- Bind-mount work copies and all growth-prone temporary state use `/data/user/$USER/tmp`; they never fall back to `$HOME/.cache`, `/tmp` or `/var/tmp`.
- When `pip install -e ".[test]"` fails on the bind mount, `tests/run_pipeline_e2e_workflow.sh` sources `tests/pip_compose_test_extras_fallback.sh`; the other inner scripts use the same pins through `compose_inner_pip_install` in `tests/compose_inner_pip_install.sh`.

The baked ini uses `host = /run/postgresql`: `web` and `pipeline` connect over the shared `postgres_runtime` tmpfs socket (password auth via `services-conf/postgres_pg_hba.conf`).
Host pytest has neither that socket nor the `db` alias.
`tests/run_dev_container_pytest.sh` mounts the dev project's `postgres_runtime` volume, so recreate the dev `db` with it.

### Workflow options

Each workflow tears down only its project containers and named volumes unless you pass `--keep-env`.
Every workflow takes `--skip-build` and `--keep-env`; forward pytest arguments after `--` (for example `-- -vv`).

```bash
# Faster iteration: skip browser E2E module and Playwright install
tests/run_db_pytest_workflow.sh --skip-browser-e2e

# Optional seed inside the web container after migrate
tests/run_db_pytest_workflow.sh --seed-cmd \
  "python path/in/repo/seed.py"
# or: DB_TEST_SEED_CMD="python ..." tests/run_db_pytest_workflow.sh

# Skip migrate on the dev database (pytest still creates its own
# test_* DB)
tests/run_db_pytest_workflow.sh --skip-migrate

# Forward pytest arguments (use -- if an option starts with -)
tests/run_db_pytest_workflow.sh -- -k job_plot

# Seed/recreate test data before E2E tests
tests/run_web_e2e_workflow.sh --seed-cmd \
  "python your_seed_script.py"

# Skip image rebuild or Playwright browser install when appropriate
tests/run_web_e2e_workflow.sh --skip-build --skip-playwright-install
```

The web workflow also reads the seed from the environment:

```bash
E2E_SEED_CMD="python your_seed_script.py" \
  tests/run_web_e2e_workflow.sh
```

`tests/run_pipeline_e2e_workflow.sh` adds `--skip-playwright-install` and `--skip-browser` (skips the phase 2 browser, navigation and API matrix; see `--help`).

### Clean up a test project

To remove a test project by hand:

```bash
podman-compose -p hpcperfstats-test \
  -f docker-compose.yaml -f tests/docker-compose.test-overlay.yaml \
  down -v --remove-orphans
```

`tests/podman_compose_teardown.sh` runs the same project-scoped teardown.
Global Podman prune is forbidden on this shared server; do not remove unrelated networks.

## Run opt-in suites

These suites sit outside the default `pytest` `testpaths` (`hpcperfstats` only), so `python scripts/run_tests.py` and `pytest hpcperfstats` never collect them.

### Stress `host_data`

`tests/run_stress_host_data_workflow.sh` starts Compose `db` and `valkey`, migrates, and runs `tests/stress_host_data/` inside `web`:

```bash
tests/run_stress_host_data_workflow.sh
```

- The workflow sets `HPCPERFSTATS_STRESS_HOST_DATA=1` and `HPCPERFSTATS_COMPOSE_NETWORK=1` and defaults `HPCPERFSTATS_STRESS_HOST_DATA_ROWS` to 400000; override it for the full 34,560,000-row case.
- The test drives the real `update_metrics(..., rerun=True)` path (readiness, pooled metrics, plot prewarm) and writes `stress_report_<utc>.json` under `HPCPERFSTATS_STRESS_REPORT_DIR` (default `test_runs/stress/`).
- It passes `timezone.localtime(job.end_time)` into `update_metrics` so Django's `end_time__date` selects the job (large 1 Hz windows can otherwise miss the queryset).
- `tests/run_stress_host_data_inner.sh` falls back to `PYTHONPATH=/home/hpcperfstats` and installs the pytest extras only if `pip install -e` fails on the bind mount.
- Scale and report variables it forwards (see `--help`): `HPCPERFSTATS_STRESS_USE_TIME_SCALE`, `HPCPERFSTATS_STRESS_N_HOSTS`, `HPCPERFSTATS_STRESS_INTERVAL_SEC`, `HPCPERFSTATS_STRESS_DURATION_SEC`, `HPCPERFSTATS_STRESS_JID`, `HPCPERFSTATS_STRESS_REPORT_DIR`, `HPCPERFSTATS_STRESS_EXPLAIN`, `HPCPERFSTATS_STRESS_MANUAL_PLOT_SANITY`, `HPCPERFSTATS_STRESS_SAMPLE_PATH`, and `HPCPERFSTATS_STRESS_PLOT_SEC` (with manual plot sanity).

### Pipeline end to end

`tests/run_pipeline_e2e_workflow.sh` runs `tests/pipeline_e2e/` (RabbitMQ -> listend -> sync_timedb -> metrics -> live web); the tests skip unless `HPCPERFSTATS_PIPELINE_E2E=1`, which the workflow sets:

```bash
tests/run_pipeline_e2e_workflow.sh
```

It starts `db`, `valkey` and `rabbitmq`, migrates, runs the synthetic ingest and metrics phase, then starts `web` for the endpoint and browser phases.
It installs Chromium unless `--skip-playwright-install` is set.
Test-project host ports are fixed at HTTP `18080`, HTTPS `18443` and RabbitMQ `15673`, so the development ports (`8080`, `8443` and `5673`) cannot contend.

The URL drift guard keeps the endpoint matrix complete:

- `EXPECTED_ROUTE_TEMPLATES` in `hpcperfstats/tests/urlconf_route_catalog.py` is the canonical template set; `hpcperfstats/tests/test_endpoint_route_snapshot.py` asserts the live Django resolver matches it.
- `build_pipeline_http_endpoint_specs()` in the same module expands every template to concrete paths.
- Phase 2 drives each check through Playwright (`page.goto` for HTML and redirects, `APIRequestContext` for JSON APIs) with status and `Content-Type` checks.
- Adding a URL without updating the catalog and the builder fails CI.

### sync_timedb regression battery

Run the host battery before closing any stall-related change:

```bash
tests/run_sync_timedb_regression_battery.sh
```

It logs to `test_runs/day-close-loop-regression-battery-<timestamp>.log` and covers:

- Job-store reconstruct and discover helpers (`test_sync_timedb_job_queue.py`, `test_sync_timedb_job_discover.py`), including digit-epoch calendar-day and over-cap hot ZADD via `-k` tokens `digit_epoch` / `over_cap` / `calendar_day`.
- Exclusive flock and entry wiring (`test_sync_timedb_queue_orchestrator.py`).
- The append lock and clone writer (`test_sync_timedb_append_tar_race.py`) and subprocess drain and timeout (`test_sync_timedb_subprocess_hardening.py`).
- Member-store smoke (`test_sync_timedb_archive_members_coord.py`) and the `day_close` predicate contracts `test_arch_*` (`test_sync_timedb_architecture_contract.py`).
- fd `-X` GNU stat discover, day-raw-removal samples (including ghost-deleted retry), and the day-close hot-path `populate_active` owner vs complete-map (`test_sync_timedb_day_close_cooperation.py`).
- gz-only migrate nested lock (`already_locked` / `nested_lock` in `test_sync_timedb_archive.py`) and `--jid` ingest-only (`test_sync_timedb_jid.py`).

The site verification that follows (T0/T1/T2 and the `--jid` smoke) is in [Verify sync_timedb after a deploy](../operate/verify-sync-timedb.md).
`sync_timedb.py --jid <JID>` is ingest-only (+/-1h pad around job start and end, plus one earlier and one later raw stats file per host), with no archival or day close; its host unit test:

```bash
../.venv/bin/python3 -m pytest -q \
  hpcperfstats/tests/test_sync_timedb_jid.py
```

`<JID>` is a job ID that has `job_data`.

### sync_timedb archive member cache

To run the archive member cache contracts on the host (no compose), use `scripts/run_tests.py --no-django` with explicit targets.
The first command runs two whole files plus five node IDs (210 tests); the second filters `test_sync_timedb_archive.py` with one `-k` (23 tests):

```bash
T=hpcperfstats/tests
C=test_sync_pipeline_tunable_defaults_and_overrides
Z=test_archive_zstd_never_runs_under_nice_or_ionice
H=$T/test_multiprocessing_pool_health.py
P=$H::test_abort_if_pool_workers_dead
../.venv/bin/python3 scripts/run_tests.py --no-django \
  $T/test_sync_timedb_archive_members_coord.py \
  $T/test_sync_timedb_queue_orchestrator.py \
  $T/test_zstd_cli.py::$Z $T/test_conf_parser.py::$C \
  ${P}_raises ${P}_log_does_not_claim_oom \
  ${P}_recycle_invokes_idle_reconcile
K="members_cache or raw_stats_needs_append or single_member"
K="$K or invalidate_daily or dedupe_sealed or day_skip"
K="$K or classify_stream or ingest_sealed or concurrent_waiters"
../.venv/bin/python3 scripts/run_tests.py --no-django \
  $T/test_sync_timedb_archive.py -k "$K"
```

Compose smoke for the same contracts:

```bash
N=test_daily_archive_members_cache_hit_skips_second_scan
tests/run_db_pytest_workflow.sh -- \
  hpcperfstats/tests/test_sync_timedb_archive_members_coord.py \
  hpcperfstats/tests/test_sync_timedb_archive.py::$N \
  hpcperfstats/tests/test_sync_timedb_queue_orchestrator.py
```

### Live Valkey cache

`tests/run_valkey_cache_pytest_workflow.sh` runs `hpcperfstats/site/lib/machine/tests/test_valkey_cache_live.py` in `web` with `HPCPERFSTATS_PYTEST_LIVE_VALKEY=1`, so Django keeps `ValkeyCache` instead of switching to `LocMemCache`:

```bash
tests/run_valkey_cache_pytest_workflow.sh
```

- The tests carry the `live_valkey` marker and skip without that variable, so a laptop run of `python scripts/run_tests.py` needs no Valkey.
- They assert `server_name` is `valkey` with `valkey_version` major >= 9, Django cache set/get/add/incr/touch/delete_many through `django_valkey.cache.ValkeyCache`, and raw `get_valkey_client()` SADD/SMEMBERS/SCAN/MEMORY USAGE over `/run/valkey/valkey.sock` (`[CACHE] valkey_location = unix:///run/valkey/valkey.sock?db=1`; TCP 6379 stays for `valkey-cli` and external URLs).
- After pytest, the inner script runs a free-threaded check on `/opt/python3.14t` with `-W error::RuntimeWarning` (`django.setup()`, a cache set/get, `get_valkey_client().ping()`) and prints `VALKEY_FT_GIL_OFF` only when `sys._is_gil_enabled()` is False, so a dependency that re-enables the GIL fails the workflow.
- `sync_timedb` job queues and member maps are in-process stores, not part of this workflow.

After changing the Compose `valkey` image tag, confirm the running server is Valkey 9.x:

```bash
podman-compose -p hpcperfstats exec valkey sh -lc '
valkey-cli INFO server | grep -E "^(server_name|valkey_version):"
valkey-cli ping
valkey-cli -s /run/valkey/valkey.sock ping
valkey-cli CONFIG GET io-threads'
```

Why `web` and `pipeline` do not `depends_on` `valkey`, and how `valkeywait.py` remaps the hostname, is in [Compose services](../../reference/compose-services.md#valkey).

## Run frontend tests

From `hpcperfstats/site/frontend`, `npm ci` (or `npm install`) installs dependencies only, and `npm test -- --run` runs Vitest:

```bash
cd hpcperfstats/site/frontend && npm ci && npm test -- --run
```

Targeted runs:

```bash
cd hpcperfstats/site/frontend
npm test -- --run src/components/PageBreadcrumbs.test.tsx
npm test -- --run src/utils/extended-search-parameters.test.ts
```

`src/components/BokehEmbed.tsx` defaults to viewport-gated embedding and a short post-`Document.idle` settle in production builds; Vitest skips the deferral unless `deferEmbedUntilVisible` is set (see `src/utils/bokeh-embed-defaults.ts`):

```bash
cd hpcperfstats/site/frontend && npm test -- --run \
  src/components/BokehEmbed.test.tsx \
  src/components/HistogramThumbnails.test.tsx \
  src/utils/bokeh-embed-defaults.test.ts
```

Bokeh is the stock `@bokeh/bokehjs` UMD (`bokeh.min.js`), synced to `public/vendor/` by `scripts/sync-bokeh-vendor.mjs` (via `write-site-identity`) and loaded by `src/bokehjs-bundle.ts` as a same-origin script.
Turbopack must not bundle Bokeh sources: package `main` bare imports and ESM rewrites both break model registration.
Keep the `@bokeh/bokehjs` version in `package.json` aligned with the `bokeh==...` pin in `pyproject.toml` so `json_item` embeds stay compatible.

The stack is Next.js 16 App Router (static export, Turbopack), strict TypeScript 6 (the `typescript-eslint` peer holds TypeScript below 6.1), TanStack Query, Orval 8 + Zod 4 (from the committed `hpcperfstats/site/openapi/openapi.yaml`), React Hook Form + Zod, and Vitest 5 + RTL.

### Build the static export

The production export omits test-only routes such as `bokeh-playwright-smoke/`; the Podman image build and `scripts/rebuild_frontend.sh` use it:

```bash
cd hpcperfstats/site/frontend && npm ci && npm run build:prod
```

The full export is for local development and runs before the Playwright Bokeh bundle test:

```bash
cd hpcperfstats/site/frontend && npm ci && npm run build
```

Test-only source lives under `hpcperfstats/site/frontend/test/` (Vitest mocks, wire-audit fixtures, Bokeh JSON fixtures in `hpcperfstats/site/frontend/test/fixtures/`).

### Regenerate the API client

After OpenAPI changes:

```bash
(cd hpcperfstats/site && ../../../.venv/bin/python3 manage.py \
  spectacular --file openapi/openapi.yaml --format openapi)
(cd hpcperfstats/site/frontend && npm run generate:api)
../.venv/bin/python3 -m pytest \
  hpcperfstats/site/lib/machine/tests/test_openapi_schema_drift.py
```

### Regenerate monitor event metadata

When monitor changes add or rename `host_data.event` strings, regenerate the bundled tooltip catalog:

```bash
D=hpcperfstats/site/frontend/src/utils
python3 "$D/generate-variable-metadata-monitor-events.py"
```

Then merge into `src/utils/variableMetadata.ts` and refresh the operator catalog with `docs/regenerate_monitor_variables_catalog.py`.

## Measure coverage

Coverage settings live in `pyproject.toml` (`[tool.coverage.*]`); HTML output goes to `htmlcov/`.

```bash
# Tests that need no database only (not a full check)
../.venv/bin/python3 scripts/run_tests.py --no-django \
  --cov=hpcperfstats --cov-report=term-missing --cov-report=html

# Full tree including Django tests (run through the podman-compose
# workflow)
../.venv/bin/python3 scripts/run_tests.py --cov=hpcperfstats \
  --cov-report=term-missing --cov-report=html
```

Host-side mock tests (LocMem cache, `django_db(databases=[])`) enforce 100% line coverage on `hpcperfstats.site.lib.machine.api`:

```bash
../.venv/bin/python3 -m pytest \
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

The dedicated `--cov-config` disables branch measurement, so the gate tracks line coverage only; the project-wide `[tool.coverage.run]` keeps `branch = true`.

Frontend (Vitest + v8) writes to `hpcperfstats/site/frontend/coverage/`, ignored via `hpcperfstats/site/frontend/.gitignore`:

```bash
npm run test:coverage -- --run
```

The tools package:

```bash
cd hpcperfstats-tools && ../../.venv/bin/python3 -m pytest \
  --cov=hpcperfstats_tools --cov-report=term-missing
```

## Check accessibility

The SPA and standalone HTML pages target WCAG 2.2 Level AA for in-app flows (keyboard, screen readers, zoom, contrast).
Bokeh canvas plots and the OAuth provider have inherent limits; the UI mitigates them with text alternatives, landmarks and the manual checks below.

To check accessibility before a release or a UI-heavy deploy:

1. Run the Vitest suite, which includes the jest-axe checks:

   ```bash
   cd hpcperfstats/site/frontend && npm test -- --run
   ```

   Colocated page and component tests call `axeSeriousViolations()` (WCAG 2.x / 2.1 AA tag scope, no serious or critical violations); the covered views are listed in [Test layout](../../reference/test-layout.md#frontend-coverage).
   jsdom cannot compute colour contrast, so jest-axe never reports `color-contrast`; `src/utils/muted-surface-contrast.test.ts` statically rejects any class string that pairs `text-muted-foreground` with solid `bg-muted` (about 4.3:1 in the light theme, under the AA 4.5:1 minimum).
   Use `text-foreground` on `bg-muted` surfaces.

2. Run `tests/run_pipeline_e2e_workflow.sh` without `--skip-browser`.
   Its browser phase runs `tests/pipeline_e2e/test_a11y_axe_browser.py` over `_AXE_SMOKE_PATHS` (`/machine/`, `/machine/jobs/`, `/machine/home/`, `/machine/api-key`, plus one job detail URL) and Extended Search open on the job list, and `tests/pipeline_e2e/test_job_detail_browser.py` runs axe on the live job detail SPA.

3. Check by keyboard only: Tab through the navbar, the Extended search dialog (Escape closes it and focus returns), Find Job, tables, pagination and the job detail Job data plot tabs.

4. Check with a screen reader (VoiceOver with Safari, or NVDA with Firefox): Search jobs -> job list -> job detail; route focus lands on the page heading after navigation.

5. Zoom the browser to 200% on the job list and job detail; tables scroll inside their `overflow-x-auto` containers.

6. Check the standalone pages: `/machine/api-key` (React) has `<main>`, a confirm before key rotation and a live region when a new key is shown; the Django admin branding links back to `/machine/`.

`jest-axe` is a devDependency only (it pulls in `axe-core` as a dev-only transitive package), imported from `*.test.{ts,tsx}`, `test/vitest/setupTests.ts` and `test/vitest/axe-test-utils.ts`.
The production image excludes `node_modules/` and `frontend/test/` from the build context and builds with `npm run build:prod`, so axe and test-only routes never ship.
Playwright injects the vendored `hpcperfstats/tests/fixtures/axe-core/axe.min.js` (version in that folder's `README.md`) through `hpcperfstats/tests/playwright_axe.py`, because the `web` container has no Node dev dependencies.
`test_web_pages_browser_e2e.py` runs axe only on a minimal empty HTML document to validate that helper; it does not scan the SPA.
Browser extensions or an external audit remain useful for full-page audits beyond CI.

## Run git hooks and static analysis

Install the dev extras and register the hooks once:

```bash
../.venv/bin/pip install -e ".[dev]"
./scripts/install-git-hooks.sh
```

The commit stage runs on staged files:

- `ruff check --fix` and `ruff format` on Python under `hpcperfstats/`, `cursor-hooks/`, `scripts/`, `services-conf/` and `hpcperfstats-tools/`, with the `pyproject.toml` policy: isort `I`, whitespace `W291`/`W292`/`W293`, plus `E`/`F`/`B`/`UP`/`SIM`/`RUF`/`C4`/`PIE`/`PGH`/`S` (Bandit).
  The linter ignores `E501`; `ruff format --check` enforces `line-length = 80`.
- ESLint on staged `hpcperfstats/site/frontend` TypeScript, the python def inventory `--check`, and the memray gate ([Profile memory](#profile-memory)).
- The Markdown hooks (mdformat, PyMarkdown, Vale, codespell) on the pages listed in [Documentation style](../../reference/documentation-style.md#scope-of-the-hooks).
- ShellCheck (`shellcheck`, the pinned `shellcheck-py` mirror `v0.11.0.1`, which bundles the binary) on scripts selected by `.sh`/`.bash` extension or a shell shebang, with policy in the root `.shellcheckrc` (`external-sources=true`, `source-path=SCRIPTDIR`).
  When a pattern is intentional (word-split flag lists, printf format variables, literal `$` in grep patterns, trap handlers), add a scoped `# shellcheck disable=SCxxxx  # reason` on that line, never a repo-wide disable.
  `hpcperfstats/tests/test_static_analysis.py::test_shellcheck_clean_on_all_tracked_scripts` guards the tree (it skips when `shellcheck` is not on `PATH`).

The push stage runs frontend `npm run typecheck` and `npm run lint:dead` (knip), and `vulture hpcperfstats scripts/vulture_whitelist.py --min-confidence 80`.
Run both stages over the whole tree without committing:

```bash
pre-commit run --all-files
pre-commit run --hook-stage pre-push --all-files
```

Manual equivalents:

```bash
# Python lint + format (2-space indent, line-length 80 - main repo +
# hpcperfstats-tools)
../.venv/bin/ruff check hpcperfstats cursor-hooks scripts \
  services-conf hpcperfstats-tools
../.venv/bin/ruff check hpcperfstats cursor-hooks scripts \
  services-conf hpcperfstats-tools --fix
../.venv/bin/ruff format hpcperfstats cursor-hooks scripts \
  services-conf hpcperfstats-tools
../.venv/bin/ruff format --check hpcperfstats cursor-hooks scripts \
  services-conf hpcperfstats-tools

# Narrow unused-import check (subset of F rules; full tree uses
# pyproject select above)
../.venv/bin/ruff check hpcperfstats cursor-hooks scripts \
  services-conf hpcperfstats-tools --select F401,F841,F811

# Python dead symbols (high confidence)
../.venv/bin/vulture hpcperfstats scripts/vulture_whitelist.py \
  --min-confidence 80

# Frontend
cd hpcperfstats/site/frontend
npm run typecheck
npm run lint
npm run lint:dead
```

Review the diff before `ruff check ... --fix`, because tests and dynamic imports can confuse static analysis.
pylint is an optional extra; Ruff is the primary linter.
The host drift guards for this tooling run with `pytest -q hpcperfstats/tests/test_static_analysis.py`.

## Profile memory

The commit-stage memory gate is the only hard gate for memory growth; do not add a second tracemalloc hook.
When staged paths match `^(hpcperfstats/|scripts/).*\.py$`, pre-commit runs `scripts/run_commit_memory_leak_check.py` (hook id `python-memory-leak-check`).
It exercises curated no-Django workloads under memray, discards warm-up iterations, and fails if late-window mean heap growth or peak or leaked bytes exceed fixed ceilings.
It exits `2` with an install hint when memray is missing.

```bash
../.venv/bin/python3 scripts/run_commit_memory_leak_check.py
# or:
pre-commit run python-memory-leak-check --all-files
```

Set `HPCPERFSTATS_MEMORY_LEAK_CHECK_LOG=1` for per-workload measurement lines.

For ad hoc investigation of RSS growth in long-lived workers or CLI jobs, run these with DB and Valkey available (for example in the compose `web` service).
tracemalloc (stdlib) around a focused pytest node:

```bash
../.venv/bin/python3 -X tracemalloc=25 -m pytest -q \
  hpcperfstats/site/lib/machine/tests/test_job_plots_timeout.py \
  --tb=no 2>&1 | tail -20
```

memray flamegraphs for a hook failure or a single test file:

```bash
../.venv/bin/memray run -m pytest -q \
  hpcperfstats/site/lib/machine/tests/test_job_plots_timeout.py
../.venv/bin/memray flamegraph memray-*.bin
```

For ingest-style workloads, run `memray` against a short `sync_timedb` invocation or a targeted dbload test module, not the full archive scan.

## Scan dependencies

Run pip-audit inside the compose `web` image and npm audit for the frontend:

```bash
tests/run_security_audit_workflow.sh
```

The workflow builds the `web` image unless you pass `--skip-build`.
GitHub Actions (`.github/workflows/security-audit.yaml`) installs rootless Podman, points storage at `/data/podman/$USER`, and runs it with `permissions: contents: read`, weekly and on pull requests that touch dependency or audit files.

To audit only the host environment or the frontend:

```bash
../.venv/bin/pip install pip-audit
../.venv/bin/pip-audit -r <(../.venv/bin/pip freeze)
(cd hpcperfstats/site/frontend && npm audit)
```

A host `pip freeze` may include extra dev tools; the workflow audits the dependency set that ships.
Findings and follow-ups are in [`docs/SECURITY_AUDIT.md`](../../SECURITY_AUDIT.md) and [`docs/SECURITY_REMEDIATION_BACKLOG.md`](../../SECURITY_REMEDIATION_BACKLOG.md).
`hpcperfstats/site/hpcperfstats_site/tests/test_csp_report_endpoint.py` checks that the CSP report endpoint accepts a POST without a CSRF token.

## Troubleshooting

- A follow-up script cannot resolve `db`: run the project-scoped teardown in [Clean up a test project](#clean-up-a-test-project) and retry.
- Host tests that use the Django DB fixtures (for example `test_sync_timedb_host_data_insert.py`, `test_sync_timedb_proc_data_insert.py`) error with `failed to resolve host 'db'`: run them through a compose workflow.
- `test_sync_timedb_day_raw_removal.py` and the zstd archive tests fail or skip: put `zstd` on `PATH`; integration tests call the real CLI.
- Live Valkey tests skip: run `tests/run_valkey_cache_pytest_workflow.sh`, which sets `HPCPERFSTATS_PYTEST_LIVE_VALKEY=1`.
- The Bokeh browser test cannot find the static tree: run `npm run build` (not `build:prod`) first.

## Related

- [Test layout](../../reference/test-layout.md)
- [Diagnose update_metrics](diagnose-update-metrics.md)
- [Run sync_timedb benchmarks](run-sync-timedb-benchmarks.md)
- [Install HPCPerfStats](../../tutorials/install-hpcperfstats.md)
- [Documentation style](../../reference/documentation-style.md)
