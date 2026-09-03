"""
Host-side archive-members sidecar wipe + optional compose pipeline restart.

This module is intentionally free of ``print_utils`` / ``conf_parser`` imports
so ``scripts/invalidate_archive_members.py`` can run on hosts whose default
``python3`` is older than 3.10 (PEP 604) without pulling the full sync_timedb
stack. Prefer Python >= 3.12 (``requires-python``); the script re-execs
when needed.

Attributes:
  DEFAULT_COMPOSE_PROJECT: Compose project name used by restart helpers.
  JOB_STORE_SNAPSHOT_RELPATH: Job-store sidecar basename that must never
    be unlinked by invalidate.
  MEMBERS_STORE_DIR_RELPATH: Day-member sidecar directory basename.
  PIPELINE_SERVICE: Compose service restarted to drop process L1 caches.
"""
from __future__ import annotations

from typing import Any, Iterable

import datetime
import os
import shlex
import subprocess


DEFAULT_COMPOSE_PROJECT = "hpcperfstats"
PIPELINE_SERVICE = "pipeline"
JOB_STORE_SNAPSHOT_RELPATH = ".sync_timedb_job_store.json"
MEMBERS_STORE_DIR_RELPATH = ".sync_timedb_archive_members"


def compose_argv(
  *,
  project: Any = DEFAULT_COMPOSE_PROJECT,
  compose_files: Any | None = None,
) -> list[str]:
  """
  Build ``docker compose -p <project> [-f …]`` argv prefix.

  Args:
    project (Any): Compose project name.
    compose_files (Any | None): Extra ``-f`` compose files.

  Returns:
    list[str]: Argv prefix for compose commands.

  Examples:
    >>> compose_argv(project="hpcperfstats")[0]
    'docker'
  """
  argv = ["docker", "compose", "-p", str(project or DEFAULT_COMPOSE_PROJECT)]
  for path in compose_files or ():
    argv.extend(["-f", str(path)])
  return argv


def restart_pipeline_compose(
  *,
  compose_dir: str,
  project: Any = DEFAULT_COMPOSE_PROJECT,
  compose_files: Any | None = None,
  timeout_s: float = 300.0,
  run_fn: Any | None = None,
) -> None:
  """
  Restart the Compose ``pipeline`` service on the host (clears worker L1).

  Args:
    compose_dir (str): Checkout directory with docker-compose.yaml.
    project (Any): Compose project name.
    compose_files (Any | None): Extra compose files.
    timeout_s (float): Subprocess timeout seconds.
    run_fn (Any | None): Optional ``subprocess.run`` replacement.

  Returns:
    None

  Raises:
    RuntimeError: When docker compose is missing, times out, or exits
      non-zero.

  Examples:
    >>> format_compose_cmd_for_log(compose_argv(project="hps") + ["restart"])
    'docker compose -p hps restart'
  """
  runner = run_fn or subprocess.run
  cmd = compose_argv(project=project, compose_files=compose_files)
  cmd.extend(["restart", PIPELINE_SERVICE])
  try:
    completed = runner(
        cmd,
        cwd=str(compose_dir),
        check=False,
        capture_output=True,
        text=True,
        timeout=float(timeout_s),
    )
  except FileNotFoundError as exc:
    raise RuntimeError(
        "docker compose not found on PATH; cannot restart pipeline",
    ) from exc
  except subprocess.TimeoutExpired as exc:
    raise RuntimeError(
        "docker compose restart pipeline timed out after %.0fs" % timeout_s,
    ) from exc
  if getattr(completed, "returncode", 1) != 0:
    err = (
        getattr(completed, "stderr", None)
        or getattr(completed, "stdout", None)
        or ""
    ).strip()
    raise RuntimeError(
        "docker compose restart pipeline failed (exit %s): %s"
        % (getattr(completed, "returncode", "?"), err or "(no output)"),
    )


def format_compose_cmd_for_log(argv: Any) -> str:
  """
  Quote argv for operator log lines.

  Args:
    argv (Any): Command argument list.

  Returns:
    str: Shell-quoted command string.

  Examples:
    >>> format_compose_cmd_for_log(["docker", "compose"])
    'docker compose'
  """
  return " ".join(shlex.quote(str(part)) for part in argv)


def _normalize_bulk_day_tokens(day_tokens: Any) -> list[str] | None:
  """
  Validate and normalize calendar day tokens (``YYYY-MM-DD``).

  Args:
    day_tokens (Any): Day tokens, or None for every sidecar.

  Returns:
    list[str] | None: Sorted unique days, or None for all days.

  Raises:
    ValueError: When a token is not an ISO calendar day.

  Examples:
    >>> _normalize_bulk_day_tokens(["2026-08-01"])
    ['2026-08-01']
  """
  if day_tokens is None:
    return None
  normalized = []
  for raw in day_tokens:
    token = str(raw or "").strip()
    if not token or token == "unknown":
      raise ValueError("day token must be YYYY-MM-DD, got %r" % (raw,))
    try:
      datetime.date.fromisoformat(token)
    except ValueError as exc:
      raise ValueError("day token must be YYYY-MM-DD, got %r" % (token,)) from exc
    normalized.append(token)
  return sorted(set(normalized))


