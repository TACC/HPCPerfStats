#!/usr/bin/env python3
"""
Seal open daily ``YYYY-MM-DD.tar`` files to ``.tar.zst`` and remove the tar.

Selects days where the open tar is a **superset** of the existing sealed
archive (every ``.tar.zst`` member name/size appears in the tar, possibly with
extra tar-only members) or where **no** ``.tar.zst`` exists yet. Skips days
where the sealed archive contains members the tar lacks.

Uses ``sync_timedb`` seal helpers (``atomic_seal_tar_to_zst``, advisory locks,
``zstd -t`` integrity). Always drops the uncompressed tar after a successful seal
or when tar/zst member maps already match.

**Redis is not required.** Member classification and sealing use local GNU
``tar tvf`` + streamed ``zstd`` scans only (same as offline filesystem truth).
Use ``--use-redis`` only when you want Redis L2 populate parity with ingest.

Run while ``sync_timedb`` is quiet; contended locks are skipped when
``--lock-timeout`` is 0 (default).

Environment: ``HPCPERFSTATS_INI`` unless ``--daily-archive-dir`` is set.

Examples (from checkout root)::

  ../.venv/bin/python3 scripts/seal_open_daily_tars_to_zst.py --dry-run --verbose
  ../.venv/bin/python3 scripts/seal_open_daily_tars_to_zst.py --workers 8
  ../.venv/bin/python3 scripts/seal_open_daily_tars_to_zst.py --since 2026-01-01 \\
      --limit 20 --verbose

Attributes:
  _REPO_ROOT: Attribute.
  ACTION_DROP_TAR_ONLY: Attribute.
  ACTION_SEAL: Attribute.
  ACTION_SKIP: Attribute.
  STATUS_DROPPED_TAR: Attribute.
  STATUS_FAILED: Attribute.
  STATUS_SEALED: Attribute.
  STATUS_SKIPPED: Attribute.
"""
from __future__ import annotations

from typing import Any, Iterator

import argparse
import os
import sys
from datetime import date, datetime

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
  sys.path.insert(0, _REPO_ROOT)

ACTION_SEAL = "seal"
ACTION_DROP_TAR_ONLY = "drop_tar_only"
ACTION_SKIP = "skip"

STATUS_SEALED = "sealed"
STATUS_DROPPED_TAR = "dropped_tar_only"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"


def _parse_day(value: str) -> date:
  """
  Parse a ``YYYY-MM-DD`` calendar day for argparse.

  Args:
    value (str): Date string.

  Returns:
    date: Parsed calendar date.

  Raises:
    argparse.ArgumentTypeError: When the string is not ``YYYY-MM-DD``.

  Examples:
    >>> _parse_day("2026-06-07")
    datetime.date(2026, 6, 7)
  """
  try:
    return datetime.strptime(value, "%Y-%m-%d").date()
  except ValueError as exc:
    raise argparse.ArgumentTypeError(
        "expected YYYY-MM-DD, got %r" % value,
    ) from exc


def classify_tar_for_operator_seal(
  tar_members: dict[str, int],
  *,
  zst_exists: bool,
  zst_readable: bool,
  zst_members: dict[str, int],
) -> tuple[str, str]:
  """
  Decide whether an open tar should be sealed, dropped, or skipped.

  Args:
    tar_members (dict[str, int]): Open tar basename → byte size.
    zst_exists (bool): Whether sibling ``.tar.zst`` is present.
    zst_readable (bool): Whether sealed scan / ``zstd -t`` succeeded.
    zst_members (dict[str, int]): Sealed member map (empty when absent).

  Returns:
    tuple[str, str]: ``(ACTION_*, reason)`` where reason is diagnostic text.

  Examples:
    >>> classify_tar_for_operator_seal({"a": 1}, zst_exists=False,
    ...     zst_readable=False, zst_members={})
    ('seal', 'missing_zst')
    >>> classify_tar_for_operator_seal({"a": 1, "b": 2}, zst_exists=True,
    ...     zst_readable=True, zst_members={"a": 1})
    ('seal', 'tar_superset_of_zst')
  """
  from hpcperfstats.dbload.lib.archive_compress import (
      classify_daily_tar_zst_reconcile,
  )

  if not zst_exists:
    return ACTION_SEAL, "missing_zst"
  if not zst_readable or not zst_members:
    return ACTION_SEAL, "zst_unreadable"
  action, reason = classify_daily_tar_zst_reconcile(
      tar_members,
      zst_members,
      zst_exists=zst_exists,
      zst_readable=zst_readable,
      tar_gnu_readable=True,
  )
  if action == "noop":
    return ACTION_DROP_TAR_ONLY, reason
  if action == "skip":
    return ACTION_SKIP, reason
  return ACTION_SEAL, reason


