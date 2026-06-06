/*! \file likwid_rapl.c
 *  LIKWID power_read helpers for intel_x86_rapl / amd_x86_rapl collectors.
 */

#include <stddef.h>
#include <stdlib.h>
#include <stdio.h>
#include <stdint.h>
#include "stats.h"
#include "trace.h"
#include "cpuid.h"
#include "likwid_rapl.h"

#ifdef HAVE_LIKWID
#include <likwid.h>
#endif

#define MSR_PKG_ENERGY_STATUS_INTEL 0x611u
#define MSR_PP0_ENERGY_STATUS_INTEL 0x639u
#define MSR_PP1_ENERGY_STATUS_INTEL 0x641u
#define MSR_DRAM_ENERGY_STATUS_INTEL 0x619u

#define MSR_AMD_CORE_ENERGY_STATUS 0xC001029Au
#define MSR_AMD_PKG_ENERGY_STATUS 0xC001029Bu

unsigned long long
likwid_rapl_raw_to_mj(uint32_t raw, double joules_per_lsb)
{
  return (unsigned long long)((double)raw * joules_per_lsb * 1000.0 + 0.5);
}

int
likwid_rapl_is_supported_processor(void)
{
  switch (processor) {
  case AMD_17H:
  case AMD_19H:
  case SANDYBRIDGE:
  case IVYBRIDGE:
  case HASWELL:
  case BROADWELL:
  case SKYLAKE:
  case CASCADE_LAKE:
  case ICELAKE_SERVER:
  case SAPPHIRE_RAPIDS:
    return 1;
  default:
    return 0;
  }
}

#ifdef HAVE_LIKWID

static void
try_read_mj(int cpu_id, uint32_t msr, int domain, unsigned long long *mj,
            int *has)
{
  uint32_t raw = 0;
  double eu;
  *has = 0;
  *mj = 0;
  if (power_read(cpu_id, (uint64_t)msr, &raw) != 0)
    return;
  eu = power_getEnergyUnit(domain);
  if (eu <= 0.0)
    return;
  *mj = likwid_rapl_raw_to_mj(raw, eu);
  *has = 1;
}

static void
collect_intel_socket_mj(int cpu_id, unsigned long long *pkg_mj,
                          unsigned long long *core_mj,
                          unsigned long long *dram_mj, int *has_pkg,
                          int *has_core, int *has_dram,
                          unsigned long long *pp1_mj, int *has_pp1)
{
  *has_pkg = *has_core = *has_dram = 0;
  try_read_mj(cpu_id, MSR_PKG_ENERGY_STATUS_INTEL, (int)PKG, pkg_mj, has_pkg);
  try_read_mj(cpu_id, MSR_PP0_ENERGY_STATUS_INTEL, (int)PP0, core_mj,
              has_core);
  try_read_mj(cpu_id, MSR_DRAM_ENERGY_STATUS_INTEL, (int)DRAM, dram_mj,
              has_dram);
  if (pp1_mj != NULL && has_pp1 != NULL) {
    *has_pp1 = 0;
    *pp1_mj = 0;
    try_read_mj(cpu_id, MSR_PP1_ENERGY_STATUS_INTEL, (int)PP1, pp1_mj,
                has_pp1);
  }
}

static void
collect_amd_socket_mj(int cpu_id, unsigned long long *pkg_mj,
                        unsigned long long *core_mj,
                        unsigned long long *dram_mj, int *has_pkg,
                        int *has_core, int *has_dram)
{
  *has_pkg = *has_core = *has_dram = 0;
  *dram_mj = 0;
  try_read_mj(cpu_id, MSR_AMD_CORE_ENERGY_STATUS, 0, core_mj, has_core);
  try_read_mj(cpu_id, MSR_AMD_PKG_ENERGY_STATUS, 1, pkg_mj, has_pkg);
}

#endif /* HAVE_LIKWID */

int
likwid_rapl_collect_socket_mj(int cpu_id, unsigned int socket_id,
                              unsigned long long *pkg_mj,
                              unsigned long long *core_mj,
                              unsigned long long *dram_mj, int *has_pkg,
                              int *has_core, int *has_dram,
                              unsigned long long *pp1_mj, int *has_pp1)
{
#ifdef HAVE_LIKWID
  PowerInfo_t pi;

  (void)socket_id;
  if (pkg_mj == NULL || core_mj == NULL || dram_mj == NULL || has_pkg == NULL ||
      has_core == NULL || has_dram == NULL)
    return -1;
  *pkg_mj = *core_mj = *dram_mj = 0;
  *has_pkg = *has_core = *has_dram = 0;
  if (pp1_mj != NULL && has_pp1 != NULL) {
    *pp1_mj = 0;
    *has_pp1 = 0;
  }
  if (cpu_id < 0 || cpu_id >= nr_cpus) {
    TRACE("likwid_rapl: invalid cpu_id %d\n", cpu_id);
    return -1;
  }
  pi = get_powerInfo();
  if (pi == NULL || !pi->hasRAPL) {
    TRACE("likwid_rapl: RAPL not initialized (cpu_id=%d)\n", cpu_id);
    return -1;
  }
#if defined(MONITOR_ARCH_INTEL)
  collect_intel_socket_mj(cpu_id, pkg_mj, core_mj, dram_mj, has_pkg, has_core,
                          has_dram, pp1_mj, has_pp1);
#elif defined(MONITOR_ARCH_AMD)
  collect_amd_socket_mj(cpu_id, pkg_mj, core_mj, dram_mj, has_pkg, has_core,
                        has_dram);
#else
  (void)cpu_id;
  return -1;
#endif
  return (*has_pkg || *has_core || *has_dram
          || (has_pp1 != NULL && *has_pp1)) ? 0 : -1;
#else
  (void)cpu_id;
  (void)socket_id;
  (void)pkg_mj;
  (void)core_mj;
  (void)dram_mj;
  (void)has_pkg;
  (void)has_core;
  (void)has_dram;
  (void)pp1_mj;
  (void)has_pp1;
  return -1;
#endif
}
