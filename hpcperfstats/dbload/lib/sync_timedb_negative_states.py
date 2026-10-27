"""
Shared classification for sync_timedb negative states (dead-end remediation).

One place decides whether a failure is the **file's** fault (it may consume
attempts and finally quarantine) or the **environment's** (DB outage,
process/thread limits, a full filesystem, a members-store coordination
miss): environment failures never burn a file's attempts and never persist a
sticky per-day flag. Classification is by **type and errno**, never by message
substring, because message markers are how DE-17 slipped through.

Attributes:
  ENVIRONMENT_ERRNOS: ``errno`` values that describe the host, not the file.
  ENVIRONMENT_STDERR_MARKERS: Lower-cased child-process stderr fragments that
    report the same errno family when only text is available (tar/zstd).
  ENVIRONMENT_EXCEPTION_NAMES: DB driver exception class names (Django and
    psycopg) treated as environment errors; matched on the MRO so neither
    driver has to be importable here.
  READABLE: Readability probe result: archive lists cleanly.
  CORRUPT: Readability probe result: archive really cannot be read.
  TRANSIENT: Readability probe result: the probe itself failed for an
    environmental reason; callers defer and must not repair, restore, or set
    ``day_skip``.
  MISSING: Readability probe result: the path does not exist.
  DEAD_END_MIGRATION_ID: Snapshot id for this release's dead-end migrations.
  MIGRATION_BACKUP_DIRNAME: Directory (under the archive root) that holds
    verified pre-migration sidecar snapshots.
  _SNAPSHOT_FAILED: Process flag; a failed snapshot forces report mode.
  DEFAULT_NEGATIVE_EXIT: Exit for unknown negative values (bounded cooldown).
  SYNC_TIMEDB_NEGATIVE_STATE_EXITS: G2 table, domain -> value -> exit edge.
"""

from __future__ import annotations

import errno
import os
from typing import Any

ENVIRONMENT_ERRNOS = frozenset(
  {
    errno.EAGAIN,
    errno.ENOMEM,
    errno.EMFILE,
    errno.ENFILE,
    errno.EBUSY,
    errno.EIO,
    errno.ENOSPC,
    errno.EDQUOT,
  }
)
ENVIRONMENT_STDERR_MARKERS = (
  "resource temporarily unavailable",
  "cannot allocate memory",
  "too many open files",
  "no space left on device",
  "disk quota exceeded",
  "device or resource busy",
)
ENVIRONMENT_EXCEPTION_NAMES = frozenset(
  {
    "OperationalError",
    "InterfaceError",
    "PermissionError",
    "IngestWriteStageShutdownError",
  }
)

READABLE = "readable"
CORRUPT = "corrupt"
TRANSIENT = "transient"
MISSING = "missing"


def is_environment_error(exc: BaseException | None) -> bool:
  """
  Return True when ``exc`` describes the environment, not the file content.

  Covers every ``ArchiveMembersStoreUnavailableError`` subclass (coordination
  and populate misses), ``PermissionError``, DB ``OperationalError`` /
  ``InterfaceError`` (Django or psycopg, matched by class name on the MRO), and
  ``OSError`` (including ``BlockingIOError``) whose errno is in
  ``ENVIRONMENT_ERRNOS``. Chained causes are followed once so a wrapper raised
  ``from`` an environment error is classified like its cause.

  Args:
    exc (BaseException | None): Exception to classify; None is not one.

  Returns:
    bool: True for environment/coordination errors.

  Examples:
    >>> is_environment_error(BlockingIOError(11, "Resource busy"))
    True
    >>> is_environment_error(ValueError("bad line"))
    False
  """
  if exc is None:
    return False
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_coord import (
    ArchiveMembersStoreUnavailableError,
  )

  if isinstance(exc, ArchiveMembersStoreUnavailableError):
    return True
  if any(c.__name__ in ENVIRONMENT_EXCEPTION_NAMES for c in type(exc).__mro__):
    return True
  if isinstance(exc, OSError) and exc.errno in ENVIRONMENT_ERRNOS:
    return True
  cause = exc.__cause__
  return cause is not None and cause is not exc and is_environment_error(cause)