def _archive_date_from_tar_path(tar_path: str) -> date | None:
  """
  Parse ``YYYY-MM-DD`` from a daily tar basename.

  Args:
    tar_path (str): Path to ``YYYY-MM-DD.tar``.

  Returns:
    date | None: Calendar day or ``None`` when basename is not daily-shaped.

  Examples:
    >>> _archive_date_from_tar_path("/x/2026-06-07.tar")
    datetime.date(2026, 6, 7)
  """
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      parse_archive_date_from_daily_tar_path,
  )

  parsed = parse_archive_date_from_daily_tar_path(tar_path)
  if parsed is None:
    return None
  return parsed


def _iter_tar_paths_for_args(
  daily_archive_dir: str,
  *,
  day: date | None,
  since: date | None,
  until: date | None,
) -> Iterator[str]:
  """
  Yield ``YYYY-MM-DD.tar`` paths selected by CLI day filters.

  Args:
    daily_archive_dir (str): Directory containing daily archives.
    day (date | None): Exact day, if requested.
    since (date | None): Lower bound inclusive.
    until (date | None): Upper bound inclusive.

  Yields:
    Iterator[str]: Absolute paths to open daily tars.

  Examples:
    >>> list(_iter_tar_paths_for_args("/tmp", day=date(2026, 1, 1), since=None, until=None))  # doctest: +SKIP
  """
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      iter_daily_tar_paths,
  )

  if day is not None:
    candidate = os.path.join(
        daily_archive_dir,
        day.strftime("%Y-%m-%d") + ".tar",
    )
    if os.path.isfile(candidate):
      yield candidate
    return
  for tar_path in sorted(iter_daily_tar_paths(daily_archive_dir)):
    archive_date = _archive_date_from_tar_path(tar_path)
    if archive_date is None:
      yield tar_path
      continue
    if since is not None and archive_date < since:
      continue
    if until is not None and archive_date > until:
      continue
    yield tar_path


def _load_member_maps(
  tar_path: str,
  zst_path: str,
) -> tuple[dict[str, int], bool, dict[str, int]]:
  """
  Load tar authority map and sealed zstd member map.

  Args:
    tar_path (str): Open daily tar path.
    zst_path (str): Sibling sealed zstd path.

  Returns:
    tuple[dict[str, int], bool, dict[str, int]]: ``(tar_members,
    zst_readable, zst_members)``.

  Examples:
    >>> _load_member_maps("/no.tar", "/no.zst")  # doctest: +SKIP
  """
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      _scan_compressed_archive_members_and_readable,
      get_mutable_tar_authority_member_map,
  )

  tar_members = get_mutable_tar_authority_member_map(tar_path)
  if not os.path.isfile(zst_path):
    return tar_members, False, {}
  zst_readable, zst_members = _scan_compressed_archive_members_and_readable(
      zst_path,
  )
  return tar_members, zst_readable, dict(zst_members or {})


