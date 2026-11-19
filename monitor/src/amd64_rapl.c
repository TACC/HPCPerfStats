/*! \file amd64_rapl.c
 *  AMD EPYC socket RAPL energy (stats type amd_x86_rapl) via x86_rapl.
 */

#include <stdio.h>
#include "stats.h"
#include "trace.h"
#include "monitor_log.h"
#include "amd_processor.h"
#include "x86_rapl.h"

#define KEYS X(core_energy, "E,W=32,U=mJ", ""), X(pkg_energy, "E,W=32,U=mJ", "")

/* Domains this type publishes; the others are never opened. */
static const char *const k_amd_rapl_key[X86_RAPL_DOMAINS] = {
    [X86_RAPL_PKG] = "pkg_energy",
    [X86_RAPL_CORES] = "core_energy",
};

static struct x86_rapl g_amd_rapl;

static int amd64_rapl_begin(struct stats_type *type)
{
  if (!amd_processor_is_epyc(processor)) {
    TRACE("amd_x86_rapl disabled: processor %d is not AMD Zen RAPL capable\n", processor);
    type->st_enabled = 0;
    return -1;
  }
  if (x86_rapl_begin(&g_amd_rapl, X86_RAPL_SYSFS_ROOT,
                     X86_RAPL_BIT(X86_RAPL_PKG) | X86_RAPL_BIT(X86_RAPL_CORES),
                     perf_open_cpu) == 0) {
    monitor_log_error(
        "amd_x86_rapl: disabled (no perf power PMU event or powercap energy_uj readable)\n");
    x86_rapl_end(&g_amd_rapl);
    type->st_enabled = 0;
    return -1;
  }
  return 0;
}

static void amd64_rapl_collect(struct stats_type *type)
{
  int i, d;

  for (i = 0; i < g_amd_rapl.n_sockets; i++) {
    struct x86_rapl_sample sample[X86_RAPL_DOMAINS];
    struct stats *stats;
    char pkg[16];

    snprintf(pkg, sizeof(pkg), "%d", g_amd_rapl.sockets[i].id);
    stats = get_current_stats(type, pkg);
    if (stats == NULL)
      continue;
    x86_rapl_read(&g_amd_rapl, i, sample);
    for (d = 0; d < X86_RAPL_DOMAINS; d++) {
      if (k_amd_rapl_key[d] == NULL)
        continue;
      if (sample[d].status == STATS_SENTINEL_OK)
        stats_set(stats, k_amd_rapl_key[d], sample[d].mj);
      else
        stats_set_missing(stats, k_amd_rapl_key[d], sample[d].status);
    }
  }
}

struct stats_type amd64_rapl_stats_type = {
    .st_name = "amd_x86_rapl",
    .st_begin = &amd64_rapl_begin,
    .st_collect = &amd64_rapl_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(KEYS),
#undef X
};
