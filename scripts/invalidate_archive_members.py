#!/usr/bin/env python3
"""
Host CLI: bulk-invalidate archive membership Redis L2, then restart pipeline.

Run from the Compose checkout (directory with ``docker-compose.yaml``). Default
Redis transport is ``docker compose exec -T redis redis-cli`` (no published
Redis port required). After a successful non-dry-run invalidate, restarts the
``pipeline`` service so worker L1 member caches are cold.

Requires Python >= 3.14 (project ``requires-python``). When the host default
``python3`` is older, this script re-execs a suitable interpreter if found.

Examples (from checkout root)::

python3 scripts/invalidate_archive_members.py --day 2026-06-08 --dry-run python3
scripts/invalidate_archive_members.py --day 2026-06-08 python3
scripts/invalidate_archive_members.py --all --yes --no-restart

Attributes:
  _MIN_PY: Attribute.
  _REPO_ROOT: Attribute.
"""
from __future__ import annotations

from typing import Any, Iterator

import argparse
import os
import shutil
import sys
from pathlib import Path

_MIN_PY = (3, 14)
_REPO_ROOT = Path(__file__).resolve().parent.parent


def _candidate_pythons() -> Iterator[Any]:
  """
  Yield executable paths that may satisfy requires-python >= 3.14.
  
  Yields:
    Iterator[Any]: Open return polymorphism from ``_candidate_pythons``:
    concrete type depends on inputs and branch (mapping, scalar, handle, or
    ``None``-like empty).
  
  Examples:
    >>> _candidate_pythons()  # doctest: +SKIP
  """
  seen = set()
  names = (
      _REPO_ROOT / ".venv" / "bin" / "python3",
      _REPO_ROOT.parent / ".venv" / "bin" / "python3",
      shutil.which("python3.14"),
      shutil.which("python3.15"),
  )
  for raw in names:
    if not raw:
      continue
    path = str(Path(raw).resolve())
    if path in seen:
      continue
    seen.add(path)
    if os.path.isfile(path) and os.access(path, os.X_OK):
      yield path


def _ensure_python_version() -> None:
  """
  Re-exec under Python >= 3.14 when the current interpreter is too old.
  
  Returns:
    None
  
  Raises:
    SystemExit: Raised when ``_ensure_python_version`` hits a ``SystemExit``
    failure path.
  
  Examples:
    >>> _ensure_python_version()  # doctest: +SKIP
  """
  if sys.version_info >= _MIN_PY:
    return
  script = str(Path(__file__).resolve())
  for candidate in _candidate_pythons():
    # Avoid infinite re-exec loops.
    if os.path.samefile(candidate, sys.executable):
      continue
    os.execv(candidate, [candidate, script] + sys.argv[1:])
  sys.stderr.write(
      "ERROR: scripts/invalidate_archive_members.py requires Python >= %s.%s "
      "(found %s.%s). Use the project venv or python3.14+, for example:\n"
      "  %s/../.venv/bin/python3 scripts/invalidate_archive_members.py ...\n"
      "  python3.14 scripts/invalidate_archive_members.py ...\n"
      % (
          _MIN_PY[0],
          _MIN_PY[1],
          sys.version_info[0],
          sys.version_info[1],
          _REPO_ROOT,
      ),
  )
  raise SystemExit(2)


_ensure_python_version()

if str(_REPO_ROOT) not in sys.path:
  sys.path.insert(0, str(_REPO_ROOT))


def _resolve_compose_dir(explicit: Any) -> Any:
  """
  Internal helper to resolve the compose dir.
  
  Args:
    explicit (Any): Explicit passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Raises:
    SystemExit: Raised when ``_resolve_compose_dir`` hits a ``SystemExit``
    failure path.
  
  Examples:
    >>> _resolve_compose_dir(None)  # doctest: +SKIP
  """
  if explicit:
    path = Path(explicit).expanduser().resolve()
  else:
    path = Path.cwd().resolve()
  if not (path / "docker-compose.yaml").is_file() and not (
      path / "docker-compose.yml"
  ).is_file():
    raise SystemExit(
        "compose dir %s has no docker-compose.yaml; pass --compose-dir "
        "pointing at the HPCPerfStats checkout" % path,
    )
  return path


