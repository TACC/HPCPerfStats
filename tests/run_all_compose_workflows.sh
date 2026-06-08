#!/usr/bin/env bash
# Run standard compose-backed test workflows (Colima / docker-compose).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# shellcheck source=colima_compose_teardown.sh
. "$(dirname "${BASH_SOURCE[0]}")/colima_compose_teardown.sh"
colima_export_docker_env

mkdir -p test_runs
LOG="${1:-test_runs/test_run_log_colima_compose.md}"
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
log "DOCKER_HOST=$DOCKER_HOST"
colima status 2>&1 | head -5 | tee -a "$LOG" || true
docker-compose version | tee -a "$LOG"

SKIP_BUILD="${SKIP_BUILD:-}"
if [[ -n "${SKIP_BUILD}" ]]; then
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

log ""
log "## Summary"
if [[ "$FAIL" -eq 0 ]]; then
  log "All workflows passed."
else
  log "At least one workflow failed (last non-zero exit retained in FAIL=$FAIL)."
fi

log ""
log "## Final Colima Docker cleanup"
bash tests/colima_docker_cleanup.sh 2>&1 | tee -a "$LOG"

exit "$FAIL"
