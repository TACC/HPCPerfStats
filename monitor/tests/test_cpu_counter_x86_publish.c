/* x86 host_cpu_hw publish (publish_x86_measured_stats): event -> key mapping for the Intel and
 * AMD rows (Intel fixed-counter names never on AMD), W=48 keys modulo 2^48 (O11), W=64 FLOP and
 * D6 sharing keys at full width, each unmeasured event's code instead of a value, and
 * NOT_PRESENT keys left to the caller. */
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "cpu_counter_metrics_x86_publish.h"
#include "stats.h"
#include "test_stats_missing_stub.h"
#include "test_stats_stub.h"

#define W48 (1ULL << 48)

struct event_key {
  int event;
  const char *key;
};

static const struct event_key g_w48_keys[] = {
    {X86_CORE_INSTRUCTIONS, "instr_retired_any"},
    {X86_CORE_INSTRUCTIONS, "instr_retired"},
    {X86_CORE_CYCLES, "cycles_unhalted_core"},
    {X86_CORE_CYCLES, "aperf"},
    {X86_CORE_REF_CYCLES, "cycles_unhalted_ref"},
    {X86_CORE_REF_CYCLES, "mperf"},
    {X86_CORE_L1D_REPLACEMENT, "l1d_replacement"},
    {X86_CORE_L1_HIT, "mem_load_uops_retired_l1_hit"},
    {X86_CORE_L2_HIT, "mem_load_uops_retired_l2_hit"},
    {X86_CORE_L3_HIT, "mem_load_uops_retired_llc_hit"},
    {X86_CORE_FP_SCALAR_DOUBLE, "fp_arith_inst_retired_scalar_double"},
    {X86_CORE_FP_SCALAR_SINGLE, "fp_arith_inst_retired_scalar_single"},
    {X86_CORE_FP_128B_DOUBLE, "fp_arith_inst_retired_128b_packed_double"},
    {X86_CORE_FP_128B_SINGLE, "fp_arith_inst_retired_128b_packed_single"},
    {X86_CORE_FP_256B_DOUBLE, "fp_arith_inst_retired_256b_packed_double"},
    {X86_CORE_FP_256B_SINGLE, "fp_arith_inst_retired_256b_packed_single"},
    {X86_CORE_FP_512B_DOUBLE, "fp_arith_inst_retired_512b_packed_double"},
    {X86_CORE_FP_512B_SINGLE, "fp_arith_inst_retired_512b_packed_single"},
    {X86_CORE_AMD_RET_INSTR, "retired_instructions"},
    {X86_CORE_AMD_RET_BRN, "retired_branch_instr"},
    {X86_CORE_AMD_RET_BRN_MISP, "retired_misp_branch_instr"},
    {X86_CORE_AMD_LS_DISPATCH, "ls_dispatch"},
};
#define N_W48 (sizeof(g_w48_keys) / sizeof(g_w48_keys[0]))

/* Intel-only events: never open on an AMD row. */
static const int g_intel_only[] = {
    X86_CORE_REF_CYCLES,       X86_CORE_L1D_REPLACEMENT, X86_CORE_L1_HIT,
    X86_CORE_L2_HIT,           X86_CORE_L3_HIT,          X86_CORE_FP_SCALAR_DOUBLE,
    X86_CORE_FP_SCALAR_SINGLE, X86_CORE_FP_128B_DOUBLE,  X86_CORE_FP_128B_SINGLE,
    X86_CORE_FP_256B_DOUBLE,   X86_CORE_FP_256B_SINGLE,  X86_CORE_FP_512B_DOUBLE,
    X86_CORE_FP_512B_SINGLE,
};
#define N_INTEL_ONLY (sizeof(g_intel_only) / sizeof(g_intel_only[0]))

/* Intel fixed-counter key names: never published on AMD, even with the generic event open. */
static const char *const g_intel_fixed_keys[] = {"instr_retired_any", "cycles_unhalted_core",
                                                 "cycles_unhalted_ref", "mperf"};
#define N_INTEL_FIXED (sizeof(g_intel_fixed_keys) / sizeof(g_intel_fixed_keys[0]))

static struct stats g_dummy_stats;

