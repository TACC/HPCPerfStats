"""
Lightweight process memory helpers for supervisor RSS guards.

Attributes:
  _MIB: Bytes in one mebibyte.
  _MEMORY_STAT_MIB_KEYS: ``memory.stat`` keys converted to MiB for telemetry.
  _MEMORY_STAT_COUNT_KEYS: ``memory.stat`` counter keys left as integers.
"""

from __future__ import annotations

import os
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


def effective_process_tree_rss_mib_from_cgroup_pct(pct: Any) -> int:
  """
  Return process-tree roof/exit MiB from ``memory.max`` × ``pct`` (0–100).

  When ``pct <= 0`` or ``memory.max`` is unknown, return ``0`` (fail open).

  Args:
    pct (Any): Percentage of cgroup ``memory.max``.

  Returns:
    int: Effective MiB (integer floor).

  Examples:
    >>> effective_process_tree_rss_mib_from_cgroup_pct(0)
    0
  """
  try:
    pct_i = int(pct or 0)
  except (TypeError, ValueError):
    pct_i = 0
  if pct_i <= 0:
    return 0
  pct_i = min(100, max(0, pct_i))
  cgroup_max = read_cgroup_memory_max_bytes()
  if cgroup_max is None:
    return 0
  max_b = int(cgroup_max)
  if max_b <= 0:
    return 0
  return (max_b * pct_i // 100) // _MIB


def effective_process_tree_rss_limit_mib() -> int:
  """
  Effective defer roof MiB from INI ``sync_process_tree_rss_limit_cgroup_pct``.

  Returns:
    int: MiB from runtime cgroup max × pct, or ``0`` when disabled.

  Examples:
    >>> effective_process_tree_rss_limit_mib()  # doctest: +SKIP
  """
  import hpcperfstats.dbload.lib.conf_parser as cfg

  return effective_process_tree_rss_mib_from_cgroup_pct(
      cfg.get_sync_process_tree_rss_limit_cgroup_pct(),
  )


def effective_cgroup_admit_max_file_cache_mib() -> int:
  """
  Effective file-cache admit cap MiB from INI cgroup pct × ``memory.max``.

  Returns:
    int: MiB from runtime cgroup max × pct, or ``0`` when disabled.

  Examples:
    >>> effective_cgroup_admit_max_file_cache_mib()  # doctest: +SKIP
  """
  import hpcperfstats.dbload.lib.conf_parser as cfg

  return effective_process_tree_rss_mib_from_cgroup_pct(
      cfg.get_sync_cgroup_admit_max_file_cache_cgroup_pct(),
  )


def effective_process_tree_rss_exit_mib() -> int:
  """
  Effective hard-exit MiB from INI ``sync_process_tree_rss_exit_cgroup_pct``.

  Returns:
    int: MiB from runtime cgroup max × pct, or ``0`` when disabled.

  Examples:
    >>> effective_process_tree_rss_exit_mib()  # doctest: +SKIP
  """
  import hpcperfstats.dbload.lib.conf_parser as cfg

  return effective_process_tree_rss_mib_from_cgroup_pct(
      cfg.get_sync_process_tree_rss_exit_cgroup_pct(),
  )


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


_MIB = 1024 * 1024

# memory.stat keys emitted by sync_timedb_mem_telemetry (bytes → MiB except faults).
_MEMORY_STAT_MIB_KEYS = (
    "anon",
    "file",
    "file_mapped",
    "shmem",
    "active_anon",
    "inactive_anon",
    "active_file",
    "inactive_file",
    "unevictable",
    "slab",
)
_MEMORY_STAT_COUNT_KEYS = ("pgfault", "pgmajfault")


def read_cgroup_memory_peak_bytes() -> Any:
  """
  Return cgroup ``memory.peak`` in bytes (0 when unknown).

  Returns:
    Any: Peak bytes or 0.

  Examples:
    >>> read_cgroup_memory_peak_bytes()  # doctest: +SKIP
  """
  value = _read_cgroup_memory_file("memory.peak")
  return int(value) if value is not None else 0


def read_cgroup_memory_stat() -> Any:
  """
  Parse cgroup ``memory.stat`` into a dict of int counters (empty if unavailable).

  Returns:
    Any: Mapping of key → int.

  Examples:
    >>> isinstance(read_cgroup_memory_stat(), dict)
    True
  """
  for path in (
      "/sys/fs/cgroup/memory.stat",
      "/sys/fs/cgroup/memory/memory.stat",
  ):
    try:
      with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    except OSError:
      continue
    out: dict[str, int] = {}
    for line in text.splitlines():
      parts = line.split()
      if len(parts) < 2:
        continue
      try:
        out[parts[0]] = int(parts[1])
      except ValueError:
        continue
    return out
  return {}


def cgroup_admit_headroom_ok(headroom_mib: Any) -> bool:
  """
  Return True when multi-file admit / append may proceed under cgroup headroom.

  When ``headroom_mib <= 0`` or ``memory.max`` is unknown, the gate is off
  (fail open). When current and max are readable, require
  ``(max - current) >= headroom_mib`` MiB.

  Args:
    headroom_mib (Any): Required free MiB under ``memory.max``.

  Returns:
    bool: True when admit/append may proceed.

  Examples:
    >>> cgroup_admit_headroom_ok(0)
    True
  """
  need = max(0, int(headroom_mib or 0))
  if need <= 0:
    return True
  cgroup_max = read_cgroup_memory_max_bytes()
  if cgroup_max is None or int(cgroup_max) <= 0:
    return True
  current = int(read_cgroup_memory_current_bytes() or 0)
  left = int(cgroup_max) - current
  return left >= (need * _MIB)


def cgroup_admit_file_cache_ok(max_file_mib: Any) -> bool:
  """
  Return True when multi-file admit / append may proceed under file page cache.

  When ``max_file_mib <= 0`` or ``memory.stat`` ``file`` is unavailable, the
  gate is off (fail open). Otherwise require ``file <= max_file_mib`` MiB.

  Args:
    max_file_mib (Any): Maximum cgroup ``memory.stat`` ``file`` in MiB.

  Returns:
    bool: True when admit/append may proceed.

  Examples:
    >>> cgroup_admit_file_cache_ok(0)
    True
  """
  cap = max(0, int(max_file_mib or 0))
  if cap <= 0:
    return True
  stat = read_cgroup_memory_stat()
  file_bytes = int(stat.get("file", 0) or 0)
  return file_bytes <= (cap * _MIB)


def read_process_nlwp(pid: Any | None = None) -> int:
  """
  Return thread count from ``/proc/<pid>/status`` Threads (0 if unknown).

  Args:
    pid (Any | None): Target pid or None for self.

  Returns:
    int: Thread count.

  Examples:
    >>> read_process_nlwp(None)  # doctest: +SKIP
  """
  proc_pid = "self" if pid is None else int(pid)
  status_path = "/proc/%s/status" % proc_pid
  try:
    with open(status_path, "r", encoding="utf-8") as fh:
      for line in fh:
        if line.startswith("Threads:"):
          parts = line.split()
          if len(parts) >= 2:
            return int(parts[1])
  except (OSError, ValueError):
    return 0
  return 0


def _cmdline_of(pid: int) -> str:
  """
  Return space-joined ``/proc/<pid>/cmdline`` (empty on error).

  Args:
    pid (int): Target process id.

  Returns:
    str: Cmdline text.

  Examples:
    >>> isinstance(_cmdline_of(1), str)
    True
  """
  try:
    with open("/proc/%d/cmdline" % pid, "rb") as fh:
      raw = fh.read().replace(b"\x00", b" ").decode("utf-8", "replace")
    return raw.strip()
  except OSError:
    return ""


def read_daemon_rss_by_cmdline() -> dict[str, int]:
  """
  Sum VmRSS bytes for listend / update_metrics / sync_timedb by cmdline match.

  Returns:
    dict[str, int]: Keys ``sync``, ``listend``, ``metrics`` → RSS bytes.

  Examples:
    >>> sorted(read_daemon_rss_by_cmdline().keys())
    ['listend', 'metrics', 'sync']
  """
  out = {"sync": 0, "listend": 0, "metrics": 0}
  try:
    pids = [int(name) for name in os.listdir("/proc") if name.isdigit()]
  except OSError:
    return out
  for pid in pids:
    cmd = _cmdline_of(pid)
    if not cmd:
      continue
    low = cmd.lower()
    if "sync_timedb" in low:
      out["sync"] += read_process_rss_bytes(pid)
    elif "listend" in low:
      out["listend"] += read_process_rss_bytes(pid)
    elif "update_metrics" in low:
      out["metrics"] += read_process_rss_bytes(pid)
  return out


def read_other_cgroup_rss(
  exclude_pids: set[int] | None = None,
) -> dict[str, Any]:
  """
  Sum RSS of PIDs under this cgroup excluding known daemons; note top ≥1 GiB.

  Args:
    exclude_pids (set[int] | None): PIDs to skip (daemon mains).

  Returns:
    dict[str, Any]: ``other_rss_bytes``, ``other_top`` (basename or empty).

  Examples:
    >>> "other_rss_bytes" in read_other_cgroup_rss()
    True
  """
  exclude = set(exclude_pids or ())
  # Prefer cgroup.procs; fall back to all /proc pids.
  proc_list: list[int] = []
  for path in (
      "/sys/fs/cgroup/cgroup.procs",
      "/sys/fs/cgroup/memory/cgroup.procs",
  ):
    try:
      with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
          line = line.strip()
          if line.isdigit():
            proc_list.append(int(line))
      break
    except OSError:
      continue
  if not proc_list:
    try:
      proc_list = [int(n) for n in os.listdir("/proc") if n.isdigit()]
    except OSError:
      return {"other_rss_bytes": 0, "other_top": ""}
  other = 0
  top_name = ""
  top_rss = 0
  for pid in proc_list:
    if pid in exclude:
      continue
    cmd = _cmdline_of(pid)
    low = cmd.lower()
    if "sync_timedb" in low or "listend" in low or "update_metrics" in low:
      continue
    rss = read_process_rss_bytes(pid)
    if rss <= 0:
      continue
    other += rss
    if rss >= _MIB and rss > top_rss:
      top_rss = rss
      base = cmd.split()[0] if cmd else str(pid)
      top_name = os.path.basename(base)
  return {"other_rss_bytes": other, "other_top": top_name}
