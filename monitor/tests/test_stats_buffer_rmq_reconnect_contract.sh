#!/bin/sh
# Contract: RMQ reconnect hardening — exp backoff, passive declare, recovery resend.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
rmq="${ROOT}/src/stats_buffer_rmq.c"
pol="${ROOT}/src/stats_buffer_rmq_policy.c"
daemon="${ROOT}/src/monitor_daemon.c"

grep -q 'stats_buffer_rmq_compute_backoff_delay_sec' "${rmq}" \
  || { echo "FAIL: ${rmq} must use exponential backoff helper" >&2; exit 1; }
grep -q 'STATS_BUFFER_RMQ_STABLE_WINDOW_SEC' "${rmq}" \
  || { echo "FAIL: ${rmq} must gate fail-streak reset on stable window" >&2; exit 1; }
grep -q 'passive=1' "${rmq}" || grep -qE 'amqp_queue_declare\([^)]*,[[:space:]]*1,' "${rmq}" \
  || { echo "FAIL: ${rmq} must passive-declare on warm reconnect" >&2; exit 1; }
grep -q 'AMQP_NOT_FOUND' "${rmq}" \
  || { echo "FAIL: ${rmq} must fall back on AMQP_NOT_FOUND" >&2; exit 1; }
grep -q 'STATS_BUFFER_RMQ_BACKOFF_CAP_ABS_SEC' "${pol}" \
  || { echo "FAIL: ${pol} must define abs backoff cap 60" >&2; exit 1; }
grep -q 'stats_buffer_rmq_choose_resend_limits' "${daemon}" \
  || { echo "FAIL: ${daemon} must use recovery resend limits" >&2; exit 1; }
grep -q 'stats_buffer_rmq_in_recovery' "${daemon}" \
  || { echo "FAIL: ${daemon} must consult RMQ recovery mode" >&2; exit 1; }

echo "test_stats_buffer_rmq_reconnect_contract passed"