def _build_parser() -> Any:
  """
  Internal helper to build the parser.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _build_parser()  # doctest: +SKIP
  """
  parser = argparse.ArgumentParser(
      description=(
          "Invalidate archive membership Redis L2 (--all or --day), then "
          "docker compose restart pipeline (unless --dry-run / --no-restart)."
      ),
  )
  scope = parser.add_mutually_exclusive_group(required=True)
  scope.add_argument(
      "--all",
      action="store_true",
      help="Clear all membership-related Redis keys (requires --yes unless --dry-run)",
  )
  scope.add_argument(
      "--day",
      action="append",
      dest="days",
      metavar="YYYY-MM-DD",
      help="Clear membership keys for one calendar day (repeatable)",
  )
  parser.add_argument(
      "--dry-run",
      action="store_true",
      help="Scan and report counts without DELETE or pipeline restart",
  )
  parser.add_argument(
      "--yes",
      action="store_true",
      help="Confirm destructive --all (required when not --dry-run)",
  )
  parser.add_argument(
      "--no-restart",
      action="store_true",
      help="Skip docker compose restart pipeline after invalidate",
  )
  parser.add_argument(
      "--compose-project",
      default="hpcperfstats",
      help="Compose project name (default: hpcperfstats)",
  )
  parser.add_argument(
      "--compose-dir",
      default=None,
      help="Directory containing docker-compose.yaml (default: cwd)",
  )
  parser.add_argument(
      "--compose-file",
      action="append",
      dest="compose_files",
      default=None,
      metavar="PATH",
      help="Extra -f compose file (repeatable); relative to --compose-dir",
  )
  parser.add_argument(
      "--redis-url",
      default=None,
      help=(
          "Optional redis:// URL reachable from the host; when set, use a "
          "direct Redis client instead of compose exec redis-cli"
      ),
  )
  return parser


def _direct_redis_client(url: Any) -> Any:
  """
  Internal helper to handle direct redis client.
  
  Args:
    url (Any): Url passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _direct_redis_client(None)  # doctest: +SKIP
  """
  import redis

  client = redis.Redis.from_url(url, decode_responses=True)
  client.ping()
  return client


def main(argv: Any | None = None) -> Any:
  """
  Run this module's command-line entrypoint.
  
  Args:
    argv (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> main(None)  # doctest: +SKIP
  """
  parser = _build_parser()
  args = parser.parse_args(argv)

  if args.all and not args.dry_run and not args.yes:
    parser.error("--all requires --yes (or use --dry-run)")

  # Import only the lightweight ops module (no print_utils / conf_parser).
  from hpcperfstats.dbload.lib.invalidate_archive_members_ops import (
      ComposeRedisCliClient,
      DEFAULT_COMPOSE_PROJECT,
      compose_argv,
      format_compose_cmd_for_log,
      invalidate_archive_members_redis_bulk,
      restart_pipeline_compose,
  )

  compose_dir = _resolve_compose_dir(args.compose_dir)
  project = args.compose_project or DEFAULT_COMPOSE_PROJECT
  compose_files = list(args.compose_files or ())

  day_tokens = None if args.all else list(args.days or [])
  if day_tokens is not None and not day_tokens:
    parser.error("at least one --day is required when not using --all")

  try:
    if args.redis_url:
      client = _direct_redis_client(args.redis_url)
    else:
      client = ComposeRedisCliClient(
          compose_dir=str(compose_dir),
          project=project,
          compose_files=compose_files,
      )
  except Exception as exc:  # noqa: BLE001 — operator CLI surface
    print("ERROR: Redis transport unavailable: %s" % exc, file=sys.stderr)
    return 2

  try:
    result = invalidate_archive_members_redis_bulk(
        day_tokens=day_tokens,
        dry_run=bool(args.dry_run),
        client=client,
    )
  except ValueError as exc:
    print("ERROR: %s" % exc, file=sys.stderr)
    return 2
  except Exception as exc:  # noqa: BLE001
    print("ERROR: Redis bulk invalidate failed: %s" % exc, file=sys.stderr)
    return 2

  if result.get("error") == "redis_unavailable":
    print("ERROR: Redis unavailable", file=sys.stderr)
    return 2

  scope_label = (
      "all days" if day_tokens is None else ",".join(result.get("days") or day_tokens)
  )
  print(
      "archive_members_invalidate scanned=%s deleted=%s dry_run=%s days=%s"
      % (
          result.get("scanned", 0),
          result.get("deleted", 0),
          result.get("dry_run", False),
          scope_label,
      ),
  )

  if args.dry_run or args.no_restart:
    if args.dry_run:
      print("dry-run: skipped docker compose restart pipeline")
    else:
      print(
          "no-restart: Redis cleared; worker L1 may stay warm until manual recycle",
      )
    return 0

  restart_cmd = compose_argv(project=project, compose_files=compose_files)
  restart_cmd.extend(["restart", "pipeline"])
  print("restarting pipeline: %s" % format_compose_cmd_for_log(restart_cmd))
  try:
    restart_pipeline_compose(
        compose_dir=str(compose_dir),
        project=project,
        compose_files=compose_files,
    )
  except Exception as exc:  # noqa: BLE001
    print(
        "ERROR: Redis membership keys were already cleared, but pipeline "
        "restart failed: %s" % exc,
        file=sys.stderr,
    )
    return 3
  print("pipeline restart requested ok")
  return 0


if __name__ == "__main__":
  sys.exit(main())
