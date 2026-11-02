/* Discover live Slurm job ids from /proc (shared-node multi-jid build). */
#ifndef JOB_PROC_DISCOVER_H
#define JOB_PROC_DISCOVER_H

#include <stddef.h>
#include <sys/types.h>

#define JOB_PROC_JID_MAX 32
#define JOB_PROC_GPUS_ENV_MAX 128
#define JOB_PROC_MAX_JIDS 128
#define JOB_PROC_CGROUP_RELPATH_MAX 512

struct job_proc_jid_entry {
  char jid[JOB_PROC_JID_MAX + 1];
  char job_gpus[JOB_PROC_GPUS_ENV_MAX]; /* raw SLURM_JOB_GPUS or empty */
  /* Path after controllers in /proc/<pid>/cgroup (e.g. /system.slice/slurmstepd.scope/TOKEN/...).
   * Empty on environ-only fallback. Never emitted on the wire. */
  char cgroup_relpath[JOB_PROC_CGROUP_RELPATH_MAX];
  int *pids;
  size_t npids;
  /* 1 when no non-root pid carried SLURM_JOB_ID and the jid came from the root
   * slurmstepd process title (idle allocation: only the extern step is left).
   * Such entries never emit %affinity (no SLURM_JOB_GPUS, stepd affinity is not the grant). */
  int named_from_title;
};

struct job_proc_discover_result {
  struct job_proc_jid_entry entries[JOB_PROC_MAX_JIDS];
  size_t n;
};

/*! Absolute proc root for tests; production uses /proc.
 *  Ignored unless absolute and free of ".." path segments. */
const char *job_proc_discover_proc_root(void);

/*! Validate jid token for host_data.jid (≤32, not 0/-/blank/whitespace). */
int job_proc_jid_acceptable(const char *jid);

/*! Parse the jid from a slurmstepd process title (/proc/<pid>/cmdline bytes):
 *  "slurmstepd: [<jid>.<step>]" or "slurmstepd: [<jid>:<name>]" -> "<jid>".
 *  jid must be all digits and pass job_proc_jid_acceptable.
 *  Returns 0 and writes jid_out on success, -1 otherwise (jid_out unspecified). */
int job_proc_jid_from_stepd_title(const char *title, size_t len, char *jid_out, size_t jid_cap);

/*! Scan proc root; fill sorted unique jid entries with pids.
 *  Name sources, in order: SLURM_JOB_ID in a non-root pid's environ, then (for scope
 *  tokens still unnamed) the root slurmstepd title of <cgroup_root>/system.slice/
 *  slurmstepd.scope/<token>/step_extern/slurm/cgroup.procs (named_from_title=1).
 *  require_step_id: drop env-named groups with no SLURM_STEP_ID on any non-root pid
 *  (title-named groups are allocation presence and are kept).
 *  pid_cap: max pids walked (default 4096). Fail-open: on scandir failure leave *out unchanged.
 *  Returns 0 on success (including empty J), -1 on hard alloc failure. */
int job_proc_discover(struct job_proc_discover_result *out, int require_step_id, int pid_cap);

void job_proc_discover_result_clear(struct job_proc_discover_result *r);

#endif
