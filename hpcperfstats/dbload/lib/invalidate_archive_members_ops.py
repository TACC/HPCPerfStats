"""
Host-side archive-members Redis bulk invalidate + compose redis-cli / restart.

This module is intentionally free of ``print_utils`` / ``conf_parser`` imports
so ``scripts/invalidate_archive_members.py`` can run on hosts whose default
``python3`` is older than 3.10 (PEP 604) without pulling the full sync_timedb
Redis L2 stack. Prefer Python >= 3.12 (``requires-python``); the script re-execs
when needed.

Attributes:
  DEFAULT_COMPOSE_PROJECT: Attribute.
  PIPELINE_SERVICE: Attribute.
  REDIS_SERVICE: Attribute.
  _ARCHIVE_APPEND_INFLIGHT_PREFIX: Attribute.
  _COMPLETE_PREFIX: Attribute.
  _DAILY_TAR_RESTORE_PREFIX: Attribute.
  _DAY_SKIP_PREFIX: Attribute.
  _DEDUPE_HINT_PREFIX: Attribute.
  _DEGRADED_PREFIX: Attribute.
  _HASH_PREFIX: Attribute.
  _INGEST_TAR_HOT_PREFIX: Attribute.
  _INVALIDATE_PENDING_PREFIX: Attribute.
  _KEY_PREFIX: Attribute.
  _LOCK_PREFIX: Attribute.
  _MEMBERSHIP_DAY_EXACT_SCAN_PREFIXES: Attribute.
  _MEMBERSHIP_IDENTITY_SCAN_PREFIXES: Attribute.
  _POPULATE_QUEUED_PREFIX: Attribute.
  _POPULATE_QUEUE_KEY: Attribute.
  _PROTECTED_COORD_PREFIXES: Attribute.
"""
from __future__ import annotations

from typing import Any, Iterator

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
  project: Any = DEFAULT_COMPOSE_PROJECT,
  compose_files: Any | None = None,
) -> Any:
  """
  Build ``docker compose -p <project> [-f …]`` argv prefix.
  
  Args:
    project (Any): Project passed to this helper.
    compose_files (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> compose_argv(None, None)  # doctest: +SKIP
  """
  argv = ["docker", "compose", "-p", str(project or DEFAULT_COMPOSE_PROJECT)]
  for path in compose_files or ():
    argv.extend(["-f", str(path)])
  return argv


