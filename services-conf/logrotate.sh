#!/bin/bash
# Per-host syslog under data_dir/logs/current is sealed daily by seal_syslog_daily
# (supervisord). This script remains as a no-op for sites that still reference it.

while true; do
  sleep 86400
done
