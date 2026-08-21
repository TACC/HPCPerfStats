/*! \file likwid_rapl.c
 *  LIKWID RAPL helpers: PWR* perfmon only (no MSR power_read).
 */

#include <stddef.h>
#include <stdlib.h>
#include <stdio.h>
#include <stdint.h>
#include "monitor_log.h"
#include "stats.h"
#include "trace.h"
#include "cpuid.h"
#include "amd_processor.h"
#include "likwid_rapl.h"
#include "likwid_rapl_pwr.h"

int likwid_rapl_is_supported_intel_processor(void)
{
  switch (processor) {
  case SKYLAKE:
  case SKYLAKE_X:
  case CASCADE_LAKE:
  case ICELAKE_SERVER:
  case SAPPHIRE_RAPIDS:
  case EMERALD_RAPIDS:
  case GRANITE_RAPIDS:
  case SIERRA_FOREST:
    return 1;
  default:
    return 0;
  }
}

int likwid_rapl_is_supported_amd_processor(void)
{
  return amd_processor_is_epyc(processor);
}

int likwid_rapl_is_supported_processor(void)
{
  return likwid_rapl_is_supported_intel_processor() || likwid_rapl_is_supported_amd_processor();
}

int likwid_rapl_collect_path(void)
{
  /* Prefer AMD when processor is EPYC — even if binary was built with MONITOR_ARCH_INTEL. */
  if (likwid_rapl_is_supported_amd_processor())
    return LIKWID_RAPL_PATH_AMD;
  if (likwid_rapl_is_supported_intel_processor())
    return LIKWID_RAPL_PATH_INTEL;
  return LIKWID_RAPL_PATH_NONE;
}

static int g_rapl_not_initialized_warned;

static void likwid_rapl_warn_not_initialized(int cpu_id)
{
  if (g_rapl_not_initialized_warned)
    return;
  g_rapl_not_initialized_warned = 1;
  monitor_log_warn("likwid_rapl: RAPL not initialized (cpu_id=%d); host_cpu_hw must begin first — "
                   "energy reads will be zero until HPMinit succeeds\n",
                   cpu_id);
}

int likwid_rapl_collect_socket_mj(int cpu_id, unsigned int socket_id, unsigned long long *pkg_mj,
                                  unsigned long long *core_mj, unsigned long long *dram_mj,
                                  int *has_pkg, int *has_core, int *has_dram,
                                  unsigned long long *pp1_mj, int *has_pp1)
{
  if (pkg_mj == NULL || core_mj == NULL || dram_mj == NULL || has_pkg == NULL || has_core == NULL ||
      has_dram == NULL)
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

  if (!likwid_rapl_pwr_ready()) {
    TRACE("likwid_rapl: PWR RAPL not ready (cpu_id=%d)\n", cpu_id);
    likwid_rapl_warn_not_initialized(cpu_id);
    return -1;
  }
  return likwid_rapl_pwr_collect_socket_mj(cpu_id, socket_id, pkg_mj, core_mj, dram_mj, has_pkg,
                                           has_core, has_dram, pp1_mj, has_pp1);
}