def _drop_equivalent_tar(
  tar_path: str,
  zst_path: str,
  *,
  zstd_threads: int,
  lock_timeout_seconds: float,
  log_fn: Any,
) -> None:
  """
  Remove ``tar_path`` when sealed zstd already matches tar members.

  Args:
    tar_path (str): Open daily tar to unlink.
    zst_path (str): Verified sealed sibling.
    zstd_threads (int): Thread count for ``zstd -t``.
    lock_timeout_seconds (float): Seconds to wait for tar write lock.
    log_fn (Any): Logger callable.

  Returns:
    None

  Raises:
    OSError: When tar removal fails after validation.

  Examples:
    >>> _drop_equivalent_tar("/no.tar", "/no.zst", zstd_threads=1, log_fn=None)  # doctest: +SKIP
  """
  from hpcperfstats.dbload.lib.file_locking import file_write_lock
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      invalidate_after_daily_tar_mutation,
      zstd_drop_page_cache_for_paths,
  )
  from hpcperfstats.dbload.lib.zstd_cli import zstd_test

  zstd_test(zst_path, zstd_threads)
  zstd_drop_page_cache_for_paths(tar_path)
  with file_write_lock(
      tar_path,
      timeout_seconds=int(max(0, lock_timeout_seconds)),
  ):
    os.remove(tar_path)
  invalidate_after_daily_tar_mutation(
      zst_path,
      reason="operator_drop_equivalent_tar",
      log_fn=log_fn,
  )
  if log_fn:
    log_fn(
        "Dropped equivalent uncompressed tar (zst unchanged): %s" % tar_path,
        flush=True,
    )


def _seal_and_drop_tar(
  tar_path: str,
  zst_path: str,
  *,
  zstd_threads: int,
  compress_level: int,
  daily_archive_dir: str,
  log_fn: Any,
) -> None:
  """
  Seal ``tar_path`` to ``zst_path`` and remove the tar via production helper.

  Args:
    tar_path (str): Open daily tar path.
    zst_path (str): Target sealed path.
    zstd_threads (int): ``zstd -T`` thread count per seal job.
    compress_level (int): Zstd compression level from INI.
    daily_archive_dir (str): Canonical daily archive directory.
    log_fn (Any): Logger callable.

  Returns:
    None

  Raises:
    Exception: Propagates seal failures from ``atomic_seal_tar_to_zst``.

  Examples:
    >>> _seal_and_drop_tar("/no.tar", "/no.zst", zstd_threads=1, compress_level=3,
    ...     daily_archive_dir="/x", log_fn=None)  # doctest: +SKIP
  """
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      atomic_seal_tar_to_zst,
  )

  result = atomic_seal_tar_to_zst(
      tar_path,
      zst_path,
      zstd_threads,
      compress_level,
      keep_uncompressed_tar=False,
      log_fn=log_fn,
      remaining_raw_by_gz=None,
      force_remove_uncompressed_tar=True,
      tgz_archive_dir=daily_archive_dir,
  )
  if result is None:
    raise RuntimeError("seal returned no member snapshot for %s" % tar_path)
  if os.path.isfile(tar_path):
    raise RuntimeError("tar still present after seal: %s" % tar_path)


