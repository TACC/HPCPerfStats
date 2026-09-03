#!/bin/sh

URL="${1:-http://web:8000}"   # use first arg or default URL
SLEEP_SECONDS=5                   # delay between checks

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

echo "Waiting for $URL to become available..."
while true; do
  # -s: silent, -o /dev/null: discard body
  # -w "%{http_code}": only print status code
  STATUS_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$URL" || echo "000")
  case "$STATUS_CODE" in
    2*|3*) echo "Connected to $URL (HTTP $STATUS_CODE). Continuing..."; break ;;
    *) echo "Still waiting for $URL (status $STATUS_CODE). Retrying in $SLEEP_SECONDS seconds..."; sleep "$SLEEP_SECONDS" ;;
  esac
done

chmod -c 755 /hpcperfstats/
# make directories if they are not there
mkdir -pv /hpcperfstats/accounting
mkdir -pv /hpcperfstats/archive
mkdir -pv /hpcperfstats/daily_archive
mkdir -pv /hpcperfstats/logs/current
mkdir -pv /hpcperfstats/logs/log_archive
chown -R hpcperfstats:hpcperfstats /hpcperfstats/* 
cp /hpcperfstats/.ssh/id* /home/hpcperfstats/.ssh/
chown -R hpcperfstats:hpcperfstats /home/hpcperfstats/.ssh
chmod -R 0600  /home/hpcperfstats/.ssh/*

# Cluster syslog is not supervised (see README "Cluster syslog"). Uncomment
# these two lines, then start syslog-ng manually as root, to re-enable it.
#mkdir -p /var/lib/hpcperfstats-syslog
#/usr/local/bin/python3 -m hpcperfstats.render_syslog_ng_generated || exit 1

/usr/bin/supervisord -c /home/hpcperfstats/services-conf/supervisord.conf



