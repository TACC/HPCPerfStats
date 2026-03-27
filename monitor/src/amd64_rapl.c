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

// RAPL Core::X86::Msr::RAPL_PWR_UNIT
#define MSR_RAPL_PWR_UNIT    0xC0010299
#define MSR_CORE_ENERGY_STAT 0xC001029A
#define MSR_PKG_ENERGY_STAT  0xC001029B


#define KEYS\
    X(MSR_CORE_ENERGY_STAT, "E,W=32,U=mJ", ""),	\
    X(MSR_PKG_ENERGY_STAT, "E,W=32,U=mJ", "")

// static double conv;

static int amd64_rapl_begin_cpu(char *cpu)
{
  (void) cpu;
  if (!likwid_rapl_is_supported_processor()) {
    TRACE("Processor model/family %d not supported by LIKWID RAPL\n", processor);
    return -1;
  }
  return 0;
}

static void amd64_rapl_collect_cpu(struct stats_type *type, char *cpu, char *socket, int core)
{
  struct stats *stats = NULL;
  unsigned long long pkg_mj = 0;
  unsigned long long core_mj = 0;
  unsigned long long dram_mj = 0;
  int has_pkg = 0;
  int has_core = 0;
  int has_dram = 0;
  unsigned int socket_id = (unsigned int) strtoul(socket, NULL, 10);
  
  stats = get_current_stats(type, socket);
  if (stats == NULL)
    goto out;
  if (likwid_rapl_collect_socket_mj(atoi(cpu), socket_id, &pkg_mj, &core_mj,
                                    &dram_mj, &has_pkg, &has_core, &has_dram) <
      0) {
    TRACE("unable to collect LIKWID RAPL energy for socket %s (cpu %s)\n", socket,
          cpu);
    goto out;
  }
  if (core == 0) {
    if (has_core)
      stats_inc(stats, "MSR_CORE_ENERGY_STAT", core_mj);
    if (has_pkg)
      stats_inc(stats, "MSR_PKG_ENERGY_STAT", pkg_mj);
  }

 out:
  return;
}

static void amd64_rapl_collect(struct stats_type *type)
{
  int i;
  for (i = 0; i < nr_cpus; i++) {
    char cpu[80];
    snprintf(cpu, sizeof(cpu), "%d", i);
    int pkg, core, smt, nr_core;

    if (cpuid_read_cpu_topology(cpu, &pkg, &core, &smt, &nr_core) && (smt == 0)) {
      char pkg_str[80];
      snprintf(pkg_str, sizeof(pkg_str), "%d", pkg);
      amd64_rapl_collect_cpu(type, cpu, pkg_str, core);
    }
  }
}

static int amd64_rapl_begin(struct stats_type *type)
{
  int nr = 0;

  int i;
  for (i = 0; i < nr_cpus; i++) {
    char cpu[80];
    snprintf(cpu, sizeof(cpu), "%d", i);
    int pkg, core, smt, nr_core;
    
    if (cpuid_read_cpu_topology(cpu, &pkg, &core, &smt, &nr_core) && (core == 0) && (smt == 0))
      if (amd64_rapl_begin_cpu(cpu) == 0)
	nr++;
  }
  
  if (nr == 0)
    type->st_enabled = 0;
  return nr > 0 ? 0 : -1;
}

struct stats_type amd64_rapl_stats_type = {
  .st_name = "amd64_rapl",
  .st_begin = &amd64_rapl_begin,
  .st_collect = &amd64_rapl_collect,
#define X SCHEMA_DEF
  .st_schema_def = JOIN(KEYS),
#undef X
};