def environment_error_tag(exc: BaseException) -> str:
  """
  Return a short ``env:<name>`` reason for logs and dead-letter entries.

  Args:
    exc (BaseException): An exception for which ``is_environment_error`` holds.

  Returns:
    str: For example ``env:ENOSPC`` or ``env:OperationalError``.

  Examples:
    >>> environment_error_tag(OSError(28, "No space left on device"))
    'env:ENOSPC'
  """
  code = getattr(exc, "errno", None)
  if isinstance(code, int) and code in errno.errorcode:
    return f"env:{errno.errorcode[code]}"
  return f"env:{type(exc).__name__}"


def stderr_reports_environment_error(text: Any) -> bool:
  """
  Return True when child stderr names an environment errno (EAGAIN, ENOSPC…).

  Args:
    text (Any): Captured stderr (``str`` or None).

  Returns:
    bool: True when any ``ENVIRONMENT_STDERR_MARKERS`` fragment appears.

  Examples:
    >>> stderr_reports_environment_error("tar: x: Cannot allocate memory")
    True
    >>> stderr_reports_environment_error("tar: Unexpected EOF in archive")
    False
  """
  low = str(text or "").lower()
  return any(marker in low for marker in ENVIRONMENT_STDERR_MARKERS)


DEAD_END_MIGRATION_ID = "dead-ends-v1"
MIGRATION_BACKUP_DIRNAME = ".sync_timedb_migration_backup"
_SNAPSHOT_FAILED = {"value": False}


def snapshot_dead_end_sidecars(
  archive_data_dir: str,
  log_fn: Any = None,
) -> bool:
  """
  Copy and sha256-verify the sidecars dead-end migrations rewrite (A17).

  Runs once per ``DEAD_END_MIGRATION_ID`` before the first migration: the
  day-removal manifests, the queue dead letter and the quarantine manifest
  go to ``.sync_timedb_migration_backup/<id>/``. A completed snapshot leaves
  a ``.complete`` marker and is not repeated. When the copy or the
  verification fails the process stays in report mode (migrations log what
  they would do and change nothing); normal operation continues.

  Args:
    archive_data_dir (str): Archive data root.
    log_fn (Any): Logger callable (``log_fn(msg, flush=True)``) or None.

  Returns:
    bool: True when a verified snapshot exists.

  Raises:
    OSError: Internally on a copy/verify mismatch; caught and reported as a
      failed snapshot (report mode), never propagated.

  Examples:
    >>> snapshot_dead_end_sidecars("/nonexistent-root")
    False
  """
  import hashlib
  import shutil

  def _sha(path: str) -> str:
    """
    Return the hex SHA-256 of one file.

    Args:
      path (str): File path.

    Returns:
      str: Hex digest.

    Examples:
      >>> len(_sha(__file__)) == 64  # doctest: +SKIP
      True
    """
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
      for chunk in iter(lambda: fh.read(1 << 20), b""):
        digest.update(chunk)
    return digest.hexdigest()

  root = os.path.join(
    archive_data_dir, MIGRATION_BACKUP_DIRNAME, DEAD_END_MIGRATION_ID
  )
  marker = os.path.join(root, ".complete")
  if os.path.isfile(marker):
    return True
  if not os.path.isdir(archive_data_dir):
    _SNAPSHOT_FAILED["value"] = True
    return False
  sources = [
    os.path.join(archive_data_dir, rel)
    for rel in (
      ".sync_timedb_day_raw_removal",
      ".sync_timedb_queue_dead_letter.json",
      ".sync_timedb_unparsable_raw.json",
    )
  ]
  try:
    os.makedirs(root, exist_ok=True)
    for src in sources:
      if os.path.isdir(src):
        files = [os.path.join(dp, n) for dp, _d, ns in os.walk(src) for n in ns]
      elif os.path.isfile(src):
        files = [src]
      else:
        continue
      for path in files:
        dest = os.path.join(root, os.path.relpath(path, archive_data_dir))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(path, dest)
        if _sha(path) != _sha(dest):
          raise OSError(f"snapshot verify mismatch {path}")
    with open(marker, "w", encoding="utf-8") as fh:
      fh.write("ok\n")
  except OSError as exc:
    _SNAPSHOT_FAILED["value"] = True
    if log_fn:
      log_fn(
        f"ERROR: dead_end migration snapshot failed root={root} err={exc}; "
        "migrations run in report mode",
        flush=True,
      )
    return False
  if log_fn:
    log_fn(
      f"INFO: dead_end migration snapshot verified root={root}", flush=True
    )
  return True


