/* arm_core_perf — Grace / aarch64 core PMU reader for host_cpu_hw (direct perf, scaled).
 *
 * Nine events per CPU, each its own fd (no groups) on the armv8_pmuv3 PMU type read from sysfs.
 * Resolution per event (O12): sysfs events/<name> first, then a MIDR-keyed raw-code table (one
 * row today: Arm Neoverse V2), else skipped with one journal line naming the MIDR. Reads use
 * arm_perf_read_scaled (enabled/running scaling; nvidia-powerd shares the PMU). */
#ifndef ARM_CORE_PERF_H
#define ARM_CORE_PERF_H

#include "arm_perf_util.h"

enum arm_core_event {
  ARM_CORE_CYC = 0,
  ARM_CORE_INS,
  ARM_CORE_FP_DP,
  ARM_CORE_FP_SP,
  ARM_CORE_FP_HP,
  ARM_CORE_FP_FIXED,
  ARM_CORE_FP_SCALE,
  ARM_CORE_SVE_INT8,
  ARM_CORE_SVE_INT16,
  ARM_CORE_NEVENTS
};

/** Scaled lifetime per event for one CPU; have[e] is 1 when event e is open on that CPU. */
struct arm_core_counts {
  unsigned long long life[ARM_CORE_NEVENTS];
  unsigned char have[ARM_CORE_NEVENTS];
};

/** Injectable paths and open function; NULL fields (or a NULL cfg) use the real system. */
struct arm_core_perf_cfg {
  const char *devices_dir;  /* /sys/bus/event_source/devices */
  const char *midr_path;    /* /sys/devices/system/cpu/cpu0/regs/identification/midr_el1 */
  const char *cpuinfo_path; /* /proc/cpuinfo ("CPU implementer" / "CPU part" fallback) */
  arm_perf_open_fn open_fn; /* arm_perf_open_cpu */
};

/** sysfs event name of @p ev (e.g. "fp_dp_spec"); NULL when out of range. */
const char *arm_core_perf_event_name(int ev);

/**
 * MIDR of CPU 0 from @p midr_path (e.g. "0x00000000410fd4f0"), else rebuilt from
 * @p cpuinfo_path "CPU implementer" / "CPU part" (implementer << 24 | part << 4).
 * @return 0 and *midr on success, -1 when neither source parses.
 */
int arm_core_perf_read_midr(const char *midr_path, const char *cpuinfo_path,
                            unsigned long long *midr);

/**
 * Resolves the nine events: attrs[e] / ok[e] per event. *midr_out is the MIDR used for the
 * table (0 when unreadable).
 * @return number of events resolved, or -1 when no armv8_pmuv3 PMU exists under devices_dir.
 */
int arm_core_perf_resolve(const struct arm_core_perf_cfg *cfg,
                          struct perf_event_attr attrs[ARM_CORE_NEVENTS], int ok[ARM_CORE_NEVENTS],
                          unsigned long long *midr_out);

/**
 * Opens every resolved event on CPUs 0..ncpus-1 (startup / SIGHUP only). A failed open logs
 * once per event (not per CPU) and leaves that key 0.
 * @return 0 when at least one counter is open, -1 otherwise (state cleaned up).
 */
int arm_core_perf_begin(int ncpus, const struct arm_core_perf_cfg *cfg);

/** 1 after a successful begin. */
int arm_core_perf_ready(void);

/** Reads the CPU's open counters (scaled) and returns their lifetimes in @p out. */
void arm_core_perf_collect_cpu(int cpu, struct arm_core_counts *out);

/** Closes every fd and frees state (safe to call repeatedly). */
void arm_core_perf_cleanup(void);

#endif
