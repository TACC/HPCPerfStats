/*! \file rapl_likwid_stats.c
 *  Map LIKWID RAPL samples into pkg_energy / pp0_energy schema keys.
 */

#include "rapl_likwid_stats.h"
#include "likwid_rapl.h"
#include "stats.h"
#include "trace.h"
#include <stdlib.h>

void rapl_likwid_intel_collect_pkg(struct stats_type *type, const char *pkg_key, int cpu_lineno,
                                   unsigned pkg_id)
{
  struct stats *stats = NULL;
  unsigned long long pkg_mj = 0;
  unsigned long long core_mj = 0;
  unsigned long long dram_mj = 0;
  unsigned long long pp1_mj = 0;
  int has_pkg = 0;
  int has_core = 0;
  int has_dram = 0;
  int has_pp1 = 0;

  TRACE("cpu %d pkg %s\n", cpu_lineno, pkg_key);

  stats = get_current_stats(type, pkg_key);
  if (stats == NULL)
    return;

  if (likwid_rapl_collect_socket_mj(cpu_lineno, pkg_id, &pkg_mj, &core_mj, &dram_mj, &has_pkg,
                                    &has_core, &has_dram, &pp1_mj, &has_pp1) < 0) {
    TRACE("unable to collect LIKWID RAPL energy for pkg %u (cpu %d)\n", pkg_id, cpu_lineno);
    return;
  }

  if (has_pkg)
    stats_set(stats, "pkg_energy", pkg_mj);
  if (has_core)
    stats_set(stats, "pp0_energy", core_mj);
  if (has_pp1)
    stats_set(stats, "pp1_energy", pp1_mj);
  if (has_dram)
    stats_set(stats, "dram_energy", dram_mj);
}

void rapl_likwid_amd_collect_socket_cpu(struct stats_type *type, const char *socket_key,
                                        int cpu_lineno, unsigned socket_id, int topology_core_id)
{
  struct stats *stats = NULL;
  unsigned long long pkg_mj = 0;
  unsigned long long core_mj = 0;
  unsigned long long dram_mj = 0;
  int has_pkg = 0;
  int has_core = 0;
  int has_dram = 0;

  stats = get_current_stats(type, socket_key);
  if (stats == NULL)
    return;

  if (likwid_rapl_collect_socket_mj(cpu_lineno, socket_id, &pkg_mj, &core_mj, &dram_mj, &has_pkg,
                                    &has_core, &has_dram, NULL, NULL) < 0) {
    TRACE("unable to collect LIKWID RAPL energy for socket %s (cpu %d)\n", socket_key, cpu_lineno);
    return;
  }

  if (topology_core_id == 0) {
    if (has_core)
      stats_inc(stats, "core_energy", core_mj);
    if (has_pkg)
      stats_inc(stats, "pkg_energy", pkg_mj);
  }
}
