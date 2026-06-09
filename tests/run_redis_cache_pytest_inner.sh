#!/usr/bin/env bash
# Invoked inside the web container by tests/run_redis_cache_pytest_workflow.sh.
set -euo pipefail
cd /home/hpcperfstats

export HPCPERFSTATS_COMPOSE_NETWORK=1
export HPCPERFSTATS_PYTEST_LIVE_REDIS=1

compose_inner_pip_install

ARGS=()
if [[ -s /tmp/hpcperfstats_pytest_extra_args ]]; then
  mapfile -t ARGS < /tmp/hpcperfstats_pytest_extra_args
fi

exec python -m pytest -q \
  hpcperfstats/site/machine/tests/test_redis_cache_live.py \
  hpcperfstats/tests/test_sync_timedb_archive_members_redis.py::test_archive_members_redis_populate_single_flight_compose \
  "${ARGS[@]}"
