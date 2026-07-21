#ifndef _AMD_CPUID_MATCH_H_
#define _AMD_CPUID_MATCH_H_

#include "cpuid.h"

/* Map AuthenticAMD CPUID sig ("%02x_%x" display fam/model) to EPYC enum. */
processor_t amd_cpuid_sig_to_processor(const char *vendor, const char *sig);

#endif
