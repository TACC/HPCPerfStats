"""Lustre llite metadata events summed for client-side IOPS (summary + job-detail FSIO peaks).

Kept in one module so summary plots, tests, and FSIO peak logic stay aligned.
"""
from __future__ import annotations

LLITE_METADATA_IOPS_EVENTS: tuple[str, ...] = (
    "open",
    "close",
    "mmap",
    "fsync",
    "setattr",
    "truncate",
    "flock",
    "getattr",
    "statfs",
    "alloc_inode",
    "setxattr",
    "listxattr",
    "removexattr",
    "readdir",
    "create",
    "lookup",
    "link",
    "unlink",
    "symlink",
    "mkdir",
    "rmdir",
    "mknod",
    "rename",
)