void stats_set(struct stats *stats, const char *key, unsigned long long val)
{
  test_stats_set_stub(stats, key, val);
}

static void publish(const struct x86_core_counts *c, int is_amd, struct test_stats_stub *stub)
{
  test_stats_stub_reset(stub);
  test_stats_stub_bind(stub);
  test_stats_missing_reset();
  publish_x86_measured_stats(&g_dummy_stats, c, is_amd);
  test_stats_stub_unbind();
}

static int has_key(const struct test_stats_stub *stub, const char *key)
{
  unsigned long long val;

  return test_stats_stub_find(stub, key, &val);
}

static unsigned long long value(const struct test_stats_stub *stub, const char *key)
{
  unsigned long long val = 0;

  assert(test_stats_stub_find(stub, key, &val));
  return val;
}

/* Every event open; event e's lifetime is 2^48 + 100 + e so masking and mapping both show. */
static void all_open(struct x86_core_counts *c)
{
  int e;

  memset(c, 0, sizeof(*c));
  for (e = 0; e < X86_CORE_NEVENTS; e++) {
    c->have[e] = 1;
    c->life[e] = W48 + 100ULL + (unsigned long long)e;
  }
}

static void test_mapping_and_widths(void)
{
  struct test_stats_stub stub;
  struct x86_core_counts c;
  size_t k;

  all_open(&c);
  publish(&c, 0, &stub);
  /* W=48 keys carry the lifetime modulo 2^48. */
  for (k = 0; k < N_W48; k++)
    assert(value(&stub, g_w48_keys[k].key) == 100ULL + (unsigned long long)g_w48_keys[k].event);
  /* W=64 FLOP keys carry the full lifetime; amd_fp_dp_ops = packed + scalar double. */
  assert(value(&stub, "fp_ops_retired") == W48 + 100ULL + X86_CORE_AMD_FLOPS);
  assert(value(&stub, "amd_fp_dp_ops") ==
         2ULL * W48 + 200ULL + X86_CORE_AMD_FLOPS_DP_PD + X86_CORE_AMD_FLOPS_DP_SD);
  assert(test_stats_missing_count() == 0);
  /* x86 never publishes util, Grace or DRAM channel keys: the caller marks them. */
  assert(!has_key(&stub, "cpu_util_total_accum_us"));
  assert(!has_key(&stub, "cpu_clock_est_cycles"));
  assert(!has_key(&stub, "arm_est_flops"));
  assert(!has_key(&stub, "dram_chan0_bytes"));

  /* Below 2^48 the W=48 value is the lifetime itself. */
  c.life[X86_CORE_INSTRUCTIONS] = 12345ULL;
  publish(&c, 0, &stub);
  assert(value(&stub, "instr_retired") == 12345ULL);
  assert(value(&stub, "instr_retired_any") == 12345ULL);
}

static void test_unmeasured_event_publishes_its_code(void)
{
  struct test_stats_stub stub;
  struct x86_core_counts c;

  all_open(&c);
  c.code[X86_CORE_INSTRUCTIONS] = STATS_SENTINEL_NOT_SCHEDULED;
  c.code[X86_CORE_L2_HIT] = STATS_SENTINEL_READ_FAILED;
  c.code[X86_CORE_FP_512B_DOUBLE] = STATS_SENTINEL_COUNTER_RESET;
  c.code[X86_CORE_AMD_FLOPS] = STATS_SENTINEL_PMU_BUSY;
  /* Open failure: the event is not open but keeps its errno code. */
  c.have[X86_CORE_AMD_LS_DISPATCH] = 0;
  c.code[X86_CORE_AMD_LS_DISPATCH] = STATS_SENTINEL_PERMISSION_DENIED;
  publish(&c, 0, &stub);
  assert(!has_key(&stub, "instr_retired"));
  assert(!has_key(&stub, "instr_retired_any"));
  assert(test_stats_missing_code("instr_retired") == STATS_SENTINEL_NOT_SCHEDULED);
  assert(test_stats_missing_code("instr_retired_any") == STATS_SENTINEL_NOT_SCHEDULED);
  assert(test_stats_missing_code("mem_load_uops_retired_l2_hit") == STATS_SENTINEL_READ_FAILED);
  assert(test_stats_missing_code("fp_arith_inst_retired_512b_packed_double") ==
         STATS_SENTINEL_COUNTER_RESET);
  assert(test_stats_missing_code("fp_ops_retired") == STATS_SENTINEL_PMU_BUSY);
  assert(test_stats_missing_code("ls_dispatch") == STATS_SENTINEL_PERMISSION_DENIED);
  assert(!has_key(&stub, "ls_dispatch"));
  /* Other events of the same CPU still publish. */
  assert(value(&stub, "aperf") == 100ULL + X86_CORE_CYCLES);
  assert(test_stats_missing_count() == 6);

  /* A stale lifetime of a closed event never leaks out. */
  all_open(&c);
  c.have[X86_CORE_L1D_REPLACEMENT] = 0;
  publish(&c, 0, &stub);
  assert(!has_key(&stub, "l1d_replacement"));
  assert(test_stats_missing_code("l1d_replacement") == 0);
}

