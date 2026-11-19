#ifndef _INTEL_ICX_IMC_H_
#define _INTEL_ICX_IMC_H_

#include "x86_uncore_perf.h"

#define INTEL_ICX_IMC_KEYS                                                                         \
  X(dram_cas_reads, "E,W=48", ""), X(dram_cas_writes, "E,W=48", ""), X86_UNCORE_PMU_TIME_KEYS

#endif
