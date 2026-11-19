#ifndef CPU_COUNTER_METRICS_X86_PUBLISH_H_
#define CPU_COUNTER_METRICS_X86_PUBLISH_H_

#include "x86_core_perf.h"

struct stats;

/* Measured x86_core_perf lifetimes for one CPU. Shared W=48 keys carry the lifetime modulo 2^48
 * (O11); fp_ops_retired, amd_fp_dp_ops (Zen5 packed + scalar double, only when both measured)
 * and the D6 pmu_* times are W=64. An event that did not measure this interval publishes its
 * code. A key whose event is not present on this CPU (no class row, or an ENOENT-class open
 * failure) is left unset: the caller's stats_set_missing_all(NOT_PRESENT) marks it, together
 * with every key x86 never publishes (cpu_util_*, cpu_clock_est_cycles, arm_*, dram_chan*).
 * With @p is_amd set, the Intel fixed-counter names (instr_retired_any, cycles_unhalted_core,
 * cycles_unhalted_ref, mperf) are left unset too: AMD publishes instructions and cycles only as
 * instr_retired and aperf. */
void publish_x86_measured_stats(struct stats *stats, const struct x86_core_counts *c, int is_amd);

#endif
