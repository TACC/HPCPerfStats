#ifndef _INTEL_SPR_IMC_H_
#define _INTEL_SPR_IMC_H_

#include "x86_uncore_perf.h"

/* mbox<N> rows carry the dram_cas_* keys, hbm<N> rows the hbm_cas_* keys; each emits
 * NOT_PRESENT for the other pair. DDR CAS is 64 bytes, HBM CAS 32 bytes. */
#define INTEL_SPR_IMC_KEYS                                                                         \
  X(dram_cas_reads, "E,W=48", ""), X(dram_cas_writes, "E,W=48", ""),                               \
      X(hbm_cas_reads, "E,W=48", ""), X(hbm_cas_writes, "E,W=48", ""), X86_UNCORE_PMU_TIME_KEYS

#endif
