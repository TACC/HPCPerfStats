/* Map SLURM_JOB_GPUS / open GPU fds to collector nvidia_gpu/amd_gpu/intel_gpu dev. */
#ifndef JOB_GPU_ASSIGN_H
#define JOB_GPU_ASSIGN_H

#include <stddef.h>

enum job_gpu_autodetect {
  JOB_GPU_AD_OFF = 0,
  JOB_GPU_AD_NVML,
  JOB_GPU_AD_NVIDIA,
  JOB_GPU_AD_RSMI,
  JOB_GPU_AD_ONEAPI,
  JOB_GPU_AD_FULL,
  JOB_GPU_AD_UNKNOWN
};

#define JOB_GPU_DEV_MAX 64
#define JOB_GPU_DEV_STR 16

struct job_gpu_set {
  char devs[JOB_GPU_DEV_MAX][JOB_GPU_DEV_STR];
  size_t n;
  int inventory_known; /* 1 after successful enumeration (incl. known-empty) */
};

/*! Parse AutoDetect from gres.conf (SLURM_CONF dir or /etc/slurm/gres.conf). Cached. */
enum job_gpu_autodetect job_gpu_assign_autodetect(void);

/*! Test override for gres.conf path; NULL clears. */
void job_gpu_assign_set_gres_conf_path(const char *path);

/*! Clear AutoDetect cache (tests). */
void job_gpu_assign_reset_autodetect_cache(void);

/*! Map grant string (SLURM_JOB_GPUS) into collector dev strings for G_granted. */
int job_gpu_assign_map_grant(const char *slurm_job_gpus, struct job_gpu_set *granted);

/*! Walk /proc/<pid>/fd for in-use GPU nodes; merge into *in_use. */
int job_gpu_assign_scan_fds(const int *pids, size_t npids, struct job_gpu_set *in_use);

/*! A job's GPU set G(jid): its grant; the GPU fds its pids hold open only when it has no grant
 * (devices are not fenced, so a probe of another job's GPU must not move that GPU). */
int job_gpu_assign_job_set(const char *slurm_job_gpus, const int *pids, size_t npids,
                           struct job_gpu_set *out);

/*! Union granted and in_use into *out (serialization filter G(jid)). */
void job_gpu_set_union(const struct job_gpu_set *a, const struct job_gpu_set *b,
                       struct job_gpu_set *out);

/*! Set difference: universe devices not in subtract -> *out (unscheduled GPUs). */
void job_gpu_set_diff(const struct job_gpu_set *universe, const struct job_gpu_set *subtract,
                      struct job_gpu_set *out);

/*! Format gpus= csv or "-" into out. */
void job_gpu_set_format_wire(const struct job_gpu_set *set, char *out, size_t out_cap);

/*! Return 1 if every granted dev is in available and sizes match (exact full grant). */
int job_gpu_set_exact_equal(const struct job_gpu_set *granted, const struct job_gpu_set *available);

/*! Test inject: map NVML index V -> collector i, and minor N -> i. -1 = unmapped. */
typedef int (*job_gpu_nvml_index_fn)(int nvml_index);
typedef int (*job_gpu_minor_fn)(int minor);
void job_gpu_assign_set_nvml_hooks(job_gpu_nvml_index_fn idx_fn, job_gpu_minor_fn minor_fn);

#endif
