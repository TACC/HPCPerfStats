/* Per-payload row filter for shared-node multi-jid emit (linkable for unit tests). */
#ifndef JOB_EMIT_FILTER_H
#define JOB_EMIT_FILTER_H

#include "job_gpu_assign.h"
#include "job_proc_discover.h"
#include "job_sched_affinity.h"

struct job_emit_filter_ctx {
  const struct job_proc_jid_entry *entry; /* NULL = node-wide only (no proc/gpu) */
  const struct job_gpu_set *gpus;         /* NULL = no gpu filter (emit none for gpu types) */
  const char *cpulist; /* non-NULL: filter host_cpu/host_cpu_hw; NULL: use include_node_wide */
  int include_node_wide;
  int include_proc;
  int include_gpu;
};

/*! stats_buffer row filter: ctx is a struct job_emit_filter_ctx (NULL keeps every row).
 *  Returns 1 to keep the (st_name, dev) row in the payload being serialized. */
int job_emit_row_filter(const char *st_name, const char *dev, void *ctx);

/*! Sole discovered jid: fill *out and return 1 when the payload is unmarked (no %jobs /
 *  %affinity) and owns every available GPU: a title-named entry (idle allocation), or a job
 *  holding every online CPU of known GPU inventory (Slurm cannot start a second job on a node
 *  with no free CPU, so its GPU grant does not matter). Returns 0 for the marked path (GPU
 *  filter = g_union; the caller then emits the per-jid / '-' remainder split).
 *  NULL entry, available or out → 0. */
int job_emit_sole_ctx(const struct job_proc_jid_entry *e, enum job_cpu_coverage cov,
                      const struct job_gpu_set *g_union, const struct job_gpu_set *available,
                      struct job_emit_filter_ctx *out);

/*! |J|>=1 marked jid payload filter. Title-named entries keep only their host_mem_job device
 *  (no CPU, GPU, proc or node-wide rows); env-named entries keep their CPU/GPU/proc split. */
void job_emit_multi_ctx(const struct job_proc_jid_entry *e, const struct job_gpu_set *g_union,
                        const char *cpulist, struct job_emit_filter_ctx *out);

/*! Node-wide '-' payload filter for a marked tick: unscheduled GPUs (omitted when the GPU
 *  inventory is unknown), the online CPUs no job claimed, every node-wide row, no proc or
 *  host_mem_job rows. NULL out is a no-op. */
void job_emit_remainder_ctx(const struct job_gpu_set *remainder, const char *rem_cpus,
                            int inventory_known, struct job_emit_filter_ctx *out);

/*! 1 when the entry may carry %affinity and claim CPUs/GPUs (env-named), 0 otherwise. */
int job_emit_entry_emits_affinity(const struct job_proc_jid_entry *e);

/*! The job's CPU allocation as a Linux cpulist in out, with *cov set like
 *  job_sched_affinity_union_cpulist. Reads the extern-step slurmstepd affinity (the job cpuset)
 *  when e->stepd_pid is known, else the union over up to sample_limit task pids (MPI binding
 *  can narrow those to one core each). Returns 0, or -1 when neither source is readable. */
int job_emit_entry_cpulist(const struct job_proc_jid_entry *e, int sample_limit, char *out,
                           size_t out_cap, enum job_cpu_coverage *cov);

#endif
