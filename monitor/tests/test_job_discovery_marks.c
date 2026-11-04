/* Phase 4: wire mark strings + jid/gpu helpers for shared-node payloads. */
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <sched.h>
#include <stdio.h>
#include <string.h>

#include "job_emit_filter.h"
#include "job_gpu_assign.h"
#include "job_proc_discover.h"
#include "job_sched_affinity.h"

static void test_mark_shapes(void)
{
  char jobs[128];
  char aff[256];
  struct job_gpu_set g = {.n = 1};
  char gpus[32];

  snprintf(g.devs[0], JOB_GPU_DEV_STR, "2");
  job_gpu_set_format_wire(&g, gpus, sizeof(gpus));
  assert(strcmp(gpus, "2") == 0);

  snprintf(jobs, sizeof(jobs), "jobs jids=%s", "111,222");
  assert(strcmp(jobs, "jobs jids=111,222") == 0);
  snprintf(aff, sizeof(aff), "affinity jid=%s cpus=%s gpus=%s cg=sched", "111", "0-35", gpus);
  assert(strstr(aff, "cpus=0-35") != NULL);
  assert(strstr(aff, "gpus=2") != NULL);
  assert(strstr(aff, "cg=sched") != NULL);
}

static void test_full_resource_predicate_logic(void)
{
  /* Sole full-resource => no marks (logic locked here; daemon uses same predicates). */
  int sole = 1;
  enum { EXACT_ALL = 2 } cov = EXACT_ALL;
  struct job_gpu_set granted = {.n = 4, .inventory_known = 0};
  struct job_gpu_set available = {.n = 4, .inventory_known = 1};
  int i;

  for (i = 0; i < 4; i++) {
    snprintf(granted.devs[i], JOB_GPU_DEV_STR, "%d", i);
    snprintf(available.devs[i], JOB_GPU_DEV_STR, "%d", i);
  }
  assert(sole && cov == EXACT_ALL && job_gpu_set_exact_equal(&granted, &available));
}

static void test_host_mem_job_dev_format(void)
{
  char dev[2 + JOB_PROC_JID_MAX + 1];
  int n;

  n = snprintf(dev, sizeof(dev), "j:%s", "62909");
  assert(n > 0 && (size_t)n < sizeof(dev));
  assert(strcmp(dev, "j:62909") == 0);
  assert(strncmp(dev, "j:", 2) == 0);
  /* Bare jid is never the host_mem_job device. */
  assert(strcmp(dev, "62909") != 0);
}

static void gpu_set_of(struct job_gpu_set *s, int n)
{
  int i;

  memset(s, 0, sizeof(*s));
  for (i = 0; i < n; i++)
    snprintf(s->devs[i], JOB_GPU_DEV_STR, "%d", i);
  s->n = (size_t)n;
  s->inventory_known = 1;
}

static struct job_proc_jid_entry entry_of(const char *jid, int *pid, int from_title)
{
  struct job_proc_jid_entry e;

  memset(&e, 0, sizeof(e));
  snprintf(e.jid, sizeof(e.jid), "%s", jid);
  e.pids = pid;
  e.npids = 1;
  e.named_from_title = from_title;
  return e;
}

static int keep(struct job_emit_filter_ctx *c, const char *st, const char *dev)
{
  return job_emit_row_filter(st, dev, c);
}

static void test_job_shared_emit_title_named_sole_is_unmarked(void)
{
  int pid = 5001;
  struct job_proc_jid_entry e = entry_of("79249", &pid, 1);
  struct job_gpu_set empty;
  struct job_gpu_set available;
  struct job_emit_filter_ctx c;

  memset(&empty, 0, sizeof(empty));
  gpu_set_of(&available, 4);
  /* No SLURM_JOB_GPUS and unknown CPU coverage: still the unmarked (no %jobs) payload. */
  assert(job_emit_sole_ctx(&e, JOB_CPU_UNKNOWN, &empty, &available, &c) == 1);
  assert(keep(&c, "nvidia_gpu", "3") == 1); /* whole node, every GPU */
  assert(keep(&c, "host_cpu", "17") == 1);
  assert(keep(&c, "ib", "mlx5_0.1") == 1);
  assert(keep(&c, "host_mem", "0") == 1);
  assert(keep(&c, "host_mem_job", "j:79249") == 1);
  assert(keep(&c, "host_mem_job", "j:1") == 0);

  /* CPU-only node: same unmarked decision. */
  memset(&available, 0, sizeof(available));
  available.inventory_known = 1;
  assert(job_emit_sole_ctx(&e, JOB_CPU_UNKNOWN, &empty, &available, &c) == 1);
}

