/*! 
 \file intel_rapl.c
 \author Todd Evans 
 \brief RAPL Counters for Intel Processors
*/

#include <stddef.h>
#include <stdlib.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <unistd.h>
#include <dirent.h>
#include <errno.h>
#include <malloc.h>
#include <ctype.h>
#include <fcntl.h>
#include <math.h>
#include "stats.h"
#include "trace.h"
#include "cpuid.h"
#include "likwid_rapl.h"

#define MSR_RAPL_POWER_UNIT        0x606

/*
 * Platform specific RAPL Domains.
 * Note that PP1 RAPL Domain is supported on 062A only
 * And DRAM RAPL Domain is supported on 062D only
 */
/* Package RAPL Domain */
#define MSR_PKG_ENERGY_STATUS      0x611
#define MSR_PKG_POWER_INFO         0x614

/* PP0 RAPL Domain */
#define MSR_PP0_ENERGY_STATUS      0x639

/* PP1 RAPL Domain, may reflect to uncore devices */
#define MSR_PP1_ENERGY_STATUS      0x641

/* DRAM RAPL Domain */
#define MSR_DRAM_ENERGY_STATUS     0x619
#define MSR_DRAM_POWER_INFO        0x61C

/* RAPL UNIT BITMASK */
#define POWER_UNIT_OFFSET          0
#define POWER_UNIT_MASK            0x0F

#define ENERGY_UNIT_OFFSET         0x08
#define ENERGY_UNIT_MASK           0x1F00

#define TIME_UNIT_OFFSET           0x10
#define TIME_UNIT_MASK             0xF000

#define KEYS						\
  X(MSR_PKG_ENERGY_STATUS, "E,W=32,U=mJ", ""),		\
    X(MSR_PP0_ENERGY_STATUS, "E,W=32,U=mJ", ""),		\
    X(MSR_DRAM_ENERGY_STATUS, "E,W=32,U=mJ", "")

static int intel_rapl_begin(struct stats_type *type)
{
  if (!likwid_rapl_is_supported_processor()) {
    TRACE("intel_rapl disabled because processor is not LIKWID RAPL capable\n");
    type->st_enabled = 0;
    return -1;
  }
  return 0;
}

static void intel_rapl_collect_socket(struct stats_type *type, char *cpu, int pkg_id)
{
  struct stats *stats = NULL;
  char pkg[80];
  unsigned long long pkg_mj = 0;
  unsigned long long core_mj = 0;
  unsigned long long dram_mj = 0;
  int has_pkg = 0;
  int has_core = 0;
  int has_dram = 0;
  
  snprintf(pkg, sizeof(pkg), "%d", pkg_id);

  TRACE("cpu %s pkg %s\n", cpu, pkg);

  stats = get_current_stats(type, pkg);
  if (stats == NULL)
    goto out;
  if (likwid_rapl_collect_socket_mj(atoi(cpu), (unsigned int) pkg_id, &pkg_mj,
                                    &core_mj, &dram_mj, &has_pkg, &has_core,
                                    &has_dram) < 0) {
    TRACE("unable to collect LIKWID RAPL energy for pkg %d (cpu %s)\n", pkg_id,
          cpu);
    goto out;
  }
  if (has_pkg)
    stats_set(stats, "MSR_PKG_ENERGY_STATUS", pkg_mj);
  if (has_core)
    stats_set(stats, "MSR_PP0_ENERGY_STATUS", core_mj);
  if (has_dram)
    stats_set(stats, "MSR_DRAM_ENERGY_STATUS", dram_mj);

 out:
  return;
}

//! Collect values of counters
static void intel_rapl_collect(struct stats_type *type)
{
  int i;
  for (i = 0; i < nr_cpus; i++) {
    char cpu[80];
    int pkg_id = -1;
    int core_id = -1;
    int smt_id = -1;
    int nr_cores = 0;
    snprintf(cpu, sizeof(cpu), "%d", i);
    cpuid_read_cpu_topology(cpu, &pkg_id, &core_id, &smt_id, &nr_cores);
  
    if (core_id == 0 && smt_id == 0)
      intel_rapl_collect_socket(type, cpu, pkg_id);
  }
}

//! Definition of stats entry for this type
struct stats_type intel_rapl_stats_type = {
  .st_name = "intel_rapl",
  .st_begin = &intel_rapl_begin,
  .st_collect = &intel_rapl_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};
