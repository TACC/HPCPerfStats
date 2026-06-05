#!/usr/bin/env bash
# Invoked inside the web container by tests/run_db_pytest_workflow.sh.
set -euo pipefail
cd /home/hpcperfstats

export HPCPERFSTATS_COMPOSE_NETWORK=1

if ! pip install -q -e ".[test]"; then
  echo "pip install -e failed on mount; using PYTHONPATH fallback and test extras only."
  export PYTHONPATH=/home/hpcperfstats
  # shellcheck source=tests/pip_compose_test_extras_fallback.sh
  source tests/pip_compose_test_extras_fallback.sh
  pip_compose_test_extras_fallback
fi

if [[ "${DOCKER_PYTEST_SKIP_BROWSER:-0}" != "1" ]]; then
  python -m playwright install --with-deps chromium
fi

if [[ "${DOCKER_PYTEST_SKIP_MIGRATE:-0}" != "1" ]]; then
  python hpcperfstats/site/manage.py migrate --noinput
fi

if [[ -n "${DOCKER_PYTEST_SEED_CMD:-}" ]]; then
  bash -lc "$DOCKER_PYTEST_SEED_CMD"
fi

IGNORE=()
if [[ "${DOCKER_PYTEST_SKIP_BROWSER:-0}" == "1" ]]; then
  IGNORE+=(--ignore=hpcperfstats/site/machine/tests/test_web_pages_browser_e2e.py)
fi

ARGS=()
if [[ -s /tmp/hpcperfstats_pytest_extra_args ]]; then
  mapfile -t ARGS < /tmp/hpcperfstats_pytest_extra_args
fi

exec python -m pytest -q hpcperfstats "${IGNORE[@]}" "${ARGS[@]}"
