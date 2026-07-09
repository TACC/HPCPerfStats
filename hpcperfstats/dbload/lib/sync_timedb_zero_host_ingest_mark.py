"""Durable zero-host (proc-only / stats_rows=0) ingest marks for archive/delete gate.

When a closed stats file successfully ingests with no new ``host_data`` rows,
``proc_data`` cannot prove head/tail Unix seconds (no ``time`` column). Archive
and raw-delete readiness therefore ORs the classic host_data head+tail gate with
a fingerprint mark under ``archive_dir``.
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

ZERO_HOST_INGEST_MARK_SCHEMA_VERSION = 1
LogFn = Optional[Callable[..., Any]]


def path_fingerprint_key(path: str) -> str | None:
  """Return ``path|mtime|size`` fingerprint, or ``None`` if the path is missing."""
  try:
    st = os.stat(path)
  except OSError:
    return None
  return "%s|%d|%d" % (os.path.normpath(path), int(st.st_mtime), int(st.st_size))


def zero_host_ingest_mark_path(archive_data_dir: str) -> str:
  return artifact_path(archive_data_dir, "zero_host_ingest_mark")


def _default_archive_dir() -> str:
  from hpcperfstats.dbload.lib import conf_parser as cfg

  return str(cfg.get_archive_dir_path() or "")


def _load_entries(mark_path: str) -> dict:
  raw = load_persistence_document(
      mark_path,
      "zero_host_ingest_mark",
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
      "zero_host_ingest_mark",
      {
          "schema_version": ZERO_HOST_INGEST_MARK_SCHEMA_VERSION,
          "entries": entries,
      },
  )


def has_zero_host_ingest_mark(
    path: str,
    *,
    archive_data_dir: str | None = None,
) -> bool:
  """True when a durable mark exists for this path fingerprint."""
  key = path_fingerprint_key(path)
  if key is None:
    return False
  archive_dir = archive_data_dir if archive_data_dir is not None else _default_archive_dir()
  if not archive_dir:
    return False
  mark_path = zero_host_ingest_mark_path(archive_dir)
  if not os.path.isfile(mark_path):
    return False
  entries = _load_entries(mark_path)
  return key in entries


def record_zero_host_ingest_mark(
    path: str,
    *,
    archive_data_dir: str | None = None,
    log_fn: LogFn = None,
) -> bool:
  """Record a durable mark for a successful zero-host-row ingest. Return True if stored."""
  key = path_fingerprint_key(path)
  if key is None:
    return False
  archive_dir = archive_data_dir if archive_data_dir is not None else _default_archive_dir()
  if not archive_dir:
    return False
  mark_path = zero_host_ingest_mark_path(archive_dir)
  parent = os.path.dirname(mark_path) or "."
  os.makedirs(parent, exist_ok=True)
  # Ensure target exists so lock sidecar open + atomic replace always have a path.
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
        "INFO: zero_host_ingest_mark recorded path=%s" % path,
        flush=True,
    )
  return True


def clear_zero_host_ingest_marks(
    paths: Iterable[str],
    *,
    archive_data_dir: str | None = None,
    log_fn: LogFn = None,
) -> int:
  """Clear marks for the given paths (any fingerprint keys matching path). Return count removed."""
  path_set = {os.path.normpath(p) for p in (paths or ()) if p}
  if not path_set:
    return 0
  archive_dir = archive_data_dir if archive_data_dir is not None else _default_archive_dir()
  if not archive_dir:
    return 0
  mark_path = zero_host_ingest_mark_path(archive_dir)
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
      # Also drop when current fingerprint for a surviving path matches key.
      if any(path_fingerprint_key(p) == key for p in path_set):
        removed += 1
        continue
      keep[key] = meta
    if removed:
      _save_entries(mark_path, keep)
  if removed and log_fn is not None:
    log_fn(
        "INFO: zero_host_ingest_mark cleared n=%d" % removed,
        flush=True,
    )
  return removed


def maybe_record_zero_host_ingest_mark_from_outcome(
    path: str,
    *,
    ingest_ok: bool,
    outcome: str | None,
    stats_rows: int | None,
    log_fn: LogFn = None,
    archive_data_dir: str | None = None,
) -> bool:
  """Record mark when ingest succeeded with zero host rows (proc-only / empty stats)."""
  if not ingest_ok:
    return False
  if str(outcome or "") not in ("ingested", ""):
    return False
  if stats_rows is None:
    return False
  if int(stats_rows) != 0:
    return False
  return record_zero_host_ingest_mark(
      path,
      archive_data_dir=archive_data_dir,
      log_fn=log_fn,
  )
