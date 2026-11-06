/* `arm` host_cpu_hw publish and accumulation helpers (aarch64 / Grace).
 *
 * Pure math over the per-CPU accumulators in cpu_counter_metrics_arm_state.h: util from
 * /proc/stat jiffies, util x cpufreq cycle estimates. Socket power is not published here; it is
 * the per-socket host_power_socket type (arm_power_socket.c, round 2 D31-D33). */
#include <stddef.h>

#include "stats.h"
#include "cpu_counter_metrics_arm_state.h"
#include "cpu_counter_metrics_arm_publish.h"

#define ARM_APPROX_FLOPS_PER_ACTIVE_CYCLE 2.0
#define ARM_APPROX_DRAM_BYTES_PER_ACTIVE_CYCLE 16.0
#define ARM_APPROX_FP64_FLOP_SHARE 0.5
#define ARM_APPROX_FP64_VECTOR_FLOP_SHARE 0.6
#define ARM_APPROX_FP32_VECTOR_FLOP_SHARE 0.8

void publish_arm_cpu_stats(struct stats *stats, int i)
{
  stats_set(stats, "cpu_util_total_accum_us", g_arm_ctr0[i]);
  stats_set(stats, "cpu_util_user_accum_us", g_arm_ctr1[i]);
  stats_set(stats, "cpu_util_sys_accum_us", g_arm_ctr2[i]);
  stats_set(stats, "cpu_util_irq_accum_us", g_arm_ctr3[i]);
  stats_set(stats, "cpu_util_nice_accum_us", g_arm_ctr4[i]);
  /*
   * Always publish util×freq cycle estimates. Under MONITOR_CPU_LIKWID_OVERLAY,
   * LIKWID overwrites these three keys only when measured cycles are nonzero.
   */
  stats_set(stats, "cpu_clock_est_cycles", g_arm_ctr5[i]);
  stats_set(stats, "aperf", g_arm_aperf[i]);
  stats_set(stats, "mperf", g_arm_mperf[i]);
#ifndef MONITOR_CPU_LIKWID_OVERLAY
  /* Match Intel LIKWID FIXC0..2 mapping (INSTR_RETIRED / core unhalted / ref). */
  stats_set(stats, "instr_retired", g_arm_inst[i]);
  stats_set(stats, "fp_arith_inst_retired_scalar_double", g_arm_fp_sca_d[i]);
  stats_set(stats, "fp_arith_inst_retired_128b_packed_double", g_arm_fp_128_d[i]);
  stats_set(stats, "fp_arith_inst_retired_256b_packed_double", g_arm_fp_256_d[i]);
  stats_set(stats, "fp_arith_inst_retired_512b_packed_double", g_arm_fp_512_d[i]);
  stats_set(stats, "fp_arith_inst_retired_scalar_single", g_arm_fp_sca_s[i]);
  stats_set(stats, "fp_arith_inst_retired_128b_packed_single", g_arm_fp_128_s[i]);
  stats_set(stats, "fp_arith_inst_retired_256b_packed_single", g_arm_fp_256_s[i]);
  stats_set(stats, "fp_arith_inst_retired_512b_packed_single", g_arm_fp_512_s[i]);
  stats_set(stats, "arm_est_flops", g_arm_est_flops[i]);
#endif
  stats_set(stats, "dram_chan0_bytes", 0);
  stats_set(stats, "dram_chan1_bytes", 0);
  stats_set(stats, "dram_chan2_bytes", 0);
  stats_set(stats, "dram_chan3_bytes", 0);
  stats_set(stats, "arm_dram_bw_bytes", g_arm_dram_bytes[i]);
}

void arm_accumulate_from_util_sample(int i, const struct arm_cpu_sample *sample, long long delta_us)
{
  double ref_cycles;
  double act_cycles;
  double us_per_pct;

  if (delta_us <= 0 || sample == NULL)
    return;

  /* Util accumulators always from /proc/stat — do not require clock_khz. util_* are percent;
   * *_accum_us are CPU microseconds, so scale by delta_us / 100. */
  us_per_pct = (double)delta_us / 100.0;
  g_arm_ctr0[i] += (unsigned long long)((sample->util_total * us_per_pct) + 0.5);
  g_arm_ctr1[i] += (unsigned long long)((sample->util_user * us_per_pct) + 0.5);
  g_arm_ctr2[i] += (unsigned long long)((sample->util_sys * us_per_pct) + 0.5);
  g_arm_ctr3[i] += (unsigned long long)((sample->util_irq * us_per_pct) + 0.5);
  g_arm_ctr4[i] += (unsigned long long)((sample->util_nice * us_per_pct) + 0.5);

  if (sample->clock_khz <= 0.0)
    return;

  ref_cycles = (sample->clock_khz * (double)delta_us) / 1000.0;
  act_cycles = ref_cycles * (sample->util_total / 100.0);
  g_arm_dram_bytes[i] +=
      (unsigned long long)((act_cycles * ARM_APPROX_DRAM_BYTES_PER_ACTIVE_CYCLE) + 0.5);

  /* Fail-soft under sparse LIKWID overlay: mperf=ref, aperf=act, ctr5=act. */
  g_arm_mperf[i] += (unsigned long long)(ref_cycles + 0.5);
  g_arm_aperf[i] += (unsigned long long)(act_cycles + 0.5);
  g_arm_ctr5[i] += (unsigned long long)(act_cycles + 0.5);

#ifndef MONITOR_CPU_LIKWID_OVERLAY
  /* Synthetic FLOPs/instr only when LIKWID overlay is not compiled in. */
  g_arm_inst[i] += (unsigned long long)((ref_cycles * (sample->util_user / 100.0)) + 0.5);
  g_arm_est_flops[i] +=
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
    g_arm_fp_sca_d[i] += (unsigned long long)(flops64_sca + 0.5);
    g_arm_fp_128_d[i] += (unsigned long long)(flops64_vec / 2.0 + 0.5);
    g_arm_fp_sca_s[i] += (unsigned long long)(flops32_sca + 0.5);
    g_arm_fp_128_s[i] += (unsigned long long)(flops32_vec / 4.0 + 0.5);
  }
#endif
}
