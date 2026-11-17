#ifndef HOST_MEM_JOB_H_
#define HOST_MEM_JOB_H_

#include "job_cgroup_mem.h"
#include "stats.h"

/* Job-scoped cgroup memory (overlap subset of host_mem KEYS + mem_peak).
 * Not NUMA meminfo — see shared-node-affinity-monitor-contract. */
#define KEYS                                                                                       \
  X(mem_used, "U=KB", "cgroup memory.current at job scope"),                                       \
      X(mem_peak, "U=KB", "cgroup memory.peak at job scope"),                                      \
      X(anon_pages, "U=KB", "cgroup memory.stat anon"),                                            \
      X(file_pages, "U=KB", "cgroup memory.stat file"),                                            \
      X(mapped, "U=KB", "cgroup memory.stat file_mapped"),                                         \
      X(dirty, "U=KB", "cgroup memory.stat file_dirty"),                                           \
      X(writeback, "U=KB", "cgroup memory.stat file_writeback"),                                   \
      X(page_tables, "U=KB", "cgroup memory.stat pagetables")

/* Publish one job scope's cgroup memory into its host_mem_job row; mem_peak and memory.stat keys
 * not read are NOT_PRESENT, or the errno sentinel of an unreadable memory.stat. */
void host_mem_job_publish(struct stats *st, const struct job_cgroup_mem_kb *kb);

#endif
