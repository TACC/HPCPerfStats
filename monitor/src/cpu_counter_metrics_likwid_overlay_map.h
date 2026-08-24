#ifndef CPU_COUNTER_METRICS_LIKWID_OVERLAY_MAP_H_
#define CPU_COUNTER_METRICS_LIKWID_OVERLAY_MAP_H_

#include "stats.h"

struct likwid_overlay_counters {
  unsigned long long cycles;
  unsigned long long instr;
  unsigned long long sp_ops;
  unsigned long long dp_ops;
  unsigned long long int8_ops;
  unsigned long long int16_ops;
  int have_cycles;
  int have_instr;
  int have_sp;
  int have_dp;
  int have_int8;
  int have_int16;
};

int likwid_overlay_should_overwrite_cycle_keys(unsigned long long cycles);

/*
 * Advance a schema-E lifetime total from a raw LIKWID reading.
 * raw == 0 leaves *accum unchanged. If raw drops below *prev_raw (or
 * *as_interval is already set), treat readings as interval deltas (add) and
 * sticky-set *as_interval. Otherwise treat raw as an absolute cumulative.
 * as_interval may be NULL (no sticky; only the drop itself uses add).
 */
unsigned long long likwid_overlay_lifetime_advance(unsigned long long *accum,
                                                   unsigned long long *prev_raw, int *as_interval,
                                                   unsigned long long raw);

void likwid_overlay_map_to_host_cpu_hw(struct stats *stats,
                                       const struct likwid_overlay_counters *c);

#endif
