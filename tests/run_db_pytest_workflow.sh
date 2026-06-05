#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

SEED_CMD="${DB_TEST_SEED_CMD:-}"
KEEP_ENV=0
SKIP_BUILD=0
SKIP_BROWSER_E2E=0
SKIP_MIGRATE=0
PYTEST_EXTRA=()
ARGS_FILE=""
ARGS_FILE_OWNED=0

usage() {
  cat <<'EOF'
Run full hpcperfstats pytest suite in Docker (Postgres host "db" resolves on compose network).

Usage:
  tests/run_db_pytest_workflow.sh [options] [-- pytest_extra_args...]

Options:
  --seed-cmd "<command>"    Run inside web container after migrate (see DB_TEST_SEED_CMD)
  --keep-env                Keep compose services/volumes after run
  --skip-build              Skip docker-compose build web
  --skip-browser-e2e        Skip Playwright install and test_web_pages_browser_e2e.py
  --skip-migrate            Skip manage.py migrate on the dev database
  -h, --help                Show this help

Environment:
  DB_TEST_SEED_CMD          Seed command (same as --seed-cmd)

Arguments after a lone "--" are forwarded to pytest (one argument per line internally).
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
    --seed-cmd)
      shift
      SEED_CMD="${1:-}"
      shift
      ;;
    --keep-env)
      KEEP_ENV=1
      shift
      ;;
    --skip-build)
      SKIP_BUILD=1
      shift
      ;;
    --skip-browser-e2e)
      SKIP_BROWSER_E2E=1
      shift
      ;;
    --skip-migrate)
      SKIP_MIGRATE=1
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
  echo "Tearing down compose services and volumes..."
  docker-compose down -v
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
docker-compose down -v --remove-orphans
docker volume prune -f

if [[ "$SKIP_BUILD" -eq 0 ]]; then
  echo "Rebuilding web image..."
  docker-compose build web
fi

echo "Starting db/redis..."
docker-compose up -d db redis

echo "Waiting for healthy db/redis..."
db_health=""
redis_health=""
for _ in $(seq 1 60); do
  db_id="$(docker-compose ps -q db)"
  redis_id="$(docker-compose ps -q redis)"

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
  docker-compose ps
  exit 1
fi

export DOCKER_PYTEST_SEED_CMD="$SEED_CMD"
export DOCKER_PYTEST_SKIP_BROWSER="$SKIP_BROWSER_E2E"
export DOCKER_PYTEST_SKIP_MIGRATE="$SKIP_MIGRATE"

echo "Running pytest in web container..."
# Mount repo so tests use current source (migrations, conftest) without rebuilding the image.
docker-compose run --rm \
  -e DOCKER_PYTEST_SEED_CMD \
  -e DOCKER_PYTEST_SKIP_BROWSER \
  -e DOCKER_PYTEST_SKIP_MIGRATE \
  -v "$ROOT_DIR:/home/hpcperfstats:rw" \
  -v "$ARGS_FILE:/tmp/hpcperfstats_pytest_extra_args:ro" \
  --entrypoint bash \
  web /home/hpcperfstats/tests/run_db_pytest_inner.sh

echo "DB pytest workflow completed."
