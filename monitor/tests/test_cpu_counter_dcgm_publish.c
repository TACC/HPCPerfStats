#ifdef MONITOR_CPU_BACKEND_DCGM

#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <string.h>

#include "cpu_counter_metrics_dcgm_publish.h"
#include "cpu_counter_metrics_dcgm_state.h"
#include "stats.h"
#include "test_stats_stub.h"

int nr_cpus = 2;
int g_dcgm_ncpu_entities = 2;

static unsigned long long s_ctr0[2];
static unsigned long long s_ctr1[2];
static unsigned long long s_ctr2[2];
static unsigned long long s_ctr3[2];
static unsigned long long s_ctr4[2];
static unsigned long long s_ctr5[2];
static unsigned long long s_inst[2];
static unsigned long long s_aperf[2];
static unsigned long long s_mperf[2];
static unsigned long long s_arm_flops[2];
static unsigned long long s_arm_dram[2];
static unsigned long long s_fp_sca_d[2];
static unsigned long long s_fp_128_d[2];
static unsigned long long s_fp_256_d[2];
static unsigned long long s_fp_512_d[2];
static unsigned long long s_fp_sca_s[2];
static unsigned long long s_fp_128_s[2];
static unsigned long long s_fp_256_s[2];
static unsigned long long s_fp_512_s[2];
static int s_power_slot[2] = {0, 1};
static double s_power_util[2] = {50.5, 0.0};
static double s_power_limit[2] = {200.0, 0.0};

unsigned long long *g_dcgm_ctr0 = s_ctr0;
unsigned long long *g_dcgm_ctr1 = s_ctr1;
unsigned long long *g_dcgm_ctr2 = s_ctr2;
unsigned long long *g_dcgm_ctr3 = s_ctr3;
unsigned long long *g_dcgm_ctr4 = s_ctr4;
unsigned long long *g_dcgm_ctr5 = s_ctr5;
unsigned long long *g_dcgm_inst = s_inst;
unsigned long long *g_dcgm_aperf = s_aperf;
unsigned long long *g_dcgm_mperf = s_mperf;
unsigned long long *g_dcgm_arm_est_flops = s_arm_flops;
unsigned long long *g_dcgm_arm_dram_bytes = s_arm_dram;
unsigned long long *g_dcgm_fp_sca_d = s_fp_sca_d;
unsigned long long *g_dcgm_fp_128_d = s_fp_128_d;
unsigned long long *g_dcgm_fp_256_d = s_fp_256_d;
unsigned long long *g_dcgm_fp_512_d = s_fp_512_d;
unsigned long long *g_dcgm_fp_sca_s = s_fp_sca_s;
unsigned long long *g_dcgm_fp_128_s = s_fp_128_s;
unsigned long long *g_dcgm_fp_256_s = s_fp_256_s;
unsigned long long *g_dcgm_fp_512_s = s_fp_512_s;
int *g_dcgm_logical_to_power_slot = s_power_slot;
double *g_dcgm_sock_power_util = s_power_util;
double *g_dcgm_sock_power_limit = s_power_limit;

void stats_set(struct stats *stats, const char *key, unsigned long long val)
{
  test_stats_set_stub(stats, key, val);
}

static struct stats g_dummy_stats;

static void reset_dcgm_arrays(void)
{
  memset(s_ctr0, 0, sizeof(s_ctr0));
  memset(s_ctr1, 0, sizeof(s_ctr1));
  memset(s_ctr2, 0, sizeof(s_ctr2));
  memset(s_ctr3, 0, sizeof(s_ctr3));
  memset(s_ctr4, 0, sizeof(s_ctr4));
  memset(s_ctr5, 0, sizeof(s_ctr5));
  memset(s_inst, 0, sizeof(s_inst));
  memset(s_aperf, 0, sizeof(s_aperf));
  memset(s_mperf, 0, sizeof(s_mperf));
  memset(s_arm_flops, 0, sizeof(s_arm_flops));
  memset(s_arm_dram, 0, sizeof(s_arm_dram));
  memset(s_fp_sca_d, 0, sizeof(s_fp_sca_d));
  memset(s_fp_128_d, 0, sizeof(s_fp_128_d));
  memset(s_fp_256_d, 0, sizeof(s_fp_256_d));
  memset(s_fp_512_d, 0, sizeof(s_fp_512_d));
  memset(s_fp_sca_s, 0, sizeof(s_fp_sca_s));
  memset(s_fp_128_s, 0, sizeof(s_fp_128_s));
  memset(s_fp_256_s, 0, sizeof(s_fp_256_s));
  memset(s_fp_512_s, 0, sizeof(s_fp_512_s));
}

