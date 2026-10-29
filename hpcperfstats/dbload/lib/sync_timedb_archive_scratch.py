"""
Remove dead scratch files and staging directories left by killed writers.

Union rebuild, dedupe, repair, sealed restore and seal each write a
``<day>.tar.<kind>.tmp`` sibling; union rebuild, pax convert and tar repair
extract into an ``hps_*`` staging directory; every sidecar write goes through
an ``.atomic.*`` or ``*.json.tmp`` temp file. They remove these when they
finish, or (the ``<day>.tar.<kind>.tmp`` files) when the next attempt for that
day starts. A process killed mid-write, or a day that later closed another
way, strands them forever (hpcperfstats04 2026-10-29: 94 GB of ``merge.tmp``
from August and September). The idle reconstruct pass sweeps them.

Safety copies (``.corrupt.*``, ``.pre-restore.*``, salvage backups, the
unparsable-raw quarantine), the real ``.tar`` / ``.tar.zst``, sidecar data,
lock sidecars and every other name never match.

Attributes:
  SCRATCH_NAME_RE: ``<day>.tar.<kind>.tmp`` with ``kind`` one of ``merge``,
    ``dedupe``, ``repair``, ``decomp`` or ``zst`` (needs the day's locks).
  STAGING_DIR_RE: ``hps_union_extract_``, ``hps_pax_convert_`` or
    ``hps_tar_repair_`` plus the 8 characters ``tempfile.mkdtemp`` appends.
  WRITE_TEMP_RE: A sidecar atomic-write temp: ``.atomic.<8 chars><name>``
    (``tempfile.mkstemp`` in ``sync_timedb_persistence``) or ``*.json.tmp``.
  PROTECTED_SUBDIRS: ``.sync_timedb_*`` directories the sweep never enters.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import stat
import time
from typing import Any

from hpcperfstats.dbload.lib import conf_parser as cfg
from hpcperfstats.dbload.lib.file_locking import try_file_write_lock
from hpcperfstats.dbload.lib.print_utils import log_print
from hpcperfstats.dbload.lib.sync_timedb_archive_members_coord import (
  archive_append_inflight_for_day,
  daily_tar_restore_in_progress_for_day,
)
from hpcperfstats.dbload.lib.sync_timedb_negative_states import (
  effective_remediation_mode,
)

SCRATCH_NAME_RE = re.compile(
  r"^(\d{4}-\d{2}-\d{2})\.tar\.(?:merge|dedupe|repair|decomp|zst)\.tmp$"
)
STAGING_DIR_RE = re.compile(
  r"^hps_(?:union_extract|pax_convert|tar_repair)_[A-Za-z0-9_]{8}$"
)
WRITE_TEMP_RE = re.compile(
  r"^(?:\.atomic\.[A-Za-z0-9_]{8}.+|[\w.-]+\.json\.tmp)$"
)
PROTECTED_SUBDIRS = frozenset(
  {".sync_timedb_unparsable_raw", ".sync_timedb_migration_backup"}
)


def _scratch_scan_dirs(*bases: str) -> list[str]:
  """
  Return the directories to sweep: each base and its sidecar subdirectories.

  Args:
    *bases (str): Daily archive dir and archive dir; empty or missing ones
      are skipped and duplicates collapse.

  Returns:
    list[str]: Each existing base followed by its immediate ``.sync_timedb_*``
    subdirectories, excluding ``PROTECTED_SUBDIRS`` and symlinks.

  Examples:
    >>> _scratch_scan_dirs("", "/nonexistent")
    []
  """
  found: list[str] = []
  for base in dict.fromkeys(os.path.normpath(b) for b in bases if b):
    if not os.path.isdir(base):
      continue
    found.append(base)
    try:
      names = sorted(os.listdir(base))
    except OSError:
      continue
    for name in names:
      sub = os.path.join(base, name)
      if (
        name.startswith(".sync_timedb_")
        and name not in PROTECTED_SUBDIRS
        and os.path.isdir(sub)
        and not os.path.islink(sub)
      ):
        found.append(sub)
  return found


def _dead_scratch_idle_and_bytes(
  path: str, now_s: float, max_idle_s: float
) -> tuple[float, int] | None:
  """
  Return ``(idle_seconds, bytes)`` when ``path`` has been idle long enough.

  A regular file is idle since its own mtime. A real directory is idle since
  the newest mtime of the directory or anything beneath it, so a live
  rebuild that is still extracting members keeps it young. Symlinks and
  anything else are never dead.

  Args:
    path (str): Candidate scratch path (a symlink is not followed).
    now_s (float): Current epoch seconds.
    max_idle_s (float): Idle seconds a dead path must have reached.

  Returns:
    tuple[float, int] | None: Idle seconds and total file bytes, or None when
    the path is missing, not a regular file or directory, or too recent.

  Examples:
    >>> _dead_scratch_idle_and_bytes("/nonexistent/x", 0.0, 1.0) is None
    True
  """
  try:
    info = os.lstat(path)
  except OSError:
    return None
  newest = info.st_mtime
  size = info.st_size
  if stat.S_ISDIR(info.st_mode):
    size = 0
    for root, dirs, files in os.walk(path):
      for entry in (*dirs, *files):
        try:
          sub = os.lstat(os.path.join(root, entry))
        except OSError:
          continue
        newest = max(newest, sub.st_mtime)
        size += sub.st_size if stat.S_ISREG(sub.st_mode) else 0
        if now_s - newest < max_idle_s:
          return None
  elif not stat.S_ISREG(info.st_mode):
    return None
  return (now_s - newest, size) if now_s - newest >= max_idle_s else None


def sweep_dead_archive_scratch(
  daily_dir: str,
  *,
  archive_dir: str = "",
  log_fn: Any = log_print,
  now: float | None = None,
) -> int:
  """
  Remove scratch files and staging directories that no writer can still use.

  Sweeps ``daily_dir`` and ``archive_dir`` plus their immediate
  ``.sync_timedb_*`` sidecar directories (never ``PROTECTED_SUBDIRS``). A
  ``SCRATCH_NAME_RE`` file is dead when it has been unmodified for
  ``sync_archive_scratch_max_idle_hours``, no append or restore is in flight
  for its day, and both the day's tar write lock and its restore lock are
  free (the age is re-checked while holding them). A ``STAGING_DIR_RE``
  directory or ``WRITE_TEMP_RE`` file has no day in its name, so it is dead
  when nothing at or under it was modified for that long.
  ``sync_dead_end_remediation_mode = report`` only logs what would go.

  Args:
    daily_dir (str): Daily archive directory.
    archive_dir (str): Archive root holding the sidecars; empty skips it.
    log_fn (Any): Logger callable; ``None`` uses ``log_print``.
    now (float | None): Epoch seconds override for tests; ``None`` is now.

  Returns:
    int: Number of files and directories removed (0 in report mode).

  Examples:
    >>> sweep_dead_archive_scratch("/nonexistent/daily")
    0
  """
  max_idle_s = cfg.get_sync_archive_scratch_max_idle_hours() * 3600
  if max_idle_s <= 0:
    return 0
  emit = log_fn or log_print
  now_s = time.time() if now is None else float(now)
  apply = effective_remediation_mode() == "apply"
  removed = 0
  for directory in _scratch_scan_dirs(daily_dir, archive_dir):
    try:
      names = sorted(os.listdir(directory))
    except OSError:
      continue
    for name in names:
      match = SCRATCH_NAME_RE.match(name)
      is_dir = STAGING_DIR_RE.match(name) is not None
      if match is None and not is_dir and not WRITE_TEMP_RE.match(name):
        continue
      path = os.path.join(directory, name)
      if _dead_scratch_idle_and_bytes(path, now_s, max_idle_s) is None:
        continue
      day = match.group(1) if match else ""
      if day and (
        archive_append_inflight_for_day(day)
        or daily_tar_restore_in_progress_for_day(day)
      ):
        continue
      tar_path = os.path.join(directory, f"{day}.tar")
      try:
        with contextlib.ExitStack() as locks:
          if day:
            locks.enter_context(try_file_write_lock(tar_path))
            locks.enter_context(try_file_write_lock(f"{tar_path}.decomp"))
          dead = _dead_scratch_idle_and_bytes(path, now_s, max_idle_s)
          if dead is None:
            continue
          detail = f"path={path} bytes={dead[1]} idle_h={dead[0] / 3600:.1f}"
          if not apply:
            emit(f"archive scratch would remove {detail}", flush=True)
            continue
          if is_dir:
            shutil.rmtree(path)
          else:
            os.remove(path)
          removed += 1
          emit(f"archive scratch removed {detail}", flush=True)
      except TimeoutError, OSError:
        continue
  return removed
