/*! \file stats_buffer_rmq_policy.h
 *  Pure RMQ reconnect / resend policy (no AMQP). Shared by daemon and unit tests.
 */
#ifndef STATS_BUFFER_RMQ_POLICY_H_
#define STATS_BUFFER_RMQ_POLICY_H_

#include <stddef.h>

/* Floor reconnect delay; exponential starts here (2 → 4 → 8 …). */
#define STATS_BUFFER_RMQ_BACKOFF_MIN_SEC 2
/* Absolute cap for min(send_freq, cap); was hardcoded 30 before hardening. */
#define STATS_BUFFER_RMQ_BACKOFF_CAP_ABS_SEC 60
/* Require this many seconds connected + ≥1 publish before clearing fail streak. */
#define STATS_BUFFER_RMQ_STABLE_WINDOW_SEC 30
/* Jitter adds 0..this percent of the base delay (hostname hash). */
#define STATS_BUFFER_RMQ_JITTER_PCT 25

/* Normal drain budgets (match historical monitor_daemon.c). */
#define STATS_BUFFER_RMQ_RESEND_MAX_BATCHES 64
#define STATS_BUFFER_RMQ_RESEND_RUNTIME_US 12000L
#define STATS_BUFFER_RMQ_RESEND_RUNTIME_US_BACKLOG 25000L
/* Recovery / post-outage: fewer batches per tick. */
#define STATS_BUFFER_RMQ_RESEND_RECOVERY_MAX_BATCHES 16
#define STATS_BUFFER_RMQ_RESEND_RECOVERY_RUNTIME_US 8000L

/*! Cap = clamp(send_freq, MIN..CAP_ABS). send_freq≤0 treated as MIN. */
double stats_buffer_rmq_backoff_cap_sec(double send_freq);

/*! Exponential base delay from consecutive_failures (1 → 2s, 2 → 4s, …) capped. */
double stats_buffer_rmq_backoff_base_delay_sec(unsigned consecutive_failures, double send_freq);

/*! delay * (1 + (jitter_seed % (JITTER_PCT+1)) / 100). */
double stats_buffer_rmq_apply_jitter(double delay_sec, unsigned jitter_seed);

/*! Full delay used when arming reconnect backoff. */
double stats_buffer_rmq_compute_backoff_delay_sec(unsigned consecutive_failures, double send_freq,
                                                  unsigned jitter_seed);

/*! Choose max_batches and runtime_us for one ring drain call. */
void stats_buffer_rmq_choose_resend_limits(int recovery_mode, int q_count, int *max_batches,
                                           long *runtime_us);

#endif /* STATS_BUFFER_RMQ_POLICY_H_ */
