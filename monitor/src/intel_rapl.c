/*!
 \file intel_rapl.c
 \author Todd Evans
 \brief RAPL energy for Intel processors (stats type intel_x86_rapl) via x86_rapl.
*/

#include <stdio.h>
#include "stats.h"
#include "trace.h"
#include "monitor_log.h"
#include "cpuid.h"
#include "x86_rapl.h"

#define KEYS                                                                                       \
  X(pkg_energy, "E,W=32,U=mJ", ""), X(pp0_energy, "E,W=32,U=mJ", ""),                              \
      X(pp1_energy, "E,W=32,U=mJ", ""), X(dram_energy, "E,W=32,U=mJ", "")

static const char *const k_intel_rapl_key[X86_RAPL_DOMAINS] = {
    [X86_RAPL_PKG] = "pkg_energy",
    [X86_RAPL_CORES] = "pp0_energy",
    [X86_RAPL_GPU] = "pp1_energy",
    [X86_RAPL_RAM] = "dram_energy",
};

static struct x86_rapl g_intel_rapl;

static int intel_rapl_is_supported_processor(void)
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

static int intel_rapl_begin(struct stats_type *type)
{
  if (!intel_rapl_is_supported_processor()) {
    TRACE("intel_x86_rapl disabled: processor %d is not a supported Intel RAPL model\n", processor);
    type->st_enabled = 0;
    return -1;
  }
  if (x86_rapl_begin(&g_intel_rapl, X86_RAPL_SYSFS_ROOT,
                     X86_RAPL_BIT(X86_RAPL_PKG) | X86_RAPL_BIT(X86_RAPL_CORES) |
                         X86_RAPL_BIT(X86_RAPL_GPU) | X86_RAPL_BIT(X86_RAPL_RAM),
                     perf_open_cpu) == 0) {
    monitor_log_error(
        "intel_x86_rapl: disabled (no perf power PMU event or powercap energy_uj readable)\n");
    x86_rapl_end(&g_intel_rapl);
    type->st_enabled = 0;
    return -1;
  }
  return 0;
}

static void intel_rapl_collect(struct stats_type *type)
{
  int i, d;

  for (i = 0; i < g_intel_rapl.n_sockets; i++) {
    struct x86_rapl_sample sample[X86_RAPL_DOMAINS];
    struct stats *stats;
    char pkg[16];

    snprintf(pkg, sizeof(pkg), "%d", g_intel_rapl.sockets[i].id);
    stats = get_current_stats(type, pkg);
    if (stats == NULL)
      continue;
    x86_rapl_read(&g_intel_rapl, i, sample);
    for (d = 0; d < X86_RAPL_DOMAINS; d++) {
      if (sample[d].status == STATS_SENTINEL_OK)
        stats_set(stats, k_intel_rapl_key[d], sample[d].mj);
      else
        stats_set_missing(stats, k_intel_rapl_key[d], sample[d].status);
    }
  }
}

struct stats_type intel_rapl_stats_type = {
    .st_name = "intel_x86_rapl",
    .st_begin = &intel_rapl_begin,
    .st_collect = &intel_rapl_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(KEYS),
#undef X
};