static void test_job_shared_emit_env_named_sole_keeps_full_resource_rule(void)
{
  int pid = 6001;
  struct job_proc_jid_entry e = entry_of("111", &pid, 0);
  struct job_gpu_set granted;
  struct job_gpu_set partial;
  struct job_gpu_set available;
  struct job_emit_filter_ctx c;

  gpu_set_of(&available, 4);
  gpu_set_of(&granted, 4);
  gpu_set_of(&partial, 1);
  assert(job_emit_sole_ctx(&e, JOB_CPU_EXACT_ALL, &granted, &available, &c) == 1);
  assert(job_emit_sole_ctx(&e, JOB_CPU_PARTIAL, &granted, &available, &c) == 0);
  /* Marked path filters GPUs to the job's set. */
  assert(job_emit_sole_ctx(&e, JOB_CPU_PARTIAL, &partial, &available, &c) == 0);
  assert(keep(&c, "nvidia_gpu", "0") == 1);
  assert(keep(&c, "nvidia_gpu", "2") == 0);
  available.inventory_known = 0;
  assert(job_emit_sole_ctx(&e, JOB_CPU_EXACT_ALL, &granted, &available, &c) == 0);
  assert(job_emit_sole_ctx(NULL, JOB_CPU_EXACT_ALL, &granted, &available, &c) == 0);
  assert(job_emit_sole_ctx(&e, JOB_CPU_EXACT_ALL, &granted, NULL, &c) == 0);
}

/* M2 regression (jid 79282, hpcperfstats04): a sole job on every online CPU that requested
 * no GPUs (empty SLURM_JOB_GPUS on a 4-GPU node) was marked gpus=- and every GPU row was
 * dropped. Holding all CPUs means no second job fits: unmarked, with every available GPU. */
static void test_job_shared_emit_sole_all_cpus_owns_gpus_without_grant(void)
{
  int pid = 6001;
  struct job_proc_jid_entry e = entry_of("79282", &pid, 0);
  struct job_gpu_set empty;
  struct job_gpu_set partial;
  struct job_gpu_set available;
  struct job_emit_filter_ctx c;

  memset(&empty, 0, sizeof(empty));
  gpu_set_of(&partial, 1);
  gpu_set_of(&available, 4);
  assert(job_emit_sole_ctx(&e, JOB_CPU_EXACT_ALL, &empty, &available, &c) == 1);
  assert(keep(&c, "nvidia_gpu", "0") == 1);
  assert(keep(&c, "nvidia_gpu", "3") == 1);
  assert(keep(&c, "host_cpu", "143") == 1);
  assert(keep(&c, "ib", "mlx5_0.1") == 1);
  /* A partial grant does not mark a whole-CPU job either. */
  assert(job_emit_sole_ctx(&e, JOB_CPU_EXACT_ALL, &partial, &available, &c) == 1);
  assert(keep(&c, "nvidia_gpu", "2") == 1);
  /* CPU-only node (known-empty inventory): unmarked too. */
  memset(&available, 0, sizeof(available));
  available.inventory_known = 1;
  assert(job_emit_sole_ctx(&e, JOB_CPU_EXACT_ALL, &empty, &available, &c) == 1);
}

/* M3: a sole job on part of the node is split like N >= 2: its payload keeps only its CPUs and
 * GPUs, and the '-' remainder carries the rest instead of the rows being dropped. */
