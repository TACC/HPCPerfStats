#ifndef PROC_STATUS_H_
#define PROC_STATUS_H_

#include <stddef.h>
#include "stats.h"

/* /proc/<pid>/status emits Uid, Vm*, and Threads before Cpus/Mems_allowed_list.
 * host_proc only creates the stats row after Name+masks, so those early fields
 * must be deferred (cap matches KEYS count with headroom). */
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

#endif
