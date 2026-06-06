#ifndef _INTEL_CPUID_MATCH_H_
#define _INTEL_CPUID_MATCH_H_

#include "cpuid.h"

/* Map CPUID vendor + family/model signature string to processor_t. */
processor_t intel_cpuid_sig_to_processor(const char *vendor, const char *sig);

#endif