static void test_job_shared_emit_sole_partial_splits_remainder(void)
{
  int pid = 6001;
  struct job_proc_jid_entry e = entry_of("72352", &pid, 0);
  struct job_gpu_set mine;
  struct job_gpu_set available;
  struct job_gpu_set remainder;
  struct job_emit_filter_ctx jid_ctx;
  struct job_emit_filter_ctx rem_ctx;
  const char *claimed[] = {"0-71"};
  char rem_cpus[64];

  gpu_set_of(&mine, 2); /* GPUs 0,1 */
  gpu_set_of(&available, 4);
  job_gpu_set_diff(&available, &mine, &remainder);
  assert(job_sched_affinity_cpulist_subtract("0-143", claimed, 1, rem_cpus, sizeof(rem_cpus)) == 0);
  assert(strcmp(rem_cpus, "72-143") == 0);

  job_emit_multi_ctx(&e, &mine, "0-71", &jid_ctx);
  job_emit_remainder_ctx(&remainder, rem_cpus, 1, &rem_ctx);
  assert(keep(&jid_ctx, "host_cpu", "10") == 1 && keep(&rem_ctx, "host_cpu", "10") == 0);
  assert(keep(&jid_ctx, "host_cpu", "100") == 0 && keep(&rem_ctx, "host_cpu", "100") == 1);
  assert(keep(&jid_ctx, "nvidia_gpu", "1") == 1 && keep(&rem_ctx, "nvidia_gpu", "1") == 0);
  assert(keep(&jid_ctx, "nvidia_gpu", "3") == 0 && keep(&rem_ctx, "nvidia_gpu", "3") == 1);
  assert(keep(&rem_ctx, "ib", "mlx5_0.1") == 1);
  assert(keep(&rem_ctx, "host_mem_job", "j:72352") == 0);
  assert(keep(&rem_ctx, "host_proc", "a.out/6001/f/1") == 0);
  /* Unknown inventory: '-' carries no GPU rows. */
  job_emit_remainder_ctx(&remainder, rem_cpus, 0, &rem_ctx);
  assert(keep(&rem_ctx, "nvidia_gpu", "3") == 0);
  job_emit_remainder_ctx(&remainder, rem_cpus, 1, NULL); /* no-op */
}

/* M1: CPU coverage comes from the extern stepd (job cpuset), not MPI-pinned task pids. */
static cpu_set_t *g_online_mask;
static size_t g_online_size;

static int inject_stepd_vs_tasks(pid_t pid, cpu_set_t *set, size_t setsize)
{
  memset(set, 0, setsize);
  if (pid == 100) { /* stepd: the whole node */
    memcpy(set, g_online_mask, setsize < g_online_size ? setsize : g_online_size);
    return 0;
  }
  if (pid == 101 || pid == 102) { /* ranks pinned to CPU 0 and 1 */
    CPU_SET_S((size_t)(pid - 101), setsize, set);
    return 0;
  }
  errno = ESRCH;
  return -1;
}

static void test_job_emit_entry_cpulist_prefers_stepd(void)
{
  int pids[] = {101, 102};
  struct job_proc_jid_entry e = entry_of("79282", pids, 0);
  size_t ncpus = 0;
  char out[512];
  enum job_cpu_coverage cov = JOB_CPU_UNKNOWN;

  e.npids = 2;
  if (job_sched_affinity_read_online(&g_online_mask, &g_online_size, &ncpus) < 0)
    return; /* no /sys online list on this host */
  g_online_size = CPU_ALLOC_SIZE(ncpus);
  job_sched_affinity_set_inject(inject_stepd_vs_tasks);

  e.stepd_pid = 100;
  assert(job_emit_entry_cpulist(&e, 8, out, sizeof(out), &cov) == 0);
  assert(cov == JOB_CPU_EXACT_ALL);

  /* No stepd known (environ-only fallback): the old pinned-task union. */
  e.stepd_pid = 0;
  assert(job_emit_entry_cpulist(&e, 8, out, sizeof(out), &cov) == 0);
  assert(strcmp(out, "0-1") == 0);
  /* stepd vanished (ESRCH): falls back to the task sample. */
  e.stepd_pid = 99;
  assert(job_emit_entry_cpulist(&e, 8, out, sizeof(out), &cov) == 0);
  assert(strcmp(out, "0-1") == 0);
  assert(job_emit_entry_cpulist(NULL, 8, out, sizeof(out), &cov) == -1);

  job_sched_affinity_set_inject(NULL);
  CPU_FREE(g_online_mask);
  g_online_mask = NULL;
}

