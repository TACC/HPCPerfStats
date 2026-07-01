#!/usr/bin/env bash
# Consolidated sync_timedb stall-regression battery (day-close plan Phase 1b).
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
EOF
      exit 0
      ;;
    *)
      PYTEST_EXTRA+=("$1")
      shift
      ;;
  esac
done

BATTERY_FILTER='handoff or handoff_priority or archive_finalize or post_finalize or phase_done or same_boot or closed_raw or chunk_gate or orphan_inflight or tar_drop or pipeline_complete or rescan or defer_day_close or manifest_fast or closed_raw_persists or reconcile_orphan or test_arch_ or architecture or ingest_stall_watchdog or oldest_day_unprocessed_frozen'

set +e
"$PYTHON" -m pytest -q \
  hpcperfstats/tests/test_sync_timedb_supervisor.py \
  hpcperfstats/tests/test_sync_timedb_day_raw_removal.py \
  hpcperfstats/tests/test_sync_timedb_async_day_close.py \
  hpcperfstats/tests/test_sync_timedb_archive.py \
  hpcperfstats/tests/test_sync_timedb_janitor.py \
  hpcperfstats/tests/test_sync_timedb_architecture_contract.py \
  -k "$BATTERY_FILTER" \
  "${PYTEST_EXTRA[@]}" \
  2>&1 | tee "$LOG"
status=${PIPESTATUS[0]}
set -e

echo "Battery exit=$status log=$LOG"
exit "$status"
