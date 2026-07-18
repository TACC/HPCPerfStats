#!/usr/bin/env python3
"""Host CLI: bulk-invalidate archive membership Redis L2, then restart pipeline.

Run from the Compose checkout (directory with ``docker-compose.yaml``). Default
Redis transport is ``docker compose exec -T redis redis-cli`` (no published
Redis port required). After a successful non-dry-run invalidate, restarts the
``pipeline`` service so worker L1 member caches are cold.

Examples (from checkout root)::

  # Dry-run one day (no DELETE, no restart)
  python3 scripts/invalidate_archive_members.py --day 2026-06-08 --dry-run

  # Invalidate one day and restart pipeline
  python3 scripts/invalidate_archive_members.py --day 2026-06-08

  # Invalidate all days (requires --yes) without restart
  python3 scripts/invalidate_archive_members.py --all --yes --no-restart
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Repo root (directory containing pyproject.toml) — required for bare
# ``python3 scripts/...`` without an editable install (production hosts).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
  sys.path.insert(0, str(_REPO_ROOT))


def _resolve_compose_dir(explicit: str | None) -> Path:
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


def _build_parser() -> argparse.ArgumentParser:
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


def _direct_redis_client(url: str):
  import redis

  client = redis.Redis.from_url(url, decode_responses=True)
  client.ping()
  return client


def main(argv=None) -> int:
  parser = _build_parser()
  args = parser.parse_args(argv)

  if args.all and not args.dry_run and not args.yes:
    parser.error("--all requires --yes (or use --dry-run)")

  from hpcperfstats.dbload.lib.invalidate_archive_members_ops import (  # noqa: E402
      ComposeRedisCliClient,
      DEFAULT_COMPOSE_PROJECT,
      compose_argv,
      format_compose_cmd_for_log,
      restart_pipeline_compose,
  )
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (  # noqa: E402
      invalidate_archive_members_redis_bulk,
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
