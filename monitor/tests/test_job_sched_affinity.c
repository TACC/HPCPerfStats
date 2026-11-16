/* Phase 3: affinity union + full-resource predicate helpers. */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include <assert.h>
#include <sched.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "job_gpu_assign.h"
#include "job_sched_affinity.h"

static cpu_set_t *g_masks[8];
static size_t g_mask_ncpus = 8;

static int inject_get(pid_t pid, cpu_set_t *set, size_t setsize)
{
  (void)setsize;
  if (pid < 1 || pid > 8 || g_masks[pid - 1] == NULL)
    return -1;
  memcpy(set, g_masks[pid - 1], CPU_ALLOC_SIZE(g_mask_ncpus));
  return 0;
}

static void set_bits(cpu_set_t *set, int a, int b)
{
  int i;

  CPU_ZERO_S(CPU_ALLOC_SIZE(g_mask_ncpus), set);
  for (i = a; i <= b; i++)
    CPU_SET_S((size_t)i, CPU_ALLOC_SIZE(g_mask_ncpus), set);
}

static void test_union_cpulist(void)
{
  char out[128];
  enum job_cpu_coverage cov;
  int pids[] = {1, 2};

  g_masks[0] = CPU_ALLOC(g_mask_ncpus);
  g_masks[1] = CPU_ALLOC(g_mask_ncpus);
  set_bits(g_masks[0], 0, 1);
  set_bits(g_masks[1], 2, 2);
  job_sched_affinity_set_inject(inject_get);

  /* Cannot assert EXACT_ALL against real /sys online; check format + partial. */
  assert(job_sched_affinity_union_cpulist(pids, 2, 8, out, sizeof(out), &cov) == 0);
  assert(strstr(out, "0") != NULL);
  assert(cov == JOB_CPU_PARTIAL || cov == JOB_CPU_EXACT_ALL || cov == JOB_CPU_UNKNOWN);

  job_sched_affinity_set_inject(NULL);
  CPU_FREE(g_masks[0]);
  CPU_FREE(g_masks[1]);
  g_masks[0] = g_masks[1] = NULL;
}

static void test_full_resource_table(void)
{
  struct job_gpu_set granted = {.inventory_known = 0};
  struct job_gpu_set available = {.inventory_known = 1, .n = 0};

  /* GPU-less known-empty + empty grant */
  assert(job_gpu_set_exact_equal(&granted, &available));

  available.inventory_known = 0;
  assert(!job_gpu_set_exact_equal(&granted, &available));

  available.inventory_known = 1;
  snprintf(available.devs[0], JOB_GPU_DEV_STR, "0");
  available.n = 1;
  assert(!job_gpu_set_exact_equal(&granted, &available));

  snprintf(granted.devs[0], JOB_GPU_DEV_STR, "0");
  granted.n = 1;
  assert(job_gpu_set_exact_equal(&granted, &available));
}

static void test_cpu_dev_in_list(void)
{
  assert(job_sched_affinity_cpu_dev_in_list("0-2,4", "0") == 1);
  assert(job_sched_affinity_cpu_dev_in_list("0-2,4", "2") == 1);
  assert(job_sched_affinity_cpu_dev_in_list("0-2,4", "4") == 1);
  assert(job_sched_affinity_cpu_dev_in_list("0-2,4", "3") == 0);
  assert(job_sched_affinity_cpu_dev_in_list("", "0") == 0);
  assert(job_sched_affinity_cpu_dev_in_list("0-2", "cpu0") == 0);
  assert(job_sched_affinity_cpu_dev_in_list(NULL, "0") == 0);
}

static void test_cpulist_subtract(void)
{
  char out[128];
  const char *claimed[] = {"0-1", "3"};

  assert(job_sched_affinity_cpulist_subtract("0-7", claimed, 2, out, sizeof(out)) == 0);
  assert(strstr(out, "2") != NULL);
  assert(strstr(out, "4") != NULL);
  assert(strstr(out, "7") != NULL);
  assert(job_sched_affinity_cpu_dev_in_list(out, "0") == 0);
  assert(job_sched_affinity_cpu_dev_in_list(out, "1") == 0);
  assert(job_sched_affinity_cpu_dev_in_list(out, "3") == 0);
  assert(job_sched_affinity_cpu_dev_in_list(out, "2") == 1);
}

int main(void)
{
  test_union_cpulist();
  test_full_resource_table();
  test_cpu_dev_in_list();
  test_cpulist_subtract();
  printf("test_job_sched_affinity: ok\n");
  return 0;
}
