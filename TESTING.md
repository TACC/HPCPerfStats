# Testing hpcperfstats

## Quick start

From the project root (directory containing `pyproject.toml`):

```bash
# Install test extras once
pip install -e ".[test]"

# Unit tests only (no Django DB tests)
python run_tests.py --no-django

# Full pytest collection (unit + Django tests)
python run_tests.py
```

You can also run `pytest` directly:

```bash
# Unit-only path
PYTHONPATH=. pytest -q hpcperfstats/tests

# Django app tests (requires DB/settings)
PYTHONPATH=. pytest -q hpcperfstats/site/machine/tests
```

**`hpcperfstats/site/machine/tests` on the host:** tests that need the default PostgreSQL database are **skipped** unless the environment sets **`HPCPERFSTATS_COMPOSE_NETWORK=1`** (the compose workflows `tests/run_db_pytest_workflow.sh` and `tests/run_redis_cache_pytest_workflow.sh` export this inside the `web` container). Tests that only need Django settings and mocks use **`django_db(databases=[])`** and still run on the host. During pytest, Django switches the default cache to **LocMem** unless **`HPCPERFSTATS_PYTEST_LIVE_REDIS=1`** (live Redis workflow).

### Opt-in stress tests (massive `host_data`)

The directory **`tests/stress_host_data/`** is **outside** the default `pytest` `testpaths` (`hpcperfstats` only), so `python run_tests.py` and `pytest hpcperfstats` never collect it.

**Default way to run (Docker Compose `db` + `redis`, migrate, pytest inside `web`):**

```bash
cd HPCPerfStats   # directory with docker-compose.yaml
tests/run_stress_host_data_workflow.sh
```

The workflow sets **`HPCPERFSTATS_STRESS_HOST_DATA=1`** and **`HPCPERFSTATS_COMPOSE_NETWORK=1`** inside the container, and defaults **`HPCPERFSTATS_STRESS_HOST_DATA_ROWS`** to **400000** for a faster smoke (override for the full **34,560,000**-row case). The test drives the real **`update_metrics(..., rerun=True)`** path (readiness, pooled metrics, plot prewarm) and writes **`stress_report_<utc>.json`** under **`HPCPERFSTATS_STRESS_REPORT_DIR`** (default **`artifacts/stress/`** on the repo mount). The test passes **`timezone.localtime(job.end_time)`** into **`update_metrics`** so the job is selected under Django’s **`end_time__date`** semantics (large 1 Hz windows can otherwise miss the queryset). Row-count scaling notes: **`docs/plans/stress_row_sweep_scaling_plan.md`**. Inside the container, **`tests/run_stress_host_data_inner.sh`** falls back to **`PYTHONPATH=/home/hpcperfstats`** plus installing pytest extras only if **`pip install -e`** fails on the bind mount (e.g. cloud-sync **Errno 35**). Options: **`--skip-build`**, **`--keep-env`**; extra pytest args after `--`.

**Scale / report env** (forwarded by `tests/run_stress_host_data_workflow.sh`; see `--help`): **`HPCPERFSTATS_STRESS_USE_TIME_SCALE`**, **`HPCPERFSTATS_STRESS_N_HOSTS`**, **`HPCPERFSTATS_STRESS_INTERVAL_SEC`**, **`HPCPERFSTATS_STRESS_DURATION_SEC`**, **`HPCPERFSTATS_STRESS_JID`**, **`HPCPERFSTATS_STRESS_REPORT_DIR`**, **`HPCPERFSTATS_STRESS_EXPLAIN`**, **`HPCPERFSTATS_STRESS_MANUAL_PLOT_SANITY`**, **`HPCPERFSTATS_STRESS_SAMPLE_PATH`**.

Optional env: **`HPCPERFSTATS_STRESS_PLOT_SEC`** (with manual plot sanity), **`HPCPERFSTATS_LARGE_JOB_HOST_DATA_ROWS`**, **`HPCPERFSTATS_LARGE_JOB_TIME_BUCKETS`**, **`HPCPERFSTATS_LARGE_JOB_WINDOW_ROW_COUNT_CACHE_TTL`** (seconds; `0` disables cached window `COUNT(*)` for large-job gating), **`HPCPERFSTATS_LARGE_JOB_TIME_SQL`** (`date_bin` default; set `ntile` for legacy distinct-time + NTILE sampling on PostgreSQL 14+), **`HPCPERFSTATS_LIVE_DISTINCT_LEGACY_HOSTLIST`** (`1` restores `unnest(host_list)` live distinct SQL).

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

