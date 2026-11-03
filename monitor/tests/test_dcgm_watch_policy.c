/* dcgm_watch_policy: DCGM dcgmWatchFields cadence follows the daemon's collect interval.
 *
 * Plan monitor-cpu-seconds-reduction round 1: on Horizon c122-018 (sample_freq 60 s) the DCGM
 * CPU watches refreshed every 1 s and kept 3600 samples per field, costing ~157 core-s/h in the
 * sysmon module while the daemon only read the latest value once per sample.
 */
#include <assert.h>
#include <math.h>
#include <stdio.h>

#include "dcgm_watch_policy.h"

static void test_dcgm_watch_policy_follows_interval(void)
{
  struct dcgm_watch_policy p;

  dcgm_watch_policy_set_interval(60.0);
  assert(dcgm_watch_policy_get(1000000LL, &p) == 0);
  assert(p.update_us == 60000000LL);
  assert(p.keep_age_s == 120.0);
  assert(p.keep_samples == DCGM_WATCH_KEEP_SAMPLES);
  assert(dcgm_watch_policy_interval_s() == 60.0);

  /* GPU and CPU callers get the same cadence once the interval is known (decision D2). */
  assert(dcgm_watch_policy_get(10000000LL, &p) == 0);
  assert(p.update_us == 60000000LL);
}

static void test_dcgm_watch_policy_clamps(void)
{
  struct dcgm_watch_policy p;

  dcgm_watch_policy_set_interval(0.1); /* DEBUG fast mode */
  assert(dcgm_watch_policy_get(1000000LL, &p) == 0);
  assert(p.update_us == DCGM_WATCH_MIN_UPDATE_US);
  assert(p.keep_age_s == 2.0);

  dcgm_watch_policy_set_interval(86400.0);
  assert(dcgm_watch_policy_get(1000000LL, &p) == 0);
  assert(p.update_us == DCGM_WATCH_MAX_UPDATE_US);
  assert(p.keep_age_s == 7200.0);
}

static void test_dcgm_watch_policy_fallback_when_unset_or_invalid(void)
{
  struct dcgm_watch_policy p;
  const double bad[] = {0.0, -5.0, NAN, INFINITY};
  size_t i;

  /* Batch client never sets an interval: each caller keeps its historical refresh. */
  dcgm_watch_policy_set_interval(0.0);
  assert(dcgm_watch_policy_interval_s() == 0.0);
  assert(dcgm_watch_policy_get(10000000LL, &p) == 1);
  assert(p.update_us == 10000000LL && p.keep_age_s == 20.0 && p.keep_samples == 2);

  for (i = 0; i < sizeof(bad) / sizeof(bad[0]); i++) {
    dcgm_watch_policy_set_interval(60.0);
    dcgm_watch_policy_set_interval(bad[i]); /* invalid input clears, never keeps stale */
    assert(dcgm_watch_policy_interval_s() == 0.0);
    assert(dcgm_watch_policy_get(1000000LL, &p) == 1);
    assert(p.update_us == 1000000LL);
  }

  /* A bad fallback is clamped too, so DCGM never sees 0 or a negative frequency. */
  assert(dcgm_watch_policy_get(0LL, &p) == 1);
  assert(p.update_us == DCGM_WATCH_MIN_UPDATE_US);
}

static void test_dcgm_watch_policy_rejects_null(void)
{
  dcgm_watch_policy_set_interval(60.0);
  assert(dcgm_watch_policy_get(1000000LL, NULL) == -1);
  dcgm_watch_policy_set_interval(0.0);
}

int main(void)
{
  test_dcgm_watch_policy_follows_interval();
  test_dcgm_watch_policy_clamps();
  test_dcgm_watch_policy_fallback_when_unset_or_invalid();
  test_dcgm_watch_policy_rejects_null();
  printf("test_dcgm_watch_policy: ok\n");
  return 0;
}
