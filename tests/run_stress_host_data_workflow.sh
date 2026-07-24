#!/usr/bin/env bash
# Default way to run stress_host_data tests: PostgreSQL + Redis on the compose network.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
# shellcheck source=colima_compose_teardown.sh
. "$(dirname "${BASH_SOURCE[0]}")/colima_compose_teardown.sh"
# shellcheck source=compose_test_cmd.sh
. "$(dirname "${BASH_SOURCE[0]}")/compose_test_cmd.sh"
colima_export_docker_env

KEEP_ENV=0
SKIP_BUILD=0
PYTEST_EXTRA=()
ARGS_FILE=""
ARGS_FILE_OWNED=0

usage() {
  cat <<'EOF'
Run massive host_data stress tests in Docker (db + redis hostnames resolve; LocMem bypass not used for Redis paths).

This is the default entry point for non-unit integration testing of tests/stress_host_data/.

Usage:
  tests/run_stress_host_data_workflow.sh [options] [-- pytest_extra_args...]

Options:
  --keep-env      Keep compose services/volumes after run
  --skip-build    Skip docker-compose build web
  -h, --help      Show this help

Environment (optional; forwarded into the web container):
  HPCPERFSTATS_STRESS_HOST_DATA_ROWS     Default 400000 (smoke). Multiple of 40 × N hosts (see N_HOSTS).
  HPCPERFSTATS_STRESS_USE_TIME_SCALE     Set to 1 to size by DURATION/INTERVAL instead of row target.
  HPCPERFSTATS_STRESS_N_HOSTS            Host count (default 1 in row mode).
  HPCPERFSTATS_STRESS_INTERVAL_SEC       Sample spacing (default 1 s in row mode; use 30 with time scale).
  HPCPERFSTATS_STRESS_DURATION_SEC       Window length in seconds when USE_TIME_SCALE=1 (default 1800).
  HPCPERFSTATS_STRESS_JID                Job id string (default stress_um_pipeline).
  HPCPERFSTATS_STRESS_REPORT_DIR         JSON report directory (default test_runs/stress under repo mount).
  HPCPERFSTATS_STRESS_EXPLAIN            Set to 1 for one EXPLAIN (FORMAT JSON) snapshot in the report.
  HPCPERFSTATS_STRESS_MANUAL_PLOT_SANITY Optional second-phase plot timing (uses STRESS_PLOT_SEC cap).
  HPCPERFSTATS_STRESS_PLOT_SEC           Per-plot cap when MANUAL_PLOT_SANITY=1.
  HPCPERFSTATS_STRESS_SAMPLE_PATH        Override path for R_ref sample (monitor_sample_density).
  HPCPERFSTATS_LARGE_JOB_HOST_DATA_ROWS / HPCPERFSTATS_LARGE_JOB_TIME_BUCKETS  jid_table sampling (conf_parser).

Examples:
  tests/run_stress_host_data_workflow.sh --skip-build
  HPCPERFSTATS_STRESS_HOST_DATA_ROWS=800000 tests/run_stress_host_data_workflow.sh -- --no-cov

See also: tests/run_db_pytest_workflow.sh (full tree), tests/run_redis_cache_pytest_workflow.sh (live Redis).
EOF
}

cleanup_args_file() {
  if [[ "$ARGS_FILE_OWNED" -eq 1 && -n "$ARGS_FILE" && -f "$ARGS_FILE" ]]; then
    rm -f "$ARGS_FILE"
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --)
      shift
      while [[ $# -gt 0 ]]; do
        PYTEST_EXTRA+=("$1")
        shift
      done
      break
      ;;
    --keep-env)
      KEEP_ENV=1
      shift
      ;;
    --skip-build)
      SKIP_BUILD=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    -*)
      echo "Unknown option: $1" >&2
      usage
      exit 2
      ;;
    *)
      PYTEST_EXTRA+=("$1")
      shift
      ;;
  esac
done

