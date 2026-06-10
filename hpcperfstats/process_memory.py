"""Lightweight process memory helpers for supervisor RSS guards."""

from __future__ import annotations

from hpcperfstats.dbload.multiprocessing_pool_health import iter_pool_worker_processes


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


def _read_cgroup_memory_file(filename):
  """Read a cgroup v2 memory file; return int bytes or None when unavailable."""
  for path in (
      "/sys/fs/cgroup/%s" % filename,
      "/sys/fs/cgroup/memory/%s" % filename,
  ):
    try:
      with open(path, "r", encoding="utf-8") as fh:
        raw = fh.read().strip()
    except OSError:
      continue
    if raw in ("", "max"):
      return None
    try:
      return int(raw)
    except ValueError:
      return None
  return None


def read_cgroup_memory_current_bytes():
  """Return cgroup ``memory.current`` in bytes (0 when unknown)."""
  value = _read_cgroup_memory_file("memory.current")
  return int(value) if value is not None else 0


def read_cgroup_memory_max_bytes():
  """Return cgroup ``memory.max`` in bytes (None when unlimited/unknown)."""
  return _read_cgroup_memory_file("memory.max")


def _read_cgroup_memory_events_raw():
  """Return raw ``memory.events`` text (empty when unavailable)."""
  for path in (
      "/sys/fs/cgroup/memory.events",
      "/sys/fs/cgroup/memory/memory.events",
  ):
    try:
      with open(path, "r", encoding="utf-8") as fh:
        return fh.read()
    except OSError:
      continue
  return ""


def read_cgroup_memory_events():
  """Parse cgroup v2 ``memory.events`` counters (empty dict when unavailable)."""
  events = {}
  for line in _read_cgroup_memory_events_raw().splitlines():
    line = line.strip()
    if not line:
      continue
    parts = line.split()
    if len(parts) < 2:
      continue
    try:
      events[parts[0]] = int(parts[1])
    except ValueError:
      continue
  return events


def sum_pool_worker_rss_bytes(pool):
  """Sum ``VmRSS`` for alive workers in a multiprocessing pool."""
  total = 0
  for proc in iter_pool_worker_processes(pool):
    pid = getattr(proc, "pid", None)
    if pid is None:
      continue
    is_alive_fn = getattr(proc, "is_alive", None)
    if callable(is_alive_fn) and not is_alive_fn():
      continue
    total += read_process_rss_bytes(pid)
  return total


def read_sync_timedb_tree_rss_bytes(ingest_pool, db_writer_pool, archive_pool):
  """Supervisor RSS plus ingest/db-writer/archive pool worker RSS."""
  total = read_process_rss_bytes()
  total += sum_pool_worker_rss_bytes(ingest_pool)
  total += sum_pool_worker_rss_bytes(db_writer_pool)
  total += sum_pool_worker_rss_bytes(archive_pool)
  return total


def format_tree_rss_breakdown_mb(ingest_pool, db_writer_pool, archive_pool):
  """Human-readable per-component RSS breakdown in MiB."""
  supervisor = read_process_rss_bytes()
  ingest = sum_pool_worker_rss_bytes(ingest_pool)
  db_writer = sum_pool_worker_rss_bytes(db_writer_pool)
  archive = sum_pool_worker_rss_bytes(archive_pool)
  return {
      "supervisor_mb": supervisor / (1024.0 * 1024.0),
      "ingest_pool_mb": ingest / (1024.0 * 1024.0),
      "db_writer_pool_mb": db_writer / (1024.0 * 1024.0),
      "archive_pool_mb": archive / (1024.0 * 1024.0),
      "tree_total_mb": (supervisor + ingest + db_writer + archive) / (1024.0 * 1024.0),
  }