def _members_dir(archive_dir: str) -> str:
  """
  Return the member-sidecar directory under ``archive_dir``.

  Args:
    archive_dir (str): Archive data directory.

  Returns:
    str: Absolute or joined member-store directory.

  Examples:
    >>> _members_dir("/archive").endswith(".sync_timedb_archive_members")
    True
  """
  return os.path.join(str(archive_dir), MEMBERS_STORE_DIR_RELPATH)


def _job_store_path(archive_dir: str) -> str:
  """
  Return the job-store snapshot path that invalidate must never unlink.

  Args:
    archive_dir (str): Archive data directory.

  Returns:
    str: Job-store sidecar path.

  Examples:
    >>> _job_store_path("/archive").endswith(".sync_timedb_job_store.json")
    True
  """
  return os.path.join(str(archive_dir), JOB_STORE_SNAPSHOT_RELPATH)


def _sidecar_paths_for_days(
  archive_dir: str,
  days: Iterable[str] | None,
) -> list[str]:
  """
  List member sidecar files that would be wiped.

  Args:
    archive_dir (str): Archive data directory.
    days (Iterable[str] | None): Calendar days, or None for every
      ``*.json`` under the members directory.

  Returns:
    list[str]: Existing sidecar paths, never including the job store.

  Examples:
    >>> _sidecar_paths_for_days("/missing", None)
    []
  """
  store_dir = _members_dir(archive_dir)
  job_store = os.path.realpath(_job_store_path(archive_dir))
  found: list[str] = []
  if days is None:
    if not os.path.isdir(store_dir):
      return []
    for name in sorted(os.listdir(store_dir)):
      if not name.endswith(".json"):
        continue
      path = os.path.join(store_dir, name)
      if os.path.realpath(path) == job_store:
        continue
      if os.path.isfile(path):
        found.append(path)
    return found
  for day in days:
    path = os.path.join(store_dir, "%s.json" % day)
    if os.path.realpath(path) == job_store:
      continue
    if os.path.isfile(path):
      found.append(path)
  return found


def invalidate_archive_members_sidecars(
  *,
  archive_dir: str,
  day_tokens: Any | None = None,
  dry_run: bool = False,
) -> dict[str, Any]:
  """
  Wipe member-store day sidecars without touching the job-store snapshot.

  Ephemeral flags (tar-hot, append-inflight, restore) are process-local
  and are not on disk. Pipeline restart drops L1 caches.

  Args:
    archive_dir (str): Archive data directory that owns the sidecars.
    day_tokens (Any | None): Calendar days, or None to wipe every day
      sidecar.
    dry_run (bool): True to report paths without unlinking.

  Returns:
    dict[str, Any]: ``scanned``, ``deleted``, ``dry_run``, ``days``,
    and ``paths``.

  Raises:
    ValueError: When ``archive_dir`` is empty or a day token is invalid.

  Examples:
    >>> invalidate_archive_members_sidecars(
    ...   archive_dir="/missing", dry_run=True,
    ... )["deleted"]
    0
  """
  root = str(archive_dir or "").strip()
  if not root:
    raise ValueError("archive_dir is required")
  days = _normalize_bulk_day_tokens(day_tokens)
  paths = _sidecar_paths_for_days(root, days)
  result = {
      "scanned": len(paths),
      "deleted": 0,
      "dry_run": bool(dry_run),
      "days": list(days) if days is not None else [],
      "paths": list(paths),
  }
  if dry_run or not paths:
    return result
  deleted = 0
  for path in paths:
    if os.path.realpath(path) == os.path.realpath(_job_store_path(root)):
      continue
    try:
      os.unlink(path)
    except OSError:
      continue
    deleted += 1
  result["deleted"] = deleted
  return result


def invalidate_archive_members_bulk(
  *,
  archive_dir: str,
  day_tokens: Any | None = None,
  dry_run: bool = False,
  client: Any | None = None,
) -> dict[str, Any]:
  """
  Compatibility wrapper for sidecar wipe (job store is ignored).

  Args:
    archive_dir (str): Archive data directory.
    day_tokens (Any | None): Calendar days, or None for every sidecar.
    dry_run (bool): True to report without unlinking.
    client (Any | None): Ignored leftover argument.

  Returns:
    dict[str, Any]: Same payload as
    :func:`invalidate_archive_members_sidecars`.

  Examples:
    >>> invalidate_archive_members_bulk(
    ...   archive_dir="/missing", dry_run=True,
    ... )["dry_run"]
    True
  """
  del client
  return invalidate_archive_members_sidecars(
      archive_dir=archive_dir,
      day_tokens=day_tokens,
      dry_run=dry_run,
  )
