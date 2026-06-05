#ifndef CPU_COUNTER_METRICS_DCGM_STATE_H_
#define CPU_COUNTER_METRICS_DCGM_STATE_H_

#ifdef MONITOR_CPU_BACKEND_DCGM

extern int g_dcgm_ncpu_entities;
extern int *g_dcgm_logical_to_power_slot;
extern double *g_dcgm_sock_power_util;
extern double *g_dcgm_sock_power_limit;
extern unsigned long long *g_dcgm_ctr0;
extern unsigned long long *g_dcgm_ctr1;
extern unsigned long long *g_dcgm_ctr2;
extern unsigned long long *g_dcgm_ctr3;
extern unsigned long long *g_dcgm_ctr4;
extern unsigned long long *g_dcgm_ctr5;
extern unsigned long long *g_dcgm_inst;
extern unsigned long long *g_dcgm_aperf;
extern unsigned long long *g_dcgm_mperf;
extern unsigned long long *g_dcgm_arm_est_flops;
extern unsigned long long *g_dcgm_arm_dram_bytes;
extern unsigned long long *g_dcgm_fp_sca_d;
extern unsigned long long *g_dcgm_fp_128_d;
extern unsigned long long *g_dcgm_fp_256_d;
extern unsigned long long *g_dcgm_fp_512_d;
extern unsigned long long *g_dcgm_fp_sca_s;
extern unsigned long long *g_dcgm_fp_128_s;
extern unsigned long long *g_dcgm_fp_256_s;
extern unsigned long long *g_dcgm_fp_512_s;

unsigned long long dcgm_watts_dbl_to_ull(double v);

#endif

#endif
