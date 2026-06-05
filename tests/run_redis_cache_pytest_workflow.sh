#!/usr/bin/env bash
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
Run live Redis cache integration tests in Docker (sets HPCPERFSTATS_PYTEST_LIVE_REDIS=1).

Usage:
  tests/run_redis_cache_pytest_workflow.sh [options] [-- pytest_extra_args...]

Options:
  --keep-env      Keep compose services/volumes after run
  --skip-build    Skip docker-compose build web
  -h, --help      Show this help

Arguments after a lone "--" are forwarded to pytest.
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

echo "Running live Redis cache tests in web container..."
compose_test run --rm \
  -v "$ROOT_DIR:/home/hpcperfstats:rw" \
  -v "$ARGS_FILE:/tmp/hpcperfstats_pytest_extra_args:ro" \
  --entrypoint bash \
  web /home/hpcperfstats/tests/run_redis_cache_pytest_inner.sh

echo "Redis cache pytest workflow completed."
