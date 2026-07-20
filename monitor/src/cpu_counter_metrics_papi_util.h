#ifndef CPU_COUNTER_METRICS_PAPI_UTIL_H_
#define CPU_COUNTER_METRICS_PAPI_UTIL_H_

#include <sys/resource.h>

/* Slot IDs must match cpu_counter_metrics_papi.c PAPI_SLOT_*. */
enum {
  PAPI_UTIL_SLOT_CYC = 0,
  PAPI_UTIL_SLOT_SP = 1,
  PAPI_UTIL_SLOT_DP = 2,
  PAPI_UTIL_SLOT_INS = 3,
  PAPI_UTIL_SLOT_INT8 = 4,
  PAPI_UTIL_SLOT_INT16 = 5
};

enum {
  /* System-wide default: CYC then INS only (avoid 5×Ncpu PMU starvation). */
  PAPI_SW_FILTER_CYC_INS = 0,
  /* Reshrink fallback: CYC only. */
  PAPI_SW_FILTER_CYC_ONLY = 1
};

rlim_t papi_desired_nofile_soft(int nr_cpus, int n_active);
/* Cap active event count to hardware counters (keeps first N; CYC must be first). */
int papi_shrink_active_count(int n_active, int hwctrs);
int papi_is_partial_attach(int ok_cpus, int nr_cpus);

/*
 * Filter parallel slots[]/codes[] in place for system-wide attach.
 * Returns new n_active (0 if CYC missing).
 */
int papi_filter_system_wide_events(int *slots, int *codes, int n_active, int filter_mode);

/* min nonzero cycle CPUs before census treats attach as "counting". */
int papi_census_min_nonzero_cyc(int nr_cpus);

/* 1 if attach looks full but almost no CPUs show nonzero cycles → reshrink. */
int papi_census_needs_reshrink(int nonzero_cyc, int ok_cpus, int nr_cpus);

#endif
