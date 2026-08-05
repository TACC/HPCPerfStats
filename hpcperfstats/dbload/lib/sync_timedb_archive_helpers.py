"""
Pure helpers for sync_timedb archiving, tar utilities, and file discovery (no
Django). Used by sync_timedb and by unit tests.

Attributes:
  ARCHIVE_SKIP_ACTIVE_SEGMENT: Attribute.
  ARCHIVE_SKIP_DAY_INGEST_SKIP_PREFIX: Attribute.
  ARCHIVE_SKIP_MEMBER_EXISTS: Attribute.
  ARCHIVE_SKIP_MISSING_PATH: Attribute.
  ARCHIVE_SKIP_UNRESOLVED_DAY: Attribute.
  INGEST_PARSE_FAILED_QUARANTINE_REASON: Attribute.
  MIGRATE_GZ_STATUS_CONVERTED: Attribute.
  MIGRATE_GZ_STATUS_DROPPED_ONLY: Attribute.
  MIGRATE_GZ_STATUS_FAILED: Attribute.
  MIGRATE_GZ_STATUS_KEPT_MISMATCH: Attribute.
  MIGRATE_GZ_STATUS_PLANNED: Attribute.
  MIGRATE_GZ_STATUS_SKIPPED_LOCKED: Attribute.
  MIGRATE_GZ_STATUS_SKIPPED_NO_GZ: Attribute.
  SKIP_KIND_READ_ERROR: Attribute.
  SKIP_KIND_TAR_TRUNCATED: Attribute.
  SKIP_KIND_ZST_FRAME_INVALID: Attribute.
  STREAM_ARCHIVE_TASK: Attribute.
  SYNC_TIMEDB_UNPARSABLE_RAW_DIRNAME: Attribute.
  SYNC_TIMEDB_UNPARSABLE_RAW_MANIFEST_BASENAME: Attribute.
  UNPARSABLE_RAW_QUARANTINE_REASON: Attribute.
  USTAR_MAX_MEMBER_BYTES: Attribute.
  _ARCHIVE_MEMBERS_INVALIDATION_HOOK: Attribute.
  _DAILY_ARCHIVE_MEMBERS_CACHE: Attribute.
  _DAILY_GZ_BASENAME_RE: Attribute.
  _DAILY_ISO_DATE_RE: Attribute.
  _DAILY_TAR_BASENAME_RE: Attribute.
  _DAILY_ZST_BASENAME_RE: Attribute.
  _DAY_CLOSE_DISQUALIFY_CODES: Attribute.
  _DEFERRED_PREWARM_FLUSH_HOOK: Attribute.
  _FNCTL_POPULATE_RETRY_DELAYS_S: Attribute.
  _INGEST_SEALED_LOOKUP_WARNED: Attribute.
  _INGEST_SEALED_LOOKUP_WARNED_MAX: Attribute.
  _INGEST_SKIPPED_CALENDAR_DAYS: Attribute.
  _INGEST_SKIPPED_CALENDAR_DAYS_MAX: Attribute.
  _LOGGED_ARCHIVE_DAY_INGEST_SKIP: Attribute.
  _LOGGED_ARCHIVE_DAY_INGEST_SKIP_MAX: Attribute.
  _POPULATE_SOURCE_BY_CANONICAL: Attribute.
  _UNMAPPED_DISQUALIFY_CACHE: Attribute.
  _UNMAPPED_DISQUALIFY_TTL_S: Attribute.
  _oldest_waiting_ingest_frozen_state: Attribute.
"""
from __future__ import annotations

from typing import Any, Iterator

import contextlib
from hpcperfstats.dbload.lib.sync_timedb_session_executor import (
    iter_bounded_thread_pool,
)
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
from collections import OrderedDict, defaultdict
from datetime import date, datetime, time as dt_time, timedelta

import hpcperfstats.dbload.lib.conf_parser as cfg
from hpcperfstats.dbload.lib.archive_compress import (
    DAILY_ARCHIVE_GZ_SUFFIX,
    DAILY_ARCHIVE_ZST_SUFFIX,
    archive_gz_members_contained_in_zst,
    compressed_sibling_paths,
    daily_compressed_path_for_date,
    daily_tar_path_from_compressed,
    detect_compressed_format,
    normalize_daily_compressed_path,
    sum_member_bytes,
)
from hpcperfstats.dbload.lib.zstd_cli import (
    decompress_compressed_to_tar,
    zstd_compressed_archive_pipe_readable,
    zstd_decompress_stdout,
    zstd_drop_page_cache_for_paths,
    zstd_gzip_decompress_stdout,
    zstd_gzip_supported,
    zstd_compress_tar_to_file,
    zstd_test,
)
from hpcperfstats.dbload.lib.sync_timedb_parsing import parse_first_timestamp_line
from hpcperfstats.dbload.lib.file_locking import (
    LOCK_EXPIRY_SECONDS,
    LOCK_SUFFIX,
    file_read_lock_wait,
    file_write_lock,
)
from hpcperfstats.dbload.lib.print_utils import janitorial_logging, log_print


def get_archive_zstd_thread_count() -> Any:
  """
  Current archive zstd ``-T`` setting (0 → ``-T0``). Read at call time for ini.
  
    freshness.
  
  Returns:
    Any: Open return polymorphism from ``get_archive_zstd_thread_count``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_archive_zstd_thread_count()  # doctest: +SKIP
  """
  return cfg.get_archive_zstd_threads()


def get_ingest_zstd_thread_count() -> Any:
  """
  Current ingest/populate zstd ``-T`` setting (default 4 → ``-T4``).
  
  Returns:
    Any: Open return polymorphism from ``get_ingest_zstd_thread_count``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> get_ingest_zstd_thread_count()  # doctest: +SKIP
  """
  return cfg.get_ingest_zstd_threads()


def zstd_thread_count_for_wrap(apply_priority_wrap: Any) -> Any:
  """
  Niced archive paths use ``archive_zstd_threads``; un-niced ingest uses.
  
    ``ingest_zstd_threads``.
  
  Args:
    apply_priority_wrap (Any): Apply priority wrap passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> zstd_thread_count_for_wrap(None)  # doctest: +SKIP
  """
  if apply_priority_wrap:
    return get_archive_zstd_thread_count()
  return get_ingest_zstd_thread_count()


def _normalize_daily_tar_path_set(paths: Any) -> Any:
  """
  Return a frozenset of normalized daily ``.tar`` paths (empty for None).
  
  Args:
    paths (Any): Iterable of filesystem paths as strings.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _normalize_daily_tar_path_set(None)  # doctest: +SKIP
  """
  if not paths:
    return frozenset()
  return frozenset(os.path.normpath(p) for p in paths)


def daily_tar_path_in_maintenance_scope(
  tar_path: str,
  *,
  skip_daily_tar_paths: Any | None = None,
  only_daily_tar_paths: Any | None = None,
) -> Any:
  """
  True when ``tar_path`` should be included in a scoped maintenance pass.
  
  Args:
    tar_path (str): String for tar path.
    skip_daily_tar_paths (Any | None): One of ``Any``, ``None``.
    only_daily_tar_paths (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> daily_tar_path_in_maintenance_scope("x", None, None)  # doctest: +SKIP
  """
  normalized = os.path.normpath(tar_path)
  only_set = _normalize_daily_tar_path_set(only_daily_tar_paths)
  if only_set and normalized not in only_set:
    return False
  skip_set = _normalize_daily_tar_path_set(skip_daily_tar_paths)
  if skip_set and normalized in skip_set:
    return False
  return True


def daily_tar_paths_for_archive_job_tasks(deferred_paths: Any) -> Any:
  """
  Daily ``.tar`` paths for an in-flight ``archive_pool`` job.
  
    (``archive_job_deferred_paths``).
  
  Args:
    deferred_paths (Any): Iterable of filesystem paths as strings.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> daily_tar_paths_for_archive_job_tasks(None)  # doctest: +SKIP
  """
  if not deferred_paths:
    return frozenset()
  tar_paths = set()
  for item in deferred_paths:
    task = item.get("task") if isinstance(item, dict) else None
    if task is None:
      continue
    archive_info = getattr(task, "archive_info", None)
    if not archive_info or len(archive_info) < 1:
      continue
    compressed_path = archive_info[0]
    if not compressed_path:
      continue
    tar_paths.add(os.path.normpath(daily_tar_path_from_compressed(compressed_path)))
  return frozenset(tar_paths)


def daily_tar_paths_from_pending_archive_tasks(
  pending_archive_tasks: Any,
) -> Any:
  """
  Daily ``.tar`` paths for every queued archive task (heap entries or item.
  
    dicts).
  
  Accepts the supervisor ``pending_archive_tasks`` heap (tuples of
  ``(retry_at, attempt, seq, item)``) or a plain iterable of item dicts.
  
  Args:
    pending_archive_tasks (Any): Pending archive tasks passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> daily_tar_paths_from_pending_archive_tasks(None)  # doctest: +SKIP
  """
  if not pending_archive_tasks:
    return frozenset()
  tar_paths = set()
  for entry in pending_archive_tasks:
    if isinstance(entry, (tuple, list)) and len(entry) >= 4:
      item = entry[3]
    else:
      item = entry
    task = item.get("task") if isinstance(item, dict) else None
    if task is None:
      continue
    archive_info = getattr(task, "archive_info", None)
    if not archive_info or len(archive_info) < 1:
      continue
    compressed_path = archive_info[0]
    if not compressed_path:
      continue
    tar_paths.add(os.path.normpath(daily_tar_path_from_compressed(compressed_path)))
  return frozenset(tar_paths)


def _derive_stats_path_date(
  stats_path: str,
  first_ts: Any | None = None,
) -> Any:
  """
  Best-effort calendar date for a raw stats file.
  
  Prefers the parsed first timestamp (same value archival buckets by), then the
  numeric filename epoch, then file mtime. Returns ``None`` if none resolve.
  
  Args:
    stats_path (str): String for stats path.
    first_ts (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _derive_stats_path_date("x", None)  # doctest: +SKIP
  """
  if first_ts is not None:
    try:
      return datetime.fromtimestamp(float(first_ts)).date()
    except (TypeError, ValueError, OSError, OverflowError):
      pass
  try:
    return datetime.fromtimestamp(int(os.path.basename(stats_path))).date()
  except (TypeError, ValueError, OSError, OverflowError):
    pass
  try:
    return datetime.fromtimestamp(int(os.path.getmtime(stats_path))).date()
  except (OSError, OverflowError, ValueError):
    return None


def _daily_tar_path_for_date(tgz_archive_dir: str, file_date: Any) -> Any:
  """
  Normalized ``YYYY-MM-DD.tar`` path for ``file_date`` under.
  
    ``tgz_archive_dir``.
  
  Args:
    tgz_archive_dir (str): String for tgz archive dir.
    file_date (Any): File date passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _daily_tar_path_for_date("x", None)  # doctest: +SKIP
  """
  return os.path.normpath(
      daily_tar_path_from_compressed(
          daily_compressed_path_for_date(tgz_archive_dir, file_date),
      )
  )


def calendar_date_from_daily_tar_path(tar_path: str) -> Any:
  """
  Return ``date`` parsed from ``YYYY-MM-DD.tar`` basename, or ``None``.
  
  Args:
    tar_path (str): String for tar path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> calendar_date_from_daily_tar_path("x")  # doctest: +SKIP
  """
  base = os.path.basename(str(tar_path or ""))
  if not base.endswith(".tar"):
    return None
  try:
    return datetime.strptime(base[:-4], "%Y-%m-%d").date()
  except ValueError:
    return None


# Classic ustar size field (octal) max before GNU/pax extended headers.
USTAR_MAX_MEMBER_BYTES = 8589934591


def partition_paths_by_ustar_member_limit(
  paths: Any,
  *,
  limit: int = USTAR_MAX_MEMBER_BYTES,
) -> Any:
  """
  Split paths into (within_limit, oversized) by ``os.path.getsize``.
  
  Args:
    paths (Any): Iterable of filesystem paths as strings.
    limit (int): Integer value for limit.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> partition_paths_by_ustar_member_limit(None, 0)  # doctest: +SKIP
  """
  within = []
  oversized = []
  for path in paths or ():
    try:
      size = os.path.getsize(path)
    except OSError:
      within.append(path)
      continue
    if size > int(limit):
      oversized.append(path)
    else:
      within.append(path)
  return within, oversized


def classify_daily_tar_file_label(tar_path: str) -> Any:
  """
  Return ``file -b`` label for a daily ``.tar`` (empty string on failure).
  
  Args:
    tar_path (str): String for tar path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> classify_daily_tar_file_label("x")  # doctest: +SKIP
  """
  if not tar_path or not os.path.isfile(tar_path):
    return ""
  file_bin = shutil.which("file") or "file"
  try:
    result = subprocess.run(
        [file_bin, "-b", tar_path],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
  except (OSError, subprocess.TimeoutExpired):
    return ""
  return (result.stdout or "").strip()


def tar_file_label_is_gnu(label: Any) -> Any:
  """
  True when ``file`` label indicates GNU tar (``--posix`` unlocks giants).
  
  Args:
    label (Any): Label passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> tar_file_label_is_gnu(None)  # doctest: +SKIP
  """
  return "GNU" in str(label or "").upper()


def daily_tar_has_pax_extended_headers(
  tar_path: str,
  *,
  max_members: int = 40,
) -> Any:
  """
  True when the first ``max_members`` entries include pax extended headers.
  
  Args:
    tar_path (str): String for tar path.
    max_members (int): Integer value for max members.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> daily_tar_has_pax_extended_headers("x", 0)  # doctest: +SKIP
  """
  if not tar_path or not os.path.isfile(tar_path):
    return False
  try:
    with tarfile.open(tar_path, "r:") as tf:
      for idx, member in enumerate(tf):
        if idx >= int(max_members):
          break
        if getattr(member, "pax_headers", None):
          return True
        mtype = getattr(member, "type", None)
        if mtype in (getattr(tarfile, "XHDTYPE", b"x"), getattr(tarfile, "XGLTYPE", b"g")):
          return True
  except (OSError, tarfile.TarError):
    return False
  return False


def is_pax_capable_daily_tar(tar_path: str) -> Any:
  """
  True when giant members can append without extract+pax recreate.
  
  Missing tar (first create with ``--posix``) and GNU labels are capable;
  bare ``POSIX tar archive`` without pax headers is not.
  
  Args:
    tar_path (str): String for tar path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> is_pax_capable_daily_tar("x")  # doctest: +SKIP
  """
  if not tar_path or not os.path.isfile(tar_path):
    return True
  label = classify_daily_tar_file_label(tar_path)
  if tar_file_label_is_gnu(label):
    return True
  return daily_tar_has_pax_extended_headers(tar_path)


def free_bytes_for_path(path: str) -> Any:
  """
  Free bytes on the filesystem containing ``path`` (0 on error).
  
  Args:
    path (str): String for path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> free_bytes_for_path("x")  # doctest: +SKIP
  """
  try:
    target = os.path.dirname(os.path.abspath(path)) or os.sep
    return int(shutil.disk_usage(target).free)
  except (OSError, ValueError, TypeError):
    return 0


def convert_daily_tar_to_pax_via_extract_recreate(
  tar_path: str,
  *,
  log_fn: Any = log_print,
) -> Any:
  """
  Rewrite ``tar_path`` as ``--format=pax`` via extract + recreate.
  
  Holds ``file_write_lock`` for the mutate window. On any failure leaves the
  original tar untouched and returns ``False``.
  
  Args:
    tar_path (str): String for tar path.
    log_fn (Any): Callable invoked by this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> convert_daily_tar_to_pax_via_extract_recreate("x", None)
  """
  tar_path = os.path.abspath(str(tar_path or ""))
  if not os.path.isfile(tar_path):
    return True
  tar_size = os.path.getsize(tar_path)
  free = free_bytes_for_path(tar_path)
  if free < tar_size:
    if log_fn:
      log_fn(
          "WARNING: convert_fail reason=insufficient_free_space tar=%s "
          "size=%d free=%d"
          % (tar_path, tar_size, free),
          flush=True,
      )
    return False
  tar_bin = shutil.which("tar") or "/bin/tar"
  parent = os.path.dirname(tar_path) or "."
  work_root = tempfile.mkdtemp(prefix="hps_pax_convert_", dir=parent)
  extract_dir = os.path.join(work_root, "extract")
  new_tar = os.path.join(work_root, "new.tar")
  os.makedirs(extract_dir, exist_ok=True)
  try:
    with file_write_lock(tar_path):
      if log_fn:
        log_fn(
            "INFO: convert_start phase=extract tar=%s" % tar_path,
            flush=True,
        )
      extract = subprocess.run(
          [tar_bin, "-xf", tar_path, "-C", extract_dir],
          capture_output=True,
          text=True,
          check=False,
      )
      if extract.returncode != 0:
        if log_fn:
          log_fn(
              "WARNING: convert_fail phase=extract tar=%s rc=%s stderr=%s"
              % (
                  tar_path,
                  extract.returncode,
                  (extract.stderr or "").strip(),
              ),
              flush=True,
          )
        return False
      if log_fn:
        log_fn(
            "INFO: convert_start phase=recreate tar=%s" % tar_path,
            flush=True,
        )
      recreate = subprocess.run(
          [tar_bin, "--format=pax", "-cf", new_tar, "-C", extract_dir, "."],
          capture_output=True,
          text=True,
          check=False,
      )
      if recreate.returncode != 0 or not os.path.isfile(new_tar):
        if log_fn:
          log_fn(
              "WARNING: convert_fail phase=recreate tar=%s rc=%s stderr=%s"
              % (
                  tar_path,
                  recreate.returncode,
                  (recreate.stderr or "").strip(),
              ),
              flush=True,
          )
        return False
      os.replace(new_tar, tar_path)
    if log_fn:
      log_fn("INFO: convert_done tar=%s" % tar_path, flush=True)
    return True
  except (OSError, subprocess.SubprocessError, TimeoutError) as exc:
    if log_fn:
      log_fn(
          "WARNING: convert_fail tar=%s exc=%s" % (tar_path, exc),
          flush=True,
      )
    return False
  finally:
    shutil.rmtree(work_root, ignore_errors=True)


def prepare_paths_for_giant_member_append(
  tar_path: str,
  stats_files: Any,
  *,
  log_fn: Any = log_print,
) -> Any:
  """
  Ensure tar can accept giants, or skip oversized paths after convert fail.
  
  Returns ``(paths_to_append, skipped_oversized)``.
  
  Args:
    tar_path (str): String for tar path.
    stats_files (Any): Iterable of filesystem paths as strings.
    log_fn (Any): Callable invoked by this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> prepare_paths_for_giant_member_append("x", None, None)  # doctest: +SKIP
  """
  within, oversized = partition_paths_by_ustar_member_limit(stats_files)
  if not oversized:
    return list(stats_files or ()), []
  if is_pax_capable_daily_tar(tar_path):
    return list(stats_files or ()), []
  max_size = 0
  for path in oversized:
    try:
      max_size = max(max_size, int(os.path.getsize(path)))
    except OSError:
      pass
  label = (
      classify_daily_tar_file_label(tar_path)
      if os.path.isfile(tar_path)
      else "missing"
  )
  if log_fn:
    log_fn(
        "INFO: must_convert tar=%s reason=size_gt_ustar_not_pax_capable "
        "file_label=%r oversized_n=%d max_member_bytes=%d ustar_limit=%d"
        % (
            tar_path,
            label,
            len(oversized),
            max_size,
            USTAR_MAX_MEMBER_BYTES,
        ),
        flush=True,
    )
  if not os.path.isfile(tar_path):
    # First create uses ``--posix``; no on-disk convert required.
    return list(stats_files or ()), []
  if convert_daily_tar_to_pax_via_extract_recreate(tar_path, log_fn=log_fn):
    return list(stats_files or ()), []
  if log_fn:
    log_fn(
        "WARNING: convert_fail_skip tar=%s skipped_n=%d sample_paths=%s"
        % (
            tar_path,
            len(oversized),
            [os.path.basename(p) for p in oversized[:5]],
        ),
        flush=True,
    )
  return within, list(oversized)


def sort_archive_items_oldest_day_first(items: Any) -> Any:
  """
  Order archive tasks ``(compressed_path, paths)`` by calendar day ascending.
  
  Args:
    items (Any): Items passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> sort_archive_items_oldest_day_first(None)  # doctest: +SKIP
  """
  def _key(item: Any) -> Any:
    """
    Internal helper to handle key.
    
    Args:
      item (Any): Value to inspect (typically a numeric scalar).
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _key(None)  # doctest: +SKIP
    """
    try:
      compressed = item[0]
    except (TypeError, IndexError, KeyError):
      return (date.max, "")
    tar = daily_tar_path_from_compressed(compressed)
    day = calendar_date_from_daily_tar_path(tar)
    return (day or date.max, os.path.normpath(str(tar or "")))

  return sorted(list(items or ()), key=_key)


def stats_path_ingest_sort_epoch(stats_path: str) -> Any:
  """
  Oldest-first ingest sort key (numeric basename, else mtime; ``None`` last).
  
  Args:
    stats_path (str): String for stats path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> stats_path_ingest_sort_epoch("x")  # doctest: +SKIP
  """
  if not stats_path:
    return None
  try:
    return int(os.path.basename(stats_path))
  except (TypeError, ValueError):
    pass
  try:
    return int(os.path.getmtime(stats_path))
  except (OSError, OverflowError, ValueError):
    return None


def _basename_ingest_sort_epoch(stats_path: str) -> Any:
  """
  Stat-free epoch from numeric basename only (``None`` when non-numeric).
  
  Args:
    stats_path (str): String for stats path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _basename_ingest_sort_epoch("x")  # doctest: +SKIP
  """
  if not stats_path:
    return None
  try:
    return int(os.path.basename(stats_path))
  except (TypeError, ValueError):
    return None


def sort_pending_stats_paths_oldest_first(
  paths: Any,
  *,
  newest_first: bool = False,
) -> Any:
  """
  Return paths sorted by epoch, reversing for newest-first dispatch.
  
  Args:
    paths (Any): Iterable of filesystem paths as strings.
    newest_first (bool): Boolean flag for newest first.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> sort_pending_stats_paths_oldest_first(None, True)  # doctest: +SKIP
  """
  indexed = []
  for index, path in enumerate(paths or ()):
    if not path:
      continue
    epoch = stats_path_ingest_sort_epoch(path)
    indexed.append(((epoch is None, epoch), index, path))
  indexed.sort(key=lambda item: item[0])
  ordered = [path for _, _, path in indexed]
  if newest_first:
    ordered.reverse()
  return ordered


def pending_minus_chunk(
  pending: Any,
  chunk: Any,
  *,
  newest_first: bool = False,
) -> Any:
  """
  Return ``pending`` paths whose normpath is not in ``chunk`` (oldest-first.
  
    order).
  
  ``select_ingest_chunk_paths`` often returns a non-prefix subset of pending.
  Never advance the queue with ``pending[len(chunk):]`` — that requeues chunk
  members past index ``len(chunk)`` and drops non-chunk head paths.
  
  Args:
    pending (Any): Pending passed to this helper.
    chunk (Any): Task payload for a worker (tuple/list per this helper's
    protocol).
    newest_first (bool): Boolean flag for newest first.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> pending_minus_chunk(None, None, True)  # doctest: +SKIP
  """
  chunk_norms = {
      os.path.normpath(str(path))
      for path in (chunk or ())
      if path
  }
  if not chunk_norms:
    return [path for path in (pending or ()) if path]
  return [
      path
      for path in (pending or ())
      if path and os.path.normpath(str(path)) not in chunk_norms
  ]


def merge_rescan_discovered_into_pending(
  existing_pending: Any,
  discovered: Any,
  *,
  processed_exclude: Any | None = None,
  newest_first: bool = False,
) -> Any:
  """
  Union incremental rescan results with in-memory pending; oldest-first.
  
  Retains still-valid ``existing_pending`` paths (on disk, not processed) when
  incremental discovery only returns a host-dir subset.
  
  Args:
    existing_pending (Any): Existing pending passed to this helper.
    discovered (Any): Discovered passed to this helper.
    processed_exclude (Any | None): One of ``Any``, ``None``.
    newest_first (bool): Boolean flag for newest first.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> merge_rescan_discovered_into_pending(None, None, None, True)
  """
  exclude = set(processed_exclude or ())
  seen = set()
  merged = []
  for path in discovered or ():
    if not path or path in exclude or path in seen:
      continue
    seen.add(path)
    merged.append(path)
  for path in existing_pending or ():
    if not path or path in exclude or path in seen:
      continue
    if not os.path.isfile(path):
      continue
    seen.add(path)
    merged.append(path)
  return sort_pending_stats_paths_oldest_first(merged, newest_first=newest_first)


def resolve_idle_rescan_closed_paths(
  *,
  coordinator_snapshot: Any | None = None,
  accrual_snapshot: Any | None = None,
) -> Any:
  """
  Return ``(closed_paths, source)`` for idle rescan; prefer coordinator.
  
  Args:
    coordinator_snapshot (Any | None): One of ``Any``, ``None``.
    accrual_snapshot (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Open return polymorphism from ``resolve_idle_rescan_closed_paths``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> resolve_idle_rescan_closed_paths(None, None)  # doctest: +SKIP
  """
  if coordinator_snapshot is not None and coordinator_snapshot.closed_paths:
    return list(coordinator_snapshot.closed_paths), "coordinator"
  if accrual_snapshot is not None and accrual_snapshot.closed_paths:
    return list(accrual_snapshot.closed_paths), "accrual"
  return None, None


def supplement_pending_paths_from_closed_paths(
  paths: Any,
  *,
  closed_paths: Any,
  max_size: int,
  processed_exclude: Any | None = None,
  log_fn: Any = log_print,
  newest_first: bool = False,
) -> Any:
  """
  Merge snapshot ``closed_paths`` into pending and retain mode-ordered.
  
    ``max_size``.
  
  Always unions ``closed_paths`` (minus exclude) even when ``paths`` is already
    at
  ``max_size``, so older snapshot entries can displace newer queue heads under
  oldest-first (and newer displace older under ``newest_first=True``).
  
  Performance (RC-1): sort by the stat-free basename epoch first, then
  ``os.path.isfile`` only candidates that can enter the retained window. When
    the
  queue is already full and every candidate is on the wrong side of the cutoff,
  skip all ``isfile`` calls (zero-yield snapshot case).
  
  Args:
    paths (Any): Iterable of filesystem paths as strings.
    closed_paths (Any): Iterable of filesystem paths as strings.
    max_size (int): Integer value for max size.
    processed_exclude (Any | None): One of ``Any``, ``None``.
    log_fn (Any): Callable invoked by this helper.
    newest_first (bool): Boolean flag for newest first.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> supplement_pending_paths_from_closed_paths(0)  # doctest: +SKIP
  """
  max_size = max(1, int(max_size))
  exclude = set(processed_exclude or ())
  before = [path for path in (paths or ()) if path]
  before_set = set(before)
  candidates = []
  for path in closed_paths or ():
    if not path or path in exclude or path in before_set:
      continue
    candidates.append(path)
  if not candidates:
    return cap_pending_stats_file_list(
        sort_pending_stats_paths_oldest_first(before, newest_first=newest_first),
        max_size,
        log_fn=log_fn,
        newest_first=newest_first,
    )

  before_oldest = sort_pending_stats_paths_oldest_first(before, newest_first=False)
  retainable = candidates
  if len(before_oldest) >= max_size:
    if newest_first:
      cutoff = stats_path_ingest_sort_epoch(before_oldest[-max_size])
    else:
      cutoff = stats_path_ingest_sort_epoch(before_oldest[max_size - 1])
    retainable = []
    for path in candidates:
      ep = _basename_ingest_sort_epoch(path)
      if ep is None:
        # Non-numeric basenames need getmtime; keep them for lazy isfile.
        retainable.append(path)
        continue
      if cutoff is None:
        retainable.append(path)
        continue
      if newest_first:
        if ep >= cutoff:
          retainable.append(path)
      elif ep <= cutoff:
        retainable.append(path)
    if not retainable:
      return cap_pending_stats_file_list(
          sort_pending_stats_paths_oldest_first(before, newest_first=newest_first),
          max_size,
          log_fn=log_fn,
          newest_first=newest_first,
      )

  def _candidate_epoch_key(path: str) -> Any:
    """
    Internal helper to handle candidate epoch key.
    
    Args:
      path (str): String for path.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _candidate_epoch_key("x")  # doctest: +SKIP
    """
    ep = _basename_ingest_sort_epoch(path)
    return (ep is None, ep if ep is not None else 0, path)

  if newest_first:
    candidates_ordered = sorted(
        retainable,
        key=lambda path: (
            _basename_ingest_sort_epoch(path) is None,
            -(_basename_ingest_sort_epoch(path) or 0),
            path,
        ),
    )
  else:
    candidates_ordered = sorted(retainable, key=_candidate_epoch_key)

  result = list(before)
  seen = set(before)
  supplemented = 0
  for path in candidates_ordered:
    if path in seen:
      continue
    if len(result) >= max_size:
      result_oldest = sort_pending_stats_paths_oldest_first(result, newest_first=False)
      ep = _basename_ingest_sort_epoch(path)
      if ep is not None:
        if newest_first:
          cutoff = stats_path_ingest_sort_epoch(result_oldest[-max_size])
          if cutoff is not None and ep < cutoff:
            break
        else:
          cutoff = stats_path_ingest_sort_epoch(result_oldest[max_size - 1])
          if cutoff is not None and ep > cutoff:
            break
    if not os.path.isfile(path):
      continue
    seen.add(path)
    result.append(path)
    supplemented += 1

  capped = cap_pending_stats_file_list(
      sort_pending_stats_paths_oldest_first(result, newest_first=newest_first),
      max_size,
      log_fn=log_fn,
      newest_first=newest_first,
  )
  if supplemented and log_fn is not None:
    at_max_before = len(before) >= max_size
    replaced = 0
    if at_max_before:
      replaced = sum(1 for path in capped if path not in before_set)
      if replaced:
        log_fn(
            "pending cap supplement replace n=%d pending=%d"
            % (replaced, len(capped)),
            flush=True,
        )
      elif supplemented:
        log_fn(
            "pending cap supplement from snapshot n=%d pending=%d"
            % (supplemented, len(capped)),
            flush=True,
        )
    else:
      log_fn(
          "pending cap supplement from snapshot n=%d pending=%d"
          % (supplemented, len(capped)),
          flush=True,
      )
  return capped


def build_giant_supplement_pending_tail(
  paths: Any,
  *,
  closed_paths: Any,
  supplement_queue: Any,
  processed_exclude: Any | None = None,
  log_fn: Any = log_print,
  newest_first: bool = False,
) -> Any:
  """
  Build giant-supplement ``pending_tail`` capped at ``supplement_queue``.
  
  Used at giant-supplement batch start and for mid-imap refresh from a closed-
    path
  snapshot. Same ceiling for both (default queue*multiplier = 6000).
  
  Args:
    paths (Any): Iterable of filesystem paths as strings.
    closed_paths (Any): Iterable of filesystem paths as strings.
    supplement_queue (Any): Supplement queue passed to this helper.
    processed_exclude (Any | None): One of ``Any``, ``None``.
    log_fn (Any): Callable invoked by this helper.
    newest_first (bool): Boolean flag for newest first.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> build_giant_supplement_pending_tail(None, None, None, None, None, True)
  """
  return supplement_pending_paths_from_closed_paths(
      paths,
      closed_paths=closed_paths,
      max_size=max(1, int(supplement_queue)),
      processed_exclude=processed_exclude,
      log_fn=log_fn,
      newest_first=newest_first,
  )


def cap_pending_stats_with_blocked_retention(
  paths: Any,
  *,
  max_size: int,
  blocked_paths: Any | None = None,
  handoff_priority_paths: Any | None = None,
  log_fn: Any = log_print,
  newest_first: bool = False,
) -> Any:
  """
  Cap pending while preserving blocked head and handoff priority paths.
  
  Args:
    paths (Any): Iterable of filesystem paths as strings.
    max_size (int): Integer value for max size.
    blocked_paths (Any | None): One of ``Any``, ``None``.
    handoff_priority_paths (Any | None): One of ``Any``, ``None``.
    log_fn (Any): Callable invoked by this helper.
    newest_first (bool): Boolean flag for newest first.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> cap_pending_stats_with_blocked_retention(0)  # doctest: +SKIP
  """
  max_size = max(1, int(max_size))
  merged = sort_pending_stats_paths_oldest_first(
      list(paths or ()),
      newest_first=newest_first,
  )
  blocked = list(blocked_paths or ())
  if newest_first:
    blocked = sort_pending_stats_paths_oldest_first(
        blocked,
        newest_first=True,
    )
  handoff = list(handoff_priority_paths or ())
  if not blocked and not handoff:
    return cap_pending_stats_file_list(
        merged,
        max_size,
        log_fn=log_fn,
        newest_first=newest_first,
    )
  reserved = list(blocked)
  reserved_set = set(reserved)
  priority_n = len(handoff)
  tail_budget = max(0, max_size - priority_n - len(reserved))
  head = [path for path in handoff if path in merged or path in set(handoff)]
  if len(head) < priority_n:
    for path in merged:
      if path in head or path in reserved_set:
        continue
      head.append(path)
      if len(head) >= priority_n:
        break
  tail_paths = [
      path for path in merged
      if path not in reserved_set and path not in set(head)
  ]
  capped = reserved + head + tail_paths[:tail_budget]
  if len(capped) > max_size:
    capped = capped[:max_size]
  if len(merged) > len(capped) and log_fn is not None:
    log_fn(
        "Pending stats file list truncated pending=%d max=%d"
        % (len(merged), max_size),
        flush=True,
    )
  return capped


def chunk_was_cross_day_defer_dispatch(
  chunk_paths: Any,
  oldest_tar_norm: Any,
  *,
  incomplete_n: Any,
  tgz_archive_dir: str,
) -> Any:
  """
  True when chunk paths are not aligned with oldest checkpoint-incomplete tar.
  
  Args:
    chunk_paths (Any): Iterable of filesystem paths as strings.
    oldest_tar_norm (Any): Oldest tar norm passed to this helper.
    incomplete_n (Any): Incomplete n passed to this helper.
    tgz_archive_dir (str): String for tgz archive dir.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> chunk_was_cross_day_defer_dispatch(None, None, None, "x")
  """
  if not oldest_tar_norm or incomplete_n <= 0 or not chunk_paths or not tgz_archive_dir:
    return False
  aligned = checkpoint_incomplete_paths_aligned_with_oldest_tar(
      chunk_paths,
      oldest_tar_norm,
      tgz_archive_dir=tgz_archive_dir,
  )
  return len(aligned) < len(chunk_paths)


def all_ingest_outcomes_db_skip_head_tail(outcomes: Any) -> Any:
  """
  True when every recorded ingest outcome is db_skip=head_tail.
  
  Args:
    outcomes (Any): Outcomes passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> all_ingest_outcomes_db_skip_head_tail(None)  # doctest: +SKIP
  """
  if not outcomes:
    return False
  for _path, outcome, db_skip in outcomes:
    if outcome != "db_skip" or db_skip != "head_tail":
      return False
  return True


def checkpoint_incomplete_paths_aligned_with_oldest_tar(
  blocked_paths: Any,
  oldest_tar_norm: Any,
  *,
  tgz_archive_dir: str,
) -> Any:
  """
  Subset of ``blocked_paths`` whose calendar tar mapping includes.
  
    ``oldest_tar``.
  
  Args:
    blocked_paths (Any): Iterable of filesystem paths as strings.
    oldest_tar_norm (Any): Oldest tar norm passed to this helper.
    tgz_archive_dir (str): String for tgz archive dir.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> checkpoint_incomplete_paths_aligned_with_oldest_tar(None, None, "x")
  """
  if not oldest_tar_norm or not tgz_archive_dir:
    return []
  aligned = []
  for path in blocked_paths or ():
    if not path:
      continue
    if oldest_tar_norm in daily_tar_paths_for_stats_paths(
        [path],
        tgz_archive_dir,
    ):
      aligned.append(path)
  return aligned


def stats_path_aligned_to_daily_tar(
  stats_path: str,
  tar_norm: Any,
  *,
  tgz_archive_dir: str,
) -> Any:
  """
  True when filename/mtime calendar day for ``stats_path`` maps to ``tar_norm``.
  
  Intentionally omits first-timestamp overrides so first_ts misbuckets cannot
  pin the wrong calendar day (same law as checkpoint unprocessed alignment).
  
  Args:
    stats_path (str): String for stats path.
    tar_norm (Any): Tar norm passed to this helper.
    tgz_archive_dir (str): String for tgz archive dir.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> stats_path_aligned_to_daily_tar("x", None, "x")  # doctest: +SKIP
  """
  tar_key = os.path.normpath(str(tar_norm or ""))
  if not tar_key or not tgz_archive_dir or not stats_path:
    return False
  return tar_key in daily_tar_paths_for_stats_paths(
      [stats_path],
      tgz_archive_dir,
  )


def filter_remaining_raw_aligned_to_tar(
  remaining_by_gz: Any,
  tar_norm: Any,
  *,
  tgz_archive_dir: str,
) -> Any:
  """
  Keep only remaining-raw paths whose filename/mtime day maps to ``tar_norm``.
  
  Census maps may key paths under a tar via first_ts while the filename epoch
  belongs to another calendar day. Those cross-day misbuckets must not block
  FS-complete / needs_work / tar-drop for the wrong day.
  
  Args:
    remaining_by_gz (Any): Remaining by gz passed to this helper.
    tar_norm (Any): Tar norm passed to this helper.
    tgz_archive_dir (str): String for tgz archive dir.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> filter_remaining_raw_aligned_to_tar(None, None, "x")  # doctest: +SKIP
  """
  tar_key = os.path.normpath(str(tar_norm or ""))
  if not remaining_by_gz or not tar_key or not tgz_archive_dir:
    return {}
  filtered = {}
  for gz_key, raw_list in remaining_by_gz.items():
    blockers = [
        path
        for path in (raw_list or ())
        if path
        and stats_path_aligned_to_daily_tar(
            path,
            tar_key,
            tgz_archive_dir=tgz_archive_dir,
        )
    ]
    if blockers:
      filtered[gz_key] = blockers
  return filtered


def remaining_raw_on_disk_counts_for_tar(
  remaining_raw_by_gz: Any,
  tar_norm: Any,
  *,
  tgz_archive_dir: str,
) -> Any:
  """
  Return ``(aligned_on_disk_n, cross_day_on_disk_n)`` for census under ``tar``.
  
  Paths keyed to this tar via first_ts but whose filename/mtime day differs are
  counted as cross-day (diagnostic only; they must not block this day).
  
  Args:
    remaining_raw_by_gz (Any): Remaining raw by gz passed to this helper.
    tar_norm (Any): Tar norm passed to this helper.
    tgz_archive_dir (str): String for tgz archive dir.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> remaining_raw_on_disk_counts_for_tar(None, None, "x")  # doctest: +SKIP
  """
  aligned_paths = remaining_raw_aligned_paths_for_tar(
      remaining_raw_by_gz,
      tar_norm,
      tgz_archive_dir=tgz_archive_dir,
  )
  tar_key = os.path.normpath(str(tar_norm or ""))
  if not tar_key or not tgz_archive_dir:
    return 0, 0
  cross_day_n = 0
  for gz_key, paths in (remaining_raw_by_gz or {}).items():
    if not paths:
      continue
    if os.path.normpath(daily_tar_path_from_compressed(gz_key)) != tar_key:
      continue
    for path in paths:
      if not path or not os.path.isfile(path):
        continue
      if not stats_path_aligned_to_daily_tar(
          path,
          tar_key,
          tgz_archive_dir=tgz_archive_dir,
      ):
        cross_day_n += 1
  return len(aligned_paths), cross_day_n


def remaining_raw_aligned_paths_for_tar(
  remaining_raw_by_gz: Any,
  tar_norm: Any,
  *,
  tgz_archive_dir: str,
) -> Any:
  """
  Aligned on-disk remaining-raw paths for ``tar_norm`` (census inventory).
  
  Args:
    remaining_raw_by_gz (Any): Remaining raw by gz passed to this helper.
    tar_norm (Any): Tar norm passed to this helper.
    tgz_archive_dir (str): String for tgz archive dir.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> remaining_raw_aligned_paths_for_tar(None, None, "x")  # doctest: +SKIP
  """
  tar_key = os.path.normpath(str(tar_norm or ""))
  if not tar_key or not tgz_archive_dir:
    return []
  aligned = []
  for gz_key, paths in (remaining_raw_by_gz or {}).items():
    if not paths:
      continue
    if os.path.normpath(daily_tar_path_from_compressed(gz_key)) != tar_key:
      continue
    for path in paths:
      if not path or not os.path.isfile(path):
        continue
      if stats_path_aligned_to_daily_tar(
          path,
          tar_key,
          tgz_archive_dir=tgz_archive_dir,
      ):
        aligned.append(path)
  return aligned


def daily_tar_path_for_stats_path(
  stats_path: str,
  tgz_archive_dir: str,
  first_ts: Any | None = None,
) -> Any:
  """
  Normalized daily ``.tar`` path for a raw stats file.
  
  Args:
    stats_path (str): String for stats path.
    tgz_archive_dir (str): String for tgz archive dir.
    first_ts (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> daily_tar_path_for_stats_path("x", "x", None)  # doctest: +SKIP
  """
  file_date = _derive_stats_path_date(stats_path, first_ts)
  if file_date is None or not tgz_archive_dir:
    return None
  return _daily_tar_path_for_date(tgz_archive_dir, file_date)


def _day_phase_name_from_hints(day_phases: Any, tar_path: str) -> Any:
  """
  Internal helper to handle day phase name from hints.
  
  Args:
    day_phases (Any): Day phases passed to this helper.
    tar_path (str): String for tar path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _day_phase_name_from_hints(None, "x")  # doctest: +SKIP
  """
  from hpcperfstats.dbload.lib.sync_timedb_manifest_contract import (
      day_phase_name_from_hints,
  )
  return day_phase_name_from_hints(day_phases, tar_path)


def _day_phase_at_least_hints(
  day_phases: Any,
  tar_path: str,
  target: Any,
) -> Any:
  """
  Internal helper to handle day phase at least hints.
  
  Args:
    day_phases (Any): Day phases passed to this helper.
    tar_path (str): String for tar path.
    target (Any): Target passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _day_phase_at_least_hints(None, "x", None)  # doctest: +SKIP
  """
  from hpcperfstats.dbload.lib.sync_timedb_manifest_contract import (
      day_phase_at_least,
  )
  return day_phase_at_least(day_phases, tar_path, target)


def ingest_stream_past_calendar_day(
  day_date: Any,
  *,
  pending_stats_paths: Any,
  max_sort_epoch_for_day: Any,
  first_timestamp_by_path: Any | None = None,
  newest_first: bool = False,
) -> Any:
  """
  True when the ingest stream has moved past ``day_date``.
  
  Oldest-first (default) compares the **minimum** pending sort epoch.
  Newest-first compares the **maximum** pending sort epoch so day-close
  coupling does not treat ancient backlog as "still on" the day.
  
  Args:
    day_date (Any): Day date passed to this helper.
    pending_stats_paths (Any): Iterable of filesystem paths as strings.
    max_sort_epoch_for_day (Any): Max sort epoch for day passed to this
    helper.
    first_timestamp_by_path (Any | None): One of ``Any``, ``None``.
    newest_first (bool): Boolean flag for newest first.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> ingest_stream_past_calendar_day(None, None, None, None, True)
  """
  if day_date is None:
    return False
  fmap = first_timestamp_by_path or {}
  for path in pending_stats_paths or ():
    if _derive_stats_path_date(path, fmap.get(path)) == day_date:
      return False
  if not pending_stats_paths:
    return True
  if max_sort_epoch_for_day is None:
    return True
  extremum = None
  for path in pending_stats_paths:
    epoch = stats_path_ingest_sort_epoch(path)
    if epoch is None:
      continue
    if extremum is None:
      extremum = epoch
    elif newest_first:
      extremum = max(extremum, epoch)
    else:
      extremum = min(extremum, epoch)
  if extremum is None:
    return True
  return extremum > max_sort_epoch_for_day


def daily_tar_eligible_for_day_close_submit(
  tar_norm: Any,
  *,
  unprocessed_by_tar: Any,
  disqualified_daily_tars: Any,
  day_phases: Any | None = None,
  remaining_raw_by_gz: Any | None = None,
  local_tz: Any | None = None,
  now: Any | None = None,
  day_raw_removal: Any | None = None,
  tgz_archive_dir: Any | None = None,
) -> Any:
  """
  Return ``(eligible, skip_reason)`` for async ``DAY_CLOSE`` submit.
  
  Args:
    tar_norm (Any): Tar norm passed to this helper.
    unprocessed_by_tar (Any): Unprocessed by tar passed to this helper.
    disqualified_daily_tars (Any): Disqualified daily tars passed to this
    helper.
    day_phases (Any | None): One of ``Any``, ``None``.
    remaining_raw_by_gz (Any | None): One of ``Any``, ``None``.
    local_tz (Any | None): One of ``Any``, ``None``.
    now (Any | None): One of ``Any``, ``None``.
    day_raw_removal (Any | None): One of ``Any``, ``None``.
    tgz_archive_dir (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> daily_tar_eligible_for_day_close_submit(0)  # doctest: +SKIP
  """
  tar_norm = os.path.normpath(str(tar_norm or ""))
  if not tar_norm:
    return False, "invalid_tar_path"
  if unprocessed_by_tar is None:
    return False, "missing_unprocessed_map"
  archive_dir = tgz_archive_dir or os.path.dirname(tar_norm)
  if aligned_unprocessed_tar_paths_still_on_disk(
      unprocessed_by_tar,
      tar_norm,
      tgz_archive_dir=archive_dir,
  ):
    return False, "checkpoint_incomplete"
  disqualified = _normalize_daily_tar_path_set(disqualified_daily_tars)
  if tar_norm in disqualified:
    return False, "disqualified"
  if local_tz is not None and not daily_tar_seal_calendar_eligible(
      tar_norm, local_tz, now=now):
    return False, "calendar_grace"
  if not daily_tar_needs_day_close_work(
      tar_norm,
      day_phases=day_phases,
      remaining_raw_by_gz=remaining_raw_by_gz,
  ):
    return False, "no_work"
  return True, ""


def find_immediate_day_close_candidates(
  *,
  tgz_archive_dir: str,
  candidate_tar_paths: Any,
  disqualified_daily_tars: Any,
  pending_stats_paths: Any | None = None,
  unprocessed_by_tar: Any | None = None,
  max_sort_epoch_by_tar: Any | None = None,
  local_tz: Any,
  now: Any | None = None,
  day_phases: Any | None = None,
  first_timestamp_by_path: Any | None = None,
  remaining_raw_by_gz: Any | None = None,
) -> Any:
  """
  Return oldest-first daily ``.tar`` paths eligible for immediate ``DAY_CLOSE``.
  
  Eligibility uses checkpoint subtraction (``unprocessed_by_tar``) rather than
  the global ingest queue when ``unprocessed_by_tar`` is provided.
  
  Args:
    tgz_archive_dir (str): String for tgz archive dir.
    candidate_tar_paths (Any): Iterable of filesystem paths as strings.
    disqualified_daily_tars (Any): Disqualified daily tars passed to this
    helper.
    pending_stats_paths (Any | None): One of ``Any``, ``None``.
    unprocessed_by_tar (Any | None): One of ``Any``, ``None``.
    max_sort_epoch_by_tar (Any | None): One of ``Any``, ``None``.
    local_tz (Any): Local tz passed to this helper.
    now (Any | None): One of ``Any``, ``None``.
    day_phases (Any | None): One of ``Any``, ``None``.
    first_timestamp_by_path (Any | None): One of ``Any``, ``None``.
    remaining_raw_by_gz (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> find_immediate_day_close_candidates(0)  # doctest: +SKIP
  """
  del pending_stats_paths, max_sort_epoch_by_tar, first_timestamp_by_path
  if not tgz_archive_dir:
    return []
  if unprocessed_by_tar is None:
    return []
  seen = set()
  ranked = []
  for tar_path in candidate_tar_paths or ():
    tar_norm = os.path.normpath(str(tar_path or ""))
    if not tar_norm or tar_norm in seen:
      continue
    seen.add(tar_norm)
    eligible, _reason = daily_tar_eligible_for_day_close_submit(
        tar_norm,
        unprocessed_by_tar=unprocessed_by_tar,
        disqualified_daily_tars=disqualified_daily_tars,
        day_phases=day_phases,
        remaining_raw_by_gz=remaining_raw_by_gz,
        local_tz=local_tz,
        now=now,
        tgz_archive_dir=tgz_archive_dir,
    )
    if not eligible:
      continue
    day_date = calendar_date_from_daily_tar_path(tar_norm)
    if day_date is None:
      continue
    ranked.append((day_date, tar_norm))
  ranked.sort(key=lambda item: item[0])
  return [tar_norm for _, tar_norm in ranked]


_DAY_CLOSE_DISQUALIFY_CODES = frozenset({
    "inflight_append_path",
    "pending_append_cache",
    "in_flight_archive_job",
    "pending_archive_task",
    "unmapped_closed_raw",
    "calendar_grace",
})


def classify_day_close_candidates(
  *,
  tgz_archive_dir: str,
  remaining_raw_by_gz: Any | None = None,
  unprocessed_by_tar: Any | None = None,
  disqualification_reasons: Any | None = None,
  day_phases: Any | None = None,
  local_tz: Any | None = None,
  now: Any | None = None,
  day_close_in_progress_tars: Any | None = None,
  debt_heap_tars: Any | None = None,
  newly_queued_tars: Any | None = None,
  queued_reason: str = "scheduled_enqueue",
  day_raw_removal: Any | None = None,
) -> Any:
  """
  Classify day-close universe into queued/disqualified/skipped_no_work entries.
  
  Args:
    tgz_archive_dir (str): String for tgz archive dir.
    remaining_raw_by_gz (Any | None): One of ``Any``, ``None``.
    unprocessed_by_tar (Any | None): One of ``Any``, ``None``.
    disqualification_reasons (Any | None): One of ``Any``, ``None``.
    day_phases (Any | None): One of ``Any``, ``None``.
    local_tz (Any | None): One of ``Any``, ``None``.
    now (Any | None): One of ``Any``, ``None``.
    day_close_in_progress_tars (Any | None): One of ``Any``, ``None``.
    debt_heap_tars (Any | None): One of ``Any``, ``None``.
    newly_queued_tars (Any | None): One of ``Any``, ``None``.
    queued_reason (str): String for queued reason.
    day_raw_removal (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> classify_day_close_candidates(0)  # doctest: +SKIP
  """
  if not tgz_archive_dir:
    return []
  unprocessed = unprocessed_by_tar or {}
  disq = disqualification_reasons or {}
  async_active = _normalize_daily_tar_path_set(day_close_in_progress_tars)
  debt_tars = _normalize_daily_tar_path_set(debt_heap_tars)
  newly_queued = _normalize_daily_tar_path_set(newly_queued_tars)
  universe = set()
  for tar_path in iter_daily_tar_paths(tgz_archive_dir):
    universe.add(os.path.normpath(tar_path))
  for tar_path in (remaining_raw_by_gz or {}):
    universe.add(os.path.normpath(daily_tar_path_from_compressed(tar_path)))
  universe |= set(unprocessed.keys())
  universe |= set(disq.keys())
  universe |= async_active | debt_tars | newly_queued
  ranked = sorted(universe, key=_calendar_date_from_tar_sort_key)
  entries = []
  for tar_norm in ranked:
    reasons = set(disq.get(tar_norm, set()))
    unprocessed_list_count = len(unprocessed.get(tar_norm, ()))
    on_disk_all_n = count_unprocessed_paths_on_disk(unprocessed, tar_norm)
    unprocessed_paths = aligned_on_disk_unprocessed_paths_for_tar(
        unprocessed,
        tar_norm,
        tgz_archive_dir=tgz_archive_dir,
    )
    unprocessed_count = len(unprocessed_paths)
    cross_day_n = max(0, on_disk_all_n - unprocessed_count)
    unprocessed_norm = {
        os.path.normpath(str(path)) for path in unprocessed_paths if path
    }
    # Checkpoint-complete leftover = aligned remaining_raw on disk that is not
    # also in the unprocessed set (avoids double-counting when remaining_raw
    # includes checkpoint-incomplete closed raw).
    remaining_aligned_n, processed_cross_day_n = remaining_raw_on_disk_counts_for_tar(
        remaining_raw_by_gz,
        tar_norm,
        tgz_archive_dir=tgz_archive_dir,
    )
    remaining_aligned_paths = remaining_raw_aligned_paths_for_tar(
        remaining_raw_by_gz,
        tar_norm,
        tgz_archive_dir=tgz_archive_dir,
    )
    processed_but_on_disk = len(
        [
            path
            for path in remaining_aligned_paths
            if os.path.normpath(str(path)) not in unprocessed_norm
        ]
    )
    # Total aligned closed-raw on disk = unprocessed + checkpoint leftover.
    on_disk_total = unprocessed_count + processed_but_on_disk
    # Prefer remaining∪unprocessed when remaining missed some unprocessed paths
    # already counted above; keep invariant on_disk == unprocessed + processed.
    _ = remaining_aligned_n  # census diagnostic only; decision uses partition
    if unprocessed_count == 0:
      reasons.discard("checkpoint_incomplete")
    phase_name = _day_phase_name_from_hints(day_phases, tar_norm) or ""
    mutable_tar = os.path.isfile(tar_norm)
    needs_work = daily_tar_needs_day_close_work(
        tar_norm,
        day_phases=day_phases,
        remaining_raw_by_gz=remaining_raw_by_gz,
    )
    if not needs_work:
      entry = {
          "tar_path": tar_norm,
          "status": "skipped_no_work",
          "reasons": sorted(reasons),
          "on_disk": on_disk_total,
          "unprocessed": unprocessed_count,
          "phase": phase_name,
          "mutable_tar": mutable_tar,
          "processed_but_on_disk": processed_but_on_disk,
      }
      if cross_day_n:
        entry["unprocessed_cross_day_n"] = cross_day_n
      if processed_cross_day_n:
        entry["processed_cross_day_n"] = processed_cross_day_n
      entries.append(entry)
      continue
    blocking = reasons & _DAY_CLOSE_DISQUALIFY_CODES
    if tar_norm in async_active:
      status = "queued"
      reasons = set(reasons)
      reasons.add("day_close_in_progress")
    elif tar_norm in newly_queued:
      status = "queued"
      reasons = set(reasons)
      reasons.discard("not_enqueued")
      reasons.add(queued_reason or "scheduled_enqueue")
    elif tar_norm in debt_tars:
      status = "queued"
      reasons = set(reasons)
      reasons.add("already_in_debt_heap")
    elif unprocessed_count > 0:
      status = "waiting_on_ingest"
      reasons = set(reasons)
      reasons.add("checkpoint_incomplete")
    elif blocking:
      status = "disqualified"
    else:
      status = "ready_for_enqueue"
      reasons = set(reasons)
      reasons.add("awaiting_janitor_discover")
    if mutable_tar:
      reasons.add("mutable_tar_present")
    entry = {
        "tar_path": tar_norm,
        "status": status,
        "reasons": sorted(reasons),
        "on_disk": on_disk_total,
        "unprocessed": unprocessed_count,
        "phase": phase_name,
        "mutable_tar": mutable_tar,
        "processed_but_on_disk": processed_but_on_disk,
    }
    if unprocessed_list_count != unprocessed_count:
      entry["unprocessed_list"] = unprocessed_list_count
    if cross_day_n:
      entry["unprocessed_cross_day_n"] = cross_day_n
    if processed_cross_day_n:
      entry["processed_cross_day_n"] = processed_cross_day_n
    entries.append(entry)
  return entries


_oldest_waiting_ingest_frozen_state = {
    "tar": None,
    "unprocessed": None,
    "streak": 0,
}


def reset_oldest_day_unprocessed_frozen_state_for_tests() -> None:
  """
  Test helper: clear module-level frozen tracker.
  
  Returns:
    None
  
  Examples:
    >>> reset_oldest_day_unprocessed_frozen_state_for_tests()  # doctest: +SKIP
  """
  _oldest_waiting_ingest_frozen_state.update(
      tar=None,
      unprocessed=None,
      streak=0,
  )


def maybe_log_oldest_day_unprocessed_frozen(
  waiting_entries: Any,
  *,
  log_fn: Any = log_print,
) -> None:
  """
  WARN when oldest waiting_on_ingest unprocessed count is unchanged across.
  
    reports.
  
  Args:
    waiting_entries (Any): Waiting entries passed to this helper.
    log_fn (Any): Callable invoked by this helper.
  
  Returns:
    None
  
  Examples:
    >>> maybe_log_oldest_day_unprocessed_frozen(None, None)  # doctest: +SKIP
  """
  waiting = list(waiting_entries or ())
  state = _oldest_waiting_ingest_frozen_state
  if not waiting:
    state.update(tar=None, unprocessed=None, streak=0)
    return
  oldest = min(waiting, key=lambda entry: str(entry.get("tar_path") or ""))
  tar = oldest.get("tar_path")
  unprocessed = int(oldest.get("unprocessed") or 0)
  if state["tar"] == tar and state["unprocessed"] == unprocessed:
    state["streak"] = int(state.get("streak") or 0) + 1
  else:
    state.update(tar=tar, unprocessed=unprocessed, streak=1)
  if int(state["streak"]) >= 2:
    log_fn(
        "WARN: oldest_day_unprocessed_frozen oldest_tar=%s unprocessed=%d streak=%d"
        % (tar, unprocessed, int(state["streak"])),
        flush=True,
    )


def log_day_close_candidate_report(
  entries: Any,
  *,
  reason: Any,
  log_fn: Any = log_print,
  async_progress_fn: Any | None = None,
) -> None:
  """
  Log day-close candidates (silent skipped_no_work), oldest calendar day first.
  
  Args:
    entries (Any): Entries passed to this helper.
    reason (Any): Reason passed to this helper.
    log_fn (Any): Callable invoked by this helper.
    async_progress_fn (Any | None): One of ``Any``, ``None``.
  
  Returns:
    None
  
  Examples:
    >>> log_day_close_candidate_report(None, None, None, None)  # doctest: +SKIP
  """
  if not cfg.get_sync_day_close_candidate_report():
    return
  queued = [e for e in entries if e.get("status") == "queued"]
  waiting = [e for e in entries if e.get("status") == "waiting_on_ingest"]
  ready = [e for e in entries if e.get("status") == "ready_for_enqueue"]
  disqualified = [e for e in entries if e.get("status") == "disqualified"]
  maybe_log_oldest_day_unprocessed_frozen(waiting, log_fn=log_fn)
  reportable = [
      e for e in entries if e.get("status") != "skipped_no_work"
  ]
  if not reportable:
    return
  reportable.sort(key=lambda e: _calendar_date_from_tar_sort_key(e.get("tar_path")))
  queued_ordered = [
      e for e in reportable if e.get("status") == "queued"
  ]
  queue_slot = {
      os.path.normpath(str(e.get("tar_path") or "")): index
      for index, e in enumerate(queued_ordered, start=1)
  }
  mutable_tar_n = sum(1 for e in reportable if e.get("mutable_tar"))
  log_fn(
      "janitor: day_close candidate report reason=%s queued=%d "
      "waiting_on_ingest=%d ready_for_enqueue=%d disqualified=%d "
      "mutable_tar_n=%d"
      % (
          reason,
          len(queued),
          len(waiting),
          len(ready),
          len(disqualified),
          mutable_tar_n,
      ),
      flush=True,
  )
  for entry in reportable:
    reasons = list(entry.get("reasons") or ())
    async_suffix = ""
    if (
        async_progress_fn is not None
        and entry.get("status") == "queued"
        and "day_close_in_progress" in reasons
    ):
      tar_path = entry.get("tar_path")
      if tar_path:
        try:
          progress = async_progress_fn(tar_path) or {}
        except Exception:
          progress = {}
        last_progress = progress.get("last_progress") or ""
        age_s = progress.get("last_progress_age_s")
        if last_progress or age_s is not None:
          age_text = (
              "%.0f" % float(age_s)
              if age_s is not None
              else ""
          )
          async_suffix = " async_last_progress=%s async_age_s=%s" % (
              last_progress,
              age_text,
          )
    unprocessed_on_disk = int(entry.get("unprocessed") or 0)
    on_disk_total = int(entry.get("on_disk") or 0)
    if on_disk_total <= 0:
      on_disk_total = unprocessed_on_disk + int(
          entry.get("processed_but_on_disk") or 0,
      )
    unprocessed_list = entry.get("unprocessed_list")
    cross_day_n = int(entry.get("unprocessed_cross_day_n") or 0)
    ghost_suffix = " on_disk=%d" % on_disk_total
    if unprocessed_list is not None:
      ghosts = max(0, int(unprocessed_list) - unprocessed_on_disk - cross_day_n)
      ghost_suffix += " ghosts=%d" % ghosts
      if ghosts > 0 and entry.get("status") == "waiting_on_ingest":
        log_fn(
            "WARN: day_close candidate tar=%s checkpoint_unprocessed_ghosts=%d "
            "list=%d on_disk_unprocessed=%d"
            % (
                entry.get("tar_path"),
                ghosts,
                int(unprocessed_list),
                unprocessed_on_disk,
            ),
            flush=True,
        )
    if cross_day_n:
      ghost_suffix += " unprocessed_cross_day_n=%d" % cross_day_n
    processed_but_on_disk = int(entry.get("processed_but_on_disk") or 0)
    processed_cross_day_n = int(entry.get("processed_cross_day_n") or 0)
    leftover_suffix = " processed_but_on_disk=%d" % processed_but_on_disk
    if processed_cross_day_n:
      leftover_suffix += " processed_cross_day_n=%d" % processed_cross_day_n
    tar_key = os.path.normpath(str(entry.get("tar_path") or ""))
    slot = queue_slot.get(tar_key)
    queue_order_token = (
        "queue_order=%d" % slot if slot is not None else "queue_order="
    )
    mutable_tar = bool(entry.get("mutable_tar"))
    if "mutable_tar" not in entry and tar_key:
      mutable_tar = os.path.isfile(tar_key)
    log_fn(
        "janitor: day_close candidate tar=%s status=%s reasons=%s "
        "unprocessed=%d phase=%s mutable_tar=%s %s%s%s%s"
        % (
            entry.get("tar_path"),
            entry.get("status"),
            ",".join(reasons),
            unprocessed_on_disk,
            entry.get("phase") or "",
            "yes" if mutable_tar else "no",
            queue_order_token,
            ghost_suffix,
            leftover_suffix,
            async_suffix,
        ),
        flush=True,
    )


def effective_keep_uncompressed_tar(
  tar_path: str,
  *,
  local_tz: Any,
  now: Any | None = None,
) -> Any:
  """
  Whether to retain uncompressed ``.tar`` after seal for ``tar_path``.
  
  When global ``archive_keep_uncompressed_tar`` is yes, always keep. Otherwise
  prior calendar days drop at seal when raw is gone; calendar-today keeps until
  local midnight plus ``archive_today_uncompressed_tar_grace_hours``.
  
  Args:
    tar_path (str): String for tar path.
    local_tz (Any): Local tz passed to this helper.
    now (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> effective_keep_uncompressed_tar("x", None, None)  # doctest: +SKIP
  """
  if cfg.get_archive_keep_uncompressed_tar():
    return True
  day_date = calendar_date_from_daily_tar_path(tar_path)
  if day_date is None:
    return True
  if now is None:
    now = datetime.now(local_tz)
  today_local = now.date()
  if day_date < today_local:
    return False
  if day_date > today_local:
    return False
  grace_h = float(cfg.get_archive_today_uncompressed_tar_grace_hours())
  midnight = datetime.combine(today_local, dt_time.min, tzinfo=local_tz)
  grace_end = midnight + timedelta(hours=grace_h)
  return now < grace_end


def daily_tar_seal_calendar_eligible(
  tar_path: str,
  local_tz: Any,
  now: Any | None = None,
) -> Any:
  """
  Whether sealing may start for ``tar_path`` by calendar policy.
  
  Prior calendar days are eligible immediately (subject to dirty/disqualified
  checks elsewhere). Calendar-today is eligible only after local midnight plus
  ``archive_today_uncompressed_tar_grace_hours``. This governs **when** sealing
  may begin, distinct from ``effective_keep_uncompressed_tar`` (whether the
  uncompressed ``.tar`` is retained after seal).
  
  Args:
    tar_path (str): String for tar path.
    local_tz (Any): Local tz passed to this helper.
    now (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> daily_tar_seal_calendar_eligible("x", None, None)  # doctest: +SKIP
  """
  day_date = calendar_date_from_daily_tar_path(tar_path)
  if day_date is None:
    return True
  if now is None:
    now = datetime.now(local_tz)
  today_local = now.date()
  if day_date < today_local:
    return True
  if day_date > today_local:
    return False
  grace_h = float(cfg.get_archive_today_uncompressed_tar_grace_hours())
  midnight = datetime.combine(today_local, dt_time.min, tzinfo=local_tz)
  grace_end = midnight + timedelta(hours=grace_h)
  return now >= grace_end


ARCHIVE_SKIP_MEMBER_EXISTS = "member_exists"
ARCHIVE_SKIP_MISSING_PATH = "missing_path"
ARCHIVE_SKIP_ACTIVE_SEGMENT = "active_segment"
ARCHIVE_SKIP_UNRESOLVED_DAY = "unresolved_day"
ARCHIVE_SKIP_DAY_INGEST_SKIP_PREFIX = "day_ingest_skip:"


def _day_ingest_skip_archive_token(kind: Any) -> Any:
  """
  Internal helper to handle day ingest skip archive token.
  
  Args:
    kind (Any): Mode or kind token selecting a code path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _day_ingest_skip_archive_token(None)  # doctest: +SKIP
  """
  return "%s%s" % (ARCHIVE_SKIP_DAY_INGEST_SKIP_PREFIX, kind)


def raw_stats_path_tar_append_decision(
  stats_path: str,
  tgz_archive_dir: str,
  *,
  first_ts: Any | None = None,
) -> Any:
  """
  Return ``(needs_append, skip_reason)`` for closed raw tar-append lookup.
  
  ``skip_reason`` is empty when ``needs_append`` is True. When append is not
  needed, ``skip_reason`` is a stable token for per-file ingest logs.
  
  Args:
    stats_path (str): String for stats path.
    tgz_archive_dir (str): String for tgz archive dir.
    first_ts (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Raises:
    Exception: Raised when ``raw_stats_path_tar_append_decision`` hits a
    ``Exception`` failure path.
  
  Examples:
    >>> raw_stats_path_tar_append_decision("x", "x", None)  # doctest: +SKIP
  """
  if not stats_path or not os.path.isfile(stats_path):
    return False, ARCHIVE_SKIP_MISSING_PATH
  if stats_file_is_active_segment(stats_path):
    return False, ARCHIVE_SKIP_ACTIVE_SEGMENT
  file_date = _derive_stats_path_date(stats_path, first_ts)
  if file_date is None:
    return False, ARCHIVE_SKIP_UNRESOLVED_DAY
  compressed_path = daily_compressed_path_for_date(tgz_archive_dir, file_date)
  member_name = get_tar_member_name(stats_path)
  try:
    expected_size = os.path.getsize(stats_path)
  except OSError:
    return True, ""
  try:
    if daily_archive_has_member_with_size(
        compressed_path, member_name, expected_size,
    ):
      return False, ARCHIVE_SKIP_MEMBER_EXISTS
  except Exception as exc:
    from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
        ArchiveDayIngestSkipError,
    )
    if isinstance(exc, ArchiveDayIngestSkipError):
      _log_archive_day_ingest_skip_once(exc)
      return False, _day_ingest_skip_archive_token(exc.kind)
    raise
  return True, ""


def raw_stats_path_needs_tar_append(
  stats_path: str,
  tgz_archive_dir: str,
  *,
  first_ts: Any | None = None,
) -> Any:
  """
  True when closed raw exists on disk but is not a matching tar member.
  
  Uses the same member-name and byte-size rules as
  ``filter_files_to_add_to_archive``. Returns ``False`` when the path is
  missing, active (same inode as ``current``), or its calendar day cannot be
  resolved.
  
  Args:
    stats_path (str): String for stats path.
    tgz_archive_dir (str): String for tgz archive dir.
    first_ts (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> raw_stats_path_needs_tar_append("x", "x", None)  # doctest: +SKIP
  """
  needs_append, _skip_reason = raw_stats_path_tar_append_decision(
      stats_path,
      tgz_archive_dir,
      first_ts=first_ts,
  )
  return needs_append


def partition_day_close_handoff_paths_for_append_vs_ingest(
  paths: Any,
  tgz_archive_dir: str,
  *,
  ingest_ready_fn: Any | None = None,
) -> Any:
  """
  Split day-close handoff pins into archive-append vs ingest requeue.
  
  DbReadyNotInArchive (hpcperfstats03): ``skipped_not_in_archive`` pins that
  already pass the DB gate must drive **tar append**, not ingest-only handoff.
  Re-ingest alone often ends as ``checkpoint_immediate`` without ensuring the
  path lands in ``files_to_be_archived``, so Branch C reclassify never sees
  membership and open daily ``.tar`` never seal/tar_drop.
  
  Returns ``(append_paths, ingest_paths)`` preserving input order within each
  bucket. Missing on-disk paths are dropped. When ``ingest_ready_fn`` is set,
  only paths that return true are eligible for the append bucket; not-ready
  paths stay on ingest handoff.
  
  Args:
    paths (Any): Iterable of filesystem paths as strings.
    tgz_archive_dir (str): String for tgz archive dir.
    ingest_ready_fn (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> partition_day_close_handoff_paths_for_append_vs_ingest(None, "x", None)
  """
  append_paths = []
  ingest_paths = []
  for path in paths or ():
    path_norm = os.path.normpath(str(path or ""))
    if not path_norm or not os.path.isfile(path_norm):
      continue
    db_ready = True
    if ingest_ready_fn is not None:
      try:
        db_ready = bool(ingest_ready_fn(path_norm))
      except Exception:
        db_ready = False
    if db_ready and raw_stats_path_needs_tar_append(path_norm, tgz_archive_dir):
      append_paths.append(path_norm)
    else:
      ingest_paths.append(path_norm)
  return append_paths, ingest_paths


def daily_tar_paths_for_stats_paths(
  paths: Any,
  tgz_archive_dir: str,
  first_timestamp_by_path: Any | None = None,
) -> Any:
  """
  Map raw stats paths to the daily ``.tar`` path each would archive into.
  
  Args:
    paths (Any): Iterable of filesystem paths as strings.
    tgz_archive_dir (str): String for tgz archive dir.
    first_timestamp_by_path (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> daily_tar_paths_for_stats_paths(None, "x", None)  # doctest: +SKIP
  """
  if not paths:
    return frozenset()
  fmap = first_timestamp_by_path or {}
  tar_paths = set()
  for path in paths:
    file_date = _derive_stats_path_date(path, fmap.get(path))
    if file_date is None:
      continue
    tar_paths.add(_daily_tar_path_for_date(tgz_archive_dir, file_date))
  return frozenset(tar_paths)


def merge_maintenance_skip_daily_tar_paths(
  skip_daily_tar_paths: bool,
  *,
  closed_paths: Any,
  mapping: Any,
  tgz_archive_dir: str,
) -> Any:
  """
  Union caller skip paths with days that have unmapped closed raw on disk.
  
  Scheduled/startup maintenance must apply the same unmapped-closed-raw gate as
  ArchiveJanitor ``TAR_DROP`` ticks so a day cannot lose its ``.tar`` while
    closed raw remains.
  
  Args:
    skip_daily_tar_paths (bool): Whether to enable skip daily tar paths.
    closed_paths (Any): Iterable of filesystem paths as strings.
    mapping (Any): Mapping passed to this helper.
    tgz_archive_dir (str): String for tgz archive dir.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> merge_maintenance_skip_daily_tar_paths(True, None, None, "x")
  """
  skip_set = set(_normalize_daily_tar_path_set(skip_daily_tar_paths))
  skip_set |= collect_days_with_unmapped_closed_raw(
      closed_paths, mapping, tgz_archive_dir)
  return frozenset(skip_set) if skip_set else None


_UNMAPPED_DISQUALIFY_CACHE = {"at": 0.0, "key": None, "tars": frozenset()}
_UNMAPPED_DISQUALIFY_TTL_S = 60.0


def collect_unmapped_closed_raw_daily_tars(
  archive_data_dir: str,
  host_name_ext: Any,
  tgz_archive_dir: str,
  *,
  log_fn: Any = log_print,
  maintenance_snapshot: Any | None = None,
  closed_paths: Any | None = None,
  mapping: Any | None = None,
) -> Any:
  """
  Daily ``.tar`` paths with closed raw on disk not present in archive mapping.
  
  Bounded scan used when the janitor has no accrual snapshot (long ingest
    backlog)
  so unmapped closed segments still disqualify seal / ``.tar`` removal.
  
  When a warm ``maintenance_snapshot`` (or explicit ``closed_paths`` +
    ``mapping``)
  is provided, reuse those collections — never run a full
    ``collect_stats_files``
  discover for day-scoped/unmapped (snapshot-first guard).
  
  Args:
    archive_data_dir (str): String for archive data dir.
    host_name_ext (Any): Host name ext passed to this helper.
    tgz_archive_dir (str): String for tgz archive dir.
    log_fn (Any): Callable invoked by this helper.
    maintenance_snapshot (Any | None): One of ``Any``, ``None``.
    closed_paths (Any | None): One of ``Any``, ``None``.
    mapping (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> collect_unmapped_closed_raw_daily_tars(0)  # doctest: +SKIP
  """
  if maintenance_snapshot is not None:
    closed_paths = list(maintenance_snapshot.closed_paths or ())
    mapping = dict(maintenance_snapshot.mapping or {})
  if closed_paths is not None and mapping is not None:
    return collect_days_with_unmapped_closed_raw(
        closed_paths, mapping, tgz_archive_dir)
  closed_paths = collect_stats_files_in_range(
      archive_data_dir, "backlog", None, host_name_ext)
  if not closed_paths:
    return frozenset()
  mapping = build_archive_mapping(closed_paths, tgz_archive_dir)
  return collect_days_with_unmapped_closed_raw(
      closed_paths, mapping, tgz_archive_dir)


def get_unmapped_closed_raw_daily_tars_cached(
  archive_data_dir: str,
  host_name_ext: Any,
  tgz_archive_dir: str,
  *,
  log_fn: Any = log_print,
  ttl_s: Any = _UNMAPPED_DISQUALIFY_TTL_S,
) -> Any:
  """
  TTL cache wrapper for :func:`collect_unmapped_closed_raw_daily_tars`.
  
  Args:
    archive_data_dir (str): String for archive data dir.
    host_name_ext (Any): Host name ext passed to this helper.
    tgz_archive_dir (str): String for tgz archive dir.
    log_fn (Any): Callable invoked by this helper.
    ttl_s (Any): Ttl s passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> get_unmapped_closed_raw_daily_tars_cached("x", None, "x", None, None)
  """
  cache_key = (archive_data_dir, host_name_ext, tgz_archive_dir)
  now = time.time()
  if (
      _UNMAPPED_DISQUALIFY_CACHE.get("key") == cache_key
      and now - float(_UNMAPPED_DISQUALIFY_CACHE.get("at", 0.0)) < float(ttl_s)
  ):
    return _UNMAPPED_DISQUALIFY_CACHE["tars"]
  tars = collect_unmapped_closed_raw_daily_tars(
      archive_data_dir,
      host_name_ext,
      tgz_archive_dir,
      log_fn=log_fn,
  )
  _UNMAPPED_DISQUALIFY_CACHE["key"] = cache_key
  _UNMAPPED_DISQUALIFY_CACHE["at"] = now
  _UNMAPPED_DISQUALIFY_CACHE["tars"] = tars
  return tars


def collect_days_with_unmapped_closed_raw(
  closed_paths: Any,
  mapping: Any,
  tgz_archive_dir: str,
) -> Any:
  """
  Daily ``.tar`` paths for closed **parsable** raw stats not present in.
  
    ``mapping``.
  
  Unparsable closed segments are moved to DLO at ingest parse failure (see
  ``quarantine_ingest_failed_raw_path``); they do not disqualify days here.
  Parsable unmapped paths still block seal / ``.tar`` removal (data-gap safety).
  
  Args:
    closed_paths (Any): Iterable of filesystem paths as strings.
    mapping (Any): Mapping passed to this helper.
    tgz_archive_dir (str): String for tgz archive dir.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> collect_days_with_unmapped_closed_raw(None, None, "x")  # doctest: +SKIP
  """
  if not closed_paths:
    return frozenset()
  mapped = set()
  for stats_paths in (mapping or {}).values():
    mapped.update(stats_paths)
  tar_paths = set()
  for path in closed_paths:
    if path in mapped:
      continue
    if is_unparsable_closed_stats_path(path):
      continue
    file_date = _derive_stats_path_date(path, None)
    if file_date is None:
      continue
    tar_paths.add(_daily_tar_path_for_date(tgz_archive_dir, file_date))
  return frozenset(tar_paths)


SYNC_TIMEDB_UNPARSABLE_RAW_DIRNAME = ".sync_timedb_unparsable_raw"
SYNC_TIMEDB_UNPARSABLE_RAW_MANIFEST_BASENAME = ".sync_timedb_unparsable_raw.json"
UNPARSABLE_RAW_QUARANTINE_REASON = "unparsable_first_timestamp"
INGEST_PARSE_FAILED_QUARANTINE_REASON = "ingest_parse_failed"


def quarantine_dir_for_archive(archive_data_dir: str) -> Any:
  """
  Root directory for quarantined unparsable closed raw stats.
  
  Args:
    archive_data_dir (str): String for archive data dir.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> quarantine_dir_for_archive("x")  # doctest: +SKIP
  """
  return os.path.join(
      os.path.normpath(archive_data_dir or ""),
      SYNC_TIMEDB_UNPARSABLE_RAW_DIRNAME,
  )


def manifest_path_for_archive(archive_data_dir: str) -> Any:
  """
  JSON manifest path for unparsable raw quarantine moves.
  
  Args:
    archive_data_dir (str): String for archive data dir.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> manifest_path_for_archive("x")  # doctest: +SKIP
  """
  return os.path.join(
      os.path.normpath(archive_data_dir or ""),
      SYNC_TIMEDB_UNPARSABLE_RAW_MANIFEST_BASENAME,
  )


def invalidate_unmapped_disqualify_cache() -> None:
  """
  Drop TTL cache for ``get_unmapped_closed_raw_daily_tars_cached``.
  
  Returns:
    None
  
  Examples:
    >>> invalidate_unmapped_disqualify_cache()  # doctest: +SKIP
  """
  _UNMAPPED_DISQUALIFY_CACHE["at"] = 0.0
  _UNMAPPED_DISQUALIFY_CACHE["key"] = None
  _UNMAPPED_DISQUALIFY_CACHE["tars"] = frozenset()


def resolve_unmapped_closed_raw_daily_tars(
  *,
  coordinator_snapshot: Any | None = None,
  accrual_snapshot: Any | None = None,
  archive_data_dir: str,
  host_name_ext: Any,
  tgz_archive_dir: str,
  log_fn: Any = log_print,
) -> Any:
  """
  Prefer coordinator ``closed_paths``; accrual only when non-empty; else TTL.
  
    collect.
  
  Args:
    coordinator_snapshot (Any | None): One of ``Any``, ``None``.
    accrual_snapshot (Any | None): One of ``Any``, ``None``.
    archive_data_dir (str): String for archive data dir.
    host_name_ext (Any): Host name ext passed to this helper.
    tgz_archive_dir (str): String for tgz archive dir.
    log_fn (Any): Callable invoked by this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> resolve_unmapped_closed_raw_daily_tars(None, None, "x", None, "x", None)
  """
  if coordinator_snapshot is not None and coordinator_snapshot.closed_paths:
    return set(collect_days_with_unmapped_closed_raw(
        coordinator_snapshot.closed_paths,
        coordinator_snapshot.mapping,
        tgz_archive_dir,
    ) or ())
  if accrual_snapshot is not None and accrual_snapshot.closed_paths:
    return set(collect_days_with_unmapped_closed_raw(
        accrual_snapshot.closed_paths,
        accrual_snapshot.mapping,
        tgz_archive_dir,
    ) or ())
  return set(get_unmapped_closed_raw_daily_tars_cached(
      archive_data_dir,
      host_name_ext,
      tgz_archive_dir,
      log_fn=log_fn,
  ) or ())


def _load_unparsable_raw_manifest(path: str) -> Any:
  """
  Internal helper to load the unparsable raw manifest.
  
  Args:
    path (str): String for path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _load_unparsable_raw_manifest("x")  # doctest: +SKIP
  """
  from hpcperfstats.dbload.lib.sync_timedb_persistence import load_persistence_document

  raw = load_persistence_document(path, "unparsable_raw", default=[])
  if not isinstance(raw, list):
    return []
  return [item for item in raw if isinstance(item, dict)]


def _save_unparsable_raw_manifest_atomic(path: str, entries: Any) -> None:
  """
  Internal helper to save the unparsable raw manifest atomic.
  
  Args:
    path (str): String for path.
    entries (Any): Entries passed to this helper.
  
  Returns:
    None
  
  Examples:
    >>> _save_unparsable_raw_manifest_atomic("x", None)  # doctest: +SKIP
  """
  from hpcperfstats.dbload.lib.sync_timedb_persistence import save_persistence_document

  save_persistence_document(path, "unparsable_raw", list(entries or []))


def _quarantined_original_paths_from_manifest(manifest_entries: Any) -> Any:
  """
  Internal helper to handle quarantined original paths from manifest.
  
  Args:
    manifest_entries (Any): Manifest entries passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _quarantined_original_paths_from_manifest(None)  # doctest: +SKIP
  """
  originals = set()
  for item in manifest_entries or []:
    original = str(item.get("original_path", "")).strip()
    if original:
      originals.add(os.path.normpath(original))
  return originals


def _quarantine_dest_path(
  archive_data_dir: str,
  quarantine_root: Any,
  original_path: str,
) -> Any:
  """
  Internal helper to handle quarantine dest path.
  
  Args:
    archive_data_dir (str): String for archive data dir.
    quarantine_root (Any): Quarantine root passed to this helper.
    original_path (str): String for original path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _quarantine_dest_path("x", None, "x")  # doctest: +SKIP
  """
  archive_norm = os.path.normpath(archive_data_dir)
  original_norm = os.path.normpath(original_path)
  prefix = archive_norm + os.sep
  if original_norm.startswith(prefix):
    rel = original_norm[len(prefix):]
  else:
    rel = os.path.basename(original_norm)
  return os.path.join(quarantine_root, rel)


def is_unparsable_closed_stats_path(path: str) -> Any:
  """
  True when ``path`` is a closed raw stats file with no parseable first.
  
    timestamp.
  
  Args:
    path (str): String for path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> is_unparsable_closed_stats_path("x")  # doctest: +SKIP
  """
  if not path or not os.path.isfile(path):
    return False
  base = os.path.basename(path)
  if base.startswith("current"):
    return False
  if stats_file_is_active_segment(path):
    return False
  _host, timestamp_utc = read_stats_file_head_identity(path)
  return timestamp_utc is None


def _closed_raw_eligible_for_quarantine(path_norm: Any) -> Any:
  """
  True when ``path_norm`` is a closed raw stats file that may be quarantined.
  
  Args:
    path_norm (Any): Path norm passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _closed_raw_eligible_for_quarantine(None)  # doctest: +SKIP
  """
  if not path_norm:
    return False
  base = os.path.basename(path_norm)
  if base.startswith("current"):
    return False
  if stats_file_is_active_segment(path_norm):
    return False
  return True


def _quarantine_one_closed_raw_path(
  path_norm: Any,
  archive_data_dir: str,
  reason: Any,
  *,
  manifest_path: str,
  manifest_entries: Any,
  already_quarantined: Any,
  quarantine_root: Any,
  log_fn: Any = log_print,
  error_detail: Any | None = None,
) -> Any:
  """
  Move one closed raw path into DLO when present on disk. Return True if.
  
    handled.
  
  Args:
    path_norm (Any): Path norm passed to this helper.
    archive_data_dir (str): String for archive data dir.
    reason (Any): Reason passed to this helper.
    manifest_path (str): String for manifest path.
    manifest_entries (Any): Manifest entries passed to this helper.
    already_quarantined (Any): Already quarantined passed to this helper.
    quarantine_root (Any): Quarantine root passed to this helper.
    log_fn (Any): Callable invoked by this helper.
    error_detail (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _quarantine_one_closed_raw_path(0)  # doctest: +SKIP
  """
  if path_norm in already_quarantined:
    return True
  if not _closed_raw_eligible_for_quarantine(path_norm):
    return False
  dest_path = _quarantine_dest_path(archive_data_dir, quarantine_root, path_norm)
  try:
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with file_write_lock(path_norm):
      if not os.path.isfile(path_norm):
        return path_norm in already_quarantined
      if os.path.exists(dest_path):
        if log_fn:
          log_fn(
              "Unparsable raw quarantine skipped existing dest: %s"
              % dest_path,
              flush=True,
          )
        return False
      entry = {
          "original_path": path_norm,
          "quarantined_path": os.path.normpath(dest_path),
          "quarantined_at": time.time(),
          "reason": reason,
      }
      if error_detail:
        entry["error_detail"] = str(error_detail)
      manifest_entries.append(entry)
      try:
        _save_unparsable_raw_manifest_atomic(manifest_path, manifest_entries)
      except OSError as exc:
        manifest_entries.pop()
        if log_fn:
          log_fn(
              "Unparsable raw quarantine manifest save failed path=%s: %s"
              % (path_norm, exc),
              flush=True,
          )
        return False
      try:
        shutil.move(path_norm, dest_path)
      except OSError as exc:
        manifest_entries.pop()
        try:
          _save_unparsable_raw_manifest_atomic(manifest_path, manifest_entries)
        except OSError:
          pass
        if log_fn:
          log_fn(
              "Unparsable raw quarantine failed path=%s: %s"
              % (path_norm, exc),
              flush=True,
          )
        return False
      already_quarantined.add(path_norm)
      if log_fn:
        log_fn(
            "Quarantined unparsable raw stats %s -> %s"
            % (path_norm, dest_path),
            flush=True,
        )
      return True
  except OSError as exc:
    if log_fn:
      log_fn(
          "Unparsable raw quarantine failed path=%s: %s"
          % (path_norm, exc),
          flush=True,
      )
    return False


def quarantine_ingest_failed_raw_path(
  path: str,
  archive_data_dir: str,
  reason: Any,
  *,
  log_fn: Any = log_print,
  error_detail: Any | None = None,
) -> Any:
  """
  Move one ingest-failed closed raw path into DLO; return True when handled.
  
  Args:
    path (str): String for path.
    archive_data_dir (str): String for archive data dir.
    reason (Any): Reason passed to this helper.
    log_fn (Any): Callable invoked by this helper.
    error_detail (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> quarantine_ingest_failed_raw_path("x", "x", None, None, None)
  """
  if not path or not archive_data_dir:
    return False
  path_norm = os.path.normpath(path)
  manifest_path = manifest_path_for_archive(archive_data_dir)
  manifest_entries = _load_unparsable_raw_manifest(manifest_path)
  already_quarantined = _quarantined_original_paths_from_manifest(manifest_entries)
  quarantine_root = quarantine_dir_for_archive(archive_data_dir)
  manifest_len_before = len(manifest_entries)
  handled = _quarantine_one_closed_raw_path(
      path_norm,
      archive_data_dir,
      reason,
      manifest_path=manifest_path,
      manifest_entries=manifest_entries,
      already_quarantined=already_quarantined,
      quarantine_root=quarantine_root,
      log_fn=log_fn,
      error_detail=error_detail,
  )
  if handled and len(manifest_entries) > manifest_len_before:
    invalidate_unmapped_disqualify_cache()
  return handled


def quarantine_unparsable_closed_raw_paths(
  paths: Any,
  archive_data_dir: str,
  *,
  skip_paths: Any | None = None,
  log_fn: Any = log_print,
  max_moves: Any | None = None,
) -> Any:
  """
  Move unparsable closed raw paths into the dead-letter office; return move.
  
    count.
  
  Args:
    paths (Any): Iterable of filesystem paths as strings.
    archive_data_dir (str): String for archive data dir.
    skip_paths (Any | None): One of ``Any``, ``None``.
    log_fn (Any): Callable invoked by this helper.
    max_moves (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> quarantine_unparsable_closed_raw_paths(None, "x", None, None, None)
  """
  if not paths or not archive_data_dir:
    return 0
  if max_moves is None:
    max_moves = len(tuple(paths))
  max_moves = max(0, int(max_moves))
  if max_moves <= 0:
    return 0

  skip_norm = {
      os.path.normpath(p)
      for p in (skip_paths or ())
      if p
  }
  manifest_path = manifest_path_for_archive(archive_data_dir)
  manifest_entries = _load_unparsable_raw_manifest(manifest_path)
  already_quarantined = _quarantined_original_paths_from_manifest(manifest_entries)
  quarantine_root = quarantine_dir_for_archive(archive_data_dir)
  moved = 0

  for path in paths:
    if moved >= max_moves:
      break
    path_norm = os.path.normpath(path)
    if path_norm in skip_norm:
      continue
    if path_norm in already_quarantined:
      continue
    if not is_unparsable_closed_stats_path(path_norm):
      continue
    if _quarantine_one_closed_raw_path(
        path_norm,
        archive_data_dir,
        UNPARSABLE_RAW_QUARANTINE_REASON,
        manifest_path=manifest_path,
        manifest_entries=manifest_entries,
        already_quarantined=already_quarantined,
        quarantine_root=quarantine_root,
        log_fn=log_fn,
    ):
      moved += 1

  if moved:
    _save_unparsable_raw_manifest_atomic(manifest_path, manifest_entries)
    invalidate_unmapped_disqualify_cache()
  return moved


def raw_stats_path_fingerprint(path: str) -> Any:
  """
  Return ``{mtime, size}`` ns fingerprint for delete-time identity checks.
  
  Args:
    path (str): String for path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> raw_stats_path_fingerprint("x")  # doctest: +SKIP
  """
  try:
    st = os.stat(path)
    return {"mtime": int(st.st_mtime_ns), "size": int(st.st_size)}
  except OSError:
    return None


def delete_raw_stats_path_if_fingerprint_unchanged(
  path: str,
  expected_fp: Any,
  *,
  log_fn: Any = log_print,
) -> Any:
  """
  Delete ``path`` when on-disk fingerprint still matches ``expected_fp``.
  
  Args:
    path (str): String for path.
    expected_fp (Any): Expected fp passed to this helper.
    log_fn (Any): Callable invoked by this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> delete_raw_stats_path_if_fingerprint_unchanged("x", None, None)
  """
  current_fp = raw_stats_path_fingerprint(path)
  if expected_fp is not None and current_fp != expected_fp:
    if log_fn:
      log_fn(
          "Skipping raw delete fingerprint changed path=%s" % path,
          flush=True,
      )
    return False
  try:
    with file_write_lock(path):
      if not os.path.isfile(path):
        return True
      os.remove(path)
    return True
  except OSError as exc:
    if log_fn:
      log_fn("Could not remove %s: %s" % (path, exc), flush=True)
    return False


def _checkpoint_entry_fingerprint(path: str) -> Any:
  """
  Return ``{size, mtime}`` for restart-safe checkpoint matching.
  
  Args:
    path (str): String for path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _checkpoint_entry_fingerprint("x")  # doctest: +SKIP
  """
  try:
    return {"size": int(os.path.getsize(path)), "mtime": int(os.path.getmtime(path))}
  except OSError:
    return None


def _load_checkpoint_entries(checkpoint_path: str) -> Any:
  """
  Load checkpoint entries (path/size/mtime); return [] on invalid.
  
  Args:
    checkpoint_path (str): String for checkpoint path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _load_checkpoint_entries("x")  # doctest: +SKIP
  """
  from hpcperfstats.dbload.lib.sync_timedb_persistence import load_persistence_document

  raw = load_persistence_document(checkpoint_path, "ingest_checkpoint", default=[])
  if not isinstance(raw, list):
    return []
  entries = []
  for item in raw:
    if not isinstance(item, dict):
      continue
    path = item.get("path")
    size = item.get("size")
    mtime = item.get("mtime")
    if not isinstance(path, str):
      continue
    try:
      size = int(size)
      mtime = int(mtime)
    except (TypeError, ValueError):
      continue
    entries.append({"path": path, "size": size, "mtime": mtime})
  return entries


def load_checkpoint_path_set(checkpoint_path: str) -> Any:
  """
  Return paths present in checkpoint whose on-disk size/mtime still match.
  
  Args:
    checkpoint_path (str): String for checkpoint path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> load_checkpoint_path_set("x")  # doctest: +SKIP
  """
  matched = set()
  for entry in _load_checkpoint_entries(checkpoint_path):
    fp = _checkpoint_entry_fingerprint(entry["path"])
    if fp is None:
      continue
    if fp["size"] == entry["size"] and fp["mtime"] == entry["mtime"]:
      matched.add(entry["path"])
  return matched


def checkpoint_entries_snapshot(checkpoint_entries: Any) -> Any:
  """
  Stable tuple copy of in-memory checkpoint entries for cross-thread reads.
  
  Day-close / ingest threads append and ``popleft`` the live ``deque`` while
  main-thread finalize/reconcile may iterate it. Iterating the live deque
  raises ``RuntimeError: deque mutated during iteration`` and kills the
  supervisor (hpcperfstats01 2026-07-26 exit status 1). Snapshot with a short
  retry so a concurrent mutation during ``tuple()`` cannot crash the process.
  
  Args:
    checkpoint_entries (Any): Checkpoint entries passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> checkpoint_entries_snapshot(None)  # doctest: +SKIP
  """
  if checkpoint_entries is None:
    return ()
  for _ in range(16):
    try:
      return tuple(checkpoint_entries)
    except RuntimeError:
      continue
  return ()


def resolved_checkpoint_path_set(
  checkpoint_path: str,
  checkpoint_entries: Any | None = None,
) -> Any:
  """
  Return checkpoint-complete paths from disk plus in-memory buffer entries.
  
  Args:
    checkpoint_path (str): String for checkpoint path.
    checkpoint_entries (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> resolved_checkpoint_path_set("x", None)  # doctest: +SKIP
  """
  paths = load_checkpoint_path_set(checkpoint_path)
  for entry in checkpoint_entries_snapshot(checkpoint_entries):
    if not isinstance(entry, dict):
      continue
    path = entry.get("path")
    if not isinstance(path, str):
      continue
    fp = _checkpoint_entry_fingerprint(path)
    if fp is None:
      continue
    try:
      size = int(entry["size"])
      mtime = int(entry["mtime"])
    except (KeyError, TypeError, ValueError):
      continue
    if fp["size"] == size and fp["mtime"] == mtime:
      paths.add(path)
  return paths


def build_unprocessed_raw_by_daily_tar(
  archive_data_dir: str,
  host_name_ext: Any,
  tgz_archive_dir: str,
  *,
  checkpoint_path: Any | None = None,
  checkpoint_paths: Any | None = None,
  first_timestamp_by_path: Any | None = None,
  maintenance_snapshot: Any | None = None,
) -> Any:
  """
  Map daily ``.tar`` paths to mapped closed raw not in the ingest checkpoint.
  
  Args:
    archive_data_dir (str): String for archive data dir.
    host_name_ext (Any): Host name ext passed to this helper.
    tgz_archive_dir (str): String for tgz archive dir.
    checkpoint_path (Any | None): One of ``Any``, ``None``.
    checkpoint_paths (Any | None): One of ``Any``, ``None``.
    first_timestamp_by_path (Any | None): One of ``Any``, ``None``.
    maintenance_snapshot (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> build_unprocessed_raw_by_daily_tar(0)  # doctest: +SKIP
  """
  if checkpoint_paths is None:
    checkpoint_paths = (
        load_checkpoint_path_set(checkpoint_path) if checkpoint_path else set()
    )
  else:
    checkpoint_paths = set(checkpoint_paths or ())
  if maintenance_snapshot is not None:
    mapping = maintenance_snapshot.mapping
    if first_timestamp_by_path is None:
      first_timestamp_by_path = maintenance_snapshot.first_timestamp_by_path
  else:
    closed_paths = collect_stats_files_in_range(
        archive_data_dir,
        "backlog",
        None,
        host_name_ext,
        force_full_scan=True,
    )
    mapping = build_archive_mapping(
        closed_paths,
        tgz_archive_dir,
        first_timestamp_by_path=first_timestamp_by_path,
    )
  unprocessed_by_tar = {}
  for gz_key, stats_paths in mapping.items():
    tar_norm = os.path.normpath(daily_tar_path_from_compressed(gz_key))
    unprocessed = [p for p in stats_paths if p not in checkpoint_paths]
    if unprocessed:
      unprocessed_by_tar[tar_norm] = list(unprocessed)
  return unprocessed_by_tar


def augment_unprocessed_by_tar_with_pending_paths(
  unprocessed_by_tar: Any,
  *,
  pending_stats_paths: Any,
  tgz_archive_dir: str,
  checkpoint_path: Any | None = None,
  checkpoint_paths: Any | None = None,
  first_timestamp_by_path: Any | None = None,
) -> Any:
  """
  Union pending ingest paths not in checkpoint into ``unprocessed_by_tar``.
  
  Args:
    unprocessed_by_tar (Any): Unprocessed by tar passed to this helper.
    pending_stats_paths (Any): Iterable of filesystem paths as strings.
    tgz_archive_dir (str): String for tgz archive dir.
    checkpoint_path (Any | None): One of ``Any``, ``None``.
    checkpoint_paths (Any | None): One of ``Any``, ``None``.
    first_timestamp_by_path (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> augment_unprocessed_by_tar_with_pending_paths(0)  # doctest: +SKIP
  """
  if checkpoint_paths is None:
    checkpoint_paths = (
        load_checkpoint_path_set(checkpoint_path) if checkpoint_path else set()
    )
  else:
    checkpoint_paths = set(checkpoint_paths or ())
  result = {
      os.path.normpath(tar_path): list(paths)
      for tar_path, paths in (unprocessed_by_tar or {}).items()
  }
  for stats_path in pending_stats_paths or ():
    if stats_path in checkpoint_paths:
      continue
    tar_path = daily_tar_path_for_stats_path(
        stats_path,
        tgz_archive_dir,
        first_ts=(first_timestamp_by_path or {}).get(stats_path),
    )
    if not tar_path:
      continue
    tar_norm = os.path.normpath(tar_path)
    bucket = result.setdefault(tar_norm, [])
    if stats_path not in bucket:
      bucket.append(stats_path)
  return result


def daily_tar_path_for_calendar_day(
  tgz_archive_dir: str,
  calendar_day_iso: Any,
) -> Any:
  """
  Return normalized daily ``.tar`` path for ``YYYY-MM-DD``.
  
  Args:
    tgz_archive_dir (str): String for tgz archive dir.
    calendar_day_iso (Any): Calendar day iso passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> daily_tar_path_for_calendar_day("x", None)  # doctest: +SKIP
  """
  day = str(calendar_day_iso or "").strip()
  if not day or not tgz_archive_dir:
    return ""
  return os.path.normpath(os.path.join(tgz_archive_dir, day + ".tar"))


def calendar_days_checkpoint_ingest_complete(
  candidate_calendar_days: Any,
  *,
  archive_data_dir: str,
  host_name_ext: Any,
  tgz_archive_dir: str,
  checkpoint_path: Any | None = None,
  pending_stats_paths: Any | None = None,
  maintenance_snapshot: Any | None = None,
) -> Any:
  """
  Return sorted ISO days with no checkpoint-unprocessed closed raw on disk.
  
  Args:
    candidate_calendar_days (Any): Candidate calendar days passed to this
    helper.
    archive_data_dir (str): String for archive data dir.
    host_name_ext (Any): Host name ext passed to this helper.
    tgz_archive_dir (str): String for tgz archive dir.
    checkpoint_path (Any | None): One of ``Any``, ``None``.
    pending_stats_paths (Any | None): One of ``Any``, ``None``.
    maintenance_snapshot (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> calendar_days_checkpoint_ingest_complete(0)  # doctest: +SKIP
  """
  if not candidate_calendar_days:
    return []
  unprocessed_by_tar = build_unprocessed_raw_by_daily_tar(
      archive_data_dir,
      host_name_ext,
      tgz_archive_dir,
      checkpoint_path=checkpoint_path,
      maintenance_snapshot=maintenance_snapshot,
  )
  unprocessed_by_tar = augment_unprocessed_by_tar_with_pending_paths(
      unprocessed_by_tar,
      pending_stats_paths=pending_stats_paths,
      tgz_archive_dir=tgz_archive_dir,
      checkpoint_path=checkpoint_path,
  )
  complete = []
  for day_iso in sorted(str(day) for day in candidate_calendar_days):
    tar_norm = daily_tar_path_for_calendar_day(tgz_archive_dir, day_iso)
    if not tar_norm:
      continue
    if not aligned_unprocessed_tar_paths_still_on_disk(
        unprocessed_by_tar,
        tar_norm,
        tgz_archive_dir=tgz_archive_dir,
    ):
      complete.append(day_iso)
  return complete


def day_close_queued_reason_for_report_reason(reason: Any) -> Any:
  """
  Map janitor/startup report ``reason`` to classify queued-reason code.
  
  Args:
    reason (Any): Reason passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> day_close_queued_reason_for_report_reason(None)  # doctest: +SKIP
  """
  reason_text = str(reason or "")
  if reason_text.startswith("day_ingest_complete"):
    return "day_ingest_complete_checkpoint"
  if reason_text in ("startup", "startup_checkpoint_discover"):
    return "startup_checkpoint_complete"
  if reason_text == "startup_quiescent_tar":
    return "startup_quiescent_tar"
  return "scheduled_enqueue"


def day_close_filesystem_complete(
  tar_path: str,
  *,
  remaining_raw_by_gz: Any | None = None,
  use_blocking_remaining: bool = True,
  tgz_archive_dir: Any | None = None,
) -> Any:
  """
  True when sealed-only day has no mutable ``.tar`` and no blocking remaining.
  
    raw.
  
  When ``remaining_raw_by_gz`` is omitted and ``use_blocking_remaining`` is
    True,
  builds the quarantine-aware blocking map for ``tar_path``. Explicit remaining
  maps are filtered to filename/mtime-aligned paths only so first_ts misbuckets
  cannot keep ``fs_complete`` false for the wrong calendar day.
  
  Args:
    tar_path (str): String for tar path.
    remaining_raw_by_gz (Any | None): One of ``Any``, ``None``.
    use_blocking_remaining (bool): Whether to enable use blocking remaining.
    tgz_archive_dir (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> day_close_filesystem_complete("x", None, True, None)  # doctest: +SKIP
  """
  tar_norm = os.path.normpath(str(tar_path or ""))
  if not tar_norm:
    return False
  if os.path.isfile(tar_norm):
    return False
  zst_path, gz_path = compressed_sibling_paths(tar_norm)
  if not (os.path.isfile(zst_path) or os.path.isfile(gz_path)):
    return False
  remaining = remaining_raw_by_gz
  if remaining is None and use_blocking_remaining:
    from hpcperfstats.dbload.lib.sync_timedb_day_raw_removal import (
        remaining_raw_blocking_day_incomplete,
    )
    remaining = remaining_raw_blocking_day_incomplete(tar_norm)
  elif remaining:
    archive_dir = tgz_archive_dir or os.path.dirname(tar_norm)
    remaining = filter_remaining_raw_aligned_to_tar(
        remaining,
        tar_norm,
        tgz_archive_dir=archive_dir,
    )
  if remaining_raw_by_gz_has_paths_on_disk(remaining, zst_path):
    return False
  return True


def daily_tar_needs_day_close_work(
  tar_path: str,
  *,
  day_phases: Any | None = None,
  remaining_raw_by_gz: Any | None = None,
) -> Any:
  """
  True when cold-path seal/raw/tar work may still be required for ``tar_path``.
  
  ``remaining_raw_by_gz`` is accepted for backward compatibility but **ignored**
  for decision branches; blocking map is always used after FS-complete check.
  
  Args:
    tar_path (str): String for tar path.
    day_phases (Any | None): One of ``Any``, ``None``.
    remaining_raw_by_gz (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> daily_tar_needs_day_close_work("x", None, None)  # doctest: +SKIP
  """
  tar_norm = os.path.normpath(str(tar_path or ""))
  if not tar_norm:
    return False
  # Always use quarantine-aware blocking map for FS-complete (never raw census).
  if day_close_filesystem_complete(tar_norm):
    return False
  if not _day_phase_at_least_hints(day_phases, tar_norm, "tar_dropped"):
    return True
  if os.path.isfile(tar_norm):
    return True
  zst_path, _gz_path = compressed_sibling_paths(tar_norm)
  from hpcperfstats.dbload.lib.sync_timedb_day_raw_removal import (
      remaining_raw_blocking_day_incomplete,
  )
  blocking = remaining_raw_blocking_day_incomplete(tar_norm)
  if remaining_raw_by_gz_has_paths_on_disk(blocking, zst_path):
    return True
  return False


def days_ingest_complete_by_checkpoint(
  unprocessed_by_tar: Any,
  *,
  tgz_archive_dir: str,
  day_phases: Any | None = None,
  remaining_raw_by_gz: Any | None = None,
  local_tz: Any | None = None,
  now: Any | None = None,
  disqualified_daily_tars: Any | None = None,
) -> Any:
  """
  Oldest-first daily ``.tar`` paths with zero unprocessed mapped raw and work.
  
    left.
  
  Args:
    unprocessed_by_tar (Any): Unprocessed by tar passed to this helper.
    tgz_archive_dir (str): String for tgz archive dir.
    day_phases (Any | None): One of ``Any``, ``None``.
    remaining_raw_by_gz (Any | None): One of ``Any``, ``None``.
    local_tz (Any | None): One of ``Any``, ``None``.
    now (Any | None): One of ``Any``, ``None``.
    disqualified_daily_tars (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> days_ingest_complete_by_checkpoint(0)  # doctest: +SKIP
  """
  if not tgz_archive_dir:
    return []
  ranked = []
  seen = set()
  universe = set(unprocessed_by_tar or ())
  for tar_path in iter_daily_tar_paths(tgz_archive_dir):
    universe.add(os.path.normpath(tar_path))
  for tar_norm in sorted(universe, key=lambda p: _calendar_date_from_tar_sort_key(p)):
    if tar_norm in seen:
      continue
    seen.add(tar_norm)
    eligible, _reason = daily_tar_eligible_for_day_close_submit(
        tar_norm,
        unprocessed_by_tar=unprocessed_by_tar,
        disqualified_daily_tars=disqualified_daily_tars or (),
        day_phases=day_phases,
        remaining_raw_by_gz=remaining_raw_by_gz,
        local_tz=local_tz,
        now=now,
        tgz_archive_dir=tgz_archive_dir,
    )
    if not eligible:
      continue
    day_date = calendar_date_from_daily_tar_path(tar_norm)
    if day_date is None:
      continue
    ranked.append((day_date, tar_norm))
  ranked.sort(key=lambda item: item[0])
  return [tar_norm for _, tar_norm in ranked]


def unprocessed_tar_paths_still_on_disk(
  unprocessed_by_tar: Any,
  tar_norm: Any,
) -> Any:
  """
  True when any checkpoint-unprocessed path for ``tar_norm`` still exists.
  
  Args:
    unprocessed_by_tar (Any): Unprocessed by tar passed to this helper.
    tar_norm (Any): Tar norm passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> unprocessed_tar_paths_still_on_disk(None, None)  # doctest: +SKIP
  """
  return count_unprocessed_paths_on_disk(unprocessed_by_tar, tar_norm) > 0


def count_unprocessed_paths_on_disk(
  unprocessed_by_tar: Any,
  tar_norm: Any,
) -> Any:
  """
  Count checkpoint-unprocessed paths for ``tar_norm`` that still exist on disk.
  
  Args:
    unprocessed_by_tar (Any): Unprocessed by tar passed to this helper.
    tar_norm (Any): Tar norm passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> count_unprocessed_paths_on_disk(None, None)  # doctest: +SKIP
  """
  tar_key = os.path.normpath(str(tar_norm or ""))
  count = 0
  for path in (unprocessed_by_tar or {}).get(tar_key, ()) or ():
    if os.path.isfile(path):
      count += 1
  return count


def on_disk_unprocessed_paths_for_tar(
  unprocessed_by_tar: Any,
  tar_norm: Any,
) -> Any:
  """
  Return on-disk checkpoint-unprocessed paths for ``tar_norm``.
  
  Args:
    unprocessed_by_tar (Any): Unprocessed by tar passed to this helper.
    tar_norm (Any): Tar norm passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> on_disk_unprocessed_paths_for_tar(None, None)  # doctest: +SKIP
  """
  tar_key = os.path.normpath(str(tar_norm or ""))
  return [
      path
      for path in ((unprocessed_by_tar or {}).get(tar_key, ()) or ())
      if os.path.isfile(path)
  ]


def aligned_on_disk_unprocessed_paths_for_tar(
  unprocessed_by_tar: Any,
  tar_norm: Any,
  *,
  tgz_archive_dir: str,
) -> Any:
  """
  On-disk unprocessed paths whose filename calendar day maps to ``tar_norm``.
  
  Args:
    unprocessed_by_tar (Any): Unprocessed by tar passed to this helper.
    tar_norm (Any): Tar norm passed to this helper.
    tgz_archive_dir (str): String for tgz archive dir.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> aligned_on_disk_unprocessed_paths_for_tar(None, None, "x")
  """
  tar_key = os.path.normpath(str(tar_norm or ""))
  if not tar_key or not tgz_archive_dir:
    return []
  return checkpoint_incomplete_paths_aligned_with_oldest_tar(
      on_disk_unprocessed_paths_for_tar(unprocessed_by_tar, tar_key),
      tar_key,
      tgz_archive_dir=tgz_archive_dir,
  )


def count_aligned_unprocessed_paths_on_disk(
  unprocessed_by_tar: Any,
  tar_norm: Any,
  *,
  tgz_archive_dir: str,
) -> Any:
  """
  Count tar-aligned on-disk checkpoint-unprocessed paths for ``tar_norm``.
  
  Args:
    unprocessed_by_tar (Any): Unprocessed by tar passed to this helper.
    tar_norm (Any): Tar norm passed to this helper.
    tgz_archive_dir (str): String for tgz archive dir.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> count_aligned_unprocessed_paths_on_disk(None, None, "x")
  """
  return len(
      aligned_on_disk_unprocessed_paths_for_tar(
          unprocessed_by_tar,
          tar_norm,
          tgz_archive_dir=tgz_archive_dir,
      )
  )


def aligned_unprocessed_tar_paths_still_on_disk(
  unprocessed_by_tar: Any,
  tar_norm: Any,
  *,
  tgz_archive_dir: str,
) -> Any:
  """
  True when any tar-aligned checkpoint-unprocessed path still exists.
  
  Args:
    unprocessed_by_tar (Any): Unprocessed by tar passed to this helper.
    tar_norm (Any): Tar norm passed to this helper.
    tgz_archive_dir (str): String for tgz archive dir.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> aligned_unprocessed_tar_paths_still_on_disk(None, None, "x")
  """
  return count_aligned_unprocessed_paths_on_disk(
      unprocessed_by_tar,
      tar_norm,
      tgz_archive_dir=tgz_archive_dir,
  ) > 0


def all_on_disk_unprocessed_paths(unprocessed_by_tar: Any) -> Any:
  """
  Deduped on-disk checkpoint-unprocessed paths across all daily tars.
  
  Args:
    unprocessed_by_tar (Any): Unprocessed by tar passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> all_on_disk_unprocessed_paths(None)  # doctest: +SKIP
  """
  seen = set()
  result = []
  for tar_norm in (unprocessed_by_tar or {}):
    for path in on_disk_unprocessed_paths_for_tar(unprocessed_by_tar, tar_norm):
      if path in seen:
        continue
      seen.add(path)
      result.append(path)
  return result


def iter_checkpoint_incomplete_days_oldest_first(
  unprocessed_by_tar: Any,
  *,
  tgz_archive_dir: str,
) -> Iterator[Any]:
  """
  Yield ``(day_date, tar_norm, aligned_on_disk_paths)`` oldest calendar day.
  
    first.
  
  Args:
    unprocessed_by_tar (Any): Unprocessed by tar passed to this helper.
    tgz_archive_dir (str): String for tgz archive dir.
  
  Yields:
    Iterator[Any]: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> iter_checkpoint_incomplete_days_oldest_first(None, "x")
  """
  if not tgz_archive_dir or not unprocessed_by_tar:
    return
  ranked = []
  seen = set()
  for tar_path in iter_daily_tar_paths(tgz_archive_dir):
    tar_norm = os.path.normpath(tar_path)
    if tar_norm in seen:
      continue
    seen.add(tar_norm)
    paths = aligned_on_disk_unprocessed_paths_for_tar(
        unprocessed_by_tar,
        tar_norm,
        tgz_archive_dir=tgz_archive_dir,
    )
    if not paths:
      continue
    day_date = calendar_date_from_daily_tar_path(tar_norm)
    if day_date is None:
      continue
    ranked.append((day_date, tar_norm, paths))
  for tar_norm in (unprocessed_by_tar or {}):
    tar_norm = os.path.normpath(str(tar_norm or ""))
    if not tar_norm or tar_norm in seen:
      continue
    paths = aligned_on_disk_unprocessed_paths_for_tar(
        unprocessed_by_tar,
        tar_norm,
        tgz_archive_dir=tgz_archive_dir,
    )
    if not paths:
      continue
    day_date = calendar_date_from_daily_tar_path(tar_norm)
    if day_date is None:
      continue
    ranked.append((day_date, tar_norm, paths))
  ranked.sort(key=lambda item: item[0])
  for item in ranked:
    yield item


def tail_eligible_days_from_unprocessed(
  unprocessed_by_tar: Any,
  *,
  tgz_archive_dir: str,
  max_files: Any,
) -> Any:
  """
  Oldest-first ``(tar_norm, paths)`` with ``1 <= len(paths) <= max_files``.
  
  Args:
    unprocessed_by_tar (Any): Unprocessed by tar passed to this helper.
    tgz_archive_dir (str): String for tgz archive dir.
    max_files (Any): Iterable of filesystem paths as strings.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> tail_eligible_days_from_unprocessed(None, "x", None)  # doctest: +SKIP
  """
  max_files = max(1, int(max_files))
  result = []
  for _day, tar_norm, paths in iter_checkpoint_incomplete_days_oldest_first(
      unprocessed_by_tar,
      tgz_archive_dir=tgz_archive_dir,
  ):
    count = len(paths)
    if 1 <= count <= max_files:
      result.append((tar_norm, paths))
  return result


def oldest_checkpoint_incomplete_tar(
  unprocessed_by_tar: Any,
  *,
  tgz_archive_dir: str,
  newest_first: bool = False,
) -> Any:
  """
  Return the selected daily tar with tar-aligned on-disk unprocessed paths.
  
  Args:
    unprocessed_by_tar (Any): Unprocessed by tar passed to this helper.
    tgz_archive_dir (str): String for tgz archive dir.
    newest_first (bool): Boolean flag for newest first.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> oldest_checkpoint_incomplete_tar(None, "x", True)  # doctest: +SKIP
  """
  if not tgz_archive_dir or not unprocessed_by_tar:
    return ""
  ranked = []
  seen = set()
  for tar_path in iter_daily_tar_paths(tgz_archive_dir):
    tar_norm = os.path.normpath(tar_path)
    if tar_norm in seen:
      continue
    seen.add(tar_norm)
    if not aligned_unprocessed_tar_paths_still_on_disk(
        unprocessed_by_tar,
        tar_norm,
        tgz_archive_dir=tgz_archive_dir,
    ):
      continue
    day_date = calendar_date_from_daily_tar_path(tar_norm)
    if day_date is None:
      continue
    ranked.append((day_date, tar_norm))
  for tar_norm in (unprocessed_by_tar or {}):
    tar_norm = os.path.normpath(str(tar_norm or ""))
    if not tar_norm or tar_norm in seen:
      continue
    if not aligned_unprocessed_tar_paths_still_on_disk(
        unprocessed_by_tar,
        tar_norm,
        tgz_archive_dir=tgz_archive_dir,
    ):
      continue
    day_date = calendar_date_from_daily_tar_path(tar_norm)
    if day_date is None:
      continue
    ranked.append((day_date, tar_norm))
  if not ranked:
    return ""
  ranked.sort(key=lambda item: item[0])
  return ranked[-1][1] if newest_first else ranked[0][1]


def build_chunk_day_histogram(paths: Any, tgz_archive_dir: str) -> Any:
  """
  Count chunk paths per calendar day (for handoff chunk telemetry).
  
  Args:
    paths (Any): Iterable of filesystem paths as strings.
    tgz_archive_dir (str): String for tgz archive dir.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> build_chunk_day_histogram(None, "x")  # doctest: +SKIP
  """
  histogram = {}
  for path in paths or ():
    for tar_path in daily_tar_paths_for_stats_paths([path], tgz_archive_dir):
      day_date = calendar_date_from_daily_tar_path(tar_path)
      if day_date is None:
        continue
      token = day_date.isoformat()
      histogram[token] = histogram.get(token, 0) + 1
  return histogram


def reconcile_orphan_inflight_for_oldest_tar(
  *,
  oldest_tar: Any,
  blocked_paths: Any,
  inflight_archive_paths: Any,
  pending_append_by_daily_tar: Any,
  in_flight_archive_tars: Any,
  tgz_archive_dir: str,
  last_reclaim_monotonic_by_path: Any | None = None,
  reclaim_throttle_s: float = 30.0,
  now: Any | None = None,
  log_fn: Any | None = None,
  newest_first: bool = False,
) -> Any:
  """
  Return blocked inflight paths with no active archive job or pending append.
  
  Args:
    oldest_tar (Any): Oldest tar passed to this helper.
    blocked_paths (Any): Iterable of filesystem paths as strings.
    inflight_archive_paths (Any): Iterable of filesystem paths as strings.
    pending_append_by_daily_tar (Any): Pending append by daily tar passed to
    this helper.
    in_flight_archive_tars (Any): In flight archive tars passed to this
    helper.
    tgz_archive_dir (str): String for tgz archive dir.
    last_reclaim_monotonic_by_path (Any | None): One of ``Any``, ``None``.
    reclaim_throttle_s (float): Floating-point value for reclaim throttle s.
    now (Any | None): One of ``Any``, ``None``.
    log_fn (Any | None): One of ``Any``, ``None``.
    newest_first (bool): Boolean flag for newest first.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> reconcile_orphan_inflight_for_oldest_tar(0)  # doctest: +SKIP
  """
  import time

  oldest_tar_norm = os.path.normpath(str(oldest_tar or ""))
  if not oldest_tar_norm or not blocked_paths or not inflight_archive_paths:
    return []
  active_tars = {
      os.path.normpath(str(tar_path))
      for tar_path in (in_flight_archive_tars or ())
  }
  if oldest_tar_norm in active_tars:
    return []
  pending_bucket = set()
  for tar_path, paths in (pending_append_by_daily_tar or {}).items():
    if os.path.normpath(str(tar_path)) == oldest_tar_norm and paths:
      pending_bucket |= set(paths)
  blocked_set = set(blocked_paths or ())
  mono_now = float(now if now is not None else time.monotonic())
  throttle_state = (
      last_reclaim_monotonic_by_path
      if last_reclaim_monotonic_by_path is not None
      else {}
  )
  reclaimed = []
  cross_day_reclaimed = 0
  for path in list(inflight_archive_paths or ()):
    if path not in blocked_set:
      continue
    calendar_tars = {
        os.path.normpath(str(tar_path))
        for tar_path in daily_tar_paths_for_stats_paths(
            [path],
            tgz_archive_dir,
        )
    }
    if not calendar_tars:
      continue
    aligned_with_oldest = oldest_tar_norm in calendar_tars
    if aligned_with_oldest:
      if path in pending_bucket:
        continue
    else:
      if calendar_tars & active_tars:
        continue
      if any(
          path in set((pending_append_by_daily_tar or {}).get(tar_path, ()))
          for tar_path in calendar_tars
      ):
        continue
    last_reclaim = float(throttle_state.get(path, 0.0))
    if mono_now - last_reclaim < reclaim_throttle_s:
      continue
    throttle_state[path] = mono_now
    reclaimed.append(path)
    if not aligned_with_oldest:
      cross_day_reclaimed += 1
  if reclaimed and log_fn is not None:
    msg = (
        "orphan inflight reclaim oldest_tar=%s reclaimed_n=%d"
        % (oldest_tar_norm, len(reclaimed))
    )
    if cross_day_reclaimed:
      msg += " cross_day_n=%d detail=cross_day_bucket" % cross_day_reclaimed
    log_fn(msg, flush=True)
  return reclaimed


def handoff_path_lacks_daily_archive(
  stats_path: str,
  tgz_archive_dir: str,
) -> bool:
  """
  True when every derived daily tar for ``stats_path`` has no populate source.
  
  Used to age misbucket handoff leads (e.g. basename epoch maps to a future day
  with neither sealed ``.tar.zst`` nor mutable ``.tar``).
  
  Args:
    stats_path (str): String for stats path.
    tgz_archive_dir (str): String for tgz archive dir.
  
  Returns:
    bool: True or False for this check.
  
  Examples:
    >>> handoff_path_lacks_daily_archive("x", "x")  # doctest: +SKIP
  """
  if not stats_path or not tgz_archive_dir:
    return False
  derived = daily_tar_paths_for_stats_paths([stats_path], tgz_archive_dir)
  if not derived:
    return False
  from hpcperfstats.dbload.lib.archive_compress import compressed_sibling_paths

  for tar_path in derived:
    zst_path, _gz_path = compressed_sibling_paths(tar_path)
    if daily_archive_populate_source_exists(zst_path):
      return False
  return True


def age_misbucket_handoff_priority_paths(
  handoff_priority_paths: Any,
  *,
  tgz_archive_dir: str,
  handoff_source_tar_by_path: Any | None = None,
  log_fn: Any | None = None,
) -> Any:
  """
  Remove forward-misbucket handoff leads with ``no_daily_archive``.
  
  Only ages when the path's derived calendar day is **strictly after** the
  source tar day that requeued it and that derived day has neither sealed nor
  mutable daily archive. Same-day / backward-derived handoffs are kept so
  legitimate cross-day retry and same-boot duplicate guards still work.
  
  Mutates ``handoff_priority_paths`` (and optional
    ``handoff_source_tar_by_path``).
  Returns source daily-tar paths that no longer have any handoff pin.
  
  Args:
    handoff_priority_paths (Any): Iterable of filesystem paths as strings.
    tgz_archive_dir (str): String for tgz archive dir.
    handoff_source_tar_by_path (Any | None): One of ``Any``, ``None``.
    log_fn (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> age_misbucket_handoff_priority_paths(None, "x", None, None)
  """
  if not handoff_priority_paths or not tgz_archive_dir:
    return set()
  source_map = handoff_source_tar_by_path
  to_drop = []
  for path in list(handoff_priority_paths):
    if not handoff_path_lacks_daily_archive(path, tgz_archive_dir):
      continue
    source_tar = None
    if source_map is not None:
      source_tar = source_map.get(path)
    if not source_tar:
      continue
    source_day = calendar_date_from_daily_tar_path(source_tar)
    derived = _derive_stats_path_date(path)
    if source_day is None or derived is None or derived <= source_day:
      continue
    to_drop.append(path)
  if not to_drop:
    return set()
  candidate_sources = set()
  for path in to_drop:
    derived = _derive_stats_path_date(path)
    derived_day = derived.isoformat() if derived is not None else ""
    source_tar = None
    source_day = ""
    if source_map is not None:
      source_tar = source_map.pop(path, None)
    if source_tar:
      source_tar = os.path.normpath(source_tar)
      candidate_sources.add(source_tar)
      src_date = calendar_date_from_daily_tar_path(source_tar)
      source_day = src_date.isoformat() if src_date is not None else source_tar
    handoff_priority_paths.discard(path)
    if log_fn is not None:
      log_fn(
          "handoff_priority_age path=%s source_day=%s "
          "derived_day=%s reason=no_daily_archive"
          % (path, source_day, derived_day),
      )
  clear_sources = set()
  for source_tar in candidate_sources:
    still_pinned = False
    if source_map is not None:
      still_pinned = any(
          os.path.normpath(source_map.get(p) or "") == source_tar
          for p in handoff_priority_paths
      )
    if not still_pinned:
      clear_sources.add(source_tar)
  return clear_sources


def select_ingest_chunk_paths(
  pending: Any,
  *,
  oldest_tar: Any,
  unprocessed_by_tar: Any,
  inflight_archive_paths: Any,
  tgz_archive_dir: str,
  chunk_size: int,
  handoff_priority_paths: Any | None = None,
  handoff_source_tar_by_path: Any | None = None,
  deferred_waiting_source_tars: Any | None = None,
  log_fn: Any | None = None,
  newest_first: bool = False,
) -> Any:
  """
  While the selected checkpoint-blocked tar has work, restrict chunk to it.
  
  When the gate tar calendar day is **strictly newer** than the oldest day with
  still-on-disk non-ingested work (handoff pins and/or aligned unprocessed),
  prepend that **entire** oldest day uncapped (additive), then fill a full
  ``chunk_size`` of gate/pad work. Other older days' pins stay in
  ``handoff_priority_paths`` but are not prepended this chunk.
  
  Args:
    pending (Any): Pending passed to this helper.
    oldest_tar (Any): Oldest tar passed to this helper.
    unprocessed_by_tar (Any): Unprocessed by tar passed to this helper.
    inflight_archive_paths (Any): Iterable of filesystem paths as strings.
    tgz_archive_dir (str): String for tgz archive dir.
    chunk_size (int): Integer value for chunk size.
    handoff_priority_paths (Any | None): One of ``Any``, ``None``.
    handoff_source_tar_by_path (Any | None): One of ``Any``, ``None``.
    deferred_waiting_source_tars (Any | None): One of ``Any``, ``None``.
    log_fn (Any | None): One of ``Any``, ``None``.
    newest_first (bool): Boolean flag for newest first.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> select_ingest_chunk_paths(0)  # doctest: +SKIP
  """
  target_chunk_size = int(chunk_size)
  if target_chunk_size <= 0:
    return []
  pending_list = list(pending or ())
  handoff_lead: list = []
  handoff_set = set(handoff_priority_paths or ())
  oldest_tar_norm = os.path.normpath(str(oldest_tar)) if oldest_tar else ""
  deferred_sources = {
      os.path.normpath(str(t))
      for t in (deferred_waiting_source_tars or ())
      if t
  }
  source_map = handoff_source_tar_by_path if isinstance(
      handoff_source_tar_by_path, dict,
  ) else {}
  inflight_set = set(inflight_archive_paths or ())
  gate_day = calendar_date_from_daily_tar_path(oldest_tar_norm)
  oldest_work_tar = ""
  oldest_work_day = None
  if tgz_archive_dir and (handoff_set or unprocessed_by_tar):
    work_candidates = []
    for tar_key, paths in (unprocessed_by_tar or {}).items():
      tar_norm = os.path.normpath(str(tar_key or ""))
      day = calendar_date_from_daily_tar_path(tar_norm)
      if day is None:
        continue
      aligned = aligned_on_disk_unprocessed_paths_for_tar(
          unprocessed_by_tar,
          tar_norm,
          tgz_archive_dir=tgz_archive_dir,
      )
      if any(path not in inflight_set for path in aligned):
        work_candidates.append((day, tar_norm))
    for path in handoff_set:
      if not path or path in inflight_set:
        continue
      if not os.path.isfile(path):
        continue
      if handoff_path_lacks_daily_archive(path, tgz_archive_dir):
        continue
      source_tar = os.path.normpath(str(source_map.get(path) or ""))
      derived = daily_tar_paths_for_stats_paths([path], tgz_archive_dir)
      candidate_tar = ""
      if source_tar and source_tar in derived:
        candidate_tar = source_tar
      elif derived:
        # Prefer the calendar-oldest derived tar for this path.
        dated = []
        for tar_path in derived:
          day = calendar_date_from_daily_tar_path(tar_path)
          if day is not None:
            dated.append((day, os.path.normpath(tar_path)))
        if dated:
          dated.sort(key=lambda item: item[0])
          candidate_tar = dated[0][1]
      if not candidate_tar:
        continue
      day = calendar_date_from_daily_tar_path(candidate_tar)
      if day is None:
        continue
      work_candidates.append((day, candidate_tar))
    if work_candidates:
      work_candidates.sort(key=lambda item: item[0])
      oldest_work_day, oldest_work_tar = work_candidates[0]

  additive = bool(
      gate_day is not None
      and oldest_work_day is not None
      and gate_day > oldest_work_day
      and oldest_work_tar
      and tgz_archive_dir
  )

  if additive:
    pending_set = set(pending_list)
    seen_lead: set = set()
    lead_paths: list = []

    def _path_maps_to_oldest_work(path: str) -> Any:
      """
      Internal helper to handle path maps to oldest work.
      
      Args:
        path (str): String for path.
      
      Returns:
        Any: Value produced by this call (type depends on inputs).
      
      Examples:
        >>> _path_maps_to_oldest_work("x")  # doctest: +SKIP
      """
      source_tar = os.path.normpath(str(source_map.get(path) or ""))
      if source_tar == oldest_work_tar:
        return True
      derived = daily_tar_paths_for_stats_paths([path], tgz_archive_dir)
      return oldest_work_tar in derived

    def _append_lead(path: str) -> None:
      """
      Internal helper to handle append lead.
      
      Args:
        path (str): String for path.
      
      Returns:
        None
      
      Examples:
        >>> _append_lead("x")  # doctest: +SKIP
      """
      if path in seen_lead or path in inflight_set:
        return
      if not path:
        return
      seen_lead.add(path)
      lead_paths.append(path)

    for path in pending_list:
      if path not in handoff_set:
        continue
      if handoff_path_lacks_daily_archive(path, tgz_archive_dir):
        if log_fn is not None:
          log_fn(
              "handoff_cross_day_skip path=%s reason=no_daily_archive"
              % path,
          )
        continue
      if _path_maps_to_oldest_work(path):
        _append_lead(path)
    for path in handoff_set:
      if path in pending_set:
        continue
      if not os.path.isfile(path):
        continue
      if handoff_path_lacks_daily_archive(path, tgz_archive_dir):
        if log_fn is not None:
          log_fn(
              "handoff_cross_day_skip path=%s reason=no_daily_archive"
              % path,
          )
        continue
      if _path_maps_to_oldest_work(path):
        _append_lead(path)
    for path in aligned_on_disk_unprocessed_paths_for_tar(
        unprocessed_by_tar,
        oldest_work_tar,
        tgz_archive_dir=tgz_archive_dir,
    ):
      if path in inflight_set:
        continue
      if not path or not os.path.isfile(path):
        continue
      _append_lead(path)

    handoff_lead = lead_paths
    deferred_days_n = 0
    if handoff_set:
      other_days = set()
      for path in handoff_set:
        if path in seen_lead:
          continue
        source_tar = os.path.normpath(str(source_map.get(path) or ""))
        derived = daily_tar_paths_for_stats_paths([path], tgz_archive_dir)
        for tar_path in derived:
          day = calendar_date_from_daily_tar_path(tar_path)
          if day is not None and day != oldest_work_day and day < gate_day:
            other_days.add(day)
        if source_tar and source_tar != oldest_work_tar:
          day = calendar_date_from_daily_tar_path(source_tar)
          if day is not None and day != oldest_work_day and day < gate_day:
            other_days.add(day)
      deferred_days_n = len(other_days)
    if handoff_lead and log_fn is not None:
      lead_day_s = (
          oldest_work_day.isoformat() if oldest_work_day is not None else ""
      )
      log_fn(
          "handoff_lead_day=%s handoff_lead_n=%d handoff_lead_uncapped=yes "
          "handoff_deferred_days_n=%d chunk_target=%d"
          % (
              lead_day_s,
              len(handoff_lead),
              deferred_days_n,
              target_chunk_size,
          ),
      )
    if handoff_lead:
      pending_list = [
          path for path in pending_list if path not in set(handoff_lead)
      ]
    # Additive: do **not** subtract lead length from target_chunk_size.
  elif handoff_set and oldest_tar_norm and tgz_archive_dir:
    pending_set = set(pending_list)
    cross_day_handoff = []
    same_day_deferred_handoff = []
    seen_lead: set = set()

    def _consider_handoff_path(path: str) -> None:
      """
      Internal helper to handle consider handoff path.
      
      Args:
        path (str): String for path.
      
      Returns:
        None
      
      Examples:
        >>> _consider_handoff_path("x")  # doctest: +SKIP
      """
      if path in seen_lead:
        return
      source_tar = os.path.normpath(str(source_map.get(path) or ""))
      derived_tars = daily_tar_paths_for_stats_paths(
          [path],
          tgz_archive_dir,
      )
      # Same-day preferential lead for deferred waiting source tars (CLI
      # current youngest gate): pins for that source day must not ride the
      # July pad forever — include even when gate tar ≠ source day.
      if (
          source_tar
          and source_tar in deferred_sources
          and source_tar in derived_tars
      ):
        seen_lead.add(path)
        same_day_deferred_handoff.append(path)
        return
      if oldest_tar_norm not in derived_tars:
        if handoff_path_lacks_daily_archive(path, tgz_archive_dir):
          if log_fn is not None:
            log_fn(
                "handoff_cross_day_skip path=%s reason=no_daily_archive"
                % path,
            )
          return
        seen_lead.add(path)
        cross_day_handoff.append(path)

    for path in pending_list:
      if path not in handoff_set:
        continue
      _consider_handoff_path(path)
    for path in handoff_set:
      if path in pending_set:
        continue
      if not path or not os.path.isfile(path):
        continue
      _consider_handoff_path(path)
    lead_candidates = same_day_deferred_handoff + cross_day_handoff
    if lead_candidates:
      handoff_lead = lead_candidates[:target_chunk_size]
      if handoff_lead:
        pending_list = [
            path for path in pending_list if path not in set(handoff_lead)
        ]
        target_chunk_size -= len(handoff_lead)
        if target_chunk_size <= 0:
          return handoff_lead
  if not oldest_tar or not tgz_archive_dir:
    tail = pending_list[:target_chunk_size]
    return handoff_lead + tail
  checkpoint_incomplete_on_disk = aligned_on_disk_unprocessed_paths_for_tar(
      unprocessed_by_tar,
      oldest_tar_norm,
      tgz_archive_dir=tgz_archive_dir,
  )
  inflight_for_oldest = False
  for path in inflight_archive_paths or ():
    if oldest_tar_norm in daily_tar_paths_for_stats_paths(
        [path],
        tgz_archive_dir,
    ):
      inflight_for_oldest = True
      break
  if not checkpoint_incomplete_on_disk and not inflight_for_oldest:
    tail = pending_list[:target_chunk_size]
    return handoff_lead + tail
  oldest_only = [
      path
      for path in pending_list
      if oldest_tar_norm in daily_tar_paths_for_stats_paths(
          [path],
          tgz_archive_dir,
      )
  ]
  if not oldest_only and checkpoint_incomplete_on_disk:
    aligned_blocked = [
        path
        for path in checkpoint_incomplete_paths_aligned_with_oldest_tar(
            checkpoint_incomplete_on_disk,
            oldest_tar_norm,
            tgz_archive_dir=tgz_archive_dir,
        )
        if path not in inflight_set
    ]
    if not aligned_blocked and pending_list:
      if log_fn is not None:
        gate_name = (
            "youngest_day_chunk_gate_cross_day_defer"
            if newest_first else "oldest_day_chunk_gate_cross_day_defer"
        )
        tar_name = "youngest_tar" if newest_first else "oldest_tar"
        log_fn(
            "%s %s=%s "
            "calendar_days=%s incomplete_n=%d pending_n=%d"
            % (
                gate_name,
                tar_name,
                oldest_tar_norm,
                build_chunk_day_histogram(checkpoint_incomplete_on_disk, tgz_archive_dir),
                len(checkpoint_incomplete_on_disk),
                len(pending_list),
            ),
        )
      tail = pending_list[:target_chunk_size]
      return handoff_lead + tail
    if aligned_blocked:
      if log_fn is not None:
        gate_name = (
            "youngest_day_chunk_gate_fallback"
            if newest_first else "oldest_day_chunk_gate_fallback"
        )
        tar_name = "youngest_tar" if newest_first else "oldest_tar"
        log_fn(
            "%s %s=%s "
            "calendar_days=%s incomplete_n=%d"
            % (
                gate_name,
                tar_name,
                oldest_tar_norm,
                build_chunk_day_histogram(aligned_blocked, tgz_archive_dir),
                len(aligned_blocked),
            ),
        )
      oldest_only = aligned_blocked
    else:
      if log_fn is not None:
        gate_name = (
            "youngest_day_chunk_gate_fallback"
            if newest_first else "oldest_day_chunk_gate_fallback"
        )
        tar_name = "youngest_tar" if newest_first else "oldest_tar"
        log_fn(
            "%s %s=%s "
            "calendar_days=%s incomplete_n=%d"
            % (
                gate_name,
                tar_name,
                oldest_tar_norm,
                build_chunk_day_histogram(checkpoint_incomplete_on_disk, tgz_archive_dir),
                len(checkpoint_incomplete_on_disk),
            ),
        )
      oldest_only = [
          path for path in checkpoint_incomplete_on_disk if path not in inflight_set
      ]
  oldest_slice = list(oldest_only[:target_chunk_size])
  chosen = set(handoff_lead)
  chosen.update(oldest_slice)
  pad = []
  if len(oldest_slice) < target_chunk_size:
    for path in pending_list:
      if path in chosen:
        continue
      pad.append(path)
      chosen.add(path)
      if len(oldest_slice) + len(pad) >= target_chunk_size:
        break
  if pad and log_fn is not None:
    gate_name = (
        "youngest_day_chunk_gate_pad"
        if newest_first else "oldest_day_chunk_gate_pad"
    )
    tar_name = "youngest_tar" if newest_first else "oldest_tar"
    log_fn(
        "%s %s=%s "
        "oldest_n=%d chunk_pad_n=%d chunk_target=%d"
        % (
            gate_name,
            tar_name,
            oldest_tar_norm,
            len(oldest_slice),
            len(pad),
            target_chunk_size,
        ),
    )
  return handoff_lead + oldest_slice + pad


def merge_daily_archive_members_l1_cache(
  canonical: Any,
  member_map: Any,
) -> None:
  """
  Merge appended tar members into the per-process L1 map when present.
  
  Args:
    canonical (Any): Canonical passed to this helper.
    member_map (Any): Member map passed to this helper.
  
  Returns:
    None
  
  Examples:
    >>> merge_daily_archive_members_l1_cache(None, None)  # doctest: +SKIP
  """
  if not member_map:
    return
  canonical = normalize_daily_compressed_path(canonical)
  cache_key = _daily_archive_members_cache_key(canonical)
  cached = _DAILY_ARCHIVE_MEMBERS_CACHE.get(cache_key)
  if cached is None:
    return
  merged = dict(cached)
  for name, size in member_map.items():
    size = int(size)
    prev = merged.get(name)
    if prev is None or size > prev:
      merged[name] = size
  _DAILY_ARCHIVE_MEMBERS_CACHE[cache_key] = merged


def build_tar_append_member_map(stats_paths: Any) -> Any:
  """
  Member name → byte size for paths successfully appended to a daily tar.
  
  Args:
    stats_paths (Any): Iterable of filesystem paths as strings.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> build_tar_append_member_map(None)  # doctest: +SKIP
  """
  member_map = {}
  for path in stats_paths or ():
    if not path:
      continue
    try:
      member_map[get_tar_member_name(path)] = int(os.path.getsize(path))
    except OSError:
      continue
  return member_map


def prepend_checkpoint_incomplete_paths_to_pending(
  pending: Any,
  blocked_paths: Any,
  *,
  exclude: Any | None = None,
  newest_first: bool = False,
) -> Any:
  """
  Merge ``blocked_paths`` at the head of ``pending`` (deduped, order preserved).
  
  Args:
    pending (Any): Pending passed to this helper.
    blocked_paths (Any): Iterable of filesystem paths as strings.
    exclude (Any | None): One of ``Any``, ``None``.
    newest_first (bool): Boolean flag for newest first.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> prepend_checkpoint_incomplete_paths_to_pending(None, None, None, True)
  """
  exclude_set = set(exclude or ())
  blocked = []
  seen = set()
  for path in blocked_paths or ():
    if path in exclude_set or path in seen:
      continue
    seen.add(path)
    blocked.append(path)
  if not blocked:
    return list(pending or ())
  merged = list(blocked)
  for path in pending or ():
    if path in seen:
      continue
    seen.add(path)
    merged.append(path)
  return merged


def try_reuse_pending_reconcile_unprocessed_cache(
  *,
  cached: Any,
  last_mono: Any,
  mono_now: Any,
  ttl_s: Any,
  last_incomplete_n: Any,
  last_oldest_tar: Any,
  stall_incomplete_n: Any | None = None,
  newest_first: bool = False,
  last_newest_first: Any | None = None,
  hard_ceiling_s: Any | None = None,
) -> Any:
  """
  Return ``(cached, oldest_tar, incomplete_n, reason)`` when skip is safe.
  
  Skips a full live unprocessed rebuild when the prior reconcile fingerprint
  (target tar + incomplete_n + ordering mode) is still valid.
  
  Soft ``ttl_s`` only forces a refresh when the incomplete fingerprint is
  missing or zero. A valid fingerprint (``incomplete_n > 0`` + tar) may reuse
  past soft TTL until ``hard_ceiling_s`` (default: max(soft TTL, 900s)) or the
  caller invalidates the cache — otherwise caps whose wall time exceeds soft
  TTL never amortize and rebuild forever.
  
  Args:
    cached (Any): Cached passed to this helper.
    last_mono (Any): Last mono passed to this helper.
    mono_now (Any): Mono now passed to this helper.
    ttl_s (Any): Ttl s passed to this helper.
    last_incomplete_n (Any): Last incomplete n passed to this helper.
    last_oldest_tar (Any): Last oldest tar passed to this helper.
    stall_incomplete_n (Any | None): One of ``Any``, ``None``.
    newest_first (bool): Boolean flag for newest first.
    last_newest_first (Any | None): One of ``Any``, ``None``.
    hard_ceiling_s (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> try_reuse_pending_reconcile_unprocessed_cache(0)  # doctest: +SKIP
  """
  if cached is None:
    return None
  if last_newest_first is not None and bool(last_newest_first) != bool(
      newest_first
  ):
    return None
  try:
    age = float(mono_now) - float(last_mono or 0.0)
  except (TypeError, ValueError):
    return None
  try:
    last_inc = int(last_incomplete_n) if last_incomplete_n is not None else None
  except (TypeError, ValueError):
    return None
  last_tar = str(last_oldest_tar or "")
  try:
    stall_n = int(stall_incomplete_n) if stall_incomplete_n is not None else None
  except (TypeError, ValueError):
    stall_n = None
  soft_ttl = float(ttl_s)
  if hard_ceiling_s is None:
    ceiling = max(soft_ttl, 900.0)
  else:
    ceiling = max(soft_ttl, float(hard_ceiling_s))
  has_fingerprint = last_inc is not None and last_inc > 0 and bool(last_tar)
  if has_fingerprint:
    if age >= ceiling:
      return None
    if stall_n and stall_n > 0 and last_inc == stall_n:
      return cached, last_tar, last_inc, "oldest_day_gate_stall_unchanged"
    return cached, last_tar, last_inc, "unchanged_incomplete"
  # No fingerprint: soft TTL only; never reuse zero/missing incomplete.
  if age >= soft_ttl:
    return None
  return None


def build_live_unprocessed_by_tar_for_reconcile(
  archive_data_dir: str,
  host_name_ext: Any,
  tgz_archive_dir: str,
  *,
  checkpoint_path: Any | None = None,
  checkpoint_paths: Any | None = None,
  pending_stats_paths: Any | None = None,
  maintenance_snapshot: Any | None = None,
) -> Any:
  """
  Unprocessed map for pending reconcile; live full-tree scan only when no.
  
    snapshot.
  
  Args:
    archive_data_dir (str): String for archive data dir.
    host_name_ext (Any): Host name ext passed to this helper.
    tgz_archive_dir (str): String for tgz archive dir.
    checkpoint_path (Any | None): One of ``Any``, ``None``.
    checkpoint_paths (Any | None): One of ``Any``, ``None``.
    pending_stats_paths (Any | None): One of ``Any``, ``None``.
    maintenance_snapshot (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> build_live_unprocessed_by_tar_for_reconcile(0)  # doctest: +SKIP
  """
  first_timestamp_by_path = None
  if maintenance_snapshot is not None:
    first_timestamp_by_path = maintenance_snapshot.first_timestamp_by_path
  unprocessed_by_tar = build_unprocessed_raw_by_daily_tar(
      archive_data_dir,
      host_name_ext,
      tgz_archive_dir,
      checkpoint_path=checkpoint_path,
      checkpoint_paths=checkpoint_paths,
      first_timestamp_by_path=first_timestamp_by_path,
      maintenance_snapshot=maintenance_snapshot,
  )
  return augment_unprocessed_by_tar_with_pending_paths(
      unprocessed_by_tar,
      pending_stats_paths=pending_stats_paths,
      tgz_archive_dir=tgz_archive_dir,
      checkpoint_path=checkpoint_path,
      checkpoint_paths=checkpoint_paths,
      first_timestamp_by_path=first_timestamp_by_path,
  )


def daily_tar_filesystem_quiescent(
  tar_norm: Any,
  remaining_raw_by_gz: Any,
  *,
  archive_data_dir: Any | None = None,
  host_name_ext: Any | None = None,
  tgz_archive_dir: Any | None = None,
  maintenance_snapshot: Any | None = None,
) -> Any:
  """
  True when no closed raw stats remain on disk for ``tar_norm``.
  
  Args:
    tar_norm (Any): Tar norm passed to this helper.
    remaining_raw_by_gz (Any): Remaining raw by gz passed to this helper.
    archive_data_dir (Any | None): One of ``Any``, ``None``.
    host_name_ext (Any | None): One of ``Any``, ``None``.
    tgz_archive_dir (Any | None): One of ``Any``, ``None``.
    maintenance_snapshot (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> daily_tar_filesystem_quiescent(None, None, None, None, None, None)
  """
  tar_norm = os.path.normpath(str(tar_norm or ""))
  if not tar_norm:
    return False
  zst_path, _gz_path = compressed_sibling_paths(tar_norm)
  if remaining_raw_by_gz_has_paths_on_disk(remaining_raw_by_gz or {}, zst_path):
    return False
  if archive_data_dir and host_name_ext and tgz_archive_dir:
    scoped = build_remaining_raw_for_daily_tar(
        archive_data_dir,
        host_name_ext,
        tgz_archive_dir,
        tar_norm,
        maintenance_snapshot=maintenance_snapshot,
        allow_full_snapshot=False,
    )
    for paths in scoped.values():
      if paths:
        return False
  return True


def daily_tar_eligible_for_quiescent_day_close_submit(
  tar_norm: Any,
  *,
  unprocessed_by_tar: Any,
  disqualified_daily_tars: Any,
  remaining_raw_by_gz: Any | None = None,
  day_phases: Any | None = None,
  local_tz: Any | None = None,
  now: Any | None = None,
  archive_data_dir: Any | None = None,
  host_name_ext: Any | None = None,
  tgz_archive_dir: Any | None = None,
  maintenance_snapshot: Any | None = None,
) -> Any:
  """
  Return ``(eligible, skip_reason)`` for quiescent startup DAY_CLOSE submit.
  
  Args:
    tar_norm (Any): Tar norm passed to this helper.
    unprocessed_by_tar (Any): Unprocessed by tar passed to this helper.
    disqualified_daily_tars (Any): Disqualified daily tars passed to this
    helper.
    remaining_raw_by_gz (Any | None): One of ``Any``, ``None``.
    day_phases (Any | None): One of ``Any``, ``None``.
    local_tz (Any | None): One of ``Any``, ``None``.
    now (Any | None): One of ``Any``, ``None``.
    archive_data_dir (Any | None): One of ``Any``, ``None``.
    host_name_ext (Any | None): One of ``Any``, ``None``.
    tgz_archive_dir (Any | None): One of ``Any``, ``None``.
    maintenance_snapshot (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> daily_tar_eligible_for_quiescent_day_close_submit(0)  # doctest: +SKIP
  """
  tar_norm = os.path.normpath(str(tar_norm or ""))
  if not tar_norm:
    return False, "invalid_tar_path"
  archive_dir = tgz_archive_dir or os.path.dirname(tar_norm)
  if aligned_unprocessed_tar_paths_still_on_disk(
      unprocessed_by_tar,
      tar_norm,
      tgz_archive_dir=archive_dir,
  ):
    return False, "checkpoint_incomplete"
  if not daily_tar_filesystem_quiescent(
      tar_norm,
      remaining_raw_by_gz,
      archive_data_dir=archive_data_dir,
      host_name_ext=host_name_ext,
      tgz_archive_dir=tgz_archive_dir,
      maintenance_snapshot=maintenance_snapshot,
  ):
    return False, "filesystem_not_quiescent"
  disqualified = _normalize_daily_tar_path_set(disqualified_daily_tars)
  if tar_norm in disqualified:
    return False, "disqualified"
  if local_tz is not None and not daily_tar_seal_calendar_eligible(
      tar_norm, local_tz, now=now):
    return False, "calendar_grace"
  if not daily_tar_needs_day_close_work(
      tar_norm,
      day_phases=day_phases,
      remaining_raw_by_gz=remaining_raw_by_gz,
  ):
    return False, "no_work"
  return True, ""


def days_quiescent_tar_needs_day_close_at_startup(
  unprocessed_by_tar: Any,
  *,
  tgz_archive_dir: str,
  checkpoint_complete_eligible: Any,
  remaining_raw_by_gz: Any | None = None,
  day_phases: Any | None = None,
  local_tz: Any | None = None,
  now: Any | None = None,
  disqualified_daily_tars: Any | None = None,
  archive_data_dir: Any | None = None,
  host_name_ext: Any | None = None,
  maintenance_snapshot: Any | None = None,
) -> Any:
  """
  Oldest-first quiescent dirty ``.tar`` paths outside checkpoint-complete set.
  
  Args:
    unprocessed_by_tar (Any): Unprocessed by tar passed to this helper.
    tgz_archive_dir (str): String for tgz archive dir.
    checkpoint_complete_eligible (Any): Checkpoint complete eligible passed to
    this helper.
    remaining_raw_by_gz (Any | None): One of ``Any``, ``None``.
    day_phases (Any | None): One of ``Any``, ``None``.
    local_tz (Any | None): One of ``Any``, ``None``.
    now (Any | None): One of ``Any``, ``None``.
    disqualified_daily_tars (Any | None): One of ``Any``, ``None``.
    archive_data_dir (Any | None): One of ``Any``, ``None``.
    host_name_ext (Any | None): One of ``Any``, ``None``.
    maintenance_snapshot (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> days_quiescent_tar_needs_day_close_at_startup(0)  # doctest: +SKIP
  """
  if not tgz_archive_dir:
    return []
  skip = _normalize_daily_tar_path_set(checkpoint_complete_eligible)
  ranked = []
  seen = set()
  universe = set(unprocessed_by_tar or ())
  for tar_path in iter_daily_tar_paths(tgz_archive_dir):
    universe.add(os.path.normpath(tar_path))
  for tar_norm in sorted(universe, key=_calendar_date_from_tar_sort_key):
    if tar_norm in seen or tar_norm in skip:
      continue
    seen.add(tar_norm)
    eligible, _reason = daily_tar_eligible_for_quiescent_day_close_submit(
        tar_norm,
        unprocessed_by_tar=unprocessed_by_tar,
        disqualified_daily_tars=disqualified_daily_tars or (),
        remaining_raw_by_gz=remaining_raw_by_gz,
        day_phases=day_phases,
        local_tz=local_tz,
        now=now,
        archive_data_dir=archive_data_dir,
        host_name_ext=host_name_ext,
        tgz_archive_dir=tgz_archive_dir,
        maintenance_snapshot=maintenance_snapshot,
    )
    if not eligible:
      continue
    day_date = calendar_date_from_daily_tar_path(tar_norm)
    if day_date is None:
      continue
    ranked.append((day_date, tar_norm))
  ranked.sort(key=lambda item: item[0])
  return [tar_norm for _, tar_norm in ranked]


def _calendar_date_from_tar_sort_key(tar_path: str) -> Any:
  """
  Internal helper to handle calendar date from tar sort key.
  
  Args:
    tar_path (str): String for tar path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _calendar_date_from_tar_sort_key("x")  # doctest: +SKIP
  """
  day = calendar_date_from_daily_tar_path(tar_path)
  if day is not None:
    return day
  return date.max


def build_disqualification_reasons_by_tar(
  *,
  tgz_archive_dir: str,
  inflight_paths: Any | None = None,
  pending_append_by_daily_tar: Any | None = None,
  in_flight_archive_tars: Any | None = None,
  pending_archive_task_tars: Any | None = None,
  unmapped_closed_raw_tars: Any | None = None,
  unprocessed_by_tar: Any | None = None,
  local_tz: Any | None = None,
  now: Any | None = None,
  first_timestamp_by_path: Any | None = None,
) -> Any:
  """
  Return ``{tar_path: set[reason_code]}`` for day-close candidate reporting.
  
  Args:
    tgz_archive_dir (str): String for tgz archive dir.
    inflight_paths (Any | None): One of ``Any``, ``None``.
    pending_append_by_daily_tar (Any | None): One of ``Any``, ``None``.
    in_flight_archive_tars (Any | None): One of ``Any``, ``None``.
    pending_archive_task_tars (Any | None): One of ``Any``, ``None``.
    unmapped_closed_raw_tars (Any | None): One of ``Any``, ``None``.
    unprocessed_by_tar (Any | None): One of ``Any``, ``None``.
    local_tz (Any | None): One of ``Any``, ``None``.
    now (Any | None): One of ``Any``, ``None``.
    first_timestamp_by_path (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> build_disqualification_reasons_by_tar(0)  # doctest: +SKIP
  """
  reasons = defaultdict(set)
  for tar_path in _normalize_daily_tar_path_set(in_flight_archive_tars):
    reasons[tar_path].add("in_flight_archive_job")
  for tar_path in _normalize_daily_tar_path_set(pending_archive_task_tars):
    reasons[tar_path].add("pending_archive_task")
  for tar_path, paths in (pending_append_by_daily_tar or {}).items():
    if paths:
      reasons[os.path.normpath(tar_path)].add("pending_append_cache")
  for stats_path in inflight_paths or ():
    tar_path = daily_tar_path_for_stats_path(
        stats_path,
        tgz_archive_dir,
        first_ts=(first_timestamp_by_path or {}).get(stats_path),
    )
    if tar_path:
      reasons[os.path.normpath(tar_path)].add("inflight_append_path")
  for tar_path in _normalize_daily_tar_path_set(unmapped_closed_raw_tars):
    reasons[tar_path].add("unmapped_closed_raw")
  if unprocessed_by_tar and tgz_archive_dir:
    for tar_path in unprocessed_by_tar:
      tar_norm = os.path.normpath(str(tar_path or ""))
      if not tar_norm:
        continue
      if aligned_unprocessed_tar_paths_still_on_disk(
          unprocessed_by_tar,
          tar_norm,
          tgz_archive_dir=tgz_archive_dir,
      ):
        reasons[tar_norm].add("checkpoint_incomplete")
  if local_tz is not None:
    for tar_path in list(reasons.keys()):
      if not daily_tar_seal_calendar_eligible(tar_path, local_tz, now=now):
        reasons[tar_path].add("calendar_grace")
    for tar_path in iter_daily_tar_paths(tgz_archive_dir or ""):
      tar_norm = os.path.normpath(tar_path)
      if not daily_tar_seal_calendar_eligible(tar_norm, local_tz, now=now):
        reasons[tar_norm].add("calendar_grace")
  return {tar: set(codes) for tar, codes in reasons.items()}


def build_day_close_disqualified_daily_tars(
  *,
  tgz_archive_dir: str,
  remaining_raw_by_gz: Any | None = None,
  pending_stats_paths: Any | None = None,
  inflight_paths: Any | None = None,
  pending_append_by_daily_tar: Any | None = None,
  in_flight_archive_tars: Any | None = None,
  pending_archive_task_tars: Any | None = None,
  unmapped_closed_raw_tars: Any | None = None,
  first_timestamp_by_path: Any | None = None,
) -> Any:
  """
  Union of daily ``.tar`` paths the janitor must not seal/verify/remove.
  
  Ingest-queue ``pending_stats_paths`` is intentionally excluded (checkpoint
  drives startup/immediate day-close eligibility instead).
  
  Args:
    tgz_archive_dir (str): String for tgz archive dir.
    remaining_raw_by_gz (Any | None): One of ``Any``, ``None``.
    pending_stats_paths (Any | None): One of ``Any``, ``None``.
    inflight_paths (Any | None): One of ``Any``, ``None``.
    pending_append_by_daily_tar (Any | None): One of ``Any``, ``None``.
    in_flight_archive_tars (Any | None): One of ``Any``, ``None``.
    pending_archive_task_tars (Any | None): One of ``Any``, ``None``.
    unmapped_closed_raw_tars (Any | None): One of ``Any``, ``None``.
    first_timestamp_by_path (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> build_day_close_disqualified_daily_tars(0)  # doctest: +SKIP
  """
  del pending_stats_paths  # legacy callers may still pass; ignored
  reasons = build_disqualification_reasons_by_tar(
      tgz_archive_dir=tgz_archive_dir,
      inflight_paths=inflight_paths,
      pending_append_by_daily_tar=pending_append_by_daily_tar,
      in_flight_archive_tars=in_flight_archive_tars,
      pending_archive_task_tars=pending_archive_task_tars,
      unmapped_closed_raw_tars=unmapped_closed_raw_tars,
      first_timestamp_by_path=first_timestamp_by_path,
  )
  disqualified = set(reasons.keys())
  if remaining_raw_by_gz:
    for gz_key, stats_paths in remaining_raw_by_gz.items():
      if stats_paths:
        disqualified.add(
            os.path.normpath(daily_tar_path_from_compressed(gz_key))
        )
  return frozenset(disqualified)


_DAILY_TAR_BASENAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.tar$")
_DAILY_ZST_BASENAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.tar\.zst$")
_DAILY_GZ_BASENAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.tar\.gz$")
_DAILY_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

STREAM_ARCHIVE_TASK = "stream_archive"

MIGRATE_GZ_STATUS_CONVERTED = "converted"
MIGRATE_GZ_STATUS_DROPPED_ONLY = "dropped_only"
MIGRATE_GZ_STATUS_SKIPPED_LOCKED = "skipped_locked"
MIGRATE_GZ_STATUS_SKIPPED_NO_GZ = "skipped_no_gz"
MIGRATE_GZ_STATUS_FAILED = "failed"
MIGRATE_GZ_STATUS_KEPT_MISMATCH = "kept_mismatch"
MIGRATE_GZ_STATUS_PLANNED = "planned"


def _get_archive_validation_worker_count(total_items: Any) -> Any:
  """
  Bounded worker count for archive read/validation fanout.
  
  Args:
    total_items (Any): Total items passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _get_archive_validation_worker_count(None)  # doctest: +SKIP
  """
  if total_items <= 0:
    return 1
  env = os.environ.get("SYNC_ARCHIVE_VALIDATION_WORKERS", "").strip()
  if env:
    try:
      configured = max(1, int(env))
    except ValueError:
      configured = max(1, int(cfg.get_sync_archive_validation_max_workers()))
  else:
    configured = max(1, int(cfg.get_sync_archive_validation_max_workers()))
  return max(1, min(total_items, configured))


def _iter_archive_validation_results_stream(
  gz_paths: Any,
  *,
  log_fn: Any = log_print,
  validation_cache: Any | None = None,
  allow_auto_seal: bool = True,
) -> Iterator[Any]:
  """
  Yield ``(gz_path, ok, members)`` as validations complete.
  
  - Keeps apply stage serial by yielding one completed result at a time.
  - Uses bounded thread fanout for read/validation only.
  - Uses shared validation_cache only on serial path (thread-safe by design).
  
  Args:
    gz_paths (Any): Iterable of filesystem paths as strings.
    log_fn (Any): Callable invoked by this helper.
    validation_cache (Any | None): One of ``Any``, ``None``.
    allow_auto_seal (bool): Boolean flag for allow auto seal.
  
  Yields:
    Iterator[Any]: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _iter_archive_validation_results_stream(None, None, None, True)
  """
  gz_paths = list(gz_paths)
  if not gz_paths:
    return
  workers = _get_archive_validation_worker_count(len(gz_paths))
  if workers <= 1:
    for gz_path in gz_paths:
      ok, members = validate_sealed_daily_archive_for_raw_removal(
          gz_path,
          log_fn=log_fn,
          validation_cache=validation_cache,
          allow_auto_seal=allow_auto_seal,
      )
      yield gz_path, ok, members
    return

  def _validate_one(gz_path: str) -> Any:
    """
    Internal helper to validate the one.
    
    Args:
      gz_path (str): String for gz path.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _validate_one("x")  # doctest: +SKIP
    """
    return validate_sealed_daily_archive_for_raw_removal(
        gz_path,
        log_fn=log_fn,
        validation_cache=None,
        allow_auto_seal=allow_auto_seal,
    )

  for gz_path, packed, err in iter_bounded_thread_pool(
      gz_paths,
      _validate_one,
      max_workers=workers,
      thread_role="archive-validation",
  ):
    if err is not None:
      if log_fn:
        log_fn(
            "Skipping removal: validation worker error for %s (%s)" % (gz_path, err),
            flush=True,
        )
      ok, members = False, None
    else:
      ok, members = packed
    yield gz_path, ok, members


def _log_archive_validation_summary(
  *,
  log_fn: Any,
  validation_targets_count: int,
  workers: int,
  success_count: int,
  failed_count: int,
  validation_started: Any,
  validation_cache: Any,
) -> None:
  """
  Internal helper to log the archive validation summary.
  
  Args:
    log_fn (Any): Callable invoked by this helper.
    validation_targets_count (int): Integer value for validation targets
    count.
    workers (int): Integer value for workers.
    success_count (int): Integer value for success count.
    failed_count (int): Integer value for failed count.
    validation_started (Any): Validation started passed to this helper.
    validation_cache (Any): Validation cache passed to this helper.
  
  Returns:
    None
  
  Examples:
    >>> _log_archive_validation_summary(None, 0, 0, 0, 0, None, None)
  """
  if not log_fn:
    return
  log_fn(
      "Archive validation parallel summary archives=%d workers=%d success=%d failed=%d elapsed_s=%.3f"
      % (
          validation_targets_count,
          workers,
          success_count,
          failed_count,
          max(0.0, time.time() - validation_started),
      ),
      flush=True,
  )
  log_fn(
      "Archive validation cache summary hits=%d misses=%d"
      % (int(validation_cache["hits"]), int(validation_cache["misses"])),
      flush=True,
  )


def _is_lock_file_name(name: Any) -> Any:
  """
  Return True for sidecar/advisory lock files (e.g. *.fnctl.lock).
  
  Args:
    name (Any): Name passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _is_lock_file_name(None)  # doctest: +SKIP
  """
  # We intentionally skip generic *.lock too, because different lock
  # implementations may exist on the filesystem (and we don't want them
  # mistaken for stats data files during archive discovery).
  return name.endswith(LOCK_SUFFIX) or name.endswith(".lock")


def _remove_read_lock_sidecar(target_path: str) -> None:
  """
  Best-effort cleanup for helper read-lock sidecars.
  
  Args:
    target_path (str): String for target path.
  
  Returns:
    None
  
  Examples:
    >>> _remove_read_lock_sidecar("x")  # doctest: +SKIP
  """
  try:
    os.remove("%s%s" % (target_path, LOCK_SUFFIX))
  except OSError:
    pass


def read_stats_file_head_identity(
  stats_fname: Any,
  parse_first_ts_fn: Any | None = None,
) -> Any:
  """
  Return ``(host, timestamp_utc)`` from the first stats timestamp line in the.
  
    file.
  
  ``host`` is the monitor hostname token from file content (same as
    ``host_data.host``
  after ingest), not the archive subdirectory name in ``stats_fname``.
  
  Args:
    stats_fname (Any): Stats fname passed to this helper.
    parse_first_ts_fn (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> read_stats_file_head_identity(None, None)  # doctest: +SKIP
  """
  if parse_first_ts_fn is None:
    parse_first_ts_fn = parse_first_timestamp_line
  try:
    with file_read_lock_wait(stats_fname):
      with open(stats_fname, "r") as f:
        head = []
        for line in f:
          head.append(line)
          stripped = line.lstrip()
          if stripped and stripped[0].isdigit():
            break
  except OSError:
    return None, None
  finally:
    _remove_read_lock_sidecar(stats_fname)
  t, _jid, host = parse_first_ts_fn(head)
  if t is None or not host:
    return None, None
  from datetime import datetime, timezone

  # Gate and duplicate detection bucket by Unix second; ingest may store subseconds.
  timestamp_utc = datetime.fromtimestamp(int(float(t)), tz=timezone.utc)
  return str(host).strip(), timestamp_utc


def read_stats_file_tail_identity(stats_fname: Any) -> Any:
  """
  Return ``(host, timestamp_utc)`` from the last stats timestamp line (EOF-.
  
    backward).
  
  Uses a bounded tail read (no full-file load). ``host`` is the monitor hostname
  token from file content (same as ``host_data.host`` after ingest).
  
  Args:
    stats_fname (Any): Stats fname passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> read_stats_file_tail_identity(None)  # doctest: +SKIP
  """
  from datetime import datetime, timezone

  from hpcperfstats.dbload.lib.sync_timedb_parsing import (
      parse_last_timestamp_line_streaming,
  )

  t, _jid, host = parse_last_timestamp_line_streaming(stats_fname)
  if t is None or not host:
    return None, None
  timestamp_utc = datetime.fromtimestamp(int(float(t)), tz=timezone.utc)
  return str(host).strip(), timestamp_utc


def _read_first_timestamp_from_stats_file(
  stats_fname: Any,
  parse_first_ts_fn: Any,
) -> Any:
  """
  Read minimal file head and parse first stats timestamp string.
  
  Args:
    stats_fname (Any): Stats fname passed to this helper.
    parse_first_ts_fn (Any): Callable invoked by this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _read_first_timestamp_from_stats_file(None, None)  # doctest: +SKIP
  """
  _host, timestamp_utc = read_stats_file_head_identity(stats_fname, parse_first_ts_fn)
  if timestamp_utc is None:
    return None
  return str(int(timestamp_utc.timestamp()))


def collect_lock_sidecar_stats(
  directory: Any,
  stale_after_seconds: int = LOCK_EXPIRY_SECONDS,
) -> Any:
  """
  Return lock sidecar diagnostics for a directory tree.
  
  Args:
    directory (Any): Directory passed to this helper.
    stale_after_seconds (int): Integer value for stale after seconds.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> collect_lock_sidecar_stats(None, 0)  # doctest: +SKIP
  """
  if not directory or not os.path.isdir(directory):
    return {
        "lock_files": 0,
        "stale_lock_files": 0,
        "oldest_lock_age_seconds": 0.0,
    }
  now = time.time()
  lock_count = 0
  stale_count = 0
  oldest_age = 0.0
  for root, _dirs, files in os.walk(directory):
    for name in files:
      if not _is_lock_file_name(name):
        continue
      lock_count += 1
      path = os.path.join(root, name)
      try:
        age = max(0.0, now - os.path.getmtime(path))
      except OSError:
        continue
      oldest_age = max(oldest_age, age)
      if age > stale_after_seconds:
        stale_count += 1
  return {
      "lock_files": lock_count,
      "stale_lock_files": stale_count,
      "oldest_lock_age_seconds": oldest_age,
  }


def get_tar_member_name(file_path: str) -> Any:
  """
  Return the name used for a file inside a tar (path without leading slash).
  
  Args:
    file_path (str): String for file path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> get_tar_member_name("x")  # doctest: +SKIP
  """
  return file_path.lstrip("/")


def _tar_list_executable() -> Any:
  """
  GNU/BSD ``tar`` for ``tar tf`` integrity checks (same family as append).
  
  Returns:
    Any: Open return polymorphism from ``_tar_list_executable``: concrete type
    depends on inputs and branch (mapping, scalar, handle, or ``None``-like
    empty).
  
  Examples:
    >>> _tar_list_executable()  # doctest: +SKIP
  """
  from hpcperfstats.dbload.lib import zstd_cli
  return zstd_cli._tar_list_executable()


@contextlib.contextmanager
def _open_tarfile_for_read(
  path: str,
  num_threads: Any,
  *,
  apply_priority_wrap: bool = True,
) -> Iterator[Any]:
  """
  Open a tar or compressed daily archive for sequential reads.
  
  Args:
    path (str): String for path.
    num_threads (Any): Num threads passed to this helper.
    apply_priority_wrap (bool): Boolean flag for apply priority wrap.
  
  Yields:
    Iterator[Any]: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _open_tarfile_for_read("x", None, True)  # doctest: +SKIP
  """
  if path.endswith(DAILY_ARCHIVE_ZST_SUFFIX) and shutil.which("zstd"):
    with zstd_decompress_stdout(
        path,
        num_threads,
        apply_priority_wrap=apply_priority_wrap,
    ) as stdout:
      with tarfile.open(fileobj=stdout, mode="r|") as tf:
        yield tf
  elif path.endswith(DAILY_ARCHIVE_GZ_SUFFIX) and zstd_gzip_supported():
    with zstd_gzip_decompress_stdout(
        path,
        num_threads,
        apply_priority_wrap=apply_priority_wrap,
    ) as stdout:
      with tarfile.open(fileobj=stdout, mode="r|") as tf:
        yield tf
  else:
    with tarfile.open(path, "r") as tf:
      yield tf


def _iter_tar_members(tf: Any) -> Iterator[Any]:
  """
  Yield tar members without forcing a full in-memory member list.
  
  Args:
    tf (Any): Tf passed to this helper.
  
  Yields:
    Iterator[Any]: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _iter_tar_members(None)  # doctest: +SKIP
  """
  try:
    iterator = iter(tf)
  except TypeError:
    iterator = tf.getmembers()
  for member in iterator:
    yield member


def verify_tar_archive_readable(
  tar_path: str,
  *,
  assume_write_lock_held: bool = False,
) -> Any:
  """
  Return True if ``tar_path`` is a readable archive (full scan via ``tar tf``).
  
  For ``.tar.zst`` / ``.tar.gz``, uses ``zstd -d -c | tar tf -`` when zstd is
  available; otherwise ``tar tf`` on the file (or :mod:`tarfile` if ``tar`` is
  missing).
  
  When ``assume_write_lock_held`` is True, skip ``file_read_lock_wait`` (caller
  holds ``file_write_lock`` on ``tar_path``).
  
  Args:
    tar_path (str): String for tar path.
    assume_write_lock_held (bool): Boolean flag for assume write lock held.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Raises:
    Exception: Raised when ``verify_tar_archive_readable`` hits a
    ``Exception`` failure path.
  
  Examples:
    >>> verify_tar_archive_readable("x", True)  # doctest: +SKIP
  """
  if not os.path.isfile(tar_path):
    return False
  tar_bin = _tar_list_executable()

  def _locked_scan() -> Any:
    """
    Internal helper to handle locked scan.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _locked_scan()  # doctest: +SKIP
    """
    if detect_compressed_format(tar_path) in ("zst", "gz"):
      return zstd_compressed_archive_pipe_readable(
          tar_path,
          get_archive_zstd_thread_count(),
      )
    result = subprocess.run(
        [tar_bin, "tf", tar_path],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0

  try:
    if assume_write_lock_held:
      return _locked_scan()
    with _archive_file_read_lock_wait(tar_path):
      return _locked_scan()
  except FileNotFoundError:
    pass
  except TimeoutError:
    raise
  except (OSError, subprocess.SubprocessError):
    return False
  try:
    if assume_write_lock_held:
      with tarfile.open(tar_path, "r") as tf:
        for _member in _iter_tar_members(tf):
          pass
      return True
    with _archive_file_read_lock_wait(tar_path):
      with tarfile.open(tar_path, "r") as tf:
        for _member in _iter_tar_members(tf):
          pass
    return True
  except TimeoutError:
    raise
  except (tarfile.TarError, OSError, EOFError):
    return False


def get_file_member_sizes_from_gzip_archive(gz_path: str) -> Any:
  """
  File member name -> size (max if duplicated), reading **only** ``.tar.gz``.
  
  Does not open the sibling ``.tar``; used to compare sealed gzip to
    uncompressed tar.
  
  Args:
    gz_path (str): String for gz path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> get_file_member_sizes_from_gzip_archive("x")  # doctest: +SKIP
  """
  if not gz_path.endswith(".tar.gz") or not os.path.isfile(gz_path):
    return {}
  _readable, members = _scan_gzip_archive_members_and_readable(gz_path)
  return members


def _archive_file_identity(path: str) -> Any:
  """
  Return ``(mtime_ns, size)`` for cache keying, or ``None`` if missing.
  
  Args:
    path (str): String for path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _archive_file_identity("x")  # doctest: +SKIP
  """
  if not os.path.isfile(path):
    return None
  st = os.stat(path)
  return (int(st.st_mtime_ns), int(st.st_size))


def _build_archive_validation_cache_key(compressed_path: str) -> Any:
  """
  Internal helper to build the archive validation cache key.
  
  Args:
    compressed_path (str): String for compressed path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _build_archive_validation_cache_key("x")  # doctest: +SKIP
  """
  tar_path = daily_tar_path_from_compressed(compressed_path)
  return (
      compressed_path,
      _archive_file_identity(compressed_path),
      _archive_file_identity(tar_path),
  )


_DAILY_ARCHIVE_MEMBERS_CACHE = {}


def clear_daily_archive_members_cache() -> None:
  """
  Clear per-process daily archive member maps (tests and worker reset).
  
  Returns:
    None
  
  Examples:
    >>> clear_daily_archive_members_cache()  # doctest: +SKIP
  """
  _DAILY_ARCHIVE_MEMBERS_CACHE.clear()
  _INGEST_SKIPPED_CALENDAR_DAYS.clear()
  _LOGGED_ARCHIVE_DAY_INGEST_SKIP.clear()


_ARCHIVE_MEMBERS_INVALIDATION_HOOK = None
_DEFERRED_PREWARM_FLUSH_HOOK = None


def set_archive_members_invalidation_hook(hook: Any) -> None:
  """
  Register supervisor callback after L1/Redis member cache invalidation.
  
  Args:
    hook (Any): Hook passed to this helper.
  
  Returns:
    None
  
  Examples:
    >>> set_archive_members_invalidation_hook(None)  # doctest: +SKIP
  """
  global _ARCHIVE_MEMBERS_INVALIDATION_HOOK
  _ARCHIVE_MEMBERS_INVALIDATION_HOOK = hook


def reset_archive_members_invalidation_hook_for_tests() -> None:
  """
  Clear invalidation hook (unit tests).
  
  Returns:
    None
  
  Examples:
    >>> reset_archive_members_invalidation_hook_for_tests()  # doctest: +SKIP
  """
  global _ARCHIVE_MEMBERS_INVALIDATION_HOOK
  _ARCHIVE_MEMBERS_INVALIDATION_HOOK = None


def set_deferred_prewarm_flush_hook(hook: Any) -> None:
  """
  Register supervisor callback after daily_tar_restore clears.
  
  Args:
    hook (Any): Hook passed to this helper.
  
  Returns:
    None
  
  Examples:
    >>> set_deferred_prewarm_flush_hook(None)  # doctest: +SKIP
  """
  global _DEFERRED_PREWARM_FLUSH_HOOK
  _DEFERRED_PREWARM_FLUSH_HOOK = hook


def reset_deferred_prewarm_flush_hook_for_tests() -> None:
  """
  Clear deferred-prewarm flush hook (unit tests).
  
  Returns:
    None
  
  Examples:
    >>> reset_deferred_prewarm_flush_hook_for_tests()  # doctest: +SKIP
  """
  global _DEFERRED_PREWARM_FLUSH_HOOK
  _DEFERRED_PREWARM_FLUSH_HOOK = None


def notify_daily_tar_restore_cleared(day_token: Any) -> None:
  """
  Flush deferred sync re-prewarm after restore key is cleared.
  
  Args:
    day_token (Any): Day token passed to this helper.
  
  Returns:
    None
  
  Examples:
    >>> notify_daily_tar_restore_cleared(None)  # doctest: +SKIP
  """
  hook = _DEFERRED_PREWARM_FLUSH_HOOK
  if hook is None or not day_token:
    return
  try:
    hook(day_token)
  except TypeError:
    try:
      hook()
    except Exception:
      pass
  except Exception:
    pass


def _notify_archive_members_invalidation(
  canonical: Any,
  day_token: Any | None = None,
  reason: Any | None = None,
) -> None:
  """
  Internal helper to handle notify archive members invalidation.
  
  Args:
    canonical (Any): Canonical passed to this helper.
    day_token (Any | None): One of ``Any``, ``None``.
    reason (Any | None): One of ``Any``, ``None``.
  
  Returns:
    None
  
  Examples:
    >>> _notify_archive_members_invalidation(None, None, None)  # doctest: +SKIP
  """
  hook = _ARCHIVE_MEMBERS_INVALIDATION_HOOK
  if hook is None:
    return
  try:
    hook(canonical, day_token, reason)
  except TypeError:
    try:
      hook(canonical, day_token)
    except Exception:
      pass
  except Exception:
    pass


def invalidate_after_daily_tar_mutation(
  daily_tar_or_compressed_path: str,
  *,
  reason: Any | None = None,
  log_fn: Any | None = None,
) -> None:
  """
  Canonical hook after mutating a daily archive (append, dedupe, seal,.
  
    bootstrap, restore).
  
  Accepts ``YYYY-MM-DD.tar``, ``.tar.zst``, or legacy ``.tar.gz``; invalidates
    L1
  and Redis member maps for the canonical daily compressed key.
  
  Args:
    daily_tar_or_compressed_path (str): daily tar or compressed path as
    ``str``.
    reason (Any | None): One of ``Any``, ``None``.
    log_fn (Any | None): One of ``Any``, ``None``.
  
  Returns:
    None
  
  Examples:
    >>> invalidate_after_daily_tar_mutation("x", None, None)  # doctest: +SKIP
  """
  if not daily_tar_or_compressed_path:
    return
  canonical = normalize_daily_compressed_path(
      os.path.normpath(daily_tar_or_compressed_path),
  )
  invalidate_daily_archive_members_cache(canonical, reason=reason)
  if log_fn and reason:
    log_fn(
        "Archive members cache invalidated path=%s reason=%s"
        % (canonical, reason),
        flush=True,
    )


def invalidate_daily_archive_members_cache(
  compressed_path: str,
  *,
  reason: Any | None = None,
) -> None:
  """
  Drop cached member maps for a daily archive (append, seal, identity change).
  
  Args:
    compressed_path (str): String for compressed path.
    reason (Any | None): One of ``Any``, ``None``.
  
  Returns:
    None
  
  Examples:
    >>> invalidate_daily_archive_members_cache("x", None)  # doctest: +SKIP
  """
  if not compressed_path:
    return
  canonical = normalize_daily_compressed_path(compressed_path)
  day_date = calendar_date_from_daily_tar_path(
      daily_tar_path_from_compressed(canonical),
  )
  day_token = day_date.isoformat() if day_date is not None else None
  drop_keys = [
      key for key in _DAILY_ARCHIVE_MEMBERS_CACHE if key[0] == canonical
  ]
  for key in drop_keys:
    _DAILY_ARCHIVE_MEMBERS_CACHE.pop(key, None)
  try:
    from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
        archive_members_redis_enabled,
        invalidate_archive_members_redis,
    )
    if archive_members_redis_enabled():
      for key in drop_keys:
        invalidate_archive_members_redis(key)
      if not drop_keys:
        invalidate_archive_members_redis(_daily_archive_members_cache_key(canonical))
  except Exception:
    pass
  _notify_archive_members_invalidation(canonical, day_token, reason)


def _daily_archive_members_cache_enabled() -> Any:
  """
  Internal helper to handle daily archive members cache enabled.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _daily_archive_members_cache_enabled()  # doctest: +SKIP
  """
  return cfg.get_sync_archive_members_cache_enabled()


def _trim_daily_archive_members_cache() -> None:
  """
  Internal helper to handle trim daily archive members cache.
  
  Returns:
    None
  
  Examples:
    >>> _trim_daily_archive_members_cache()  # doctest: +SKIP
  """
  max_entries = cfg.get_sync_archive_members_cache_max_entries()
  while len(_DAILY_ARCHIVE_MEMBERS_CACHE) > max_entries:
    oldest_key = next(iter(_DAILY_ARCHIVE_MEMBERS_CACHE))
    _DAILY_ARCHIVE_MEMBERS_CACHE.pop(oldest_key, None)


def _daily_archive_members_cache_key(canonical_zst_path: str) -> Any:
  """
  Cache key keyed by canonical ``.tar.zst`` path and on-disk archive identities.
  
  Args:
    canonical_zst_path (str): String for canonical zst path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _daily_archive_members_cache_key("x")  # doctest: +SKIP
  """
  tar_path = daily_tar_path_from_compressed(canonical_zst_path)
  zst_path, gz_path = compressed_sibling_paths(tar_path)
  if os.path.isfile(zst_path):
    sealed_identity = _archive_file_identity(zst_path)
  elif os.path.isfile(gz_path):
    sealed_identity = _archive_file_identity(gz_path)
  else:
    sealed_identity = None
  return (
      canonical_zst_path,
      sealed_identity,
      _archive_file_identity(tar_path),
  )


def _resolve_sealed_daily_archive_path(compressed_path: str) -> Any:
  """
  Return on-disk sealed path (``.tar.zst`` or legacy ``.tar.gz``), if any.
  
  Args:
    compressed_path (str): String for compressed path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _resolve_sealed_daily_archive_path("x")  # doctest: +SKIP
  """
  if detect_compressed_format(compressed_path) is not None and os.path.isfile(
      compressed_path,
  ):
    return compressed_path
  canonical = normalize_daily_compressed_path(compressed_path)
  tar_path = daily_tar_path_from_compressed(canonical)
  zst_path, gz_path = compressed_sibling_paths(tar_path)
  if os.path.isfile(zst_path):
    return zst_path
  if os.path.isfile(gz_path):
    return gz_path
  return None


def daily_archive_populate_source_exists(compressed_path: str) -> Any:
  """
  True when a calendar day has a sealed archive and/or mutable ``.tar`` on disk.
  
  Args:
    compressed_path (str): String for compressed path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> daily_archive_populate_source_exists("x")  # doctest: +SKIP
  """
  canonical = normalize_daily_compressed_path(compressed_path)
  sealed_path = _resolve_sealed_daily_archive_path(canonical)
  tar_path = daily_tar_path_from_compressed(canonical)
  return sealed_path is not None or os.path.isfile(tar_path)


def _lookup_daily_archive_members_cache(compressed_path: str) -> Any:
  """
  Internal helper to handle lookup daily archive members cache.
  
  Args:
    compressed_path (str): String for compressed path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _lookup_daily_archive_members_cache("x")  # doctest: +SKIP
  """
  if not _daily_archive_members_cache_enabled():
    return None
  canonical = normalize_daily_compressed_path(compressed_path)
  cache_key = _daily_archive_members_cache_key(canonical)
  cached = _DAILY_ARCHIVE_MEMBERS_CACHE.get(cache_key)
  if cached is None:
    return None
  return dict(cached)


def _store_daily_archive_members_cache(
  compressed_path: str,
  members: Any,
) -> None:
  """
  Internal helper to handle store daily archive members cache.
  
  Args:
    compressed_path (str): String for compressed path.
    members (Any): Iterable of filesystem paths as strings.
  
  Returns:
    None
  
  Examples:
    >>> _store_daily_archive_members_cache("x", None)  # doctest: +SKIP
  """
  if not _daily_archive_members_cache_enabled():
    return
  canonical = normalize_daily_compressed_path(compressed_path)
  cache_key = _daily_archive_members_cache_key(canonical)
  _DAILY_ARCHIVE_MEMBERS_CACHE[cache_key] = dict(members)
  _trim_daily_archive_members_cache()


def _daily_archive_member_match_via_redis_l2(
  canonical: Any,
  compressed_path: str,
  member_name: Any,
  expected_size: int,
) -> Any:
  """
  Ingest duplicate-check via Redis L2 before local tar scan.
  
  Returns ``None`` when Redis is disabled or the caller should fall back to a
  mutable ``.tar`` scan (no sealed archive and Redis map not warm).
  
  Args:
    canonical (Any): Canonical passed to this helper.
    compressed_path (str): String for compressed path.
    member_name (Any): Member name passed to this helper.
    expected_size (int): Integer value for expected size.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _daily_archive_member_match_via_redis_l2(None, "x", None, 0)
  """
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      archive_members_redis_enabled,
      build_archive_members_redis_keys,
      get_archive_members_redis_client,
      redis_member_match_when_warm,
      redis_members_cache_is_fully_warm,
  )

  if not archive_members_redis_enabled():
    return None
  cache_key = _daily_archive_members_cache_key(canonical)
  keys = build_archive_members_redis_keys(cache_key)
  client = get_archive_members_redis_client(required=True)
  from hpcperfstats.dbload.lib.sync_timedb_ingest_worker_diagnostics import (
      update_worker_substage,
  )

  update_worker_substage(
      "archive_member_lookup",
      lookup_mode="hget",
  )
  warm_match = redis_member_match_when_warm(
      keys,
      member_name,
      expected_size,
      client=client,
  )
  if warm_match is not None:
    return warm_match
  sealed_path = _resolve_sealed_daily_archive_path(compressed_path)
  if sealed_path is None:
    if redis_members_cache_is_fully_warm(keys):
      return False
    return None
  client = get_archive_members_redis_client(required=True)
  return _member_match_via_redis_or_sealed_point(
      canonical,
      cache_key,
      keys,
      sealed_path,
      member_name,
      expected_size,
      client=client,
  )


def daily_archive_has_member_with_size(
  compressed_path: str,
  member_name: Any,
  expected_size: int,
) -> Any:
  """
  True when the daily archive contains ``member_name`` with exact byte size.
  
  Args:
    compressed_path (str): String for compressed path.
    member_name (Any): Member name passed to this helper.
    expected_size (int): Integer value for expected size.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Raises:
    Exception: Raised when ``daily_archive_has_member_with_size`` hits a
    ``Exception`` failure path.
  
  Examples:
    >>> daily_archive_has_member_with_size("x", None, 0)  # doctest: +SKIP
  """
  members = _lookup_daily_archive_members_cache(compressed_path)
  if members is not None:
    return members.get(member_name) == expected_size
  canonical = normalize_daily_compressed_path(compressed_path)
  try:
    redis_match = _daily_archive_member_match_via_redis_l2(
        canonical,
        compressed_path,
        member_name,
        expected_size,
    )
    if redis_match is not None:
      return redis_match
  except Exception:
    raise
  members = get_existing_archive_members_for_daily_archive(compressed_path)
  return members.get(member_name) == expected_size


class _MemberStreamEarlyExit(Exception):
  """
  Stop streaming after a single-member point lookup match.
  """


def _sealed_archive_member_has_exact_size(
  sealed_path: str,
  member_name: Any,
  expected_size: int,
) -> Any:
  """
  Return ``True``/``False`` when readable; ``None`` when sealed archive.
  
    unreadable.
  
  Args:
    sealed_path (str): String for sealed path.
    member_name (Any): Member name passed to this helper.
    expected_size (int): Integer value for expected size.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _sealed_archive_member_has_exact_size("x", None, 0)  # doctest: +SKIP
  """
  match_result = [None]

  def on_member(name: Any, size: int) -> None:
    """
    On member.
    
    Args:
      name (Any): Name passed to this helper.
      size (int): Integer value for size.
    
    Returns:
      None
    
    Raises:
      _MemberStreamEarlyExit: Raised when ``on_member`` hits a
      ``_MemberStreamEarlyExit`` failure path.
    
    Examples:
      >>> on_member(None, 0)  # doctest: +SKIP
    """
    if name == member_name:
      match_result[0] = int(size) == int(expected_size)
      raise _MemberStreamEarlyExit()

  try:
    readable, _, _, _stream_error = _stream_compressed_archive_members(
        sealed_path,
        on_member,
        apply_priority_wrap=False,
    )
    if not readable:
      return None
    return False if match_result[0] is None else match_result[0]
  except _MemberStreamEarlyExit:
    return match_result[0]


_INGEST_SEALED_LOOKUP_WARNED = set()
_INGEST_SEALED_LOOKUP_WARNED_MAX = 256
_INGEST_SKIPPED_CALENDAR_DAYS = OrderedDict()
_INGEST_SKIPPED_CALENDAR_DAYS_MAX = 512
_LOGGED_ARCHIVE_DAY_INGEST_SKIP = set()
_LOGGED_ARCHIVE_DAY_INGEST_SKIP_MAX = 256

SKIP_KIND_ZST_FRAME_INVALID = "zst_frame_invalid"
SKIP_KIND_TAR_TRUNCATED = "tar_truncated_or_unreadable"
SKIP_KIND_READ_ERROR = "read_error"


def _is_fnctl_read_lock_timeout_error(exc: Any) -> Any:
  """
  True when ``file_read_lock_wait`` timed out (transient append contention).
  
  Args:
    exc (Any): Exception instance being classified or logged.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _is_fnctl_read_lock_timeout_error(None)  # doctest: +SKIP
  """
  if exc is None:
    return False
  if isinstance(exc, TimeoutError):
    return True
  msg = str(exc).lower()
  return "timed out waiting" in msg and "fnctl.lock" in msg


def _is_fnctl_read_lock_timeout_detail(detail: Any) -> Any:
  """
  Internal helper to check if fnctl read lock timeout detail.
  
  Args:
    detail (Any): Detail passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _is_fnctl_read_lock_timeout_detail(None)  # doctest: +SKIP
  """
  if not detail:
    return False
  msg = str(detail).lower()
  return "timed out waiting" in msg and "fnctl.lock" in msg


_FNCTL_POPULATE_RETRY_DELAYS_S = (2.0, 5.0)


def _archive_members_fnctl_read_lock_timeout_seconds() -> Any:
  """
  Internal helper to archive the members fnctl read lock timeout seconds.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _archive_members_fnctl_read_lock_timeout_seconds()  # doctest: +SKIP
  """
  return cfg.get_sync_archive_members_fnctl_read_lock_timeout_seconds()


@contextlib.contextmanager
def _archive_file_read_lock_wait(target_path: str) -> Iterator[Any]:
  """
  Shared read-lock wait for archive populate/verify (INI-backed timeout).
  
  Best-effort unlinks the read-lock sidecar after release (parity with tar
  populate) so sealed-stream ``*.tar.zst.fnctl.lock`` files do not accumulate.
  
  Args:
    target_path (str): String for target path.
  
  Yields:
    Iterator[Any]: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _archive_file_read_lock_wait("x")  # doctest: +SKIP
  """
  try:
    with file_read_lock_wait(
        target_path,
        timeout_seconds=_archive_members_fnctl_read_lock_timeout_seconds(),
    ):
      yield
  finally:
    _remove_read_lock_sidecar(target_path)


def _populate_tar_file_read_lock_wait(target_path: str) -> Any:
  """
  Tar populate read-lock wait — bounded by populate_max_seconds when set.
  
  Args:
    target_path (str): String for target path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _populate_tar_file_read_lock_wait("x")  # doctest: +SKIP
  """
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      wait_for_daily_tar_restore_before_populate,
  )

  wait_for_daily_tar_restore_before_populate(target_path, log_fn=log_print)
  max_s = cfg.get_sync_archive_members_redis_populate_max_seconds()
  timeout = float(max_s) if max_s > 0 else float(
      _archive_members_fnctl_read_lock_timeout_seconds(),
  )
  return file_read_lock_wait(target_path, timeout_seconds=timeout)


def _decompress_should_unlink_compressed(tar_path: str) -> Any:
  """
  Whether restore may unlink the sealed sibling after materialize.
  
  Restore hot path must **never** build a full maintenance / remaining-raw
  census (gated prewarm stall). Day-close tar-drop owns sealed unlink after
  blocking remaining-raw is empty. While a sealed sibling exists, keep it.
  
  Args:
    tar_path (str): String for tar path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _decompress_should_unlink_compressed("x")  # doctest: +SKIP
  """
  if not tar_path:
    return True
  zst_path, gz_path = compressed_sibling_paths(tar_path)
  if os.path.isfile(zst_path) or os.path.isfile(gz_path):
    return False
  return True


def _populate_should_use_tar_scan(
  tar_path: str,
  zst_path: str,
  gz_path: str,
  sealed_path: str,
) -> Any:
  """
  Return ``(use_tar, reason)`` for populate source selection.
  
  Args:
    tar_path (str): String for tar path.
    zst_path (str): String for zst path.
    gz_path (str): String for gz path.
    sealed_path (str): String for sealed path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _populate_should_use_tar_scan("x", "x", "x", "x")  # doctest: +SKIP
  """
  del zst_path, gz_path, sealed_path
  if os.path.isfile(tar_path):
    return True, "tar_exists"
  return False, None


def _log_populate_source_decision(
  day_token: Any,
  tar_path: str,
  zst_path: str,
  gz_path: str,
  sealed_path: str,
) -> None:
  """
  Internal helper to log the populate source decision.
  
  Args:
    day_token (Any): Day token passed to this helper.
    tar_path (str): String for tar path.
    zst_path (str): String for zst path.
    gz_path (str): String for gz path.
    sealed_path (str): String for sealed path.
  
  Returns:
    None
  
  Examples:
    >>> _log_populate_source_decision(None, "x", "x", "x", "x")
  """
  use_tar, reason = _populate_should_use_tar_scan(
      tar_path, zst_path, gz_path, sealed_path,
  )
  dirty = is_daily_tar_sealed_dirty(tar_path, zst_path, gz_path) if os.path.isfile(
      tar_path,
  ) else False
  sealed_exists = bool(sealed_path and os.path.isfile(sealed_path))
  log_print(
      "INFO: populate_source_decision day=%s dirty=%s sealed_exists=%s "
      "use_tar=%s reason=%s tar=%s sealed=%s"
      % (
          day_token,
          dirty,
          sealed_exists,
          use_tar,
          reason or ("sealed_only" if not use_tar else "tar_exists"),
          tar_path,
          sealed_path or "",
      ),
      flush=True,
  )


def _resolve_sealed_path_for_day_token(day_token: Any) -> Any:
  """
  Internal helper to resolve the sealed path for day token.
  
  Args:
    day_token (Any): Day token passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _resolve_sealed_path_for_day_token(None)  # doctest: +SKIP
  """
  if not day_token or day_token == "unknown":
    return ""
  daily_dir = cfg.get_daily_archive_dir_path()
  if not daily_dir:
    return ""
  tar_path = os.path.join(daily_dir, "%s.tar" % day_token)
  zst_path, gz_path = compressed_sibling_paths(tar_path)
  if os.path.isfile(zst_path):
    return zst_path
  if os.path.isfile(gz_path):
    return gz_path
  return ""


def classify_sealed_archive_stream_failure(
  sealed_path: str,
  stream_error: Any | None = None,
) -> Any:
  """
  Classify sealed archive stream failure (single-flight populate winner only).
  
  Args:
    sealed_path (str): String for sealed path.
    stream_error (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> classify_sealed_archive_stream_failure("x", None)  # doctest: +SKIP
  """
  import subprocess

  from hpcperfstats.dbload.lib import zstd_cli

  stream_detail = ""
  if stream_error is not None:
    stream_detail = str(stream_error).strip()[:500]

  is_zst = (
      detect_compressed_format(sealed_path) == DAILY_ARCHIVE_ZST_SUFFIX
      or str(sealed_path).endswith(DAILY_ARCHIVE_ZST_SUFFIX)
  )
  if is_zst:
    try:
      zstd_cli.zstd_test(
          sealed_path,
          get_ingest_zstd_thread_count(),
          apply_priority_wrap=False,
      )
      detail = stream_detail or "tar stream unreadable"
      return SKIP_KIND_TAR_TRUNCATED, detail
    except subprocess.CalledProcessError as exc:
      zst_err = (getattr(exc, "stderr", None) or str(exc)).strip()[:500]
      return SKIP_KIND_ZST_FRAME_INVALID, zst_err
    except Exception as exc:
      return SKIP_KIND_READ_ERROR, str(exc)[:500]

  if stream_detail:
    lowered = stream_detail.lower()
    if "unexpected end" in lowered or "eof" in lowered or "tarerror" in lowered:
      return SKIP_KIND_TAR_TRUNCATED, stream_detail
  return SKIP_KIND_READ_ERROR, stream_detail or "archive stream unreadable"


def _cache_ingest_skipped_calendar_day(
  day_token: Any,
  kind: Any,
  detail: Any,
  sealed_path: str,
) -> None:
  """
  Internal helper to handle cache ingest skipped calendar day.
  
  Args:
    day_token (Any): Day token passed to this helper.
    kind (Any): Mode or kind token selecting a code path.
    detail (Any): Detail passed to this helper.
    sealed_path (str): String for sealed path.
  
  Returns:
    None
  
  Examples:
    >>> _cache_ingest_skipped_calendar_day(None, None, None, "x")
  """
  if day_token in _INGEST_SKIPPED_CALENDAR_DAYS:
    _INGEST_SKIPPED_CALENDAR_DAYS.move_to_end(day_token)
  _INGEST_SKIPPED_CALENDAR_DAYS[day_token] = (kind, detail, sealed_path)
  while len(_INGEST_SKIPPED_CALENDAR_DAYS) > _INGEST_SKIPPED_CALENDAR_DAYS_MAX:
    _INGEST_SKIPPED_CALENDAR_DAYS.popitem(last=False)


def _raise_if_ingest_day_skipped(
  keys: Any,
  sealed_path: str,
  client: Any,
) -> None:
  """
  Internal helper to handle raise if ingest day skipped.
  
  Args:
    keys (Any): Keys passed to this helper.
    sealed_path (str): String for sealed path.
    client (Any): Live handle (pool, client, or connection).
  
  Returns:
    None
  
  Raises:
    ArchiveDayIngestSkipError: Raised when ``_raise_if_ingest_day_skipped``
    hits a ``ArchiveDayIngestSkipError`` failure path.
  
  Examples:
    >>> _raise_if_ingest_day_skipped(None, "x", None)  # doctest: +SKIP
  """
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      ArchiveDayIngestSkipError,
      get_archive_day_ingest_skip,
  )

  cached = _INGEST_SKIPPED_CALENDAR_DAYS.get(keys.day_token)
  if cached is not None:
    kind, detail, cached_path = cached
    resolved = cached_path or sealed_path or _resolve_sealed_path_for_day_token(
        keys.day_token,
    )
    raise ArchiveDayIngestSkipError(
        keys.day_token, resolved, kind, detail,
    )
  skip = get_archive_day_ingest_skip(keys, client=client)
  if skip is not None:
    kind, detail = skip
    resolved = sealed_path or _resolve_sealed_path_for_day_token(keys.day_token)
    _cache_ingest_skipped_calendar_day(keys.day_token, kind, detail, resolved)
    raise ArchiveDayIngestSkipError(keys.day_token, resolved, kind, detail)


def mark_archive_day_ingest_skip_and_raise(
  sealed_path: str,
  keys: Any,
  client: Any,
  stream_error: Any | None = None,
) -> None:
  """
  Mark archive day ingest skip and raise.
  
  Args:
    sealed_path (str): String for sealed path.
    keys (Any): Keys passed to this helper.
    client (Any): Live handle (pool, client, or connection).
    stream_error (Any | None): One of ``Any``, ``None``.
  
  Returns:
    None
  
  Raises:
    ArchiveDayIngestSkipError: Raised when
    ``mark_archive_day_ingest_skip_and_raise`` hits a
    ``ArchiveDayIngestSkipError`` failure path.
    ArchiveMembersRedisUnavailableError: Raised when
    ``mark_archive_day_ingest_skip_and_raise`` hits a
    ``ArchiveMembersRedisUnavailableError`` failure path.
  
  Examples:
    >>> mark_archive_day_ingest_skip_and_raise("x", None, None, None)
  """
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      ArchiveDayIngestSkipError,
      ArchiveMembersRedisUnavailableError,
      _SELF_INGEST_TAR_HOT_REASONS,
      archive_append_inflight_for_day,
      ingest_tar_hot_for_day,
      ingest_tar_hot_reason_for_day,
      set_archive_day_ingest_skip,
  )

  kind, detail = classify_sealed_archive_stream_failure(sealed_path, stream_error)
  if kind == SKIP_KIND_READ_ERROR and (
      _is_fnctl_read_lock_timeout_error(stream_error)
      or _is_fnctl_read_lock_timeout_detail(detail)
  ):
    raise ArchiveMembersRedisUnavailableError(
        "transient fnctl read lock timeout during sealed populate day=%s path=%s"
        % (keys.day_token, sealed_path or ""),
    )
  if kind == SKIP_KIND_TAR_TRUNCATED and not (
      sealed_path and os.path.isfile(sealed_path)
  ):
    day_token = keys.day_token
    tar_path = ""
    if day_token and day_token != "unknown":
      daily_dir = cfg.get_daily_archive_dir_path()
      if daily_dir:
        tar_path = os.path.join(daily_dir, "%s.tar" % day_token)
    sealed_sibling = _resolve_sealed_path_for_day_token(day_token)
    # True append (or non-self hot) → transient Unavailable; preserve no day-skip.
    if day_token and archive_append_inflight_for_day(day_token):
      raise ArchiveMembersRedisUnavailableError(
          "transient tar populate EOF during hot/append activity day=%s"
          % day_token,
      )
    # Self-hot alone + sealed sibling → prefer sealed populate (not forever-transient).
    if (
        day_token
        and sealed_sibling
        and os.path.isfile(sealed_sibling)
    ):
      raise ArchiveMembersRedisUnavailableError(
          "tar populate EOF prefer sealed fallback day=%s" % day_token,
      )
    if day_token and ingest_tar_hot_for_day(day_token):
      hot_reason = ingest_tar_hot_reason_for_day(day_token)
      if hot_reason and hot_reason not in _SELF_INGEST_TAR_HOT_REASONS:
        raise ArchiveMembersRedisUnavailableError(
            "transient tar populate EOF during hot/append activity day=%s"
            % day_token,
        )
      # Self-hot only without sealed: fall through to readable / sticky skip.
    if tar_path and os.path.isfile(tar_path):
      try:
        if verify_tar_archive_readable(tar_path):
          raise ArchiveMembersRedisUnavailableError(
              "transient tar populate EOF while mutable tar readable day=%s"
              % day_token,
          )
      except TimeoutError:
        raise ArchiveMembersRedisUnavailableError(
            "transient tar populate EOF during fnctl contention day=%s"
            % day_token,
        ) from None
  resolved_sealed = sealed_path or _resolve_sealed_path_for_day_token(keys.day_token)
  set_archive_day_ingest_skip(client, keys, kind, detail)
  _cache_ingest_skipped_calendar_day(
      keys.day_token, kind, detail, resolved_sealed,
  )
  raise ArchiveDayIngestSkipError(
      keys.day_token, resolved_sealed, kind, detail,
  )


def _log_archive_day_ingest_skip_once(exc: Any) -> None:
  """
  Internal helper to log the archive day ingest skip once.
  
  Args:
    exc (Any): Exception instance being classified or logged.
  
  Returns:
    None
  
  Examples:
    >>> _log_archive_day_ingest_skip_once(None)  # doctest: +SKIP
  """
  if len(_LOGGED_ARCHIVE_DAY_INGEST_SKIP) >= _LOGGED_ARCHIVE_DAY_INGEST_SKIP_MAX:
    _LOGGED_ARCHIVE_DAY_INGEST_SKIP.clear()
  if exc.day_token in _LOGGED_ARCHIVE_DAY_INGEST_SKIP:
    return
  _LOGGED_ARCHIVE_DAY_INGEST_SKIP.add(exc.day_token)
  if exc.kind == SKIP_KIND_TAR_TRUNCATED:
    reason_phrase = (
        "tar_truncated_or_unreadable (zstd -t passed; tar stream: %s)"
        % exc.detail
    )
  elif exc.kind == SKIP_KIND_ZST_FRAME_INVALID:
    reason_phrase = "zst_frame_invalid (zstd -t failed: %s)" % exc.detail
  else:
    reason_phrase = "%s (%s)" % (exc.kind, exc.detail)
  log_print(
      "ERROR: daily sealed archive unusable for ingest lookup: sealed_path=%s day=%s "
      "reason=%s; skipping tar-append duplicate-check for all raw stats files on this "
      "day until the archive is repaired"
      % (exc.sealed_path, exc.day_token, reason_phrase),
      flush=True,
  )


def _log_ingest_sealed_lookup_issue(sealed_path: str, message: Any) -> None:
  """
  Internal helper to log the ingest sealed lookup issue.
  
  Args:
    sealed_path (str): String for sealed path.
    message (Any): Message passed to this helper.
  
  Returns:
    None
  
  Examples:
    >>> _log_ingest_sealed_lookup_issue("x", None)  # doctest: +SKIP
  """
  if len(_INGEST_SEALED_LOOKUP_WARNED) >= _INGEST_SEALED_LOOKUP_WARNED_MAX:
    _INGEST_SEALED_LOOKUP_WARNED.clear()
  if sealed_path in _INGEST_SEALED_LOOKUP_WARNED:
    return
  _INGEST_SEALED_LOOKUP_WARNED.add(sealed_path)
  log_print(message, flush=True)


def _member_match_via_redis_or_sealed_point(
  canonical: Any,
  cache_key: Any,
  keys: Any,
  sealed_path: str,
  member_name: Any,
  expected_size: int,
  *,
  client: Any,
) -> Any:
  """
  Internal helper to handle member match via redis or sealed point.
  
  Args:
    canonical (Any): Canonical passed to this helper.
    cache_key (Any): Cache key passed to this helper.
    keys (Any): Keys passed to this helper.
    sealed_path (str): String for sealed path.
    member_name (Any): Member name passed to this helper.
    expected_size (int): Integer value for expected size.
    client (Any): Live handle (pool, client, or connection).
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Raises:
    Exception: Raised when ``_member_match_via_redis_or_sealed_point`` hits a
    ``Exception`` failure path.
  
  Examples:
    >>> _member_match_via_redis_or_sealed_point(0)  # doctest: +SKIP
  """
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      ArchiveDayIngestSkipError,
      ArchiveMembersRedisUnavailableError,
      populate_degraded_is_set,
      request_archive_members_populate_and_wait,
      wait_for_member_match,
  )

  expected_size = int(expected_size)
  _raise_if_ingest_day_skipped(keys, sealed_path, client)

  from hpcperfstats.dbload.lib.sync_timedb_ingest_worker_diagnostics import (
      update_worker_substage,
  )

  update_worker_substage(
      "archive_member_lookup",
      lookup_mode="hget",
  )
  raw_size = client.hget(keys.hash_key, member_name)
  if raw_size is not None:
    size = int(raw_size)
    if size == expected_size:
      return True
    if size > expected_size:
      return False

  if client.get(keys.complete_key) == "1":
    return False

  if populate_degraded_is_set(keys, client=client):
    _raise_if_ingest_day_skipped(keys, sealed_path, client)
    update_worker_substage(
        "archive_member_lookup",
        lookup_mode="redis_wait",
    )
    try:
      members = request_archive_members_populate_and_wait(canonical)
      size = members.get(member_name)
      if size is None:
        return False
      return int(size) == expected_size
    except ArchiveDayIngestSkipError:
      raise
    except ArchiveMembersRedisUnavailableError:
      _raise_if_ingest_day_skipped(keys, sealed_path, client)
      if client.exists(keys.lock_key) or client.get(keys.complete_key) == "1":
        return wait_for_member_match(
            keys, member_name, expected_size, sealed_path=sealed_path,
            respect_ingest_deadline=False,
            canonical=canonical,
        )
      raise

  if client.exists(keys.lock_key):
    if populate_degraded_is_set(keys, client=client):
      _raise_if_ingest_day_skipped(keys, sealed_path, client)
    update_worker_substage(
        "archive_member_lookup",
        lookup_mode="redis_wait",
    )
    return wait_for_member_match(
        keys, member_name, expected_size, sealed_path=sealed_path,
        respect_ingest_deadline=False,
        canonical=canonical,
    )

  try:
    members = request_archive_members_populate_and_wait(canonical)
    return members.get(member_name) == expected_size
  except ArchiveDayIngestSkipError:
    raise
  except ArchiveMembersRedisUnavailableError:
    _raise_if_ingest_day_skipped(keys, sealed_path, client)
    if client.exists(keys.lock_key) or client.get(keys.complete_key) == "1":
      return wait_for_member_match(
          keys, member_name, expected_size, sealed_path=sealed_path,
          respect_ingest_deadline=False,
          canonical=canonical,
      )


def _stream_compressed_archive_members(
  compressed_path: str,
  on_member: Any | None = None,
  *,
  apply_priority_wrap: bool = True,
) -> Any:
  """
  Stream file members from a sealed archive.
  
  ``on_member(name, size)`` is invoked for each file member when provided.
  Returns ``(readable, members_dict, saw_duplicate_names, stream_error)``.
  
  Args:
    compressed_path (str): String for compressed path.
    on_member (Any | None): One of ``Any``, ``None``.
    apply_priority_wrap (bool): Boolean flag for apply priority wrap.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Raises:
    Exception: Raised when ``_stream_compressed_archive_members`` hits a
    ``Exception`` failure path.
  
  Examples:
    >>> _stream_compressed_archive_members("x", None, True)  # doctest: +SKIP
  """
  if detect_compressed_format(compressed_path) is None or not os.path.isfile(
      compressed_path,
  ):
    return False, {}, False, None
  try:
    with _archive_file_read_lock_wait(compressed_path):
      with _open_tarfile_for_read(
          compressed_path,
          zstd_thread_count_for_wrap(apply_priority_wrap),
          apply_priority_wrap=apply_priority_wrap,
      ) as tf:
        by_name = defaultdict(list)
        seen_names = set()
        saw_duplicates = False
        for m in _iter_tar_members(tf):
          if not m.isfile():
            continue
          if m.name in seen_names:
            saw_duplicates = True
          seen_names.add(m.name)
          by_name[m.name].append(m.size)
          if on_member is not None:
            on_member(m.name, m.size)
        members = {name: max(sizes) for name, sizes in by_name.items()}
        return True, members, saw_duplicates, None
  except _MemberStreamEarlyExit:
    raise
  except Exception as exc:
    from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
        ArchiveMembersRedisUnavailableError,
    )
    if isinstance(exc, ArchiveMembersRedisUnavailableError):
      raise
    log_print(
        "WARNING: sealed archive member stream failed for %s: %s"
        % (compressed_path, exc),
        flush=True,
    )
    return False, {}, False, exc


def _scan_compressed_archive_members_and_readable(
  compressed_path: str,
  *,
  apply_priority_wrap: bool = True,
) -> Any:
  """
  Return ``(readable, members)`` from one streamed zstd/gzip pass.
  
  Args:
    compressed_path (str): String for compressed path.
    apply_priority_wrap (bool): Boolean flag for apply priority wrap.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _scan_compressed_archive_members_and_readable("x", True)
  """
  readable, members, _duplicates, _stream_error = _stream_compressed_archive_members(
      compressed_path,
      apply_priority_wrap=apply_priority_wrap,
  )
  return readable, members


def _scan_gzip_archive_members_and_readable(gz_path: str) -> Any:
  """
  Return ``(readable, members)`` from one streamed gzip pass.
  
  Args:
    gz_path (str): String for gz path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _scan_gzip_archive_members_and_readable("x")  # doctest: +SKIP
  """
  return _scan_compressed_archive_members_and_readable(gz_path)


def _sealed_archive_members_via_redis_or_scan(
  sealed_path: str,
  *,
  apply_priority_wrap: bool = False,
) -> Any:
  """
  Return ``(readable, members)`` for sealed-side raw-removal / validation reads.
  
  When Redis L2 is enabled, use the same single-flight populate path as ingest
  prewarm and duplicate-check (at most one ``zstd -d -c`` per calendar day).
  
  Args:
    sealed_path (str): String for sealed path.
    apply_priority_wrap (bool): Boolean flag for apply priority wrap.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Raises:
    Exception: Raised when ``_sealed_archive_members_via_redis_or_scan`` hits
    a ``Exception`` failure path.
  
  Examples:
    >>> _sealed_archive_members_via_redis_or_scan("x", True)  # doctest: +SKIP
  """
  sealed_path = os.path.normpath(str(sealed_path or ""))
  if not sealed_path or not os.path.isfile(sealed_path):
    return False, {}
  try:
    from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
        ArchiveDayIngestSkipError,
        archive_members_redis_enabled,
    )
    if archive_members_redis_enabled():
      try:
        members = get_existing_archive_members_for_daily_archive(sealed_path)
      except ArchiveDayIngestSkipError:
        return False, {}
      if members is None:
        return False, {}
      return True, dict(members)
  except Exception:
    raise
  readable, members = _scan_compressed_archive_members_and_readable(
      sealed_path,
      apply_priority_wrap=apply_priority_wrap,
  )
  return readable, dict(members or {})


def validate_sealed_daily_archive_for_raw_removal(
  archive_compressed_path: str,
  log_fn: Any = log_print,
  *,
  validation_cache: Any | None = None,
  allow_auto_seal: bool = True,
) -> Any:
  """
  Validate uncompressed tar (if present) then sealed ``.tar.zst`` (or legacy.
  
    ``.tar.gz``).
  
  Order: (1) readable ``YYYY-MM-DD.tar`` and member sizes if it exists;
  (2) readable sealed archive and member sizes from compressed file only;
  (3) if both exist, dicts must be equal. Returns ``(True, members)`` for use
  with ``get_verified_files_to_remove``, or ``(False, None)``.
  
  When ``allow_auto_seal`` is false (janitor raw-remove ticks, which must not
    run a
  second seal in the same pass), a missing sealed archive is not created here;
    the
  day is skipped instead.
  
  Args:
    archive_compressed_path (str): String for archive compressed path.
    log_fn (Any): Callable invoked by this helper.
    validation_cache (Any | None): One of ``Any``, ``None``.
    allow_auto_seal (bool): Boolean flag for allow auto seal.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> validate_sealed_daily_archive_for_raw_removal("x", None, None, True)
  """
  fmt = detect_compressed_format(archive_compressed_path)
  if fmt not in ("zst", "gz"):
    if log_fn:
      log_fn(
          "Skipping removal validation: not a daily compressed archive: %s"
          % archive_compressed_path,
          flush=True,
      )
    return False, None
  sealed_path = archive_compressed_path
  tar_path = daily_tar_path_from_compressed(sealed_path)
  cache_key = None
  if validation_cache is not None:
    cache_key = _build_archive_validation_cache_key(sealed_path)
    cached = validation_cache.get(cache_key)
    if cached is not None:
      validation_cache["hits"] = int(validation_cache.get("hits", 0)) + 1
      cached_members = dict(cached["members"]) if cached["members"] is not None else None
      return bool(cached["ok"]), cached_members
    validation_cache["misses"] = int(validation_cache.get("misses", 0)) + 1
  members_tar = None
  if os.path.isfile(tar_path):
    if not restore_tar_from_sealed_if_unreadable(
        tar_path,
        get_archive_zstd_thread_count(),
        log_fn=log_fn,
    ):
      if log_fn:
        log_fn(
            "Skipping removal: uncompressed tar failed check: %s" % tar_path,
            flush=True,
        )
      return False, None
    members_tar = get_existing_archive_members(tar_path)
    if not members_tar:
      if log_fn:
        log_fn(
            "Skipping removal: uncompressed tar has no file members: %s" % tar_path,
            flush=True,
        )
      return False, None

  zst_path, _gz_path = compressed_sibling_paths(tar_path)
  if not os.path.isfile(sealed_path):
    if members_tar is None:
      if log_fn:
        log_fn(
            "Skipping removal: sealed archive missing: %s" % sealed_path,
            flush=True,
        )
      return False, None
    if not allow_auto_seal:
      if log_fn:
        log_fn(
            "Skipping removal: sealed archive missing and auto-seal disabled "
            "(janitor seal already ran this pass): %s" % tar_path,
            flush=True,
        )
      return False, None
    if log_fn:
      log_fn(
          "Sealed archive missing; creating from valid uncompressed tar: %s"
          % tar_path,
          flush=True,
      )
    try:
      atomic_seal_tar_to_zst(
          tar_path,
          zst_path,
          num_threads=get_archive_zstd_thread_count(),
          compress_level=cfg.get_archive_zstd_level(),
          keep_uncompressed_tar=cfg.get_archive_keep_uncompressed_tar(),
          log_fn=log_fn,
      )
      sealed_path = zst_path
      sealed_readable, members_sealed = _sealed_archive_members_via_redis_or_scan(
          sealed_path,
      )
      if not sealed_readable or members_sealed != members_tar:
        if log_fn:
          log_fn(
              "Skipping removal: auto-seal member mismatch for %s "
              "(tar count=%s sealed count=%s)"
              % (sealed_path, len(members_tar), len(members_sealed)),
              flush=True,
          )
        return False, None
    except (OSError, subprocess.CalledProcessError) as exc:
      if log_fn:
        log_fn(
            "Skipping removal: failed to seal tar into zstd for %s (%s)"
            % (tar_path, exc),
            flush=True,
        )
      return False, None

  sealed_readable, members_sealed = _sealed_archive_members_via_redis_or_scan(
      sealed_path,
  )
  if not sealed_readable:
    if log_fn:
      log_fn(
          "Skipping removal: sealed archive failed integrity check: %s"
          % sealed_path,
          flush=True,
      )
    result = (False, None)
    if validation_cache is not None:
      validation_cache[cache_key] = {"ok": result[0], "members": result[1]}
    return result

  if members_tar is not None:
    if members_tar != members_sealed:
      if log_fn:
        log_fn(
            "Skipping removal: tar vs sealed member mismatch for %s "
            "(uncompressed count=%s sealed count=%s)"
            % (sealed_path, len(members_tar), len(members_sealed)),
            flush=True,
        )
      result = (False, None)
      if validation_cache is not None:
        validation_cache[cache_key] = {"ok": result[0], "members": result[1]}
      return result
    result = (True, members_tar)
    if validation_cache is not None:
      validation_cache[cache_key] = {"ok": result[0], "members": dict(result[1])}
    return result

  result = (True, members_sealed)
  if validation_cache is not None:
    validation_cache[cache_key] = {"ok": result[0], "members": dict(result[1])}
  return result


def ensure_daily_tar_restored_for_append(
  tar_path: str,
  zstd_threads: Any,
  *,
  wait_for_other_owner: bool = True,
) -> Any:
  """
  Return True when sibling ``.tar`` exists or was restored from sealed backup.
  
  When no sealed ``.tar.zst`` / ``.tar.gz`` sibling exists, returns True so the
  caller may bootstrap a fresh ``.tar``. Returns False when a sealed sibling
  remains but restore did not produce ``tar_path``.
  
  ``wait_for_other_owner=False`` (day-close pre_seal) returns False immediately
  when another thread holds the exclusive restore lease, so the worker can
  defer without blocking a day-close slot for the full decompress.
  
  Args:
    tar_path (str): String for tar path.
    zstd_threads (Any): Zstd threads passed to this helper.
    wait_for_other_owner (bool): Boolean flag for wait for other owner.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> ensure_daily_tar_restored_for_append("x", None, True)  # doctest: +SKIP
  """
  if os.path.isfile(tar_path):
    return True
  zst_path, gz_path = compressed_sibling_paths(tar_path)
  remove_compressed = _decompress_should_unlink_compressed(tar_path)
  sealed = zst_path if os.path.isfile(zst_path) else (
      gz_path if os.path.isfile(gz_path) else ""
  )
  if sealed:
    log_print(
        "INFO: archive decompress restore begin tar=%s from=%s "
        "remove_compressed=%s"
        % (tar_path, sealed, remove_compressed),
        flush=True,
    )
  if os.path.isfile(zst_path):
    if decompress_compressed_to_tar(
        zst_path,
        tar_path,
        zstd_threads,
        remove_compressed=remove_compressed,
        wait_for_other_owner=wait_for_other_owner,
    ):
      log_print(
          "INFO: archive decompress restore tar=%s from=%s remove_compressed=%s"
          % (tar_path, zst_path, remove_compressed),
          flush=True,
      )
      return True
  if os.path.isfile(gz_path):
    if decompress_compressed_to_tar(
        gz_path,
        tar_path,
        zstd_threads,
        remove_compressed=remove_compressed,
        wait_for_other_owner=wait_for_other_owner,
    ):
      log_print(
          "INFO: archive decompress restore tar=%s from=%s remove_compressed=%s"
          % (tar_path, gz_path, remove_compressed),
          flush=True,
      )
      return True
  if os.path.isfile(zst_path) or os.path.isfile(gz_path):
    return False
  return True


def replace_corrupt_tar_from_compressed_backup(
  tar_path: str,
  zst_path: str,
  gz_path: str,
  zstd_threads: Any,
) -> Any:
  """
  Remove corrupt ``tar_path``, then restore from ``.tar.zst`` or legacy ``.gz``.
  
  Returns True if the filesystem is in a consistent state for the caller to
  append: either ``tar_path`` exists (restored from backup) or both backups
  and tar are absent. Returns False only if restore was attempted but
  ``tar_path`` is still missing afterward.
  
  Args:
    tar_path (str): String for tar path.
    zst_path (str): String for zst path.
    gz_path (str): String for gz path.
    zstd_threads (Any): Zstd threads passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> replace_corrupt_tar_from_compressed_backup("x", "x", "x", None)
  """
  try:
    with file_write_lock(tar_path):
      if os.path.isfile(tar_path):
        os.remove(tar_path)
      remove_compressed = _decompress_should_unlink_compressed(tar_path)
      if os.path.isfile(zst_path):
        if decompress_compressed_to_tar(
            zst_path,
            tar_path,
            zstd_threads,
            remove_compressed=remove_compressed,
            restore_reason="corrupt_tar",
            restore_caller="replace_corrupt_tar_from_compressed_backup",
        ):
          return True
      if os.path.isfile(gz_path):
        if decompress_compressed_to_tar(
            gz_path,
            tar_path,
            zstd_threads,
            remove_compressed=remove_compressed,
            restore_reason="corrupt_tar",
            restore_caller="replace_corrupt_tar_from_compressed_backup",
        ):
          return True
      if os.path.isfile(tar_path):
        return True
      if os.path.isfile(zst_path) or os.path.isfile(gz_path):
        return False
      return True
  except OSError:
    return False


def restore_tar_from_sealed_if_unreadable(
  tar_path: str,
  zstd_threads: Any,
  *,
  log_fn: Any = log_print,
) -> Any:
  """
  Verify ``tar_path`` readable; restore from sealed sibling when corrupt.
  
  Returns True when ``tar_path`` is readable after (optional) restore.
  Returns False when unreadable and restore failed or tar is absent.
  
  Args:
    tar_path (str): String for tar path.
    zstd_threads (Any): Zstd threads passed to this helper.
    log_fn (Any): Callable invoked by this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> restore_tar_from_sealed_if_unreadable("x", None, None)  # doctest: +SKIP
  """
  if not tar_path or not os.path.isfile(tar_path):
    return False
  if verify_tar_archive_readable(tar_path):
    return True
  zst_path, gz_path = compressed_sibling_paths(tar_path)
  if replace_corrupt_tar_from_compressed_backup(
      tar_path,
      zst_path,
      gz_path,
      zstd_threads,
  ) and verify_tar_archive_readable(tar_path):
    if log_fn:
      log_fn(
          "Restored unreadable tar from sealed backup: %s" % tar_path,
          flush=True,
      )
    return True
  if log_fn:
    log_fn(
        "Tar unreadable and sealed restore failed: %s" % tar_path,
        flush=True,
    )
  return False


def iter_archive_file_member_infos(
  tar_path: str,
  *,
  thread_count: Any | None = None,
  apply_priority_wrap: bool = True,
) -> Iterator[Any]:
  """
  Yield tarfile member info for file members (shared scan surface).
  
  Args:
    tar_path (str): String for tar path.
    thread_count (Any | None): One of ``Any``, ``None``.
    apply_priority_wrap (bool): Boolean flag for apply priority wrap.
  
  Yields:
    Iterator[Any]: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> iter_archive_file_member_infos("x", None, True)  # doctest: +SKIP
  """
  if thread_count is None:
    thread_count = get_archive_zstd_thread_count()
  open_path = resolve_preferred_archive_path_for_read(tar_path)
  with file_read_lock_wait(open_path):
    with _open_tarfile_for_read(
        open_path,
        thread_count,
        apply_priority_wrap=apply_priority_wrap,
    ) as archive_tar:
      try:
        member_infos = iter(archive_tar)
      except TypeError:
        member_infos = archive_tar.getmembers()
      for member_info in member_infos:
        if member_info.isfile():
          yield member_info


def _read_tar_file_member_sizes_unlocked(tar_path: str) -> Any:
  """
  Read file member name -> max size from ``tar_path`` without a read lock.
  
  Caller must hold ``file_write_lock(tar_path)`` or otherwise exclude writers.
  
  Args:
    tar_path (str): String for tar path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _read_tar_file_member_sizes_unlocked("x")  # doctest: +SKIP
  """
  if not os.path.isfile(tar_path):
    return {}
  try:
    with _open_tarfile_for_read(tar_path, get_archive_zstd_thread_count()) as tf:
      by_name = defaultdict(list)
      for m in _iter_tar_members(tf):
        if m.isfile():
          by_name[m.name].append(m.size)
      return {name: max(sizes) for name, sizes in by_name.items()}
  except Exception:
    return {}


def get_existing_archive_members(tar_path: str) -> Any:
  """
  Read tar at tar_path and return dict of member name -> size for **file**.
  
    members.
  
  If the same path appears multiple times (e.g. repeated append passes), the
  reported size is the **largest** among those entries so verification matches
  the preferred retained copy after deduplication.
  
  Args:
    tar_path (str): String for tar path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> get_existing_archive_members("x")  # doctest: +SKIP
  """
  if not os.path.exists(tar_path):
    return {}
  zst_path, gz_path = compressed_sibling_paths(tar_path)
  canonical = None
  if os.path.isfile(zst_path):
    canonical = normalize_daily_compressed_path(zst_path)
  elif os.path.isfile(gz_path):
    canonical = normalize_daily_compressed_path(gz_path)
  if canonical is not None:
    cached = _lookup_daily_archive_members_cache(canonical)
    if cached is not None:
      return cached
  members = {}
  try:
    with file_read_lock_wait(tar_path):
      members = _read_tar_file_member_sizes_unlocked(tar_path)
  except Exception:
    return {}
  finally:
    _remove_read_lock_sidecar(tar_path)
  if canonical is not None:
    _store_daily_archive_members_cache(canonical, members)
  return members


_POPULATE_SOURCE_BY_CANONICAL = {}


def consume_archive_members_populate_source(
  canonical: Any,
  default: Any | None = None,
) -> Any:
  """
  Pop last populate source token for prewarm summary (``tar_populated`` /.
  
    ``sealed_populated``).
  
  Default is ``None`` so callers can distinguish a recorded populate from a
    silent
  miss (do not invent ``prewarmed`` when Redis stayed empty).
  
  Args:
    canonical (Any): Canonical passed to this helper.
    default (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> consume_archive_members_populate_source(None, None)  # doctest: +SKIP
  """
  return _POPULATE_SOURCE_BY_CANONICAL.pop(
      normalize_daily_compressed_path(canonical),
      default,
  )


def _record_archive_members_populate_source(
  canonical: Any,
  source: Any,
) -> None:
  """
  Internal helper to handle record archive members populate source.
  
  Args:
    canonical (Any): Canonical passed to this helper.
    source (Any): Source passed to this helper.
  
  Returns:
    None
  
  Examples:
    >>> _record_archive_members_populate_source(None, None)  # doctest: +SKIP
  """
  _POPULATE_SOURCE_BY_CANONICAL[normalize_daily_compressed_path(canonical)] = source


def _ensure_populate_scan_allowed() -> None:
  """
  Internal helper to ensure the populate scan allowed.
  
  Returns:
    None
  
  Raises:
    ArchiveMembersRedisUnavailableError: Raised when
    ``_ensure_populate_scan_allowed`` hits a
    ``ArchiveMembersRedisUnavailableError`` failure path.
  
  Examples:
    >>> _ensure_populate_scan_allowed()  # doctest: +SKIP
  """
  from hpcperfstats.dbload.lib.sync_timedb_ingest_worker_diagnostics import (
      get_worker_pool_kind,
      may_run_archive_members_populate_scan,
  )
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      ArchiveMembersRedisUnavailableError,
  )

  if not may_run_archive_members_populate_scan():
    raise ArchiveMembersRedisUnavailableError(
        "archive members populate scan forbidden for pool_kind=%s"
        % get_worker_pool_kind(),
    )


def _clear_stale_day_ingest_skip_if_tar_repaired(
  client: Any,
  keys: Any,
  tar_path: str,
  zst_path: str,
  gz_path: str,
  sealed_path: str,
) -> None:
  """
  Fix D: drop sticky skip when sealed is gone and mutable tar is readable.
  
  Args:
    client (Any): Live handle (pool, client, or connection).
    keys (Any): Keys passed to this helper.
    tar_path (str): String for tar path.
    zst_path (str): String for zst path.
    gz_path (str): String for gz path.
    sealed_path (str): String for sealed path.
  
  Returns:
    None
  
  Examples:
    >>> _clear_stale_day_ingest_skip_if_tar_repaired(0)  # doctest: +SKIP
  """
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      clear_archive_day_ingest_skip,
      get_archive_day_ingest_skip,
      populate_degraded_is_set,
  )

  if client is None or keys.day_token == "unknown":
    return
  skip = get_archive_day_ingest_skip(keys, client=client)
  if skip is None:
    return
  if sealed_path and os.path.isfile(sealed_path):
    return
  if not os.path.isfile(tar_path):
    return
  dirty = is_daily_tar_sealed_dirty(tar_path, zst_path, gz_path)
  if not dirty and os.path.isfile(sealed_path or ""):
    return
  try:
    if not verify_tar_archive_readable(tar_path):
      return
  except TimeoutError:
    return
  clear_archive_day_ingest_skip(client, keys)
  _INGEST_SKIPPED_CALENDAR_DAYS.pop(keys.day_token, None)
  _LOGGED_ARCHIVE_DAY_INGEST_SKIP.discard(keys.day_token)
  if populate_degraded_is_set(keys, client=client):
    client.delete(keys.degraded_key)
  log_print(
      "INFO: cleared stale archive_day_ingest_skip day=%s tar=%s "
      "(sealed missing/dirty; tar readable)"
      % (keys.day_token, tar_path),
      flush=True,
  )


def _build_populate_source_decision(
  day_token: Any,
  tar_path: str,
  zst_path: str,
  gz_path: str,
  sealed_path: str,
) -> Any:
  """
  Internal helper to build the populate source decision.
  
  Args:
    day_token (Any): Day token passed to this helper.
    tar_path (str): String for tar path.
    zst_path (str): String for zst path.
    gz_path (str): String for gz path.
    sealed_path (str): String for sealed path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _build_populate_source_decision(None, "x", "x", "x", "x")
  """
  return {
      "day_token": day_token,
      "tar_path": tar_path,
      "zst_path": zst_path,
      "gz_path": gz_path,
      "sealed_path": sealed_path or "",
  }


def _populate_redis_members_from_sealed_scan(
  sealed_path: str,
  cache_key: Any,
  tar_path: Any | None = None,
) -> Any:
  """
  Internal helper to populate the redis members from sealed scan.
  
  Args:
    sealed_path (str): String for sealed path.
    cache_key (Any): Cache key passed to this helper.
    tar_path (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Raises:
    Exception: Raised when ``_populate_redis_members_from_sealed_scan`` hits a
    ``Exception`` failure path.
  
  Examples:
    >>> _populate_redis_members_from_sealed_scan("x", None, None)
  """
  _ensure_populate_scan_allowed()
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      build_archive_members_redis_keys,
      populate_archive_members_redis,
  )

  keys = build_archive_members_redis_keys(cache_key)
  canonical = cache_key[0] if cache_key else sealed_path
  if tar_path is None:
    tar_path = daily_tar_path_from_compressed(canonical)
  zst_path, gz_path = compressed_sibling_paths(tar_path)

  def _scan_fn(on_member: Any) -> Any:
    """
    Internal helper to handle scan function.
    
    Args:
      on_member (Any): On member passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _scan_fn(None)  # doctest: +SKIP
    """
    readable, _members, saw_duplicates, stream_error = (
        _stream_compressed_archive_members(
            sealed_path,
            on_member,
            apply_priority_wrap=False,
        )
    )
    return readable, saw_duplicates, stream_error

  members = None
  try:
    members = populate_archive_members_redis(
        keys,
        _scan_fn,
        sealed_path=sealed_path,
        source_decision=_build_populate_source_decision(
            keys.day_token, tar_path, zst_path, gz_path, sealed_path,
        ),
    )
  except Exception as exc:
    from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
        ArchiveDayIngestSkipError,
        clear_archive_day_ingest_skip,
        get_archive_members_redis_client,
    )
    if not isinstance(exc, ArchiveDayIngestSkipError):
      raise
    if tar_path and os.path.isfile(tar_path):
      try:
        tar_ok = verify_tar_archive_readable(tar_path)
      except TimeoutError:
        raise
      if tar_ok:
        log_print(
            "INFO: populate tar fallback after sealed failure day=%s "
            "sealed=%s tar=%s reason=%s"
            % (
                keys.day_token,
                sealed_path,
                tar_path,
                exc.detail,
            ),
            flush=True,
        )
        client = get_archive_members_redis_client(required=False)
        if client is not None:
          clear_archive_day_ingest_skip(client, keys)
          client.delete(keys.degraded_key)
        _INGEST_SKIPPED_CALENDAR_DAYS.pop(keys.day_token, None)
        _LOGGED_ARCHIVE_DAY_INGEST_SKIP.discard(keys.day_token)
        return _populate_redis_members_from_tar_scan(tar_path, cache_key)
    raise
  if members is not None:
    _record_archive_members_populate_source(canonical, "sealed_populated")
    day_token = keys.day_token if keys.day_token != "unknown" else ""
    if day_token:
      log_print(
          "INFO: populate_source=sealed day=%s path=%s"
          % (day_token, sealed_path),
          flush=True,
      )
  return members


def _populate_redis_members_from_tar_scan(tar_path: str, cache_key: Any) -> Any:
  """
  Internal helper to populate the redis members from tar scan.
  
  Args:
    tar_path (str): String for tar path.
    cache_key (Any): Cache key passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Raises:
    Exception: Raised when ``_populate_redis_members_from_tar_scan`` hits a
    ``Exception`` failure path.
  
  Examples:
    >>> _populate_redis_members_from_tar_scan("x", None)  # doctest: +SKIP
  """
  _ensure_populate_scan_allowed()
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      build_archive_members_redis_keys,
      populate_archive_members_redis,
  )

  keys = build_archive_members_redis_keys(cache_key)
  canonical = cache_key[0] if cache_key else tar_path
  zst_path, gz_path = compressed_sibling_paths(tar_path)
  sealed_path = _resolve_sealed_daily_archive_path(canonical)

  def _scan_fn(on_member: Any) -> Any:
    """
    Internal helper to handle scan function.
    
    Args:
      on_member (Any): On member passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Raises:
      ArchiveMembersRedisUnavailableError: Raised when ``_scan_fn`` hits a
      ``ArchiveMembersRedisUnavailableError`` failure path.
    
    Examples:
      >>> _scan_fn(None)  # doctest: +SKIP
    """
    if not os.path.isfile(tar_path):
      return False, False, None
    saw_duplicates = False
    seen_names = set()
    try:
      with _populate_tar_file_read_lock_wait(tar_path):
        with _open_tarfile_for_read(tar_path, get_archive_zstd_thread_count()) as tf:
          for member in _iter_tar_members(tf):
            if not member.isfile():
              continue
            if member.name in seen_names:
              saw_duplicates = True
            seen_names.add(member.name)
            on_member(member.name, member.size)
    except Exception as exc:
      from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
          ArchiveMembersRedisUnavailableError,
      )
      if _is_fnctl_read_lock_timeout_error(exc):
        raise ArchiveMembersRedisUnavailableError(
            "transient fnctl read lock timeout during tar populate path=%s"
            % tar_path,
        ) from exc
      return False, False, exc
    finally:
      _remove_read_lock_sidecar(tar_path)
    return True, saw_duplicates, None

  try:
    members = populate_archive_members_redis(
        keys,
        _scan_fn,
        sealed_path=None,
        source_decision=_build_populate_source_decision(
            keys.day_token, tar_path, zst_path, gz_path, sealed_path,
        ),
        scanning_mutable_tar=True,
    )
  except Exception as exc:
    from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
        ArchiveMembersRedisUnavailableError,
    )
    if not isinstance(exc, ArchiveMembersRedisUnavailableError):
      raise
    if "prefer sealed fallback" not in str(exc):
      raise
    if not (sealed_path and os.path.isfile(sealed_path)):
      raise
    log_print(
        "INFO: populate sealed fallback after dirty-tar EOF day=%s "
        "tar=%s sealed=%s"
        % (keys.day_token, tar_path, sealed_path),
        flush=True,
    )
    return _populate_redis_members_from_sealed_scan(
        sealed_path, cache_key, tar_path=tar_path,
    )
  if members is not None:
    _record_archive_members_populate_source(canonical, "tar_populated")
    day_token = keys.day_token if keys.day_token != "unknown" else ""
    if day_token:
      log_print(
          "INFO: populate_source=tar day=%s path=%s"
          % (day_token, tar_path),
          flush=True,
      )
  return members


def execute_archive_members_populate_for_canonical(canonical: Any) -> Any:
  """
  Run Redis single-flight populate for one daily archive (populate-pool only).
  
  Args:
    canonical (Any): Canonical passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Raises:
    ArchiveMembersRedisUnavailableError: Raised when
    ``execute_archive_members_populate_for_canonical`` hits a
    ``ArchiveMembersRedisUnavailableError`` failure path.
  
  Examples:
    >>> execute_archive_members_populate_for_canonical(None)  # doctest: +SKIP
  """
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      ArchiveMembersRedisUnavailableError,
      archive_members_redis_enabled,
      build_archive_members_redis_keys,
      get_archive_day_ingest_skip,
      populate_degraded_is_set,
      redis_lookup_full_members,
  )
  from hpcperfstats.dbload.lib.sync_timedb_ingest_worker_diagnostics import (
      get_worker_pool_kind,
      reset_worker_pool_kind,
      set_worker_pool_kind,
  )

  canonical = normalize_daily_compressed_path(canonical)
  if not archive_members_redis_enabled():
    raise ArchiveMembersRedisUnavailableError(
        "execute_archive_members_populate_for_canonical requires Redis L2",
    )
  kind = get_worker_pool_kind()
  if kind in ("ingest-pool", "archive-pool"):
    raise ArchiveMembersRedisUnavailableError(
        "execute_archive_members_populate_for_canonical forbidden on %s "
        "(use request_archive_members_populate_and_wait)"
        % kind,
    )
  token = None
  if kind != "populate-pool":
    token = set_worker_pool_kind("populate-pool")
  try:
    cache_key = _daily_archive_members_cache_key(canonical)
    keys = build_archive_members_redis_keys(cache_key)
    members = redis_lookup_full_members(keys)
    if members is not None:
      _store_daily_archive_members_cache(canonical, members)
      return dict(members)
    tar_path = daily_tar_path_from_compressed(canonical)
    sealed_path = _resolve_sealed_daily_archive_path(canonical)
    client = None
    try:
      from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
          get_archive_members_redis_client,
      )
      client = get_archive_members_redis_client(required=True)
    except Exception:
      client = None
    if client is not None and populate_degraded_is_set(keys, client=client):
      if get_archive_day_ingest_skip(keys, client=client) is not None:
        return {}
      from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
          clear_stale_incomplete_archive_members_redis,
      )
      clear_stale_incomplete_archive_members_redis(keys, client=client)
    if sealed_path is None and not os.path.isfile(tar_path):
      _store_daily_archive_members_cache(canonical, {})
      return {}
    zst_path, gz_path = compressed_sibling_paths(tar_path)
    _clear_stale_day_ingest_skip_if_tar_repaired(
        client, keys, tar_path, zst_path, gz_path, sealed_path,
    )
    use_tar, _reason = _populate_should_use_tar_scan(
        tar_path, zst_path, gz_path, sealed_path,
    )
    if use_tar:
      members = _populate_redis_members_from_tar_scan(tar_path, cache_key)
    else:
      if sealed_path is None:
        raise ArchiveMembersRedisUnavailableError(
            "no sealed archive for populate canonical=%s" % canonical,
        )
      members = _populate_redis_members_from_sealed_scan(
          sealed_path, cache_key, tar_path=tar_path,
      )
    if members is not None:
      _store_daily_archive_members_cache(canonical, members)
      return dict(members)
    return {}
  finally:
    if token is not None:
      reset_worker_pool_kind(token)


def get_existing_archive_members_for_daily_archive(
  archive_compressed_path: str,
) -> Any:
  """
  File member sizes for a daily ``.tar.zst`` or legacy ``.tar.gz``.
  
  When Redis L2 is enabled, prefer a warm Redis member map before scanning a
  sibling ``.tar`` (ingest duplicate-check must not N× parallel tar reads).
  When Redis is disabled or cold and ``.tar`` exists, scan the mutable tar.
  
  Args:
    archive_compressed_path (str): String for archive compressed path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Raises:
    Exception: Raised when ``get_existing_archive_members_for_daily_archive``
    hits a ``Exception`` failure path.
  
  Examples:
    >>> get_existing_archive_members_for_daily_archive("x")  # doctest: +SKIP
  """
  canonical = normalize_daily_compressed_path(archive_compressed_path)
  cached = _lookup_daily_archive_members_cache(canonical)
  if cached is not None:
    return cached
  if not daily_archive_populate_source_exists(canonical):
    _store_daily_archive_members_cache(canonical, {})
    return {}
  try:
    from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
        archive_members_redis_enabled,
        request_archive_members_populate_and_wait,
    )
    from hpcperfstats.dbload.lib.sync_timedb_ingest_worker_diagnostics import (
        may_run_archive_members_populate_scan,
    )
    if archive_members_redis_enabled():
      if may_run_archive_members_populate_scan():
        return execute_archive_members_populate_for_canonical(canonical)
      return request_archive_members_populate_and_wait(canonical)
  except Exception:
    raise
  return _get_existing_archive_members_for_daily_archive_local_scan(
      archive_compressed_path,
      canonical,
  )


def _get_existing_archive_members_for_daily_archive_local_scan(
  archive_compressed_path: str,
  canonical: Any,
) -> Any:
  """
  Local tar/sealed scan when Redis L2 is disabled.
  
  Args:
    archive_compressed_path (str): String for archive compressed path.
    canonical (Any): Canonical passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Raises:
    ArchiveMembersRedisUnavailableError: Raised when
    ``_get_existing_archive_members_for_daily_archive_local_scan`` hits a
    ``ArchiveMembersRedisUnavailableError`` failure path.
  
  Examples:
    >>> _get_existing_archive_members_for_daily_archive_local_scan("x", None)
  """
  cache_key = _daily_archive_members_cache_key(canonical)
  tar_path = daily_tar_path_from_compressed(canonical)
  sealed_path = _resolve_sealed_daily_archive_path(archive_compressed_path)
  if os.path.isfile(tar_path):
    members = get_existing_archive_members(tar_path)
    _store_daily_archive_members_cache(canonical, members)
    try:
      from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
          archive_members_redis_enabled,
          build_archive_members_redis_keys,
          store_complete_members_in_redis,
      )
      if archive_members_redis_enabled():
        store_complete_members_in_redis(
            build_archive_members_redis_keys(cache_key),
            members,
            saw_duplicates=tar_has_duplicate_file_members(tar_path),
        )
    except Exception as exc:
      log_print(
          "WARNING: store_complete_members_in_redis failed for %s: %s"
          % (cache_key, exc),
          flush=True,
      )
    return members
  if sealed_path is None:
    return {}
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      ArchiveMembersRedisUnavailableError,
      archive_members_redis_enabled,
  )
  if archive_members_redis_enabled():
    raise ArchiveMembersRedisUnavailableError(
        "archive members Redis enabled but lookup did not return members for %s"
        % canonical,
    )
  readable, members = _scan_compressed_archive_members_and_readable(
      sealed_path,
      apply_priority_wrap=False,
  )
  if not readable:
    return {}
  _store_daily_archive_members_cache(canonical, members)
  return dict(members)


def _record_validated_day_hint(
  validated_days_out: Any,
  gz_path: str,
  members: Any,
) -> None:
  """
  Internal helper to handle record validated day hint.
  
  Args:
    validated_days_out (Any): Validated days out passed to this helper.
    gz_path (str): String for gz path.
    members (Any): Iterable of filesystem paths as strings.
  
  Returns:
    None
  
  Examples:
    >>> _record_validated_day_hint(None, "x", None)  # doctest: +SKIP
  """
  if validated_days_out is None or members is None:
    return
  identity = _archive_file_identity(gz_path)
  if identity is None:
    return
  entry = {
      "mtime_ns": identity[0],
      "size": identity[1],
      "ok": True,
      "member_count": len(members),
      "member_byte_sum": sum_member_bytes(members),
  }
  tar_path = daily_tar_path_from_compressed(gz_path)
  try:
    if os.path.isfile(tar_path):
      st = os.stat(tar_path)
      entry["tar_mtime_ns"] = int(st.st_mtime_ns)
      entry["tar_size"] = int(st.st_size)
  except OSError:
    pass
  validated_days_out[normalize_daily_compressed_path(gz_path)] = entry


def classify_removable_raw_paths_for_members(
  stats_paths: Any,
  members: Any,
  *,
  ingest_ready_fn: Any | None = None,
) -> Any:
  """
  Classify raw paths against a validated member map without deleting.
  
  Args:
    stats_paths (Any): Iterable of filesystem paths as strings.
    members (Any): Iterable of filesystem paths as strings.
    ingest_ready_fn (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> classify_removable_raw_paths_for_members(None, None, None)
  """
  results = []
  for stats_path in stats_paths:
    if ingest_ready_fn is not None and not ingest_ready_fn(stats_path):
      results.append(
          (stats_path, "skipped_not_head_tail_ingested", "not_head_tail_ingested"))
      continue
    removable = get_verified_files_to_remove([stats_path], members)
    if stats_path in removable:
      results.append((stats_path, "verified", ""))
      continue
    member_name = get_tar_member_name(stats_path)
    if member_name not in members:
      results.append(
          (stats_path, "skipped_not_in_archive", "not_in_sealed_archive"))
    else:
      results.append((stats_path, "skipped_size_mismatch", "size_mismatch"))
  return results


def classify_removable_raw_paths_for_daily_gz(
  gz_path: str,
  stats_paths: Any,
  *,
  ingest_ready_fn: Any | None = None,
  allow_auto_seal: bool = False,
  log_fn: Any = log_print,
  validation_cache: Any | None = None,
  sealed_members: Any | None = None,
) -> Any:
  """
  Classify raw paths for a daily compressed archive without deleting.
  
  Args:
    gz_path (str): String for gz path.
    stats_paths (Any): Iterable of filesystem paths as strings.
    ingest_ready_fn (Any | None): One of ``Any``, ``None``.
    allow_auto_seal (bool): Boolean flag for allow auto seal.
    log_fn (Any): Callable invoked by this helper.
    validation_cache (Any | None): One of ``Any``, ``None``.
    sealed_members (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> classify_removable_raw_paths_for_daily_gz(0)  # doctest: +SKIP
  """
  if not stats_paths:
    return []
  if sealed_members is not None:
    ok, members = True, dict(sealed_members)
  else:
    ok, members = validate_sealed_daily_archive_for_raw_removal(
        gz_path,
        log_fn=log_fn,
        validation_cache=validation_cache,
        allow_auto_seal=allow_auto_seal,
    )
  if not ok or members is None:
    return [
        (path, "skipped_seal_invalid", "seal_validation_failed")
        for path in stats_paths
    ]
  return classify_removable_raw_paths_for_members(
      stats_paths,
      members,
      ingest_ready_fn=ingest_ready_fn,
  )


def validate_open_tar_for_raw_removal(
  tar_path: str,
  log_fn: Any = log_print,
  *,
  validation_cache: Any | None = None,
) -> Any:
  """
  Validate readable open ``.tar`` and return file member map for pre-seal.
  
    verify.
  
  Args:
    tar_path (str): String for tar path.
    log_fn (Any): Callable invoked by this helper.
    validation_cache (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> validate_open_tar_for_raw_removal("x", None, None)  # doctest: +SKIP
  """
  tar_path = os.path.normpath(str(tar_path or ""))
  if not tar_path:
    return False, None
  if not os.path.isfile(tar_path):
    if not ensure_daily_tar_restored_for_append(
        tar_path,
        get_archive_zstd_thread_count(),
    ):
      if log_fn:
        log_fn(
            "Skipping open-tar verify: cannot restore tar from sealed: %s"
            % tar_path,
            flush=True,
        )
      return False, None
  if not restore_tar_from_sealed_if_unreadable(
      tar_path,
      get_archive_zstd_thread_count(),
      log_fn=log_fn,
  ):
    if log_fn:
      log_fn(
          "Skipping open-tar verify: uncompressed tar failed check: %s"
          % tar_path,
          flush=True,
      )
    return False, None
  members = get_existing_archive_members(tar_path)
  if not members:
    if log_fn:
      log_fn(
          "Skipping open-tar verify: uncompressed tar has no file members: %s"
          % tar_path,
          flush=True,
      )
    return False, None
  return True, dict(members)


def classify_removable_raw_paths_for_open_tar(
  tar_path: str,
  stats_paths: Any,
  *,
  ingest_ready_fn: Any | None = None,
  log_fn: Any = log_print,
  validation_cache: Any | None = None,
  open_tar_members: Any | None = None,
) -> Any:
  """
  Classify raw paths against an open daily ``.tar`` without deleting.
  
  Args:
    tar_path (str): String for tar path.
    stats_paths (Any): Iterable of filesystem paths as strings.
    ingest_ready_fn (Any | None): One of ``Any``, ``None``.
    log_fn (Any): Callable invoked by this helper.
    validation_cache (Any | None): One of ``Any``, ``None``.
    open_tar_members (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> classify_removable_raw_paths_for_open_tar(0)  # doctest: +SKIP
  """
  if not stats_paths:
    return []
  if open_tar_members is not None:
    ok, members = True, dict(open_tar_members)
  else:
    ok, members = validate_open_tar_for_raw_removal(
        tar_path,
        log_fn=log_fn,
        validation_cache=validation_cache,
    )
  if not ok or members is None:
    return [
        (path, "skipped_seal_invalid", "seal_validation_failed")
        for path in stats_paths
    ]
  return classify_removable_raw_paths_for_members(
      stats_paths,
      members,
      ingest_ready_fn=ingest_ready_fn,
  )


def validate_post_seal_tar_zst_parity(
  tar_path: str,
  log_fn: Any = log_print,
  *,
  validation_cache: Any | None = None,
) -> Any:
  """
  Return True when open ``.tar`` and ``.tar.zst`` member maps match.
  
  Args:
    tar_path (str): String for tar path.
    log_fn (Any): Callable invoked by this helper.
    validation_cache (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> validate_post_seal_tar_zst_parity("x", None, None)  # doctest: +SKIP
  """
  tar_path = os.path.normpath(str(tar_path or ""))
  zst_path, _gz_path = compressed_sibling_paths(tar_path)
  if not os.path.isfile(zst_path):
    if log_fn:
      log_fn(
          "janitor: day_close post_seal_verify failed sealed missing: %s"
          % zst_path,
          flush=True,
      )
    return False
  ok, _members = validate_sealed_daily_archive_for_raw_removal(
      zst_path,
      log_fn=log_fn,
      validation_cache=validation_cache,
      allow_auto_seal=False,
  )
  if not ok and log_fn:
    log_fn(
        "janitor: day_close post_seal_verify mismatch tar=%s"
        % tar_path,
        flush=True,
    )
  return ok


def remove_verified_archived_raw_files(
  archive_data_dir: str,
  host_name_ext: Any,
  tgz_archive_dir: str,
  *,
  log_fn: Any = log_print,
  archive_stats_files_fn: Any | None = None,
  ingest_ready_fn: Any | None = None,
  maintenance_snapshot: Any | None = None,
  validation_cache: Any | None = None,
  validated_days_out: Any | None = None,
  skip_daily_tar_paths: Any | None = None,
  only_daily_tar_paths: Any | None = None,
  allow_auto_seal: bool = True,
  max_deletes_per_pass: Any | None = None,
  skip_raw_paths: Any | None = None,
  require_fingerprint_at_delete: bool = False,
) -> None:
  """
  Remove raw stats files only after tar + sealed archive validation.
  
  For each daily ``.tar.zst`` (or legacy ``.tar.gz``), requires: sibling
    ``.tar`` (if present) passes
  ``verify_tar_archive_readable`` and yields file member sizes; ``.tar.gz``
    passes
  the same and yields sizes from the gzip archive only; the two maps must match.
  Then deletes raw paths that match a member name and size.
  
  When ``ingest_ready_fn`` is set (production: sampled timestamps in
    ``host_data``),
  bootstrap and removal apply only to paths for which it returns true.
  
  Scans all closed segments under ``archive_data_dir`` (same rules as ingest).
  Run after ``seal_dirty_daily_archives``. If only ``.tar.gz`` exists (no
    sibling
  ``.tar``), gzip alone is validated and its map is used.
  
  If raw files map to a day but neither ``.tar`` nor ``.tar.gz`` exists yet
  (e.g. ingest already finished and archival was never run for those paths),
  this routine bootstraps the daily archive via ``archive_stats_files`` before
  validation and removal.
  
  Args:
    archive_data_dir (str): String for archive data dir.
    host_name_ext (Any): Host name ext passed to this helper.
    tgz_archive_dir (str): String for tgz archive dir.
    log_fn (Any): Callable invoked by this helper.
    archive_stats_files_fn (Any | None): One of ``Any``, ``None``.
    ingest_ready_fn (Any | None): One of ``Any``, ``None``.
    maintenance_snapshot (Any | None): One of ``Any``, ``None``.
    validation_cache (Any | None): One of ``Any``, ``None``.
    validated_days_out (Any | None): One of ``Any``, ``None``.
    skip_daily_tar_paths (Any | None): One of ``Any``, ``None``.
    only_daily_tar_paths (Any | None): One of ``Any``, ``None``.
    allow_auto_seal (bool): Boolean flag for allow auto seal.
    max_deletes_per_pass (Any | None): One of ``Any``, ``None``.
    skip_raw_paths (Any | None): One of ``Any``, ``None``.
    require_fingerprint_at_delete (bool): Boolean flag for require fingerprint
    at delete.
  
  Returns:
    None
  
  Examples:
    >>> remove_verified_archived_raw_files(0)  # doctest: +SKIP
  """
  ready_paths_set = None
  if maintenance_snapshot is not None:
    ready_paths_set = maintenance_snapshot.ready_paths
    paths = maintenance_snapshot.closed_paths
    mapping = maintenance_snapshot.mapping
  else:
    paths = collect_stats_files_in_range(
        archive_data_dir, "backlog", None, host_name_ext)
    if not paths:
      return
    mapping = build_archive_mapping(paths, tgz_archive_dir)

  def _path_ingest_ready(path: str) -> Any:
    """
    Internal helper to check if the path ingest is ready.
    
    Args:
      path (str): String for path.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _path_ingest_ready("x")  # doctest: +SKIP
    """
    if ready_paths_set is not None:
      return path in ready_paths_set
    if ingest_ready_fn is None:
      return True
    return bool(ingest_ready_fn(path))

  skip_raw_norm = {
      os.path.normpath(p) for p in (skip_raw_paths or ()) if p
  }

  if not paths:
    return
  if archive_stats_files_fn is None:
    import hpcperfstats.dbload.sync_timedb as _sync_timedb_mod

    archive_stats_files_fn = _sync_timedb_mod.archive_stats_files
  if validation_cache is None:
    validation_cache = {"hits": 0, "misses": 0}
  validation_targets = []
  for archive_path, stats_paths in mapping.items():
    if detect_compressed_format(archive_path) is not None:
      tar_path = daily_tar_path_from_compressed(archive_path)
      if not daily_tar_path_in_maintenance_scope(
          tar_path,
          skip_daily_tar_paths=skip_daily_tar_paths,
          only_daily_tar_paths=only_daily_tar_paths,
      ):
        continue
      bootstrap_ready = [
          p for p in stats_paths if _path_ingest_ready(p)
      ]
      if (not os.path.isfile(archive_path) and not os.path.isfile(tar_path)
          and bootstrap_ready):
        if log_fn:
          log_fn(
              "Bootstrapping missing daily archive from %d raw stats file(s): %s"
              % (len(bootstrap_ready), archive_path),
              flush=True,
          )
        if not archive_stats_files_fn((archive_path, list(bootstrap_ready))):
          if log_fn:
            log_fn(
                "Skipping removal: could not bootstrap daily archive: %s"
                % archive_path,
                flush=True,
            )
          continue
      elif (not os.path.isfile(archive_path) and not os.path.isfile(tar_path)
            and stats_paths and not bootstrap_ready and log_fn):
        log_fn(
            "Skipping bootstrap for %s: %d path(s) without sampled timestamps in DB"
            % (archive_path, len(stats_paths)),
            flush=True,
        )
    validation_targets.append((archive_path, list(stats_paths)))

  if validation_targets:
    workers = _get_archive_validation_worker_count(len(validation_targets))
    validation_started = time.time()
    success_count = 0
    failed_count = 0
    stats_paths_by_gz = {gz_path: stats_paths for gz_path, stats_paths in validation_targets}
  else:
    workers = 1
    validation_started = time.time()
    success_count = 0
    failed_count = 0
    stats_paths_by_gz = {}

  for gz_path, ok, members in _iter_archive_validation_results_stream(
      [gz_path for gz_path, _stats_paths in validation_targets],
      log_fn=log_fn,
      validation_cache=validation_cache if workers <= 1 else None,
      allow_auto_seal=allow_auto_seal,
  ):
    if workers > 1:
      validation_cache["misses"] = int(validation_cache.get("misses", 0)) + 1
    if not ok or members is None:
      failed_count += 1
      continue
    success_count += 1
    _record_validated_day_hint(validated_days_out, gz_path, members)
    deletes_this_pass = 0
    for stats_path in stats_paths_by_gz.get(gz_path, []):
      for path, status, _reason in classify_removable_raw_paths_for_members(
          [stats_path],
          members,
          ingest_ready_fn=_path_ingest_ready,
      ):
        if status != "verified":
          continue
        if os.path.normpath(path) in skip_raw_norm:
          continue
        if (
            max_deletes_per_pass is not None
            and deletes_this_pass >= max_deletes_per_pass
        ):
          return
        if log_fn:
          log_fn(
              "removing stats file (scheduled archive maintenance): " + path,
              flush=True,
          )
        if require_fingerprint_at_delete:
          fp = raw_stats_path_fingerprint(path)
          if not delete_raw_stats_path_if_fingerprint_unchanged(
              path, fp, log_fn=log_fn,
          ):
            continue
          deletes_this_pass += 1
          continue
        try:
          with file_write_lock(path):
            os.remove(path)
          deletes_this_pass += 1
        except OSError as exc:
          if log_fn:
            log_fn("Could not remove %s: %s" % (path, exc), flush=True)
  _log_archive_validation_summary(
      log_fn=log_fn,
      validation_targets_count=len(validation_targets),
      workers=workers,
      success_count=success_count,
      failed_count=failed_count,
      validation_started=validation_started,
      validation_cache=validation_cache,
  )


def build_remaining_raw_stats_by_daily_gz(
  archive_data_dir: str,
  host_name_ext: Any,
  tgz_archive_dir: str,
  *,
  maintenance_snapshot: Any | None = None,
  allow_full_snapshot: bool = True,
  log_fn: Any | None = None,
) -> Any:
  """
  Map each daily ``.tar.zst`` to closed raw stats paths still on disk for that.
  
    day.
  
  Uses the same discovery and first-timestamp grouping as archival
  (``collect_stats_files_in_range`` + ``build_archive_mapping``). Not filtered
    by
  DB head-ingest readiness: not-yet-ingested closed segments still block
    ``.tar``
  removal (see ``sync-timedb-db-before-archive-contract.mdc``).
  
  Files with no parseable first timestamp are omitted from the mapping and do
    not
  block removal (same as archival bootstrap).
  
  When ``maintenance_snapshot`` is None and ``allow_full_snapshot`` is False,
  returns ``{}`` instead of building a full-tree maintenance snapshot
    (MainThread
  / handoff hot path must not collect head metadata for the entire archive).
  
  Args:
    archive_data_dir (str): String for archive data dir.
    host_name_ext (Any): Host name ext passed to this helper.
    tgz_archive_dir (str): String for tgz archive dir.
    maintenance_snapshot (Any | None): One of ``Any``, ``None``.
    allow_full_snapshot (bool): Boolean flag for allow full snapshot.
    log_fn (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> build_remaining_raw_stats_by_daily_gz("x", None, "x", None, True, None)
  """
  if maintenance_snapshot is not None:
    return dict(maintenance_snapshot.remaining_raw_by_gz or {})
  if not allow_full_snapshot:
    if log_fn:
      with janitorial_logging():
        log_fn(
            "remaining_raw skip full snapshot "
            "(allow_full_snapshot=False; prefer day-scoped or published snapshot)",
            flush=True,
        )
    return {}
  from hpcperfstats.dbload.lib.sync_timedb_archive_maint import (
      build_archive_maintenance_snapshot,
  )

  snapshot = build_archive_maintenance_snapshot(
      archive_data_dir,
      host_name_ext,
      tgz_archive_dir,
      build_ready_set=False,
      log_fn=log_fn,
  )
  return snapshot.remaining_raw_by_gz


def build_day_scoped_closed_raw_by_gz(
  archive_data_dir: str,
  host_name_ext: Any,
  tgz_archive_dir: str,
  tar_path: str,
  *,
  log_fn: Any | None = None,
  maintenance_snapshot: Any | None = None,
  closed_paths_snapshot: Any | None = None,
) -> Any:
  """
  Closed raw for one daily ``.tar`` without full-tree head metadata.
  
  Uses date-scoped ``collect_stats_files_in_range`` plus filename/mtime
    alignment
  (``stats_path_aligned_to_daily_tar``). Does **not** call
  ``build_archive_maintenance_snapshot`` or read first-timestamp heads.
  
  When ``maintenance_snapshot`` or ``closed_paths_snapshot`` is provided, filter
  those paths only — forbid a full discover/collect for that day.
  
  Args:
    archive_data_dir (str): String for archive data dir.
    host_name_ext (Any): Host name ext passed to this helper.
    tgz_archive_dir (str): String for tgz archive dir.
    tar_path (str): String for tar path.
    log_fn (Any | None): One of ``Any``, ``None``.
    maintenance_snapshot (Any | None): One of ``Any``, ``None``.
    closed_paths_snapshot (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> build_day_scoped_closed_raw_by_gz("x", None, "x", "x", None, None, None)
  """
  tar_norm = os.path.normpath(str(tar_path or ""))
  day = calendar_date_from_daily_tar_path(tar_norm)
  if not tar_norm or day is None or not archive_data_dir or not tgz_archive_dir:
    return {}
  if maintenance_snapshot is not None:
    closed_paths_snapshot = list(maintenance_snapshot.closed_paths or ())
  if closed_paths_snapshot is not None:
    paths = list(closed_paths_snapshot)
  else:
    # collect_stats_files_in_range expects datetime bounds (not ISO strings).
    # Inclusive calendar day: end must be past midnight so noon segments match
    # ``ts > enddate`` filter (same as single-day ingest windows).
    day_start = datetime(day.year, day.month, day.day)
    day_end = day_start + timedelta(days=1)
    paths = collect_stats_files_in_range(
        archive_data_dir,
        day_start,
        day_end,
        host_name_ext,
        force_full_scan=True,
        log_fn=None,
    )
  aligned = []
  for path in paths or ():
    if not path or not os.path.isfile(path):
      continue
    if stats_file_is_active_segment(path):
      continue
    if not stats_path_aligned_to_daily_tar(
        path,
        tar_norm,
        tgz_archive_dir=tgz_archive_dir,
    ):
      continue
    aligned.append(path)
  if not aligned:
    return {}
  zst_path, _gz_path = compressed_sibling_paths(tar_norm)
  if log_fn:
    with janitorial_logging():
      log_fn(
          "day-scoped closed_raw tar=%s paths=%d "
          "(no full maintenance snapshot)"
          % (os.path.basename(tar_norm), len(aligned)),
          flush=True,
      )
  return {zst_path: aligned}


def build_remaining_raw_for_daily_tar(
  archive_data_dir: str,
  host_name_ext: Any,
  tgz_archive_dir: str,
  tar_path: str,
  *,
  maintenance_snapshot: Any | None = None,
  allow_full_snapshot: bool = True,
  log_fn: Any | None = None,
) -> Any:
  """
  Day-scoped ``remaining_raw_by_gz`` for one daily ``.tar`` path.
  
  Prefer ``maintenance_snapshot``. When absent and ``allow_full_snapshot`` is
  False (MainThread handoff/exclude), use ``build_day_scoped_closed_raw_by_gz``
  instead of a full-tree ``build_archive_maintenance_snapshot``.
  
  Args:
    archive_data_dir (str): String for archive data dir.
    host_name_ext (Any): Host name ext passed to this helper.
    tgz_archive_dir (str): String for tgz archive dir.
    tar_path (str): String for tar path.
    maintenance_snapshot (Any | None): One of ``Any``, ``None``.
    allow_full_snapshot (bool): Boolean flag for allow full snapshot.
    log_fn (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> build_remaining_raw_for_daily_tar("x", None, "x", "x", None, True, None)
  """
  tar_norm = os.path.normpath(tar_path)
  if maintenance_snapshot is not None:
    full = build_remaining_raw_stats_by_daily_gz(
        archive_data_dir,
        host_name_ext,
        tgz_archive_dir,
        maintenance_snapshot=maintenance_snapshot,
        allow_full_snapshot=False,
        log_fn=log_fn,
    )
    scoped = {}
    for gz_key, paths in (full or {}).items():
      if not paths:
        continue
      if os.path.normpath(daily_tar_path_from_compressed(gz_key)) == tar_norm:
        scoped[gz_key] = paths
    filtered = filter_remaining_raw_aligned_to_tar(
        scoped,
        tar_norm,
        tgz_archive_dir=tgz_archive_dir,
    )
    if filtered or allow_full_snapshot:
      return filtered
    # Snapshot published but this tar has no remaining entries — day-scoped
    # refresh so unmanifested closed raw is not silently omitted (R2).
    return build_day_scoped_closed_raw_by_gz(
        archive_data_dir,
        host_name_ext,
        tgz_archive_dir,
        tar_norm,
        log_fn=log_fn,
    )
  if not allow_full_snapshot:
    return build_day_scoped_closed_raw_by_gz(
        archive_data_dir,
        host_name_ext,
        tgz_archive_dir,
        tar_norm,
        log_fn=log_fn,
    )
  full = build_remaining_raw_stats_by_daily_gz(
      archive_data_dir,
      host_name_ext,
      tgz_archive_dir,
      maintenance_snapshot=None,
      allow_full_snapshot=True,
      log_fn=log_fn,
  )
  scoped = {}
  for gz_key, paths in (full or {}).items():
    if not paths:
      continue
    if os.path.normpath(daily_tar_path_from_compressed(gz_key)) == tar_norm:
      scoped[gz_key] = paths
  return scoped

def remaining_raw_by_gz_has_paths_on_disk(
  remaining_by_gz: Any,
  gz_path: Any | None = None,
) -> Any:
  """
  True if ``remaining_by_gz`` lists at least one raw stats path that exists on.
  
    disk.
  
  When ``gz_path`` is set, only paths mapped to that daily compressed archive
    are
  considered. Ghost mapping entries (listed but already deleted) return False.
  
  Args:
    remaining_by_gz (Any): Remaining by gz passed to this helper.
    gz_path (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> remaining_raw_by_gz_has_paths_on_disk(None, None)  # doctest: +SKIP
  """
  if not remaining_by_gz:
    return False
  if gz_path is not None:
    key = normalize_daily_compressed_path(gz_path)
    paths = remaining_by_gz.get(key) or ()
    return any(os.path.isfile(path) for path in paths)
  for paths in remaining_by_gz.values():
    if any(os.path.isfile(path) for path in (paths or ())):
      return True
  return False


def remove_verified_uncompressed_daily_tars(
  daily_archive_dir: str,
  *,
  log_fn: Any = log_print,
  remaining_raw_by_gz: Any | None = None,
  force_remove_uncompressed_tar: bool = False,
  validation_cache: Any | None = None,
  validated_days_out: Any | None = None,
  skip_daily_tar_paths: Any | None = None,
  only_daily_tar_paths: Any | None = None,
) -> None:
  """
  Remove ``YYYY-MM-DD.tar`` only after tar/tar.gz verification succeeds.
  
  Called after ``remove_verified_archived_raw_files`` on every archive
    maintenance
  pass. Skips a day when ``remaining_raw_by_gz`` still lists closed raw stats
    for
  that calendar day (filesystem gate; not the DB head-ingest gate), unless
  ``force_remove_uncompressed_tar`` is true (startup maintenance only).
  
  When ``archive_keep_uncompressed_tar`` is false, ``atomic_seal_tar_to_zst``
    may
  also unlink the ``.tar`` after sealing when no raw stats remain for that day.
  
  Args:
    daily_archive_dir (str): String for daily archive dir.
    log_fn (Any): Callable invoked by this helper.
    remaining_raw_by_gz (Any | None): One of ``Any``, ``None``.
    force_remove_uncompressed_tar (bool): Whether to enable force remove
    uncompressed tar.
    validation_cache (Any | None): One of ``Any``, ``None``.
    validated_days_out (Any | None): One of ``Any``, ``None``.
    skip_daily_tar_paths (Any | None): One of ``Any``, ``None``.
    only_daily_tar_paths (Any | None): One of ``Any``, ``None``.
  
  Returns:
    None
  
  Examples:
    >>> remove_verified_uncompressed_daily_tars(0)  # doctest: +SKIP
  """
  if not daily_archive_dir or not os.path.isdir(daily_archive_dir):
    return
  if validation_cache is None:
    validation_cache = {"hits": 0, "misses": 0}
  validation_targets = []
  tar_by_gz = {}
  for tar_path in sorted(iter_daily_tar_paths(daily_archive_dir)):
    if not daily_tar_path_in_maintenance_scope(
        tar_path,
        skip_daily_tar_paths=skip_daily_tar_paths,
        only_daily_tar_paths=only_daily_tar_paths,
    ):
      continue
    zst_path, _gz_path = compressed_sibling_paths(tar_path)
    if not os.path.isfile(tar_path):
      continue
    sealed_path = zst_path if os.path.isfile(zst_path) else (
        _gz_path if os.path.isfile(_gz_path) else zst_path
    )
    validation_targets.append(sealed_path)
    tar_by_gz[sealed_path] = tar_path

  if validation_targets:
    workers = _get_archive_validation_worker_count(len(validation_targets))
    validation_started = time.time()
    success_count = 0
    failed_count = 0
  else:
    workers = 1
    validation_started = time.time()
    success_count = 0
    failed_count = 0

  for gz_path, ok, members in _iter_archive_validation_results_stream(
      validation_targets,
      log_fn=log_fn,
      validation_cache=validation_cache if workers <= 1 else None,
  ):
    if workers > 1:
      validation_cache["misses"] = int(validation_cache.get("misses", 0)) + 1
    if not ok or members is None:
      failed_count += 1
      continue
    success_count += 1
    _record_validated_day_hint(validated_days_out, gz_path, members)
    tar_path = tar_by_gz[gz_path]
    if (
        not force_remove_uncompressed_tar
        and remaining_raw_by_gz_has_paths_on_disk(remaining_raw_by_gz, gz_path)
    ):
      if log_fn:
        log_fn(
            "Skipping removal of verified uncompressed tar (raw stats still "
            "present for day): %s" % tar_path,
            flush=True,
        )
      continue
    try:
      with file_write_lock(tar_path):
        if os.path.isfile(tar_path):
          os.remove(tar_path)
      if log_fn:
        log_fn(
            "Maintenance removed verified uncompressed tar: %s" % tar_path,
            flush=True,
        )
    except OSError as exc:
      if log_fn:
        log_fn("Could not remove verified tar %s: %s" % (tar_path, exc), flush=True)
  _log_archive_validation_summary(
      log_fn=log_fn,
      validation_targets_count=len(validation_targets),
      workers=workers,
      success_count=success_count,
      failed_count=failed_count,
      validation_started=validation_started,
      validation_cache=validation_cache,
  )


def tar_has_duplicate_file_members(tar_path: str) -> Any:
  """
  Return True if any file member path appears more than once in archive order.
  
  Args:
    tar_path (str): String for tar path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> tar_has_duplicate_file_members("x")  # doctest: +SKIP
  """
  if not os.path.isfile(tar_path):
    return False
  try:
    with file_read_lock_wait(tar_path):
      with tarfile.open(tar_path, "r") as tf:
        seen = set()
        for m in _iter_tar_members(tf):
          if not m.isfile():
            continue
          if m.name in seen:
            return True
          seen.add(m.name)
        return False
  except Exception:
    return False


def _dedupe_member_indices_keep_largest_file_per_name(members: Any) -> Any:
  """
  Indices to keep: all non-file members; for each file name, one entry with max.
  
    size (tie: last).
  
  Args:
    members (Any): Iterable of filesystem paths as strings.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _dedupe_member_indices_keep_largest_file_per_name(None)
  """
  keep = set()
  by_name = defaultdict(list)
  for i, m in enumerate(members):
    if m.isfile():
      by_name[m.name].append((i, m.size))
    else:
      keep.add(i)
  for _name, lst in by_name.items():
    max_sz = max(s for _i, s in lst)
    tie_indices = [i for i, s in lst if s == max_sz]
    keep.add(tie_indices[-1])
  return keep


def dedupe_tar_keep_largest_file_per_member(
  tar_path: str,
  log_fn: Any = log_print,
  *,
  tgz_archive_dir: str = "",
  yield_phase: str = "dedupe",
) -> Any:
  """
  Rewrite ``tar_path`` so each file path appears once: keep largest size (tie:.
  
    last).
  
  Writes ``tar_path`` + ``.dedupe.tmp``, verifies, then ``os.replace``. Returns
  False on failure (original tar unchanged if replace never ran).
  Raises ``DayCloseYieldError`` when ingest hot signals require cooperative
    yield.
  
  Args:
    tar_path (str): String for tar path.
    log_fn (Any): Callable invoked by this helper.
    tgz_archive_dir (str): String for tgz archive dir.
    yield_phase (str): String for yield phase.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Raises:
    DayCloseYieldError: Raised when
    ``dedupe_tar_keep_largest_file_per_member`` hits a ``DayCloseYieldError``
    failure path.
    Exception: Raised when ``dedupe_tar_keep_largest_file_per_member`` hits a
    ``Exception`` failure path.
  
  Examples:
    >>> dedupe_tar_keep_largest_file_per_member("x", None, "x", "x")
  """
  from hpcperfstats.dbload.lib.sync_timedb_day_close_cooperation import (
      DayCloseYieldError,
      check_day_close_yield_or_continue,
      day_close_yield_requested,
  )

  if not os.path.isfile(tar_path):
    return True
  tmp_path = "%s.dedupe.tmp" % tar_path
  try:
    if os.path.exists(tmp_path):
      os.remove(tmp_path)
  except OSError:
    pass
  try:
    with file_write_lock(tar_path):
      file_keep = {}
      last_yield_poll = time.monotonic()
      with tarfile.open(tar_path, "r") as tin:
        for idx, member in enumerate(_iter_tar_members(tin)):
          last_yield_poll, _ = check_day_close_yield_or_continue(
              tar_path,
              last_poll_monotonic=last_yield_poll,
              tgz_archive_dir=tgz_archive_dir,
              phase=yield_phase,
          )
          if not member.isfile():
            continue
          prev = file_keep.get(member.name)
          if prev is None or member.size > prev[0] or (
              member.size == prev[0] and idx > prev[1]
          ):
            file_keep[member.name] = (member.size, idx)
      with tarfile.open(tar_path, "r") as tin:
        with tarfile.open(tmp_path, "w") as tout:
          for idx, member in enumerate(_iter_tar_members(tin)):
            last_yield_poll, _ = check_day_close_yield_or_continue(
                tar_path,
                last_poll_monotonic=last_yield_poll,
                tgz_archive_dir=tgz_archive_dir,
                phase=yield_phase,
            )
            if member.isfile():
              keep = file_keep.get(member.name)
              if keep is None or keep[1] != idx:
                continue
              fobj = tin.extractfile(member)
              if fobj is None:
                continue
              try:
                tout.addfile(member, fobj)
              finally:
                fobj.close()
              continue
            tout.addfile(member)
      requested, reason = day_close_yield_requested(
          tar_path,
          tgz_archive_dir=tgz_archive_dir,
          phase=yield_phase,
      )
      if requested:
        raise DayCloseYieldError(tar_path, phase=yield_phase, reason=reason)
      if not verify_tar_archive_readable(tmp_path):
        try:
          os.remove(tmp_path)
        except OSError:
          pass
        return False
      os.replace(tmp_path, tar_path)
    invalidate_after_daily_tar_mutation(
        tar_path,
        reason="dedupe_tar_keep_largest",
        log_fn=log_fn,
    )
    if log_fn:
      log_fn("Deduplicated archive (largest wins per path): %s" % tar_path, flush=True)
    return True
  except DayCloseYieldError:
    try:
      if os.path.exists(tmp_path):
        os.remove(tmp_path)
    except OSError:
      pass
    raise
  except Exception:
    try:
      if os.path.exists(tmp_path):
        os.remove(tmp_path)
    except OSError:
      pass
    return False


def dedupe_sealed_daily_archive(
  archive_compressed_path: str,
  log_fn: Any = log_print,
  *,
  keep_uncompressed_tar: Any | None = None,
  tgz_archive_dir: str = "",
) -> Any:
  """
  Last resort: decompress sealed archive, dedupe ``.tar``, and re-seal.
  
  Used by the archive janitor when only ``.tar.zst`` / ``.tar.gz`` exists and
  duplicate file members were detected during ingest member-cache populate.
  
  Args:
    archive_compressed_path (str): String for archive compressed path.
    log_fn (Any): Callable invoked by this helper.
    keep_uncompressed_tar (Any | None): One of ``Any``, ``None``.
    tgz_archive_dir (str): String for tgz archive dir.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Raises:
    Exception: Raised when ``dedupe_sealed_daily_archive`` hits a
    ``Exception`` failure path.
  
  Examples:
    >>> dedupe_sealed_daily_archive("x", None, None, "x")  # doctest: +SKIP
  """
  fmt = detect_compressed_format(archive_compressed_path)
  if fmt not in ("zst", "gz"):
    return False
  sealed_path = archive_compressed_path
  if not os.path.isfile(sealed_path):
    sealed_path = _resolve_sealed_daily_archive_path(archive_compressed_path)
  if sealed_path is None or not os.path.isfile(sealed_path):
    return False
  tar_path = daily_tar_path_from_compressed(sealed_path)
  zst_path, gz_path = compressed_sibling_paths(tar_path)
  if os.path.isfile(tar_path):
    if not tar_has_duplicate_file_members(tar_path):
      return True
    if not dedupe_tar_keep_largest_file_per_member(
        tar_path,
        log_fn=log_fn,
        tgz_archive_dir=tgz_archive_dir,
        yield_phase="dedupe_sealed",
    ):
      return False
  else:
    if not decompress_compressed_to_tar(
        sealed_path,
        tar_path,
        get_archive_zstd_thread_count(),
        remove_compressed=False,
    ):
      if log_fn:
        log_fn(
            "dedupe_sealed_daily_archive: decompress failed for %s"
            % sealed_path,
            flush=True,
        )
      return False
    if not dedupe_tar_keep_largest_file_per_member(
        tar_path,
        log_fn=log_fn,
        tgz_archive_dir=tgz_archive_dir,
        yield_phase="dedupe_sealed",
    ):
      return False
  if keep_uncompressed_tar is None:
    keep_uncompressed_tar = cfg.get_archive_keep_uncompressed_tar()
  try:
    atomic_seal_tar_to_zst(
        tar_path,
        zst_path if sealed_path.endswith(DAILY_ARCHIVE_ZST_SUFFIX) else zst_path,
        num_threads=get_archive_zstd_thread_count(),
        compress_level=cfg.get_archive_zstd_level(),
        keep_uncompressed_tar=keep_uncompressed_tar,
        log_fn=log_fn,
        tgz_archive_dir=tgz_archive_dir,
    )
  except DayCloseYieldError:
    raise
  except (OSError, subprocess.CalledProcessError) as exc:
    if log_fn:
      log_fn(
          "dedupe_sealed_daily_archive: re-seal failed for %s (%s)"
          % (tar_path, exc),
          flush=True,
      )
    return False
  invalidate_after_daily_tar_mutation(
      sealed_path,
      reason="dedupe_sealed_reseal",
      log_fn=log_fn,
  )
  try:
    from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
        clear_dedupe_hint,
        dedupe_hint_is_set,
    )
    day = calendar_date_from_daily_tar_path(tar_path)
    if day is not None and dedupe_hint_is_set(day.isoformat()):
      clear_dedupe_hint(day.isoformat())
  except Exception:
    pass
  if log_fn:
    log_fn(
        "dedupe_sealed_daily_archive: re-sealed after dedupe %s" % sealed_path,
        flush=True,
    )
  return True


def resolve_preferred_archive_path_for_read(path: str) -> Any:
  """
  Prefer mutable uncompressed ``.tar`` when it and a compressed sibling exist.
  
  Args:
    path (str): String for path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> resolve_preferred_archive_path_for_read("x")  # doctest: +SKIP
  """
  tar_path = daily_tar_path_from_compressed(path)
  if os.path.isfile(tar_path):
    return tar_path
  return path


def resolve_sealed_archive_path_for_ingest(
  archive_path_or_day: Any,
  daily_archive_dir: str = "",
) -> Any:
  """
  Return on-disk sealed path (``.tar.zst`` or legacy ``.tar.gz``) for ingest.
  
  Never returns uncompressed ``.tar``. Returns ``None`` when only ``.tar``
    exists
  or the day/path is missing (``sync_timedb_archive`` sealed-only backfill).
  
  Args:
    archive_path_or_day (Any): Archive path or day passed to this helper.
    daily_archive_dir (str): String for daily archive dir.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> resolve_sealed_archive_path_for_ingest(None, "x")  # doctest: +SKIP
  """
  token = str(archive_path_or_day or "").strip()
  if not token:
    return None
  fmt = detect_compressed_format(token)
  if fmt in ("zst", "gz"):
    return _resolve_sealed_daily_archive_path(token)
  if _DAILY_ISO_DATE_RE.fullmatch(token):
    if not daily_archive_dir:
      return None
    ref_tar = daily_tar_path_for_calendar_day(daily_archive_dir, token)
  else:
    ref_tar = daily_tar_path_from_compressed(token)
  canonical = normalize_daily_compressed_path(ref_tar)
  sealed = _resolve_sealed_daily_archive_path(canonical)
  if sealed:
    return sealed
  if os.path.isfile(ref_tar):
    return None
  return None


def iter_sealed_daily_archive_member_lines(sealed_path: str) -> Iterator[Any]:
  """
  Yield ``(member_name, lines)`` from a sealed daily archive via in-memory zstd.
  
    stream.
  
  Does not read uncompressed ``.tar`` or write decompress artifacts to disk.
  
  Args:
    sealed_path (str): String for sealed path.
  
  Yields:
    Iterator[Any]: Value produced by this call (type depends on inputs).
  
  Raises:
    FileNotFoundError: Raised when ``iter_sealed_daily_archive_member_lines``
    hits a ``FileNotFoundError`` failure path.
    ValueError: Raised when ``iter_sealed_daily_archive_member_lines`` hits a
    ``ValueError`` failure path.
  
  Examples:
    >>> iter_sealed_daily_archive_member_lines("x")  # doctest: +SKIP
  """
  fmt = detect_compressed_format(sealed_path)
  if fmt not in ("zst", "gz"):
    raise ValueError(
        "sync_timedb_archive requires sealed archive (.tar.zst or .tar.gz): %s"
        % sealed_path,
    )
  if not os.path.isfile(sealed_path):
    raise FileNotFoundError(sealed_path)
  max_bytes = cfg.get_sync_ingest_max_file_read_bytes()
  with file_read_lock_wait(sealed_path):
    with _open_tarfile_for_read(
        sealed_path,
        get_archive_zstd_thread_count(),
        apply_priority_wrap=True,
    ) as archive_tar:
      for member_info in _iter_tar_members(archive_tar):
        if not member_info.isfile():
          continue
        if max_bytes > 0 and member_info.size > max_bytes:
          log_print(
              "sync_timedb_archive: skip oversize member %s (%d bytes > %d)"
              % (member_info.name, member_info.size, max_bytes),
              flush=True,
          )
          continue
        fobj = archive_tar.extractfile(member_info)
        if fobj is None:
          continue
        raw = fobj.read()
        if not raw:
          content = []
        else:
          text = raw.decode("utf-8")
          if text.endswith("\n"):
            content = text.splitlines(keepends=True)
          else:
            lines = text.splitlines(keepends=True)
            content = lines if lines else [text]
        yield member_info.name, content


def _archive_ingest_spool_root(
  spool_dir: Any | None = None,
  *,
  sealed_path: Any | None = None,
) -> Any:
  """
  Directory for spooling sealed tar members to disk (path-only ingest).
  
  Args:
    spool_dir (Any | None): One of ``Any``, ``None``.
    sealed_path (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Open return polymorphism from ``_archive_ingest_spool_root``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> _archive_ingest_spool_root(None, None)  # doctest: +SKIP
  """
  if spool_dir:
    root = spool_dir
  elif sealed_path:
    root = os.path.join(
        os.path.dirname(os.path.abspath(sealed_path)),
        ".sync_archive_ingest_spool",
    )
  else:
    base = cfg.get_archive_dir_path() or cfg.get_daily_archive_dir_path()
    if not base:
      import tempfile
      base = tempfile.gettempdir()
    root = os.path.join(base, ".sync_archive_ingest_spool")
  os.makedirs(root, exist_ok=True)
  return root


def _member_spool_relative_path(member_name: Any) -> Any:
  """
  Return a relative path with host/filename shape for ``parse_stats_file_path``.
  
  Args:
    member_name (Any): Member name passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _member_spool_relative_path(None)  # doctest: +SKIP
  """
  member_name = member_name.lstrip("/").replace("\\", "/")
  parts = member_name.split("/")
  if len(parts) >= 2:
    return member_name
  return os.path.join("_archive", member_name)


def iter_sealed_daily_archive_member_paths(
  sealed_path: str,
  spool_dir: Any | None = None,
  on_member_skipped: Any | None = None,
) -> Iterator[Any]:
  """
  Yield ``(member_name, path_on_disk)`` for path-only ingest (enables streaming.
  
    parse).
  
  Each tar member is spooled under ``spool_dir`` (or
    ``.sync_archive_ingest_spool``).
  Callers must remove ``path_on_disk`` after ingest.
  ``on_member_skipped`` is invoked once per member not yielded (oversize,
    unreadable).
  
  Args:
    sealed_path (str): String for sealed path.
    spool_dir (Any | None): One of ``Any``, ``None``.
    on_member_skipped (Any | None): One of ``Any``, ``None``.
  
  Yields:
    Iterator[Any]: Value produced by this call (type depends on inputs).
  
  Raises:
    FileNotFoundError: Raised when ``iter_sealed_daily_archive_member_paths``
    hits a ``FileNotFoundError`` failure path.
    ValueError: Raised when ``iter_sealed_daily_archive_member_paths`` hits a
    ``ValueError`` failure path.
  
  Examples:
    >>> iter_sealed_daily_archive_member_paths("x", None, None)
  """
  fmt = detect_compressed_format(sealed_path)
  if fmt not in ("zst", "gz"):
    raise ValueError(
        "sync_timedb_archive requires sealed archive (.tar.zst or .tar.gz): %s"
        % sealed_path,
    )
  if not os.path.isfile(sealed_path):
    raise FileNotFoundError(sealed_path)
  max_bytes = cfg.get_sync_ingest_max_file_read_bytes()
  spool_root = _archive_ingest_spool_root(spool_dir, sealed_path=sealed_path)
  with file_read_lock_wait(sealed_path):
    with _open_tarfile_for_read(
        sealed_path,
        get_archive_zstd_thread_count(),
        apply_priority_wrap=True,
    ) as archive_tar:
      for member_info in _iter_tar_members(archive_tar):
        if not member_info.isfile():
          continue
        if max_bytes > 0 and member_info.size > max_bytes:
          log_print(
              "sync_timedb_archive: skip oversize member %s (%d bytes > %d)"
              % (member_info.name, member_info.size, max_bytes),
              flush=True,
          )
          if on_member_skipped is not None:
            on_member_skipped()
          continue
        fobj = archive_tar.extractfile(member_info)
        if fobj is None:
          if on_member_skipped is not None:
            on_member_skipped()
          continue
        rel_path = _member_spool_relative_path(member_info.name)
        dest = os.path.join(spool_root, rel_path)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        try:
          with open(dest, "wb") as out:
            while True:
              chunk = fobj.read(1 << 20)
              if not chunk:
                break
              out.write(chunk)
        finally:
          try:
            fobj.close()
          except Exception:
            pass
        yield member_info.name, dest


def iter_archive_ingest_tasks(
  archive_paths: Any,
  daily_archive_dir: str = "",
) -> Iterator[Any]:
  """
  Yield ``(STREAM_ARCHIVE_TASK, sealed_path)`` for resolved sealed archives.
  
  Args:
    archive_paths (Any): Iterable of filesystem paths as strings.
    daily_archive_dir (str): String for daily archive dir.
  
  Yields:
    Iterator[Any]: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> iter_archive_ingest_tasks(None, "x")  # doctest: +SKIP
  """
  seen = set()
  for path in archive_paths or []:
    sealed = resolve_sealed_archive_path_for_ingest(path, daily_archive_dir)
    if not sealed:
      log_print(
          "sync_timedb_archive: skipped_tar_only (no sealed archive): %s"
          % path,
          flush=True,
      )
      continue
    norm = os.path.normpath(sealed)
    if norm in seen:
      continue
    seen.add(norm)
    yield (STREAM_ARCHIVE_TASK, sealed)


def iter_daily_sealed_archive_calendar_days(
  daily_archive_dir: str,
) -> Iterator[Any]:
  """
  Yield ``date`` objects for days with ``.tar.zst`` or legacy ``.tar.gz`` on.
  
    disk.
  
  Args:
    daily_archive_dir (str): String for daily archive dir.
  
  Yields:
    Iterator[Any]: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> iter_daily_sealed_archive_calendar_days("x")  # doctest: +SKIP
  """
  if not daily_archive_dir or not os.path.isdir(daily_archive_dir):
    return
  day_tokens = set()
  try:
    names = os.listdir(daily_archive_dir)
  except OSError:
    return
  for name in names:
    if _DAILY_ZST_BASENAME_RE.match(name) or _DAILY_GZ_BASENAME_RE.match(name):
      day_tokens.add(name[:10])
  for day_str in sorted(day_tokens):
    try:
      yield datetime.strptime(day_str, "%Y-%m-%d").date()
    except ValueError:
      continue


def collect_sealed_daily_archive_paths_in_range(
  daily_archive_dir: str,
  startdate: Any,
  enddate: Any,
) -> Any:
  """
  Return ``(sealed_paths, skipped_tar_only_count)`` for the date range.
  
  ``startdate`` may be ``'backlog'`` to scan every sealed day under
    ``daily_archive_dir``.
  Calendar-day ranges are inclusive on both ends.
  
  Args:
    daily_archive_dir (str): String for daily archive dir.
    startdate (Any): Time value (``datetime``, ISO string, sentinel, or
    ``None``).
    enddate (Any): Time value (``datetime``, ISO string, sentinel, or
    ``None``).
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> collect_sealed_daily_archive_paths_in_range("x", None, None)
  """
  from hpcperfstats.dbload.lib.date_utils import daterange

  paths = []
  skipped_tar_only = 0
  if startdate == "backlog":
    for day in iter_daily_sealed_archive_calendar_days(daily_archive_dir):
      sealed = resolve_sealed_archive_path_for_ingest(
          day.strftime("%Y-%m-%d"),
          daily_archive_dir,
      )
      if sealed:
        paths.append(sealed)
    return paths, skipped_tar_only

  start_dt = startdate
  end_dt = enddate
  if isinstance(start_dt, date) and not isinstance(start_dt, datetime):
    start_dt = datetime.combine(start_dt, datetime.min.time())
  if isinstance(end_dt, date) and not isinstance(end_dt, datetime):
    end_dt = datetime.combine(end_dt, datetime.min.time())

  for day_dt in daterange(start_dt, end_dt, inclusive_end=True):
    day_iso = day_dt.strftime("%Y-%m-%d")
    ref_tar = daily_tar_path_for_calendar_day(daily_archive_dir, day_iso)
    sealed = resolve_sealed_archive_path_for_ingest(day_iso, daily_archive_dir)
    if sealed:
      paths.append(sealed)
    elif os.path.isfile(ref_tar):
      skipped_tar_only += 1
      log_print(
          "sync_timedb_archive: skipped_tar_only (unsealed day): %s" % ref_tar,
          flush=True,
      )
  return paths, skipped_tar_only


def _compressed_backup_and_uncompressed_targets(open_path: str) -> Any:
  """
  Return ``(zst_path, gz_path, uncompressed_tar_path)`` for restore.
  
  Args:
    open_path (str): String for open path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _compressed_backup_and_uncompressed_targets("x")  # doctest: +SKIP
  """
  tar_path = daily_tar_path_from_compressed(open_path)
  zst_path, gz_path = compressed_sibling_paths(tar_path)
  return (zst_path, gz_path, tar_path)


def iter_tar_file_tasks(tar_path: str) -> Iterator[Any]:
  """
  Yield ``(tar_path, member_name)`` for file members only (no dirs).
  
  If the tar is unreadable and a sibling ``.tar.zst`` / ``.tar.gz`` exists,
    delete the
  unreadable tar, restore from compressed backup, and retry once.
  
  When both ``.tar`` and a compressed sibling exist (deferred compression), the
  uncompressed ``.tar`` is used so readers see the latest appends.
  
  Args:
    tar_path (str): String for tar path.
  
  Yields:
    Iterator[Any]: Value produced by this call (type depends on inputs).
  
  Raises:
    Exception: Raised when ``iter_tar_file_tasks`` hits a ``Exception``
    failure path.
  
  Examples:
    >>> iter_tar_file_tasks("x")  # doctest: +SKIP
  """
  open_path = resolve_preferred_archive_path_for_read(tar_path)

  def _iter_members() -> Iterator[Any]:
    """
    Internal helper to iterate over the members.
    
    Yields:
      Iterator[Any]: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _iter_members()  # doctest: +SKIP
    """
    with file_read_lock_wait(open_path):
      with _open_tarfile_for_read(open_path, get_archive_zstd_thread_count()) as archive_tar:
        try:
          member_infos = iter(archive_tar)
        except TypeError:
          member_infos = archive_tar.getmembers()
        for member_info in member_infos:
          if not member_info.isfile():
            continue
          yield (open_path, member_info.name)

  def _restore_from_compressed() -> Any:
    """
    Internal helper to handle restore from compressed.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _restore_from_compressed()  # doctest: +SKIP
    """
    zst_path, gz_path, tar_out = _compressed_backup_and_uncompressed_targets(
        open_path,
    )
    return replace_corrupt_tar_from_compressed_backup(
        tar_out,
        zst_path,
        gz_path,
        get_archive_zstd_thread_count(),
    )

  try:
    yield from _iter_members()
  except (tarfile.TarError, OSError, EOFError):
    log_print(
        "Unable to read archive %s (possible corruption); attempting restore "
        "from compressed backup" % open_path
    )
    if not _restore_from_compressed():
      log_print(
          "Archive recovery failed for %s; no usable compressed backup"
          % open_path
      )
      raise
    log_print("Archive recovery succeeded for %s; retrying read" % open_path)
    open_path = resolve_preferred_archive_path_for_read(tar_path)
  yield from _iter_members()


def get_tar_file_tasks(tar_path: str) -> Any:
  """
  Return list of (tar_path, member_name) for file members only (no dirs).
  
  Wrapper over ``iter_tar_file_tasks`` for callers/tests that need eager lists.
  
  Args:
    tar_path (str): String for tar path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> get_tar_file_tasks("x")  # doctest: +SKIP
  """
  return list(iter_tar_file_tasks(tar_path))


def parse_archive_date_from_daily_tar_path(tar_path: str) -> Any:
  """
  Return date from ``YYYY-MM-DD.tar`` basename, or None if not matched.
  
  Args:
    tar_path (str): String for tar path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> parse_archive_date_from_daily_tar_path("x")  # doctest: +SKIP
  """
  base = os.path.basename(tar_path)
  if not _DAILY_TAR_BASENAME_RE.match(base):
    return None
  try:
    return datetime.strptime(base[:10], "%Y-%m-%d").date()
  except ValueError:
    return None


def is_daily_tar_sealed_dirty(
  tar_path: str,
  zst_path: str,
  gz_path: str,
) -> Any:
  """
  True if ``.tar`` should be re-sealed to ``.tar.zst`` (zst missing/stale or.
  
    only legacy gz).
  
  Args:
    tar_path (str): String for tar path.
    zst_path (str): String for zst path.
    gz_path (str): String for gz path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> is_daily_tar_sealed_dirty("x", "x", "x")  # doctest: +SKIP
  """
  if not os.path.isfile(tar_path):
    return False
  if os.path.isfile(zst_path):
    try:
      return os.path.getmtime(tar_path) > os.path.getmtime(zst_path)
    except OSError:
      return True
  if os.path.isfile(gz_path) and not os.path.isfile(zst_path):
    return True
  if not os.path.exists(zst_path):
    return True
  try:
    return os.path.getmtime(tar_path) > os.path.getmtime(zst_path)
  except OSError:
    return True


def tar_day_dirty_by_mtime(tar_path: str) -> Any:
  """
  Cheap mtime check for accrual enqueue (no zstd).
  
  Args:
    tar_path (str): String for tar path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> tar_day_dirty_by_mtime("x")  # doctest: +SKIP
  """
  zst_path, gz_path = compressed_sibling_paths(tar_path)
  return is_daily_tar_sealed_dirty(tar_path, zst_path, gz_path)


def should_seal_daily_tar(
  tar_path: str,
  zst_path: str,
  idle_seconds: int,
  today_local_date: Any,
  seal_immediately_if_dirty: bool = False,
  gz_path: Any | None = None,
) -> Any:
  """
  Whether to seal now: dirty tar/zst pair and idle / prior-day rules.
  
  If ``seal_immediately_if_dirty`` (e.g. end of a sync_timedb ingest pass), any
  dirty pair is sealed regardless of idle. Otherwise today's archive waits until
  ``idle_seconds`` after its last mtime (prior calendar days seal when dirty).
  
  Args:
    tar_path (str): String for tar path.
    zst_path (str): String for zst path.
    idle_seconds (int): Integer value for idle seconds.
    today_local_date (Any): Today local date passed to this helper.
    seal_immediately_if_dirty (bool): Boolean flag for seal immediately if
    dirty.
    gz_path (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> should_seal_daily_tar("x", "x", 0, None, True, None)  # doctest: +SKIP
  """
  if gz_path is None:
    _zst, gz_path = compressed_sibling_paths(tar_path)
  if not is_daily_tar_sealed_dirty(tar_path, zst_path, gz_path):
    return False
  if seal_immediately_if_dirty:
    return True
  archive_date = parse_archive_date_from_daily_tar_path(tar_path)
  tar_mtime_age = time.time() - os.path.getmtime(tar_path)
  if archive_date is not None and archive_date < today_local_date:
    return True
  return tar_mtime_age >= float(idle_seconds)


def iter_daily_tar_paths(daily_archive_dir: str) -> Iterator[Any]:
  """
  Yield paths to ``YYYY-MM-DD.tar`` under ``daily_archive_dir``.
  
  Args:
    daily_archive_dir (str): String for daily archive dir.
  
  Yields:
    Iterator[Any]: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> iter_daily_tar_paths("x")  # doctest: +SKIP
  """
  try:
    names = os.listdir(daily_archive_dir)
  except OSError:
    return
  for name in names:
    if _DAILY_TAR_BASENAME_RE.match(name):
      yield os.path.join(daily_archive_dir, name)


def _is_migration_scratch_name(name: Any) -> Any:
  """
  True for transient seal/decompress artifacts in ``daily_archive_dir``.
  
  Args:
    name (Any): Name passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _is_migration_scratch_name(None)  # doctest: +SKIP
  """
  return (
      name.endswith(".tmp")
      or name.endswith(".decomp.tmp")
  )


def iter_daily_gz_paths(daily_archive_dir: str) -> Iterator[Any]:
  """
  Yield paths to legacy ``YYYY-MM-DD.tar.gz`` under ``daily_archive_dir``.
  
  Args:
    daily_archive_dir (str): String for daily archive dir.
  
  Yields:
    Iterator[Any]: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> iter_daily_gz_paths("x")  # doctest: +SKIP
  """
  if not daily_archive_dir or not os.path.isdir(daily_archive_dir):
    return
  try:
    names = os.listdir(daily_archive_dir)
  except OSError:
    return
  for name in sorted(names):
    if _is_lock_file_name(name) or _is_migration_scratch_name(name):
      continue
    if _DAILY_GZ_BASENAME_RE.match(name):
      yield os.path.join(daily_archive_dir, name)


def parse_archive_date_from_daily_gz_path(gz_path: str) -> Any:
  """
  Return date from ``YYYY-MM-DD.tar.gz`` basename, or None if not matched.
  
  Args:
    gz_path (str): String for gz path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> parse_archive_date_from_daily_gz_path("x")  # doctest: +SKIP
  """
  base = os.path.basename(gz_path)
  if not _DAILY_GZ_BASENAME_RE.match(base):
    return None
  try:
    return datetime.strptime(base[:10], "%Y-%m-%d").date()
  except ValueError:
    return None


def check_archive_migration_prerequisites() -> None:
  """
  Raise ``RuntimeError`` when zstd or gzip-via-zstd support is unavailable.
  
  Returns:
    None
  
  Raises:
    RuntimeError: Raised when ``check_archive_migration_prerequisites`` hits a
    ``RuntimeError`` failure path.
  
  Examples:
    >>> check_archive_migration_prerequisites()  # doctest: +SKIP
  """
  if not shutil.which("zstd"):
    raise RuntimeError("zstd executable not found on PATH")
  if not zstd_gzip_supported():
    raise RuntimeError(
        "zstd does not report gzip format support (required for legacy .tar.gz)",
    )


def compare_compressed_archive_members(
  gz_path: str,
  zst_path: str,
  *,
  gz_members: Any | None = None,
  zst_members: Any | None = None,
) -> Any:
  """
  Return ``(gz_contained_in_zst, gz_members, zst_members)`` for migration.
  
    checks.
  
  Args:
    gz_path (str): String for gz path.
    zst_path (str): String for zst path.
    gz_members (Any | None): One of ``Any``, ``None``.
    zst_members (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> compare_compressed_archive_members("x", "x", None, None)
  """
  if gz_members is not None:
    gz_ok, gz_members = True, dict(gz_members)
  else:
    gz_ok, gz_members = _scan_compressed_archive_members_and_readable(gz_path)
  if zst_members is not None:
    zst_ok, zst_members = True, dict(zst_members)
  else:
    zst_ok, zst_members = _sealed_archive_members_via_redis_or_scan(zst_path)
  if not gz_ok or not zst_ok:
    return False, gz_members, zst_members
  return (
      archive_gz_members_contained_in_zst(gz_members, zst_members),
      gz_members,
      zst_members,
  )


def drop_legacy_gz_if_equivalent_to_zst(
  gz_path: str,
  zst_path: str,
  log_fn: Any = log_print,
  *,
  gz_members: Any | None = None,
  zst_members: Any | None = None,
) -> None:
  """
  Remove legacy ``.tar.gz`` when all gzip members match in ``.tar.zst``.
  
  Args:
    gz_path (str): String for gz path.
    zst_path (str): String for zst path.
    log_fn (Any): Callable invoked by this helper.
    gz_members (Any | None): One of ``Any``, ``None``.
    zst_members (Any | None): One of ``Any``, ``None``.
  
  Returns:
    None
  
  Examples:
    >>> drop_legacy_gz_if_equivalent_to_zst("x", "x", None, None, None)
  """
  if not os.path.isfile(gz_path) or not os.path.isfile(zst_path):
    return
  gz_contained_in_zst, gz_members, zst_members = compare_compressed_archive_members(
      gz_path,
      zst_path,
      gz_members=gz_members,
      zst_members=zst_members,
  )
  if gz_contained_in_zst:
    try:
      with file_write_lock(gz_path):
        os.remove(gz_path)
      if log_fn:
        log_fn(
            "Removed legacy gzip archive after zst equivalence: %s" % gz_path,
            flush=True,
        )
    except OSError as exc:
      if log_fn:
        log_fn("Could not remove legacy gzip %s: %s" % (gz_path, exc), flush=True)
    return
  if log_fn:
    log_fn(
        "Keeping legacy gzip %s: member mismatch with %s "
        "(gzip_bytes=%s zst_bytes=%s gzip_members=%s zst_members=%s)"
        % (
            gz_path,
            zst_path,
            sum_member_bytes(gz_members),
            sum_member_bytes(zst_members),
            len(gz_members),
            len(zst_members),
        ),
        flush=True,
    )


def _seal_skip_existing_zst_equivalent(
  tar_path: str,
  zst_path: str,
  num_threads: Any,
  log_fn: Any,
) -> Any:
  """
  Return ``(skipped, zst_members)`` for seal idempotence and shrink-guard reuse.
  
  ``zst_members`` is a member map when the sealed archive was readable; ``None``
    when
  skip applies with tar absent, zst missing/unreadable, or members could not be
    loaded.
  
  Args:
    tar_path (str): String for tar path.
    zst_path (str): String for zst path.
    num_threads (Any): Num threads passed to this helper.
    log_fn (Any): Callable invoked by this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _seal_skip_existing_zst_equivalent("x", "x", None, None)
  """
  if not os.path.isfile(zst_path):
    return False, None
  try:
    zstd_test(zst_path, num_threads)
  except (OSError, subprocess.CalledProcessError, RuntimeError):
    return False, None
  if not os.path.isfile(tar_path):
    if log_fn:
      log_fn(
          "Seal skipped: sealed archive valid and uncompressed tar absent: %s"
          % zst_path,
          flush=True,
      )
    return True, None
  existing_ok, existing_members = _sealed_archive_members_via_redis_or_scan(
      zst_path,
  )
  if not existing_ok:
    return False, None
  zst_members = dict(existing_members)
  tar_members = get_existing_archive_members(tar_path)
  from hpcperfstats.dbload.lib.archive_compress import archive_member_maps_equivalent
  if archive_member_maps_equivalent(zst_members, tar_members):
    if log_fn:
      log_fn(
          "Seal skipped: tar and zst already equivalent (members=%d): %s"
          % (len(tar_members), zst_path),
          flush=True,
      )
    return True, zst_members
  return False, zst_members


def atomic_seal_tar_to_zst(
  tar_path: str,
  zst_path: str,
  num_threads: Any,
  compress_level: Any,
  keep_uncompressed_tar: Any,
  log_fn: Any = log_print,
  remaining_raw_by_gz: Any | None = None,
  force_remove_uncompressed_tar: bool = False,
  skip_result: Any | None = None,
  tgz_archive_dir: str = "",
) -> Any:
  """
  Compress ``tar_path`` to ``zst_path`` using temp file, ``zstd -t``,.
  
    ``os.replace``.
  
  Returns a zst member map snapshot when seal skipped or compress succeeded;
    ``None`` otherwise.
  
  Args:
    tar_path (str): String for tar path.
    zst_path (str): String for zst path.
    num_threads (Any): Num threads passed to this helper.
    compress_level (Any): Compress level passed to this helper.
    keep_uncompressed_tar (Any): Keep uncompressed tar passed to this helper.
    log_fn (Any): Callable invoked by this helper.
    remaining_raw_by_gz (Any | None): One of ``Any``, ``None``.
    force_remove_uncompressed_tar (bool): Whether to enable force remove
    uncompressed tar.
    skip_result (Any | None): One of ``Any``, ``None``.
    tgz_archive_dir (str): String for tgz archive dir.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Raises:
    Exception: Raised when ``atomic_seal_tar_to_zst`` hits a ``Exception``
    failure path.
    RuntimeError: Raised when ``atomic_seal_tar_to_zst`` hits a
    ``RuntimeError`` failure path.
  
  Examples:
    >>> atomic_seal_tar_to_zst(0)  # doctest: +SKIP
  """
  if not os.path.isfile(tar_path):
    if os.path.isfile(zst_path):
      _seal_skip_existing_zst_equivalent(tar_path, zst_path, num_threads, log_fn)
    return None
  if not shutil.which("zstd"):
    raise RuntimeError("zstd executable not found on PATH")
  if skip_result is None:
    skip_result = _seal_skip_existing_zst_equivalent(
        tar_path, zst_path, num_threads, log_fn,
    )
  skipped, zst_members = skip_result
  if skipped:
    return zst_members
  tmp_zst = "%s.tmp" % zst_path
  try:
    if os.path.exists(tmp_zst):
      os.remove(tmp_zst)
  except OSError:
    pass
  sealed_member_snapshot = None
  try:
    with file_write_lock(tar_path):
      tar_members = _read_tar_file_member_sizes_unlocked(tar_path)
      if os.path.isfile(zst_path):
        existing_members = zst_members
        if existing_members is None:
          existing_ok, existing_members = (
              _scan_compressed_archive_members_and_readable(zst_path)
          )
          if not existing_ok:
            existing_members = None
        if existing_members is not None:
          if len(existing_members) > len(tar_members):
            if log_fn:
              log_fn(
                  "Seal refused: existing zst has more members than tar "
                  "(zst=%s tar=%s): %s"
                  % (len(existing_members), len(tar_members), zst_path),
                  flush=True,
              )
            return None
          if sum_member_bytes(existing_members) > sum_member_bytes(tar_members):
            if log_fn:
              log_fn(
                  "Seal refused: existing zst byte sum exceeds tar "
                  "(zst=%s tar=%s): %s"
                  % (
                      sum_member_bytes(existing_members),
                      sum_member_bytes(tar_members),
                      zst_path,
                  ),
                  flush=True,
              )
            return None
      if not verify_tar_archive_readable(tar_path, assume_write_lock_held=True):
        if log_fn:
          log_fn(
              "Seal refused: tar unreadable before compress: %s" % tar_path,
              flush=True,
          )
        return None
      zstd_compress_tar_to_file(
          tar_path,
          tmp_zst,
          num_threads,
          compress_level,
          tgz_archive_dir=tgz_archive_dir,
          yield_phase="seal",
      )
      zstd_test(tmp_zst, num_threads)
      with file_write_lock(zst_path):
        os.replace(tmp_zst, zst_path)
      sealed_member_snapshot = dict(tar_members)
  except BaseException:
    try:
      if os.path.exists(tmp_zst):
        os.remove(tmp_zst)
    except OSError:
      pass
    raise
  if log_fn:
    log_fn("Sealed archive %s -> %s" % (tar_path, zst_path), flush=True)
  invalidate_daily_archive_members_cache(zst_path)
  zst_key = normalize_daily_compressed_path(zst_path)
  if keep_uncompressed_tar:
    if log_fn:
      log_fn("Sealed archive retaining uncompressed tar: %s" % tar_path, flush=True)
  elif (
      not force_remove_uncompressed_tar
      and remaining_raw_by_gz_has_paths_on_disk(remaining_raw_by_gz, zst_key)
  ):
    if log_fn:
      log_fn(
          "Sealed archive retaining uncompressed tar (raw stats still present "
          "for day): %s" % tar_path,
          flush=True,
      )
  else:
    try:
      zstd_drop_page_cache_for_paths(tar_path)
      with file_write_lock(tar_path):
        os.remove(tar_path)
      if log_fn:
        log_fn("Sealed archive removed uncompressed tar: %s" % tar_path, flush=True)
    except OSError:
      pass
  return sealed_member_snapshot


def _get_archive_seal_worker_count(total_candidates: Any) -> Any:
  """
  Bounded worker count for parallel daily tar sealing.
  
  Args:
    total_candidates (Any): Total candidates passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _get_archive_seal_worker_count(None)  # doctest: +SKIP
  """
  if total_candidates <= 0:
    return 1
  env = os.environ.get("SYNC_ARCHIVE_SEAL_WORKERS", "").strip()
  if env:
    try:
      configured = max(1, int(env))
    except ValueError:
      configured = max(1, int(cfg.get_archive_seal_parallel_workers()))
  else:
    configured = max(1, int(cfg.get_archive_seal_parallel_workers()))
  return max(1, min(total_candidates, configured))


def _seal_one_daily_tar(
  tar_path: str,
  zst_path: str,
  gz_path: str,
  *,
  zstd_threads: Any,
  compress_level: Any,
  keep_uncompressed_tar: Any,
  log_fn: Any,
  remaining_raw_by_gz: Any,
  force_remove_uncompressed_tar: bool,
) -> None:
  """
  Internal helper to seal the one daily tar.
  
  Args:
    tar_path (str): String for tar path.
    zst_path (str): String for zst path.
    gz_path (str): String for gz path.
    zstd_threads (Any): Zstd threads passed to this helper.
    compress_level (Any): Compress level passed to this helper.
    keep_uncompressed_tar (Any): Keep uncompressed tar passed to this helper.
    log_fn (Any): Callable invoked by this helper.
    remaining_raw_by_gz (Any): Remaining raw by gz passed to this helper.
    force_remove_uncompressed_tar (bool): Whether to enable force remove
    uncompressed tar.
  
  Returns:
    None
  
  Examples:
    >>> _seal_one_daily_tar("x", "x", "x", None, None, None, None, None, True)
  """
  from hpcperfstats.dbload.lib.process_title import set_daemon_thread_title

  set_daemon_thread_title("", script_name="sync_timedb.py", role="archive-seal")
  try:
    zst_members = atomic_seal_tar_to_zst(
        tar_path,
        zst_path,
        zstd_threads,
        compress_level,
        keep_uncompressed_tar,
        log_fn=log_fn,
        remaining_raw_by_gz=remaining_raw_by_gz,
        force_remove_uncompressed_tar=force_remove_uncompressed_tar,
    )
    drop_legacy_gz_if_equivalent_to_zst(
        gz_path, zst_path, log_fn=log_fn, zst_members=zst_members,
    )
  except (subprocess.CalledProcessError, RuntimeError, TimeoutError) as exc:
    if log_fn:
      log_fn("Seal failed for %s: %s" % (tar_path, exc), flush=True)


def seal_dirty_daily_archives(
  daily_archive_dir: str,
  *,
  local_tz: Any,
  zstd_threads: Any,
  compress_level: Any,
  keep_uncompressed_tar: Any,
  idle_seconds: int,
  seal_immediately_if_dirty: bool = False,
  log_fn: Any = log_print,
  remaining_raw_by_gz: Any | None = None,
  force_remove_uncompressed_tar: bool = False,
  skip_daily_tar_paths: Any | None = None,
  only_daily_tar_paths: Any | None = None,
  only_when_no_remaining_raw: bool = False,
) -> None:
  """
  Seal every dirty ``YYYY-MM-DD.tar`` under ``daily_archive_dir`` per policy.
  
  When ``only_when_no_remaining_raw`` is true (janitor seal ticks), a dirty day
    is
  skipped while ``remaining_raw_by_gz`` still lists closed raw stats for that
    day,
  so the expensive zstd seal runs only once the day's raw stats are gone.
  
  Args:
    daily_archive_dir (str): String for daily archive dir.
    local_tz (Any): Local tz passed to this helper.
    zstd_threads (Any): Zstd threads passed to this helper.
    compress_level (Any): Compress level passed to this helper.
    keep_uncompressed_tar (Any): Keep uncompressed tar passed to this helper.
    idle_seconds (int): Integer value for idle seconds.
    seal_immediately_if_dirty (bool): Boolean flag for seal immediately if
    dirty.
    log_fn (Any): Callable invoked by this helper.
    remaining_raw_by_gz (Any | None): One of ``Any``, ``None``.
    force_remove_uncompressed_tar (bool): Whether to enable force remove
    uncompressed tar.
    skip_daily_tar_paths (Any | None): One of ``Any``, ``None``.
    only_daily_tar_paths (Any | None): One of ``Any``, ``None``.
    only_when_no_remaining_raw (bool): Boolean flag for only when no remaining
    raw.
  
  Returns:
    None
  
  Raises:
    err: Raised when ``seal_dirty_daily_archives`` hits a ``err`` failure
    path.
  
  Examples:
    >>> seal_dirty_daily_archives(0)  # doctest: +SKIP
  """
  if not daily_archive_dir or not os.path.isdir(daily_archive_dir):
    return
  today_local = datetime.now(local_tz).date()
  candidates = []
  for tar_path in sorted(iter_daily_tar_paths(daily_archive_dir)):
    if not daily_tar_path_in_maintenance_scope(
        tar_path,
        skip_daily_tar_paths=skip_daily_tar_paths,
        only_daily_tar_paths=only_daily_tar_paths,
    ):
      continue
    zst_path, gz_path = compressed_sibling_paths(tar_path)
    if not should_seal_daily_tar(
        tar_path,
        zst_path,
        idle_seconds,
        today_local,
        seal_immediately_if_dirty=seal_immediately_if_dirty,
        gz_path=gz_path,
    ):
      continue
    if only_when_no_remaining_raw:
      from hpcperfstats.dbload.lib.sync_timedb_day_raw_removal import (
          remaining_raw_blocking_day_incomplete,
      )
      blocking = remaining_raw_blocking_day_incomplete(tar_path)
      if remaining_raw_by_gz_has_paths_on_disk(blocking, zst_path):
        if log_fn:
          log_fn(
              "Post-chunk seal deferred (raw stats still present for day): %s"
              % tar_path,
              flush=True,
          )
        continue
    candidates.append((tar_path, zst_path, gz_path))
  if not candidates:
    return

  seal_kwargs = dict(
      zstd_threads=zstd_threads,
      compress_level=compress_level,
      keep_uncompressed_tar=keep_uncompressed_tar,
      log_fn=log_fn,
      remaining_raw_by_gz=remaining_raw_by_gz,
      force_remove_uncompressed_tar=force_remove_uncompressed_tar,
  )
  workers = _get_archive_seal_worker_count(len(candidates))
  if workers <= 1 or len(candidates) <= 1:
    for tar_path, zst_path, gz_path in candidates:
      _seal_one_daily_tar(tar_path, zst_path, gz_path, **seal_kwargs)
    return

  def _seal_candidate(candidate: Any) -> None:
    """
    Internal helper to seal the candidate.
    
    Args:
      candidate (Any): Candidate passed to this helper.
    
    Returns:
      None
    
    Examples:
      >>> _seal_candidate(None)  # doctest: +SKIP
    """
    tar_path, zst_path, gz_path = candidate
    _seal_one_daily_tar(tar_path, zst_path, gz_path, **seal_kwargs)

  for _candidate, _result, err in iter_bounded_thread_pool(
      candidates,
      _seal_candidate,
      max_workers=workers,
      thread_role="archive-seal",
  ):
    if err is not None:
      raise err


def _planned_migrate_action_for_legacy_gz(
  gz_path: str,
  tar_path: str,
  zst_path: str,
) -> Any:
  """
  Return the action ``migrate_one_daily_legacy_gz`` would take (no I/O locks).
  
  Args:
    gz_path (str): String for gz path.
    tar_path (str): String for tar path.
    zst_path (str): String for zst path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _planned_migrate_action_for_legacy_gz("x", "x", "x")  # doctest: +SKIP
  """
  if not os.path.isfile(gz_path):
    return MIGRATE_GZ_STATUS_SKIPPED_NO_GZ
  if os.path.isfile(zst_path) and os.path.isfile(gz_path):
    gz_contained, _, _ = compare_compressed_archive_members(gz_path, zst_path)
    if gz_contained:
      return MIGRATE_GZ_STATUS_DROPPED_ONLY
    if (
        os.path.isfile(tar_path)
        and is_daily_tar_sealed_dirty(tar_path, zst_path, gz_path)
    ):
      return MIGRATE_GZ_STATUS_CONVERTED
    return MIGRATE_GZ_STATUS_KEPT_MISMATCH
  if os.path.isfile(tar_path):
    return MIGRATE_GZ_STATUS_CONVERTED
  if os.path.isfile(gz_path):
    return MIGRATE_GZ_STATUS_CONVERTED
  return MIGRATE_GZ_STATUS_SKIPPED_NO_GZ


def _migrate_one_daily_legacy_gz_locked(
  gz_path: str,
  tar_path: str,
  zst_path: str,
  *,
  zstd_threads: Any,
  compress_level: Any,
  keep_uncompressed_tar: Any,
  remaining_raw_by_gz: Any,
  force_remove_uncompressed_tar: bool,
  decompress_tmp_dir: str,
  log_fn: Any,
) -> Any:
  """
  Migrate one day while the caller holds the write lock on tar or gz.
  
  Args:
    gz_path (str): String for gz path.
    tar_path (str): String for tar path.
    zst_path (str): String for zst path.
    zstd_threads (Any): Zstd threads passed to this helper.
    compress_level (Any): Compress level passed to this helper.
    keep_uncompressed_tar (Any): Keep uncompressed tar passed to this helper.
    remaining_raw_by_gz (Any): Remaining raw by gz passed to this helper.
    force_remove_uncompressed_tar (bool): Whether to enable force remove
    uncompressed tar.
    decompress_tmp_dir (str): String for decompress tmp dir.
    log_fn (Any): Callable invoked by this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _migrate_one_daily_legacy_gz_locked(0)  # doctest: +SKIP
  """
  if not os.path.isfile(gz_path):
    return MIGRATE_GZ_STATUS_SKIPPED_NO_GZ

  def _seal_and_drop(seal_tar_path: Any | None = None) -> Any:
    """
    Internal helper to seal the and drop.
    
    Args:
      seal_tar_path (Any | None): One of ``Any``, ``None``.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _seal_and_drop(None)  # doctest: +SKIP
    """
    if seal_tar_path is None:
      seal_tar_path = tar_path
    if not os.path.isfile(seal_tar_path):
      return MIGRATE_GZ_STATUS_FAILED
    try:
      zst_members = atomic_seal_tar_to_zst(
          seal_tar_path,
          zst_path,
          zstd_threads,
          compress_level,
          keep_uncompressed_tar,
          log_fn=log_fn,
          remaining_raw_by_gz=remaining_raw_by_gz,
          force_remove_uncompressed_tar=force_remove_uncompressed_tar,
      )
    except (subprocess.CalledProcessError, RuntimeError) as exc:
      if log_fn:
        log_fn(
            "Migration seal failed for %s: %s" % (seal_tar_path, exc),
            flush=True,
        )
      return MIGRATE_GZ_STATUS_FAILED
    if os.path.isfile(gz_path):
      drop_legacy_gz_if_equivalent_to_zst(
          gz_path,
          zst_path,
          log_fn=log_fn,
          zst_members=zst_members,
      )
    if os.path.isfile(gz_path):
      return MIGRATE_GZ_STATUS_KEPT_MISMATCH
    return MIGRATE_GZ_STATUS_CONVERTED

  if os.path.isfile(zst_path):
    gz_contained, gz_members, zst_members = compare_compressed_archive_members(
        gz_path, zst_path,
    )
    if gz_contained and os.path.isfile(gz_path):
      drop_legacy_gz_if_equivalent_to_zst(
          gz_path,
          zst_path,
          log_fn=log_fn,
          gz_members=gz_members,
          zst_members=zst_members,
      )
      if os.path.isfile(gz_path):
        return MIGRATE_GZ_STATUS_KEPT_MISMATCH
      return MIGRATE_GZ_STATUS_DROPPED_ONLY
    if (
        os.path.isfile(tar_path)
        and is_daily_tar_sealed_dirty(tar_path, zst_path, gz_path)
    ):
      return _seal_and_drop()
    if log_fn:
      log_fn(
          "Keeping legacy gzip (zst member mismatch or no re-sealable tar): %s"
          % gz_path,
          flush=True,
      )
    return MIGRATE_GZ_STATUS_KEPT_MISMATCH

  if os.path.isfile(tar_path):
    return _seal_and_drop()

  if os.path.isfile(gz_path):
    # We already hold a write lock on gz_path in migrate_one_daily_legacy_gz().
    # Avoid re-locking gz_path inside decompress_compressed_to_tar(remove_compressed=True),
    # which can fail under non-reentrant advisory lock semantics.
    temp_tar_path = tar_path
    remove_temp_tar_after = False
    if decompress_tmp_dir:
      try:
        os.makedirs(decompress_tmp_dir, exist_ok=True)
      except OSError as exc:
        if log_fn:
          log_fn(
              "Migration temp directory unavailable %s: %s"
              % (decompress_tmp_dir, exc),
              flush=True,
          )
        return MIGRATE_GZ_STATUS_FAILED
      fd, temp_tar_path = tempfile.mkstemp(
          prefix="%s.migrate." % os.path.basename(tar_path),
          suffix=".tar",
          dir=decompress_tmp_dir,
      )
      os.close(fd)
      remove_temp_tar_after = True
    if not decompress_compressed_to_tar(
        gz_path,
        temp_tar_path,
        zstd_threads,
        remove_compressed=False,
    ):
      if log_fn:
        log_fn(
            "Migration decompress failed for legacy gzip: %s" % gz_path,
            flush=True,
        )
      if remove_temp_tar_after:
        try:
          if os.path.exists(temp_tar_path):
            os.remove(temp_tar_path)
        except OSError:
          pass
      return MIGRATE_GZ_STATUS_FAILED
    try:
      if os.path.isfile(gz_path):
        os.remove(gz_path)
    except OSError as exc:
      if log_fn:
        log_fn(
            "Migration failed removing legacy gzip after decompress %s: %s"
            % (gz_path, exc),
            flush=True,
        )
      if remove_temp_tar_after:
        try:
          if os.path.exists(temp_tar_path):
            os.remove(temp_tar_path)
        except OSError:
          pass
      return MIGRATE_GZ_STATUS_FAILED
    status = _seal_and_drop(temp_tar_path)
    if remove_temp_tar_after:
      try:
        if os.path.exists(temp_tar_path):
          os.remove(temp_tar_path)
      except OSError:
        pass
    return status

  return MIGRATE_GZ_STATUS_SKIPPED_NO_GZ


def migrate_one_daily_legacy_gz(
  gz_path: str,
  *,
  zstd_threads: Any,
  compress_level: Any,
  keep_uncompressed_tar: Any,
  remaining_raw_by_gz: Any | None = None,
  force_remove_uncompressed_tar: bool = False,
  decompress_tmp_dir: Any | None = None,
  log_fn: Any = log_print,
  lock_timeout_seconds: int = 0,
  dry_run: bool = False,
) -> Any:
  """
  Convert one legacy ``.tar.gz`` to canonical ``.tar.zst`` (or drop redundant.
  
    gzip).
  
  Returns a status string (see ``MIGRATE_GZ_STATUS_*``). When
    ``lock_timeout_seconds``
  is 0 and the advisory lock is contended, returns ``skipped_locked`` without
    waiting.
  
  Args:
    gz_path (str): String for gz path.
    zstd_threads (Any): Zstd threads passed to this helper.
    compress_level (Any): Compress level passed to this helper.
    keep_uncompressed_tar (Any): Keep uncompressed tar passed to this helper.
    remaining_raw_by_gz (Any | None): One of ``Any``, ``None``.
    force_remove_uncompressed_tar (bool): Whether to enable force remove
    uncompressed tar.
    decompress_tmp_dir (Any | None): One of ``Any``, ``None``.
    log_fn (Any): Callable invoked by this helper.
    lock_timeout_seconds (int): Integer value for lock timeout seconds.
    dry_run (bool): Boolean flag for dry run.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> migrate_one_daily_legacy_gz(0)  # doctest: +SKIP
  """
  tar_path = daily_tar_path_from_compressed(gz_path)
  zst_path, _gz_path = compressed_sibling_paths(tar_path)

  if dry_run:
    action = _planned_migrate_action_for_legacy_gz(gz_path, tar_path, zst_path)
    if log_fn and action != MIGRATE_GZ_STATUS_SKIPPED_NO_GZ:
      log_fn(
          "Dry-run %s: %s -> %s" % (action, gz_path, zst_path),
          flush=True,
      )
    return action

  if not os.path.isfile(gz_path):
    return MIGRATE_GZ_STATUS_SKIPPED_NO_GZ

  from hpcperfstats.dbload.lib.sync_timedb_day_close_cooperation import (
      daily_tar_janitor_mutation_should_defer,
      log_janitor_day_close_defer,
  )

  tgz_archive_dir = cfg.get_daily_archive_dir_path() or ""
  defer, defer_reason = daily_tar_janitor_mutation_should_defer(
      tar_path,
      tgz_archive_dir=tgz_archive_dir,
      disqualified_daily_tars=set(),
      phase="migrate_legacy_gz",
  )
  if defer:
    if log_fn:
      log_janitor_day_close_defer(
          tar_path,
          phase="migrate_legacy_gz",
          reason=defer_reason,
          log_fn=log_fn,
      )
    return MIGRATE_GZ_STATUS_SKIPPED_LOCKED

  primary_path = tar_path if os.path.isfile(tar_path) else gz_path
  try:
    with file_write_lock(primary_path, timeout_seconds=lock_timeout_seconds):
      return _migrate_one_daily_legacy_gz_locked(
          gz_path,
          tar_path,
          zst_path,
          zstd_threads=zstd_threads,
          compress_level=compress_level,
          keep_uncompressed_tar=keep_uncompressed_tar,
          remaining_raw_by_gz=remaining_raw_by_gz,
          force_remove_uncompressed_tar=force_remove_uncompressed_tar,
          decompress_tmp_dir=decompress_tmp_dir,
          log_fn=log_fn,
      )
  except TimeoutError:
    if log_fn:
      log_fn(
          "Skipping migration (lock contended): %s" % primary_path,
          flush=True,
      )
    return MIGRATE_GZ_STATUS_SKIPPED_LOCKED


def _migrate_summary_init() -> Any:
  """
  Internal helper to handle migrate summary init.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _migrate_summary_init()  # doctest: +SKIP
  """
  return {
      MIGRATE_GZ_STATUS_CONVERTED: 0,
      MIGRATE_GZ_STATUS_DROPPED_ONLY: 0,
      MIGRATE_GZ_STATUS_SKIPPED_LOCKED: 0,
      MIGRATE_GZ_STATUS_SKIPPED_NO_GZ: 0,
      MIGRATE_GZ_STATUS_FAILED: 0,
      MIGRATE_GZ_STATUS_KEPT_MISMATCH: 0,
      MIGRATE_GZ_STATUS_PLANNED: 0,
  }


def _migrate_summary_bump(summary: Any, status: Any) -> None:
  """
  Internal helper to handle migrate summary bump.
  
  Args:
    summary (Any): Summary passed to this helper.
    status (Any): Status passed to this helper.
  
  Returns:
    None
  
  Examples:
    >>> _migrate_summary_bump(None, None)  # doctest: +SKIP
  """
  if status in summary:
    summary[status] = summary.get(status, 0) + 1


def migrate_legacy_daily_gz_archives(
  daily_archive_dir: str,
  *,
  zstd_threads: Any | None = None,
  compress_level: Any | None = None,
  keep_uncompressed_tar: Any | None = None,
  remaining_raw_by_gz: Any | None = None,
  force_remove_uncompressed_tar: bool = False,
  decompress_tmp_dir: Any | None = None,
  log_fn: Any = log_print,
  lock_timeout_seconds: int = 0,
  dry_run: bool = False,
  since_date: Any | None = None,
  limit: Any | None = None,
  workers: Any | None = None,
) -> Any:
  """
  Migrate all legacy ``.tar.gz`` files under ``daily_archive_dir`` to.
  
    ``.tar.zst``.
  
  Returns a summary dict keyed by ``MIGRATE_GZ_STATUS_*`` counts plus
    ``gz_remaining``.
  
  Args:
    daily_archive_dir (str): String for daily archive dir.
    zstd_threads (Any | None): One of ``Any``, ``None``.
    compress_level (Any | None): One of ``Any``, ``None``.
    keep_uncompressed_tar (Any | None): One of ``Any``, ``None``.
    remaining_raw_by_gz (Any | None): One of ``Any``, ``None``.
    force_remove_uncompressed_tar (bool): Whether to enable force remove
    uncompressed tar.
    decompress_tmp_dir (Any | None): One of ``Any``, ``None``.
    log_fn (Any): Callable invoked by this helper.
    lock_timeout_seconds (int): Integer value for lock timeout seconds.
    dry_run (bool): Boolean flag for dry run.
    since_date (Any | None): One of ``Any``, ``None``.
    limit (Any | None): One of ``Any``, ``None``.
    workers (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Raises:
    err: Raised when ``migrate_legacy_daily_gz_archives`` hits a ``err``
    failure path.
  
  Examples:
    >>> migrate_legacy_daily_gz_archives(0)  # doctest: +SKIP
  """
  check_archive_migration_prerequisites()
  if zstd_threads is None:
    zstd_threads = get_archive_zstd_thread_count()
  if compress_level is None:
    compress_level = cfg.get_archive_zstd_level()
  if keep_uncompressed_tar is None:
    keep_uncompressed_tar = cfg.get_archive_keep_uncompressed_tar()

  gz_paths = []
  for gz_path in iter_daily_gz_paths(daily_archive_dir):
    archive_date = parse_archive_date_from_daily_gz_path(gz_path)
    if since_date is not None and archive_date is not None:
      if archive_date < since_date:
        continue
    gz_paths.append(gz_path)
    if limit is not None and len(gz_paths) >= int(limit):
      break

  summary = _migrate_summary_init()
  if not gz_paths:
    summary["gz_remaining"] = 0
    return summary

  migrate_kwargs = dict(
      zstd_threads=zstd_threads,
      compress_level=compress_level,
      keep_uncompressed_tar=keep_uncompressed_tar,
      remaining_raw_by_gz=remaining_raw_by_gz,
      force_remove_uncompressed_tar=force_remove_uncompressed_tar,
      decompress_tmp_dir=decompress_tmp_dir,
      log_fn=log_fn,
      lock_timeout_seconds=lock_timeout_seconds,
      dry_run=dry_run,
  )

  worker_count = workers
  if worker_count is None:
    worker_count = _get_archive_seal_worker_count(len(gz_paths))
  worker_count = max(1, min(int(worker_count), len(gz_paths)))

  def _run_one(path: str) -> Any:
    """
    Internal helper to run the one.
    
    Args:
      path (str): String for path.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> _run_one("x")  # doctest: +SKIP
    """
    if dry_run:
      tar_path = daily_tar_path_from_compressed(path)
      zst_path, _ = compressed_sibling_paths(tar_path)
      return _planned_migrate_action_for_legacy_gz(path, tar_path, zst_path)
    return migrate_one_daily_legacy_gz(path, **migrate_kwargs)

  if worker_count <= 1 or len(gz_paths) <= 1:
    for gz_path in gz_paths:
      _migrate_summary_bump(summary, _run_one(gz_path))
  else:
    for gz_path, result, err in iter_bounded_thread_pool(
        gz_paths,
        _run_one,
        max_workers=worker_count,
    ):
      if err is not None:
        raise err
      _migrate_summary_bump(summary, result)

  remaining = sum(1 for _ in iter_daily_gz_paths(daily_archive_dir))
  summary["gz_remaining"] = remaining
  return summary


def filter_files_to_add_to_archive(
  stats_files: Any,
  existing_members: Any,
  debug: bool = False,
) -> Any:
  """
  Return list of stats file paths that are not already in archive with same.
  
    size.
  
  Args:
    stats_files (Any): Iterable of filesystem paths as strings.
    existing_members (Any): Iterable of filesystem paths as strings.
    debug (bool): Boolean flag for debug.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> filter_files_to_add_to_archive(None, None, True)  # doctest: +SKIP
  """
  to_add = []
  for path in stats_files:
    member_name = get_tar_member_name(path)
    if member_name not in existing_members:
      to_add.append(path)
      continue
    try:
      file_size = os.path.getsize(path)
    except OSError:
      to_add.append(path)
      continue
    if file_size != existing_members[member_name]:
      to_add.append(path)
    elif debug:
      log_print("file %s found in archive, skipping" % path)
  return to_add


def get_verified_files_to_remove(
  stats_files: Any,
  existing_members: Any,
) -> Any:
  """
  Return list of stats file paths that exist in archive with same size (safe to.
  
    remove).
  
  Args:
    stats_files (Any): Iterable of filesystem paths as strings.
    existing_members (Any): Iterable of filesystem paths as strings.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> get_verified_files_to_remove(None, None)  # doctest: +SKIP
  """
  to_remove = []
  for path in stats_files:
    member_name = get_tar_member_name(path)
    if member_name not in existing_members:
      continue
    try:
      if os.path.getsize(path) == existing_members[member_name]:
        to_remove.append(path)
    except OSError:
      pass
  return to_remove


def get_stats_chunk(stats_files: Any, chunk_index: Any, chunk_size: int) -> Any:
  """
  Return slice of stats_files for chunk chunk_index (0-based).
  
  Args:
    stats_files (Any): Iterable of filesystem paths as strings.
    chunk_index (Any): Chunk index passed to this helper.
    chunk_size (int): Integer value for chunk size.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> get_stats_chunk(None, None, 0)  # doctest: +SKIP
  """
  start = chunk_index * chunk_size
  end = (chunk_index + 1) * chunk_size
  return stats_files[start:end]


def stats_file_is_active_segment(stats_path: str) -> Any:
  """
  Return True if ``stats_path`` is still being appended by listend.
  
  On ``$`` rotation, listend creates a new ``current`` and hard-links it to an
  epoch-named file; both names refer to the same inode until the next ``$``,
  when ``current`` is unlinked from that inode. Only then is the epoch file a
  complete, stable segment. Same-inode-as-``current`` means still active.
  
  Discovery uses GNU find inode maps (see ``sync_timedb_stats_find``); this
  helper remains for single-path checks and unit tests.
  
  Args:
    stats_path (str): String for stats path.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> stats_file_is_active_segment("x")  # doctest: +SKIP
  """
  host_dir = os.path.dirname(stats_path)
  current_path = os.path.join(host_dir, "current")
  try:
    if not os.path.isfile(current_path):
      return False
    return os.path.samefile(stats_path, current_path)
  except OSError:
    return False


def collect_stats_files_in_range(
  directory: Any,
  startdate: Any,
  enddate: Any,
  host_name_ext: Any,
  host_scan_hints: Any | None = None,
  force_full_scan: bool = False,
  log_fn: Any | None = None,
  *,
  newest_first: bool = False,
  mtime_days: Any | None = None,
) -> Any:
  """
  Discover stats files under ``archive_dir`` via GNU find ``-printf``.
  
  Skips the live segment: epoch files whose inode matches host ``current`` are
  omitted so sync does not race with listend appends.
  
  When ``mtime_days`` is a positive int, find uses ``-mtime -N`` (incremental
  rescan). When ``None`` (or ``force_full_scan``), the full archive ages are
  scanned. ``host_scan_hints`` still tracks ``__rescan_count__`` for callers;
  per-host dir-mtime skip is retired (find is cheap enough).
  
  When startdate is ``'backlog'`` or ``'current'``, every eligible file is
    returned
  (no date filtering). Otherwise files are included if mtime or filename epoch
  falls in (startdate - 1 day, enddate]. Returns paths sorted oldest-first
  unless ``newest_first``. Empty ``host_name_ext`` returns ``[]``.
  
  Args:
    directory (Any): Directory passed to this helper.
    startdate (Any): Time value (``datetime``, ISO string, sentinel, or
    ``None``).
    enddate (Any): Time value (``datetime``, ISO string, sentinel, or
    ``None``).
    host_name_ext (Any): Host name ext passed to this helper.
    host_scan_hints (Any | None): One of ``Any``, ``None``.
    force_full_scan (bool): Whether to enable force full scan.
    log_fn (Any | None): One of ``Any``, ``None``.
    newest_first (bool): Boolean flag for newest first.
    mtime_days (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> collect_stats_files_in_range(0)  # doctest: +SKIP
  """
  from hpcperfstats.dbload.lib.sync_timedb_stats_find import discover_stats_records

  suffix = (host_name_ext or "").strip()
  if not suffix:
    return []
  effective_mtime = None if force_full_scan else mtime_days
  collect_t0 = time.monotonic()
  records = discover_stats_records(
      directory,
      startdate,
      enddate,
      host_name_ext,
      mtime_days=effective_mtime,
      newest_first=newest_first,
      log_fn=log_fn,
  )
  paths = [rec.path for rec in records]
  if log_fn is not None:
    log_fn(
        "collect_stats_files_in_range: find paths=%d elapsed_s=%.3f mtime_days=%s"
        % (
            len(paths),
            time.monotonic() - collect_t0,
            "None" if effective_mtime is None else str(int(effective_mtime)),
        ),
        flush=True,
    )
  return paths


def rescan_pending_stats_files(
  directory: Any,
  startdate: Any,
  enddate: Any,
  host_name_ext: Any,
  processed_files: Any,
  host_scan_hints: Any | None = None,
  full_rescan_every: Any | None = None,
  startup_closed_paths: Any | None = None,
  *,
  force_snapshot_paths: bool = False,
  log_fn: Any | None = None,
  progress_interval: int = 5000,
  newest_first: bool = False,
  mtime_days: Any | None = None,
  prefer_incremental: bool = False,
  pending_pressure_n: Any | None = None,
) -> Any:
  """
  Return ordered files still pending after excluding processed files.
  
  Args:
    directory (Any): Directory passed to this helper.
    startdate (Any): Time value (``datetime``, ISO string, sentinel, or
    ``None``).
    enddate (Any): Time value (``datetime``, ISO string, sentinel, or
    ``None``).
    host_name_ext (Any): Host name ext passed to this helper.
    processed_files (Any): Iterable of filesystem paths as strings.
    host_scan_hints (Any | None): One of ``Any``, ``None``.
    full_rescan_every (Any | None): One of ``Any``, ``None``.
    startup_closed_paths (Any | None): One of ``Any``, ``None``.
    force_snapshot_paths (bool): Whether to enable force snapshot paths.
    log_fn (Any | None): One of ``Any``, ``None``.
    progress_interval (int): Integer value for progress interval.
    newest_first (bool): Boolean flag for newest first.
    mtime_days (Any | None): One of ``Any``, ``None``.
    prefer_incremental (bool): Boolean flag for prefer incremental.
    pending_pressure_n (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> rescan_pending_stats_files(0)  # doctest: +SKIP
  """
  if full_rescan_every is None:
    full_rescan_every = cfg.get_sync_ingest_rescan_full_every()
  if mtime_days is None:
    mtime_days = cfg.get_sync_ingest_rescan_mtime_days()
  should_force_full = True
  if isinstance(host_scan_hints, dict):
    should_force_full = (
        int(host_scan_hints.get("__rescan_count__", 0)) % max(1, int(full_rescan_every)) == 0
    )
    host_scan_hints["__rescan_count__"] = int(
        host_scan_hints.get("__rescan_count__", 0)
    ) + 1
  # Under high pending pressure, skip periodic full-age find (mtime incremental
  # is enough; full find is cheap but still wasteful on every Nth chunk).
  if prefer_incremental:
    should_force_full = False
  elif pending_pressure_n is not None:
    try:
      pressure = int(pending_pressure_n)
    except (TypeError, ValueError):
      pressure = 0
    queue_max = int(cfg.get_sync_ingest_queue_max_size())
    if pressure >= max(1, queue_max // 2):
      should_force_full = False
  use_snapshot = (
      (should_force_full or force_snapshot_paths)
      and startup_closed_paths is not None
      and startdate in ("backlog", "current", None, "")
      and enddate in (None, "")
  )
  if use_snapshot:
    discovered_files = list(startup_closed_paths)
    # Idle refill (force_snapshot_paths=True) must merge a live find with the
    # coordinator snapshot. Snapshot-only freezes discovery on a stale
    # closed_paths list while newly closed segments accrue (hpcperfstats04:
    # closed_paths=5382 frozen, pending=0 for 13h+). Non-idle should_force_full
    # alone stays snapshot-only (T2 fast-path).
    if force_snapshot_paths:
      find_files = collect_stats_files_in_range(
          directory,
          startdate,
          enddate,
          host_name_ext,
          host_scan_hints=host_scan_hints,
          force_full_scan=should_force_full,
          newest_first=newest_first,
          mtime_days=None if should_force_full else mtime_days,
          log_fn=log_fn,
      )
      seen = set(discovered_files)
      find_only_n = 0
      for path in find_files:
        if path in seen:
          continue
        discovered_files.append(path)
        seen.add(path)
        find_only_n += 1
      if log_fn is not None and find_only_n:
        log_fn(
            "idle_rescan_merge snapshot_n=%d find_n=%d find_only_n=%d "
            "merged_n=%d force_full=%s"
            % (
                len(startup_closed_paths),
                len(find_files),
                find_only_n,
                len(discovered_files),
                "yes" if should_force_full else "no",
            ),
            flush=True,
        )
    if newest_first:
      discovered_files = sort_pending_stats_paths_oldest_first(
          discovered_files,
          newest_first=True,
      )
  else:
    discovered_files = collect_stats_files_in_range(
        directory,
        startdate,
        enddate,
        host_name_ext,
        host_scan_hints=host_scan_hints,
        force_full_scan=should_force_full,
        newest_first=newest_first,
        mtime_days=None if should_force_full else mtime_days,
        log_fn=log_fn,
    )
  if isinstance(processed_files, set):
    exclude = processed_files
  else:
    exclude = set(processed_files or [])
  total = len(discovered_files)
  if total == 0:
    return []
  filter_t0 = time.monotonic()
  result = []
  for index, path in enumerate(discovered_files):
    if path not in exclude:
      result.append(path)
    if (
        log_fn is not None
        and total >= 10000
        and progress_interval > 0
        and (index + 1) % progress_interval == 0
    ):
      log_fn(
          "pending rescan progress filtered_n=%d/%d elapsed_s=%.1f"
          % (
              index + 1,
              total,
              time.monotonic() - filter_t0,
          ),
          flush=True,
      )
  return result


def cap_pending_stats_file_list(
  paths: Any,
  max_size: int,
  log_fn: Any = log_print,
  *,
  newest_first: bool = False,
) -> Any:
  """
  Return ordered pending paths capped to ``max_size`` (memory bound).
  
  When truncating, newer paths are dropped and the oldest ``max_size`` entries
  are retained by default. Newest-first mode retains the newest paths at the
  dispatch head.
  
  Args:
    paths (Any): Iterable of filesystem paths as strings.
    max_size (int): Integer value for max size.
    log_fn (Any): Callable invoked by this helper.
    newest_first (bool): Boolean flag for newest first.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> cap_pending_stats_file_list(None, 0, None, True)  # doctest: +SKIP
  """
  max_size = max(1, int(max_size))
  if len(paths) <= max_size:
    return list(paths)
  if log_fn is not None:
    log_fn(
        "Pending stats file list truncated pending=%d max=%d"
        % (len(paths), max_size),
        flush=True,
    )
  if not newest_first:
    return list(paths[:max_size])
  ordered = sort_pending_stats_paths_oldest_first(paths)
  return list(reversed(ordered[-max_size:]))


def build_archive_mapping(
  files_to_be_archived: Any,
  tgz_archive_dir: str,
  parse_first_ts_fn: Any | None = None,
  first_timestamp_by_path: Any | None = None,
) -> Any:
  """
  Group stats file paths by daily archive path (``YYYY-MM-DD.tar.zst``).
  
  Uses parse_first_ts_fn to get timestamp from each file. Files with no
  parseable timestamp are skipped. Today's files are included (closed
  segments only reach this list; active segments are filtered earlier).
  
  Args:
    files_to_be_archived (Any): Files to be archived passed to this helper.
    tgz_archive_dir (str): String for tgz archive dir.
    parse_first_ts_fn (Any | None): One of ``Any``, ``None``.
    first_timestamp_by_path (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> build_archive_mapping(None, "x", None, None)  # doctest: +SKIP
  """
  if parse_first_ts_fn is None:
    parse_first_ts_fn = parse_first_timestamp_line
  ar_file_mapping = {}
  skipped_no_ts = 0
  skipped_samples = []
  for stats_fname in files_to_be_archived:
    precomputed_ts = None
    if first_timestamp_by_path:
      precomputed_ts = first_timestamp_by_path.get(stats_fname)
    if precomputed_ts is not None:
      t = precomputed_ts
      _jid = _host = None
    else:
      t = _read_first_timestamp_from_stats_file(stats_fname, parse_first_ts_fn)
    if t is None:
      skipped_no_ts += 1
      if len(skipped_samples) < 5:
        skipped_samples.append(os.path.basename(str(stats_fname)))
      continue
    file_date = datetime.fromtimestamp(float(t))
    archive_fname = daily_compressed_path_for_date(tgz_archive_dir, file_date)
    if archive_fname not in ar_file_mapping:
      ar_file_mapping[archive_fname] = []
    ar_file_mapping[archive_fname].append(stats_fname)
  if skipped_no_ts:
    log_print(
        "Unable to find first timestamp in %d path(s), skipping archiving "
        "sample=%s"
        % (skipped_no_ts, ",".join(skipped_samples) or "-"),
        flush=True,
    )
  if skipped_no_ts and not ar_file_mapping:
    log_print(
        "No files added to archive mapping (%d skipped: no timestamp)"
        % skipped_no_ts
    )
  return ar_file_mapping


def collect_first_timestamps_by_path(
  files_to_be_archived: Any,
  parse_first_ts_fn: Any | None = None,
) -> Any:
  """
  Return {path: first_timestamp_str} for files with parseable first timestamp.
  
  Args:
    files_to_be_archived (Any): Files to be archived passed to this helper.
    parse_first_ts_fn (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> collect_first_timestamps_by_path(None, None)  # doctest: +SKIP
  """
  if parse_first_ts_fn is None:
    parse_first_ts_fn = parse_first_timestamp_line
  timestamps = {}
  for stats_fname in files_to_be_archived:
    t = _read_first_timestamp_from_stats_file(stats_fname, parse_first_ts_fn)
    if t is not None:
      timestamps[stats_fname] = t
  return timestamps
