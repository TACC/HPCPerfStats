#ifndef _ARM_AARCH64_IMC_H_
#define _ARM_AARCH64_IMC_H_

#define ARM_AARCH64_IMC_ST_NAME "arm_aarch64_imc"

/* D6 (Phase 1b): pmu_enabled_time / pmu_running_time are the device's counter time lifetimes in
 * ns, summed over its read and write events (no solo baseline for uncore PMUs). */
#define ARM_AARCH64_IMC_KEYS                                                                       \
  X(dram_cas_reads, "E,W=48", ""), X(dram_cas_writes, "E,W=48", ""),                               \
      X(pmu_enabled_time, "E,W=64,U=ns", ""), X(pmu_running_time, "E,W=64,U=ns", "")

#endif
