#!/usr/bin/env bash
# Pipeline ingest E2E (RabbitMQ → archive → sync_timedb → update_metrics).
# Optional browser phase (live web + Playwright endpoint matrix) can be enabled.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
# shellcheck source=colima_compose_teardown.sh
. "$(dirname "${BASH_SOURCE[0]}")/colima_compose_teardown.sh"
colima_export_docker_env

# shellcheck source=compose_test_cmd.sh
. "$(dirname "${BASH_SOURCE[0]}")/compose_test_cmd.sh"

KEEP_ENV=0
SKIP_BUILD=0
SKIP_PLAYWRIGHT_INSTALL=0
WITH_BROWSER=0

usage() {
  cat <<'EOF'
Run pipeline E2E workflow in Docker (db, redis, rabbitmq, web image, /hpcperfstats volume).

Usage:
  tests/run_pipeline_e2e_workflow.sh [options]

Options:
  --keep-env                     Keep compose services/volumes after run
  --skip-build                   Skip docker compose build web
  --skip-playwright-install      Skip Playwright browser install in phase 2 container
  --with-browser                 Also run phase 2 browser/API matrix tests
  -h, --help                     Show this help

Environment:
  HPCPERFSTATS_PIPELINE_E2E      Set to 1 inside container by this script (gate for pytest).

Prerequisites:
  docker-compose.yaml plus tests/docker-compose.test-overlay.yaml (named /hpcperfstats volume).
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --keep-env)
      KEEP_ENV=1
      ;;
    --skip-build)
      SKIP_BUILD=1
      ;;
    --skip-playwright-install)
      SKIP_PLAYWRIGHT_INSTALL=1
      ;;
    --with-browser)
      WITH_BROWSER=1
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
  if [[ "$KEEP_ENV" -eq 1 ]]; then
    echo "Keeping compose environment (--keep-env)."
    return
  fi
  colima_compose_teardown "${COMPOSE_TEST[@]}"
}
trap cleanup EXIT

echo "Resetting Docker compose state and volumes..."
compose_test down -v --remove-orphans

if [[ "$SKIP_BUILD" -eq 0 ]]; then
  echo "Rebuilding web image..."
  compose_test build web
fi

echo "Starting db, redis, rabbitmq..."
compose_test up -d db redis rabbitmq

echo "Waiting for healthy db, redis, rabbitmq..."
db_health=""
redis_health=""
rmq_health=""
for _ in $(seq 1 60); do
  db_id="$(compose_test ps -q db 2>/dev/null || true)"
  redis_id="$(compose_test ps -q redis 2>/dev/null || true)"
  rmq_id="$(compose_test ps -q rabbitmq 2>/dev/null || true)"

  if [[ -n "$db_id" && -n "$redis_id" && -n "$rmq_id" ]]; then
    db_health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}starting{{end}}' "$db_id" 2>/dev/null || true)"
    redis_health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}starting{{end}}' "$redis_id" 2>/dev/null || true)"
    rmq_health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}starting{{end}}' "$rmq_id" 2>/dev/null || true)"
    if [[ "$db_health" == "healthy" && "$redis_health" == "healthy" && "$rmq_health" == "healthy" ]]; then
      echo "db, redis, and rabbitmq are healthy."
      break
    fi
  fi
  sleep 2
done

if [[ "$db_health" != "healthy" || "$redis_health" != "healthy" || "$rmq_health" != "healthy" ]]; then
  echo "Timed out waiting for service health." >&2
  compose_test ps
  exit 1
fi

echo "Running migrations..."
compose_test run --rm web python hpcperfstats/site/manage.py migrate --noinput

