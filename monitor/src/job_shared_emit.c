/* One metrics collect + N serialized payloads for shared-node multi-jid. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "job_cgroup_mem.h"
#include "job_emit_filter.h"
#include "job_gpu_assign.h"
#include "job_proc_discover.h"
#include "job_sched_affinity.h"
#include "job_shared_emit.h"
#include "monitor_daemon.h"
#include "monitor_log.h"
#include "stats.h"
#include "stats_buffer.h"
#include "stats_buffer_debug_shm.h"
#include "stats_buffer_rows.h"
#include "stats_runtime.h"
#include "stats_text_format.h"

#if defined(MONITOR_HAVE_NVIDIA_GPU)
#include "nvidia_gpu_dcgm_watch.h"
#endif

/*! Fill cgroup_relpath from first readable pid when discovery left it empty. */
static void fill_cgroup_relpath_from_pids(struct job_proc_jid_entry *e)
{
  const char *proc_root;
  size_t i;

  if (e == NULL || e->cgroup_relpath[0] != '\0' || e->pids == NULL)
    return;
  proc_root = job_proc_discover_proc_root();
  for (i = 0; i < e->npids; i++) {
    char path[512];
    char line[JOB_PROC_CGROUP_RELPATH_MAX + 64];
    FILE *f;
    const char *colon;
    const char *p;
    size_t len;
    int n;

    n = snprintf(path, sizeof(path), "%s/%d/cgroup", proc_root, e->pids[i]);
    if (n < 0 || (size_t)n >= sizeof(path))
      continue;
    f = fopen(path, "re");
    if (f == NULL)
      continue;
    if (fgets(line, (int)sizeof(line), f) == NULL) {
      fclose(f);
      continue;
    }
    fclose(f);
    colon = strchr(line, ':');
    if (colon == NULL)
      continue;
    colon = strchr(colon + 1, ':');
    if (colon == NULL)
      continue;
    p = colon + 1;
    while (*p == ' ' || *p == '\t')
      p++;
    if (*p != '/')
      continue;
    len = 0;
    while (p[len] != '\0' && p[len] != '\n' && p[len] != '\r')
      len++;
    while (len > 0 && p[len - 1] == '/')
      len--;
    if (len == 0 || len >= sizeof(e->cgroup_relpath))
      continue;
    memcpy(e->cgroup_relpath, p, len);
    e->cgroup_relpath[len] = '\0';
    return;
  }
}

static void populate_host_mem_job(struct job_proc_discover_result *disc)
{
  struct stats_type *type;
  size_t i;
  static time_t last_warn;

  if (disc == NULL)
    return;
  type = stats_type_get("host_mem_job");
  if (type == NULL || !type->st_enabled)
    return;
  for (i = 0; i < disc->n; i++) {
    struct job_cgroup_mem_kb kb;
    struct stats *st;
    char dev[2 + JOB_PROC_JID_MAX + 1];
    struct job_proc_jid_entry *e = &disc->entries[i];

    fill_cgroup_relpath_from_pids(e);
    if (e->cgroup_relpath[0] == '\0' || job_cgroup_mem_read(e->cgroup_relpath, &kb) < 0) {
      time_t now = time(NULL);

      if (last_warn == 0 || (now > last_warn && now - last_warn >= 60)) {
        monitor_log_warn("host_mem_job: jid=%s cgroup read failed (relpath=%s); omit device\n",
                         e->jid, e->cgroup_relpath[0] ? e->cgroup_relpath : "(empty)");
        last_warn = now;
      }
      continue;
    }
    if (job_cgroup_mem_format_dev(e->jid, dev, sizeof(dev)) < 0)
      continue;
    st = get_current_stats(type, dev);
    if (st == NULL)
      continue;
    stats_set(st, "mem_used", kb.mem_used);
    stats_set(st, "mem_peak", kb.mem_peak);
    stats_set(st, "anon_pages", kb.anon_pages);
    stats_set(st, "file_pages", kb.file_pages);
    stats_set(st, "mapped", kb.mapped);
    stats_set(st, "dirty", kb.dirty);
    stats_set(st, "writeback", kb.writeback);
    stats_set(st, "page_tables", kb.page_tables);
  }
}

static void fill_available_gpus(struct job_gpu_set *available)
{
  memset(available, 0, sizeof(*available));
#if defined(MONITOR_HAVE_NVIDIA_GPU)
  if (g_nvidia_gpu_runtime_ndev > 0) {
    int i;

    for (i = 0; i < g_nvidia_gpu_runtime_ndev && available->n < JOB_GPU_DEV_MAX; i++) {
      snprintf(available->devs[available->n], JOB_GPU_DEV_STR, "%d", i);
      available->n++;
    }
    available->inventory_known = 1;
    return;
  }
#endif
  /* Known-empty when NVIDIA GPU is compiled out or no devices are up. */
  available->inventory_known = 1;
}

