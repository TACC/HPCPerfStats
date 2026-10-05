"""
Exhaustive gated ``sync_timedb_mem_telemetry:`` OOM attribution lines.

Enable with PIPELINE ``sync_timedb_mem_telemetry=yes`` (default no). Distinct
from ``sync_ingest_worker_memory_telemetry`` (worker recycle batch_summary).

Attributes:
  _MIB: Bytes in one mebibyte.
  _PREV: Process-local previous snapshot for ``d_*`` deltas.
  _LAST_EDGE_OOM_KILL: Last seen ``oom_kill`` for edge emit.
  _LAST_EDGE_EV_MAX: Last seen ``memory.events`` ``max`` for edge emit.
"""

from __future__ import annotations

import os
import time
from typing import Any

from hpcperfstats.dbload.lib import process_memory as pm
from hpcperfstats.dbload.lib.sync_timedb_worker_memory import (
    PEAK_CGROUP_PER_RAW_FILE_BYTE,
    compute_ingest_inflight_raw_bytes_budget,
)

_MIB = 1024 * 1024

# Process-local previous snapshot for d_* deltas and edge triggers.
_PREV: dict[str, Any] = {}
_LAST_EDGE_OOM_KILL: int | None = None
_LAST_EDGE_EV_MAX: int | None = None


def reset_mem_telemetry_state_for_tests() -> None:
  """
  Clear process-local delta / edge state (unit tests only).

  Returns:
    None

  Examples:
    >>> reset_mem_telemetry_state_for_tests()
  """
  _PREV.clear()
  global _LAST_EDGE_OOM_KILL, _LAST_EDGE_EV_MAX
  _LAST_EDGE_OOM_KILL = None
  _LAST_EDGE_EV_MAX = None


def _mib(nbytes: Any) -> int:
  """
  Convert bytes to truncated mebibytes.

  Args:
    nbytes (Any): Byte count.

  Returns:
    int: MiB.

  Examples:
    >>> _mib(2 * 1024 * 1024)
    2
  """
  try:
    return int(nbytes or 0) // _MIB
  except (TypeError, ValueError):
    return 0


def _basename(path: str) -> str:
  """
  Return basename of ``path`` (or the string itself when empty path).

  Args:
    path (str): Filesystem path.

  Returns:
    str: Basename.

  Examples:
    >>> _basename("/a/b.stats")
    'b.stats'
  """
  return os.path.basename(str(path or "")) or str(path or "")


def _inflight_rank(
  inflight_sizes: dict[str, int] | None,
  submitted: dict[str, float] | None,
) -> dict[str, Any]:
  """
  Rank in-flight ingest identities by size and age for telemetry tokens.

  Args:
    inflight_sizes (dict[str, int] | None): Identity → ``st_size``.
    submitted (dict[str, float] | None): Identity → submit monotonic time.

  Returns:
    dict[str, Any]: Inflight summary tokens.

  Examples:
    >>> _inflight_rank({}, {})["inflight_n"]
    0
  """
  sizes = inflight_sizes if isinstance(inflight_sizes, dict) else {}
  ranked = sorted(
      ((str(k), int(v or 0)) for k, v in sizes.items()),
      key=lambda kv: kv[1],
      reverse=True,
  )
  top5 = ranked[:5]
  largest = top5[0] if top5 else ("", 0)
  now = time.monotonic()
  oldest_id = ""
  oldest_s = 0.0
  sub = submitted if isinstance(submitted, dict) else {}
  for ident, _sz in sizes.items():
    t0 = sub.get(ident)
    if t0 is None:
      continue
    age = max(0.0, now - float(t0))
    if age >= oldest_s:
      oldest_s = age
      oldest_id = ident
  return {
      "inflight_n": len(sizes),
      "inflight_raw_mib": _mib(sum(int(v or 0) for v in sizes.values())),
      "largest_inflight_mib": _mib(largest[1]),
      "largest_inflight": _basename(largest[0]),
      "top_inflight": ",".join(
          "%s:%d" % (_basename(p), _mib(sz)) for p, sz in top5
      ),
      "oldest_inflight_s": int(oldest_s),
      "oldest_inflight": _basename(oldest_id),
  }


