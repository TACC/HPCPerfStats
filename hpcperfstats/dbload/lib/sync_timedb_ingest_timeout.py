"""
Per-file ingest timeout helpers (shared by sync_timedb and pool dispatch).

Internal wall soft-kill is deleted: ``resolve_*`` and ``stall_abort_polls_*``
always return ``0``. Idle stall + Postgres ``statement_timeout`` remain.
``get_sync_ingest_per_file_timeout_max_s`` is retained for job-store lease EX only.

Attributes:
  GIANT_SUPPLEMENT_LARGE_MAX_BYTES: Attribute.
  GIANT_SUPPLEMENT_MAX_BYTES: Attribute.
  GIANT_SUPPLEMENT_TRIGGER_BUDGET_S: Attribute.
  STALL_ABORT_GRACE_S: Attribute.
  _INGEST_TIMEOUT_MIB_BYTES: Attribute.
  _TYPICAL_SEALED_MEMBER_BYTES: Attribute.
"""

from __future__ import annotations

from typing import Any

# Former B giant-supplement thresholds (INI keys retired). Used only to label
# oversized paths for worker-memory telemetry — not a coordinator supplement path.
GIANT_SUPPLEMENT_TRIGGER_BUDGET_S = 6600.0
GIANT_SUPPLEMENT_MAX_BYTES = 1024 * 1024 * 1024
GIANT_SUPPLEMENT_LARGE_MAX_BYTES = 8 * 1024 * 1024 * 1024


import os

import hpcperfstats.dbload.lib.conf_parser as cfg

_INGEST_TIMEOUT_MIB_BYTES = 1024 * 1024
# Conservative proxy when store hlen is unavailable (typical spooled member size).
_TYPICAL_SEALED_MEMBER_BYTES = 32 * 1024 * 1024


def resolve_ingest_per_file_timeout_s(stats_file: str) -> Any:
  """
  Per-file internal wall budget — always ``0`` (wall soft-kill deleted).

  Idle stall + Postgres ``statement_timeout`` remain. INI/env floor keys are
  ignored so wall soft-kill cannot be re-armed.

  Args:
    stats_file (str): Stats path (unused; kept for API compatibility).

  Returns:
    float: Always ``0.0``.

  Examples:
    >>> resolve_ingest_per_file_timeout_s("/x") == 0.0
    True
  """
  del stats_file
  return 0.0


def resolve_ingest_per_file_timeout_for_size_bytes(
  size_bytes: Any,
  *,
  base: Any | None = None,
) -> Any:
  """
  Size-proportional wall budget — always ``0`` (wall soft-kill deleted).

  Args:
    size_bytes (Any): Unused; kept for API compatibility.
    base (Any | None): Unused; kept for API compatibility.

  Returns:
    float: Always ``0.0``.

  Examples:
    >>> resolve_ingest_per_file_timeout_for_size_bytes(1 << 30) == 0.0
    True
  """
  del size_bytes, base
  return 0.0


def max_ingest_per_file_timeout_for_paths(paths: Any) -> Any:
  """
  Largest per-file wall budget for paths — always ``0`` (walls deleted).

  Args:
    paths (Any): Unused; kept for API compatibility.

  Returns:
    float: Always ``0.0``.

  Examples:
    >>> max_ingest_per_file_timeout_for_paths(["/a"]) == 0.0
    True
  """
  del paths
  return 0.0


# Retained for tests that still name the constant; no longer drives stall abort.
STALL_ABORT_GRACE_S = 120.0


def stall_abort_polls_for_paths(paths: Any) -> Any:
  """
  Pool poll-count stall abort — always ``0`` (disabled; no wall reclaim).

  Dead-worker / packed ``idle_stall`` reclaim only. INI ceiling is ignored.

  Args:
    paths (Any): Unused; kept for API compatibility.

  Returns:
    int: Always ``0`` (callers must skip poll-count abort when ``<= 0``).

  Examples:
    >>> stall_abort_polls_for_paths(["/a"]) == 0
    True
  """
  del paths
  return 0


def default_giant_supplement_trigger_budget_s() -> Any:
  """
  Default trigger budget: 2 GiB under historical slope (floor-900 anchor +.
  
    per_mib).
  
  Returns:
    Any: Open return polymorphism from
    ``default_giant_supplement_trigger_budget_s``: concrete type depends on
    inputs and branch (mapping, scalar, handle, or ``None``-like empty).
  
  Examples:
    >>> default_giant_supplement_trigger_budget_s()  # doctest: +SKIP
  """
  per_mib = float(cfg.get_sync_ingest_per_file_timeout_s_per_mib())
  return 900.0 + 2048.0 * per_mib


