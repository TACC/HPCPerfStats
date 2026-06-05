/* LIKWID CPU counter backend begin. */
#include <stddef.h>
#include <stdlib.h>
#include <stdio.h>
#include <stdint.h>
#include <limits.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include "stats.h"
#include "trace.h"
#include "cpu_counter_metrics.h"
#include "cpu_counter_metrics_likwid_begin.h"

#ifndef MONITOR_CPU_BACKEND_DCGM
#include "likwid_pmc_adapter.h"
#include "likwid_arch_map.h"

static int g_likwid_ready;

int likwid_backend_begin(struct stats_type *type)
{
  (void)type;
  if (likwid_pmc_adapter_init(nr_cpus) == 0 &&
      likwid_pmc_adapter_setup_events(likwid_arch_eventset()) == 0) {
    g_likwid_ready = 1;
    return 0;
  }
  likwid_pmc_adapter_finalize();
  g_likwid_ready = 0;
  type->st_enabled = 1;
  return 0;
}

int cpu_counter_metrics_likwid_ready(void)
{
  return g_likwid_ready;
}

#endif
