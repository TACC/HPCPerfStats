#!/usr/bin/env bash
# Day-close space-reclaim soak: assert open_tar/dual decline without prod wall-clock.
# Default: host pytest (no compose). Opt-in compose when HPCPERFSTATS_ENABLE_LOCAL_DOCKER=1.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
WS_ROOT="$(cd "$ROOT_DIR/.." && pwd)"
PYTHON="${WS_ROOT}/.venv/bin/python3"
TEST_RUNS="${WS_ROOT}/test_runs"
mkdir -p "$TEST_RUNS"

if [[ ! -x "$PYTHON" ]]; then
  echo "Missing venv interpreter: $PYTHON" >&2
  exit 1
fi

LOG="${TEST_RUNS}/day-close-space-reclaim-soak-$(date +%Y%m%d-%H%M%S).log"
echo "day-close space-reclaim soak → $LOG"

usage() {
  cat <<'EOF'
Run day-close space-reclaim soak tests (open_tar / dual reclaim oracle).

Usage:
  tests/run_sync_timedb_day_close_soak_workflow.sh [-- pytest_extra_args...]

Default (no compose): host pytest under tests/sync_timedb_day_close_soak/ plus
focused orchestrator reclaim regressions.

Compose (requires HPCPERFSTATS_ENABLE_LOCAL_DOCKER=1 and Podman):
  HPCPERFSTATS_DAY_CLOSE_SOAK_COMPOSE=1 tests/run_sync_timedb_day_close_soak_workflow.sh

Logs to: <workspace_root>/test_runs/day-close-space-reclaim-soak-<timestamp>.log
EOF
}

PYTEST_EXTRA=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      PYTEST_EXTRA=("$@")
      break
      ;;
    *)
      PYTEST_EXTRA+=("$1")
      shift
      ;;
  esac
done

HOST_TARGETS=(
  tests/sync_timedb_day_close_soak
  hpcperfstats/tests/test_sync_timedb_queue_orchestrator.py::test_day_close_skips_tar_drop_when_post_seal_returns_false
  hpcperfstats/tests/test_sync_timedb_queue_orchestrator.py::test_day_close_inventory_shrink_after_delete_and_tar_drop
  hpcperfstats/tests/test_sync_timedb_queue_orchestrator.py::test_day_close_reseals_after_raw_delete_when_zst_missing
  hpcperfstats/tests/test_sync_timedb_queue_orchestrator.py::test_day_close_phase_done_dual_reclaims_open_tar
  hpcperfstats/tests/test_sync_timedb_day_raw_removal.py::test_apply_batch_delete_reopens_phase_done_verified_pending
)

if [[ "${HPCPERFSTATS_DAY_CLOSE_SOAK_COMPOSE:-0}" == "1" ]]; then
  if [[ "${HPCPERFSTATS_ENABLE_LOCAL_DOCKER:-0}" != "1" ]]; then
    echo "Compose soak refused: set HPCPERFSTATS_ENABLE_LOCAL_DOCKER=1" >&2
    exit 78
  fi
  COMPOSE_HELPER="$(dirname "${BASH_SOURCE[0]}")/compose_test_cmd.sh"
  # shellcheck source=compose_test_cmd.sh
  . "$COMPOSE_HELPER"
  compose_prepare_bind_mount || exit 1
  compose_run_inner_script_prepare_env
  compose_web_repo_bind_mount_args || exit 1
  compose_test up -d db redis
  set +e
  compose_test run --rm -T \
    -e HPCPERFSTATS_COMPOSE_NETWORK=1 \
    "${compose_run_inner_script_bind_mount_env[@]}" \
    "${compose_web_repo_bind_mount_args[@]}" \
    pipeline \
    su hpcperfstats -c \
    '/opt/python3.14t/bin/python -m pytest tests/sync_timedb_day_close_soak -q --tb=short' \
    2>&1 | tee "$LOG"
  status=${PIPESTATUS[0]}
  set -e
else
  set +e
  "$PYTHON" -m pytest -q --tb=short \
    "${HOST_TARGETS[@]}" \
    ${PYTEST_EXTRA[@]+"${PYTEST_EXTRA[@]}"} \
    2>&1 | tee "$LOG"
  status=${PIPESTATUS[0]}
  set -e
fi

if [[ "$status" -eq 0 ]]; then
  echo "DAY_CLOSE_SOAK OK"
  echo "Soak exit=$status log=$LOG"
else
  echo "DAY_CLOSE_SOAK FAIL exit=$status log=$LOG" >&2
fi
exit "$status"
