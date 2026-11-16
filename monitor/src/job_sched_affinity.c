/* Read-only CPU affinity union for shared-node %affinity / full-resource path. */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include <errno.h>
#include <sched.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "job_sched_affinity.h"

static job_sched_affinity_get_fn g_inject;

void job_sched_affinity_set_inject(job_sched_affinity_get_fn fn)
{
  g_inject = fn;
}

static int affinity_get(pid_t pid, cpu_set_t *set, size_t setsize)
{
  if (g_inject != NULL)
    return g_inject(pid, set, setsize);
  return sched_getaffinity(pid, setsize, set);
}

static int parse_cpulist_into(const char *list, cpu_set_t *set, size_t ncpus)
{
  const char *p = list;

  if (list == NULL || set == NULL)
    return -1;
  CPU_ZERO_S(CPU_ALLOC_SIZE(ncpus), set);
  while (*p) {
    char *end = NULL;
    long a;
    long b;

    while (*p == ',' || *p == ' ' || *p == '\t' || *p == '\n')
      p++;
    if (*p == '\0')
      break;
    a = strtol(p, &end, 10);
    if (end == p || a < 0)
      return -1;
    p = end;
    if (*p == '-') {
      p++;
      b = strtol(p, &end, 10);
      if (end == p || b < a)
        return -1;
      p = end;
    } else {
      b = a;
    }
    for (; a <= b; a++) {
      if ((size_t)a >= ncpus)
        return -1;
      CPU_SET_S((size_t)a, CPU_ALLOC_SIZE(ncpus), set);
    }
  }
  return 0;
}

int job_sched_affinity_read_online(cpu_set_t **out_set, size_t *out_size, size_t *out_ncpus)
{
  char buf[4096];
  FILE *f;
  size_t ncpus;
  size_t setsize;
  cpu_set_t *set;

  if (out_set == NULL || out_size == NULL || out_ncpus == NULL)
    return -1;
  *out_set = NULL;
  f = fopen("/sys/devices/system/cpu/online", "re");
  if (f == NULL)
    return -1;
  if (fgets(buf, sizeof(buf), f) == NULL) {
    fclose(f);
    return -1;
  }
  fclose(f);

  ncpus = (size_t)sysconf(_SC_NPROCESSORS_CONF);
  /* online list may reference higher indices than CONF on some kernels */
  if (ncpus < 1024)
    ncpus = 1024;
  setsize = CPU_ALLOC_SIZE(ncpus);
  set = CPU_ALLOC(ncpus);
  if (set == NULL)
    return -1;
  if (parse_cpulist_into(buf, set, ncpus) < 0) {
    CPU_FREE(set);
    return -1;
  }
  *out_set = set;
  *out_size = setsize;
  *out_ncpus = ncpus;
  return 0;
}

static int format_cpulist(const cpu_set_t *set, size_t ncpus, char *out, size_t out_cap)
{
  size_t i;
  size_t used = 0;
  int first = 1;

  if (out == NULL || out_cap == 0)
    return -1;
  out[0] = '\0';
  i = 0;
  while (i < ncpus) {
    if (!CPU_ISSET_S(i, CPU_ALLOC_SIZE(ncpus), set)) {
      i++;
      continue;
    }
    {
      size_t start = i;
      size_t end = i;

      while (end + 1 < ncpus && CPU_ISSET_S(end + 1, CPU_ALLOC_SIZE(ncpus), set))
        end++;
      {
        char piece[64];
        int pn;

        if (start == end)
          pn = snprintf(piece, sizeof(piece), "%s%zu", first ? "" : ",", start);
        else
          pn = snprintf(piece, sizeof(piece), "%s%zu-%zu", first ? "" : ",", start, end);
        if (pn < 0 || used + (size_t)pn >= out_cap)
          return -1;
        memcpy(out + used, piece, (size_t)pn);
        used += (size_t)pn;
        out[used] = '\0';
        first = 0;
      }
      i = end + 1;
    }
  }
  return 0;
}