def is_giant_ingest_budget(path: str, *, trigger_s: Any | None = None) -> Any:
  """
  True when ``path`` resolved ingest budget meets the giant supplement.
  
    threshold.
  
  Args:
    path (str): String for path.
    trigger_s (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> is_giant_ingest_budget("x", None)  # doctest: +SKIP
  """
  if trigger_s is None:
    trigger_s = float(GIANT_SUPPLEMENT_TRIGGER_BUDGET_S)
  if trigger_s <= 0.0:
    return False
  resolved = resolve_ingest_per_file_timeout_s(path)
  return resolved >= float(trigger_s)


def calendar_day_from_sealed_archive_path(sealed_path: str) -> Any:
  """
  Return ``YYYY-MM-DD`` ISO day token from a sealed daily archive path.
  
  Args:
    sealed_path (str): String for sealed path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> calendar_day_from_sealed_archive_path("x")  # doctest: +SKIP
  """
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


def _store_member_count_for_sealed_day(day_token: Any) -> Any:
  """
  Best-effort store HASH length for a calendar day (0 when unavailable).
  
  Args:
    day_token (Any): Day token passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _store_member_count_for_sealed_day(None)  # doctest: +SKIP
  """
  if not day_token:
    return 0
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_coord import (
      build_archive_members_keys,
      lookup_full_members,
  )

  tgz_archive_dir = cfg.get_daily_archive_dir_path()
  if not tgz_archive_dir:
    return 0
  try:
    from datetime import date as date_cls

    from hpcperfstats.dbload.lib.archive_compress import (
      daily_compressed_path_for_date,
    )
    from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      _daily_archive_members_cache_key,
      normalize_daily_compressed_path,
    )

    day_date = date_cls.fromisoformat(day_token)
    compressed = daily_compressed_path_for_date(tgz_archive_dir, day_date)
    cache_key = _daily_archive_members_cache_key(
        normalize_daily_compressed_path(compressed),
    )
    keys = build_archive_members_keys(cache_key)
    members = lookup_full_members(keys)
    return 0 if members is None else len(members)
  except (ValueError, TypeError, OSError):
    return 0


def sealed_archive_member_count_hint(
  sealed_path: str,
  *,
  member_count: Any | None = None,
) -> Any:
  """
  Estimate member count for sealed-day stall budgeting.
  
  Args:
    sealed_path (str): String for sealed path.
    member_count (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> sealed_archive_member_count_hint("x", None)  # doctest: +SKIP
  """
  if member_count is not None:
    try:
      count = int(member_count)
    except (TypeError, ValueError):
      count = 0
    if count > 0:
      return count
  day_token = calendar_day_from_sealed_archive_path(sealed_path)
  hlen = _store_member_count_for_sealed_day(day_token)
  if hlen > 0:
    return hlen
  try:
    compressed_size = int(os.path.getsize(sealed_path))
  except OSError:
    compressed_size = 0
  if compressed_size <= 0:
    return 1
  return max(1, compressed_size // _TYPICAL_SEALED_MEMBER_BYTES)


def estimate_sealed_archive_ingest_budget_s(
  sealed_path: str,
  *,
  member_count: Any | None = None,
) -> Any:
  """
  Wall-clock budget for one sealed-day pool task (sum of member budgets,.
  
    capped).
  
  Args:
    sealed_path (str): String for sealed path.
    member_count (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> estimate_sealed_archive_ingest_budget_s("x", None)  # doctest: +SKIP
  """
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


def max_sealed_archive_ingest_budget_for_paths(
  sealed_paths: Any,
  *,
  member_counts: Any | None = None,
) -> Any:
  """
  Largest sealed-day ingest budget across a chunk of sealed archives.
  
  Args:
    sealed_paths (Any): Iterable of filesystem paths as strings.
    member_counts (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> max_sealed_archive_ingest_budget_for_paths(None, None)  # doctest: +SKIP
  """
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


def stall_abort_polls_for_sealed_archives(
  sealed_paths: Any,
  *,
  member_counts: Any | None = None,
) -> Any:
  """
  Pool poll-count stall abort for sealed archives — always ``0`` (disabled).

  Args:
    sealed_paths (Any): Unused; kept for API compatibility.
    member_counts (Any | None): Unused; kept for API compatibility.

  Returns:
    int: Always ``0``.

  Examples:
    >>> stall_abort_polls_for_sealed_archives(["/a.tar.zst"]) == 0
    True
  """
  del sealed_paths, member_counts
  return 0
