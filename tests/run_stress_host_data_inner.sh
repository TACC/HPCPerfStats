#!/usr/bin/env bash
# Invoked inside the web container by tests/run_stress_host_data_workflow.sh.
# Sets compose network + stress gate; migrates; runs tests/stress_host_data/.
set -euo pipefail
cd /home/hpcperfstats

export HPCPERFSTATS_COMPOSE_NETWORK=1
export HPCPERFSTATS_STRESS_HOST_DATA=1
export HPCPERFSTATS_STRESS_HOST_DATA_ROWS="${HPCPERFSTATS_STRESS_HOST_DATA_ROWS:-400000}"
export HPCPERFSTATS_STRESS_REPORT_DIR="${HPCPERFSTATS_STRESS_REPORT_DIR:-/home/hpcperfstats/artifacts/stress}"
mkdir -p "${HPCPERFSTATS_STRESS_REPORT_DIR}"

# Prefer editable install (matches local dev). Cloud-sync bind mounts can make
# egg-info writes fail (Errno 35). Then use repo root on PYTHONPATH and only
# install test extras (no writes under the project tree).
export PYTHONPATH="/home/hpcperfstats${PYTHONPATH:+:$PYTHONPATH}"
if ! pip install -e ".[test]" -q 2>/dev/null; then
  echo "pip install -e failed on mount; using PYTHONPATH=/home/hpcperfstats and pip install test extras only."
  pip install -q "pytest>=7.0" "pytest-django>=4.5" "pytest-cov>=4.0"
fi

cd /home/hpcperfstats

python hpcperfstats/site/manage.py migrate --noinput

ARGS=()
if [[ -s /tmp/hpcperfstats_pytest_extra_args ]]; then
  mapfile -t ARGS < /tmp/hpcperfstats_pytest_extra_args
fi

exec python -m pytest -v tests/stress_host_data "${ARGS[@]}" --tb=short