echo "Running pipeline ingest pytest (phase 1)..."
compose_test run --rm \
  -e HPCPERFSTATS_PIPELINE_E2E=1 \
  -e HPCPERFSTATS_COMPOSE_NETWORK=1 \
  -v "$ROOT_DIR:/home/hpcperfstats:rw" \
  --entrypoint bash \
  web -lc 'cd /home/hpcperfstats && if ! pip install -q -e ".[test]"; then echo "pip install -e failed; using PYTHONPATH fallback for pipeline e2e."; export PYTHONPATH=/home/hpcperfstats; source tests/pip_compose_test_extras_fallback.sh; pip_compose_test_extras_fallback; pip install -q pika; fi; python -m pytest -q tests/pipeline_e2e/test_full_ingest_pipeline.py'

if [[ "$WITH_BROWSER" -eq 1 ]]; then
  echo "Seeding live DB records for browser/API phase..."
  compose_test run --rm \
    --entrypoint bash \
    web -lc "python hpcperfstats/site/manage.py shell -c \"from datetime import timedelta; from django.utils import timezone; from hpcperfstats.site.machine.models import ApiKey, job_data; pipeline_key = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'; pipeline_jid = 'pipeline_e2e_j01'; pipeline_user = 'pipeline_e2e_user'; pipeline_host = 'node001'; key_hash = ApiKey.hash_raw_key(pipeline_key); ApiKey.objects.update_or_create(key=key_hash, defaults={'key_prefix': pipeline_key[:12], 'username': pipeline_user, 'is_staff': True, 'is_active': True}); now = timezone.now(); start = now - timedelta(hours=4); end = now - timedelta(hours=2); job_data.objects.update_or_create(jid=pipeline_jid, defaults={'submit_time': start, 'start_time': start, 'end_time': end, 'runtime': 7200.0, 'node_hrs': 16.0, 'nhosts': 2, 'ncores': 4, 'username': pipeline_user, 'account': 'pipeline', 'queue': 'debug', 'state': 'COMPLETED', 'host_list': [pipeline_host], 'metrics_distinct_time_count': 1}); print('Seeded API key and job_data for browser phase.')\""

  echo "Starting web (gunicorn) for browser phase..."
  compose_test up -d web

  echo "Waiting for web to accept connections..."
  web_up=0
  for _ in $(seq 1 60); do
    if compose_test exec -T web python -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/machine/", timeout=3).read(8)' >/dev/null 2>&1; then
      web_up=1
      echo "web is up."
      break
    fi
    sleep 2
  done
  if [[ "$web_up" -ne 1 ]]; then
    echo "Timed out waiting for web inside container." >&2
    compose_test logs --tail=80 web >&2 || true
    exit 1
  fi

  PLAYWRIGHT_SETUP=""
  if [[ "$SKIP_PLAYWRIGHT_INSTALL" -eq 0 ]]; then
    PLAYWRIGHT_SETUP="python -m playwright install --with-deps chromium && "
  fi

  echo "Running browser + endpoint matrix pytest (phase 2)..."
  compose_test run --rm \
    -e HPCPERFSTATS_PIPELINE_E2E=1 \
    -e HPCPERFSTATS_COMPOSE_NETWORK=1 \
    -e HPCPERFSTATS_PIPELINE_E2E_BASE_URL=http://web:8000 \
    -v "$ROOT_DIR:/home/hpcperfstats:rw" \
    --entrypoint bash \
    web -lc "cd /home/hpcperfstats && if ! pip install -q -e '.[test]'; then echo 'pip install -e failed; using PYTHONPATH fallback for pipeline browser phase.'; export PYTHONPATH=/home/hpcperfstats; source tests/pip_compose_test_extras_fallback.sh; pip_compose_test_extras_fallback; pip install -q 'playwright>=1.60.0'; fi; ${PLAYWRIGHT_SETUP}python -m pytest -q tests/pipeline_e2e/test_job_detail_browser.py tests/pipeline_e2e/test_all_endpoints_browser.py tests/pipeline_e2e/test_a11y_axe_browser.py"
fi

if [[ "$WITH_BROWSER" -eq 1 ]]; then
  echo "Pipeline E2E workflow completed (phase 1 + phase 2)."
else
  echo "Pipeline E2E workflow completed (phase 1 only)."
fi
