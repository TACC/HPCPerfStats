#!/usr/bin/env bash
# Invoked inside the web container by tests/run_update_metrics_diagnosis_workflow.sh.
set -euo pipefail
cd /home/hpcperfstats

export HPCPERFSTATS_COMPOSE_NETWORK=1
# Optional: write a copy of the diagnosis JSON to the bind-mounted repo (see TESTING.md).
export HPCPERFSTATS_UM_DIAG_JSON_OUT="${HPCPERFSTATS_UM_DIAG_JSON_OUT:-/home/hpcperfstats/test_runs/diagnosis/update_metrics_diagnosis.json}"
mkdir -p "$(dirname "${HPCPERFSTATS_UM_DIAG_JSON_OUT}")"

compose_inner_pip_install

python hpcperfstats/site/manage.py migrate --noinput

ARGS=()
if [[ -s /tmp/hpcperfstats_pytest_extra_args ]]; then
  mapfile -t ARGS < /tmp/hpcperfstats_pytest_extra_args
fi

exec python -m pytest -v \
  hpcperfstats/site/lib/machine/tests/test_update_metrics_diagnosis_compose.py \
  "${ARGS[@]}" --tb=short
