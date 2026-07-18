"""Host-side archive-members Redis bulk invalidate + compose redis-cli / restart.

This module is intentionally free of ``print_utils`` / ``conf_parser`` imports so
``scripts/invalidate_archive_members.py`` can run on hosts whose default
``python3`` is older than 3.10 (PEP 604) without pulling the full sync_timedb
Redis L2 stack. Prefer Python >= 3.12 (``requires-python``); the script re-execs
when needed.
"""
from __future__ import annotations

import datetime
import shlex
import subprocess


DEFAULT_COMPOSE_PROJECT = "hpcperfstats"
PIPELINE_SERVICE = "pipeline"
REDIS_SERVICE = "redis"

# Keep in sync with sync_timedb_archive_members_redis.py key families.
_KEY_PREFIX = "hpcperfstats:sync_timedb"
_HASH_PREFIX = "%s:archive_members:hash:v1" % _KEY_PREFIX
_COMPLETE_PREFIX = "%s:archive_members:complete:v1" % _KEY_PREFIX
_LOCK_PREFIX = "%s:archive_members:lock:v1" % _KEY_PREFIX
_DEDUPE_HINT_PREFIX = "%s:archive_dedupe_hint:v1" % _KEY_PREFIX
_DEGRADED_PREFIX = "%s:archive_populate_degraded:v1" % _KEY_PREFIX
_DAY_SKIP_PREFIX = "%s:archive_day_ingest_skip:v1" % _KEY_PREFIX
_INVALIDATE_PENDING_PREFIX = "%s:archive_members:invalidate_pending:v1" % _KEY_PREFIX
_POPULATE_QUEUE_KEY = "%s:archive_members:populate_queue:v1" % _KEY_PREFIX
_POPULATE_QUEUED_PREFIX = "%s:archive_members:populate_queued:v1" % _KEY_PREFIX
_INGEST_TAR_HOT_PREFIX = "%s:ingest_tar_hot:v1" % _KEY_PREFIX
_ARCHIVE_APPEND_INFLIGHT_PREFIX = "%s:archive_append_inflight:v1" % _KEY_PREFIX
_DAILY_TAR_RESTORE_PREFIX = "%s:daily_tar_restore:v1" % _KEY_PREFIX

_MEMBERSHIP_IDENTITY_SCAN_PREFIXES = (
    _HASH_PREFIX,
    _COMPLETE_PREFIX,
    _LOCK_PREFIX,
    _INVALIDATE_PENDING_PREFIX,
)
_MEMBERSHIP_DAY_EXACT_SCAN_PREFIXES = (
    _DEGRADED_PREFIX,
    _DAY_SKIP_PREFIX,
    _DEDUPE_HINT_PREFIX,
    _POPULATE_QUEUED_PREFIX,
)
_PROTECTED_COORD_PREFIXES = (
    _INGEST_TAR_HOT_PREFIX,
    _ARCHIVE_APPEND_INFLIGHT_PREFIX,
    _DAILY_TAR_RESTORE_PREFIX,
)


def compose_argv(
    *,
    project=DEFAULT_COMPOSE_PROJECT,
    compose_files=None,
):
  """Build ``docker compose -p <project> [-f …]`` argv prefix."""
  argv = ["docker", "compose", "-p", str(project or DEFAULT_COMPOSE_PROJECT)]
  for path in compose_files or ():
    argv.extend(["-f", str(path)])
  return argv


