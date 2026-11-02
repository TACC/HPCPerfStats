/* Per-payload row filter for shared-node multi-jid emit (job_shared_emit.c). */
#include <stdlib.h>
#include <string.h>

#include "job_cgroup_mem.h"
#include "job_emit_filter.h"

static int is_gpu_type(const char *st_name)
{
  return st_name != NULL && (strcmp(st_name, "nvidia_gpu") == 0 ||
                             strcmp(st_name, "amd_gpu") == 0 || strcmp(st_name, "intel_gpu") == 0);
}

static int is_proc_type(const char *st_name)
{
  return st_name != NULL && strcmp(st_name, "host_proc") == 0;
}

static int is_job_mem_type(const char *st_name)
{
  return st_name != NULL && strcmp(st_name, "host_mem_job") == 0;
}

static int is_cpu_type(const char *st_name)
{
  return st_name != NULL &&
         (strcmp(st_name, "host_cpu") == 0 || strcmp(st_name, "host_cpu_hw") == 0);
}

static int proc_dev_pid(const char *dev, int *pid_out)
{
  /* host_proc device: name/pid/cmask/mmask */
  const char *slash;
  const char *p;
  char *end;
  long pid;

  if (dev == NULL)
    return -1;
  slash = strchr(dev, '/');
  if (slash == NULL)
    return -1;
  p = slash + 1;
  pid = strtol(p, &end, 10);
  if (end == p || (*end != '/' && *end != '\0'))
    return -1;
  *pid_out = (int)pid;
  return 0;
}

static int pid_in_entry(const struct job_proc_jid_entry *e, int pid)
{
  size_t i;

  if (e == NULL)
    return 0;
  for (i = 0; i < e->npids; i++) {
    if (e->pids[i] == pid)
      return 1;
  }
  return 0;
}

static int gpu_dev_in_set(const struct job_gpu_set *set, const char *dev)
{
  size_t i;

  if (set == NULL || dev == NULL)
    return 0;
  for (i = 0; i < set->n; i++) {
    if (strcmp(set->devs[i], dev) == 0)
      return 1;
  }
  return 0;
}

int job_emit_row_filter(const char *st_name, const char *dev, void *ctx)
{
  struct job_emit_filter_ctx *c = ctx;

  if (c == NULL)
    return 1;
  if (is_proc_type(st_name)) {
    int pid;

    if (!c->include_proc || c->entry == NULL)
      return 0;
    if (proc_dev_pid(dev, &pid) < 0)
      return 0;
    return pid_in_entry(c->entry, pid);
  }
  if (is_gpu_type(st_name)) {
    if (!c->include_gpu)
      return 0;
    return gpu_dev_in_set(c->gpus, dev);
  }
  if (is_cpu_type(st_name)) {
    if (c->cpulist != NULL)
      return job_sched_affinity_cpu_dev_in_list(c->cpulist, dev);
    return c->include_node_wide ? 1 : 0;
  }
  if (is_job_mem_type(st_name)) {
    if (c->entry == NULL)
      return 0; /* "-" / idle: omit all host_mem_job */
    return job_cgroup_mem_keep_dev(c->entry->jid, dev);
  }
  return c->include_node_wide ? 1 : 0;
}

int job_emit_entry_emits_affinity(const struct job_proc_jid_entry *e)
{
  return e != NULL && !e->named_from_title;
}

int job_emit_sole_ctx(const struct job_proc_jid_entry *e, enum job_cpu_coverage cov,
                      const struct job_gpu_set *granted, const struct job_gpu_set *g_union,
                      const struct job_gpu_set *available, struct job_emit_filter_ctx *out)
{
  int unmarked;

  if (e == NULL || out == NULL || available == NULL)
    return 0;
  unmarked = !job_emit_entry_emits_affinity(e) ||
             (cov == JOB_CPU_EXACT_ALL && available->inventory_known && granted != NULL &&
              job_gpu_set_exact_equal(granted, available));
  memset(out, 0, sizeof(*out));
  out->entry = e;
  /* Title-named: the allocation owns the node, so keep every available GPU. */
  out->gpus = e->named_from_title ? available : g_union;
  out->include_node_wide = 1;
  out->include_proc = 1;
  out->include_gpu = 1;
  return unmarked;
}

void job_emit_multi_ctx(const struct job_proc_jid_entry *e, const struct job_gpu_set *g_union,
                        const char *cpulist, struct job_emit_filter_ctx *out)
{
  int split;

  if (out == NULL)
    return;
  split = job_emit_entry_emits_affinity(e);
  memset(out, 0, sizeof(*out));
  out->entry = e;
  out->gpus = split ? g_union : NULL;
  out->cpulist = split ? cpulist : NULL;
  out->include_proc = split;
  out->include_gpu = split;
}
