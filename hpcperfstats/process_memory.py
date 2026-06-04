"""Lightweight process memory helpers for supervisor RSS guards."""

from __future__ import annotations


def read_process_rss_bytes(pid=None):
  """Return resident set size in bytes from Linux ``/proc`` (0 if unknown)."""
  proc_pid = "self" if pid is None else int(pid)
  status_path = "/proc/%s/status" % proc_pid
  try:
    with open(status_path, "r", encoding="utf-8") as fh:
      for line in fh:
        if line.startswith("VmRSS:"):
          parts = line.split()
          if len(parts) >= 2:
            return int(parts[1]) * 1024
          break
  except (OSError, ValueError):
    return 0
  return 0
