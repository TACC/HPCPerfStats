/* x86_core_perf - Intel / AMD core PMU reader for host_cpu_hw (direct perf, scaled).
 *
 * One fd per event per CPU (no groups, never pinned) on the sysfs `cpu` PMU, opened at begin /
 * SIGHUP only and read through perf_read_scaled (enabled/running scaling, kernel multiplexing).
 * Events resolve per class: sysfs events/<name> first (instructions, cpu-cycles, ref-cycles), then a
 * processor_t raw-code table verified on hardware (#51-#74); an event the class has no row for
 * is skipped and its key emits NOT_PRESENT. Same accumulator shape as arm_core_perf: software
 * lifetimes that only grow, so failed reads and re-seeds bridge (p2-x86-sentinel-bridge). */
#ifndef X86_CORE_PERF_H
#define X86_CORE_PERF_H

#include "cpuid.h"
#include "perf_util.h"

enum x86_core_event {
  X86_CORE_INSTRUCTIONS = 0, /* sysfs "instructions" (Intel fixed counter 0) */
  X86_CORE_CYCLES,           /* sysfs "cpu-cycles" */
  X86_CORE_REF_CYCLES,       /* sysfs "ref-cycles" (Intel; absent on Genoa) */
  X86_CORE_L1D_REPLACEMENT,  /* Intel 0x51/0x01 */
  X86_CORE_L1_HIT,           /* Intel 0xd1/0x01 (D18: every Intel class) */
  X86_CORE_L2_HIT,           /* Intel 0xd1/0x02 */
  X86_CORE_L3_HIT,           /* Intel 0xd1/0x04 */
  X86_CORE_FP_SCALAR_DOUBLE, /* Intel 0xc7/0x01 */
  X86_CORE_FP_SCALAR_SINGLE, /* Intel 0xc7/0x02 */
  X86_CORE_FP_128B_DOUBLE,   /* Intel 0xc7/0x04 */
  X86_CORE_FP_128B_SINGLE,   /* Intel 0xc7/0x08 */
  X86_CORE_FP_256B_DOUBLE,   /* Intel 0xc7/0x10 */
  X86_CORE_FP_256B_SINGLE,   /* Intel 0xc7/0x20 */
  X86_CORE_FP_512B_DOUBLE,   /* Intel 0xc7/0x40 */
  X86_CORE_FP_512B_SINGLE,   /* Intel 0xc7/0x80 */
  X86_CORE_AMD_RET_INSTR,    /* AMD 0xc0 */
  X86_CORE_AMD_RET_BRN,      /* AMD 0xc2 */
  X86_CORE_AMD_RET_BRN_MISP, /* AMD 0xc3 */
  X86_CORE_AMD_LS_DISPATCH,  /* AMD 0x29/0x07 */
  X86_CORE_AMD_FLOPS,        /* AMD 0x03: umask 0xff on Zen3/Zen4, 0x0f on Zen5 (kernel pairs) */
  X86_CORE_AMD_FLOPS_DP_PD,  /* Zen5 0x03/0xaf packed double */
  X86_CORE_AMD_FLOPS_DP_SD,  /* Zen5 0x03/0x8f scalar double */
  X86_CORE_NEVENTS
};

/** Scaled lifetime per event for one CPU (same shape as arm_core_counts). have[e]: event e is
 * open on that CPU; code[e]: enum stats_sentinel, OK when life[e] is this interval's reading.
 * D6 sharing: enabled / running / solo-expected ns summed over the CPU's open events; solo uses
 * the class's general-counter count (have_solo 0 when unknown). */
struct x86_core_counts {
  unsigned long long life[X86_CORE_NEVENTS];
  unsigned char have[X86_CORE_NEVENTS];
  unsigned char code[X86_CORE_NEVENTS];
  unsigned long long pmu_enabled_ns;
  unsigned long long pmu_running_ns;
  unsigned long long pmu_solo_ns;
  unsigned char have_times;
  unsigned char have_solo;
};

/** Injectable paths and open function; NULL fields (or a NULL cfg) use the real system. */
struct x86_core_perf_cfg {
  const char *devices_dir; /* /sys/bus/event_source/devices */
  perf_open_fn open_fn;    /* perf_open_cpu */
};

/** Event name used in logs ("cycles", "l1_hit", ...); NULL out of range. */
const char *x86_core_perf_event_name(int ev);

/**
 * Resolves the events of class @p proc on the `cpu` PMU under cfg->devices_dir: attrs[e] / ok[e]
 * per event (ok 0 = the class has no row and sysfs names none).
 * @return number of events resolved, or -1 when the `cpu` PMU is missing.
 */
int x86_core_perf_resolve(processor_t proc, const struct x86_core_perf_cfg *cfg,
                          struct perf_event_attr attrs[X86_CORE_NEVENTS], int ok[X86_CORE_NEVENTS]);

/**
 * Opens every resolved event on CPUs 0..ncpus-1 (startup / SIGHUP only). A failed open logs once
 * per event and leaves that event's code at perf_open_sentinel(errno).
 * @return 0 when at least one counter is open, -1 otherwise (state cleaned up).
 */
int x86_core_perf_begin(int ncpus, processor_t proc, const struct x86_core_perf_cfg *cfg);

/** 1 after a successful begin. */
int x86_core_perf_ready(void);

/** Reads the CPU's open counters (scaled) and returns their lifetimes in @p out. */
void x86_core_perf_collect_cpu(int cpu, struct x86_core_counts *out);

/** Closes every fd and frees state (safe to call repeatedly). */
void x86_core_perf_cleanup(void);

#endif