def effective_remediation_mode() -> str:
  """
  Return ``report`` when the INI says so or the migration snapshot failed.

  Returns:
    str: ``apply`` or ``report``.

  Examples:
    >>> effective_remediation_mode() in ("apply", "report")  # doctest: +SKIP
    True
  """
  from hpcperfstats.dbload.lib import conf_parser as cfg

  if _SNAPSHOT_FAILED["value"]:
    return "report"
  return cfg.get_sync_dead_end_remediation_mode()


DEFAULT_NEGATIVE_EXIT = "retry_or_dead_letter"
SYNC_TIMEDB_NEGATIVE_STATE_EXITS: dict[str, dict[str, str]] = {
  # Ingest worker ``outcome`` when ``ingest_ok`` is False (drain dispatch).
  "ingest_outcome": {
    "timeout": "bounded_timeout",
    "lookup_budget": "bounded_timeout",
    "ingest_mark_failed": "env_requeue",
    "write_failed": "env_requeue",
    "ingested": "env_requeue",
    "restore_deferred": "env_requeue",
    "probe_transient": "env_requeue",
    "active_segment": "ack_rediscovered",
    "parse_fail": DEFAULT_NEGATIVE_EXIT,
    "repair_failed": DEFAULT_NEGATIVE_EXIT,
    "quarantine": DEFAULT_NEGATIVE_EXIT,
    "fail": DEFAULT_NEGATIVE_EXIT,
  },
  # ``_run_day_close_job`` return values.
  "day_close_outcome": {
    "yielded": "cooperative_requeue",
    "deferred_age": "cooperative_requeue",
    "incomplete_raw": "cooperative_requeue",
    "skipped": "cooperative_requeue",
    "verify_failed": DEFAULT_NEGATIVE_EXIT,
    "error": DEFAULT_NEGATIVE_EXIT,
  },
  # Day-removal manifest entry statuses (raw still on disk).
  "day_removal_status": {
    "skipped_not_head_tail_ingested": "retryable_reclassify",
    "skipped_not_head_ingested": "retryable_reclassify",
    "skipped_not_sample_ingested": "retryable_reclassify",
    "skipped_not_in_archive": "retryable_reclassify",
    "skipped_size_mismatch": "retryable_reclassify",
    "skipped_seal_invalid": "repair_reseal",
    "skipped_fingerprint_changed": "repair_reclassify",
    "skipped_active_segment": "repair_reclassify",
    "skipped_quarantine": "terminal_quarantine",
    "skipped_locked": "retryable_reclassify",
    "skipped_no_gz": "repair_reseal",
    "skipped_no_work": "retryable_reclassify",
  },
  # Sticky members-store ``day_skip`` kinds.
  "day_skip_kind": {
    "open_tar_gnu_tvf_failed": "revalidate_or_salvage",
    "tar_truncated_or_unreadable": "revalidate_or_salvage",
    "zst_frame_invalid": "revalidate_or_reseal",
    "read_error": "revalidate",
    "salvage_backup_failed": "revalidate_or_salvage",
  },
}


def negative_state_exit(domain: str, value: str) -> str:
  """
  Return the exit edge for one negative status, outcome or kind (G2).

  Unknown or dynamic values take ``DEFAULT_NEGATIVE_EXIT`` (attempt bump,
  dead-letter cooldown, then terminal), never "stay forever".

  Args:
    domain (str): Table name (``ingest_outcome``, ``day_close_outcome``,
      ``day_removal_status``, ``day_skip_kind``).
    value (str): The negative value observed.

  Returns:
    str: Exit edge name.

  Examples:
    >>> negative_state_exit("ingest_outcome", "timeout")
    'bounded_timeout'
    >>> negative_state_exit("ingest_outcome", "never_seen_before")
    'retry_or_dead_letter'
  """
  return SYNC_TIMEDB_NEGATIVE_STATE_EXITS.get(str(domain), {}).get(
    str(value), DEFAULT_NEGATIVE_EXIT
  )
