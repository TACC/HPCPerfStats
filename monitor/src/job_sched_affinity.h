/* Read-only sched_getaffinity union for %affinity cpus= (shared-node build). */
#ifndef JOB_SCHED_AFFINITY_H
#define JOB_SCHED_AFFINITY_H

#include <sched.h>
#include <stddef.h>
#include <sys/types.h>

enum job_cpu_coverage { JOB_CPU_UNKNOWN = 0, JOB_CPU_PARTIAL = 1, JOB_CPU_EXACT_ALL = 2 };

/*! Inject for tests; NULL restores sched_getaffinity. Never mutate affinity masks. */
typedef int (*job_sched_affinity_get_fn)(pid_t pid, cpu_set_t *set, size_t setsize);
void job_sched_affinity_set_inject(job_sched_affinity_get_fn fn);

/*! Read /sys/devices/system/cpu/online into a CPU_ALLOC mask. Returns 0 on success. */
int job_sched_affinity_read_online(cpu_set_t **out_set, size_t *out_size, size_t *out_ncpus);

/*! Union affinity of up to sample_limit lowest pids; format Linux cpulist into out.
 *  Sets *cov to EXACT_ALL only when union equals online set. */
int job_sched_affinity_union_cpulist(const int *pids, size_t npids, int sample_limit, char *out,
                                     size_t out_cap, enum job_cpu_coverage *cov);

/*! Return 1 if cpu_dev (decimal CPU index string) is a member of Linux cpulist. */
int job_sched_affinity_cpu_dev_in_list(const char *cpulist, const char *cpu_dev);

/*! Subtract claimed cpulists from online_list; write remainder cpulist to out.
 *  Pure string helper for tests; empty claimed -> copy of online_list. */
int job_sched_affinity_cpulist_subtract(const char *online_list, const char *const *claimed,
                                        size_t nclaimed, char *out, size_t out_cap);

/*! online CPUs minus union of claimed job cpulists -> out (node-wide remainder). */
int job_sched_affinity_remainder_cpulist(const char *const *claimed, size_t nclaimed, char *out,
                                         size_t out_cap);

#endif
