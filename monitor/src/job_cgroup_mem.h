/* Read job-scope cgroup v2 memory at slurmstepd.scope/<token>/ (shared-node). */
#ifndef JOB_CGROUP_MEM_H
#define JOB_CGROUP_MEM_H

#include <stddef.h>

struct job_cgroup_mem_kb {
  unsigned long long mem_used;
  unsigned long long mem_peak;
  unsigned long long anon_pages;
  unsigned long long file_pages;
  unsigned long long mapped;
  unsigned long long dirty;
  unsigned long long writeback;
  unsigned long long page_tables;
  int has_peak;              /* memory.peak read (absent before kernel 5.19 unless backported) */
  unsigned int stat_present; /* JOB_CGROUP_MEM_STAT_* bits of the memory.stat keys parsed */
  int stat_errno;            /* memory.stat read errno, 0 when it was read */
};

/* stat_present bits; bit i is g_stat_fields[i] in job_cgroup_mem.c. */
enum {
  JOB_CGROUP_MEM_STAT_ANON = 1u << 0,
  JOB_CGROUP_MEM_STAT_FILE = 1u << 1,
  JOB_CGROUP_MEM_STAT_MAPPED = 1u << 2,
  JOB_CGROUP_MEM_STAT_DIRTY = 1u << 3,
  JOB_CGROUP_MEM_STAT_WRITEBACK = 1u << 4,
  JOB_CGROUP_MEM_STAT_PAGE_TABLES = 1u << 5,
};

/*! Absolute cgroup fs root for tests; production uses /sys/fs/cgroup.
 *  Ignored unless absolute and free of ".." path segments. */
const char *job_cgroup_mem_cgroup_root(void);

/*! Resolve leaf cgroup_relpath to slurmstepd.scope/<token>/ and read
 *  memory.current (required), memory.peak, and memory.stat overlap fields.
 *  Bytes converted to KB (/1024). Missing memory.peak -> has_peak 0; each stat key parsed sets
 *  its stat_present bit; an unreadable memory.stat sets stat_errno.
 *  Never reads task leaf or bare slurmstepd.scope aggregate.
 *  Returns 0 on success, -1 fail-open. */
int job_cgroup_mem_read(const char *cgroup_relpath, struct job_cgroup_mem_kb *out);

/*! Format host_mem_job device as j:<jid>. Returns 0 on success, -1 on error. */
int job_cgroup_mem_format_dev(const char *jid, char *out, size_t out_cap);

/*! Emit filter: keep host_mem_job when jid non-NULL and dev equals j:<jid>.
 *  NULL jid (node-wide "-" payload) always omits. */
int job_cgroup_mem_keep_dev(const char *jid, const char *dev);

#endif
