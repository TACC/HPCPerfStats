/*! \file amd64_rapl.c
 *  AMD socket RAPL energy via LIKWID (stats type amd_x86_rapl).
 */

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "stats.h"
#include "trace.h"
#include "monitor_log.h"
#include "cpuid.h"
#include "likwid_rapl.h"
#include "rapl_likwid_stats.h"
#include "cpu_counter_metrics_likwid_begin.h"

#define KEYS X(core_energy, "E,W=32,U=mJ", ""), X(pkg_energy, "E,W=32,U=mJ", "")

static int amd64_rapl_begin_cpu(char *cpu)
{
  (void)cpu;
  if (!likwid_rapl_is_supported_amd_processor()) {
    TRACE("Processor model/family %d not supported by AMD LIKWID RAPL\n", processor);
    return -1;
  }
  return 0;
}

static void amd64_rapl_collect(struct stats_type *type)
{
  int i;

  for (i = 0; i < nr_cpus; i++) {
    char cpu[80];
    int pkg, core, smt, nr_core;
    char pkg_str[80];

    snprintf(cpu, sizeof(cpu), "%d", i);

    if (cpuid_read_cpu_topology(cpu, &pkg, &core, &smt, &nr_core) && (smt == 0)) {
      snprintf(pkg_str, sizeof(pkg_str), "%d", pkg);
      rapl_likwid_amd_collect_socket_cpu(type, pkg_str, atoi(cpu), (unsigned)pkg, core);
    }
  }
}

static int amd64_rapl_begin(struct stats_type *type)
{
  int nr = 0;
  int i;

  if (!likwid_rapl_is_supported_amd_processor()) {
    TRACE("amd_x86_rapl disabled: processor %d is not AMD Zen RAPL capable\n", processor);
    type->st_enabled = 0;
    return -1;
  }

  if (!cpu_counter_metrics_likwid_ready()) {
    monitor_log_error(
        "amd_x86_rapl: disabled (LIKWID PMC session not ready; host_cpu_hw must init first)\n");
    type->st_enabled = 0;
    return -1;
  }

  for (i = 0; i < nr_cpus; i++) {
    char cpu[80];
    int pkg, core, smt, nr_core;

    snprintf(cpu, sizeof(cpu), "%d", i);

    if (cpuid_read_cpu_topology(cpu, &pkg, &core, &smt, &nr_core) && (core == 0) && (smt == 0)) {
      if (amd64_rapl_begin_cpu(cpu) == 0)
        nr++;
    }
  }

  if (nr == 0)
    type->st_enabled = 0;
  return nr > 0 ? 0 : -1;
}

struct stats_type amd64_rapl_stats_type = {
    .st_name = "amd_x86_rapl",
    .st_begin = &amd64_rapl_begin,
    .st_collect = &amd64_rapl_collect,
#define X SCHEMA_DEF
    .st_schema_def = JOIN(KEYS),
#undef X
};
