#ifndef _SYS_ITER_H_
#define _SYS_ITER_H_

/* Directory iteration for /sys/class/<x> and /sys/block style trees. Entries
 * starting with '.' are skipped; the callback filters the rest (e.g. "ram*"). */

typedef void (*sys_iter_cb_fn)(const char *base, const char *name, void *ctx);

/* Walk base (a directory under /sys, /proc, or similar). Records the path in
 * path_open_fail_once on opendir() failure to suppress repeated complaints.
 *
 * Returns 0 on a successful directory open (the callback may have run zero
 * or more times) or -1 if the directory could not be opened. */
int sys_iter_for_each(const char *base, sys_iter_cb_fn cb, void *ctx);

#endif
