#!/bin/sh
# Regression: release logging gates (startup summary, hourly timer, first-fail classes).
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

grep -q 'MONITOR_REL_FAIL_RING_RESEND' src/monitor_daemon.c \
  || { echo "monitor_daemon.c must latch ring resend failures" >&2; exit 1; }
grep -q 'ring_resend_fail_delta' src/monitor_daemon.c \
  || { echo "hourly status must include ring_resend_fail_delta" >&2; exit 1; }
grep -q 'ib_mad_fail_delta' src/monitor_daemon.c \
  || { echo "hourly status must include ib_mad_fail_delta" >&2; exit 1; }
grep -q 'opa_mad_fail_delta' src/monitor_daemon.c \
  || { echo "hourly status must include opa_mad_fail_delta" >&2; exit 1; }
grep -q 'MONITOR_REL_FAIL_OPA_MAD' src/opa.c \
  || { echo "opa.c must use OPA MAD first-fail counter" >&2; exit 1; }
grep -q 'opa_mad_dyn_available' src/opa.c \
  || { echo "opa.c must gate MAD on opa_mad_dyn_available when dlopen" >&2; exit 1; }
grep -q 'monitor_stderr_quiet_begin' src/ib_mad.c \
  || { echo "ib_mad.c must quiet stderr around MAD RPC" >&2; exit 1; }
grep -q 'MONITOR_REL_FAIL_IB_MAD' src/ib_mad.c \
  || { echo "ib_mad.c must use IB MAD first-fail counter" >&2; exit 1; }
grep -q 'MONITOR_REL_FAIL_NVIDIA_ZERO' src/nvidia_gpu.c \
  || { echo "nvidia_gpu.c must first-fail zero-row warnings" >&2; exit 1; }
# Release must not emit ungated per-attempt socket/login detail ERROR strings.
if grep -n 'ERROR("socket failed to open")' src/stats_buffer_rmq.c >/dev/null 2>&1; then
  echo "stats_buffer_rmq.c must not emit ungated release socket ERROR" >&2
  exit 1
fi
if grep -n 'ERROR("amqp login failed")' src/stats_buffer_rmq.c >/dev/null 2>&1; then
  echo "stats_buffer_rmq.c must not emit ungated release login ERROR" >&2
  exit 1
fi

echo "test_monitor_release_logging_contract passed"
