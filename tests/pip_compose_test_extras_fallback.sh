#!/usr/bin/env bash
# Install test extras when `pip install -e ".[test]"` fails on a bind-mounted repo.
# Pins mirror pyproject.toml [project] + [project.optional-dependencies] test.
# Note: pytest-django 4.x is the *plugin* major version; Django framework is 6.x.
set -euo pipefail

pip_compose_test_extras_fallback() {
  pip install -q \
    "Django>=6.0.5,<7.0" \
    "pytest>=9.0" \
    "pytest-django>=4.12.0" \
    "pytest-cov>=7.1.0"
}
