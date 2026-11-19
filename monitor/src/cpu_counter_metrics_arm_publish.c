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
  stats_set(stats, "aperf", g_arm_aperf[i] & ARM_W48_MASK);
  stats_set(stats, "mperf", g_arm_mperf[i] & ARM_W48_MASK);
  /* No per-channel DRAM reader on Grace (DRAM traffic is arm_aarch64_imc SCF). */
  stats_set_missing(stats, "dram_chan0_bytes", STATS_SENTINEL_NOT_BUILT);
  stats_set_missing(stats, "dram_chan1_bytes", STATS_SENTINEL_NOT_BUILT);
  stats_set_missing(stats, "dram_chan2_bytes", STATS_SENTINEL_NOT_BUILT);
  stats_set_missing(stats, "dram_chan3_bytes", STATS_SENTINEL_NOT_BUILT);
  stats_set(stats, "arm_dram_bw_bytes", g_arm_dram_bytes[i]);
}

/* STATS_SENTINEL_OK when event @p e was read this interval, else why not (an event that is not
 * open and carries no code is NOT_PRESENT). */
static enum stats_sentinel event_code(const struct arm_core_counts *m, int e)
{
  if (m->code[e] != STATS_SENTINEL_OK)
    return (enum stats_sentinel)m->code[e];
  return m->have[e] ? STATS_SENTINEL_OK : STATS_SENTINEL_NOT_PRESENT;
}

/* Publish one event lifetime (masked to the key's width), or the event's sentinel. */
static void publish_event(struct stats *stats, const char *key, const struct arm_core_counts *m,
                          int e, unsigned long long mask)
{
  enum stats_sentinel code = event_code(m, e);

  if (code == STATS_SENTINEL_OK)
    stats_set(stats, key, m->life[e] & mask);
  else
    stats_set_missing(stats, key, code);
}

void publish_arm_measured_stats(struct stats *stats, const struct arm_core_counts *m)
{
  const unsigned long long *life;

  if (stats == NULL || m == NULL)
    return;
  life = m->life;
  /* aperf / mperf / cpu_clock_est_cycles keep the util x freq estimate unless CYC measured. */
  if (event_code(m, ARM_CORE_CYC) == STATS_SENTINEL_OK && life[ARM_CORE_CYC] > 0) {
    stats_set(stats, "aperf", life[ARM_CORE_CYC] & ARM_W48_MASK);
    stats_set(stats, "mperf", life[ARM_CORE_CYC] & ARM_W48_MASK);
    stats_set(stats, "cpu_clock_est_cycles", life[ARM_CORE_CYC]);
  }
  publish_event(stats, "instr_retired", m, ARM_CORE_INS, ARM_W48_MASK);
  publish_event(stats, "arm_fp_dp_spec", m, ARM_CORE_FP_DP, ~0ULL);
  publish_event(stats, "arm_fp_sp_spec", m, ARM_CORE_FP_SP, ~0ULL);
  publish_event(stats, "arm_fp_hp_spec", m, ARM_CORE_FP_HP, ~0ULL);
  /* Measured FLOPs (D1): FP_FIXED_OPS_SPEC + FP_SCALE_OPS_SPEC, only when both measured. */
  if (event_code(m, ARM_CORE_FP_FIXED) != STATS_SENTINEL_OK)
    stats_set_missing(stats, "arm_est_flops", event_code(m, ARM_CORE_FP_FIXED));
  else if (event_code(m, ARM_CORE_FP_SCALE) != STATS_SENTINEL_OK)
    stats_set_missing(stats, "arm_est_flops", event_code(m, ARM_CORE_FP_SCALE));
  else
    stats_set(stats, "arm_est_flops", life[ARM_CORE_FP_FIXED] + life[ARM_CORE_FP_SCALE]);
  publish_event(stats, "arm_int8_ops", m, ARM_CORE_SVE_INT8, ARM_W48_MASK);
  publish_event(stats, "arm_int16_ops", m, ARM_CORE_SVE_INT16, ARM_W48_MASK);
  /* D6 PMU sharing: W=64 time lifetimes (ns). Ingest sums host_cpu_hw rows across CPUs, so the
   * consumer derives the host share as d(running) / d(enabled); outside sharing as running
   * below solo. No solo key without a known counter count. */
  if (m->have_times) {
    stats_set(stats, "pmu_enabled_time", m->pmu_enabled_ns);
    stats_set(stats, "pmu_running_time", m->pmu_running_ns);
  }
  if (m->have_times && m->have_solo)
    stats_set(stats, "pmu_solo_running_time", m->pmu_solo_ns);
}

void arm_accumulate_from_util_sample(int i, const struct arm_cpu_sample *sample, long long delta_us)
{
  double ref_cycles;
  double act_cycles;
  double us_per_pct;

  if (delta_us <= 0 || sample == NULL)
    return;

  /* Util accumulators always from /proc/stat; do not require clock_khz. util_* are percent;
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
