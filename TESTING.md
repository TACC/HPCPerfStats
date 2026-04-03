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
| `hpcperfstats/tests/` | Non-Django unit/integration tests (config parsing, service startup/health, listend behavior, sync helpers, cache/date/print/file-locking helpers, XALT models, API key mobile checks, dbload `pigz`/row-builder helpers, Django `timezone.utc` shim). |
| `hpcperfstats/analysis/**/test*.py` | Analysis/plot/metrics-focused tests (summary/heatmap/roofline behavior, roofline peak inference from `host_data.type` schema keys, hover tooltips, shared job-window parsing, metrics helpers). `analysis/metrics/test_per_interval_rate.py` also covers node-imbalance variants (DRAM, LNET, GPU util/tensor) and GPU peak helpers. `analysis/test_bokeh_job_embed.py` is a pure-Python unit test for `bokeh_job_embed.figure_embed_kw` (no Django). |
| `hpcperfstats/site/machine/tests/` | Django + web tests (ORM/query/update helpers, job detail file-system llite vs NFS fallback, security headers, API/misc endpoints, SPA rendering, page and browser E2E tests). Includes `test_type_detail_api.py`, which asserts type-detail `host_data` ORM SQL does not reference `jid` (job scope is start/end time + accounting hosts) with `django_db(databases=[])` so it does not need a live DB. |
| `hpcperfstats/site/machine/tests/test_job_detail_staff_sample_count.py` | Staff-only `staff_metrics_distinct_time_count` on `job_detail` JSON (mocked ORM; no live DB required). |
| `hpcperfstats/site/frontend` | React SPA unit tests (Vitest): `npm test` from that directory. Vitest picks up `*.test.jsx` / `*.test.js` under `src/` (pages, components, utils), including small pure helpers such as `normalize-job-list-histogram-entry.test.js` and `useDocumentTitle.test.js`. |

## Test runners

### `run_tests.py` (default local runner)

`run_tests.py` wraps `pytest` and is the easiest default runner:

- `python run_tests.py --no-django` ignores `hpcperfstats/site/machine/tests`
- `python run_tests.py` runs `pytest -v hpcperfstats` by default
- extra pytest args are forwarded (for example `python run_tests.py -k metrics`)

### `tests/run_web_e2e_workflow.sh` (compose E2E runner)

Use this for web-page E2E modules:

- `hpcperfstats/site/machine/tests/test_web_pages_e2e.py` (includes a module-level `test_job_detail_api_includes_staff_metrics_distinct_time_count_for_staff` that does not use the `django_db` class marker, so it can run without a reachable Postgres host)
- `hpcperfstats/site/machine/tests/test_web_pages_browser_e2e.py` (uses a minimal static `index.html` stub for `/machine/*` that mirrors key SPA affordances—staff menu, plot-unavailable copy—rather than the full Vite bundle)

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

Vitest includes a minimal **axe-core** smoke test (`src/accessibility.smoke.test.jsx`) with `color-contrast` disabled under jsdom (run full contrast checks in a real browser).

**Manual spot-check (each release or major UI change)**

- Keyboard only: Tab through navbar, **Extended search** dialog (Escape closes, focus returns), **Find Job**, tables, pagination, plot **Expand** / zoom close.
- Screen reader: VoiceOver (Safari) or NVDA (Firefox)—**Search jobs** → job list → job detail; confirm **route focus** lands on the page heading after navigation.
- Zoom: browser **200%** on job list and job detail; tables scroll inside `.table-responsive` where needed.

**Standalone pages**

- `/api-key/`: skip link, `<main>`, confirm before key rotation, live region when a new key is shown.
- Django **admin**: branding includes a link back to `/machine/`.

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