static int sets_equal(const cpu_set_t *a, const cpu_set_t *b, size_t ncpus)
{
  size_t i;

  for (i = 0; i < ncpus; i++) {
    int ina = CPU_ISSET_S(i, CPU_ALLOC_SIZE(ncpus), a);
    int inb = CPU_ISSET_S(i, CPU_ALLOC_SIZE(ncpus), b);

    if (ina != inb)
      return 0;
  }
  return 1;
}

int job_sched_affinity_union_cpulist(const int *pids, size_t npids, int sample_limit, char *out,
                                     size_t out_cap, enum job_cpu_coverage *cov)
{
  cpu_set_t *online = NULL;
  cpu_set_t *union_set = NULL;
  size_t online_size = 0;
  size_t ncpus = 0;
  size_t i;
  size_t sampled = 0;
  int ok_any = 0;

  if (cov)
    *cov = JOB_CPU_UNKNOWN;
  if (out && out_cap)
    out[0] = '\0';
  if (pids == NULL || npids == 0 || out == NULL || out_cap == 0)
    return -1;
  if (sample_limit <= 0)
    sample_limit = 8;

  if (job_sched_affinity_read_online(&online, &online_size, &ncpus) < 0)
    return -1;
  union_set = CPU_ALLOC(ncpus);
  if (union_set == NULL) {
    CPU_FREE(online);
    return -1;
  }
  CPU_ZERO_S(CPU_ALLOC_SIZE(ncpus), union_set);

  for (i = 0; i < npids && (int)sampled < sample_limit; i++) {
    cpu_set_t *tmp = CPU_ALLOC(ncpus);
    size_t c;

    if (tmp == NULL)
      break;
    CPU_ZERO_S(CPU_ALLOC_SIZE(ncpus), tmp);
    if (affinity_get((pid_t)pids[i], tmp, CPU_ALLOC_SIZE(ncpus)) != 0) {
      CPU_FREE(tmp);
      if (errno == EPERM || errno == ESRCH)
        continue;
      CPU_FREE(union_set);
      CPU_FREE(online);
      if (cov)
        *cov = JOB_CPU_UNKNOWN;
      return -1;
    }
    for (c = 0; c < ncpus; c++) {
      if (CPU_ISSET_S(c, CPU_ALLOC_SIZE(ncpus), tmp))
        CPU_SET_S(c, CPU_ALLOC_SIZE(ncpus), union_set);
    }
    CPU_FREE(tmp);
    sampled++;
    ok_any = 1;
  }

  if (!ok_any) {
    CPU_FREE(union_set);
    CPU_FREE(online);
    if (cov)
      *cov = JOB_CPU_UNKNOWN;
    return -1;
  }

  if (format_cpulist(union_set, ncpus, out, out_cap) < 0) {
    CPU_FREE(union_set);
    CPU_FREE(online);
    if (cov)
      *cov = JOB_CPU_UNKNOWN;
    return -1;
  }

  if (cov) {
    if (sets_equal(union_set, online, ncpus))
      *cov = JOB_CPU_EXACT_ALL;
    else
      *cov = JOB_CPU_PARTIAL;
  }
  CPU_FREE(union_set);
  CPU_FREE(online);
  return 0;
}

int job_sched_affinity_cpu_dev_in_list(const char *cpulist, const char *cpu_dev)
{
  char *end = NULL;
  long cpu;
  const char *p;

  if (cpulist == NULL || cpulist[0] == '\0' || cpu_dev == NULL || cpu_dev[0] == '\0')
    return 0;
  errno = 0;
  cpu = strtol(cpu_dev, &end, 10);
  if (end == cpu_dev || *end != '\0' || errno || cpu < 0)
    return 0;
  p = cpulist;
  while (*p) {
    char *e = NULL;
    long a;
    long b;

    while (*p == ',' || *p == ' ' || *p == '\t')
      p++;
    if (*p == '\0')
      break;
    a = strtol(p, &e, 10);
    if (e == p || a < 0)
      return 0;
    p = e;
    if (*p == '-') {
      p++;
      b = strtol(p, &e, 10);
      if (e == p || b < a)
        return 0;
      p = e;
    } else {
      b = a;
    }
    if (cpu >= a && cpu <= b)
      return 1;
  }
  return 0;
}

