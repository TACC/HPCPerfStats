#ifndef _INTEL_CPUID_MATCH_H_
#define _INTEL_CPUID_MATCH_H_

#include "cpuid.h"

/*
 * Map CPUID vendor + family/model signature + stepping to processor_t.
 * Stepping follows LIKWID: model 0x55 stepping < 5 → SKYLAKE_X, else CASCADE_LAKE.
 */
processor_t intel_cpuid_sig_to_processor(const char *vendor, const char *sig, int stepping);

#endif
