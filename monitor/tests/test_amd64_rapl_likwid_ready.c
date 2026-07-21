#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "cpuid.h"
#include "stats.h"
#include "cpu_counter_metrics_likwid_begin.h"

processor_t processor = AMD_TURIN;
int nr_cpus = 1;
int n_pmcs = 6;

static int g_likwid_ready;

int cpu_counter_metrics_likwid_ready(void)
{
  return g_likwid_ready;
}

int cpuid_read_cpu_topology(char *cpu, int *pkg, int *core, int *smt, int *nr_core)
{
  (void)cpu;
  if (pkg != NULL)
    *pkg = 0;
  if (core != NULL)
    *core = 0;
  if (smt != NULL)
    *smt = 0;
  if (nr_core != NULL)
    *nr_core = 1;
  return 1;
}

void rapl_likwid_amd_collect_socket_cpu(struct stats_type *type, const char *socket_key,
                                        int cpu_lineno, unsigned socket_id, int topology_core_id)
{
  (void)type;
  (void)socket_key;
  (void)cpu_lineno;
  (void)socket_id;
  (void)topology_core_id;
}

extern struct stats_type amd64_rapl_stats_type;

static void test_disabled_when_likwid_not_ready(void)
{
  struct stats_type *t = &amd64_rapl_stats_type;

  g_likwid_ready = 0;
  t->st_enabled = 1;
  assert((*t->st_begin)(t) < 0);
  assert(t->st_enabled == 0);
}

static void test_begins_when_likwid_ready(void)
{
  struct stats_type *t = &amd64_rapl_stats_type;

  g_likwid_ready = 1;
  t->st_enabled = 1;
  assert((*t->st_begin)(t) == 0);
  assert(t->st_enabled == 1);
}

int main(void)
{
  test_disabled_when_likwid_not_ready();
  test_begins_when_likwid_ready();
  printf("test_amd64_rapl_likwid_ready passed\n");
  return 0;
}
