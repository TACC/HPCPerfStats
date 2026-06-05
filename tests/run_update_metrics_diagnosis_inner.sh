#!/usr/bin/env bash
# Invoked inside the web container by tests/run_update_metrics_diagnosis_workflow.sh.
set -euo pipefail
cd /home/hpcperfstats

export HPCPERFSTATS_COMPOSE_NETWORK=1
# Optional: write a copy of the diagnosis JSON to the bind-mounted repo (see TESTING.md).
export HPCPERFSTATS_UM_DIAG_JSON_OUT="${HPCPERFSTATS_UM_DIAG_JSON_OUT:-/home/hpcperfstats/tmp/update_metrics_diagnosis.json}"

export PYTHONPATH="/home/hpcperfstats${PYTHONPATH:+:$PYTHONPATH}"
if ! pip install -e ".[test]" -q 2>/dev/null; then
  echo "pip install -e failed on mount; using PYTHONPATH=/home/hpcperfstats and pip install test extras only."
  # shellcheck source=tests/pip_compose_test_extras_fallback.sh
  source tests/pip_compose_test_extras_fallback.sh
  pip_compose_test_extras_fallback
fi

python hpcperfstats/site/manage.py migrate --noinput

ARGS=()
if [[ -s /tmp/hpcperfstats_pytest_extra_args ]]; then
  mapfile -t ARGS < /tmp/hpcperfstats_pytest_extra_args
fi

exec python -m pytest -v \
  hpcperfstats/site/machine/tests/test_update_metrics_diagnosis_compose.py \
  "${ARGS[@]}" --tb=short
