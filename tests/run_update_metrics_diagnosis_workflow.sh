#!/usr/bin/env bash
# Opt-in Compose workflow: PostgreSQL + Redis; seeds mixed-scale jobs and runs update_metrics diagnosis pytest.
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
Run update_metrics diagnosis integration test in Docker (db + redis hostnames resolve).

Usage:
  tests/run_update_metrics_diagnosis_workflow.sh [options] [-- pytest_extra_args...]

Options:
  --keep-env      Keep compose services/volumes after run
  --skip-build    Skip docker-compose build web
  -h, --help      Show this help

Environment (optional; forwarded into the web container):
  HPCPERFSTATS_UM_DIAG_SMALL_HOSTS / HPCPERFSTATS_UM_DIAG_SMALL_STEPS   (default 10 x 15 = 150 rows)
  HPCPERFSTATS_UM_DIAG_LARGE_HOSTS / HPCPERFSTATS_UM_DIAG_LARGE_STEPS   (default 25 x 32 = 800 rows)
  METRICS_POOL_PROCESS_CAP   (override metrics pool cap for diagnosis sweeps)
  HPCPERFSTATS_METRICS_SCHEDULER_SKIP_PREWARM   (default unset in workflow; test sets 1 for speed)
  HPCPERFSTATS_UPDATE_METRICS_RETURN_DIAGNOSTICS (test sets 1)

See docs/TESTING.md (update_metrics diagnosis section).
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

ARGS_FILE="$(mktemp)"
ARGS_FILE_OWNED=1
if [[ ${#PYTEST_EXTRA[@]} -gt 0 ]]; then
  printf '%s\n' "${PYTEST_EXTRA[@]}" > "$ARGS_FILE"
else
  : > "$ARGS_FILE"
fi

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

echo "Running update_metrics diagnosis pytest in web container..."
compose_test run --rm \
  -e HPCPERFSTATS_UM_DIAG_SMALL_HOSTS \
  -e HPCPERFSTATS_UM_DIAG_SMALL_STEPS \
  -e HPCPERFSTATS_UM_DIAG_LARGE_HOSTS \
  -e HPCPERFSTATS_UM_DIAG_LARGE_STEPS \
  -e METRICS_POOL_PROCESS_CAP \
  -e HPCPERFSTATS_METRICS_SCHEDULER_SKIP_PREWARM \
  -e HPCPERFSTATS_UPDATE_METRICS_RETURN_DIAGNOSTICS \
  -e HPCPERFSTATS_UM_DIAG_JSON_OUT \
  -v "$ROOT_DIR:/home/hpcperfstats:rw" \
  -v "$ARGS_FILE:/tmp/hpcperfstats_pytest_extra_args:ro" \
  --entrypoint bash \
  web /home/hpcperfstats/tests/run_update_metrics_diagnosis_inner.sh

echo "update_metrics diagnosis workflow completed."
