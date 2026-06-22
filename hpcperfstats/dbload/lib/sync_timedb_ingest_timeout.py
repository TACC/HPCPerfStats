"""Per-file ingest timeout and pool stall-abort helpers (shared by sync_timedb and pool dispatch).

Default INI fallbacks map **30 GiB → max** per-file ingest budget (24h at reference size).
Operators should tune ``sync_ingest_per_file_timeout_max_s``, ``per_mib``, and
``sync_pool_stall_abort_after_timeouts`` together.
"""

from __future__ import annotations

import os

import hpcperfstats.dbload.lib.conf_parser as cfg
from hpcperfstats.dbload.lib.sync_timedb_parsing import stats_file_size_bytes

_INGEST_TIMEOUT_MIB_BYTES = 1024 * 1024
# Conservative proxy when Redis hlen is unavailable (typical spooled member size).
_TYPICAL_SEALED_MEMBER_BYTES = 32 * 1024 * 1024


def resolve_ingest_per_file_timeout_s(stats_file):
  """Size-proportional wall-clock budget for one ingest worker task."""
  base = float(cfg.get_sync_ingest_per_file_timeout_s())
  if base <= 0.0:
    return 0.0
  size = stats_file_size_bytes(stats_file)
  return resolve_ingest_per_file_timeout_for_size_bytes(size, base=base)


def resolve_ingest_per_file_timeout_for_size_bytes(size_bytes, *, base=None):
  """Size-proportional ingest budget for a byte count (not necessarily a path)."""
  if base is None:
    base = float(cfg.get_sync_ingest_per_file_timeout_s())
  if base <= 0.0:
    return 0.0
  size = int(size_bytes or 0)
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


def default_giant_supplement_trigger_budget_s():
  """Default trigger budget: resolved timeout at 2 GiB under current INI slope."""
  per_mib = float(cfg.get_sync_ingest_per_file_timeout_s_per_mib())
  return 900.0 + 2048.0 * per_mib


def is_giant_ingest_budget(path, *, trigger_s=None):
  """True when ``path`` resolved ingest budget meets the giant supplement threshold."""
  if trigger_s is None:
    trigger_s = float(cfg.get_sync_ingest_giant_pool_supplement_trigger_budget_s())
  if trigger_s <= 0.0:
    return False
  resolved = resolve_ingest_per_file_timeout_s(path)
  return resolved >= float(trigger_s)


def any_giant_ingest_budget_in_flight(paths, *, trigger_s=None):
  """True when any in-flight path qualifies as a giant for pool supplement."""
  if trigger_s is None:
    trigger_s = float(cfg.get_sync_ingest_giant_pool_supplement_trigger_budget_s())
  for path in paths or ():
    if path and is_giant_ingest_budget(path, trigger_s=trigger_s):
      return True
  return False


def iter_giant_supplement_paths(
    pending_tail,
    *,
    max_bytes=None,
    limit=None,
    exclude=None,
):
  """Oldest-first pending tail paths under ``max_bytes`` for giant pool supplement."""
  if max_bytes is None:
    max_bytes = int(cfg.get_sync_ingest_giant_pool_supplement_max_bytes())
  exclude_set = set(exclude or ())
  max_bytes = int(max_bytes)
  remaining = None if limit is None else max(0, int(limit))
  for path in pending_tail or ():
    if not path or path in exclude_set:
      continue
    if remaining is not None and remaining <= 0:
      break
    try:
      size = int(stats_file_size_bytes(path))
    except (TypeError, ValueError, OSError):
      continue
    if size <= 0 or size >= max_bytes:
      continue
    yield path
    if remaining is not None:
      remaining -= 1


def calendar_day_from_sealed_archive_path(sealed_path):
  """Return ``YYYY-MM-DD`` ISO day token from a sealed daily archive path."""
  if not sealed_path:
    return ""
  base = os.path.basename(os.path.normpath(str(sealed_path)))
  if len(base) >= 10 and base[4:5] == "-" and base[7:8] == "-":
    token = base[:10]
    try:
      from datetime import date

      date.fromisoformat(token)
      return token
    except ValueError:
      pass
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      parse_archive_date_from_daily_gz_path,
  )

  day_date = parse_archive_date_from_daily_gz_path(sealed_path)
  if day_date is not None:
    return day_date.isoformat()
  return ""


