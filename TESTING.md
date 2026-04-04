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

## Test layout

| Location | Description |
|---------|-------------|
| `hpcperfstats/tests/` | Non-Django unit/integration tests (config parsing, service startup/health, listend behavior, sync helpers, cache/date/print/file-locking helpers, XALT models, API key mobile checks, dbload `pigz`/row-builder helpers, Django `timezone.utc` shim). `test_conf_parser.py` covers `get_effective_cores()`, `get_metrics_pool_process_count()`, default `total_cores=40`, `get_parallel_db_prefetch_max_workers()` / `get_api_small_executor_max_workers()`, DB `CONN_MAX_AGE` and PostgreSQL `OPTIONS` builders, and `get_sync_ingest_pool_processes()` / `get_sync_archive_pool_processes()` caps. `test_pg_connection_stats_command.py` exercises the `pg_connection_stats` management command with a mocked DB connection (no live Postgres). `test_numa_topology.py`, `test_compose_cpu_layout.py`, and `test_apply_compose_numa_pinning.py` cover sysfs NUMA parsing, **single-node** topology, responsive linear CPU partitions, and `scripts/apply_compose_cpu_pinning.py --dry-run`. `test_sync_timedb_archive.py` includes deferred-archive / atomic-seal helpers, `tar tf` integrity checks, gzip restore, tar dedupe (largest wins per path), and helpers used for scheduled pigz + raw-file removal (`get_existing_archive_members_for_daily_archive`, `remove_verified_archived_raw_files`, `validate_sealed_daily_archive_for_raw_removal`); two tests call real `pigz` and skip when `pigz` is not on `PATH`. `test_monitor_analysis_typename_contract.py` asserts monitor `stats_type.st_name` values stay aligned with `INTEL_IMC_STATS_TYPES`, `ARM_IMC_STATS_TYPES`, and `INTEL_CORE_PMC_TYPES_ORDERED` (skips if `monitor/src` is absent). |
| `hpcperfstats/analysis/**/test*.py` | Analysis/plot/metrics-focused tests (summary/heatmap/roofline behavior, roofline peak inference from `host_data.type` schema keys, hover tooltips, shared job-window parsing, metrics helpers). `analysis/metrics/test_per_interval_rate.py` also covers node-imbalance variants (DRAM, LNET, GPU util/tensor) and GPU peak helpers. `analysis/test_bokeh_job_embed.py` is a pure-Python unit test for `bokeh_job_embed.figure_embed_kw` (no Django). |
| `hpcperfstats/site/machine/tests/` | Django + web tests (ORM/query/update helpers, job detail file-system llite vs NFS fallback, security headers, API/misc endpoints, SPA rendering, page and browser E2E tests). Includes `test_type_detail_api.py`, which asserts type-detail `host_data` ORM SQL does not reference `jid` (job scope is start/end time + accounting hosts) with `django_db(databases=[])` so it does not need a live DB. `test_metrics.py` includes `test_job_metric_short_labels_cover_catalog`, which asserts every `job_metrics_catalog_entries()` metric has a matching entry in `job_metric_display_labels.JOB_METRIC_SHORT_LABELS` (parity with the frontend short-label map). `test_job_plot_artifacts.py` covers gzip-serialized Bokeh `json_item` persistence (`job_plot_artifact`), fingerprinting, and invalidation (requires Postgres like other `django_db` machine tests). |
| `hpcperfstats/site/machine/tests/test_job_detail_staff_sample_count.py` | Staff-only `staff_metrics_distinct_time_count` on `job_detail` JSON (mocked ORM; no live DB required). |
| `hpcperfstats/site/frontend` | React SPA unit tests (Vitest): `npm test` from that directory. Vitest picks up `*.test.jsx` / `*.test.js` under `src/` (pages, components, utils), including small pure helpers such as `normalize-job-list-histogram-entry.test.js`, `job-list-route-title-context.test.js`, `Search.test.jsx` (browse-by-time **Calendar** tab first and selected by default), `JobList.test.jsx` (includes narrow-viewport Jobs/Charts tab behavior), `JobDetail.test.jsx` (job detail **Job data** inner tabs—including per-plot tabs (Summary, Heatmap, rooflines) before Bokeh assertions—progressive plot loading, and metrics tab single- vs two-table layout), `src/utils/jobMetricDisplayLabels.test.js` (short labels for job-level metrics table), and `useDocumentTitle.test.js`. `src/setupTests.js` stubs `HTMLElement.prototype.scrollIntoView` for jsdom. |

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
- `hpcperfstats/site/machine/tests/test_web_pages_browser_e2e.py` (uses a minimal static `index.html` stub for `/machine/*` that mirrors key SPA affordances—staff menu, plot-unavailable copy, keyboard-friendly plot error disclosure—rather than the full Vite bundle; when `axe-core` is available it also runs a WCAG-tagged **axe-core** scan on stub `/machine/`, `/machine/job/123/`, `/machine/job/123/cpu/`, `/machine/year/2020/`, `/machine/admin_monitor/`, `/machine/job_monitor/`, and `/api-key/`. The Docker image copies `axe-core.min.js` into `hpcperfstats/site/machine/tests/support/` from the frontend build stage; host-side pytest can use `hpcperfstats/site/frontend/node_modules/axe-core/axe.min.js` after `npm ci`.)

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

## Accessibility (WCAG 2.2 AA target)

The SPA and standalone HTML pages aim for **WCAG 2.2 Level AA** for in-app flows (keyboard, screen readers, zoom, contrast). **Bokeh canvas plots** and the **OAuth provider** have inherent limits; the UI mitigates with text alternatives, landmarks, and documented manual checks.

**Automated (frontend)**

```bash
cd hpcperfstats/site/frontend && npm test -- --run
```

Vitest includes a minimal **axe-core** smoke test (`src/accessibility.smoke.test.jsx`) with `color-contrast` disabled under jsdom. **Playwright** browser E2E (`test_web_pages_browser_e2e.py`) runs axe with WCAG 2.0/2.1 A/AA tags (including color contrast) on the stub `/machine/` shell (`/machine/`, `/machine/job/123/`, `/machine/job/123/cpu/`, `/machine/year/2020/`, `/machine/admin_monitor/`, `/machine/job_monitor/`) and on `/api-key/` when the axe bundle is present.

**Manual spot-check (each release or major UI change)**

Treat the following as a **release gate** alongside automated tests: run through the checklist below before tagging or deploying UI-heavy changes.

- Keyboard only: Tab through navbar, **Extended search** dialog (Escape closes, focus returns), **Find Job**, tables, pagination, job detail **Job data** plot tabs.
- Screen reader: VoiceOver (Safari) or NVDA (Firefox)—**Search jobs** → job list → job detail; confirm **route focus** lands on the page heading after navigation.
- Zoom: browser **200%** on job list and job detail; tables scroll inside `.table-responsive` where needed.

**Standalone pages**

- `/api-key/`: skip link, `<main>`, confirm before key rotation, live region when a new key is shown.
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
- **Django tests**: PostgreSQL/Redis availability and valid `HPCPERFSTATS_INI` with required sections (`[PORTAL]`, `[DEFAULT]`).
- **Browser E2E tests**: Playwright/Chromium tooling (installed by the E2E workflow script unless skipped).

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

- `pyproject.toml` exposes the canonical web E2E runner path under `[tool.hpcperfstats.testing].web_e2e_runner`.
- Prefer the compose E2E runner for page-level tests so DB/Redis/networking match expected service topology.
