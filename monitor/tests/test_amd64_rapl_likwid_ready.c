#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cpuid.h"
#include "stats.h"
#include "cpu_counter_metrics_likwid_begin.h"
#include "likwid_rapl_pwr.h"

processor_t processor = AMD_TURIN;
int nr_cpus = 1;
int n_pmcs = 6;

static int g_likwid_ready;
static int g_pwr_begin_rc = -1;

int cpu_counter_metrics_likwid_ready(void)
{
  return g_likwid_ready;
}

int likwid_rapl_pwr_begin(int amd_path)
{
  (void)amd_path;
  return g_pwr_begin_rc;
}

int likwid_rapl_pwr_ready(void)
{
  return g_pwr_begin_rc == 0;
}

int likwid_rapl_pwr_collect_socket_mj(int cpu_id, unsigned int socket_id,
                                      unsigned long long *pkg_mj, unsigned long long *core_mj,
                                      unsigned long long *dram_mj, int *has_pkg, int *has_core,
                                      int *has_dram, unsigned long long *pp1_mj, int *has_pp1)
{
  (void)cpu_id;
  (void)socket_id;
  (void)pkg_mj;
  (void)core_mj;
  (void)dram_mj;
  (void)has_pkg;
  (void)has_core;
  (void)has_dram;
  (void)pp1_mj;
  (void)has_pp1;
  return -1;
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

  unsetenv("HPCPERFSTATS_LIKWID_ACCESS");
  g_likwid_ready = 0;
  g_pwr_begin_rc = 0;
  t->st_enabled = 1;
  assert((*t->st_begin)(t) < 0);
  assert(t->st_enabled == 0);
}

static void test_disabled_when_direct_env_and_pwr_fails(void)
{
  struct stats_type *t = &amd64_rapl_stats_type;

  /* DIRECT MSR escape is removed: same failure mode as PERF when PWR begin fails. */
  assert(setenv("HPCPERFSTATS_LIKWID_ACCESS", "direct", 1) == 0);
  g_likwid_ready = 1;
  g_pwr_begin_rc = -1;
  t->st_enabled = 1;
  assert((*t->st_begin)(t) < 0);
  assert(t->st_enabled == 0);
}

static void test_begins_when_perf_pwr_ok(void)
{
  struct stats_type *t = &amd64_rapl_stats_type;

  unsetenv("HPCPERFSTATS_LIKWID_ACCESS");
  g_likwid_ready = 1;
  g_pwr_begin_rc = 0;
  t->st_enabled = 1;
  assert((*t->st_begin)(t) == 0);
  assert(t->st_enabled == 1);
}

static void test_disabled_when_perf_pwr_fails(void)
{
  struct stats_type *t = &amd64_rapl_stats_type;

  unsetenv("HPCPERFSTATS_LIKWID_ACCESS");
  g_likwid_ready = 1;
  g_pwr_begin_rc = -1;
  t->st_enabled = 1;
  assert((*t->st_begin)(t) < 0);
  assert(t->st_enabled == 0);
}

int main(void)
{
  test_disabled_when_likwid_not_ready();
  test_disabled_when_direct_env_and_pwr_fails();
  test_begins_when_perf_pwr_ok();
  test_disabled_when_perf_pwr_fails();
  unsetenv("HPCPERFSTATS_LIKWID_ACCESS");
  printf("test_amd64_rapl_likwid_ready passed\n");
  return 0;
}
