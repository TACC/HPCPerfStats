#ifndef _AMD_X86_UNCORE_DF_H_
#define _AMD_X86_UNCORE_DF_H_

#include "x86_uncore_perf.h"

/* One `df` row for amd_x86_uncore_df_{rome,milan,genoa,turin}: DRAM data beats (64 bytes each)
 * of channels 0-3 summed over the sockets the amd_df PMU counts (socket 0 only on el8 kernels;
 * begin logs that). The keys keep their historical _bytes names. */
#define AMD_X86_UNCORE_DF_KEYS                                                                     \
  X(dram_chan0_bytes, "E,W=48", ""), X(dram_chan1_bytes, "E,W=48", ""),                            \
      X(dram_chan2_bytes, "E,W=48", ""), X(dram_chan3_bytes, "E,W=48", ""),                        \
      X86_UNCORE_PMU_TIME_KEYS

#endif
