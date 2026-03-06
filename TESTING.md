# Testing hpcperfstats

## Quick start

From the project root (directory containing `pyproject.toml`):

```bash
# Unit tests only (no database required)
python run_tests.py --no-django

# Or with pytest directly (unit tests only)
PYTHONPATH=. pytest hpcperfstats/tests -v

# All tests (Django tests require PostgreSQL and HPCPERFSTATS_INI)
python run_tests.py
# or: PYTHONPATH=. pytest hpcperfstats -v
```

## Test layout

| Location | Description |
|---------|-------------|
| `hpcperfstats/tests/` | Unit tests: conf_parser, progress, analysis utils, dbload helpers. No Django/DB. |
| `hpcperfstats/site/machine/tests/` | Django tests: ORM helpers, jid_table, require PostgreSQL. |

## Requirements

- **Unit tests**: `pytest`, `pytest-django` (optional). A temporary INI is created automatically if `HPCPERFSTATS_INI` is not set.
- **Django tests**: PostgreSQL, valid `HPCPERFSTATS_INI` with `[PORTAL]` and `[DEFAULT]` sections.

## Optional: install test dependencies

```bash
pip install -e ".[test]"
```

Then run `pytest` or `python run_tests.py` from the project root.