From the `HPCPerfStats/` directory that contains `pyproject.toml`:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[test]"
```

For Vitest:

```bash
cd hpcperfstats/site/frontend && npm ci
```

The SPA bundles Bokeh via **`@bokeh/bokehjs`** in `package.json`; keep its version aligned with the **`bokeh==…`** pin in `pyproject.toml` so `json_item` embeds stay compatible.

**Bootswatch** (Spacelab) is imported from **`bootswatch`** in the Vite bundle (same CSS stack as the rest of `/machine/`).

### 3. Full compose-backed gate (Django DB, Playwright browser E2E, live Redis)

All commands below assume your current directory is **`HPCPerfStats/`** (the one with `docker-compose.yaml`).

| Step | Command | What it covers |
|------|---------|----------------|
| 1 | `tests/run_db_pytest_workflow.sh` | Resets compose volumes, builds `web`, starts **db** + **redis**, migrates dev DB, installs **Playwright/Chromium** in the container, runs **`pytest -q hpcperfstats`** (entire tree, including **`test_web_pages_browser_e2e.py`** unless you pass `--skip-browser-e2e`). |
| 2 | `tests/run_redis_cache_pytest_workflow.sh --skip-build` | Fresh compose session, **`test_redis_cache_live.py`** against real **Redis** (`HPCPERFSTATS_PYTEST_LIVE_REDIS=1`). |
| 3 | `tests/run_web_e2e_workflow.sh --skip-build` | Dedicated **HTTP + Playwright** session for `test_web_pages_e2e.py` and `test_web_pages_browser_e2e.py` (run again after step 1 for an isolated E2E pass or CI parity). |
| 4 | `tests/run_stress_host_data_workflow.sh --skip-build` | Opt-in **`host_data`** stress (`tests/stress_host_data/`): seed + **`update_metrics`**, JSON report under **`artifacts/stress/`**, default **400000** rows (override row/time-scale env vars; see section above). |

Faster iteration after the first successful build:

```bash
tests/run_db_pytest_workflow.sh --skip-build
tests/run_redis_cache_pytest_workflow.sh --skip-build
tests/run_web_e2e_workflow.sh --skip-build
tests/run_stress_host_data_workflow.sh --skip-build
```

Each workflow tears down containers and **named volumes** on exit unless you pass **`--keep-env`** (see per-script help).

If a follow-up script fails with **`failed to resolve host 'db'`** inside the container, Docker networking may still be cleaning up from a previous run. Run `docker-compose down --remove-orphans` from `HPCPerfStats/` and retry, or wait a few seconds between workflows.

### 4. Sibling repo and SPA unit tests

From `HPCPerfStats/` (inner project root, next to `hpcperfstats-tools/`):

```bash
cd ../hpcperfstats-tools && python -m pytest -q
cd ../HPCPerfStats/hpcperfstats/site/frontend && npm test -- --run
```

## Code coverage

Coverage settings live in `pyproject.toml` (`[tool.coverage.*]`). Typical commands:

```bash
# Python package (from repo root with test extras installed)
python run_tests.py --no-django --cov=hpcperfstats --cov-report=term-missing --cov-report=html

