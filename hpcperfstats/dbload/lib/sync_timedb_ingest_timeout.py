"""Per-file ingest timeout and pool stall-abort helpers (shared by sync_timedb and pool dispatch)."""

from __future__ import annotations

import hpcperfstats.dbload.lib.conf_parser as cfg
from hpcperfstats.dbload.lib.sync_timedb_parsing import stats_file_size_bytes

_INGEST_TIMEOUT_MIB_BYTES = 1024 * 1024


def resolve_ingest_per_file_timeout_s(stats_file):
  """Size-proportional wall-clock budget for one ingest worker task."""
  base = float(cfg.get_sync_ingest_per_file_timeout_s())
  if base <= 0.0:
    return 0.0
  size = stats_file_size_bytes(stats_file)
  if size <= 0:
    return base
  mib = (size + (_INGEST_TIMEOUT_MIB_BYTES - 1)) // _INGEST_TIMEOUT_MIB_BYTES
  per_mib = float(cfg.get_sync_ingest_per_file_timeout_s_per_mib())
  cap = float(cfg.get_sync_ingest_per_file_timeout_max_s())
  scaled = base + float(mib) * per_mib
  if cap > 0.0:
    scaled = min(scaled, cap)
  return max(base, scaled)


def max_ingest_per_file_timeout_for_paths(paths):
  """Largest resolved per-file ingest budget for a set of stats paths."""
  floor_s = float(cfg.get_sync_ingest_per_file_timeout_s())
  if floor_s <= 0.0:
    return 0.0
  best = floor_s
  for path in paths or ():
    if not path:
      continue
    resolved = resolve_ingest_per_file_timeout_s(path)
    if resolved > best:
      best = resolved
  return best


def stall_abort_polls_for_paths(paths):
  """Poll-timeout abort count for in-flight paths (floor .. INI ceiling)."""
  poll_s = float(cfg.get_sync_pool_poll_timeout_s())
  ceiling_polls = int(cfg.get_sync_pool_stall_abort_after_timeouts())
  if poll_s <= 0.0:
    return max(1, ceiling_polls)
  floor_s = float(cfg.get_sync_ingest_per_file_timeout_s())
  if not paths:
    batch_max_s = floor_s if floor_s > 0.0 else poll_s
  else:
    batch_max_s = max_ingest_per_file_timeout_for_paths(paths)
    if batch_max_s <= 0.0:
      batch_max_s = floor_s if floor_s > 0.0 else poll_s
  dynamic_polls = int(batch_max_s / poll_s) + 1
  min_polls = int(floor_s / poll_s) + 1 if floor_s > 0.0 else 1
  return max(1, min(ceiling_polls, max(min_polls, dynamic_polls)))
