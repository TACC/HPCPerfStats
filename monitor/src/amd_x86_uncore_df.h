#ifndef _AMD_X86_UNCORE_DF_H_
#define _AMD_X86_UNCORE_DF_H_

/* Portable DF channel keys for amd_x86_uncore_df_{rome,milan,genoa,turin}. */
#define AMD_X86_UNCORE_DF_KEYS                                                                     \
  X(dram_chan0_bytes, "E,W=48", ""), X(dram_chan1_bytes, "E,W=48", ""),                            \
      X(dram_chan2_bytes, "E,W=48", ""), X(dram_chan3_bytes, "E,W=48", "")

#endif
