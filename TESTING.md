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
| `hpcperfstats/tests/` | Non-Django unit/integration tests (config parsing, service startup/health, listend behavior, sync helpers, cache/date/print/file-locking helpers, XALT models, API key mobile checks). |
| `hpcperfstats/analysis/**/test*.py` | Analysis/plot/metrics-focused tests (summary/heatmap/roofline behavior, hover tooltips, metrics helpers). |
| `hpcperfstats/site/machine/tests/` | Django + web tests (ORM/query/update helpers, job detail file-system llite vs NFS fallback, security headers, API/misc endpoints, SPA rendering, page and browser E2E tests). Includes `test_type_detail_api.py`, which compiles the type-detail `host_data` jid filter (exact jid OR null OR empty) with `django_db(databases=[])` so it does not need a live DB. |

## Test runners

### `run_tests.py` (default local runner)

`run_tests.py` wraps `pytest` and is the easiest default runner:

- `python run_tests.py --no-django` ignores `hpcperfstats/site/machine/tests`
- `python run_tests.py` runs `pytest -v hpcperfstats` by default
- extra pytest args are forwarded (for example `python run_tests.py -k metrics`)

### `tests/run_web_e2e_workflow.sh` (compose E2E runner)

Use this for web-page E2E modules:

- `hpcperfstats/site/machine/tests/test_web_pages_e2e.py`
- `hpcperfstats/site/machine/tests/test_web_pages_browser_e2e.py`

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

## Requirements

- **General**: Python 3.12+, `pip install -e ".[test]"`.
- **Django tests**: PostgreSQL/Redis availability and valid `HPCPERFSTATS_INI` with required sections (`[PORTAL]`, `[DEFAULT]`).
- **Browser E2E tests**: Playwright/Chromium tooling (installed by the E2E workflow script unless skipped).

## Notes

- `pyproject.toml` exposes the canonical web E2E runner path under `[tool.hpcperfstats.testing].web_e2e_runner`.
- Prefer the compose E2E runner for page-level tests so DB/Redis/networking match expected service topology.
