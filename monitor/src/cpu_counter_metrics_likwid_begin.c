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
#include "monitor_log.h"
#include "cpu_counter_metrics.h"
#include "cpu_counter_metrics_likwid_begin.h"

#ifndef MONITOR_CPU_BACKEND_DCGM
#include "cpuid.h"
#include "likwid_pmc_adapter.h"
#include "likwid_arch_map.h"

static int g_likwid_ready;

int likwid_backend_begin(struct stats_type *type)
{
  const char *eventset;
  int init_rc;
  int setup_rc;

  if (type == NULL)
    return -1;

  eventset = likwid_arch_eventset_for_processor(processor, n_pmcs);
  init_rc = likwid_pmc_adapter_init(nr_cpus);
  if (init_rc != 0) {
    monitor_log_error("host_cpu_hw: LIKWID PMC init failed (nr_cpus=%d); disabling type\n",
                      nr_cpus);
    goto fail;
  }
  setup_rc = likwid_pmc_adapter_setup_events(eventset);
  if (setup_rc != 0) {
    monitor_log_error("host_cpu_hw: LIKWID eventset setup failed (events=`%s`); disabling type\n",
                      eventset != NULL ? eventset : "(null)");
    goto fail;
  }
  g_likwid_ready = 1;
  return 0;

fail:
  likwid_pmc_adapter_finalize();
  g_likwid_ready = 0;
  /* LIKWID-only (Intel + AMD): disable host_cpu_hw when setup fails (no MSR fallback). */
  type->st_enabled = 0;
  return -1;
}

int cpu_counter_metrics_likwid_ready(void)
{
  return g_likwid_ready;
}

#endif