def _process_one_tar(
  tar_path: str,
  *,
  daily_archive_dir: str,
  zstd_threads: int,
  compress_level: int,
  lock_timeout_seconds: float,
  dry_run: bool,
  log_fn: Any,
) -> tuple[str, str]:
  """
  Classify and seal/drop one daily tar (or dry-run plan).

  Args:
    tar_path (str): Open daily tar path.
    daily_archive_dir (str): Daily archive root.
    zstd_threads (int): Zstd threads per seal.
    compress_level (int): Zstd level from INI.
    lock_timeout_seconds (float): Advisory write-lock wait (0 = skip contended).
    dry_run (bool): Log plan only.
    log_fn (Any): Logger callable.

  Returns:
    tuple[str, str]: ``(STATUS_*, detail)`` outcome for summary counts.

  Examples:
    >>> _process_one_tar("/no.tar", daily_archive_dir="/x", zstd_threads=1,
    ...     compress_level=3, lock_timeout_seconds=0.0, dry_run=True,
    ...     log_fn=None)  # doctest: +SKIP
  """
  from hpcperfstats.dbload.lib.archive_compress import compressed_sibling_paths
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      reconcile_open_tar_with_sealed_zst,
      verify_tar_archive_readable,
  )
  from hpcperfstats.dbload.lib.sync_timedb_day_close_cooperation import (
      daily_tar_janitor_mutation_should_defer,
      log_janitor_day_close_defer,
  )

  tar_path = os.path.normpath(tar_path)
  day_token = os.path.basename(tar_path).replace(".tar", "")
  zst_path, _gz_path = compressed_sibling_paths(tar_path)

  if not verify_tar_archive_readable(tar_path):
    reconcile_result = reconcile_open_tar_with_sealed_zst(
        tar_path,
        zstd_threads=zstd_threads,
        compress_level=compress_level,
        remaining_raw_by_gz=None,
        force_remove_uncompressed_tar=True,
        log_fn=log_fn,
        tgz_archive_dir=daily_archive_dir,
    )
    if not reconcile_result.success:
      return STATUS_SKIPPED, reconcile_result.reason
    if reconcile_result.tar_removed:
      return STATUS_DROPPED_TAR, reconcile_result.reason
    if reconcile_result.sealed:
      return STATUS_SEALED, reconcile_result.reason
    if not verify_tar_archive_readable(tar_path):
      return STATUS_SKIPPED, "tar_still_unreadable_after_reconcile"

  defer, defer_reason = daily_tar_janitor_mutation_should_defer(
      tar_path,
      tgz_archive_dir=daily_archive_dir,
      disqualified_daily_tars=set(),
      phase="operator_seal_open_tar",
  )
  if defer:
    if log_fn:
      log_janitor_day_close_defer(
          tar_path,
          phase="operator_seal_open_tar",
          reason=defer_reason,
          log_fn=log_fn,
      )
    return STATUS_SKIPPED, "deferred_%s" % defer_reason

  tar_members, zst_readable, zst_members = _load_member_maps(tar_path, zst_path)
  if not tar_members:
    return STATUS_SKIPPED, "tar_empty"

  action, reason = classify_tar_for_operator_seal(
      tar_members,
      zst_exists=os.path.isfile(zst_path),
      zst_readable=zst_readable,
      zst_members=zst_members,
  )
  if action == ACTION_SKIP:
    return STATUS_SKIPPED, reason

  if dry_run:
    if log_fn:
      log_fn(
          "Dry-run %s %s: %s -> %s (tar_members=%d zst_members=%d)"
          % (
              action,
              day_token,
              tar_path,
              zst_path,
              len(tar_members),
              len(zst_members),
          ),
          flush=True,
      )
    if action == ACTION_DROP_TAR_ONLY:
      return STATUS_DROPPED_TAR, "planned_%s" % reason
    return STATUS_SEALED, "planned_%s" % reason

  try:
    if action == ACTION_DROP_TAR_ONLY:
      reconcile_result = reconcile_open_tar_with_sealed_zst(
          tar_path,
          zstd_threads=zstd_threads,
          compress_level=compress_level,
          remaining_raw_by_gz=None,
          force_remove_uncompressed_tar=True,
          log_fn=log_fn,
          tgz_archive_dir=daily_archive_dir,
      )
      if not reconcile_result.success:
        return STATUS_SKIPPED, reconcile_result.reason
      if reconcile_result.tar_removed:
        return STATUS_DROPPED_TAR, reason
      return STATUS_DROPPED_TAR, reason
    reconcile_result = reconcile_open_tar_with_sealed_zst(
        tar_path,
        zstd_threads=zstd_threads,
        compress_level=compress_level,
        remaining_raw_by_gz=None,
        force_remove_uncompressed_tar=True,
        log_fn=log_fn,
        tgz_archive_dir=daily_archive_dir,
    )
    if not reconcile_result.success:
      return STATUS_FAILED, reconcile_result.reason
    if os.path.isfile(tar_path):
      raise RuntimeError("tar still present after reconcile: %s" % tar_path)
    return STATUS_SEALED, reason
  except TimeoutError as exc:
    if log_fn:
      log_fn(
          "Skipping (write lock timeout): %s (%s)"
          % (tar_path, exc),
          flush=True,
      )
    return STATUS_SKIPPED, "lock_contended: %s" % exc
  except Exception as exc:
    if log_fn:
      log_fn(
          "Failed %s: %s (%s)" % (day_token, tar_path, exc),
          flush=True,
      )
    return STATUS_FAILED, str(exc)


