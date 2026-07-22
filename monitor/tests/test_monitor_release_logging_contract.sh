#!/bin/sh
# Regression: release logging gates (startup summary, hourly timer, first-fail RMQ).
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

grep -q 'monitor_log_release_startup_summary' src/monitor.c \
  || { echo "monitor.c must emit release startup summary" >&2; exit 1; }
grep -q 'MONITOR_DAEMON_HOURLY_STATUS_SEC' src/monitor_daemon.h \
  || { echo "monitor_daemon.h must define hourly status interval" >&2; exit 1; }
grep -q 'monitor_daemon_hourly_status_cb' src/monitor_daemon.c \
  || { echo "monitor_daemon.c must implement hourly status callback" >&2; exit 1; }
grep -q 'hourly_status_timer' src/monitor.c \
  || { echo "monitor.c must start hourly_status_timer in release" >&2; exit 1; }
grep -q 'rmq_note_send_failure' src/stats_buffer_rmq.c \
  || { echo "stats_buffer_rmq.c must gate RMQ failure ERROR via first-fail helper" >&2; exit 1; }
grep -q 'stats_buffer_rmq_get_failure_counts' src/stats_buffer.h \
  || { echo "stats_buffer.h must export RMQ failure counters" >&2; exit 1; }
grep -q 'monitor_log_debug' src/monitor_options.c \
  || { echo "monitor_options.c must demote conf echoes to monitor_log_debug" >&2; exit 1; }
# Conf apply path should not use monitor_log_info for routine key echoes.
if grep -n 'monitor_log_info' src/monitor_options.c >/dev/null 2>&1; then
  echo "monitor_options.c must not use monitor_log_info for conf echoes" >&2
  exit 1
fi

echo "test_monitor_release_logging_contract passed"
