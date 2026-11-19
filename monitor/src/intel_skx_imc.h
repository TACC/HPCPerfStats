#ifndef _INTEL_SKX_IMC_H_
#define _INTEL_SKX_IMC_H_

#include "x86_uncore_perf.h"

/* Per mbox<N> row: CAS counts (64 bytes each); act / pre-miss have no encoding here. */
#define INTEL_SKX_IMC_KEYS                                                                         \
  X(dram_cas_reads, "E,W=48", ""), X(dram_cas_writes, "E,W=48", ""),                               \
      X(dram_act_count, "E,W=48", ""), X(dram_pre_count_miss, "E,W=48", ""),                       \
      X86_UNCORE_PMU_TIME_KEYS

#endif
