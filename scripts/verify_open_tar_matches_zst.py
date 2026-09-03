#!/usr/bin/env python3
"""
Verify open daily ``YYYY-MM-DD.tar`` archives match sibling ``.tar.zst`` files.

Compares tar **file** member basenames and byte sizes using the same helpers as
``sync_timedb``:

- open tar: ``get_mutable_tar_authority_member_map`` (GNU ``tar tvf``, no cache)
- sealed zstd: streamed scan (default) or members-store single-flight when
  ``--use-store``

Use when ``daily_archive_dir`` still holds uncompressed tars alongside sealed
``.tar.zst`` (``archive_keep_uncompressed_tar=yes`` or pre tar-drop days).

Environment: ``HPCPERFSTATS_INI`` selects ``daily_archive_dir`` unless
``--daily-archive-dir`` is set.

Examples (from checkout root)::

  ../.venv/bin/python3 scripts/verify_open_tar_matches_zst.py
  ../.venv/bin/python3 scripts/verify_open_tar_matches_zst.py --day 2026-06-07
  ../.venv/bin/python3 scripts/verify_open_tar_matches_zst.py --since 2026-01-01 \\
      --skip-sealed-dirty --verbose
  ../.venv/bin/python3 scripts/verify_open_tar_matches_zst.py --json

Attributes:
  _REPO_ROOT: Attribute.
"""
from __future__ import annotations

from typing import Any, Iterator

import argparse
import json
import os
import sys
from datetime import date, datetime

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
  sys.path.insert(0, _REPO_ROOT)


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


def _member_map_diff(
  tar_members: dict[str, int],
  zst_members: dict[str, int],
) -> dict[str, Any]:
  """
  Summarize differences between two daily archive member maps.

  Args:
    tar_members (dict[str, int]): Open tar basename → byte size.
    zst_members (dict[str, int]): Sealed zstd basename → byte size.

  Returns:
    dict[str, Any]: Keys ``tar_only``, ``zst_only``, ``size_mismatch``.

  Examples:
    >>> _member_map_diff({"a": 1}, {"a": 1, "b": 2})["zst_only"]
    ['b']
  """
  tar_names = set(tar_members)
  zst_names = set(zst_members)
  tar_only = sorted(tar_names - zst_names)
  zst_only = sorted(zst_names - tar_names)
  size_mismatch = sorted(
      name
      for name in tar_names & zst_names
      if tar_members[name] != zst_members[name]
  )
  return {
      "tar_only": tar_only,
      "zst_only": zst_only,
      "size_mismatch": size_mismatch,
  }


def _format_diff_detail(
  tar_members: dict[str, int],
  zst_members: dict[str, int],
  diff: dict[str, Any],
) -> str:
  """
  Build a human-readable diff snippet for one day.

  Args:
    tar_members (dict[str, int]): Open tar member map.
    zst_members (dict[str, int]): Sealed zstd member map.
    diff (dict[str, Any]): Output of ``_member_map_diff``.

  Returns:
    str: Multi-line detail (may be empty).

  Examples:
    >>> _format_diff_detail({}, {}, {"tar_only": [], "zst_only": [], "size_mismatch": []})
    ''
  """
  lines: list[str] = []
  for name in diff["tar_only"][:20]:
    lines.append("  tar_only %s size=%s" % (name, tar_members.get(name)))
  for name in diff["zst_only"][:20]:
    lines.append("  zst_only %s size=%s" % (name, zst_members.get(name)))
  for name in diff["size_mismatch"][:20]:
    lines.append(
        "  size_mismatch %s tar=%s zst=%s"
        % (name, tar_members.get(name), zst_members.get(name))
    )
  extra = (
      len(diff["tar_only"])
      + len(diff["zst_only"])
      + len(diff["size_mismatch"])
      - min(20, len(diff["tar_only"]))
      - min(20, len(diff["zst_only"]))
      - min(20, len(diff["size_mismatch"]))
  )
  if extra > 0:
    lines.append("  ... (%d more diff entries)" % extra)
  return "\n".join(lines)


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


