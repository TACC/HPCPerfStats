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

# prewarm_retries covers inflight + members_n>0 cold Redis; l1_cold_redis / fingerprint
# cover L1-bypass-warm and post_finalize pending-reconcile fingerprint refresh.
BATTERY_FILTER='handoff or handoff_priority or archive_finalize or post_finalize or phase_done or same_boot or closed_raw or chunk_gate or orphan_inflight or tar_drop or pipeline_complete or rescan or defer_day_close or manifest_fast or closed_raw_persists or reconcile_orphan or test_arch_ or architecture or ingest_stall_watchdog or oldest_day_unprocessed_frozen or budget_exit_nonblocking or reconcile_before_discover or on_disk_equals_unprocessed or never_streams_sealed_when_populate_pool_down or redis_populate_before_idle_ghost or lock_held_stall_recoverable or find_stats or printf or rescan_mtime or rescan_full or rescan_every_chunks or gfind or disabled_day_close or pending_maintenance or chunk_stall or allow_full or day_scoped or async_inflight or pending_rescan or identity_drift or prewarm_fails_loud or prewarm_retries or l1_cold_redis or fingerprint_updates or reconcile_fingerprint or jid or host_scoped'

set +e
"$PYTHON" -m pytest -q \
  hpcperfstats/tests/test_sync_timedb_supervisor.py \
  hpcperfstats/tests/test_sync_timedb_day_raw_removal.py \
  hpcperfstats/tests/test_sync_timedb_day_close_manifest.py \
  hpcperfstats/tests/test_sync_timedb_archive.py \
  hpcperfstats/tests/test_sync_timedb_archive_members_redis.py \
  hpcperfstats/tests/test_sync_timedb_janitor.py \
  hpcperfstats/tests/test_sync_timedb_architecture_contract.py \
  hpcperfstats/tests/test_sync_timedb_stats_find.py \
  hpcperfstats/tests/test_sync_timedb_chunk_stall_faststart.py \
  hpcperfstats/tests/test_sync_timedb_jid.py \
  -k "$BATTERY_FILTER" \
  "${PYTEST_EXTRA[@]}" \
  2>&1 | tee "$LOG"
status=${PIPESTATUS[0]}
set -e

echo "Battery exit=$status log=$LOG"
exit "$status"
