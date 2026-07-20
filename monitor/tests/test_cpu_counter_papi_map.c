#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "cpu_counter_metrics_papi_map.h"
#include "stats.h"
#include "test_stats_stub.h"

void stats_set(struct stats *stats, const char *key, unsigned long long val)
{
  test_stats_set_stub(stats, key, val);
}

static struct stats g_dummy;

static void test_map_sp_dp_sum_and_cycles(void)
{
  struct test_stats_stub stub;
  struct papi_cpu_hw_counters c;
  unsigned long long val;

  memset(&c, 0, sizeof(c));
  c.have_sp = 1;
  c.sp_ops = 100;
  c.have_dp = 1;
  c.dp_ops = 250;
  c.have_cycles = 1;
  c.cycles = 999;
  c.have_instr = 1;
  c.instr = 42;

  test_stats_stub_reset(&stub);
  test_stats_stub_bind(&stub);
  papi_map_counters_to_host_cpu_hw(&g_dummy, &c);

  assert(test_stats_stub_find(&stub, "fp_arith_inst_retired_scalar_single", &val) && val == 100ULL);
  assert(test_stats_stub_find(&stub, "fp_arith_inst_retired_scalar_double", &val) && val == 250ULL);
  assert(test_stats_stub_find(&stub, "arm_est_flops", &val) && val == 350ULL);
  assert(test_stats_stub_find(&stub, "arm_int8_ops", &val) && val == 0ULL);
  assert(test_stats_stub_find(&stub, "arm_int16_ops", &val) && val == 0ULL);
  assert(test_stats_stub_find(&stub, "aperf", &val) && val == 999ULL);
  assert(test_stats_stub_find(&stub, "mperf", &val) && val == 999ULL);
  assert(test_stats_stub_find(&stub, "cpu_clock_est_cycles", &val) && val == 999ULL);
  assert(test_stats_stub_find(&stub, "instr_retired", &val) && val == 42ULL);
  assert(test_stats_stub_find(&stub, "fp_arith_inst_retired_128b_packed_double", &val) && val == 0ULL);
  assert(test_stats_stub_find(&stub, "fp_arith_inst_retired_256b_packed_single", &val) && val == 0ULL);

  test_stats_stub_unbind();
}

static void test_map_partial_sp_only(void)
{
  struct test_stats_stub stub;
  struct papi_cpu_hw_counters c;
  unsigned long long val;

  memset(&c, 0, sizeof(c));
  c.have_sp = 1;
  c.sp_ops = 7;

  test_stats_stub_reset(&stub);
  test_stats_stub_bind(&stub);
  papi_map_counters_to_host_cpu_hw(&g_dummy, &c);

  assert(test_stats_stub_find(&stub, "fp_arith_inst_retired_scalar_single", &val) && val == 7ULL);
  assert(test_stats_stub_find(&stub, "fp_arith_inst_retired_scalar_double", &val) && val == 0ULL);
  assert(test_stats_stub_find(&stub, "arm_est_flops", &val) && val == 7ULL);
  assert(test_stats_stub_find(&stub, "aperf", &val) && val == 0ULL);

  test_stats_stub_unbind();
}

static void test_map_int8_int16_excluded_from_flops(void)
{
  struct test_stats_stub stub;
  struct papi_cpu_hw_counters c;
  unsigned long long val;

  memset(&c, 0, sizeof(c));
  c.have_sp = 1;
  c.sp_ops = 10;
  c.have_dp = 1;
  c.dp_ops = 20;
  c.have_int8 = 1;
  c.int8_ops = 1000;
  c.have_int16 = 1;
  c.int16_ops = 2000;

  test_stats_stub_reset(&stub);
  test_stats_stub_bind(&stub);
  papi_map_counters_to_host_cpu_hw(&g_dummy, &c);

  assert(test_stats_stub_find(&stub, "arm_int8_ops", &val) && val == 1000ULL);
  assert(test_stats_stub_find(&stub, "arm_int16_ops", &val) && val == 2000ULL);
  assert(test_stats_stub_find(&stub, "arm_est_flops", &val) && val == 30ULL);

  test_stats_stub_unbind();
}

int main(void)
{
  test_map_sp_dp_sum_and_cycles();
  test_map_partial_sp_only();
  test_map_int8_int16_excluded_from_flops();
  printf("test_cpu_counter_papi_map passed\n");
  return 0;
}
