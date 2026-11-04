#ifndef CPU_COUNTER_METRICS_DCGM_UTIL_H_
#define CPU_COUNTER_METRICS_DCGM_UTIL_H_

#include <stddef.h>
#include <time.h>

/* True for NVIDIA DCGM_FP64_BLANK and blank-family sentinels (>= blank). */
int dcgm_fp64_value_is_blank(double v);
/* True for NVIDIA DCGM_INT64_BLANK and blank-family sentinels (>= blank). */
int dcgm_int64_value_is_blank(long long v);
unsigned long long dcgm_watts_dbl_to_ull(double v);

/* Collect host_cpu_hw when DCGM is up and/or overlay/util buffers remain after soft fail. */
int dcgm_host_cpu_hw_collect_active(int dcgm_ready, int overlay_ready, int util_bufs_ok);
/* True when retry_after is unset or now is past the backoff deadline. */
int dcgm_backend_retry_due(time_t now, time_t retry_after);
/* Interval (us) one DCGM util sample covers. DCGM refreshes at sample_freq, so a tick can read
 * the same sample again (ts == last_ts): credit 0, not the wall delta. Jiffy-derived util
 * (from_dcgm 0), a first sample, or a ts that went back / jumped >= 1 h use wall_us. */
long long dcgm_cpu_sample_delta_us(long long wall_us, long long ts, long long last_ts,
                                   int from_dcgm);
int dcgm_count_unique_sorted_ints(const int *sorted, int n);

#ifdef MONITOR_CPU_BACKEND_DCGM
#include "cpu_counter_metrics_dcgm_publish.h"

struct dcgm_cpu_jifs {
  unsigned long long u, nice, sys, idle, iow, irq, sft, stl, gu, gn;
};

double dcgm_clamp_percent(double v);
void dcgm_cpu_scale_util_if_fraction(struct dcgm_cpu_sample *s);
void dcgm_cpu_sample_from_jiffy_diff(struct dcgm_cpu_sample *s, const struct dcgm_cpu_jifs *cur,
                                     const struct dcgm_cpu_jifs *prev);
#endif

#endif
