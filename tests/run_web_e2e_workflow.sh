#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
# shellcheck source=colima_compose_teardown.sh
. "$(dirname "${BASH_SOURCE[0]}")/colima_compose_teardown.sh"
# shellcheck source=compose_test_cmd.sh
. "$(dirname "${BASH_SOURCE[0]}")/compose_test_cmd.sh"
colima_export_docker_env

if [[ ! -f hpcperfstats.ini ]]; then
  echo "hpcperfstats.ini not found; copying from hpcperfstats.ini.example"
  cp hpcperfstats.ini.example hpcperfstats.ini
fi

SEED_CMD="${E2E_SEED_CMD:-}"
KEEP_ENV=0
SKIP_BUILD=0
SKIP_PLAYWRIGHT_INSTALL=0

usage() {
  cat <<'EOF'
Run full web/browser E2E workflow in Docker (compose Postgres + migrate + web-pages tests + nginx WSGI contract).

Usage:
  tests/run_web_e2e_workflow.sh [options]

Options:
  --seed-cmd "<command>"         Command to seed/recreate required test data
  --keep-env                     Keep compose services/volumes after run
  --skip-build                   Skip docker-compose build web
  --skip-playwright-install      Skip Playwright browser install in container
  -h, --help                     Show this help

Environment:
  E2E_SEED_CMD                   Seed command (same as --seed-cmd)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --seed-cmd)
      shift
      SEED_CMD="${1:-}"
      ;;
    --keep-env)
      KEEP_ENV=1
      ;;
    --skip-build)
      SKIP_BUILD=1
      ;;
    --skip-playwright-install)
      SKIP_PLAYWRIGHT_INSTALL=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 2
      ;;
  esac
  shift
done

cleanup() {
  compose_cleanup_bind_mount
  if [[ "$KEEP_ENV" -eq 1 ]]; then
    echo "Keeping compose environment (--keep-env)."
    return
  fi
  colima_compose_teardown "${COMPOSE_TEST[@]}"
}
trap cleanup EXIT

export COMPOSE_BIND_MOUNT_SKIP_BUILD="$SKIP_BUILD"
compose_prepare_bind_mount

echo "Resetting Docker compose state and volumes..."
compose_test down -v --remove-orphans

if [[ "$SKIP_BUILD" -eq 0 ]]; then
  echo "Rebuilding web image..."
  compose_test build web
fi

echo "Starting db/redis..."
compose_test up -d db redis

echo "Waiting for healthy db/redis..."
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

if [[ "${db_health:-}" != "healthy" || "${redis_health:-}" != "healthy" ]]; then
  echo "Timed out waiting for db/redis health." >&2
  compose_test ps
  exit 1
fi

if [[ -n "$SEED_CMD" ]]; then
  echo "Running seed command..."
  bash -lc "$SEED_CMD"
fi

PLAYWRIGHT_SETUP=""
if [[ "$SKIP_PLAYWRIGHT_INSTALL" -eq 0 ]]; then
  PLAYWRIGHT_SETUP=" && python -m playwright install --with-deps chromium"
fi

echo "Running web E2E, browser E2E, and nginx/WSGI contract tests..."
compose_web_repo_bind_mount_args
compose_test run --rm \
  -e HPCPERFSTATS_COMPOSE_NETWORK=1 \
  "${compose_web_repo_bind_mount_args[@]}" \
  --entrypoint "sh -lc 'pip install -e \".[test]\"${PLAYWRIGHT_SETUP} && python hpcperfstats/site/manage.py migrate --noinput && python -m pytest -q hpcperfstats/site/lib/machine/tests/test_web_pages_e2e.py hpcperfstats/site/lib/machine/tests/test_web_pages_browser_e2e.py hpcperfstats/site/hpcperfstats_site/tests/test_nginx_static_wsgi_contract.py'" \
  web

echo "E2E workflow completed."
