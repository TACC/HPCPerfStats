/*! \file likwid_arch_map.h
 *  Default LIKWID event sets for Intel vs AMD hosts.
 */

#ifndef _LIKWID_ARCH_MAP_H_
#define _LIKWID_ARCH_MAP_H_

#include "cpuid.h"

const char *likwid_arch_eventset(void);
const char *likwid_arch_eventset_for_processor(processor_t p, int n_pmcs);
/* Grace/Neoverse: CYC+INS only (do not arm SP/DP/SVE INT system-wide). */
const char *likwid_arch_eventset_grace(void);
const char *likwid_arch_eventset_grace_cyc_only(void);

#endif
