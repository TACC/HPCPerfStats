"""
Lustre llite metadata events summed for client-side IOPS (summary + job-detail
FSIO peaks).

Canonical vfs_*_ops names (monitor lustre_llite KEYS). Dual-read via
``events_probe_names(..., typ=lustre_llite)`` expands legacy proc opcode names
for historical archives. Kept in one module so summary plots, tests, and FSIO
peak logic stay aligned.

Attributes:
  LLITE_METADATA_IOPS_EVENTS: Attribute.
  LLITE_READ_BYTES_EVENTS: Attribute.
  LLITE_WRITE_BYTES_EVENTS: Attribute.
"""
from __future__ import annotations

LLITE_METADATA_IOPS_EVENTS: tuple[str, ...] = (
    "vfs_open_ops",
    "vfs_close_ops",
    "vfs_mmap_ops",
    "vfs_fsync_ops",
    "vfs_setattr_ops",
    "vfs_truncate_ops",
    "vfs_flock_ops",
    "vfs_getattr_ops",
    "vfs_statfs_ops",
    "vfs_alloc_inode_ops",
    "vfs_setxattr_ops",
    "vfs_listxattr_ops",
    "vfs_removexattr_ops",
    "vfs_readdir_ops",
    "vfs_create_ops",
    "vfs_lookup_ops",
    "vfs_link_ops",
    "vfs_unlink_ops",
    "vfs_symlink_ops",
    "vfs_mkdir_ops",
    "vfs_rmdir_ops",
    "vfs_mknod_ops",
    "vfs_rename_ops",
)

LLITE_READ_BYTES_EVENTS: tuple[str, ...] = ("vfs_read_bytes",)
LLITE_WRITE_BYTES_EVENTS: tuple[str, ...] = ("vfs_write_bytes",)
