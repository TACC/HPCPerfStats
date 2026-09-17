#!/usr/bin/env bash
# Run standard rootless Podman compose-backed test workflows.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# shellcheck source=compose_test_cmd.sh
. "$(dirname "${BASH_SOURCE[0]}")/compose_test_cmd.sh"
podman_runtime_require

mkdir -p test_runs
LOG="test_runs/test_run_log_podman_compose.md"
SKIP_BUILD="${SKIP_BUILD:-0}"
if [[ $# -gt 0 && "$1" != --* ]]; then
  LOG="$1"
  shift
fi
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-build)
      SKIP_BUILD=1
      ;;
    -h|--help)
      echo "Usage: tests/run_all_compose_workflows.sh [log-path] [--skip-build]"
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 2
      ;;
  esac
  shift
done
: >"$LOG"

log() { echo "$*" | tee -a "$LOG"; }
run_step() {
  local name="$1"
  shift
  log ""
  log "## $name"
  log '```'
  log "\$ $*"
  set +e
  "$@" 2>&1 | tee -a "$LOG"
  local ec=${PIPESTATUS[0]}
  set -e
  log '```'
  log "Exit: $ec"
  return "$ec"
}

log "# Compose workflow run ($(date -u '+%Y-%m-%d %H:%M:%S UTC'))"
log "Compose project=${HPCPERFSTATS_COMPOSE_PROJECT}"
"${PODMAN[@]}" version | tee -a "$LOG"
"${PODMAN_COMPOSE[@]}" version | tee -a "$LOG"

if [[ "$SKIP_BUILD" == "1" ]]; then
  DB_EXTRA=(--skip-build)
  REST_EXTRA=(--skip-build)
else
  DB_EXTRA=()
  REST_EXTRA=(--skip-build)
fi

FAIL=0
run_step "1. run_db_pytest_workflow.sh" tests/run_db_pytest_workflow.sh "${DB_EXTRA[@]}" || FAIL=$?
run_step "2. run_redis_cache_pytest_workflow.sh" \
  tests/run_redis_cache_pytest_workflow.sh "${REST_EXTRA[@]}" || FAIL=$?
run_step "3. run_web_e2e_workflow.sh" \
  tests/run_web_e2e_workflow.sh "${REST_EXTRA[@]}" || FAIL=$?
run_step "4. run_pipeline_e2e_workflow.sh" \
  tests/run_pipeline_e2e_workflow.sh "${REST_EXTRA[@]}" || FAIL=$?
run_step "5. run_update_metrics_diagnosis_workflow.sh" \
  tests/run_update_metrics_diagnosis_workflow.sh "${REST_EXTRA[@]}" || FAIL=$?
run_step "6. run_stress_host_data_workflow.sh" \
  env HPCPERFSTATS_STRESS_HOST_DATA_ROWS=400000 \
  tests/run_stress_host_data_workflow.sh "${REST_EXTRA[@]}" || FAIL=$?
run_step "7. run_security_audit_workflow.sh" \
  tests/run_security_audit_workflow.sh "${REST_EXTRA[@]}" || FAIL=$?
run_step "8. run_bokeh_browser_workflow.sh" \
  tests/run_bokeh_browser_workflow.sh "${REST_EXTRA[@]}" || FAIL=$?

log ""
log "## Summary"
if [[ "$FAIL" -eq 0 ]]; then
  log "All workflows passed."
else
  log "At least one workflow failed (last non-zero exit retained in FAIL=$FAIL)."
fi

exit "$FAIL"
