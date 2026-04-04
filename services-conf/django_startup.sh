#!/bin/sh

echo "Waiting for Redis..."

REDIS_WAIT_URL=$(
  /usr/local/bin/python3 -c "from hpcperfstats import conf_parser as cfg; print(cfg.get_redis_location())" 2>/dev/null \
    || echo "redis://redis:6379/1"
)

REDIS_WAIT_TIMEOUT_SECONDS="${REDIS_WAIT_TIMEOUT_SECONDS:-60}"
REDIS_PING_TIMEOUT_SECONDS="${REDIS_PING_TIMEOUT_SECONDS:-2}"

/usr/local/bin/python3 -c '
import sys
from hpcperfstats.rediswait import wait_for_redis_available

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
  /usr/local/bin/python3 -c "from hpcperfstats.dbwait import resolve_postgres_wait_target as f; h,p=f(); print(h)" 2>/dev/null \
  || echo "db"
)
DB_WAIT_PORT=$(
  /usr/local/bin/python3 -c "from hpcperfstats.dbwait import resolve_postgres_wait_target as f; h,p=f(); print(p)" 2>/dev/null \
  || echo "5432"
)

echo "Waiting for PostgreSQL at ${DB_WAIT_HOST}:${DB_WAIT_PORT}..."

DNS_TIMEOUT_SECONDS="${POSTGRES_DNS_WAIT_TIMEOUT_SECONDS:-60}"
POSTGRES_CONNECT_TIMEOUT_SECONDS="${POSTGRES_CONNECT_TIMEOUT_SECONDS:-2}"

# Docker's internal DNS can lag briefly on container startup; wait for name
# resolution before attempting TCP connect.
/usr/local/bin/python3 -c '
import sys
from hpcperfstats.dbwait import wait_for_host_port_resolution

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

# detect if the tables are existing and create if not
/usr/local/bin/python3 hpcperfstats/site/manage.py makemigrations
/usr/local/bin/python3 hpcperfstats/site/manage.py migrate

# Gunicorn workers: WEB_CONCURRENCY overrides; else min(2*base+1, max_gunicorn_workers)
# where base = min(visible_cpus, effective_cores) so ini total_cores caps workers even
# when the container sees more CPUs. Default max_gunicorn_workers in ini is 32.
WORKERS=$(/usr/local/bin/python3 -c "
import os
from hpcperfstats import conf_parser as cfg
override = os.environ.get('WEB_CONCURRENCY', '').strip()
if override:
    print(max(1, int(override)))
else:
    visible = os.cpu_count() or 1
    eff = cfg.get_effective_cores()
    base = min(visible, eff)
    cap = cfg.get_max_gunicorn_workers_cap()
    print(min(2 * base + 1, cap))
")

# gunicorn is the django web server
/usr/local/bin/gunicorn hpcperfstats.site.hpcperfstats_site.wsgi --bind 0.0.0.0:8000  \
  --env DJANGO_SETTINGS_MODULE=hpcperfstats.site.hpcperfstats_site.settings -u hpcperfstats \
  --workers=${WORKERS} --timeout 600 --preload --max-requests 100 --access-logfile - --error-logfile - 

