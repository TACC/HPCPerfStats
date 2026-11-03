/* dcgm_watch_policy — dcgmWatchFields cadence tied to the daemon's collect interval.
 *
 * The daemon reads DCGM values once per sample (dcgmGetLatestValues), so DCGM refreshing
 * fields faster than sample_freq, or keeping an hour of history, is pure overhead. Pure C,
 * no DCGM headers: linked into every build, set by the RabbitMQ daemon on conf load/SIGHUP.
 */
#ifndef DCGM_WATCH_POLICY_H
#define DCGM_WATCH_POLICY_H

#define DCGM_WATCH_MIN_UPDATE_US 1000000LL    /* never refresh faster than 1 s */
#define DCGM_WATCH_MAX_UPDATE_US 3600000000LL /* nor slower than 1 h */
#define DCGM_WATCH_KEEP_SAMPLES 2             /* latest value plus one spare */

struct dcgm_watch_policy {
  long long update_us; /* dcgmWatchFields updateFreq */
  double keep_age_s;   /* maxKeepAge: two refresh periods */
  int keep_samples;    /* maxKeepSamples */
};

/**
 * Record the collect interval (sample_freq). A non-finite or non-positive value clears it,
 * so callers fall back to their own refresh.
 */
void dcgm_watch_policy_set_interval(double interval_s);

/** The recorded interval in seconds, or 0 when unset. */
double dcgm_watch_policy_interval_s(void);

/**
 * Fill @p out for one dcgmWatchFields call.
 * @param fallback_update_us refresh to use when no interval is recorded (the caller's
 *        historical value, e.g. 1 s for CPU, 10 s for GPU); clamped like the interval.
 * @return 0 from the recorded interval, 1 from the fallback, -1 when @p out is NULL.
 */
int dcgm_watch_policy_get(long long fallback_update_us, struct dcgm_watch_policy *out);

#endif
