/* DCGM CPU counter publish and accumulation helpers. */
#include <stddef.h>
#include <stdlib.h>
#include <stdio.h>
#include <stdint.h>
#include <limits.h>
#include <string.h>
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <unistd.h>
#include <time.h>
#include "stats.h"
#include "trace.h"
#include "cpuid.h"
#include "monitor_log.h"
#ifdef MONITOR_CPU_BACKEND_DCGM
/* No libdcgm headers: publish/accumulate are pure math over shared state (unit-tested
 * without DCGM on LIKWID and foreign cross trees). */
#include "cpu_counter_metrics_dcgm_state.h"
#include "cpu_counter_metrics_dcgm_publish.h"

#define ARM_APPROX_FLOPS_PER_ACTIVE_CYCLE 2.0
#define ARM_APPROX_DRAM_BYTES_PER_ACTIVE_CYCLE 16.0
#define ARM_APPROX_FP64_FLOP_SHARE 0.5
#define ARM_APPROX_FP64_VECTOR_FLOP_SHARE 0.6
#define ARM_APPROX_FP32_VECTOR_FLOP_SHARE 0.8

void publish_dcgm_cpu_stats(struct stats *stats, int i)
{
  stats_set(stats, "cpu_util_total_accum_us", g_dcgm_ctr0[i]);
  stats_set(stats, "cpu_util_user_accum_us", g_dcgm_ctr1[i]);
  stats_set(stats, "cpu_util_sys_accum_us", g_dcgm_ctr2[i]);
  stats_set(stats, "cpu_util_irq_accum_us", g_dcgm_ctr3[i]);
  stats_set(stats, "cpu_util_nice_accum_us", g_dcgm_ctr4[i]);
#ifndef MONITOR_CPU_PAPI_FLOPS
  stats_set(stats, "cpu_clock_est_cycles", g_dcgm_ctr5[i]);
  /* Match Intel LIKWID FIXC0..2 mapping (INSTR_RETIRED / core unhalted / ref). */
  stats_set(stats, "instr_retired", g_dcgm_inst[i]);
  stats_set(stats, "aperf", g_dcgm_aperf[i]);
  stats_set(stats, "mperf", g_dcgm_mperf[i]);
  stats_set(stats, "fp_arith_inst_retired_scalar_double", g_dcgm_fp_sca_d[i]);
  stats_set(stats, "fp_arith_inst_retired_128b_packed_double", g_dcgm_fp_128_d[i]);
  stats_set(stats, "fp_arith_inst_retired_256b_packed_double", g_dcgm_fp_256_d[i]);
  stats_set(stats, "fp_arith_inst_retired_512b_packed_double", g_dcgm_fp_512_d[i]);
  stats_set(stats, "fp_arith_inst_retired_scalar_single", g_dcgm_fp_sca_s[i]);
  stats_set(stats, "fp_arith_inst_retired_128b_packed_single", g_dcgm_fp_128_s[i]);
  stats_set(stats, "fp_arith_inst_retired_256b_packed_single", g_dcgm_fp_256_s[i]);
  stats_set(stats, "fp_arith_inst_retired_512b_packed_single", g_dcgm_fp_512_s[i]);
  stats_set(stats, "arm_est_flops", g_dcgm_arm_est_flops[i]);
#endif
  stats_set(stats, "dram_chan0_bytes", 0);
  stats_set(stats, "dram_chan1_bytes", 0);
  stats_set(stats, "dram_chan2_bytes", 0);
  stats_set(stats, "dram_chan3_bytes", 0);
  stats_set(stats, "arm_dram_bw_bytes", g_dcgm_arm_dram_bytes[i]);
  if (g_dcgm_logical_to_power_slot != NULL && i >= 0 && i < nr_cpus) {
    int slot = g_dcgm_logical_to_power_slot[i];

    if (slot >= 0 && slot < g_dcgm_ncpu_entities && g_dcgm_sock_power_util != NULL &&
        g_dcgm_sock_power_limit != NULL) {
      stats_set(stats, "dcgm_cpu_power_util_w",
                dcgm_watts_dbl_to_ull(g_dcgm_sock_power_util[slot]));
      stats_set(stats, "dcgm_cpu_power_limit_w",
                dcgm_watts_dbl_to_ull(g_dcgm_sock_power_limit[slot]));
    } else {
      stats_set(stats, "dcgm_cpu_power_util_w", 0ULL);
      stats_set(stats, "dcgm_cpu_power_limit_w", 0ULL);
    }
  } else {
    stats_set(stats, "dcgm_cpu_power_util_w", 0ULL);
    stats_set(stats, "dcgm_cpu_power_limit_w", 0ULL);
  }
}

