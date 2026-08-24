/*! \file stats_buffer_rmq_policy.c
 *  Pure RMQ reconnect / resend policy helpers (no AMQP linkage).
 */
#include "stats_buffer_rmq_policy.h"

double stats_buffer_rmq_backoff_cap_sec(double send_freq)
{
  double cap = send_freq;

  if (cap <= 0.0)
    cap = (double)STATS_BUFFER_RMQ_BACKOFF_MIN_SEC;
  if (cap > (double)STATS_BUFFER_RMQ_BACKOFF_CAP_ABS_SEC)
    cap = (double)STATS_BUFFER_RMQ_BACKOFF_CAP_ABS_SEC;
  if (cap < (double)STATS_BUFFER_RMQ_BACKOFF_MIN_SEC)
    cap = (double)STATS_BUFFER_RMQ_BACKOFF_MIN_SEC;
  return cap;
}

double stats_buffer_rmq_backoff_base_delay_sec(unsigned consecutive_failures, double send_freq)
{
  unsigned n = consecutive_failures;
  double delay = (double)STATS_BUFFER_RMQ_BACKOFF_MIN_SEC;
  double cap;
  unsigned i;

  if (n < 1u)
    n = 1u;
  for (i = 1u; i < n; i++) {
    if (delay >= 1e12)
      break;
    delay *= 2.0;
  }
  cap = stats_buffer_rmq_backoff_cap_sec(send_freq);
  if (delay > cap)
    delay = cap;
  if (delay < (double)STATS_BUFFER_RMQ_BACKOFF_MIN_SEC)
    delay = (double)STATS_BUFFER_RMQ_BACKOFF_MIN_SEC;
  return delay;
}

double stats_buffer_rmq_apply_jitter(double delay_sec, unsigned jitter_seed)
{
  unsigned pct;

  if (delay_sec < 0.0)
    delay_sec = 0.0;
  pct = jitter_seed % ((unsigned)STATS_BUFFER_RMQ_JITTER_PCT + 1u);
  return delay_sec * (1.0 + (double)pct / 100.0);
}

double stats_buffer_rmq_compute_backoff_delay_sec(unsigned consecutive_failures, double send_freq,
                                                  unsigned jitter_seed)
{
  return stats_buffer_rmq_apply_jitter(
      stats_buffer_rmq_backoff_base_delay_sec(consecutive_failures, send_freq), jitter_seed);
}

void stats_buffer_rmq_choose_resend_limits(int recovery_mode, int q_count, int *max_batches,
                                           long *runtime_us)
{
  if (max_batches == NULL || runtime_us == NULL)
    return;
  /* Recovery or deep backlog: throttle per-tick drain so quorum is not slammed. */
  if (recovery_mode || q_count > STATS_BUFFER_RMQ_RESEND_MAX_BATCHES) {
    *max_batches = STATS_BUFFER_RMQ_RESEND_RECOVERY_MAX_BATCHES;
    *runtime_us = STATS_BUFFER_RMQ_RESEND_RECOVERY_RUNTIME_US;
    return;
  }
  *max_batches = STATS_BUFFER_RMQ_RESEND_MAX_BATCHES;
  *runtime_us = STATS_BUFFER_RMQ_RESEND_RUNTIME_US;
}
