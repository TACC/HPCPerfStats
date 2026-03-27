#include <stddef.h>
#include <stdlib.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include "trace.h"
#include "stats.h"
#include "likwid_pmc_adapter.h"

#ifdef HAVE_LIKWID
#include <likwid.h>
#endif

static int g_initialized = 0;
static int g_group = -1;

int likwid_pmc_adapter_init(int nr_threads)
{
#ifdef HAVE_LIKWID
  int rc = -1;
  int i = 0;
  int *cpus = NULL;
  if (nr_threads <= 0)
    return -1;
  cpus = (int *) malloc((size_t) nr_threads * sizeof(*cpus));
  if (cpus == NULL) {
    ERROR("cannot allocate LIKWID cpu map: %m\n");
    return -1;
  }
  for (i = 0; i < nr_threads; i++)
    cpus[i] = i;
  topology_init();
  numa_init();
  HPMmode(ACCESSMODE_PERF);
  if (HPMinit() < 0) {
    ERROR("LIKWID HPMinit failed\n");
    goto out;
  }
  if (perfmon_init(nr_threads, cpus) < 0) {
    ERROR("LIKWID perfmon_init failed\n");
    goto out;
  }
  g_initialized = 1;
  rc = 0;
 out:
  free(cpus);
  return rc;
#else
  (void) nr_threads;
  return -1;
#endif
}

void likwid_pmc_adapter_finalize(void)
{
#ifdef HAVE_LIKWID
  if (g_initialized) {
    perfmon_finalize();
    HPMfinalize();
  }
#endif
  g_initialized = 0;
  g_group = -1;
}

int likwid_pmc_adapter_setup_events(const char *event_string)
{
#ifdef HAVE_LIKWID
  if (!g_initialized || event_string == NULL)
    return -1;
  g_group = perfmon_addEventSet(event_string);
  if (g_group < 0)
    return -1;
  if (perfmon_setupCounters(g_group) < 0)
    return -1;
  if (perfmon_startCounters() < 0)
    return -1;
  return 0;
#else
  (void) event_string;
  return -1;
#endif
}

static void set_counter_by_name(struct stats *stats, const char *counter_name,
                                unsigned long long value)
{
  if (counter_name == NULL || stats == NULL)
    return;
  if (strcmp(counter_name, "FIXC0") == 0)
    stats_set(stats, "FIXED_CTR0", value);
  else if (strcmp(counter_name, "FIXC1") == 0)
    stats_set(stats, "FIXED_CTR1", value);
  else if (strcmp(counter_name, "FIXC2") == 0)
    stats_set(stats, "FIXED_CTR2", value);
  else if (strcmp(counter_name, "PMC0") == 0)
    stats_set(stats, "CTR0", value);
  else if (strcmp(counter_name, "PMC1") == 0)
    stats_set(stats, "CTR1", value);
  else if (strcmp(counter_name, "PMC2") == 0)
    stats_set(stats, "CTR2", value);
  else if (strcmp(counter_name, "PMC3") == 0)
    stats_set(stats, "CTR3", value);
  else if (strcmp(counter_name, "PMC4") == 0)
    stats_set(stats, "CTR4", value);
  else if (strcmp(counter_name, "PMC5") == 0)
    stats_set(stats, "CTR5", value);
  else if (strcmp(counter_name, "PMC6") == 0)
    stats_set(stats, "CTR6", value);
  else if (strcmp(counter_name, "PMC7") == 0)
    stats_set(stats, "CTR7", value);
}

int likwid_pmc_adapter_read_cpu(struct stats *stats, int cpu, uint64_t *events,
                                int nr_events, int max_ctrs)
{
#ifdef HAVE_LIKWID
  int i = 0;
  int n_events = 0;
  (void) max_ctrs;
  if (!g_initialized || g_group < 0 || stats == NULL || cpu < 0)
    return -1;
  if (perfmon_readCounters() < 0)
    return -1;
  n_events = perfmon_getNumberOfEvents(g_group);
  for (i = 0; i < n_events; i++) {
    const char *counter_name = perfmon_getCounterName(g_group, i);
    unsigned long long val = (unsigned long long) perfmon_getResult(g_group, i, cpu);
    set_counter_by_name(stats, counter_name, val);
  }
  for (i = 0; i < nr_events; i++) {
    char key[16];
    snprintf(key, sizeof(key), "CTL%d", i);
    stats_set(stats, key, events[i]);
  }
  return 0;
#else
  (void) stats;
  (void) cpu;
  (void) events;
  (void) nr_events;
  (void) max_ctrs;
  return -1;
#endif
}
