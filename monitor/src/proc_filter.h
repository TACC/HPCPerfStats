/* Shared /proc pid filter for host_proc and shared-node jid discovery. */
#ifndef PROC_FILTER_H
#define PROC_FILTER_H

#include <dirent.h>

/*! Return 1 if dirent under proc_root is a non-root, non-denylisted process dir.
 *  proc_root must be an absolute path (e.g. "/proc"); no ".." allowed by callers. */
int proc_filter_pid_at_root(const char *proc_root, const struct dirent *dir);

/*! Return 1 if pw_name is a denylisted system account. NULL name -> 0 (do not skip). */
int proc_filter_is_denylisted_name(const char *pw_name);

#endif
