#ifndef LUSTRE_LLITE_H_
#define LUSTRE_LLITE_H_

#include "stats.h"

/* Usable vfs_* opcode/byte keys + fs_* capacity gauges (sysfs). */
#define KEYS                                                                                       \
  X(vfs_read_ops, "E", ""), X(vfs_write_ops, "E", ""), X(vfs_read_bytes, "E,U=B", ""),             \
      X(vfs_write_bytes, "E,U=B", ""), X(vfs_direct_read_bytes, "E,U=B", ""),                      \
      X(vfs_direct_write_bytes, "E,U=B", ""), X(vfs_osc_read_bytes, "E,U=B", ""),                  \
      X(vfs_osc_write_bytes, "E,U=B", ""), X(vfs_dirty_page_hits, "E", ""),                        \
      X(vfs_dirty_page_misses, "E", ""), X(vfs_ioctl_ops, "E", ""), X(vfs_open_ops, "E", ""),      \
      X(vfs_close_ops, "E", ""), X(vfs_mmap_ops, "E", ""), X(vfs_seek_ops, "E", ""),               \
      X(vfs_fsync_ops, "E", ""), X(vfs_setattr_ops, "E", ""), X(vfs_truncate_ops, "E", ""),        \
      X(vfs_flock_ops, "E", ""), X(vfs_getattr_ops, "E", ""), X(vfs_statfs_ops, "E", ""),          \
      X(vfs_alloc_inode_ops, "E", ""), X(vfs_setxattr_ops, "E", ""), X(vfs_getxattr_ops, "E", ""), \
      X(vfs_listxattr_ops, "E", ""), X(vfs_removexattr_ops, "E", ""),                              \
      X(vfs_inode_permission_ops, "E", ""), X(vfs_readdir_ops, "E", ""),                           \
      X(vfs_create_ops, "E", ""), X(vfs_lookup_ops, "E", ""), X(vfs_link_ops, "E", ""),            \
      X(vfs_unlink_ops, "E", ""), X(vfs_symlink_ops, "E", ""), X(vfs_mkdir_ops, "E", ""),          \
      X(vfs_rmdir_ops, "E", ""), X(vfs_mknod_ops, "E", ""), X(vfs_rename_ops, "E", ""),            \
      X(fs_bytes_total, "U=B", ""), X(fs_bytes_free, "U=B", ""), X(fs_bytes_avail, "U=B", ""),     \
      X(fs_files_total, "", ""), X(fs_files_free, "", "")

#endif