static void test_job_shared_emit_title_named_multi_omits_affinity(void)
{
  int pid = 5001;
  struct job_proc_jid_entry e = entry_of("79249", &pid, 1);
  struct job_gpu_set g;
  struct job_emit_filter_ctx c;

  gpu_set_of(&g, 2);
  job_emit_multi_ctx(&e, &g, "0-63", &c);
  assert(job_emit_entry_emits_affinity(&e) == 0);
  /* Only host_mem_job: no CPU, GPU, proc or node-wide copies (D4). */
  assert(keep(&c, "host_mem_job", "j:79249") == 1);
  assert(keep(&c, "host_mem_job", "j:111") == 0);
  assert(keep(&c, "host_cpu", "0") == 0);
  assert(keep(&c, "host_cpu_hw", "0") == 0);
  assert(keep(&c, "nvidia_gpu", "0") == 0);
  assert(keep(&c, "host_proc", "slurmstepd/5001/ff/1") == 0);
  assert(keep(&c, "ib", "mlx5_0.1") == 0);
  assert(keep(&c, "nfs", "/home") == 0);
  assert(keep(&c, "host_mem", "0") == 0);
}

static void test_job_shared_emit_env_named_multi_keeps_split(void)
{
  int pid = 6001;
  struct job_proc_jid_entry e = entry_of("111", &pid, 0);
  struct job_gpu_set g;
  struct job_emit_filter_ctx c;

  gpu_set_of(&g, 1);
  job_emit_multi_ctx(&e, &g, "0-3", &c);
  assert(job_emit_entry_emits_affinity(&e) == 1);
  assert(keep(&c, "host_cpu", "2") == 1);
  assert(keep(&c, "host_cpu", "5") == 0);
  assert(keep(&c, "nvidia_gpu", "0") == 1);
  assert(keep(&c, "nvidia_gpu", "1") == 0);
  assert(keep(&c, "host_proc", "a.out/6001/f/1") == 1);
  assert(keep(&c, "host_proc", "a.out/7/f/1") == 0);
  assert(keep(&c, "ib", "mlx5_0.1") == 0);
  assert(keep(&c, "host_mem_job", "j:111") == 1);
  assert(job_emit_entry_emits_affinity(NULL) == 0);
}

static void test_node_wide_filter_ctx(void)
{
  struct job_gpu_set rem;
  struct job_emit_filter_ctx c = {
      .gpus = &rem, .cpulist = "4-7", .include_node_wide = 1, .include_proc = 0, .include_gpu = 1};

  gpu_set_of(&rem, 1);
  assert(keep(&c, "host_mem_job", "j:111") == 0); /* '-' never carries host_mem_job */
  assert(keep(&c, "host_cpu", "5") == 1);
  assert(keep(&c, "host_cpu", "1") == 0);
  assert(keep(&c, "ib", "mlx5_0.1") == 1);
  assert(keep(&c, "host_proc", "x/1/f/1") == 0);
  assert(job_emit_row_filter("ib", "x", NULL) == 1);
}

int main(void)
{
  assert(job_proc_jid_acceptable("62909"));
  test_mark_shapes();
  test_full_resource_predicate_logic();
  test_host_mem_job_dev_format();
  test_job_shared_emit_title_named_sole_is_unmarked();
  test_job_shared_emit_env_named_sole_keeps_full_resource_rule();
  test_job_shared_emit_sole_all_cpus_owns_gpus_without_grant();
  test_job_shared_emit_sole_partial_splits_remainder();
  test_job_emit_entry_cpulist_prefers_stepd();
  test_job_shared_emit_title_named_multi_omits_affinity();
  test_job_shared_emit_env_named_multi_keeps_split();
  test_node_wide_filter_ctx();
  printf("test_job_discovery_marks: ok\n");
  return 0;
}
