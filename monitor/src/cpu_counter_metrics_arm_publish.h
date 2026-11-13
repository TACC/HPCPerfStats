#ifndef CPU_COUNTER_METRICS_ARM_PUBLISH_H_
#define CPU_COUNTER_METRICS_ARM_PUBLISH_H_

#include "arm_core_perf.h"

struct stats;

/* One logical CPU over one collect interval: util percent from /proc/stat, clock from cpufreq. */
struct arm_cpu_sample {
  double util_total;
  double util_user;
  double util_nice;
  double util_sys;
  double util_irq;
  double clock_khz;
};

void arm_accumulate_from_util_sample(int i, const struct arm_cpu_sample *sample,
                                     long long delta_us);
/* Util accumulators, fail-soft cycles (aperf / mperf / cpu_clock_est_cycles) and the
 * arm_dram_bw_bytes estimate. Never instr_retired / FP / arm_est_flops (O15). */
void publish_arm_cpu_stats(struct stats *stats, int i);
/* Measured arm_core_perf lifetimes: CYC > 0 overwrites the three cycle keys (O16); D1 keys
 * (W=64), instr_retired / arm_int8_ops / arm_int16_ops modulo 2^48 (O11); arm_est_flops =
 * FP_FIXED_OPS_SPEC + FP_SCALE_OPS_SPEC. Events not counted leave their keys untouched. */
void publish_arm_measured_stats(struct stats *stats, const struct arm_core_counts *m);

#endif