class ComposeRedisCliClient:
  """Minimal Redis client via ``docker compose exec -T redis redis-cli``.

  Implements ``scan_iter`` / ``delete`` for
  :func:`invalidate_archive_members_redis_bulk`.
  """

  def __init__(
      self,
      *,
      compose_dir,
      project=DEFAULT_COMPOSE_PROJECT,
      compose_files=None,
      timeout_s=120.0,
  ):
    self.compose_dir = str(compose_dir)
    self.project = str(project or DEFAULT_COMPOSE_PROJECT)
    self.compose_files = list(compose_files or ())
    self.timeout_s = float(timeout_s)

  def _redis_cli(self, *args):
    cmd = compose_argv(project=self.project, compose_files=self.compose_files)
    cmd.extend(["exec", "-T", REDIS_SERVICE, "redis-cli"] + list(args))
    try:
      completed = subprocess.run(
          cmd,
          cwd=self.compose_dir,
          check=False,
          capture_output=True,
          text=True,
          timeout=self.timeout_s,
      )
    except FileNotFoundError as exc:
      raise RuntimeError(
          "docker compose not found on PATH; install Docker or pass --redis-url",
      ) from exc
    except subprocess.TimeoutExpired as exc:
      raise RuntimeError(
          "redis-cli via compose timed out after %.0fs" % self.timeout_s,
      ) from exc
    if completed.returncode != 0:
      err = (completed.stderr or completed.stdout or "").strip()
      raise RuntimeError(
          "compose redis-cli failed (exit %s): %s"
          % (completed.returncode, err or "(no output)"),
      )
    return completed.stdout or ""

  def scan_iter(self, match=None, count=100):
    del count
    pattern = match if match else "*"
    out = self._redis_cli("--scan", "--pattern", str(pattern))
    for line in out.splitlines():
      key = line.strip()
      if key:
        yield key

  def delete(self, *keys):
    if not keys:
      return 0
    deleted = 0
    batch_size = 100
    for i in range(0, len(keys), batch_size):
      chunk = [str(k) for k in keys[i:i + batch_size]]
      out = self._redis_cli(*(["DEL"] + chunk))
      try:
        deleted += int((out or "0").strip().splitlines()[-1])
      except (TypeError, ValueError, IndexError):
        deleted += len(chunk)
    return deleted


def restart_pipeline_compose(
    *,
    compose_dir,
    project=DEFAULT_COMPOSE_PROJECT,
    compose_files=None,
    timeout_s=300.0,
    run_fn=None,
):
  """Restart the Compose ``pipeline`` service on the host (clears worker L1)."""
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


def format_compose_cmd_for_log(argv):
  return " ".join(shlex.quote(str(part)) for part in argv)


def _normalize_bulk_day_tokens(day_tokens):
  """Validate and normalize calendar day tokens (``YYYY-MM-DD``)."""
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


def _bulk_membership_scan_patterns(day_tokens):
  patterns = []
  if day_tokens is None:
    for prefix in _MEMBERSHIP_IDENTITY_SCAN_PREFIXES:
      patterns.append("%s:*" % prefix)
    for prefix in _MEMBERSHIP_DAY_EXACT_SCAN_PREFIXES:
      patterns.append("%s:*" % prefix)
    return patterns
  for day in day_tokens:
    for prefix in _MEMBERSHIP_IDENTITY_SCAN_PREFIXES:
      patterns.append("%s:%s:*" % (prefix, day))
    for prefix in _MEMBERSHIP_DAY_EXACT_SCAN_PREFIXES:
      patterns.append("%s:%s" % (prefix, day))
  return patterns


def _is_protected_coord_redis_key(key):
  text = str(key)
  for prefix in _PROTECTED_COORD_PREFIXES:
    if text == prefix or text.startswith(prefix + ":"):
      return True
  return False


def invalidate_archive_members_redis_bulk(
    *,
    day_tokens=None,
    dry_run=False,
    client=None,
):
  """Bulk-clear archive membership Redis L2 (operator recovery).

  ``day_tokens=None`` clears all membership-related key families and the
  populate queue list. Otherwise only keys for those calendar days.

  Does **not** delete ``ingest_tar_hot``, ``archive_append_inflight``, or
  ``daily_tar_restore`` coordination keys.

  Returns ``{"scanned": int, "deleted": int, "dry_run": bool, "days": list}``.
  When ``client`` is omitted, returns ``"error": "redis_unavailable"`` (callers
  that have a Redis URL / FakeRedis must pass ``client`` explicitly).
  """
  days = _normalize_bulk_day_tokens(day_tokens)
  result = {
      "scanned": 0,
      "deleted": 0,
      "dry_run": bool(dry_run),
      "days": list(days) if days is not None else [],
  }
  if client is None:
    result["error"] = "redis_unavailable"
    return result

  found = set()
  for pattern in _bulk_membership_scan_patterns(days):
    for key in client.scan_iter(match=pattern, count=200):
      text = str(key)
      if _is_protected_coord_redis_key(text):
        continue
      found.add(text)
  if days is None:
    found.add(_POPULATE_QUEUE_KEY)

  result["scanned"] = len(found)
  if dry_run or not found:
    return result

  deleted = 0
  batch = []
  for key in sorted(found):
    batch.append(key)
    if len(batch) >= 200:
      client.delete(*batch)
      deleted += len(batch)
      batch.clear()
  if batch:
    client.delete(*batch)
    deleted += len(batch)
  result["deleted"] = deleted
  return result
