#ifndef BEEGFS_CLIENT_H_
#define BEEGFS_CLIENT_H_

#include "beegfs_tcp.h"
#include "stats.h"

/* BeeGFS client metrics: vfs_*_bytes from the TCP counters of the client's storage connections
 * (vfs_*_ops NOT_PRESENT: the client keeps no op counters) + fs_* capacity (statvfs).
 * Device id is the mount path (e.g. /scratch). */
#define KEYS                                                                                       \
  X(vfs_read_ops, "E", ""), X(vfs_write_ops, "E", ""), X(vfs_read_bytes, "E,U=B", ""),             \
      X(vfs_write_bytes, "E,U=B", ""), X(vfs_open_ops, "E", ""), X(vfs_close_ops, "E", ""),        \
      X(vfs_getattr_ops, "E", ""), X(vfs_setattr_ops, "E", ""), X(vfs_truncate_ops, "E", ""),      \
      X(vfs_readdir_ops, "E", ""), X(vfs_create_ops, "E", ""), X(vfs_mkdir_ops, "E", ""),          \
      X(vfs_rmdir_ops, "E", ""), X(vfs_rename_ops, "E", ""), X(vfs_unlink_ops, "E", ""),           \
      X(vfs_link_ops, "E", ""), X(vfs_statfs_ops, "E", ""), X(fs_bytes_total, "U=B", ""),          \
      X(fs_bytes_free, "U=B", ""), X(fs_bytes_avail, "U=B", ""), X(fs_files_total, "", ""),        \
      X(fs_files_free, "", "")

/* Publishes the I/O keys of mount row `stats`: with code OK, adds the growth of `socks` (this
 * refresh's storage connections) to the mount's totals; any other code goes on the byte keys.
 * Caches the outcome for beegfs_republish_io. */
void beegfs_publish_io(struct stats *stats, const struct beegfs_tcp_sock *socks, size_t n,
                       enum stats_sentinel code);
/* On a tick the I/O refresh gate skips: publishes the mount's cached totals or code again
 * (nothing when the mount was never refreshed). */
void beegfs_republish_io(struct stats *stats);
/* Test seam: read mounts from `path` instead of /proc/mounts (NULL or "" restores it). */
void beegfs_test_set_mounts_path(const char *path);
/* Test seam: read client instances from `path` instead of /proc/fs/beegfs (NULL or "" restores
 * it). Like the mounts seam it keeps the pointer, so `path` must outlive the collects. */
void beegfs_test_set_procfs_root(const char *path);

#endif
