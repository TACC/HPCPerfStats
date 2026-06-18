#!/usr/bin/env python3
"""One-shot migration: legacy daily ``YYYY-MM-DD.tar.gz`` -> canonical ``.tar.zst``.

Uses the same archive helpers as ``sync_timedb`` (safe decompress, atomic seal,
member-equivalence gzip drop, ``*.fnctl.lock`` advisory locks). Days with
contended locks are skipped when ``--lock-timeout`` is 0 (default); re-run after
``sync_timedb`` is quiet.

Environment:
  HPCPERFSTATS_INI  Path to site config (default search includes
                    /home/hpcperfstats/hpcperfstats.ini).

Examples:
  HPCPERFSTATS_INI=/home/hpcperfstats/hpcperfstats.ini \\
    python scripts/migrate_daily_archive_gz_to_zst.py --dry-run

  python scripts/migrate_daily_archive_gz_to_zst.py --verbose --limit 10
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

# Repo root (directory containing pyproject.toml).
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
  sys.path.insert(0, _ROOT)


def _parse_since(value):
  try:
    return datetime.strptime(value, "%Y-%m-%d").date()
  except ValueError as exc:
    raise argparse.ArgumentTypeError(
        "expected YYYY-MM-DD, got %r" % value,
    ) from exc


def _build_arg_parser():
  parser = argparse.ArgumentParser(
      description=(
          "Migrate legacy daily .tar.gz archives to .tar.zst in daily_archive_dir."
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
      help="Override PORTAL daily_archive_dir from ini.",
  )
  parser.add_argument(
      "--dry-run",
      action="store_true",
      help="Log planned actions only; do not modify archives.",
  )
  parser.add_argument(
      "--since",
      type=_parse_since,
      default=None,
      metavar="YYYY-MM-DD",
      help="Only migrate archives on or after this calendar date.",
  )
  parser.add_argument(
      "--limit",
      type=int,
      default=None,
      metavar="N",
      help="Process at most N legacy .tar.gz files.",
  )
  parser.add_argument(
      "--workers",
      type=int,
      default=None,
      metavar="N",
      help=(
          "Parallel migration workers (default: archive_seal_parallel_workers "
          "or SYNC_ARCHIVE_SEAL_WORKERS)."
      ),
  )
  parser.add_argument(
      "--lock-timeout",
      type=float,
      default=0.0,
      metavar="SECONDS",
      help=(
          "Seconds to wait for write locks (0 = skip contended days immediately)."
      ),
  )
  parser.add_argument(
      "--force-remove-tar",
      action="store_true",
      help=(
          "Pass force_remove_uncompressed_tar to seal (operator use after raw "
          "stats are gone; ignores remaining_raw_by_gz gate)."
      ),
  )
  parser.add_argument(
      "--cleanup-stale-lock-sidecars",
      action="store_true",
      help="Remove stale *.fnctl.lock sidecars before and after migration.",
  )
  parser.add_argument(
      "--verbose",
      action="store_true",
      help="Print per-day migration log lines.",
  )
  parser.add_argument(
      "--decompress-tmp-dir",
      default="/tmp",
      help=(
          "Directory for temporary decompressed tar files when migrating gz-only "
          "days (default: /tmp)."
      ),
  )
  return parser


def main(argv=None):
  args = _build_arg_parser().parse_args(argv)
  if args.ini:
    os.environ["HPCPERFSTATS_INI"] = args.ini

  import hpcperfstats.dbload.lib.conf_parser as cfg_mod
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      MIGRATE_GZ_STATUS_CONVERTED,
      MIGRATE_GZ_STATUS_DROPPED_ONLY,
      MIGRATE_GZ_STATUS_FAILED,
      MIGRATE_GZ_STATUS_KEPT_MISMATCH,
      MIGRATE_GZ_STATUS_SKIPPED_LOCKED,
      build_remaining_raw_stats_by_daily_gz,
      check_archive_migration_prerequisites,
      migrate_legacy_daily_gz_archives,
  )
  from hpcperfstats.dbload.lib.file_locking import cleanup_stale_fnctl_lock_sidecars
  from hpcperfstats.dbload.lib.print_utils import log_print

  cfg_mod._ensure_cfg_loaded()

  daily_archive_dir = (
      args.daily_archive_dir.strip()
      or cfg_mod.get_daily_archive_dir_path()
  )
  if not daily_archive_dir or not os.path.isdir(daily_archive_dir):
    print(
        "ERROR: daily archive directory does not exist: %s" % daily_archive_dir,
        file=sys.stderr,
    )
    return 2

  log_fn = log_print if args.verbose or args.dry_run else None

  try:
    check_archive_migration_prerequisites()
  except RuntimeError as exc:
    print("ERROR: %s" % exc, file=sys.stderr)
    return 2

  if args.cleanup_stale_lock_sidecars:
    removed = cleanup_stale_fnctl_lock_sidecars(daily_archive_dir)
    if log_fn:
      log_fn(
          "Removed stale lock sidecars before migrate: %d" % removed,
          flush=True,
      )

  remaining_raw_by_gz = None
  if not args.force_remove_tar:
    archive_data_dir = cfg_mod.get_archive_dir_path()
    host_name_ext = cfg_mod.get_host_name_ext()
    remaining_raw_by_gz = build_remaining_raw_stats_by_daily_gz(
        archive_data_dir,
        host_name_ext,
        daily_archive_dir,
    )

  try:
    summary = migrate_legacy_daily_gz_archives(
        daily_archive_dir,
        remaining_raw_by_gz=remaining_raw_by_gz,
        force_remove_uncompressed_tar=args.force_remove_tar,
        decompress_tmp_dir=args.decompress_tmp_dir,
        log_fn=log_fn,
        lock_timeout_seconds=args.lock_timeout,
        dry_run=args.dry_run,
        since_date=args.since,
        limit=args.limit,
        workers=args.workers,
    )
  except RuntimeError as exc:
    print("ERROR: %s" % exc, file=sys.stderr)
    return 2

  if args.cleanup_stale_lock_sidecars:
    removed = cleanup_stale_fnctl_lock_sidecars(daily_archive_dir)
    if log_fn:
      log_fn(
          "Removed stale lock sidecars after migrate: %d" % removed,
          flush=True,
      )

  print("Migration summary for %s:" % daily_archive_dir)
  for key in sorted(summary.keys()):
    if key == "gz_remaining":
      print("  %s: %d" % (key, summary[key]))
    else:
      print("  %s: %d" % (key, summary.get(key, 0)))

  if args.dry_run:
    return 0

  failed = int(summary.get(MIGRATE_GZ_STATUS_FAILED, 0))
  gz_remaining = int(summary.get("gz_remaining", 0))
  converted = int(summary.get(MIGRATE_GZ_STATUS_CONVERTED, 0))
  dropped = int(summary.get(MIGRATE_GZ_STATUS_DROPPED_ONLY, 0))

  if failed > 0:
    return 1
  if gz_remaining > 0 and converted + dropped == 0:
    return 1
  return 0


if __name__ == "__main__":
  sys.exit(main())