static void test_accumulate_from_util_sample(void)
{
  struct dcgm_cpu_sample sample;

  memset(&sample, 0, sizeof(sample));
  sample.util_total = 50.0;
  sample.util_user = 30.0;
  sample.util_sys = 10.0;
  sample.util_irq = 5.0;
  sample.util_nice = 5.0;
  sample.clock_khz = 2000000.0;

  reset_dcgm_arrays();
  dcgm_accumulate_from_util_sample(0, &sample, 1000000LL);
  assert(s_ctr0[0] == 50000000ULL);
  assert(s_ctr1[0] == 30000000ULL);
  assert(s_ctr2[0] == 10000000ULL);
  assert(s_ctr3[0] == 5000000ULL);
  assert(s_ctr4[0] == 5000000ULL);
#ifdef MONITOR_CPU_PAPI_FLOPS
  /* Hybrid: util + act cycles (ctr5/aperf); mperf=ref; FLOPs/instr stay 0 (PAPI-owned). */
  assert(s_ctr5[0] == 1000000000ULL);
  assert(s_mperf[0] == 2000000000ULL);
  assert(s_aperf[0] == 1000000000ULL);
  assert(s_inst[0] == 0ULL);
  assert(s_arm_flops[0] == 0ULL);
#else
  assert(s_ctr5[0] == 1000000000ULL);
  assert(s_mperf[0] == 2000000000ULL);
  assert(s_aperf[0] == 1000000000ULL);
  assert(s_inst[0] == 600000000ULL);
#endif

  dcgm_accumulate_from_util_sample(0, &sample, 0);
  assert(s_ctr0[0] == 50000000ULL);

  /* Idle util=0: ctr5/aperf flat; mperf still advances (ref). */
  reset_dcgm_arrays();
  sample.util_total = 0.0;
  sample.util_user = 0.0;
  sample.util_sys = 0.0;
  sample.util_irq = 0.0;
  sample.util_nice = 0.0;
  sample.clock_khz = 2000000.0;
  dcgm_accumulate_from_util_sample(0, &sample, 1000000LL);
  assert(s_ctr0[0] == 0ULL);
  assert(s_ctr5[0] == 0ULL);
  assert(s_aperf[0] == 0ULL);
  assert(s_mperf[0] == 2000000000ULL);

  /* Util must accumulate even when clock_khz is missing. */
  reset_dcgm_arrays();
  sample.util_total = 50.0;
  sample.util_user = 30.0;
  sample.util_sys = 10.0;
  sample.util_irq = 5.0;
  sample.util_nice = 5.0;
  sample.clock_khz = 0.0;
  dcgm_accumulate_from_util_sample(0, &sample, 1000000LL);
  assert(s_ctr0[0] == 50000000ULL);
  assert(s_ctr1[0] == 30000000ULL);
  assert(s_ctr5[0] == 0ULL);
  assert(s_arm_dram[0] == 0ULL);
  assert(s_mperf[0] == 0ULL);
}

static void test_publish_dcgm_cpu_stats(void)
{
  struct test_stats_stub stub;
  unsigned long long val;

  reset_dcgm_arrays();
  test_stats_stub_reset(&stub);
  test_stats_stub_bind(&stub);

  s_ctr0[0] = 111ULL;
  s_ctr5[0] = 888ULL;
  s_inst[0] = 222ULL;
  s_aperf[0] = 333ULL;
  s_mperf[0] = 444ULL;
  s_power_util[0] = 50.6;
  s_power_limit[0] = 199.4;

  publish_dcgm_cpu_stats(&g_dummy_stats, 0);
  assert(test_stats_stub_find(&stub, "cpu_util_total_accum_us", &val) && val == 111ULL);
  assert(test_stats_stub_find(&stub, "cpu_clock_est_cycles", &val) && val == 888ULL);
#ifdef MONITOR_CPU_PAPI_FLOPS
  /* Hybrid fail-soft: publish cycle estimates; leave FLOPs/instr to PAPI. */
  assert(!test_stats_stub_find(&stub, "instr_retired", &val));
  assert(test_stats_stub_find(&stub, "aperf", &val) && val == 333ULL);
  assert(test_stats_stub_find(&stub, "mperf", &val) && val == 444ULL);
#else
  assert(test_stats_stub_find(&stub, "instr_retired", &val) && val == 222ULL);
  assert(test_stats_stub_find(&stub, "aperf", &val) && val == 333ULL);
  assert(test_stats_stub_find(&stub, "mperf", &val) && val == 444ULL);
#endif
  assert(test_stats_stub_find(&stub, "dcgm_cpu_power_util_w", &val) && val == 51ULL);
  assert(test_stats_stub_find(&stub, "dcgm_cpu_power_limit_w", &val) && val == 199ULL);

  /* Blank sentinel must publish as 0 W. */
  test_stats_stub_reset(&stub);
  s_power_util[0] = 44.0;
  s_power_limit[0] = 140737488355328.0;
  publish_dcgm_cpu_stats(&g_dummy_stats, 0);
  assert(test_stats_stub_find(&stub, "dcgm_cpu_power_util_w", &val) && val == 44ULL);
  assert(test_stats_stub_find(&stub, "dcgm_cpu_power_limit_w", &val) && val == 0ULL);

  test_stats_stub_unbind();
}

int main(void)
{
  struct test_stats_stub stub;

  test_stats_stub_reset(&stub);
  test_stats_stub_bind(&stub);

  test_accumulate_from_util_sample();
  test_publish_dcgm_cpu_stats();

  test_stats_stub_unbind();
#ifdef MONITOR_CPU_PAPI_FLOPS
  printf("test_cpu_counter_dcgm_publish_papi passed\n");
#else
  printf("test_cpu_counter_dcgm_publish passed\n");
#endif
  return 0;
}

#else

#include <stdio.h>

int main(void)
{
  printf("test_cpu_counter_dcgm_publish skipped (not DCGM backend)\n");
  return 0;
}

#endif