def _check_one_tar(
  tar_path: str,
  *,
  use_store: bool,
  skip_sealed_dirty: bool,
  strict: bool,
) -> dict[str, Any]:
  """
  Compare one open tar against its sibling ``.tar.zst``.

  Args:
    tar_path (str): Path to ``YYYY-MM-DD.tar``.
    use_store (bool): Read sealed members via the in-process members store.
    skip_sealed_dirty (bool): Skip when tar mtime is newer than zst mtime.
    strict (bool): Treat sealed-dirty (mtime) as failure even when maps match.

  Returns:
    dict[str, Any]: Result record with ``status`` and diagnostic fields.

  Examples:
    >>> _check_one_tar("/no/such.tar", use_store=False, skip_sealed_dirty=False, strict=False)  # doctest: +SKIP
  """
  from hpcperfstats.dbload.lib.archive_compress import compressed_sibling_paths
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      _scan_compressed_archive_members_and_readable,
      _sealed_archive_members_via_store_or_scan,
      get_mutable_tar_authority_member_map,
      is_daily_tar_sealed_dirty,
      verify_tar_archive_readable,
  )

  tar_path = os.path.normpath(tar_path)
  day_token = os.path.basename(tar_path).replace(".tar", "")
  zst_path, gz_path = compressed_sibling_paths(tar_path)
  result: dict[str, Any] = {
      "day": day_token,
      "tar_path": tar_path,
      "zst_path": zst_path,
      "status": "ok",
  }

  if not os.path.isfile(zst_path):
    result["status"] = "missing_zst"
    return result

  sealed_dirty = is_daily_tar_sealed_dirty(tar_path, zst_path, gz_path)
  result["sealed_dirty"] = sealed_dirty
  if sealed_dirty and skip_sealed_dirty:
    result["status"] = "skipped_sealed_dirty"
    return result

  if not verify_tar_archive_readable(tar_path):
    result["status"] = "tar_unreadable"
    return result

  tar_members = get_mutable_tar_authority_member_map(tar_path)
  if not tar_members:
    result["status"] = "tar_empty"
    result["tar_member_count"] = 0
    return result

  if use_store:
    zst_readable, zst_members = _sealed_archive_members_via_store_or_scan(zst_path)
  else:
    zst_readable, zst_members = _scan_compressed_archive_members_and_readable(
        zst_path,
    )
  if not zst_readable:
    result["status"] = "zst_unreadable"
    return result
  if not zst_members:
    result["status"] = "zst_empty"
    result["zst_member_count"] = 0
    return result

  result["tar_member_count"] = len(tar_members)
  result["zst_member_count"] = len(zst_members)
  if tar_members == zst_members:
    if sealed_dirty and strict:
      result["status"] = "sealed_dirty"
    else:
      result["status"] = "ok"
      if sealed_dirty:
        result["status"] = "warn_sealed_dirty"
    return result

  diff = _member_map_diff(tar_members, zst_members)
  result["status"] = "mismatch"
  result["diff"] = diff
  result["diff_detail"] = _format_diff_detail(tar_members, zst_members, diff)
  return result


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
          "Confirm open daily .tar files match sibling .tar.zst member maps "
          "(names and byte sizes)."
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
      "--day",
      type=_parse_day,
      default=None,
      metavar="YYYY-MM-DD",
      help="Check one calendar day only.",
  )
  parser.add_argument(
      "--since",
      type=_parse_day,
      default=None,
      metavar="YYYY-MM-DD",
      help="Only check days on or after this date.",
  )
  parser.add_argument(
      "--until",
      type=_parse_day,
      default=None,
      metavar="YYYY-MM-DD",
      help="Only check days on or before this date.",
  )
  parser.add_argument(
      "--skip-sealed-dirty",
      action="store_true",
      help=(
          "Skip days whose .tar mtime is newer than .tar.zst (active append / "
          "pending re-seal)."
      ),
  )
  parser.add_argument(
      "--strict",
      action="store_true",
      help=(
          "Treat sealed-dirty days as failure even when member maps match "
          "(default: warn only)."
      ),
  )
  parser.add_argument(
      "--use-store",
      action="store_true",
      help=(
          "Read sealed members via the in-process members store "
          "(matches ingest single-flight; default is a direct zstd stream)."
      ),
  )
  parser.add_argument(
      "--verbose",
      action="store_true",
      help="Print one line per checked day.",
  )
  parser.add_argument(
      "--json",
      action="store_true",
      help="Emit machine-readable JSON summary on stdout.",
  )
  return parser


