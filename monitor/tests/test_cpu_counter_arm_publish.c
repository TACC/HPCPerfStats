/* `arm` host_cpu_hw accumulate/publish math: util, fail-soft cycles, and the measured
 * arm_core_perf keys.
 *
 * O15 (3.1): removing MONITOR_CPU_LIKWID_OVERLAY must never revive the /proc/stat synthetic
 * instr / FLOP / FP estimate; those keys come only from measured counters.
 * O16: measured CYC > 0 overwrites aperf / mperf / cpu_clock_est_cycles, else fail-soft stays. */
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "cpu_counter_metrics_arm_publish.h"
#include "cpu_counter_metrics_arm_state.h"
#include "stats.h"
#include "test_stats_stub.h"

#define W48 (1ULL << 48)

int nr_cpus = 2;

static unsigned long long s_ctr0[2];
static unsigned long long s_ctr1[2];
static unsigned long long s_ctr2[2];
static unsigned long long s_ctr3[2];
static unsigned long long s_ctr4[2];
static unsigned long long s_ctr5[2];
static unsigned long long s_aperf[2];
static unsigned long long s_mperf[2];
static unsigned long long s_arm_dram[2];

unsigned long long *g_arm_ctr0 = s_ctr0;
unsigned long long *g_arm_ctr1 = s_ctr1;
unsigned long long *g_arm_ctr2 = s_ctr2;
unsigned long long *g_arm_ctr3 = s_ctr3;
unsigned long long *g_arm_ctr4 = s_ctr4;
unsigned long long *g_arm_ctr5 = s_ctr5;
unsigned long long *g_arm_aperf = s_aperf;
unsigned long long *g_arm_mperf = s_mperf;
unsigned long long *g_arm_dram_bytes = s_arm_dram;

static const char *const g_measured_only_keys[] = {"instr_retired",
                                                   "arm_est_flops",
                                                   "arm_fp_dp_spec",
                                                   "arm_fp_sp_spec",
                                                   "arm_fp_hp_spec",
                                                   "arm_int8_ops",
                                                   "arm_int16_ops",
                                                   "fp_arith_inst_retired_scalar_double",
                                                   "fp_arith_inst_retired_128b_packed_double",
                                                   "fp_arith_inst_retired_256b_packed_double",
                                                   "fp_arith_inst_retired_512b_packed_double",
                                                   "fp_arith_inst_retired_scalar_single",
                                                   "fp_arith_inst_retired_128b_packed_single",
                                                   "fp_arith_inst_retired_256b_packed_single",
                                                   "fp_arith_inst_retired_512b_packed_single",
                                                   NULL};

void stats_set(struct stats *stats, const char *key, unsigned long long val)
{
  test_stats_set_stub(stats, key, val);
}

static struct stats g_dummy_stats;

static void reset_arm_arrays(void)
{
  memset(s_ctr0, 0, sizeof(s_ctr0));
  memset(s_ctr1, 0, sizeof(s_ctr1));
  memset(s_ctr2, 0, sizeof(s_ctr2));
  memset(s_ctr3, 0, sizeof(s_ctr3));
  memset(s_ctr4, 0, sizeof(s_ctr4));
  memset(s_ctr5, 0, sizeof(s_ctr5));
  memset(s_aperf, 0, sizeof(s_aperf));
  memset(s_mperf, 0, sizeof(s_mperf));
  memset(s_arm_dram, 0, sizeof(s_arm_dram));
}

static void busy_sample(struct arm_cpu_sample *sample)
{
  memset(sample, 0, sizeof(*sample));
  sample->util_total = 50.0;
  sample->util_user = 30.0;
  sample->util_sys = 10.0;
  sample->util_irq = 5.0;
  sample->util_nice = 5.0;
  sample->clock_khz = 2000000.0;
}

