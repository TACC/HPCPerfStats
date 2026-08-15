"""
BeeGFS client metadata events summed for IOPS (summary + job-detail FSIO peaks).

Canonical ``vfs_*_ops`` names from monitor ``beegfs_client.h`` KEYS. Kept in one
module so summary plots, tests, and FSIO peak logic stay aligned. Do not reuse
``LLITE_METADATA_IOPS_EVENTS`` — BeeGFS does not emit several Lustre-only ops
(for example ``vfs_mmap_ops``, ``vfs_fsync_ops``, xattr ops).

Attributes:
  BEEGFS_METADATA_IOPS_EVENTS: BeeGFS ``vfs_*_ops`` used for peak / avg IOPS.
  BEEGFS_READ_BYTES_EVENTS: Canonical BeeGFS read-byte counter event names.
  BEEGFS_WRITE_BYTES_EVENTS: Canonical BeeGFS write-byte counter event names.
"""
from __future__ import annotations

BEEGFS_METADATA_IOPS_EVENTS: tuple[str, ...] = (
    "vfs_open_ops",
    "vfs_close_ops",
    "vfs_getattr_ops",
    "vfs_setattr_ops",
    "vfs_truncate_ops",
    "vfs_readdir_ops",
    "vfs_create_ops",
    "vfs_mkdir_ops",
    "vfs_rmdir_ops",
    "vfs_rename_ops",
    "vfs_unlink_ops",
    "vfs_link_ops",
    "vfs_statfs_ops",
)

BEEGFS_READ_BYTES_EVENTS: tuple[str, ...] = ("vfs_read_bytes",)
BEEGFS_WRITE_BYTES_EVENTS: tuple[str, ...] = ("vfs_write_bytes",)