def snapshot_pipeline_mem_telemetry(
  *,
  ingest_pool: Any | None = None,
  archive_pool: Any | None = None,
  inflight_sizes: dict[str, int] | None = None,
  submitted: dict[str, float] | None = None,
  ingest_mem_blocked: bool = False,
  alone_oversized: bool = False,
  total_ingested: int = 0,
  total_completed: int = 0,
  hot_used: int | None = None,
  catch_used: int | None = None,
  fill_block: str | None = None,
  ingest_q: str | None = None,
  append_q: str | None = None,
  discover_q: str | None = None,
  day_close_q: str | None = None,
  append_inflight_n: int | None = None,
  day_close_inflight_n: int | None = None,
) -> dict[str, Any]:
  """
  Build a telemetry snapshot dict (omiters happen at format time).

  Args:
    ingest_pool (Any | None): Optional ingest pool for tree RSS.
    archive_pool (Any | None): Optional archive pool for tree RSS.
    inflight_sizes (dict[str, int] | None): Identity → ``st_size``.
    submitted (dict[str, float] | None): Identity → submit monotonic.
    ingest_mem_blocked (bool): Raw-budget blocked flag.
    alone_oversized (bool): Alone-oversized admit flag.
    total_ingested (int): Lifetime ingested ACK count.
    total_completed (int): Lifetime completed ACK count.
    hot_used (int | None): Hot-band used slots.
    catch_used (int | None): Catchup-band used slots.
    fill_block (str | None): Last dominant fill block key.
    ingest_q (str | None): ``inflight/queued`` census token.
    append_q (str | None): Append census token.
    discover_q (str | None): Discover census token.
    day_close_q (str | None): Day-close census token.
    append_inflight_n (int | None): Local append inflight count.
    day_close_inflight_n (int | None): Local day-close inflight count.

  Returns:
    dict[str, Any]: Snapshot for :func:`format_sync_timedb_mem_telemetry_line`.

  Examples:
    >>> isinstance(snapshot_pipeline_mem_telemetry(), dict)
    True
  """
  import hpcperfstats.dbload.lib.conf_parser as cfg

  snap: dict[str, Any] = {
      "rss_limit_cgroup_pct": int(
          cfg.get_sync_process_tree_rss_limit_cgroup_pct(),
      ),
      "rss_exit_cgroup_pct": int(
          cfg.get_sync_process_tree_rss_exit_cgroup_pct(),
      ),
      "rss_limit_mib": int(pm.effective_process_tree_rss_limit_mib()),
      "rss_exit_mib": int(pm.effective_process_tree_rss_exit_mib()),
      "peak_factor": float(PEAK_CGROUP_PER_RAW_FILE_BYTE),
      "budget_mib": _mib(compute_ingest_inflight_raw_bytes_budget()),
      "headroom_cfg_mib": int(cfg.get_sync_cgroup_admit_headroom_mib()),
      "file_cache_cgroup_pct": int(
          cfg.get_sync_cgroup_admit_max_file_cache_cgroup_pct(),
      ),
      "file_cache_cfg_mib": int(pm.effective_cgroup_admit_max_file_cache_mib()),
      "drop_page_cache": (
          "yes" if cfg.get_sync_pipeline_drop_page_cache() else "no"
      ),
      "ingest_pool": int(cfg.get_sync_ingest_pool_processes()),
      "listend_pool": int(cfg.get_listend_db_ingest_pool_processes()),
      "metrics_pool": int(cfg.get_metrics_pool_processes()),
      "ingest_mem_blocked": "yes" if ingest_mem_blocked else "no",
      "alone_oversized": "yes" if alone_oversized else "no",
      "total_ingested": int(total_ingested or 0),
      "total_completed": int(total_completed or 0),
  }
  snap.update(_inflight_rank(inflight_sizes, submitted))

  current = pm.read_cgroup_memory_current_bytes()
  peak = pm.read_cgroup_memory_peak_bytes()
  cmax = pm.read_cgroup_memory_max_bytes()
  if current or peak or cmax is not None:
    snap["cgroup_mib"] = _mib(current)
    snap["cgroup_peak_mib"] = _mib(peak)
    if cmax is not None:
      snap["cgroup_max_mib"] = _mib(cmax)
      left = int(cmax) - int(current or 0)
      snap["headroom_left_mib"] = max(0, left // _MIB)
      snap["headroom_ok"] = (
          "yes" if pm.cgroup_admit_headroom_ok(snap["headroom_cfg_mib"])
          else "no"
      )
    else:
      snap["headroom_ok"] = "n/a"

  cap_mib = int(snap.get("file_cache_cfg_mib", 0) or 0)
  if cap_mib <= 0:
    snap["file_cache_ok"] = "n/a"
  else:
    snap["file_cache_ok"] = (
        "yes" if pm.cgroup_admit_file_cache_ok(cap_mib) else "no"
    )

  events = pm.read_cgroup_memory_events()
  if events:
    snap["ev_low"] = int(events.get("low", 0) or 0)
    snap["ev_high"] = int(events.get("high", 0) or 0)
    snap["ev_max"] = int(events.get("max", 0) or 0)
    snap["ev_oom"] = int(events.get("oom", 0) or 0)
    snap["ev_oom_kill"] = int(events.get("oom_kill", 0) or 0)
    snap["ev_oom_group_kill"] = int(events.get("oom_group_kill", 0) or 0)

  stat = pm.read_cgroup_memory_stat()
  if stat:
    for key in pm._MEMORY_STAT_MIB_KEYS:
      if key in stat:
        snap["%s_mib" % key] = _mib(stat[key])
    for key in pm._MEMORY_STAT_COUNT_KEYS:
      if key in stat:
        snap[key] = int(stat[key])

  tree = pm.format_tree_rss_breakdown_mb(ingest_pool, archive_pool)
  snap["sync_sup_mib"] = int(tree["supervisor_mb"])
  snap["sync_ingest_pool_mib"] = int(tree["ingest_pool_mb"])
  snap["sync_archive_pool_mib"] = int(tree["archive_pool_mb"])
  snap["sync_nlwp"] = pm.read_process_nlwp()

  daemons = pm.read_daemon_rss_by_cmdline()
  snap["sync_rss_mib"] = _mib(daemons.get("sync", 0))
  snap["listend_rss_mib"] = _mib(daemons.get("listend", 0))
  snap["metrics_rss_mib"] = _mib(daemons.get("metrics", 0))
  daemon_sum = (
      int(daemons.get("sync", 0) or 0)
      + int(daemons.get("listend", 0) or 0)
      + int(daemons.get("metrics", 0) or 0)
  )
  snap["daemon_rss_sum_mib"] = _mib(daemon_sum)
  if "cgroup_mib" in snap:
    snap["gap_mib"] = max(
        0, int(snap["cgroup_mib"]) - int(snap["daemon_rss_sum_mib"]),
    )

  other = pm.read_other_cgroup_rss()
  snap["other_rss_mib"] = _mib(other.get("other_rss_bytes", 0))
  if other.get("other_top"):
    snap["other_top"] = str(other["other_top"])

  if hot_used is not None:
    snap["hot_used"] = int(hot_used)
  if catch_used is not None:
    snap["catch_used"] = int(catch_used)
  if fill_block:
    snap["fill_block"] = str(fill_block)
  if ingest_q is not None:
    snap["ingest_q"] = str(ingest_q)
  if append_q is not None:
    snap["append_q"] = str(append_q)
  if discover_q is not None:
    snap["discover_q"] = str(discover_q)
  if day_close_q is not None:
    snap["day_close_q"] = str(day_close_q)
  if append_inflight_n is not None:
    snap["append_inflight_n"] = int(append_inflight_n)
  if day_close_inflight_n is not None:
    snap["day_close_inflight_n"] = int(day_close_inflight_n)

  prev = _PREV
  for key, dkey in (
      ("cgroup_mib", "d_cgroup_mib"),
      ("file_mib", "d_file_mib"),
      ("anon_mib", "d_anon_mib"),
      ("ev_oom_kill", "d_oom_kill"),
      ("ev_max", "d_ev_max"),
  ):
    if key in snap:
      snap[dkey] = int(snap[key]) - int(prev.get(key, snap[key]) or 0)
  _PREV.clear()
  _PREV.update(snap)
  return snap


def format_sync_timedb_mem_telemetry_line(
  event: str,
  snap: dict[str, Any] | None = None,
  **kwargs: Any,
) -> str:
  """
  Format one greppable ``sync_timedb_mem_telemetry:`` line.

  Args:
    event (str): Event name (census, admit_alone, skip_budget, …).
    snap (dict | None): Prebuilt snapshot; else build via kwargs.
    **kwargs: Forwarded to :func:`snapshot_pipeline_mem_telemetry`.

  Returns:
    str: Full log line body (caller prefixes via ``log_print``).

  Examples:
    >>> "sync_timedb_mem_telemetry:" in format_sync_timedb_mem_telemetry_line(
    ...   "census",
    ... )
    True
  """
  data = (
      snap if isinstance(snap, dict)
      else snapshot_pipeline_mem_telemetry(**kwargs)
  )
  parts = ["sync_timedb_mem_telemetry: event=%s" % event]
  order = (
      "rss_limit_cgroup_pct", "rss_exit_cgroup_pct",
      "rss_limit_mib", "rss_exit_mib", "peak_factor", "budget_mib",
      "headroom_cfg_mib", "headroom_left_mib", "headroom_ok",
      "file_cache_cgroup_pct", "file_cache_cfg_mib", "file_cache_ok",
      "drop_page_cache", "ingest_pool", "listend_pool", "metrics_pool",
      "cgroup_mib", "cgroup_peak_mib", "cgroup_max_mib",
      "ev_low", "ev_high", "ev_max", "ev_oom", "ev_oom_kill",
      "ev_oom_group_kill",
      "anon_mib", "file_mib", "file_mapped_mib", "shmem_mib",
      "active_anon_mib", "inactive_anon_mib", "active_file_mib",
      "inactive_file_mib",
      "unevictable_mib", "slab_mib", "pgfault", "pgmajfault",
      "sync_rss_mib", "sync_sup_mib", "sync_ingest_pool_mib",
      "sync_archive_pool_mib",
      "sync_nlwp", "listend_rss_mib", "metrics_rss_mib",
      "other_rss_mib", "other_top", "daemon_rss_sum_mib", "gap_mib",
      "inflight_n", "inflight_raw_mib", "ingest_mem_blocked",
      "largest_inflight_mib", "largest_inflight", "top_inflight",
      "oldest_inflight_s", "oldest_inflight", "alone_oversized",
      "total_ingested", "total_completed", "hot_used", "catch_used",
      "fill_block",
      "ingest_q", "append_q", "discover_q", "day_close_q",
      "append_inflight_n", "day_close_inflight_n",
      "d_cgroup_mib", "d_file_mib", "d_anon_mib", "d_oom_kill", "d_ev_max",
  )
  for key in order:
    if key not in data:
      continue
    parts.append("%s=%s" % (key, data[key]))
  return "INFO: " + " ".join(parts)


def maybe_emit_mem_telemetry(
  event: str,
  log_fn: Any,
  snap: dict[str, Any] | None = None,
  **kwargs: Any,
) -> None:
  """
  Emit one telemetry line when ``sync_timedb_mem_telemetry`` is enabled.

  Args:
    event (str): Event name.
    log_fn (Any): Callable taking one string (typically ``log_print``).
    snap (dict | None): Optional prebuilt snapshot.
    **kwargs: Snapshot kwargs when ``snap`` is None.

  Returns:
    None

  Examples:
    >>> maybe_emit_mem_telemetry("census", lambda _s: None)
  """
  import hpcperfstats.dbload.lib.conf_parser as cfg

  if not cfg.get_sync_timedb_mem_telemetry():
    return
  if not callable(log_fn):
    return
  log_fn(format_sync_timedb_mem_telemetry_line(event, snap=snap, **kwargs))


def detect_edge_events(snap: dict[str, Any]) -> list[str]:
  """
  Return edge event names when oom_kill or ev_max increased vs last edge emit.

  Args:
    snap (dict[str, Any]): Latest snapshot (must include ev_* when present).

  Returns:
    list[str]: Zero or more of ``oom_kill_delta``, ``cgroup_max_delta``.

  Examples:
    >>> detect_edge_events({})
    []
  """
  global _LAST_EDGE_OOM_KILL, _LAST_EDGE_EV_MAX
  out: list[str] = []
  oom = snap.get("ev_oom_kill")
  if oom is not None:
    if (
        _LAST_EDGE_OOM_KILL is not None
        and int(oom) > int(_LAST_EDGE_OOM_KILL)
    ):
      out.append("oom_kill_delta")
    _LAST_EDGE_OOM_KILL = int(oom)
  ev_max = snap.get("ev_max")
  if ev_max is not None:
    if _LAST_EDGE_EV_MAX is not None and int(ev_max) > int(_LAST_EDGE_EV_MAX):
      out.append("cgroup_max_delta")
    _LAST_EDGE_EV_MAX = int(ev_max)
  return out
