/* dcgm_watch_policy — dcgmWatchFields cadence tied to the daemon's collect interval. */
#include <math.h>
#include <stddef.h>

#include "dcgm_watch_policy.h"

static double g_dcgm_watch_interval_s;

void dcgm_watch_policy_set_interval(double interval_s)
{
  g_dcgm_watch_interval_s = (isfinite(interval_s) && interval_s > 0.0) ? interval_s : 0.0;
}

double dcgm_watch_policy_interval_s(void)
{
  return g_dcgm_watch_interval_s;
}

int dcgm_watch_policy_get(long long fallback_update_us, struct dcgm_watch_policy *out)
{
  int from_interval = g_dcgm_watch_interval_s > 0.0;
  long long us =
      from_interval ? (long long)llround(g_dcgm_watch_interval_s * 1e6) : fallback_update_us;

  if (out == NULL)
    return -1;
  if (us < DCGM_WATCH_MIN_UPDATE_US)
    us = DCGM_WATCH_MIN_UPDATE_US;
  if (us > DCGM_WATCH_MAX_UPDATE_US)
    us = DCGM_WATCH_MAX_UPDATE_US;
  out->update_us = us;
  out->keep_age_s = 2.0 * (double)us / 1e6;
  out->keep_samples = DCGM_WATCH_KEEP_SAMPLES;
  return from_interval ? 0 : 1;
}
