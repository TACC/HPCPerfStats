#ifndef CPU_COUNTER_METRICS_PAPI_UTIL_H_
#define CPU_COUNTER_METRICS_PAPI_UTIL_H_

#include <sys/resource.h>

rlim_t papi_desired_nofile_soft(int nr_cpus, int n_active);
/* Cap active event count to hardware counters (priority: keep first N). */
int papi_shrink_active_count(int n_active, int hwctrs);
int papi_is_partial_attach(int ok_cpus, int nr_cpus);

#endif