static void test_dp_sum_needs_both_events(void)
{
  struct test_stats_stub stub;
  struct x86_core_counts c;

  all_open(&c);
  c.code[X86_CORE_AMD_FLOPS_DP_SD] = STATS_SENTINEL_NOT_SCHEDULED;
  publish(&c, 0, &stub);
  assert(!has_key(&stub, "amd_fp_dp_ops"));
  assert(test_stats_missing_code("amd_fp_dp_ops") == STATS_SENTINEL_NOT_SCHEDULED);

  /* Both unmeasured: the packed event's code comes first. */
  c.code[X86_CORE_AMD_FLOPS_DP_PD] = STATS_SENTINEL_READ_FAILED;
  publish(&c, 0, &stub);
  assert(test_stats_missing_code("amd_fp_dp_ops") == STATS_SENTINEL_READ_FAILED);

  /* Packed open, scalar not present (no row): no partial sum, left to the caller. */
  all_open(&c);
  c.have[X86_CORE_AMD_FLOPS_DP_SD] = 0;
  publish(&c, 0, &stub);
  assert(!has_key(&stub, "amd_fp_dp_ops"));
  assert(test_stats_missing_code("amd_fp_dp_ops") == 0);
}

static void test_amd_row_leaves_intel_keys_to_caller(void)
{
  struct test_stats_stub stub;
  struct x86_core_counts c;
  size_t k;

  /* Zen4 shape: generic instructions / cycles, the four AMD core events and total FLOPs. */
  all_open(&c);
  for (k = 0; k < N_INTEL_ONLY; k++)
    c.have[g_intel_only[k]] = 0;
  c.have[X86_CORE_AMD_FLOPS_DP_PD] = 0;
  c.have[X86_CORE_AMD_FLOPS_DP_SD] = 0;
  publish(&c, 1, &stub);
  for (k = 0; k < N_W48; k++) {
    size_t j;
    int intel_only = 0;

    for (j = 0; j < N_INTEL_ONLY; j++)
      intel_only |= g_w48_keys[k].event == g_intel_only[j];
    for (j = 0; j < N_INTEL_FIXED; j++)
      intel_only |= strcmp(g_w48_keys[k].key, g_intel_fixed_keys[j]) == 0;
    assert(has_key(&stub, g_w48_keys[k].key) == !intel_only);
  }
  assert(value(&stub, "retired_instructions") == 100ULL + X86_CORE_AMD_RET_INSTR);
  assert(value(&stub, "instr_retired") == 100ULL + X86_CORE_INSTRUCTIONS);
  assert(value(&stub, "aperf") == 100ULL + X86_CORE_CYCLES);
  assert(value(&stub, "fp_ops_retired") == W48 + 100ULL + X86_CORE_AMD_FLOPS);
  assert(!has_key(&stub, "mperf"));
  assert(!has_key(&stub, "amd_fp_dp_ops"));
  /* Nothing marked here: the caller's stats_set_missing_all(NOT_PRESENT) covers them. */
  assert(test_stats_missing_count() == 0);
}

