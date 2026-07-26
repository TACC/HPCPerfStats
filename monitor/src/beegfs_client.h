#ifndef BEEGFS_CLIENT_H_
#define BEEGFS_CLIENT_H_

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

#endif
