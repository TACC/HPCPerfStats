#ifndef CPU_COUNTER_METRICS_ARM_STATE_H_
#define CPU_COUNTER_METRICS_ARM_STATE_H_

/* Per-CPU accumulators of the `arm` host_cpu_hw backend, owned by cpu_counter_metrics.c and
 * read by cpu_counter_metrics_arm_publish.c (and its unit tests). ctr0..4 = util accum us,
 * ctr5 = fail-soft active cycles. Measured counters live in arm_core_perf.c. */

extern unsigned long long *g_arm_ctr0;
extern unsigned long long *g_arm_ctr1;
extern unsigned long long *g_arm_ctr2;
extern unsigned long long *g_arm_ctr3;
extern unsigned long long *g_arm_ctr4;
extern unsigned long long *g_arm_ctr5;
extern unsigned long long *g_arm_aperf;
extern unsigned long long *g_arm_mperf;
extern unsigned long long *g_arm_dram_bytes;

#endif
