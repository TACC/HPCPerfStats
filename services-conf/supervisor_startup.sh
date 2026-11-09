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

# host may be a Unix socket dir (/run/postgresql, shared tmpfs) or a TCP host.
# TCP: wait for Compose DNS (it lags on start), then connect. Socket: connect.
/usr/local/bin/python3 -c '
import sys
from hpcperfstats.dbload.lib.dbwait import wait_for_postgres_available

wait_for_postgres_available(
  sys.argv[1],
  sys.argv[2],
  dns_timeout_seconds=int(sys.argv[3]),
  connect_timeout_seconds=float(sys.argv[4]),
)
' "${DB_WAIT_HOST}" "${DB_WAIT_PORT}" "${DNS_TIMEOUT_SECONDS}" \
  "${POSTGRES_CONNECT_TIMEOUT_SECONDS}"

echo "PostgreSQL started"

echo "Waiting for Valkey..."

VALKEY_WAIT_URL=$(
  /usr/local/bin/python3 -c "from hpcperfstats.dbload.lib.valkeywait import valkey_wait_url; print(valkey_wait_url())"
)

VALKEY_WAIT_TIMEOUT_SECONDS="${VALKEY_WAIT_TIMEOUT_SECONDS:-60}"
VALKEY_PING_TIMEOUT_SECONDS="${VALKEY_PING_TIMEOUT_SECONDS:-2}"

/usr/local/bin/python3 -c '
import sys
from hpcperfstats.dbload.lib.valkeywait import wait_for_valkey_available

wait_for_valkey_available(
  sys.argv[1],
  timeout_seconds=int(sys.argv[2]),
  interval_seconds=0.25,
  ping_timeout_seconds=float(sys.argv[3]),
)
' "${VALKEY_WAIT_URL}" "${VALKEY_WAIT_TIMEOUT_SECONDS}" "${VALKEY_PING_TIMEOUT_SECONDS}"

echo "Valkey started"

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

# Make the pipeline the host OOM killer's first choice so a memory spike kills
# an ingest daemon (supervisord restarts it) instead of a Postgres backend
# (hpcperfstats03 2026-11-08: three db crash-recoveries while sync_timedb held
# 54-119 GB). Raising oom_score_adj needs no privilege; supervisord and every
# program it starts inherit it. podman-compose ignores compose oom_score_adj.
echo 500 > /proc/self/oom_score_adj || echo "warning: could not set pipeline oom_score_adj" >&2

supervisord_pid=
trap 'kill -TERM "${supervisord_pid}" 2>/dev/null || true' TERM INT
/usr/bin/supervisord -c /home/hpcperfstats/services-conf/supervisord.conf &
supervisord_pid=$!
status=0
while kill -0 "${supervisord_pid}" 2>/dev/null; do
  wait "${supervisord_pid}"
  status=$?
done
exit "${status}"



