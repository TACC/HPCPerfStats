"""
Lightweight process memory helpers for supervisor RSS guards.
"""

from __future__ import annotations

from typing import Any

from hpcperfstats.dbload.lib.multiprocessing_pool_health import iter_pool_worker_processes


def read_process_rss_bytes(pid: Any | None = None) -> Any:
  """
  Return resident set size in bytes from Linux ``/proc`` (0 if unknown).
  
  Args:
    pid (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Open return polymorphism from ``read_process_rss_bytes``: concrete
    type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> read_process_rss_bytes(None)  # doctest: +SKIP
  """
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


def _read_cgroup_memory_file(filename: str) -> Any:
  """
  Read a cgroup v2 memory file; return int bytes or None when unavailable.
  
  Args:
    filename (str): String for filename.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _read_cgroup_memory_file("x")  # doctest: +SKIP
  """
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


def read_cgroup_memory_current_bytes() -> Any:
  """
  Return cgroup ``memory.current`` in bytes (0 when unknown).
  
  Returns:
    Any: Open return polymorphism from ``read_cgroup_memory_current_bytes``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> read_cgroup_memory_current_bytes()  # doctest: +SKIP
  """
  value = _read_cgroup_memory_file("memory.current")
  return int(value) if value is not None else 0


def read_cgroup_memory_max_bytes() -> Any:
  """
  Return cgroup ``memory.max`` in bytes (None when unlimited/unknown).
  
  Returns:
    Any: Open return polymorphism from ``read_cgroup_memory_max_bytes``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> read_cgroup_memory_max_bytes()  # doctest: +SKIP
  """
  return _read_cgroup_memory_file("memory.max")


def _read_cgroup_memory_events_raw() -> Any:
  """
  Return raw ``memory.events`` text (empty when unavailable).
  
  Returns:
    Any: Open return polymorphism from ``_read_cgroup_memory_events_raw``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> _read_cgroup_memory_events_raw()  # doctest: +SKIP
  """
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


def read_cgroup_memory_events() -> Any:
  """
  Parse cgroup v2 ``memory.events`` counters (empty dict when unavailable).
  
  Returns:
    Any: Open return polymorphism from ``read_cgroup_memory_events``: concrete
    type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> read_cgroup_memory_events()  # doctest: +SKIP
  """
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


def sum_pool_worker_rss_bytes(pool: Any) -> Any:
  """
  Sum ``VmRSS`` for alive workers in a multiprocessing pool.
  
  Args:
    pool (Any): Live handle (pool, client, or connection).
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> sum_pool_worker_rss_bytes(None)  # doctest: +SKIP
  """
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


def read_sync_timedb_tree_rss_bytes(ingest_pool: Any, archive_pool: Any) -> Any:
  """
  Supervisor RSS plus ingest/archive pool worker RSS.
  
  Args:
    ingest_pool (Any): Ingest pool passed to this helper.
    archive_pool (Any): Archive pool passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> read_sync_timedb_tree_rss_bytes(None, None)  # doctest: +SKIP
  """
  total = read_process_rss_bytes()
  total += sum_pool_worker_rss_bytes(ingest_pool)
  total += sum_pool_worker_rss_bytes(archive_pool)
  return total


def format_tree_rss_breakdown_mb(ingest_pool: Any, archive_pool: Any) -> Any:
  """
  Human-readable per-component RSS breakdown in MiB.
  
  Args:
    ingest_pool (Any): Ingest pool passed to this helper.
    archive_pool (Any): Archive pool passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> format_tree_rss_breakdown_mb(None, None)  # doctest: +SKIP
  """
  supervisor = read_process_rss_bytes()
  ingest = sum_pool_worker_rss_bytes(ingest_pool)
  archive = sum_pool_worker_rss_bytes(archive_pool)
  return {
      "supervisor_mb": supervisor / (1024.0 * 1024.0),
      "ingest_pool_mb": ingest / (1024.0 * 1024.0),
      "archive_pool_mb": archive / (1024.0 * 1024.0),
      "tree_total_mb": (supervisor + ingest + archive) / (1024.0 * 1024.0),
  }
