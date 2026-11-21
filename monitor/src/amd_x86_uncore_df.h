#ifndef _AMD_X86_UNCORE_DF_H_
#define _AMD_X86_UNCORE_DF_H_

#include "x86_uncore_perf.h"

/* amd_x86_uncore_df_{rome,milan,genoa,turin}: one `chan<N>` row per DRAM channel (8 on Rome /
 * Milan, 12 on Genoa / Turin), counting 64-byte DRAM data beats over the sockets the amd_df PMU
 * counts (socket 0 only on el8 kernels; begin logs that). Zen2 / Zen3 count read + write in one
 * event (dram_data_beats); Zen4 / Zen5 split dram_read_beats / dram_write_beats. */
#define AMD_X86_UNCORE_DF_KEYS                                                                     \
  X(dram_data_beats, "E,W=48", ""), X(dram_read_beats, "E,W=48", ""),                              \
      X(dram_write_beats, "E,W=48", ""), X86_UNCORE_PMU_TIME_KEYS

#endif
