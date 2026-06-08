#!/usr/bin/env bash
# Sourced by compose *_inner.sh scripts (via stdin stream, not from the bind mount).
# shellcheck disable=SC2034
set -euo pipefail

compose_inner_pip_test_extras() {
  pip install -q \
    "Django>=6.0.6,<7.0" \
    "pytest>=9.0" \
    "pytest-django>=4.12.0" \
    "pytest-cov>=7.1.0"
}

compose_inner_pip_install() {
  export PYTHONPATH="/home/hpcperfstats${PYTHONPATH:+:$PYTHONPATH}"
  if [[ "${DOCKER_PYTEST_BIND_MOUNT:-0}" == "1" ]]; then
    compose_inner_pip_test_extras
    return 0
  fi
  if ! pip install -q -e ".[test]"; then
    echo "pip install -e failed; using PYTHONPATH fallback and test extras only."
    compose_inner_pip_test_extras
  fi
}
