#!/bin/sh
set -e
export STATIC_ROOT="${STATIC_ROOT:-/home/hpcperfstats/staticfiles}"

echo "Waiting for Redis..."

REDIS_WAIT_URL=$(
  /usr/local/bin/python3 -c "from hpcperfstats.dbload.lib import conf_parser as cfg; print(cfg.get_redis_location())" 2>/dev/null \
    || echo "redis://redis:6379/1"
)

REDIS_WAIT_TIMEOUT_SECONDS="${REDIS_WAIT_TIMEOUT_SECONDS:-60}"
REDIS_PING_TIMEOUT_SECONDS="${REDIS_PING_TIMEOUT_SECONDS:-2}"

/usr/local/bin/python3 -c '
import sys
from hpcperfstats.dbload.lib.rediswait import wait_for_redis_available

wait_for_redis_available(
  sys.argv[1],
  timeout_seconds=int(sys.argv[2]),
  interval_seconds=0.25,
  ping_timeout_seconds=float(sys.argv[3]),
)
' "${REDIS_WAIT_URL}" "${REDIS_WAIT_TIMEOUT_SECONDS}" "${REDIS_PING_TIMEOUT_SECONDS}"

echo "Redis started"

echo "Waiting for postgres..."

DB_WAIT_HOST=$(
  /usr/local/bin/python3 -c "from hpcperfstats.dbload.lib.dbwait import resolve_postgres_wait_target as f; h,p=f(); print(h)" 2>/dev/null \
  || echo "db"
)
DB_WAIT_PORT=$(
  /usr/local/bin/python3 -c "from hpcperfstats.dbload.lib.dbwait import resolve_postgres_wait_target as f; h,p=f(); print(p)" 2>/dev/null \
  || echo "5432"
)

echo "Waiting for PostgreSQL at ${DB_WAIT_HOST}:${DB_WAIT_PORT}..."

DNS_TIMEOUT_SECONDS="${POSTGRES_DNS_WAIT_TIMEOUT_SECONDS:-60}"
POSTGRES_CONNECT_TIMEOUT_SECONDS="${POSTGRES_CONNECT_TIMEOUT_SECONDS:-2}"

# Docker's internal DNS can lag briefly on container startup; wait for name
# resolution before attempting TCP connect.
/usr/local/bin/python3 -c '
import sys
from hpcperfstats.dbload.lib.dbwait import wait_for_host_port_resolution

host = sys.argv[1]
port = sys.argv[2]
timeout_seconds = int(sys.argv[3])
wait_for_host_port_resolution(host, port, timeout_seconds=timeout_seconds)
' "${DB_WAIT_HOST}" "${DB_WAIT_PORT}" "${DNS_TIMEOUT_SECONDS}"

while ! nc -z -w "${POSTGRES_CONNECT_TIMEOUT_SECONDS}" \
  "${DB_WAIT_HOST}" "${DB_WAIT_PORT}" 2>/dev/null; do
  sleep 0.25
done

echo "PostgreSQL started"


chown -R hpcperfstats:hpcperfstats /hpcperfstats/

# Apply reviewed, committed migrations only — never auto-generate in production
# (makemigrations would write ephemeral DDL into site-packages and race migrate).
/usr/local/bin/python3 hpcperfstats/site/manage.py migrate
/usr/local/bin/python3 hpcperfstats/site/manage.py collectstatic --noinput
# Fail-closed SPA shells; auto-heal Vite-era STATIC_ROOT/frontend from package Next export.
/usr/local/bin/python3 - <<'PY'
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hpcperfstats.site.hpcperfstats_site.settings")
import django
django.setup()
from hpcperfstats.site.lib.spa_static_root_heal import ensure_spa_shells_from_django_settings

ensure_spa_shells_from_django_settings()
PY

# Gunicorn workers: WEB_CONCURRENCY overrides; else absolute [PORTAL] gunicorn_workers (default 32).
WORKERS=$(/usr/local/bin/python3 -c "
import os
from hpcperfstats.dbload.lib import conf_parser as cfg
override = os.environ.get('WEB_CONCURRENCY', '').strip()
if override:
    print(max(1, int(override)))
else:
    print(max(1, int(cfg.get_gunicorn_workers())))
")

# Browser CORS: export origins from [DEFAULT] server when unset (production).
if [ -z "${CORS_ALLOWED_ORIGINS:-}" ]; then
  _cors_csv="$(
    /usr/local/bin/python3 -c \
      "from hpcperfstats.dbload.lib import conf_parser as cfg; print(cfg.format_cors_allowed_origins_csv_from_ini())" \
      2>/dev/null || true
  )"
  if [ -n "${_cors_csv}" ]; then
    export CORS_ALLOWED_ORIGINS="${_cors_csv}"
    echo "Derived CORS_ALLOWED_ORIGINS from hpcperfstats.ini [DEFAULT] server (${_cors_csv})."
  fi
fi

# gunicorn is the django web server
/usr/local/bin/gunicorn hpcperfstats.site.hpcperfstats_site.wsgi --bind 0.0.0.0:8000  \
  --env DJANGO_SETTINGS_MODULE=hpcperfstats.site.hpcperfstats_site.settings -u hpcperfstats \
  --workers=${WORKERS} --timeout 600 --preload --max-requests 100 --access-logfile - --error-logfile - 