static long cpulist_max_index(const char *list, long max_cpu)
{
  const char *p = list;

  while (*p) {
    char *e = NULL;
    long v;

    while (*p == ',' || *p == ' ' || *p == '-' || *p == '\t' || *p == '\n')
      p++;
    if (*p == '\0')
      break;
    v = strtol(p, &e, 10);
    if (e == p)
      break;
    if (v > max_cpu)
      max_cpu = v;
    p = e;
  }
  return max_cpu;
}

int job_sched_affinity_cpulist_subtract(const char *online_list, const char *const *claimed,
                                        size_t nclaimed, char *out, size_t out_cap)
{
  cpu_set_t *online = NULL;
  cpu_set_t *claim = NULL;
  size_t ncpus = 1024;
  size_t i;
  long max_cpu = -1;

  if (out == NULL || out_cap == 0)
    return -1;
  out[0] = '\0';
  if (online_list == NULL || online_list[0] == '\0')
    return -1;

  /* Bound ncpus from the highest index referenced in any list. */
  max_cpu = cpulist_max_index(online_list, max_cpu);
  if (claimed != NULL) {
    for (i = 0; i < nclaimed; i++) {
      if (claimed[i] != NULL)
        max_cpu = cpulist_max_index(claimed[i], max_cpu);
    }
  }
  if (max_cpu >= 0 && (size_t)max_cpu + 1 > ncpus)
    ncpus = (size_t)max_cpu + 1;
  if (ncpus < 1024)
    ncpus = 1024;

  online = CPU_ALLOC(ncpus);
  claim = CPU_ALLOC(ncpus);
  if (online == NULL || claim == NULL) {
    CPU_FREE(online);
    CPU_FREE(claim);
    return -1;
  }
  if (parse_cpulist_into(online_list, online, ncpus) < 0) {
    CPU_FREE(online);
    CPU_FREE(claim);
    return -1;
  }
  CPU_ZERO_S(CPU_ALLOC_SIZE(ncpus), claim);
  if (claimed != NULL) {
    for (i = 0; i < nclaimed; i++) {
      cpu_set_t *tmp;

      if (claimed[i] == NULL || claimed[i][0] == '\0')
        continue;
      tmp = CPU_ALLOC(ncpus);
      if (tmp == NULL) {
        CPU_FREE(online);
        CPU_FREE(claim);
        return -1;
      }
      if (parse_cpulist_into(claimed[i], tmp, ncpus) == 0) {
        size_t c;

        for (c = 0; c < ncpus; c++) {
          if (CPU_ISSET_S(c, CPU_ALLOC_SIZE(ncpus), tmp))
            CPU_SET_S(c, CPU_ALLOC_SIZE(ncpus), claim);
        }
      }
      CPU_FREE(tmp);
    }
  }
  for (i = 0; i < ncpus; i++) {
    if (CPU_ISSET_S(i, CPU_ALLOC_SIZE(ncpus), claim))
      CPU_CLR_S(i, CPU_ALLOC_SIZE(ncpus), online);
  }
  if (format_cpulist(online, ncpus, out, out_cap) < 0) {
    CPU_FREE(online);
    CPU_FREE(claim);
    return -1;
  }
  CPU_FREE(online);
  CPU_FREE(claim);
  return 0;
}

int job_sched_affinity_remainder_cpulist(const char *const *claimed, size_t nclaimed, char *out,
                                         size_t out_cap)
{
  char online_buf[4096];
  FILE *f;

  if (out == NULL || out_cap == 0)
    return -1;
  out[0] = '\0';
  f = fopen("/sys/devices/system/cpu/online", "re");
  if (f == NULL)
    return -1;
  if (fgets(online_buf, sizeof(online_buf), f) == NULL) {
    fclose(f);
    return -1;
  }
  fclose(f);
  return job_sched_affinity_cpulist_subtract(online_buf, claimed, nclaimed, out, out_cap);
}
