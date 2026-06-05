#ifndef _SYS_ITER_H_
#define _SYS_ITER_H_

/* Shared iteration helper for /sys/class/<x> and /sys/block style trees.
 *
 * Many TYPE drivers (block, net, ib family, opa, numa, llite, mdc)
 * repeated the same pattern:
 *
 *   dir = path_opendir_or_record_fail(base);
 *   if (dir == NULL) goto out;
 *   while ((ent = readdir(dir)) != NULL) {
 *     if (ent->d_name[0] == '.') continue;
 *     ... per-entry work using ent->d_name ...
 *   }
 *   closedir(dir);
 *
 * sys_iter_for_each centralises that loop. The callback decides whether to
 * skip an entry (e.g. ignore "ram*" / "loop*" devices). Entries whose name
 * starts with '.' are skipped automatically.
 */

typedef void (*sys_iter_cb_fn)(const char *base, const char *name, void *ctx);

/* Walk base (a directory under /sys, /proc, or similar). Records the path in
 * path_open_fail_once on opendir() failure to suppress repeated complaints.
 *
 * Returns 0 on a successful directory open (the callback may have run zero
 * or more times) or -1 if the directory could not be opened. */
int sys_iter_for_each(const char *base, sys_iter_cb_fn cb, void *ctx);

#endif
