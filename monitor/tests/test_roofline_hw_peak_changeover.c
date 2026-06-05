#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cpuid.h"
#include "stats.h"
#include "roofline_hw_peak.h"

double current_time = 0.0;
char jobid[80] = "-";
int nr_cpus = 64;
int n_pmcs = 0;
processor_t processor = SKYLAKE;
int stats_collect_on_changeover = 0;

static struct stats g_stats;
static unsigned long long g_cpu_peak_fp64_flops_per_s = 0ULL;
static unsigned long long g_cpu_peak_source = 0ULL;
static unsigned long long g_peak_calc_version = 0ULL;
static int g_get_current_stats_calls = 0;

struct stats *get_current_stats(struct stats_type *type, const char *dev)
{
  (void) type;
  (void) dev;
  g_get_current_stats_calls++;
  memset(&g_stats, 0, sizeof(g_stats));
  return &g_stats;
}

void stats_set(struct stats *stats, const char *key, unsigned long long val)
{
  (void) stats;
  if (strcmp(key, "cpu_peak_fp64_flops_per_s") == 0) {
    g_cpu_peak_fp64_flops_per_s = val;
  } else if (strcmp(key, "cpu_peak_source") == 0) {
    g_cpu_peak_source = val;
  } else if (strcmp(key, "peak_calc_version") == 0) {
    g_peak_calc_version = val;
  }
}

int main(void)
{
  struct stats_type type = roofline_hw_peak_stats_type;

  assert(strcmp(type.st_name, ROOFLINE_HW_PEAK_ST_NAME) == 0);
  setenv("HPCPERFSTATS_SKIP_HW_PROBE", "1", 1);
  unsetenv("HPCPERFSTATS_ROOFLINE_MODE");
  unsetenv("HPCPERFSTATS_ROOFLINE_PERIOD_SAMPLES");

  stats_collect_on_changeover = 0;
  type.st_collect(&type);
  assert(g_get_current_stats_calls == 0);

  stats_collect_on_changeover = 1;
  type.st_collect(&type);
  assert(g_get_current_stats_calls == 1);
  assert(g_cpu_peak_fp64_flops_per_s > 0ULL);
  assert(g_cpu_peak_source == 1ULL);
  assert(g_peak_calc_version == 1ULL);

  setenv("HPCPERFSTATS_ROOFLINE_MODE", "every_sample", 1);
  stats_collect_on_changeover = 0;
  type.st_collect(&type);
  assert(g_get_current_stats_calls == 2);
  {
    unsigned long long cached = g_cpu_peak_fp64_flops_per_s;
    nr_cpus = 1;
    type.st_collect(&type);
    assert(g_get_current_stats_calls == 3);
    assert(g_cpu_peak_fp64_flops_per_s == cached);
  }

  setenv("HPCPERFSTATS_ROOFLINE_MODE", "periodic", 1);
  setenv("HPCPERFSTATS_ROOFLINE_PERIOD_SAMPLES", "100", 1);
  stats_collect_on_changeover = 0;
  type.st_collect(&type);
  assert(g_get_current_stats_calls == 3);
  stats_collect_on_changeover = 1;
  type.st_collect(&type);
  assert(g_get_current_stats_calls == 4);

  printf("test_roofline_hw_peak_changeover passed\n");
  return 0;
}