class ComposeRedisCliClient:
  """
  Minimal Redis client via ``docker compose exec -T redis redis-cli``.
  
  Implements ``scan_iter`` / ``delete`` for
  :func:`invalidate_archive_members_redis_bulk`.
  
  Attributes:
    compose_dir: Attribute.
    compose_files: Attribute.
    project: Attribute.
    timeout_s: Attribute.
  """

  def __init__(
    self,
    *,
    compose_dir: str,
    project: Any = DEFAULT_COMPOSE_PROJECT,
    compose_files: Any | None = None,
    timeout_s: float = 120.0,
  ) -> None:
    """
    Initialize a new instance.
    
    Args:
      compose_dir (str): String for compose dir.
      project (Any): Project passed to this helper.
      compose_files (Any | None): One of ``Any``, ``None``.
      timeout_s (float): Floating-point value for timeout s.
    
    Returns:
      None
    
    Examples:
      >>> ComposeRedisCliClient("x", None, None, 0)  # doctest: +SKIP
    """
    self.compose_dir = str(compose_dir)
    self.project = str(project or DEFAULT_COMPOSE_PROJECT)
    self.compose_files = list(compose_files or ())
    self.timeout_s = float(timeout_s)

  def _redis_cli(self, *args: Any) -> Any:
    """
    Internal helper to handle redis cli.
    
    Args:
      *args (Any): Extra positional arguments; unused unless the callee
      documents a specific leftover protocol.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Raises:
      RuntimeError: Raised when ``_redis_cli`` hits a ``RuntimeError`` failure
      path.
    
    Examples:
      >>> ComposeRedisCliClient()._redis_cli()  # doctest: +SKIP
    """
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

  def scan_iter(
    self,
    match: Any | None = None,
    count: int = 100,
  ) -> Iterator[Any]:
    """
    Scan iter.
    
    Args:
      match (Any | None): One of ``Any``, ``None``.
      count (int): Integer value for count.
    
    Yields:
      Iterator[Any]: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> ComposeRedisCliClient().scan_iter(None, 0)  # doctest: +SKIP
    """
    del count
    pattern = match if match else "*"
    out = self._redis_cli("--scan", "--pattern", str(pattern))
    for line in out.splitlines():
      key = line.strip()
      if key:
        yield key

  def delete(self, *keys: Any) -> Any:
    """
    Delete a key from this store.
    
    Args:
      *keys (Any): Extra positional values for ``keys``; element types match
      the helper's documented protocol.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> ComposeRedisCliClient().delete()  # doctest: +SKIP
    """
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
  compose_dir: str,
  project: Any = DEFAULT_COMPOSE_PROJECT,
  compose_files: Any | None = None,
  timeout_s: float = 300.0,
  run_fn: Any | None = None,
) -> None:
  """
  Restart the Compose ``pipeline`` service on the host (clears worker L1).
  
  Args:
    compose_dir (str): String for compose dir.
    project (Any): Project passed to this helper.
    compose_files (Any | None): One of ``Any``, ``None``.
    timeout_s (float): Floating-point value for timeout s.
    run_fn (Any | None): One of ``Any``, ``None``.
  
  Returns:
    None
  
  Raises:
    RuntimeError: Raised when ``restart_pipeline_compose`` hits a
    ``RuntimeError`` failure path.
  
  Examples:
    >>> restart_pipeline_compose("x", None, None, 0, None)  # doctest: +SKIP
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


def format_compose_cmd_for_log(argv: Any) -> Any:
  """
  Format the compose cmd for log.
  
  Args:
    argv (Any): CLI argument list (``sys.argv``-like).
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> format_compose_cmd_for_log(None)  # doctest: +SKIP
  """
  return " ".join(shlex.quote(str(part)) for part in argv)


def _normalize_bulk_day_tokens(day_tokens: Any) -> Any:
  """
  Validate and normalize calendar day tokens (``YYYY-MM-DD``).
  
  Args:
    day_tokens (Any): Day tokens passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Raises:
    ValueError: Raised when ``_normalize_bulk_day_tokens`` hits a
    ``ValueError`` failure path.
  
  Examples:
    >>> _normalize_bulk_day_tokens(None)  # doctest: +SKIP
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


def _bulk_membership_scan_patterns(day_tokens: Any) -> Any:
  """
  Internal helper to handle bulk membership scan patterns.
  
  Args:
    day_tokens (Any): Day tokens passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _bulk_membership_scan_patterns(None)  # doctest: +SKIP
  """
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


def _is_protected_coord_redis_key(key: Any) -> Any:
  """
  Internal helper to check if protected coord redis key.
  
  Args:
    key (Any): Key passed to this helper.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> _is_protected_coord_redis_key(None)  # doctest: +SKIP
  """
  text = str(key)
  for prefix in _PROTECTED_COORD_PREFIXES:
    if text == prefix or text.startswith(prefix + ":"):
      return True
  return False


def invalidate_archive_members_redis_bulk(
  *,
  day_tokens: Any | None = None,
  dry_run: bool = False,
  client: Any | None = None,
) -> Any:
  """
  Bulk-clear archive membership Redis L2 (operator recovery).
  
  ``day_tokens=None`` clears all membership-related key families and the
  populate queue list. Otherwise only keys for those calendar days.
  
  Does **not** delete ``ingest_tar_hot``, ``archive_append_inflight``, or
  ``daily_tar_restore`` coordination keys.
  
  Returns ``{"scanned": int, "deleted": int, "dry_run": bool, "days": list}``.
  When ``client`` is omitted, returns ``"error": "redis_unavailable"`` (callers
  that have a Redis URL / FakeRedis must pass ``client`` explicitly).
  
  Args:
    day_tokens (Any | None): One of ``Any``, ``None``.
    dry_run (bool): Boolean flag for dry run.
    client (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> invalidate_archive_members_redis_bulk(None, True, None)
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