def _build_arg_parser() -> argparse.ArgumentParser:
  """
  Build CLI argument parser.

  Returns:
    argparse.ArgumentParser: Configured parser.

  Examples:
    >>> _build_arg_parser()  # doctest: +SKIP
  """
  parser = argparse.ArgumentParser(
      description=(
          "Seal open daily .tar files to .tar.zst when tar is a superset of "
          "the sealed archive (or no zst exists), then remove the tar."
      ),
  )
  parser.add_argument(
      "--ini",
      default="",
      help="Path to hpcperfstats.ini (sets HPCPERFSTATS_INI for this run).",
  )
  parser.add_argument(
      "--daily-archive-dir",
      default="",
      help="Override [PIPELINE] daily_archive_dir from ini.",
  )
  parser.add_argument(
      "--workers",
      type=int,
      default=8,
      metavar="N",
      help="Parallel seal jobs (default: 8).",
  )
  parser.add_argument(
      "--day",
      type=_parse_day,
      default=None,
      metavar="YYYY-MM-DD",
      help="Process one calendar day only.",
  )
  parser.add_argument(
      "--since",
      type=_parse_day,
      default=None,
      metavar="YYYY-MM-DD",
      help="Only process days on or after this date.",
  )
  parser.add_argument(
      "--until",
      type=_parse_day,
      default=None,
      metavar="YYYY-MM-DD",
      help="Only process days on or before this date.",
  )
  parser.add_argument(
      "--limit",
      type=int,
      default=None,
      metavar="N",
      help="Process at most N tar files.",
  )
  parser.add_argument(
      "--lock-timeout",
      type=float,
      default=0.0,
      metavar="SECONDS",
      help=(
          "Seconds to wait for per-tar write locks (0 = skip contended days)."
      ),
  )
  parser.add_argument(
      "--dry-run",
      action="store_true",
      help="Log planned seal/drop actions only; do not modify archives.",
  )
  parser.add_argument(
      "--cleanup-orphan-locks",
      action="store_true",
      default=True,
      help=(
          "Remove uncontended *.fnctl.lock sidecars before sealing (default: "
          "on). Note: advisory locks use .tar.fnctl.lock, not bare .lock files."
      ),
  )
  parser.add_argument(
      "--no-cleanup-orphan-locks",
      action="store_false",
      dest="cleanup_orphan_locks",
      help="Do not remove orphan .fnctl.lock sidecars before sealing.",
  )
  parser.add_argument(
      "--use-redis",
      action="store_true",
      help=(
          "Use Redis L2 archive member populate (requires reachable "
          "[CACHE] redis_location). Default is local tar/zstd scans only."
      ),
  )
  parser.add_argument(
      "--verbose",
      action="store_true",
      help="Print per-day log lines.",
  )
  return parser