cleanup() {
  cleanup_args_file
  compose_cleanup_bind_mount
  if [[ "$KEEP_ENV" -eq 1 ]]; then
    echo "Keeping compose environment (--keep-env)."
    return
  fi
  colima_compose_teardown "${COMPOSE_TEST[@]}"
}
trap cleanup EXIT

if [[ ! -f hpcperfstats.ini ]]; then
  echo "hpcperfstats.ini not found; copying from hpcperfstats.ini.example"
  cp hpcperfstats.ini.example hpcperfstats.ini
fi

# Colima virtiofs only shares $HOME by default; macOS mktemp under /var/folders
# is not mountable and Docker creates a directory at the container path instead.
mkdir -p "${HOME}/.cache/hpcperfstats-compose"
ARGS_FILE="$(mktemp "${HOME}/.cache/hpcperfstats-compose/pytest-extra-args.XXXXXX")"
ARGS_FILE_OWNED=1
if [[ ${#PYTEST_EXTRA[@]} -gt 0 ]]; then
  printf '%s\n' "${PYTEST_EXTRA[@]}" > "$ARGS_FILE"
else
  : > "$ARGS_FILE"
fi

export HPCPERFSTATS_STRESS_HOST_DATA_ROWS="${HPCPERFSTATS_STRESS_HOST_DATA_ROWS:-400000}"

compose_prepare_bind_mount
compose_run_inner_script_prepare_env

echo "Resetting Docker compose state and volumes..."
compose_test down -v --remove-orphans

if [[ "$SKIP_BUILD" -eq 0 ]]; then
  echo "Rebuilding web image..."
  compose_test build web
fi

echo "Starting db/redis..."
compose_test up -d db redis

echo "Waiting for healthy db/redis..."
db_health=""
redis_health=""
for _ in $(seq 1 60); do
  db_id="$(compose_test ps -q db)"
  redis_id="$(compose_test ps -q redis)"

  if [[ -n "$db_id" && -n "$redis_id" ]]; then
    db_health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}starting{{end}}' "$db_id" 2>/dev/null || true)"
    redis_health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}starting{{end}}' "$redis_id" 2>/dev/null || true)"
    if [[ "$db_health" == "healthy" && "$redis_health" == "healthy" ]]; then
      echo "db and redis are healthy."
      break
    fi
  fi
  sleep 2
done

if [[ "$db_health" != "healthy" || "$redis_health" != "healthy" ]]; then
  echo "Timed out waiting for db/redis health." >&2
  compose_test ps
  exit 1
fi

echo "Running stress_host_data tests in web container (HPCPERFSTATS_STRESS_HOST_DATA_ROWS=${HPCPERFSTATS_STRESS_HOST_DATA_ROWS})..."
compose_run_inner_script tests/run_stress_host_data_inner.sh \
  -e HPCPERFSTATS_STRESS_HOST_DATA_ROWS \
  -e HPCPERFSTATS_STRESS_USE_TIME_SCALE \
  -e HPCPERFSTATS_STRESS_N_HOSTS \
  -e HPCPERFSTATS_STRESS_INTERVAL_SEC \
  -e HPCPERFSTATS_STRESS_DURATION_SEC \
  -e HPCPERFSTATS_STRESS_JOB_END_MARGIN_SEC \
  -e HPCPERFSTATS_STRESS_PROBE_AFTER_END_SEC \
  -e HPCPERFSTATS_STRESS_JID \
  -e HPCPERFSTATS_STRESS_REPORT_DIR \
  -e HPCPERFSTATS_STRESS_EXPLAIN \
  -e HPCPERFSTATS_STRESS_MANUAL_PLOT_SANITY \
  -e HPCPERFSTATS_STRESS_PLOT_SEC \
  -e HPCPERFSTATS_STRESS_SAMPLE_PATH \
  -e HPCPERFSTATS_LARGE_JOB_HOST_DATA_ROWS \
  -e HPCPERFSTATS_LARGE_JOB_TIME_BUCKETS \
  -v "$ARGS_FILE:/tmp/hpcperfstats_pytest_extra_args:ro"

echo "Stress host_data workflow completed."
