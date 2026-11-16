#ifndef PROC_STATUS_H_
#define PROC_STATUS_H_

#include <stddef.h>
#include "stats.h"

/* /proc/<pid>/status emits Uid, Vm*, and Threads before Cpus/Mems_allowed_list.
 * host_proc only creates the stats row after Name+masks, so those early fields
 * must be deferred (cap is the host_key_alias proc key count plus headroom). */
#define PROC_STATUS_PENDING_MAX 16

struct proc_status_pending_entry {
  char key[32];
  unsigned long long val;
};

struct proc_status_pending {
  struct proc_status_pending_entry e[PROC_STATUS_PENDING_MAX];
  unsigned n;
};

void proc_status_pending_init(struct proc_status_pending *p);

/* Push only when host_key_alias_lookup(key) is non-NULL. Returns 0 or -1 (full). */
int proc_status_pending_push(struct proc_status_pending *p, const char *kernel_key,
                             unsigned long long val);

void proc_status_pending_flush(struct proc_status_pending *p, struct stats *stats);

/*
 * key_with_colon is a status field name including trailing ':' (e.g. "Uid:").
 * If stats is ready, emit via host_key_alias; else defer aliased keys.
 */
void proc_status_emit_or_defer_kv(struct stats *stats, struct proc_status_pending *p,
                                  const char *key_with_colon, const char *rest);

/*
 * Return 1 if /proc/<pid>/status Name: should be omitted from host_proc.
 * Matches kernel TASK_COMM_LEN names (max 15 chars), e.g. nvidia-persiste.
 */
int proc_status_skip_process_name(const char *name);

/* host_proc device fields. Mask lists are sized for the kernel maxima so no process is
 * dropped for a long list: worst case is every other CPU of NR_CPUS=8192 (~19.9k chars)
 * and every other node of MAX_NUMNODES=1024 (~2k chars). */
#define PROC_STATUS_NAME_MAX 16 /* TASK_COMM_LEN */
#define PROC_STATUS_PID_MAX 32
#define PROC_STATUS_CPULIST_MAX 24576
#define PROC_STATUS_MEMLIST_MAX 4096
/* Three '/' separators and the NUL fit in the four fields' NUL slots. */
#define PROC_STATUS_DEVICE_MAX                                                                     \
  (PROC_STATUS_NAME_MAX + PROC_STATUS_PID_MAX + PROC_STATUS_CPULIST_MAX + PROC_STATUS_MEMLIST_MAX)

/*
 * Format the host_proc device token "name/pid/cpus/mems" (consumer proc_data.device is
 * an unbounded varchar). Returns 0, or -1 on NULL input or when it does not fit cap;
 * never returns a truncated token.
 */
int proc_status_format_device(char *out, size_t cap, const char *name, const char *pid,
                              const char *cpus, const char *mems);

#endif