static void test_accumulate_from_util_sample(void)
{
  struct arm_cpu_sample sample;

  busy_sample(&sample);
  reset_arm_arrays();
  arm_accumulate_from_util_sample(0, &sample, 1000000LL);
  /* *_accum_us are CPU microseconds (util% / 100 x wall us). c122-018 regression: percent x us
   * was published, 100x the /proc/stat user time of the loaded CPUs. */
  assert(s_ctr0[0] == 500000ULL);
  assert(s_ctr1[0] == 300000ULL);
  assert(s_ctr2[0] == 100000ULL);
  assert(s_ctr3[0] == 50000ULL);
  assert(s_ctr4[0] == 50000ULL);
  /* Fail-soft cycles: act = ctr5 / aperf, ref = mperf; DRAM estimate 16 B per active cycle. */
  assert(s_ctr5[0] == 1000000000ULL);
  assert(s_mperf[0] == 2000000000ULL);
  assert(s_aperf[0] == 1000000000ULL);
  assert(s_arm_dram[0] == 16000000000ULL);

  arm_accumulate_from_util_sample(0, &sample, 0);
  assert(s_ctr0[0] == 500000ULL);
  arm_accumulate_from_util_sample(0, NULL, 1000000LL);
  assert(s_ctr0[0] == 500000ULL);

  /* Idle util=0: ctr5/aperf flat; mperf still advances (ref). */
  reset_arm_arrays();
  memset(&sample, 0, sizeof(sample));
  sample.clock_khz = 2000000.0;
  arm_accumulate_from_util_sample(0, &sample, 1000000LL);
  assert(s_ctr0[0] == 0ULL);
  assert(s_ctr5[0] == 0ULL);
  assert(s_aperf[0] == 0ULL);
  assert(s_mperf[0] == 2000000000ULL);

  /* Util must accumulate even when clock_khz is missing. */
  reset_arm_arrays();
  busy_sample(&sample);
  sample.clock_khz = 0.0;
  arm_accumulate_from_util_sample(0, &sample, 1000000LL);
  assert(s_ctr0[0] == 500000ULL);
  assert(s_ctr1[0] == 300000ULL);
  assert(s_ctr5[0] == 0ULL);
  assert(s_arm_dram[0] == 0ULL);
  assert(s_mperf[0] == 0ULL);
}

static void test_publish_never_synthesizes_instr_or_flops(void)
{
  struct test_stats_stub stub;
  struct arm_cpu_sample sample;
  unsigned long long val;
  int k;

  reset_arm_arrays();
  busy_sample(&sample);
  arm_accumulate_from_util_sample(0, &sample, 1000000LL);
  test_stats_stub_reset(&stub);
  test_stats_stub_bind(&stub);
  publish_arm_cpu_stats(&g_dummy_stats, 0);
  /* O15: a busy CPU with no measured counters emits no instr / FLOP / FP value at all. */
  for (k = 0; g_measured_only_keys[k] != NULL; k++)
    assert(!test_stats_stub_find(&stub, g_measured_only_keys[k], &val));
  assert(test_stats_stub_find(&stub, "cpu_util_total_accum_us", &val) && val == 500000ULL);
  assert(test_stats_stub_find(&stub, "cpu_clock_est_cycles", &val) && val == 1000000000ULL);
  assert(test_stats_stub_find(&stub, "aperf", &val) && val == 1000000000ULL);
  assert(test_stats_stub_find(&stub, "mperf", &val) && val == 2000000000ULL);
  assert(test_stats_stub_find(&stub, "arm_dram_bw_bytes", &val) && val == 16000000000ULL);
  /* D33 / D36: socket power moved to host_power_socket; host_cpu_hw no longer emits it. */
  assert(!test_stats_stub_find(&stub, "dcgm_cpu_power_util_w", &val));
  assert(!test_stats_stub_find(&stub, "dcgm_cpu_power_limit_w", &val));

  /* Measured counts with nothing open (begin failed) change nothing either. */
  {
    struct arm_core_counts none;

    memset(&none, 0, sizeof(none));
    publish_arm_measured_stats(&g_dummy_stats, &none);
    for (k = 0; g_measured_only_keys[k] != NULL; k++)
      assert(!test_stats_stub_find(&stub, g_measured_only_keys[k], &val));
    assert(test_stats_stub_find(&stub, "aperf", &val) && val == 1000000000ULL);
  }
  test_stats_stub_unbind();
}

static void publish_fail_soft_then(const struct arm_core_counts *m, struct test_stats_stub *stub)
{
  reset_arm_arrays();
  s_ctr5[0] = 111ULL;
  s_aperf[0] = 111ULL;
  s_mperf[0] = 222ULL;
  test_stats_stub_reset(stub);
  test_stats_stub_bind(stub);
  publish_arm_cpu_stats(&g_dummy_stats, 0);
  publish_arm_measured_stats(&g_dummy_stats, m);
}

