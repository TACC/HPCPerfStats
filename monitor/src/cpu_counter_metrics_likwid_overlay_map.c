/* Pure mapping from LIKWID overlay counters onto host_cpu_hw portable keys. */
#include "cpu_counter_metrics_likwid_overlay_map.h"

#include "stats.h"

int likwid_overlay_should_overwrite_cycle_keys(unsigned long long cycles)
{
  return (cycles > 0) ? 1 : 0;
}

void likwid_overlay_map_to_host_cpu_hw(struct stats *stats, const struct likwid_overlay_counters *c)
{
  unsigned long long sp = 0;
  unsigned long long dp = 0;
  unsigned long long cycles = 0;
  unsigned long long instr = 0;
  unsigned long long int8 = 0;
  unsigned long long int16 = 0;

  if (stats == NULL || c == NULL)
    return;

  if (c->have_sp)
    sp = c->sp_ops;
  if (c->have_dp)
    dp = c->dp_ops;
  if (c->have_cycles)
    cycles = c->cycles;
  if (c->have_instr)
    instr = c->instr;
  if (c->have_int8)
    int8 = c->int8_ops;
  if (c->have_int16)
    int16 = c->int16_ops;

  stats_set(stats, "fp_arith_inst_retired_scalar_single", sp);
  stats_set(stats, "fp_arith_inst_retired_scalar_double", dp);
  stats_set(stats, "arm_est_flops", sp + dp);
  stats_set(stats, "arm_int8_ops", int8);
  stats_set(stats, "arm_int16_ops", int16);

  stats_set(stats, "fp_arith_inst_retired_128b_packed_double", 0);
  stats_set(stats, "fp_arith_inst_retired_256b_packed_double", 0);
  stats_set(stats, "fp_arith_inst_retired_512b_packed_double", 0);
  stats_set(stats, "fp_arith_inst_retired_128b_packed_single", 0);
  stats_set(stats, "fp_arith_inst_retired_256b_packed_single", 0);
  stats_set(stats, "fp_arith_inst_retired_512b_packed_single", 0);

  /* Leave DCGM util×freq estimate when LIKWID returns 0 (sparse PERF). */
  if (likwid_overlay_should_overwrite_cycle_keys(cycles)) {
    stats_set(stats, "aperf", cycles);
    stats_set(stats, "mperf", cycles);
    stats_set(stats, "cpu_clock_est_cycles", cycles);
  }
  if (c->have_instr)
    stats_set(stats, "instr_retired", instr);
}
