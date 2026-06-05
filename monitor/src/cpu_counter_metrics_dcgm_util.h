#ifndef CPU_COUNTER_METRICS_DCGM_UTIL_H_
#define CPU_COUNTER_METRICS_DCGM_UTIL_H_

#include <stddef.h>

#include "cpu_counter_metrics_dcgm_publish.h"

struct dcgm_cpu_jifs {
  unsigned long long u, nice, sys, idle, iow, irq, sft, stl, gu, gn;
};

double dcgm_clamp_percent(double v);
void dcgm_cpu_scale_util_if_fraction(struct dcgm_cpu_sample *s);
void dcgm_cpu_sample_from_jiffy_diff(struct dcgm_cpu_sample *s,
                                     const struct dcgm_cpu_jifs *cur,
                                     const struct dcgm_cpu_jifs *prev);
int dcgm_count_unique_sorted_ints(const int *sorted, int n);
unsigned long long dcgm_watts_dbl_to_ull(double v);

#endif
