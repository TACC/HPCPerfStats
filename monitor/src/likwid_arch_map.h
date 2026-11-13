/*! \file likwid_arch_map.h
 *  Default LIKWID event sets for Intel vs AMD hosts.
 */

#ifndef _LIKWID_ARCH_MAP_H_
#define _LIKWID_ARCH_MAP_H_

#include "cpuid.h"

const char *likwid_arch_eventset(void);
const char *likwid_arch_eventset_for_processor(processor_t p, int n_pmcs);
/* Grace / aarch64 does not use LIKWID: its core and SCF counters are read by direct perf
 * (arm_core_perf.c, arm_imc_scf.c). */

#endif
