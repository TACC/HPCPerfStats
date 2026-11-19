/* x86 host_cpu_hw publish: maps x86_core_perf lifetimes (Intel and AMD rows) to schema keys. */
#include <stddef.h>

#include "stats.h"
#include "cpu_counter_metrics_x86_publish.h"

/* Shared W=48 keys carry the software lifetime modulo 2^48 (O11), so the consumer's 48-bit wrap
 * handling stays correct; W=64 keys carry the full lifetime. */
#define X86_W48_MASK ((1ULL << 48) - 1ULL)
#define X86_W64_MASK (~0ULL)

struct x86_key_map {
  int event;
  const char *key;
  unsigned long long mask;
  int intel_only; /* Intel fixed-counter name: NOT_PRESENT on AMD even when the event is open */
};

/* One row per key; an event may feed several keys. */
static const struct x86_key_map g_x86_keys[] = {
    {X86_CORE_INSTRUCTIONS, "instr_retired_any", X86_W48_MASK, 1},
    {X86_CORE_INSTRUCTIONS, "instr_retired", X86_W48_MASK, 0},
    {X86_CORE_CYCLES, "cycles_unhalted_core", X86_W48_MASK, 1},
    {X86_CORE_CYCLES, "aperf", X86_W48_MASK, 0},
    {X86_CORE_REF_CYCLES, "cycles_unhalted_ref", X86_W48_MASK, 1},
    {X86_CORE_REF_CYCLES, "mperf", X86_W48_MASK, 1},
    {X86_CORE_L1D_REPLACEMENT, "l1d_replacement", X86_W48_MASK, 0},
    {X86_CORE_L1_HIT, "mem_load_uops_retired_l1_hit", X86_W48_MASK, 0},
    {X86_CORE_L2_HIT, "mem_load_uops_retired_l2_hit", X86_W48_MASK, 0},
    {X86_CORE_L3_HIT, "mem_load_uops_retired_llc_hit", X86_W48_MASK, 0},
    {X86_CORE_FP_SCALAR_DOUBLE, "fp_arith_inst_retired_scalar_double", X86_W48_MASK, 0},
    {X86_CORE_FP_SCALAR_SINGLE, "fp_arith_inst_retired_scalar_single", X86_W48_MASK, 0},
    {X86_CORE_FP_128B_DOUBLE, "fp_arith_inst_retired_128b_packed_double", X86_W48_MASK, 0},
    {X86_CORE_FP_128B_SINGLE, "fp_arith_inst_retired_128b_packed_single", X86_W48_MASK, 0},
    {X86_CORE_FP_256B_DOUBLE, "fp_arith_inst_retired_256b_packed_double", X86_W48_MASK, 0},
    {X86_CORE_FP_256B_SINGLE, "fp_arith_inst_retired_256b_packed_single", X86_W48_MASK, 0},
    {X86_CORE_FP_512B_DOUBLE, "fp_arith_inst_retired_512b_packed_double", X86_W48_MASK, 0},
    {X86_CORE_FP_512B_SINGLE, "fp_arith_inst_retired_512b_packed_single", X86_W48_MASK, 0},
    {X86_CORE_AMD_RET_INSTR, "retired_instructions", X86_W48_MASK, 0},
    {X86_CORE_AMD_RET_BRN, "retired_branch_instr", X86_W48_MASK, 0},
    {X86_CORE_AMD_RET_BRN_MISP, "retired_misp_branch_instr", X86_W48_MASK, 0},
    {X86_CORE_AMD_LS_DISPATCH, "ls_dispatch", X86_W48_MASK, 0},
    {X86_CORE_AMD_FLOPS, "fp_ops_retired", X86_W64_MASK, 0},
};
#define X86_NKEYS (sizeof(g_x86_keys) / sizeof(g_x86_keys[0]))

/* STATS_SENTINEL_OK when event @p e was read this interval, else why not (an event that is not
 * open and carries no code is NOT_PRESENT). */
static enum stats_sentinel event_code(const struct x86_core_counts *c, int e)
{
  if (c->code[e] != STATS_SENTINEL_OK)
    return (enum stats_sentinel)c->code[e];
  return c->have[e] ? STATS_SENTINEL_OK : STATS_SENTINEL_NOT_PRESENT;
}

/* Value when @p code is OK, else the code; NOT_PRESENT is left to the caller's
 * stats_set_missing_all. */
static void publish_value(struct stats *stats, const char *key, enum stats_sentinel code,
                          unsigned long long val)
{
  if (code == STATS_SENTINEL_OK)
    stats_set(stats, key, val);
  else if (code != STATS_SENTINEL_NOT_PRESENT)
    stats_set_missing(stats, key, code);
}

void publish_x86_measured_stats(struct stats *stats, const struct x86_core_counts *c, int is_amd)
{
  enum stats_sentinel pd, sd;
  size_t k;

  if (stats == NULL || c == NULL)
    return;
  for (k = 0; k < X86_NKEYS; k++) {
    const struct x86_key_map *m = &g_x86_keys[k];

    if (is_amd && m->intel_only)
      continue;
    publish_value(stats, m->key, event_code(c, m->event), c->life[m->event] & m->mask);
  }
  /* Zen5 double-precision FLOPs = packed + scalar, only when both measured this interval. */
  pd = event_code(c, X86_CORE_AMD_FLOPS_DP_PD);
  sd = event_code(c, X86_CORE_AMD_FLOPS_DP_SD);
  publish_value(stats, "amd_fp_dp_ops", pd != STATS_SENTINEL_OK ? pd : sd,
                c->life[X86_CORE_AMD_FLOPS_DP_PD] + c->life[X86_CORE_AMD_FLOPS_DP_SD]);
  /* D6 PMU sharing: W=64 ns lifetimes summed over the CPU's open events; no solo key without a
   * known counter count. */
  if (c->have_times) {
    stats_set(stats, "pmu_enabled_time", c->pmu_enabled_ns);
    stats_set(stats, "pmu_running_time", c->pmu_running_ns);
    if (c->have_solo)
      stats_set(stats, "pmu_solo_running_time", c->pmu_solo_ns);
  }
}