static void format_jobs_csv(const struct job_proc_discover_result *disc, char *out, size_t out_cap)
{
  size_t i;
  size_t used = 0;

  out[0] = '\0';
  for (i = 0; i < disc->n; i++) {
    int pn = snprintf(out + used, out_cap - used, "%s%s", i ? "," : "", disc->entries[i].jid);

    if (pn < 0 || used + (size_t)pn >= out_cap) {
      out[0] = '\0';
      return;
    }
    used += (size_t)pn;
  }
}

/* mirror_shm: 0 = still ring-insert, but do not overwrite DEBUG fast/full (trailing
 * multi-jid node-wide '-' would otherwise hide the last jid sample in shm). */
static int insert_serialized(struct sf_ring_buffer *w, const char *hdr_jobid, const char *mark1,
                             const char *mark2, int mirror_shm, enum stats_row_tier *tier_out)
{
  struct stats_buffer *sf;
  int rc;
  enum stats_row_tier tier;

  if (w == NULL)
    return -1;
  sf = monitor_daemon_alloc_stats_buffer();
  if (sf == NULL)
    return -1;
  snprintf(jobid, sizeof(jobid), "%s", hdr_jobid != NULL ? hdr_jobid : "-");
  if (mark1 != NULL)
    stats_buffer_mark(sf, "%s", mark1);
  if (mark2 != NULL)
    stats_buffer_mark(sf, "%s", mark2);
  w->b_count++;
  rc = stats_buffer_collect(sf);
  if (rc < 0) {
    stats_buffer_close(sf);
    free(sf);
    return -1;
  }
  tier = stats_buffer_payload_row_tier(sf);
  if (tier_out != NULL)
    *tier_out = tier;
  if (stats_buffer_debug_shm_sample_wanted(0, 1)) {
    stats_buffer_debug_shm_burst_append(sf);
    if (mirror_shm)
      stats_buffer_debug_shm_write_sample(sf, tier);
  }
  rc = ring_buffer_insert(sf, w, max_buffer_size, allow_ring_buffer_overwrite);
  if (rc < 0) {
    stats_buffer_close(sf);
    free(sf);
    return -1;
  }
  return 0;
}

