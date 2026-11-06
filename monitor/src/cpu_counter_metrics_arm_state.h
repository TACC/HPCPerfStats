#ifndef CPU_COUNTER_METRICS_ARM_STATE_H_
#define CPU_COUNTER_METRICS_ARM_STATE_H_

/* Per-CPU accumulators of the `arm` host_cpu_hw backend, owned by cpu_counter_metrics.c and
 * read by cpu_counter_metrics_arm_publish.c (and its unit tests). */

extern unsigned long long *g_arm_ctr0;
extern unsigned long long *g_arm_ctr1;
extern unsigned long long *g_arm_ctr2;
extern unsigned long long *g_arm_ctr3;
extern unsigned long long *g_arm_ctr4;
extern unsigned long long *g_arm_ctr5;
extern unsigned long long *g_arm_inst;
extern unsigned long long *g_arm_aperf;
extern unsigned long long *g_arm_mperf;
extern unsigned long long *g_arm_est_flops;
extern unsigned long long *g_arm_dram_bytes;
extern unsigned long long *g_arm_fp_sca_d;
extern unsigned long long *g_arm_fp_128_d;
extern unsigned long long *g_arm_fp_256_d;
extern unsigned long long *g_arm_fp_512_d;
extern unsigned long long *g_arm_fp_sca_s;
extern unsigned long long *g_arm_fp_128_s;
extern unsigned long long *g_arm_fp_256_s;
extern unsigned long long *g_arm_fp_512_s;

#endif
