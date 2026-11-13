/* `arm` host_cpu_hw publish and accumulation helpers (aarch64 / Grace).
 *
 * Pure math over the per-CPU accumulators in cpu_counter_metrics_arm_state.h: util from
 * /proc/stat jiffies and util x cpufreq fail-soft cycle estimates, plus the measured core
 * counters from arm_core_perf (publish_arm_measured_stats). Instructions and FP keys come only
 * from measured counters: nothing here synthesises them (O15). Socket power is the per-socket
 * host_power_socket type (arm_power_socket.c). */
#include <stddef.h>

#include "stats.h"
#include "cpu_counter_metrics_arm_state.h"
#include "cpu_counter_metrics_arm_publish.h"

#define ARM_APPROX_DRAM_BYTES_PER_ACTIVE_CYCLE 16.0
/* Shared W=48 schema keys carry the software lifetime modulo 2^48 (O11), so the consumer's
 * 48-bit wrap handling stays correct. */
#define ARM_W48_MASK ((1ULL << 48) - 1ULL)

void publish_arm_cpu_stats(struct stats *stats, int i)
{
  stats_set(stats, "cpu_util_total_accum_us", g_arm_ctr0[i]);
  stats_set(stats, "cpu_util_user_accum_us", g_arm_ctr1[i]);
  stats_set(stats, "cpu_util_sys_accum_us", g_arm_ctr2[i]);
  stats_set(stats, "cpu_util_irq_accum_us", g_arm_ctr3[i]);
  stats_set(stats, "cpu_util_nice_accum_us", g_arm_ctr4[i]);
  /* Fail-soft util x freq cycle estimates; publish_arm_measured_stats overwrites these three
   * keys when the measured CYC lifetime is > 0 (O16). */
  stats_set(stats, "cpu_clock_est_cycles", g_arm_ctr5[i]);
  stats_set(stats, "aperf", g_arm_aperf[i]);
  stats_set(stats, "mperf", g_arm_mperf[i]);
  stats_set(stats, "dram_chan0_bytes", 0);
  stats_set(stats, "dram_chan1_bytes", 0);
  stats_set(stats, "dram_chan2_bytes", 0);
  stats_set(stats, "dram_chan3_bytes", 0);
  stats_set(stats, "arm_dram_bw_bytes", g_arm_dram_bytes[i]);
}

void publish_arm_measured_stats(struct stats *stats, const struct arm_core_counts *m)
{
  const unsigned long long *life;

  if (stats == NULL || m == NULL)
    return;
  life = m->life;
  if (m->have[ARM_CORE_CYC] && life[ARM_CORE_CYC] > 0) {
    stats_set(stats, "aperf", life[ARM_CORE_CYC] & ARM_W48_MASK);
    stats_set(stats, "mperf", life[ARM_CORE_CYC] & ARM_W48_MASK);
    stats_set(stats, "cpu_clock_est_cycles", life[ARM_CORE_CYC]);
  }
  if (m->have[ARM_CORE_INS])
    stats_set(stats, "instr_retired", life[ARM_CORE_INS] & ARM_W48_MASK);
  if (m->have[ARM_CORE_FP_DP])
    stats_set(stats, "arm_fp_dp_spec", life[ARM_CORE_FP_DP]);
  if (m->have[ARM_CORE_FP_SP])
    stats_set(stats, "arm_fp_sp_spec", life[ARM_CORE_FP_SP]);
  if (m->have[ARM_CORE_FP_HP])
    stats_set(stats, "arm_fp_hp_spec", life[ARM_CORE_FP_HP]);
  /* Measured FLOPs (D1): FP_FIXED_OPS_SPEC + FP_SCALE_OPS_SPEC, only when both are counted. */
  if (m->have[ARM_CORE_FP_FIXED] && m->have[ARM_CORE_FP_SCALE])
    stats_set(stats, "arm_est_flops", life[ARM_CORE_FP_FIXED] + life[ARM_CORE_FP_SCALE]);
  if (m->have[ARM_CORE_SVE_INT8])
    stats_set(stats, "arm_int8_ops", life[ARM_CORE_SVE_INT8] & ARM_W48_MASK);
  if (m->have[ARM_CORE_SVE_INT16])
    stats_set(stats, "arm_int16_ops", life[ARM_CORE_SVE_INT16] & ARM_W48_MASK);
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

  /* Fail-soft cycles (kept when CYC is not counted): mperf=ref, aperf=act, ctr5=act. */
  g_arm_mperf[i] += (unsigned long long)(ref_cycles + 0.5);
  g_arm_aperf[i] += (unsigned long long)(act_cycles + 0.5);
  g_arm_ctr5[i] += (unsigned long long)(act_cycles + 0.5);
}
