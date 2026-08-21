/*! \file likwid_rapl.h
 *  LIKWID RAPL millijoule sampling via PWR* perfmon / powercap (no MSR).
 */

#ifndef _LIKWID_RAPL_H_
#define _LIKWID_RAPL_H_

#include <stdint.h>

int likwid_rapl_is_supported_intel_processor(void);
int likwid_rapl_is_supported_amd_processor(void);
/* OR of Intel and AMD helpers (legacy callers). Prefer vendor-specific helpers. */
int likwid_rapl_is_supported_processor(void);

/* Vendor path for PWR* event strings (not MONITOR_ARCH_*). AMD wins if both match. */
#define LIKWID_RAPL_PATH_NONE 0
#define LIKWID_RAPL_PATH_INTEL 1
#define LIKWID_RAPL_PATH_AMD 2
int likwid_rapl_collect_path(void);

int likwid_rapl_collect_socket_mj(int cpu_id, unsigned int socket_id, unsigned long long *pkg_mj,
                                  unsigned long long *core_mj, unsigned long long *dram_mj,
                                  int *has_pkg, int *has_core, int *has_dram,
                                  unsigned long long *pp1_mj, int *has_pp1);

#endif
