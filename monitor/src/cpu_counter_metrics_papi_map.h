#ifndef CPU_COUNTER_METRICS_PAPI_MAP_H_
#define CPU_COUNTER_METRICS_PAPI_MAP_H_

#include <stdint.h>

struct stats;

/* Raw PAPI (or native) counter deltas / cumulatives for one logical CPU. */
struct papi_cpu_hw_counters {
  unsigned long long cycles;
  unsigned long long sp_ops;
  unsigned long long dp_ops;
  unsigned long long instr;
  unsigned long long int8_ops;
  unsigned long long int16_ops;
  int have_cycles;
  int have_sp;
  int have_dp;
  int have_instr;
  int have_int8;
  int have_int16;
};

/*
 * Map PAPI SP/DP/cycles(/instr)/int8/int16 onto portable host_cpu_hw keys:
 *   fp_arith_inst_retired_scalar_single / _scalar_double
 *   arm_est_flops = SP + DP (never includes int ops)
 *   arm_int8_ops / arm_int16_ops from ASE_SVE_INT{8,16}_SPEC
 *   aperf / mperf / cpu_clock_est_cycles from cycles
 * Packed FP width buckets are zeroed. Does not touch util or DCGM power keys.
 */
void papi_map_counters_to_host_cpu_hw(struct stats *stats, const struct papi_cpu_hw_counters *c);

#endif
