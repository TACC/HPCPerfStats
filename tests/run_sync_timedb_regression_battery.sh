#!/usr/bin/env bash
# Consolidated sync_timedb stall-regression battery (queue-orchestrator cutover).
# Host pytest only — no compose DB required for this -k filter scope.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
WS_ROOT="$(cd "$ROOT_DIR/.." && pwd)"
PYTHON="${WS_ROOT}/.venv/bin/python3"
TEST_RUNS="${WS_ROOT}/test_runs"
mkdir -p "$TEST_RUNS"

if [[ ! -x "$PYTHON" ]]; then
  echo "Missing venv interpreter: $PYTHON" >&2
  echo "Create/repair per workspace-layout-and-python-env.mdc" >&2
  exit 1
fi

LOG="${TEST_RUNS}/day-close-loop-regression-battery-$(date +%Y%m%d-%H%M%S).log"
echo "sync_timedb regression battery → $LOG"

PYTEST_EXTRA=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --)
      shift
      PYTEST_EXTRA=("$@")
      break
      ;;
    -h|--help)
      cat <<'EOF'
Run consolidated sync_timedb stall regression pytest battery (host venv).

Usage:
  tests/run_sync_timedb_regression_battery.sh [-- pytest_extra_args...]

Logs to: <workspace_root>/test_runs/day-close-loop-regression-battery-<timestamp>.log

Mandatory before closing any sync_timedb ingest/archive stall fix (sync-timedb-change-regression-gate.mdc).
Slice 4+: queue orchestrator + job:v1 + B-09 predicate architecture (supervisor/janitor B
tests removed from this battery).
EOF
      exit 0
      ;;
    *)
      PYTEST_EXTRA+=("$1")
      shift
      ;;
  esac
done

# Queue cutover battery: job schema, reconstruct/discover, orchestrator flock/entry,
# B-09 predicates, plus durable archive/members/find/jid helpers still valid without
# the retired supervisor_loop / ArchiveJanitor coordinator.
BATTERY_FILTER='test_arch_ or architecture or find_stats or printf or rescan_mtime or rescan_full or gfind or jid or host_scoped or flock or orchestrator or job_queue or reconstruct or streaming or ingest_identity or lease or ranged or hot_cap or catchup or empty_job or checkpoint_sidecar or day_close_min_age or discovered_incomplete or remaining_raw_enqueues or claim_is_atomic or populate_pool or fingerprint or verify_failure or allkeys or census or append_jobs or drain_subprocess or already_held or handoff'

set +e
"$PYTHON" -m pytest -q \
  hpcperfstats/tests/test_sync_timedb_job_queue.py \
  hpcperfstats/tests/test_sync_timedb_job_queue_redis.py \
  hpcperfstats/tests/test_sync_timedb_job_discover.py \
  hpcperfstats/tests/test_sync_timedb_queue_orchestrator.py \
  hpcperfstats/tests/test_sync_timedb_append_tar_race.py \
  hpcperfstats/tests/test_sync_timedb_subprocess_hardening.py \
  hpcperfstats/tests/test_sync_timedb_archive_members_redis_client.py \
  hpcperfstats/tests/test_sync_timedb_architecture_contract.py \
  hpcperfstats/tests/test_sync_timedb_archive.py \
  hpcperfstats/tests/test_sync_timedb_archive_members_redis.py \
  hpcperfstats/tests/test_sync_timedb_stats_find.py \
  hpcperfstats/tests/test_sync_timedb_jid.py \
  hpcperfstats/tests/test_sync_timedb_day_raw_removal.py \
  -k "$BATTERY_FILTER" \
  "${PYTEST_EXTRA[@]}" \
  2>&1 | tee "$LOG"
status=${PIPESTATUS[0]}
set -e

if [[ "$status" -eq 0 ]]; then
  echo "BATTERY OK"
  echo "Battery exit=$status log=$LOG"
  exit 0
fi
echo "Battery exit=$status log=$LOG"
exit "$status"
