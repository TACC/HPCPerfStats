"""
Per-file ingest timeout helpers (shared by sync_timedb and pool dispatch).

Internal wall soft-kill names are deleted. Idle stall plus Postgres
``statement_timeout`` remain. ``get_sync_ingest_per_file_timeout_max_s`` is
retained for job-store lease EX only.

Attributes:
  GIANT_SUPPLEMENT_LARGE_MAX_BYTES: Attribute.
  GIANT_SUPPLEMENT_MAX_BYTES: Attribute.
  GIANT_SUPPLEMENT_TRIGGER_BUDGET_S: Attribute.
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

# Conservative proxy when store hlen is unavailable (typical spooled member size).
_TYPICAL_SEALED_MEMBER_BYTES = 32 * 1024 * 1024


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
  del path, trigger_s
  return False


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
  Sealed-day wall-clock budget — always ``0`` (internal walls deleted).

  Args:
    sealed_path (str): Daily sealed archive path (ignored; always 0.0).
    member_count (Any | None): Optional member count (ignored; always 0.0).

  Returns:
    float: Always ``0.0``.

  Examples:
    >>> estimate_sealed_archive_ingest_budget_s("/x.tar.zst")
    0.0
  """
  del sealed_path, member_count
  return 0.0


def max_sealed_archive_ingest_budget_for_paths(
  sealed_paths: Any,
  *,
  member_counts: Any | None = None,
) -> Any:
  """
  Largest sealed-day ingest budget — always ``0`` (internal walls deleted).

  Args:
    sealed_paths (Any): Sealed archive paths (ignored; always 0.0).
    member_counts (Any | None): Optional per-path counts (ignored; always 0.0).

  Returns:
    float: Always ``0.0``.

  Examples:
    >>> max_sealed_archive_ingest_budget_for_paths(["/a.tar.zst"])
    0.0
  """
  del sealed_paths, member_counts
  return 0.0
