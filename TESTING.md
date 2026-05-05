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
| `hpcperfstats/site/machine/tests/` | Django + web tests (ORM/query/update helpers, security headers, API/misc endpoints, SPA rendering, page and browser E2E tests). |

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

## Local Job Detail seed (`HPCPerfStatsdDataSample`)

To load the bundled daemon sample (`hpcperfstats/dbload/tests/HPCPerfStatsdDataSample`) into PostgreSQL so Job Detail can join `job_data` and `host_data`, run from the repo root with a reachable DB and ini:

```bash
HPCPERFSTATS_INI=/path/to/hpcperfstats.ini PYTHONPATH=. \
  python3 hpcperfstats/dbload/ingest_sample_test_data.py
```

The script rewrites the sample host to match `DEFAULT.host_name_ext` in your ini (so short node + suffix matches `jid_table`), ingests stats via `add_stats_file_to_db`, then inserts a synthetic sacct row via `sync_acct_from_content`. Set `SAMPLE_TEST_USERNAME` if the accounting user should differ from your login.

Use a **fresh database** or delete conflicting `job_data` / `host_data` rows when re-seeding or switching fixtures; duplicate timestamps may otherwise be skipped by `add_stats_file_to_db`, and re-ingest behavior depends on existing rows.

## Notes

- `pyproject.toml` exposes the canonical web E2E runner path under `[tool.hpcperfstats.testing].web_e2e_runner`.
- Prefer the compose E2E runner for page-level tests so DB/Redis/networking match expected service topology.