def main(argv: list[str] | None = None) -> int:
  """
  Run parallel tar→zst sealing and return a process exit code.

  Args:
    argv (list[str] | None): Optional argv override for tests.

  Returns:
    int: ``0`` on success; ``1`` when any seal/drop failed; ``2`` on bad config.

  Examples:
    >>> main(["--help"])  # doctest: +SKIP
  """
  args = _build_arg_parser().parse_args(argv)
  if args.ini:
    os.environ["HPCPERFSTATS_INI"] = args.ini

  from hpcperfstats.dbload.lib.django_bootstrap import ensure_django

  ensure_django()

  import hpcperfstats.dbload.lib.conf_parser as cfg_mod
  from hpcperfstats.dbload.lib.print_utils import log_print
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      get_archive_zstd_thread_count,
  )
  from hpcperfstats.dbload.lib.sync_timedb_session_executor import (
      iter_bounded_thread_pool,
  )

  cfg_mod._ensure_cfg_loaded()

  daily_archive_dir = (
      args.daily_archive_dir.strip()
      or cfg_mod.get_daily_archive_dir_path()
  )
  daily_archive_dir = os.path.normpath(str(daily_archive_dir))
  if not os.path.isdir(daily_archive_dir):
    print(
        "ERROR: daily_archive_dir is not a directory: %s" % daily_archive_dir,
        file=sys.stderr,
    )
    return 2

  tar_paths = list(
      _iter_tar_paths_for_args(
          daily_archive_dir,
          day=args.day,
          since=args.since,
          until=args.until,
      )
  )
  if args.limit is not None:
    tar_paths = tar_paths[: max(0, int(args.limit))]

  if not tar_paths:
    print(
        "No open daily .tar files matched under %s" % daily_archive_dir,
        file=sys.stderr,
    )
    return 0

  log_fn = log_print if args.verbose or args.dry_run else None

  if args.cleanup_orphan_locks:
    from hpcperfstats.dbload.lib.archive_compress import compressed_sibling_paths
    from hpcperfstats.dbload.lib.file_locking import (
        cleanup_orphan_fnctl_lock_sidecars_for_targets,
    )

    lock_targets: list[str] = []
    for tar_path in tar_paths:
      zst_path, _gz_path = compressed_sibling_paths(tar_path)
      lock_targets.extend([tar_path, zst_path])
    removed = cleanup_orphan_fnctl_lock_sidecars_for_targets(lock_targets)
    if removed and log_fn:
      log_fn(
          "Removed %d uncontended .fnctl.lock sidecar(s)" % removed,
          flush=True,
      )

  redis_restore = None
  if not args.use_redis:
    import hpcperfstats.dbload.lib.sync_timedb_archive_members_redis as redis_mod

    redis_restore = redis_mod.archive_members_redis_enabled
    redis_mod.archive_members_redis_enabled = lambda: False

  zstd_threads = int(get_archive_zstd_thread_count())
  compress_level = int(cfg_mod.get_archive_zstd_level())

  worker_kwargs = dict(
      daily_archive_dir=daily_archive_dir,
      zstd_threads=zstd_threads,
      compress_level=compress_level,
      lock_timeout_seconds=float(args.lock_timeout),
      dry_run=bool(args.dry_run),
      log_fn=log_fn,
  )

  counts: dict[str, int] = {}
  failures: list[tuple[str, str, str]] = []

  def _worker(tar_path: str) -> tuple[str, str]:
    """
    Thread-pool worker wrapper for one tar path.

    Args:
      tar_path (str): Open daily tar path.

    Returns:
      tuple[str, str]: Status and detail from ``_process_one_tar``.

    Examples:
      >>> _worker("/no.tar")  # doctest: +SKIP
    """
    return _process_one_tar(tar_path, **worker_kwargs)

  workers = max(1, min(int(args.workers), len(tar_paths)))
  try:
    for tar_path, result, err in iter_bounded_thread_pool(
        tar_paths,
        _worker,
        max_workers=workers,
        thread_role="operator-seal",
        process_title="seal_open_daily_tars_to_zst.py",
    ):
      if err is not None:
        status, detail = STATUS_FAILED, str(err)
      else:
        status, detail = result or (STATUS_FAILED, "missing_result")
      counts[status] = counts.get(status, 0) + 1
      if status == STATUS_FAILED:
        failures.append((tar_path, status, detail))
      elif args.verbose and not args.dry_run and log_fn:
        log_fn(
            "%s %s (%s)"
            % (os.path.basename(tar_path), status, detail),
            flush=True,
        )
  finally:
    if redis_restore is not None:
      import hpcperfstats.dbload.lib.sync_timedb_archive_members_redis as redis_mod

      redis_mod.archive_members_redis_enabled = redis_restore

  print(
      "daily_archive_dir=%s candidates=%d workers=%d counts=%s dry_run=%s"
      % (
          daily_archive_dir,
          len(tar_paths),
          workers,
          counts,
          args.dry_run,
      )
  )
  for tar_path, status, detail in failures:
    print("FAIL %s %s: %s" % (tar_path, status, detail), file=sys.stderr)

  return 1 if failures else 0


if __name__ == "__main__":
  raise SystemExit(main())