static void test_measured_cycles_overwrite_or_fail_soft(void)
{
  struct test_stats_stub stub;
  struct arm_core_counts m;
  unsigned long long val;

  /* O16: CYC open with lifetime > 0 overwrites all three keys. */
  memset(&m, 0, sizeof(m));
  m.have[ARM_CORE_CYC] = 1;
  m.life[ARM_CORE_CYC] = 5000ULL;
  publish_fail_soft_then(&m, &stub);
  assert(test_stats_stub_find(&stub, "aperf", &val) && val == 5000ULL);
  assert(test_stats_stub_find(&stub, "mperf", &val) && val == 5000ULL);
  assert(test_stats_stub_find(&stub, "cpu_clock_est_cycles", &val) && val == 5000ULL);

  /* CYC open but still 0 (first tick, never scheduled): fail-soft stays. */
  m.life[ARM_CORE_CYC] = 0ULL;
  publish_fail_soft_then(&m, &stub);
  assert(test_stats_stub_find(&stub, "aperf", &val) && val == 111ULL);
  assert(test_stats_stub_find(&stub, "mperf", &val) && val == 222ULL);
  assert(test_stats_stub_find(&stub, "cpu_clock_est_cycles", &val) && val == 111ULL);

  /* CYC not open: fail-soft stays even with a stale lifetime. */
  m.have[ARM_CORE_CYC] = 0;
  m.life[ARM_CORE_CYC] = 9999ULL;
  publish_fail_soft_then(&m, &stub);
  assert(test_stats_stub_find(&stub, "aperf", &val) && val == 111ULL);

  /* W=48 aperf / mperf carry the lifetime modulo 2^48; the W=64 cycle key does not (O11). */
  m.have[ARM_CORE_CYC] = 1;
  m.life[ARM_CORE_CYC] = W48 + 7ULL;
  publish_fail_soft_then(&m, &stub);
  assert(test_stats_stub_find(&stub, "aperf", &val) && val == 7ULL);
  assert(test_stats_stub_find(&stub, "mperf", &val) && val == 7ULL);
  assert(test_stats_stub_find(&stub, "cpu_clock_est_cycles", &val) && val == W48 + 7ULL);
  test_stats_stub_unbind();
}

static void test_measured_keys_and_widths(void)
{
  struct test_stats_stub stub;
  struct arm_core_counts m;
  unsigned long long val;

  memset(&m, 0, sizeof(m));
  memset(m.have, 1, sizeof(m.have));
  m.life[ARM_CORE_CYC] = 100ULL;
  m.life[ARM_CORE_INS] = W48 + 5ULL;
  m.life[ARM_CORE_FP_DP] = W48 + 11ULL;
  m.life[ARM_CORE_FP_SP] = 22ULL;
  m.life[ARM_CORE_FP_HP] = 33ULL;
  m.life[ARM_CORE_FP_FIXED] = 400ULL;
  m.life[ARM_CORE_FP_SCALE] = 600ULL;
  m.life[ARM_CORE_SVE_INT8] = W48 + 8ULL;
  m.life[ARM_CORE_SVE_INT16] = 16ULL;
  publish_fail_soft_then(&m, &stub);
  /* D1 keys are W=64 software lifetimes: never wrapped. */
  assert(test_stats_stub_find(&stub, "arm_fp_dp_spec", &val) && val == W48 + 11ULL);
  assert(test_stats_stub_find(&stub, "arm_fp_sp_spec", &val) && val == 22ULL);
  assert(test_stats_stub_find(&stub, "arm_fp_hp_spec", &val) && val == 33ULL);
  /* Shared W=48 keys modulo 2^48. */
  assert(test_stats_stub_find(&stub, "instr_retired", &val) && val == 5ULL);
  assert(test_stats_stub_find(&stub, "arm_int8_ops", &val) && val == 8ULL);
  assert(test_stats_stub_find(&stub, "arm_int16_ops", &val) && val == 16ULL);
  /* arm_est_flops = FP_FIXED_OPS_SPEC + FP_SCALE_OPS_SPEC (measured FLOPs). */
  assert(test_stats_stub_find(&stub, "arm_est_flops", &val) && val == 1000ULL);
  /* Grace never fills the x86 FP_ARITH buckets. */
  assert(!test_stats_stub_find(&stub, "fp_arith_inst_retired_scalar_double", &val));

  /* Only one of FIXED / SCALE counted: arm_est_flops is not published (no partial FLOPs). */
  m.have[ARM_CORE_FP_SCALE] = 0;
  publish_fail_soft_then(&m, &stub);
  assert(!test_stats_stub_find(&stub, "arm_est_flops", &val));
  assert(test_stats_stub_find(&stub, "arm_fp_dp_spec", &val));

  /* NULL inputs are ignored. */
  publish_arm_measured_stats(NULL, &m);
  publish_arm_measured_stats(&g_dummy_stats, NULL);
  test_stats_stub_unbind();
}

int main(void)
{
  test_accumulate_from_util_sample();
  test_publish_never_synthesizes_instr_or_flops();
  test_measured_cycles_overwrite_or_fail_soft();
  test_measured_keys_and_widths();
  printf("test_cpu_counter_arm_publish passed\n");
  return 0;
}
