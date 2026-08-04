"""Durable sync_timedb file-complete marks for archive/delete when live ingest is on.

Live listend dual-write can place head+tail ``host_data`` rows while middle samples
were dropped from the live queue. Archive/delete readiness must not treat
head+tail alone as complete when ``listend_db_ingest_enabled``. sync_timedb writes
this fingerprint mark after successful ingest or ``db_skip=full_scan``; listend
never writes it.
"""
from __future__ import annotations

import os
import time
from typing import Any, Callable, Iterable, Optional

from hpcperfstats.dbload.lib.file_locking import file_write_lock
from hpcperfstats.dbload.lib.sync_timedb_persistence import (
    artifact_path,
    load_persistence_document,
    save_persistence_document,
)

FILE_COMPLETE_INGEST_MARK_SCHEMA_VERSION = 1
LogFn = Optional[Callable[..., Any]]


def path_fingerprint_key(path: str) -> str | None:
  """Return ``path|mtime|size`` fingerprint, or ``None`` if the path is missing."""
  try:
    st = os.stat(path)
  except OSError:
    return None
  return "%s|%d|%d" % (os.path.normpath(path), int(st.st_mtime), int(st.st_size))


def file_complete_ingest_mark_path(archive_data_dir: str) -> str:
  return artifact_path(archive_data_dir, "file_complete_ingest_mark")


def _default_archive_dir() -> str:
  from hpcperfstats.dbload.lib import conf_parser as cfg

  return str(cfg.get_archive_dir_path() or "")


def _load_entries(mark_path: str) -> dict:
  raw = load_persistence_document(
      mark_path,
      "file_complete_ingest_mark",
      default={"entries": {}},
  )
  if not isinstance(raw, dict):
    return {}
  entries = raw.get("entries")
  if not isinstance(entries, dict):
    return {}
  return dict(entries)


def _save_entries(mark_path: str, entries: dict) -> None:
  save_persistence_document(
      mark_path,
      "file_complete_ingest_mark",
      {
          "schema_version": FILE_COMPLETE_INGEST_MARK_SCHEMA_VERSION,
          "entries": entries,
      },
  )


def has_file_complete_ingest_mark(
    path: str,
    *,
    archive_data_dir: str | None = None,
) -> bool:
  """True when a durable sync_timedb file-complete mark exists for this fingerprint."""
  key = path_fingerprint_key(path)
  if key is None:
    return False
  archive_dir = archive_data_dir if archive_data_dir is not None else _default_archive_dir()
  if not archive_dir:
    return False
  mark_path = file_complete_ingest_mark_path(archive_dir)
  if not os.path.isfile(mark_path):
    return False
  entries = _load_entries(mark_path)
  return key in entries


def record_file_complete_ingest_mark(
    path: str,
    *,
    archive_data_dir: str | None = None,
    log_fn: LogFn = None,
) -> bool:
  """Record a durable mark after sync_timedb completes a path. Return True if stored."""
  key = path_fingerprint_key(path)
  if key is None:
    return False
  archive_dir = archive_data_dir if archive_data_dir is not None else _default_archive_dir()
  if not archive_dir:
    return False
  mark_path = file_complete_ingest_mark_path(archive_dir)
  parent = os.path.dirname(mark_path) or "."
  os.makedirs(parent, exist_ok=True)
  if not os.path.isfile(mark_path):
    _save_entries(mark_path, {})
  try:
    st = os.stat(path)
  except OSError:
    return False
  with file_write_lock(mark_path):
    entries = _load_entries(mark_path)
    entries[key] = {
        "path": os.path.normpath(path),
        "mtime": int(st.st_mtime),
        "size": int(st.st_size),
        "marked_at": time.time(),
    }
    _save_entries(mark_path, entries)
  if log_fn is not None:
    log_fn(
        "INFO: file_complete_ingest_mark recorded path=%s" % path,
        flush=True,
    )
  return True


def clear_file_complete_ingest_marks(
    paths: Iterable[str],
    *,
    archive_data_dir: str | None = None,
    log_fn: LogFn = None,
) -> int:
  """Clear marks for the given paths. Return count removed."""
  path_set = {os.path.normpath(p) for p in (paths or ()) if p}
  if not path_set:
    return 0
  archive_dir = archive_data_dir if archive_data_dir is not None else _default_archive_dir()
  if not archive_dir:
    return 0
  mark_path = file_complete_ingest_mark_path(archive_dir)
  if not os.path.isfile(mark_path):
    return 0
  removed = 0
  with file_write_lock(mark_path):
    entries = _load_entries(mark_path)
    keep = {}
    for key, meta in entries.items():
      meta_path = ""
      if isinstance(meta, dict):
        meta_path = os.path.normpath(str(meta.get("path") or ""))
      fp_path = key.split("|", 1)[0] if "|" in key else ""
      if meta_path in path_set or fp_path in path_set:
        removed += 1
        continue
      if any(path_fingerprint_key(p) == key for p in path_set):
        removed += 1
        continue
      keep[key] = meta
    if removed:
      _save_entries(mark_path, keep)
  if removed and log_fn is not None:
    log_fn(
        "INFO: file_complete_ingest_mark cleared n=%d" % removed,
        flush=True,
    )
  return removed


def maybe_record_file_complete_ingest_mark_from_outcome(
    path: str,
    *,
    ingest_ok: bool,
    outcome: str | None,
    db_skip: str | None = None,
    log_fn: LogFn = None,
    archive_data_dir: str | None = None,
) -> bool:
  """Record mark when sync_timedb finished the path (ingest or full_scan skip).

  Never after live-only head/tail. Live listend must not call this helper.
  """
  if not ingest_ok:
    return False
  outcome_s = str(outcome or "")
  if outcome_s == "ingested":
    return record_file_complete_ingest_mark(
        path,
        archive_data_dir=archive_data_dir,
        log_fn=log_fn,
    )
  if outcome_s == "db_skip":
    skip = str(db_skip or "")
    # Accept full_scan token only (not head_tail / tail_window).
    if skip in ("full_scan", "db_complete_full_scan"):
      return record_file_complete_ingest_mark(
          path,
          archive_data_dir=archive_data_dir,
          log_fn=log_fn,
      )
  return False