static void test_fixed_counter_names_by_vendor(void)
{
  struct test_stats_stub stub;
  struct x86_core_counts c;
  size_t k;

  /* Same counts, generic instructions / cycles / ref-cycles all open. */
  all_open(&c);
  publish(&c, 1, &stub);
  assert(value(&stub, "instr_retired") == 100ULL + X86_CORE_INSTRUCTIONS);
  assert(value(&stub, "aperf") == 100ULL + X86_CORE_CYCLES);
  for (k = 0; k < N_INTEL_FIXED; k++)
    assert(!has_key(&stub, g_intel_fixed_keys[k]));
  assert(test_stats_missing_count() == 0);

  /* An unmeasured instructions event on AMD still marks only instr_retired. */
  c.code[X86_CORE_INSTRUCTIONS] = STATS_SENTINEL_NOT_SCHEDULED;
  publish(&c, 1, &stub);
  assert(test_stats_missing_code("instr_retired") == STATS_SENTINEL_NOT_SCHEDULED);
  assert(test_stats_missing_code("instr_retired_any") == 0);
  c.code[X86_CORE_INSTRUCTIONS] = STATS_SENTINEL_OK;

  publish(&c, 0, &stub);
  assert(value(&stub, "instr_retired_any") == 100ULL + X86_CORE_INSTRUCTIONS);
  assert(value(&stub, "instr_retired") == 100ULL + X86_CORE_INSTRUCTIONS);
  assert(value(&stub, "cycles_unhalted_core") == 100ULL + X86_CORE_CYCLES);
  assert(value(&stub, "aperf") == 100ULL + X86_CORE_CYCLES);
  assert(value(&stub, "cycles_unhalted_ref") == 100ULL + X86_CORE_REF_CYCLES);
  assert(value(&stub, "mperf") == 100ULL + X86_CORE_REF_CYCLES);
}

static void test_nothing_open_sets_nothing(void)
{
  struct test_stats_stub stub;
  struct x86_core_counts c;

  memset(&c, 0, sizeof(c));
  c.life[X86_CORE_CYCLES] = 999ULL;
  publish(&c, 0, &stub);
  assert(stub.n == 0);
  assert(test_stats_missing_count() == 0);
}

static void test_sharing_keys(void)
{
  struct test_stats_stub stub;
  struct x86_core_counts c;

  memset(&c, 0, sizeof(c));
  c.have_times = 1;
  c.have_solo = 1;
  c.pmu_enabled_ns = W48 + 9000ULL;
  c.pmu_running_ns = W48 + 2500ULL;
  c.pmu_solo_ns = 7000ULL;
  publish(&c, 0, &stub);
  assert(value(&stub, "pmu_enabled_time") == W48 + 9000ULL);
  assert(value(&stub, "pmu_running_time") == W48 + 2500ULL);
  assert(value(&stub, "pmu_solo_running_time") == 7000ULL);

  /* Unknown counter count: no guessed solo baseline. */
  c.have_solo = 0;
  publish(&c, 0, &stub);
  assert(has_key(&stub, "pmu_running_time"));
  assert(!has_key(&stub, "pmu_solo_running_time"));

  /* No open event: no sharing keys, NOT_PRESENT via the caller. */
  c.have_times = 0;
  c.have_solo = 1;
  publish(&c, 0, &stub);
  assert(!has_key(&stub, "pmu_enabled_time"));
  assert(!has_key(&stub, "pmu_running_time"));
  assert(!has_key(&stub, "pmu_solo_running_time"));
  assert(test_stats_missing_count() == 0);
}

static void test_null_inputs_ignored(void)
{
  struct test_stats_stub stub;
  struct x86_core_counts c;

  all_open(&c);
  test_stats_stub_reset(&stub);
  test_stats_stub_bind(&stub);
  test_stats_missing_reset();
  publish_x86_measured_stats(NULL, &c, 0);
  publish_x86_measured_stats(&g_dummy_stats, NULL, 1);
  test_stats_stub_unbind();
  assert(stub.n == 0);
  assert(test_stats_missing_count() == 0);
}

int main(void)
{
  test_mapping_and_widths();
  test_unmeasured_event_publishes_its_code();
  test_dp_sum_needs_both_events();
  test_amd_row_leaves_intel_keys_to_caller();
  test_fixed_counter_names_by_vendor();
  test_nothing_open_sets_nothing();
  test_sharing_keys();
  test_null_inputs_ignored();
  printf("test_cpu_counter_x86_publish passed\n");
  return 0;
}