def _redis_member_count_for_sealed_day(day_token):
  """Best-effort Redis HASH length for a calendar day (0 when unavailable)."""
  if not day_token:
    return 0
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      archive_members_redis_enabled,
      build_archive_members_redis_keys,
      get_archive_members_redis_client,
  )

  if not archive_members_redis_enabled():
    return 0
  tgz_archive_dir = cfg.get_daily_archive_dir_path()
  if not tgz_archive_dir:
    return 0
  try:
    from datetime import date as date_cls

    from hpcperfstats.dbload.lib.archive_compress import daily_compressed_path_for_date
    from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
        _daily_archive_members_cache_key,
        normalize_daily_compressed_path,
    )

    day_date = date_cls.fromisoformat(day_token)
    compressed = daily_compressed_path_for_date(tgz_archive_dir, day_date)
    cache_key = _daily_archive_members_cache_key(
        normalize_daily_compressed_path(compressed),
    )
    keys = build_archive_members_redis_keys(cache_key)
    client = get_archive_members_redis_client(required=False)
    if client is None:
      return 0
    return int(client.hlen(keys.hash_key))
  except (ValueError, TypeError, OSError):
    return 0


def sealed_archive_member_count_hint(sealed_path, *, member_count=None):
  """Estimate member count for sealed-day stall budgeting."""
  if member_count is not None:
    try:
      count = int(member_count)
    except (TypeError, ValueError):
      count = 0
    if count > 0:
      return count
  day_token = calendar_day_from_sealed_archive_path(sealed_path)
  hlen = _redis_member_count_for_sealed_day(day_token)
  if hlen > 0:
    return hlen
  try:
    compressed_size = int(os.path.getsize(sealed_path))
  except OSError:
    compressed_size = 0
  if compressed_size <= 0:
    return 1
  return max(1, compressed_size // _TYPICAL_SEALED_MEMBER_BYTES)


def estimate_sealed_archive_ingest_budget_s(sealed_path, *, member_count=None):
  """Wall-clock budget for one sealed-day pool task (sum of member budgets, capped)."""
  floor_s = float(cfg.get_sync_ingest_per_file_timeout_s())
  if floor_s <= 0.0:
    return 0.0
  count = sealed_archive_member_count_hint(sealed_path, member_count=member_count)
  try:
    compressed_size = int(os.path.getsize(sealed_path))
  except OSError:
    compressed_size = 0
  if compressed_size > 0 and count > 0:
    avg_member_bytes = max(1, compressed_size // count)
  else:
    avg_member_bytes = _TYPICAL_SEALED_MEMBER_BYTES
  per_member_s = resolve_ingest_per_file_timeout_for_size_bytes(avg_member_bytes)
  max_per_file = float(cfg.get_sync_ingest_per_file_timeout_max_s())
  if max_per_file > 0.0:
    per_member_s = min(per_member_s, max_per_file)
  total_s = per_member_s * float(count)
  poll_s = float(cfg.get_sync_pool_poll_timeout_s())
  ceiling_polls = int(cfg.get_sync_pool_stall_abort_after_timeouts())
  if poll_s > 0.0 and ceiling_polls > 0:
    total_s = min(total_s, poll_s * ceiling_polls)
  return max(floor_s, total_s)


def max_sealed_archive_ingest_budget_for_paths(sealed_paths, *, member_counts=None):
  """Largest sealed-day ingest budget across a chunk of sealed archives."""
  floor_s = float(cfg.get_sync_ingest_per_file_timeout_s())
  if floor_s <= 0.0:
    return 0.0
  best = floor_s
  counts = member_counts or {}
  for path in sealed_paths or ():
    if not path:
      continue
    resolved = estimate_sealed_archive_ingest_budget_s(
        path,
        member_count=counts.get(path),
    )
    if resolved > best:
      best = resolved
  return best


def stall_abort_polls_for_sealed_archives(sealed_paths, *, member_counts=None):
  """Poll-timeout abort count for in-flight sealed archives (floor .. INI ceiling)."""
  poll_s = float(cfg.get_sync_pool_poll_timeout_s())
  ceiling_polls = int(cfg.get_sync_pool_stall_abort_after_timeouts())
  if poll_s <= 0.0:
    return max(1, ceiling_polls)
  floor_s = float(cfg.get_sync_ingest_per_file_timeout_s())
  if not sealed_paths:
    batch_max_s = floor_s if floor_s > 0.0 else poll_s
  else:
    batch_max_s = max_sealed_archive_ingest_budget_for_paths(
        sealed_paths,
        member_counts=member_counts,
    )
    if batch_max_s <= 0.0:
      batch_max_s = floor_s if floor_s > 0.0 else poll_s
  dynamic_polls = int(batch_max_s / poll_s) + 1
  min_polls = int(floor_s / poll_s) + 1 if floor_s > 0.0 else 1
  return max(1, min(ceiling_polls, max(min_polls, dynamic_polls)))