# Full tree including Django tests (needs Postgres/Redis per your settings, often via Docker)
python run_tests.py --cov=hpcperfstats --cov-report=term-missing --cov-report=html
```

HTML output is written to `htmlcov/` (gitignored by convention).

**React (Vitest + v8)** — from `hpcperfstats/site/frontend/`:

```bash
npm run test:coverage -- --run
```

Reports are written to `hpcperfstats/site/frontend/coverage/` (ignored via `hpcperfstats/site/frontend/.gitignore`).

**`hpcperfstats-tools`** (sibling package):

```bash
cd hpcperfstats-tools && python -m pytest --cov=hpcperfstats_tools --cov-report=term-missing
```

## Test layout

| Location | Description |
|---------|-------------|
| `hpcperfstats/tests/` | Non-Django unit/integration tests (config parsing, service startup/health, listend behavior, sync helpers, cache/date/print/file-locking helpers, XALT models, API key mobile checks, dbload `pigz`/row-builder helpers, Django `timezone.utc` shim). `test_conf_parser.py` covers `get_effective_cores()`, `get_metrics_pool_process_count()`, default `total_cores=40`, `get_parallel_db_prefetch_max_workers()` / `get_api_small_executor_max_workers()`, DB `CONN_MAX_AGE` and PostgreSQL `OPTIONS` builders, and `get_sync_ingest_pool_processes()` / `get_sync_archive_pool_processes()` caps. `test_pg_connection_stats_command.py` exercises the `pg_connection_stats` management command with a mocked DB connection (no live Postgres). `test_numa_topology.py`, `test_compose_cpu_layout.py`, and `test_apply_compose_numa_pinning.py` cover sysfs NUMA parsing, **single-node** topology, responsive linear CPU partitions, and `scripts/apply_compose_cpu_pinning.py --dry-run`. `test_sync_timedb_supervisor.py` exercises `run_sync_timedb_supervisor_loop` (empty-queue sleep, ingest wave, second empty sleep) with mocked multiprocessing pools and rescans. `test_sync_timedb_archive.py` includes deferred-archive / atomic-seal helpers, `tar tf` integrity checks, gzip restore, tar dedupe (largest wins per path), and helpers used for scheduled pigz + raw-file removal (`get_existing_archive_members_for_daily_archive`, `remove_verified_archived_raw_files`, `validate_sealed_daily_archive_for_raw_removal`); two tests call real `pigz` and skip when `pigz` is not on `PATH`. `test_monitor_analysis_typename_contract.py` asserts monitor `stats_type.st_name` values stay aligned with `INTEL_IMC_STATS_TYPES`, `ARM_IMC_STATS_TYPES`, and `INTEL_CORE_PMC_TYPES_ORDERED` (skips if `monitor/src` is absent). |
| `hpcperfstats/analysis/**/test*.py` | Analysis/plot/metrics-focused tests (summary/heatmap/roofline behavior, roofline peak inference from `host_data.type` schema keys, hover tooltips, shared job-window parsing, metrics helpers). `analysis/metrics/test_per_interval_rate.py` also covers node-imbalance variants (DRAM, LNET, GPU util/tensor) and GPU peak helpers. `analysis/metrics/test_job_for_metrics.py` and `analysis/plot/test_summaryplot_no_data.py` exercise metrics/summaryplot edge cases. `analysis/metrics/test_job_metric_display_labels.py` and `test_metrics_add_arrays.py` cover label maps and small `metrics.py` helpers. `analysis/test_bokeh_job_embed.py` is a pure-Python unit test for `bokeh_job_embed.figure_embed_kw` (no Django). |
| `hpcperfstats/site/machine/tests/` | Django + web tests (ORM/query/update helpers, job detail file-system llite vs NFS fallback, security headers, API/misc endpoints, SPA rendering, page and browser E2E tests). Includes `test_type_detail_api.py`, which asserts type-detail `host_data` ORM SQL does not reference `jid` (job scope is start/end time + accounting hosts) with `django_db(databases=[])` so it does not need a live DB. `test_api_coverage_gaps.py` hits additional `api.py` branches (cache invalidation, `host_plot`, job monitor, `sacct_ingest`) with mocks and locmem cache—no Postgres host required. `test_oauth2.py`, `test_cache_middleware.py`, `test_renderers.py`, and `test_update_xalt.py` cover OAuth helpers, dynamic cache TTL middleware, JSON NaN sanitization, and the XALT log script loop. `test_metrics.py` includes `test_job_metric_short_labels_cover_catalog`, which asserts every `job_metrics_catalog_entries()` metric has a matching entry in `job_metric_display_labels.JOB_METRIC_SHORT_LABELS` (parity with the frontend short-label map). `test_job_plot_artifacts.py` covers gzip-serialized Bokeh `json_item` persistence (`job_plot_artifact`), fingerprinting, and invalidation (requires Postgres like other `django_db` machine tests). `test_job_list_performance_summary.py` covers job list `performance` labels / `sort_rank` and ORM `performance_sort_rank` ordering vs `summarize_performance()`. `test_redis_cache_live.py` hits Django `RedisCache` against a live Redis when `HPCPERFSTATS_PYTEST_LIVE_REDIS=1` (`tests/run_redis_cache_pytest_workflow.sh`); otherwise those tests skip. `test_query_utils.py` includes `get_job_list_order_by` allowlist (`performance_sort_rank`; legacy `has_metrics` is rejected). **API:** `/api/jobs/` list entries expose `performance` (not `has_metrics`); sort with `order_by=performance_sort_rank` or `-performance_sort_rank`. |
| `hpcperfstats/site/machine/tests/test_job_detail_staff_sample_count.py` | Staff-only `staff_metrics_distinct_time_count` on `job_detail` JSON (mocked ORM; no live DB required). |
| `hpcperfstats/site/frontend` | React SPA unit tests (Vitest): `npm test` from that directory. Vitest picks up `*.test.jsx` / `*.test.js` under `src/` (pages, components, utils), including `api.test.js` (fetch/CSRF/401 paths), `components/ExtendedSearch.test.jsx`, `normalize-job-list-histogram-entry.test.js`, `table-sort-a11y.test.js`, `job-list-route-title-context.test.js`, `Search.test.jsx` (browse-by-time **Calendar** tab first and selected by default), `JobList.test.jsx` (includes narrow-viewport Jobs/Charts tab behavior), `JobDetail.test.jsx` (job detail **Job data** inner tabs—including per-plot tabs (Summary, Heatmap, rooflines) before Bokeh assertions—progressive plot loading, and metrics tab single- vs two-table layout), `src/utils/jobMetricDisplayLabels.test.js` (short labels for job-level metrics table), and `useDocumentTitle.test.js`. `src/setupTests.js` stubs `HTMLElement.prototype.scrollIntoView` for jsdom. |
| `hpcperfstats-tools/tests/` | Client CLI/API helpers: `test_api_client.py`, `test_api_auth_headers.py`, `test_job_dataframe.py`, `test_sacct_gen_security.py`, plus `test_config.py` (INI `base_url`), `test_api_key_cache.py` (`~/.hpcperfstats-api` parsing), and `test_jobstats_cli.py` (`jobstats` formatting and `main()` exit codes). Run from the `hpcperfstats-tools` directory with `python -m pytest`. |

### Monitor event metadata (frontend tooltips)

When approved monitor changes add or rename `host_data.event` strings, regenerate the bundled catalog from the repo root:

```bash
python3 hpcperfstats/site/frontend/src/utils/generate-variable-metadata-monitor-events.py
```

Then follow `.cursor/rules/variable-metadata-*.mdc` for merging into `variableMetadata.js` and operator docs (`docs/regenerate_monitor_variables_catalog.py`, etc.).

## Test runners

### `run_tests.py` (default local runner)

`run_tests.py` wraps `pytest` and is the easiest default runner:

- `python run_tests.py --no-django` ignores `hpcperfstats/site/machine/tests`
- `python run_tests.py` runs `pytest -v hpcperfstats` by default
- extra pytest args are forwarded (for example `python run_tests.py -k metrics`)

### `tests/run_web_e2e_workflow.sh` (compose E2E runner)

Use this for web-page E2E modules:

- `hpcperfstats/site/machine/tests/test_web_pages_e2e.py` (includes a module-level `test_job_detail_api_includes_staff_metrics_distinct_time_count_for_staff` that does not use the `django_db` class marker, so it can run without a reachable Postgres host)
- `hpcperfstats/site/machine/tests/test_web_pages_browser_e2e.py` (uses a minimal static `index.html` stub for `/machine/*` that mirrors key SPA affordances—staff menu, plot-unavailable copy, keyboard-friendly plot error disclosure—rather than the full Vite bundle)

The workflow script handles Docker lifecycle and runs both files in one session:

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

Implementation detail: `tests/run_db_pytest_inner.sh` runs inside the `web` container; the project directory is **bind-mounted** to `/home/hpcperfstats` so pytest uses your working tree (conftest, migrations, tests) without rebuilding the image. Extra pytest arguments are passed via a mounted temp file (`/tmp/hpcperfstats_pytest_extra_args`). The same mount is used by `run_redis_cache_pytest_workflow.sh` and `run_web_e2e_workflow.sh`.

### `tests/run_redis_cache_pytest_workflow.sh` (live Redis cache integration)

Runs `hpcperfstats/site/machine/tests/test_redis_cache_live.py` inside the `web` container with **`HPCPERFSTATS_PYTEST_LIVE_REDIS=1`**, so Django keeps **`RedisCache`** against the Compose **redis** service instead of switching to `LocMemCache` during pytest.

```bash
tests/run_redis_cache_pytest_workflow.sh
```

Without that env var, the live Redis tests are **skipped** so normal `python run_tests.py` on a laptop does not require Redis.

Options: `--keep-env`, `--skip-build`. Forward pytest args the same way as the DB workflow (`-- -vv`).

## Accessibility (WCAG 2.2 AA target)

The SPA and standalone HTML pages aim for **WCAG 2.2 Level AA** for in-app flows (keyboard, screen readers, zoom, contrast). **Bokeh canvas plots** and the **OAuth provider** have inherent limits; the UI mitigates with text alternatives, landmarks, and documented manual checks.

**Automated (frontend)**

```bash
cd hpcperfstats/site/frontend && npm test -- --run
```

Accessibility regressions are caught primarily by component/page tests and the Playwright flows above; full automated axe scans are not part of this repo (use browser extensions or an external audit pipeline if needed).

**Manual spot-check (each release or major UI change)**

Treat the following as a **release gate** alongside automated tests: run through the checklist below before tagging or deploying UI-heavy changes.

- Keyboard only: Tab through navbar, **Extended search** dialog (Escape closes, focus returns), **Find Job**, tables, pagination, job detail **Job data** plot tabs.
- Screen reader: VoiceOver (Safari) or NVDA (Firefox)—**Search jobs** → job list → job detail; confirm **route focus** lands on the page heading after navigation.
- Zoom: browser **200%** on job list and job detail; tables scroll inside `.table-responsive` where needed.

**Standalone pages**

- `/machine/api-key` (React): skip link, `<main>`, confirm before key rotation, live region when a new key is shown.
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
- **Django tests**: For a full run matching production hostnames (`db`, `redis`), use `tests/run_db_pytest_workflow.sh`. Host-side `python run_tests.py` needs a reachable Postgres matching `HPCPERFSTATS_INI` `[PORTAL]` (and typically `host=localhost` with a published port, not `host=db`).
- **Live Redis cache tests**: Optional; use `tests/run_redis_cache_pytest_workflow.sh` (sets `HPCPERFSTATS_PYTEST_LIVE_REDIS=1`).
- **Browser E2E tests**: Playwright/Chromium tooling (installed by the DB workflow or E2E workflow script unless skipped).

## Optional memory profiling (development)

Use these on a machine with DB/Redis available (for example the Docker Compose `web` service) when investigating RSS growth in long-lived workers or CLI jobs. They are not part of CI.

**tracemalloc** (stdlib) around a focused pytest node:

```bash
PYTHONPATH=. python -X tracemalloc=25 -m pytest -q \
  hpcperfstats/site/machine/tests/test_job_plots_timeout.py \
  --tb=no 2>&1 | tail -20
```

**memray** (install with `pip install memray` in the same environment) on a single test file:

```bash
PYTHONPATH=. memray run -m pytest -q hpcperfstats/site/machine/tests/test_job_plots_timeout.py
memray flamegraph memray-*.bin
```

For ingestion-style workloads, run `memray` against a short `sync_timedb` invocation or a targeted dbload test module instead of the full archive scan.

**Unused imports (ruff F401):** install [Ruff](https://docs.astral.sh/ruff/) in your environment (`pip install ruff`), then from the project root:

```bash
ruff check hpcperfstats --select F401
```

Use `ruff check hpcperfstats --select F401 --fix` only after reviewing the diff (tests and dynamic imports can confuse static analysis).

## Notes

- `pyproject.toml` exposes canonical runner paths under `[tool.hpcperfstats.testing]`: `web_e2e_runner`, `db_pytest_runner`, `redis_cache_pytest_runner`.
- Prefer the compose E2E runner for page-level tests so DB/Redis/networking match expected service topology.
