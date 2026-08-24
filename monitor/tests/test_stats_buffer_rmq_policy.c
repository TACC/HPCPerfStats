/*! \file test_stats_buffer_rmq_policy.c
 *  Unit tests for RMQ reconnect backoff / resend recovery policy.
 */
#include <assert.h>
#include <math.h>
#include <stdio.h>

#include "stats_buffer_rmq_policy.h"

static void test_backoff_cap_follows_send_freq(void)
{
  assert(stats_buffer_rmq_backoff_cap_sec(600.0) == (double)STATS_BUFFER_RMQ_BACKOFF_CAP_ABS_SEC);
  assert(stats_buffer_rmq_backoff_cap_sec(45.0) == 45.0);
  assert(stats_buffer_rmq_backoff_cap_sec(1.0) == (double)STATS_BUFFER_RMQ_BACKOFF_MIN_SEC);
  assert(stats_buffer_rmq_backoff_cap_sec(0.0) == (double)STATS_BUFFER_RMQ_BACKOFF_MIN_SEC);
}

static void test_backoff_doubles_then_caps(void)
{
  double d1 = stats_buffer_rmq_backoff_base_delay_sec(1, 600.0);
  double d2 = stats_buffer_rmq_backoff_base_delay_sec(2, 600.0);
  double d3 = stats_buffer_rmq_backoff_base_delay_sec(3, 600.0);
  double d_big = stats_buffer_rmq_backoff_base_delay_sec(20, 600.0);

  assert(d1 == 2.0);
  assert(d2 == 4.0);
  assert(d3 == 8.0);
  assert(d_big == (double)STATS_BUFFER_RMQ_BACKOFF_CAP_ABS_SEC);
}

static void test_jitter_bounded(void)
{
  double base = 10.0;
  double j0 = stats_buffer_rmq_apply_jitter(base, 0);
  double j25 = stats_buffer_rmq_apply_jitter(base, 25);
  double j26 = stats_buffer_rmq_apply_jitter(base, 26); /* 26 % 26 == 0 */

  assert(j0 == 10.0);
  assert(fabs(j25 - 12.5) < 1e-9);
  assert(j26 == 10.0);
}

static void test_compute_includes_jitter(void)
{
  double plain = stats_buffer_rmq_backoff_base_delay_sec(1, 30.0);
  double with = stats_buffer_rmq_compute_backoff_delay_sec(1, 30.0, 10);

  assert(plain == 2.0);
  assert(fabs(with - plain * 1.10) < 1e-9);
}

static void test_resend_limits_recovery(void)
{
  int batches = 0;
  long runtime = 0;

  stats_buffer_rmq_choose_resend_limits(0, 10, &batches, &runtime);
  assert(batches == STATS_BUFFER_RMQ_RESEND_MAX_BATCHES);
  assert(runtime == STATS_BUFFER_RMQ_RESEND_RUNTIME_US);

  stats_buffer_rmq_choose_resend_limits(1, 10, &batches, &runtime);
  assert(batches == STATS_BUFFER_RMQ_RESEND_RECOVERY_MAX_BATCHES);
  assert(runtime == STATS_BUFFER_RMQ_RESEND_RECOVERY_RUNTIME_US);

  /* Deep backlog alone enters recovery throttle. */
  stats_buffer_rmq_choose_resend_limits(0, STATS_BUFFER_RMQ_RESEND_MAX_BATCHES + 1, &batches,
                                        &runtime);
  assert(batches == STATS_BUFFER_RMQ_RESEND_RECOVERY_MAX_BATCHES);
  assert(runtime == STATS_BUFFER_RMQ_RESEND_RECOVERY_RUNTIME_US);
}

static void test_send_freq_600_cap_gt_legacy_30(void)
{
  double cap = stats_buffer_rmq_backoff_cap_sec(600.0);
  assert(cap > 30.0);
  assert(cap == 60.0);
}

int main(void)
{
  test_backoff_cap_follows_send_freq();
  test_backoff_doubles_then_caps();
  test_jitter_bounded();
  test_compute_includes_jitter();
  test_resend_limits_recovery();
  test_send_freq_600_cap_gt_legacy_30();
  printf("test_stats_buffer_rmq_policy: OK\n");
  return 0;
}