void dcgm_accumulate_from_util_sample(int i, struct dcgm_cpu_sample *sample, long long delta_us)
{
  double ref_cycles;
  double act_cycles;

  if (delta_us <= 0 || sample == NULL)
    return;

  /* Util accumulators always from DCGM/proc — do not require clock_khz. */
  g_dcgm_ctr0[i] += (unsigned long long)((sample->util_total * (double)delta_us) + 0.5);
  g_dcgm_ctr1[i] += (unsigned long long)((sample->util_user * (double)delta_us) + 0.5);
  g_dcgm_ctr2[i] += (unsigned long long)((sample->util_sys * (double)delta_us) + 0.5);
  g_dcgm_ctr3[i] += (unsigned long long)((sample->util_irq * (double)delta_us) + 0.5);
  g_dcgm_ctr4[i] += (unsigned long long)((sample->util_nice * (double)delta_us) + 0.5);

  if (sample->clock_khz <= 0.0)
    return;

  ref_cycles = (sample->clock_khz * (double)delta_us) / 1000.0;
  act_cycles = ref_cycles * (sample->util_total / 100.0);
  g_dcgm_arm_dram_bytes[i] +=
      (unsigned long long)((act_cycles * ARM_APPROX_DRAM_BYTES_PER_ACTIVE_CYCLE) + 0.5);
#ifndef MONITOR_CPU_PAPI_FLOPS
  /* Synthetic cycles/FLOPs only when PAPI overlay is not compiled in. */
  g_dcgm_mperf[i] += (unsigned long long)(ref_cycles + 0.5);
  g_dcgm_aperf[i] += (unsigned long long)(act_cycles + 0.5);
  g_dcgm_inst[i] += (unsigned long long)((ref_cycles * (sample->util_user / 100.0)) + 0.5);
  g_dcgm_ctr5[i] += (unsigned long long)((sample->clock_khz * (double)delta_us) / 1000.0 + 0.5);
  g_dcgm_arm_est_flops[i] +=
      (unsigned long long)((act_cycles * ARM_APPROX_FLOPS_PER_ACTIVE_CYCLE) + 0.5);
  {
    double total_flops = act_cycles * ARM_APPROX_FLOPS_PER_ACTIVE_CYCLE;
    double flops64 = total_flops * ARM_APPROX_FP64_FLOP_SHARE;
    double flops32 = total_flops - flops64;
    double flops64_vec = flops64 * ARM_APPROX_FP64_VECTOR_FLOP_SHARE;
    double flops64_sca = flops64 - flops64_vec;
    double flops32_vec = flops32 * ARM_APPROX_FP32_VECTOR_FLOP_SHARE;
    double flops32_sca = flops32 - flops32_vec;
    /* Map vector FLOPs to 128b packed buckets by default (2x64b, 4x32b). */
    g_dcgm_fp_sca_d[i] += (unsigned long long)(flops64_sca + 0.5);
    g_dcgm_fp_128_d[i] += (unsigned long long)(flops64_vec / 2.0 + 0.5);
    g_dcgm_fp_sca_s[i] += (unsigned long long)(flops32_sca + 0.5);
    g_dcgm_fp_128_s[i] += (unsigned long long)(flops32_vec / 4.0 + 0.5);
  }
#endif
}

#endif
