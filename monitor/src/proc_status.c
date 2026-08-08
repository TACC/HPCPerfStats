/* Deferred /proc/<pid>/status field emit for host_proc (Uid, Vm*, Threads). */
#include <errno.h>
#include <stdlib.h>
#include <string.h>

#include "host_key_alias.h"
#include "proc_status.h"

void proc_status_pending_init(struct proc_status_pending *p)
{
  if (p == NULL)
    return;
  p->n = 0;
}

int proc_status_pending_push(struct proc_status_pending *p, const char *kernel_key,
                             unsigned long long val)
{
  size_t len;

  if (p == NULL || kernel_key == NULL || kernel_key[0] == '\0')
    return -1;
  if (host_key_alias_lookup(kernel_key) == NULL)
    return -1;
  if (p->n >= PROC_STATUS_PENDING_MAX)
    return -1;
  len = strlen(kernel_key);
  if (len >= sizeof(p->e[0].key))
    return -1;
  memcpy(p->e[p->n].key, kernel_key, len + 1);
  p->e[p->n].val = val;
  p->n++;
  return 0;
}

void proc_status_pending_flush(struct proc_status_pending *p, struct stats *stats)
{
  unsigned i;

  if (p == NULL || stats == NULL)
    return;
  for (i = 0; i < p->n; i++)
    host_key_alias_emit(stats, p->e[i].key, p->e[i].val);
  p->n = 0;
}

void proc_status_emit_or_defer_kv(struct stats *stats, struct proc_status_pending *p,
                                  const char *key_with_colon, const char *rest)
{
  char key[64];
  size_t len;
  unsigned long long val;
  int saved_errno;

  if (p == NULL || key_with_colon == NULL || rest == NULL)
    return;
  len = strlen(key_with_colon);
  if (len < 2 || len >= sizeof(key) || key_with_colon[len - 1] != ':')
    return;
  memcpy(key, key_with_colon, len - 1);
  key[len - 1] = '\0';

  if (host_key_alias_lookup(key) == NULL)
    return;

  saved_errno = errno;
  errno = 0;
  val = strtoull(rest, NULL, 0);
  if (errno != 0) {
    errno = saved_errno;
    return;
  }
  errno = saved_errno;

  if (stats != NULL)
    host_key_alias_emit(stats, key, val);
  else
    (void)proc_status_pending_push(p, key, val);
}

int proc_status_skip_process_name(const char *name)
{
  /* Exact match on /proc/<pid>/status Name: (kernel TASK_COMM_LEN, ≤15 chars). */
  static const char *const deny[] = {
      "bash",
      "ssh",
      "sshd",
      "sshd-session",
      "metacity",
      "(sd-pam)",
      "chronyd",
      "fwupdmgr",
      "munged",
      "systemd",
      "polkitd",
      /* nvidia-persistenced truncated by kernel to 15 chars */
      "nvidia-persiste",
      "nv-hostengine",
      "sssd_kcm",
      "nvidia-smi",
  };
  size_t i;

  if (name == NULL)
    return 1;
  for (i = 0; i < sizeof(deny) / sizeof(deny[0]); i++) {
    if (strcmp(name, deny[i]) == 0)
      return 1;
  }
  return 0;
}