def main(argv: list[str] | None = None) -> int:
  """
  Run tar-vs-zst verification and return a process exit code.

  Args:
    argv (list[str] | None): Optional argv override for tests.

  Returns:
    int: ``0`` when no hard failures; ``1`` when any mismatch/unreadable/missing.

  Examples:
    >>> main(["--help"])  # doctest: +SKIP
  """
  args = _build_arg_parser().parse_args(argv)
  if args.ini:
    os.environ["HPCPERFSTATS_INI"] = args.ini

  from hpcperfstats.dbload.lib.django_bootstrap import ensure_django

  ensure_django()

  import hpcperfstats.dbload.lib.conf_parser as cfg_mod

  daily_archive_dir = args.daily_archive_dir or cfg_mod.get_daily_archive_dir_path()
  daily_archive_dir = os.path.normpath(str(daily_archive_dir))
  if not os.path.isdir(daily_archive_dir):
    print(
        "ERROR: daily_archive_dir is not a directory: %s" % daily_archive_dir,
        file=sys.stderr,
    )
    return 1

  tar_paths = list(
      _iter_tar_paths_for_args(
          daily_archive_dir,
          day=args.day,
          since=args.since,
          until=args.until,
      )
  )
  if not tar_paths:
    print(
        "No open daily .tar files matched under %s" % daily_archive_dir,
        file=sys.stderr,
    )
    return 0

  results: list[dict[str, Any]] = []
  for tar_path in tar_paths:
    result = _check_one_tar(
        tar_path,
        use_store=args.use_store,
        skip_sealed_dirty=args.skip_sealed_dirty,
        strict=args.strict,
    )
    results.append(result)
    if args.verbose and not args.json:
      print(
          "%(day)s status=%(status)s tar_members=%(tar_member_count)s "
          "zst_members=%(zst_member_count)s"
          % {
              "day": result.get("day", "?"),
              "status": result.get("status", "?"),
              "tar_member_count": result.get("tar_member_count", "-"),
              "zst_member_count": result.get("zst_member_count", "-"),
          }
      )
      diff_detail = result.get("diff_detail")
      if diff_detail:
        print(diff_detail)

  counts: dict[str, int] = {}
  for result in results:
    status = str(result.get("status", "unknown"))
    counts[status] = counts.get(status, 0) + 1

  hard_fail_statuses = {
      "missing_zst",
      "tar_unreadable",
      "zst_unreadable",
      "tar_empty",
      "zst_empty",
      "mismatch",
      "sealed_dirty",
  }
  failures = [
      result for result in results
      if result.get("status") in hard_fail_statuses
  ]

  summary = {
      "daily_archive_dir": daily_archive_dir,
      "checked": len(results),
      "counts": counts,
      "failures": [
          {
              "day": item.get("day"),
              "status": item.get("status"),
              "tar_path": item.get("tar_path"),
              "zst_path": item.get("zst_path"),
              "diff": item.get("diff"),
              "diff_detail": item.get("diff_detail"),
          }
          for item in failures
      ],
  }

  if args.json:
    print(json.dumps(summary, indent=2, sort_keys=True))
  else:
    print(
        "daily_archive_dir=%s checked=%d counts=%s"
        % (daily_archive_dir, len(results), counts)
    )
    for item in failures:
      print(
          "FAIL %(day)s status=%(status)s tar=%(tar_path)s zst=%(zst_path)s"
          % item
      )
      diff_detail = item.get("diff_detail")
      if diff_detail:
        print(diff_detail)

  return 1 if failures else 0


if __name__ == "__main__":
  raise SystemExit(main())
