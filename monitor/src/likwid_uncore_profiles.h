#ifndef _LIKWID_UNCORE_PROFILES_H_
#define _LIKWID_UNCORE_PROFILES_H_

#include "cpuid.h"

typedef enum {
  LIKWID_UNCORE_PROFILE_IMC_SKX = 0,
  LIKWID_UNCORE_PROFILE_IMC_ICX,
  LIKWID_UNCORE_PROFILE_IMC_SPR,
  LIKWID_UNCORE_PROFILE_CHA_SKX,
  LIKWID_UNCORE_PROFILE_COUNT
} likwid_uncore_profile_t;

int likwid_uncore_profile_matches_processor(likwid_uncore_profile_t profile,
                                            processor_t p);
const char *likwid_uncore_profile_eventset(likwid_uncore_profile_t profile);
int likwid_uncore_profile_map_counter(likwid_uncore_profile_t profile,
                                      const char *counter_name,
                                      char *dev_out, size_t dev_len,
                                      const char **key_out);

#endif
