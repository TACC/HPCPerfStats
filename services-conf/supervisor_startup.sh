#!/bin/sh

URL="${1:-http://web:8000}"   # use first arg or default URL
SLEEP_SECONDS=5                   # delay between checks
echo "Waiting for $URL to become available..."
while true; do
  # -s: silent, -o /dev/null: discard body
  # -w "%{http_code}": only print status code
  STATUS_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$URL" || echo "000")
  if [[ "$STATUS_CODE" =~ ^2|3 ]]; then
    echo "Connected to $URL (HTTP $STATUS_CODE). Continuing..."
    break
  else
    echo "Still waiting for $URL (status $STATUS_CODE). Retrying in $SLEEP_SECONDS seconds..."
    sleep "$SLEEP_SECONDS"
  fi
done

chmod -c 755 /hpcperfstats/
# make directories if they are not there
mkdir -pv /hpcperfstats/accounting
mkdir -pv /hpcperfstats/archive
mkdir -pv /hpcperfstats/daily_archive
chown -R hpcperfstats:hpcperfstats /hpcperfstats/* 
cp /hpcperfstats/.ssh/id* /home/hpcperfstats/.ssh/
chown -R hpcperfstats:hpcperfstats /home/hpcperfstats/.ssh
chmod -R 0600  /home/hpcperfstats/.ssh/*

/usr/bin/supervisord -c /home/hpcperfstats/services-conf/supervisord.conf