void job_shared_emit_sample_burst(struct sf_ring_buffer *w)
{
  struct job_proc_discover_result disc;
  struct job_gpu_set available;
  struct timespec now;
  char jobs_csv[JOB_PROC_MAX_JIDS * (JOB_PROC_JID_MAX + 1)];
  size_t i;
  int full_resource = 0;
  enum stats_row_tier burst_tier = STATS_ROW_FULL;

  memset(&disc, 0, sizeof(disc));
  if (job_proc_discover(&disc, jid_require_step_id, jid_discover_pid_cap) < 0) {
    /* fail-open: idle sample */
    disc.n = 0;
  }

  fill_available_gpus(&available);

  if (clock_gettime(CLOCK_REALTIME, &now) != 0) {
    now.tv_sec = 0;
    now.tv_nsec = 0;
  }
  stats_buffer_set_frozen_time(&now);
  stats_buffer_debug_shm_burst_begin();

  /* One metrics collect for the whole burst (host_mem_job collect clears devices). */
  stats_runtime_collect_enabled_metrics(0);
  populate_host_mem_job(&disc);

  if (disc.n == 0) {
    struct job_emit_filter_ctx ctx = {.include_node_wide = 1, .include_proc = 0, .include_gpu = 0};

    stats_buffer_set_row_filter(job_emit_row_filter, &ctx);
    insert_serialized(w, "-", NULL, NULL, 1, &burst_tier);
    goto done;
  }

  if (disc.n == 1) {
    struct job_gpu_set granted;
    struct job_gpu_set in_use;
    struct job_gpu_set g_union;
    char cpulist[512] = "";
    enum job_cpu_coverage cov = JOB_CPU_UNKNOWN;
    struct job_emit_filter_ctx ctx;

    memset(&granted, 0, sizeof(granted));
    memset(&g_union, 0, sizeof(g_union));
    /* Title-named (idle allocation): no grant, and stepd fds/affinity are not the job's. */
    if (job_emit_entry_emits_affinity(&disc.entries[0])) {
      job_gpu_assign_map_grant(disc.entries[0].job_gpus, &granted);
      job_gpu_assign_scan_fds(disc.entries[0].pids, disc.entries[0].npids, &in_use);
      job_gpu_set_union(&granted, &in_use, &g_union);
      (void)job_sched_affinity_union_cpulist(disc.entries[0].pids, disc.entries[0].npids,
                                             affinity_pid_sample_limit, cpulist, sizeof(cpulist),
                                             &cov);
    }
    full_resource = job_emit_sole_ctx(&disc.entries[0], cov, &granted, &g_union, &available, &ctx);
    stats_buffer_set_row_filter(job_emit_row_filter, &ctx);
    if (full_resource) {
      insert_serialized(w, disc.entries[0].jid, NULL, NULL, 1, &burst_tier);
      goto done;
    }

    {
      char jobs_mark[256];
      char aff_mark[768];
      char gpus_wire[256];

      format_jobs_csv(&disc, jobs_csv, sizeof(jobs_csv));
      snprintf(jobs_mark, sizeof(jobs_mark), "jobs jids=%s", jobs_csv);
      job_gpu_set_format_wire(&g_union, gpus_wire, sizeof(gpus_wire));
      if (cov != JOB_CPU_UNKNOWN && cpulist[0])
        snprintf(aff_mark, sizeof(aff_mark), "affinity jid=%s cpus=%s gpus=%s cg=sched",
                 disc.entries[0].jid, cpulist, gpus_wire);
      else
        aff_mark[0] = '\0';
      insert_serialized(w, disc.entries[0].jid, jobs_mark, aff_mark[0] ? aff_mark : NULL, 1,
                        &burst_tier);
    }
    goto done;
  }

  /* N >= 2: per-jid payloads + one node-wide '-' (remainder GPU/CPU). */
  format_jobs_csv(&disc, jobs_csv, sizeof(jobs_csv));
  {
    struct job_gpu_set claimed;
    struct job_gpu_set remainder;
    struct job_gpu_set tmp;
    char cpu_lists[JOB_PROC_MAX_JIDS][512];
    const char *cpu_ptrs[JOB_PROC_MAX_JIDS];
    char rem_cpus[512];
    size_t n_cpu_lists = 0;

    memset(&claimed, 0, sizeof(claimed));
    for (i = 0; i < disc.n; i++) {
      struct job_gpu_set granted;
      struct job_gpu_set in_use;
      struct job_gpu_set g_union;
      enum job_cpu_coverage cov = JOB_CPU_UNKNOWN;

      cpu_lists[i][0] = '\0';
      /* Title-named jobs claim no CPUs/GPUs: those stay on the node-wide '-' payload. */
      if (!job_emit_entry_emits_affinity(&disc.entries[i]))
        continue;
      job_gpu_assign_map_grant(disc.entries[i].job_gpus, &granted);
      job_gpu_assign_scan_fds(disc.entries[i].pids, disc.entries[i].npids, &in_use);
      job_gpu_set_union(&granted, &in_use, &g_union);
      job_gpu_set_union(&claimed, &g_union, &tmp);
      claimed = tmp;
      (void)job_sched_affinity_union_cpulist(disc.entries[i].pids, disc.entries[i].npids,
                                             affinity_pid_sample_limit, cpu_lists[i],
                                             sizeof(cpu_lists[i]), &cov);
      if (cpu_lists[i][0])
        cpu_ptrs[n_cpu_lists++] = cpu_lists[i];
    }
    job_gpu_set_diff(&available, &claimed, &remainder);
    rem_cpus[0] = '\0';
    (void)job_sched_affinity_remainder_cpulist(cpu_ptrs, n_cpu_lists, rem_cpus, sizeof(rem_cpus));

    for (i = 0; i < disc.n; i++) {
      struct job_gpu_set granted;
      struct job_gpu_set in_use;
      struct job_gpu_set g_union;
      char jobs_mark[256];
      char aff_mark[768];
      char gpus_wire[256];
      struct job_emit_filter_ctx ctx;

      memset(&g_union, 0, sizeof(g_union));
      if (job_emit_entry_emits_affinity(&disc.entries[i])) {
        job_gpu_assign_map_grant(disc.entries[i].job_gpus, &granted);
        job_gpu_assign_scan_fds(disc.entries[i].pids, disc.entries[i].npids, &in_use);
        job_gpu_set_union(&granted, &in_use, &g_union);
      }
      job_emit_multi_ctx(&disc.entries[i], &g_union, cpu_lists[i], &ctx);
      snprintf(jobs_mark, sizeof(jobs_mark), "jobs jids=%s", jobs_csv);
      job_gpu_set_format_wire(&g_union, gpus_wire, sizeof(gpus_wire));
      if (cpu_lists[i][0])
        snprintf(aff_mark, sizeof(aff_mark), "affinity jid=%s cpus=%s gpus=%s cg=sched",
                 disc.entries[i].jid, cpu_lists[i], gpus_wire);
      else
        aff_mark[0] = '\0';
      stats_buffer_set_row_filter(job_emit_row_filter, &ctx);
      insert_serialized(w, disc.entries[i].jid, jobs_mark, aff_mark[0] ? aff_mark : NULL, 1,
                        &burst_tier);
    }
    {
      struct job_emit_filter_ctx ctx = {.gpus = &remainder,
                                        .cpulist = rem_cpus,
                                        .include_node_wide = 1,
                                        .include_proc = 0,
                                        .include_gpu = available.inventory_known ? 1 : 0};

      stats_buffer_set_row_filter(job_emit_row_filter, &ctx);
      /* Do not mirror: would clobber last jid sample in DEBUG shm fast/full. */
      insert_serialized(w, "-", NULL, NULL, 0, &burst_tier);
    }
  }

done:
  stats_buffer_debug_shm_burst_commit(burst_tier);
  stats_buffer_set_row_filter(NULL, NULL);
  stats_buffer_set_frozen_time(NULL);
  job_proc_discover_result_clear(&disc);
}
