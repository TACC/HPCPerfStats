#ifndef BEEGFS_CLIENT_H_
#define BEEGFS_CLIENT_H_

#include "beegfs_ctl_parse.h"
#include "stats.h"

/* BeeGFS client metrics: vfs_* I/O/metadata (beegfs-ctl) + fs_* capacity (statvfs).
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

/* Publishes the beegfs-ctl keys of mount row `stats` from the selected local clientstats lines
 * (NULL: that node type gave no local line; both NULL: BACKEND_ERROR) and caches them for
 * beegfs_republish_ctl. */
void beegfs_publish_ctl(struct stats *stats, const struct beegfs_ctl_counters *storage,
                        const struct beegfs_ctl_counters *meta);
/* On a tick the ctl refresh gate skips: publishes the mount's cached values and codes again
 * (nothing when the mount was never refreshed). */
void beegfs_republish_ctl(struct stats *stats);
/* Test seam: read mounts from `path` instead of /proc/mounts (NULL or "" restores it). */
void beegfs_test_set_mounts_path(const char *path);

#endif
