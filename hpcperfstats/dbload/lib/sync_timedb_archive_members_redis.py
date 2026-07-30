"""Redis L2 for daily archive member maps (single-flight populate, incremental HASH)."""
from __future__ import annotations

import contextvars
import json
import os
import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import date as date_cls
from typing import Callable, Dict, Optional

from hpcperfstats.dbload.lib import conf_parser as cfg
from hpcperfstats.dbload.lib.print_utils import log_print

_KEY_PREFIX = "hpcperfstats:sync_timedb"
_HASH_PREFIX = "%s:archive_members:hash:v1" % _KEY_PREFIX
_COMPLETE_PREFIX = "%s:archive_members:complete:v1" % _KEY_PREFIX
_LOCK_PREFIX = "%s:archive_members:lock:v1" % _KEY_PREFIX
_DEDUPE_HINT_PREFIX = "%s:archive_dedupe_hint:v1" % _KEY_PREFIX
_DEGRADED_PREFIX = "%s:archive_populate_degraded:v1" % _KEY_PREFIX
_DAY_SKIP_PREFIX = "%s:archive_day_ingest_skip:v1" % _KEY_PREFIX
_INVALIDATE_PENDING_PREFIX = "%s:archive_members:invalidate_pending:v1" % _KEY_PREFIX
_PROGRESS_SUFFIX = ":progress"
_POPULATE_QUEUE_KEY = "%s:archive_members:populate_queue:v1" % _KEY_PREFIX
_POPULATE_QUEUED_PREFIX = "%s:archive_members:populate_queued:v1" % _KEY_PREFIX
_INGEST_TAR_HOT_PREFIX = "%s:ingest_tar_hot:v1" % _KEY_PREFIX
_ARCHIVE_APPEND_INFLIGHT_PREFIX = "%s:archive_append_inflight:v1" % _KEY_PREFIX
_DAILY_TAR_RESTORE_PREFIX = "%s:daily_tar_restore:v1" % _KEY_PREFIX
_POPULATE_QUEUED_TTL_SECONDS = 3600
# Bounded peek when preferring ingest-hot jobs over cold FIFO day-close work.
_POPULATE_QUEUE_PREFER_PEEK_N = 64
_POPULATE_PREFER_INGEST_HOT_REASONS = frozenset({
    "chunk_prewarm",
    "populate_wait",
    "populate_enqueue",
})
# Higher rank wins when multiple queued days are ingest-hot.
_POPULATE_PREFER_REASON_RANK = {
    "chunk_prewarm": 3,
    "populate_wait": 2,
    "populate_enqueue": 1,
}
_IDENTITY_DRIFT_LOG_INTERVAL_S = 120.0
_STALE_INCOMPLETE_LOG_INTERVAL_S = 60.0
# Cluster-wide WARN gate (spawn pool cannot share process-local state).
_STALE_INCOMPLETE_LOG_REDIS_TTL_S = 300
_STALE_INCOMPLETE_LOG_PREFIX = "%s:stale_incomplete_log:v1" % _KEY_PREFIX
# Single-flight clear+re-enqueue after orphan / degraded / incomplete-after-lock.
_POPULATE_RECOVER_TTL_S = 60
_POPULATE_RECOVER_PREFIX = "%s:archive_populate_recover:v1" % _KEY_PREFIX
_IDENTITY_DRIFT_LOG_STATE: Dict[str, Dict[str, float]] = {}
_APPEND_INFLIGHT_DEFER_LOG_STATE: Dict[str, Dict[str, float]] = {}
_STALE_INCOMPLETE_LOG_STATE: Dict[str, Dict[str, float]] = {}

_archive_pre_append_member_lookup = contextvars.ContextVar(
    "sync_timedb_archive_pre_append_member_lookup",
    default=False,
)

_REDIS_CLIENT = None
_REDIS_CLIENT_URL = None


class ArchiveMembersRedisUnavailableError(RuntimeError):
  """Raised when Redis L2 archive member cache contract cannot be satisfied."""


class ArchiveMembersRedisConnectionError(ArchiveMembersRedisUnavailableError):
  """Raised when Redis ping or command I/O fails."""


class ArchiveMembersPopulateStalledError(ArchiveMembersRedisUnavailableError):
  """Raised when populate lock is held but shows no progress within stall limits."""


def is_transient_fnctl_populate_unavailable(exc) -> bool:
  """True when *exc* is a transient fnctl read-lock timeout during populate."""
  if not isinstance(exc, ArchiveMembersRedisUnavailableError):
    return False
  if isinstance(exc, (ArchiveMembersRedisConnectionError, ArchiveMembersPopulateStalledError)):
    return False
  msg = str(exc).lower()
  if "transient fnctl" in msg and "read lock timeout" in msg:
    return True
  return "timed out waiting" in msg and "fnctl.lock" in msg


def is_populate_pool_unavailable_error(exc) -> bool:
  """True when *exc* is populate-pool-down refuse-stream (recoverable, not L2 fatal).

  Spawn ingest/archive workers never share MainThread's PopulatePoolController
  global; they must enqueue Redis populate jobs and wait. A refuse-stream raise
  must not map to immediate ``sys.exit(1)``.
  """
  if not isinstance(exc, ArchiveMembersRedisUnavailableError):
    return False
  if isinstance(exc, (ArchiveMembersRedisConnectionError, ArchiveMembersPopulateStalledError)):
    return False
  msg = str(exc).lower()
  return "populate-pool unavailable" in msg or "refusing sealed stream" in msg


class IngestArchiveLookupBudgetExceededError(TimeoutError):
  """Raised when Redis archive duplicate-check exceeds ingest per-file budget."""


_ingest_task_deadline_monotonic = contextvars.ContextVar(
    "ingest_task_deadline_monotonic",
    default=None,
)

_ingest_task_effective_timeout_s = contextvars.ContextVar(
    "ingest_task_effective_timeout_s",
    default=None,
)


def set_ingest_task_deadline_monotonic(deadline):
  """Set monotonic deadline for ingest worker archive lookups (ContextVar)."""
  return _ingest_task_deadline_monotonic.set(deadline)


def reset_ingest_task_deadline_monotonic(token):
  _ingest_task_deadline_monotonic.reset(token)


def get_ingest_task_deadline_monotonic():
  return _ingest_task_deadline_monotonic.get()


def extend_ingest_task_deadline_monotonic(delta_seconds):
  """Extend active ingest worker deadline by populate-wait wall time."""
  delta_seconds = float(delta_seconds)
  if delta_seconds <= 0.0:
    return
  deadline = get_ingest_task_deadline_monotonic()
  if deadline is None:
    return
  _ingest_task_deadline_monotonic.set(float(deadline) + delta_seconds)


def set_ingest_task_effective_timeout_s(timeout_s):
  """Resolved per-file ingest budget for this worker task (ContextVar)."""
  return _ingest_task_effective_timeout_s.set(timeout_s)


def reset_ingest_task_effective_timeout_s(token):
  _ingest_task_effective_timeout_s.reset(token)


def get_ingest_task_effective_timeout_s():
  return _ingest_task_effective_timeout_s.get()


def _raise_if_ingest_deadline_exceeded():
  deadline = get_ingest_task_deadline_monotonic()
  if deadline is not None and time.monotonic() >= float(deadline):
    raise IngestArchiveLookupBudgetExceededError(
        "ingest archive lookup budget exceeded (deadline_monotonic=%s)"
        % deadline,
    )


def _raise_if_ingest_deadline_exceeded_when_enabled(respect_ingest_deadline):
  if respect_ingest_deadline:
    _raise_if_ingest_deadline_exceeded()


class ArchiveDayIngestSkipError(RuntimeError):
  """Sealed daily archive unreadable; ingest skips tar-append checks for the day."""

  def __init__(self, day_token, sealed_path, kind, detail):
    self.day_token = day_token
    self.sealed_path = sealed_path
    self.kind = kind
    self.detail = detail
    super().__init__(
        "archive day ingest skip day=%s sealed_path=%s kind=%s detail=%s"
        % (day_token, sealed_path, kind, detail),
    )


@dataclass(frozen=True)
class ArchiveMembersRedisKeys:
  day_token: str
  hash_key: str
  complete_key: str
  lock_key: str
  dedupe_hint_key: str
  invalidate_pending_key: str

  @property
  def progress_key(self) -> str:
    return "%s%s" % (self.hash_key, _PROGRESS_SUFFIX)

  @property
  def degraded_key(self) -> str:
    return "%s:%s" % (_DEGRADED_PREFIX, self.day_token)

  @property
  def day_skip_key(self) -> str:
    return "%s:%s" % (_DAY_SKIP_PREFIX, self.day_token)


def archive_members_redis_enabled() -> bool:
  return cfg.get_sync_archive_members_redis_enabled()


def _identity_pair(identity) -> tuple:
  if identity is None:
    return ("none", "none")
  return (str(int(identity[0])), str(int(identity[1])))


def build_archive_members_redis_keys(cache_key) -> ArchiveMembersRedisKeys:
  """Build Redis keys from ``_daily_archive_members_cache_key`` tuple."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      calendar_date_from_daily_tar_path,
      daily_tar_path_from_compressed,
  )

  canonical_zst_path, sealed_identity, tar_identity = cache_key
  tar_path = daily_tar_path_from_compressed(canonical_zst_path)
  day_date = calendar_date_from_daily_tar_path(tar_path)
  day_token = day_date.isoformat() if day_date is not None else "unknown"
  sealed_m, sealed_s = _identity_pair(sealed_identity)
  tar_m, tar_s = _identity_pair(tar_identity)
  suffix = "%s:%s:%s:%s:%s" % (day_token, sealed_m, sealed_s, tar_m, tar_s)
  return ArchiveMembersRedisKeys(
      day_token=day_token,
      hash_key="%s:%s" % (_HASH_PREFIX, suffix),
      complete_key="%s:%s" % (_COMPLETE_PREFIX, suffix),
      lock_key="%s:%s" % (_LOCK_PREFIX, suffix),
      dedupe_hint_key="%s:%s" % (_DEDUPE_HINT_PREFIX, day_token),
      invalidate_pending_key="%s:%s" % (_INVALIDATE_PENDING_PREFIX, suffix),
  )


def _redis_ttl_seconds() -> int:
  return cfg.get_sync_archive_members_redis_ttl_seconds()


def _populate_lock_seconds() -> int:
  return cfg.get_sync_archive_members_redis_populate_lock_seconds()


def _populate_stall_seconds() -> int:
  return cfg.get_sync_archive_members_redis_populate_stall_seconds()


def _populate_heartbeat_seconds() -> float:
  """Derived progress heartbeat interval (no separate ini key)."""
  return float(max(5, min(30, _populate_stall_seconds() // 4)))


def _populate_max_seconds() -> int:
  return cfg.get_sync_archive_members_redis_populate_max_seconds()


def _wait_poll_seconds() -> float:
  return cfg.get_sync_archive_members_redis_wait_poll_seconds()


def _hset_batch_size() -> int:
  return cfg.get_sync_archive_members_redis_hset_batch_size()


def _max_payload_bytes() -> int:
  return cfg.get_sync_archive_members_redis_max_payload_bytes()


def get_archive_members_redis_client(*, required: bool = True):
  """Return a shared ``redis.Redis`` client (decode_responses=True)."""
  global _REDIS_CLIENT, _REDIS_CLIENT_URL
  if not archive_members_redis_enabled():
    return None
  url = cfg.get_redis_location()
  if _REDIS_CLIENT is not None and _REDIS_CLIENT_URL == url:
    return _REDIS_CLIENT
  try:
    import redis
  except ImportError as exc:
    if required:
      raise ArchiveMembersRedisConnectionError(
          "redis package is not installed",
      ) from exc
    return None
  try:
    client = redis.from_url(url, decode_responses=True)
    client.ping()
  except Exception as exc:
    if required:
      raise ArchiveMembersRedisConnectionError(
          "Redis unreachable at [CACHE] redis_location=%s (%s)"
          % (url, exc),
      ) from exc
    return None
  _REDIS_CLIENT = client
  _REDIS_CLIENT_URL = url
  return client


def reset_archive_members_redis_client_for_tests():
  """Clear module-level client cache (unit tests)."""
  global _REDIS_CLIENT, _REDIS_CLIENT_URL
  _REDIS_CLIENT = None
  _REDIS_CLIENT_URL = None
  _STALE_INCOMPLETE_LOG_STATE.clear()


def verify_archive_members_redis_startup():
  """Fail closed at supervisor startup when Redis L2 is enabled."""
  if not archive_members_redis_enabled():
    return
  client = get_archive_members_redis_client(required=True)
  probe = "hpcperfstats:sync_timedb:startup_probe:%s" % uuid.uuid4().hex
  try:
    client.set(probe, "1", ex=30)
    if client.get(probe) != "1":
      raise ArchiveMembersRedisConnectionError(
          "Redis SET/GET smoke failed at [CACHE] redis_location=%s"
          % cfg.get_redis_location(),
      )
    client.delete(probe)
  except ArchiveMembersRedisUnavailableError:
    raise
  except Exception as exc:
    raise ArchiveMembersRedisConnectionError(
        "Redis startup probe failed at [CACHE] redis_location=%s (%s)"
        % (cfg.get_redis_location(), exc),
    ) from exc


def _apply_ttl(client, keys: ArchiveMembersRedisKeys):
  ttl = _redis_ttl_seconds()
  if ttl > 0:
    client.expire(keys.hash_key, ttl)
    client.expire(keys.complete_key, ttl)


def _hgetall_members(client, keys: ArchiveMembersRedisKeys) -> Optional[dict]:
  if client.get(keys.complete_key) != "1":
    return None
  raw = client.hgetall(keys.hash_key)
  if not raw:
    return {}
  return {name: int(size) for name, size in raw.items()}


def _hash_member_count(client, keys: ArchiveMembersRedisKeys) -> int:
  try:
    return int(client.hlen(keys.hash_key))
  except Exception:
    return len(client.hgetall(keys.hash_key))


def _touch_populate_progress(client, keys: ArchiveMembersRedisKeys):
  ttl = _redis_ttl_seconds()
  client.set(keys.progress_key, str(time.time()), ex=ttl if ttl > 0 else None)


def _read_populate_progress_ts(client, keys: ArchiveMembersRedisKeys) -> Optional[float]:
  raw = client.get(keys.progress_key)
  if raw is None:
    return None
  try:
    return float(raw)
  except (TypeError, ValueError):
    return None


def _renew_populate_lock(client, keys: ArchiveMembersRedisKeys):
  ttl = _populate_lock_seconds()
  if ttl > 0:
    client.expire(keys.lock_key, ttl)


def _encode_populate_lock_value(token: str) -> str:
  return "%s:%d" % (token, os.getpid())


def _parse_populate_lock_owner_pid(lock_value) -> Optional[int]:
  if not lock_value:
    return None
  text = str(lock_value)
  if ":" not in text:
    return None
  _token, _, pid_text = text.rpartition(":")
  try:
    return int(pid_text)
  except (TypeError, ValueError):
    return None


def _process_is_zombie(pid: int) -> bool:
  """Return True when ``pid`` exists as a zombie (state Z in ``/proc``)."""
  try:
    with open("/proc/%d/stat" % int(pid), "r", encoding="ascii") as proc_stat:
      stat_line = proc_stat.read()
  except OSError:
    return False
  rparen = stat_line.rfind(")")
  if rparen < 0 or rparen + 2 >= len(stat_line):
    return False
  return stat_line[rparen + 2] == "Z"


def _process_is_alive(pid: int) -> bool:
  try:
    os.kill(pid, 0)
  except ProcessLookupError:
    return False
  except PermissionError:
    return True
  except OSError:
    return False
  if _process_is_zombie(pid):
    return False
  return True


def _verify_redis_ping_or_raise(client):
  try:
    client.ping()
  except Exception as exc:
    raise ArchiveMembersRedisConnectionError(
        "Redis ping failed at [CACHE] redis_location=%s (%s)"
        % (cfg.get_redis_location(), exc),
    ) from exc


def _maybe_populate_heartbeat(
    client,
    keys: ArchiveMembersRedisKeys,
    last_heartbeat_monotonic,
):
  now = time.monotonic()
  if now - last_heartbeat_monotonic < _populate_heartbeat_seconds():
    return last_heartbeat_monotonic
  try:
    _touch_populate_progress(client, keys)
    _renew_populate_lock(client, keys)
  except Exception as exc:
    log_print(
        "WARNING: archive members populate heartbeat failed: %s" % exc,
        flush=True,
    )
    return last_heartbeat_monotonic
  return now


def _start_populate_heartbeat(client, keys: ArchiveMembersRedisKeys):
  stop = threading.Event()

  def _loop():
    while not stop.wait(_populate_heartbeat_seconds()):
      if stop.is_set():
        return
      try:
        _touch_populate_progress(client, keys)
        _renew_populate_lock(client, keys)
      except Exception as exc:
        log_print(
            "WARNING: archive members populate heartbeat failed: %s" % exc,
            flush=True,
        )

  thread = threading.Thread(target=_loop, daemon=True)
  thread.start()
  return stop, thread


def _stop_populate_heartbeat(stop, thread):
  stop.set()
  thread.join(timeout=max(1.0, _populate_heartbeat_seconds() * 2))


def populate_degraded_is_set(keys: ArchiveMembersRedisKeys, client=None) -> bool:
  if keys.day_token == "unknown":
    return False
  if client is None:
    client = get_archive_members_redis_client(required=False)
  if client is None:
    return False
  return bool(client.get(keys.degraded_key))


def _set_populate_degraded(client, keys: ArchiveMembersRedisKeys):
  if keys.day_token == "unknown":
    return
  ttl = _redis_ttl_seconds()
  client.set(keys.degraded_key, "1", ex=ttl if ttl > 0 else None)


def _encode_day_skip_value(kind: str, detail: str) -> str:
  safe_detail = (detail or "").replace(":", ";")[:500]
  return "%s:%s" % (kind, safe_detail)


def set_archive_day_ingest_skip(
    client,
    keys: ArchiveMembersRedisKeys,
    kind: str,
    detail: str,
):
  if keys.day_token == "unknown":
    return
  ttl = _redis_ttl_seconds()
  client.set(
      keys.day_skip_key,
      _encode_day_skip_value(kind, detail),
      ex=ttl if ttl > 0 else None,
  )


def clear_archive_day_ingest_skip(client, keys: ArchiveMembersRedisKeys):
  """Remove sticky day skip after tar repair (Fix D)."""
  if keys.day_token == "unknown":
    return
  client.delete(keys.day_skip_key)


def get_archive_day_ingest_skip(
    keys: ArchiveMembersRedisKeys,
    client=None,
) -> Optional[tuple]:
  if keys.day_token == "unknown":
    return None
  if client is None:
    client = get_archive_members_redis_client(required=False)
  if client is None:
    return None
  raw = client.get(keys.day_skip_key)
  if not raw:
    return None
  kind, _, detail = str(raw).partition(":")
  if not kind:
    return None
  return kind, detail


def archive_day_ingest_skip_error_from_redis(
    keys: ArchiveMembersRedisKeys,
    sealed_path: str,
    client=None,
) -> Optional[ArchiveDayIngestSkipError]:
  skip = get_archive_day_ingest_skip(keys, client=client)
  if skip is None:
    return None
  kind, detail = skip
  return ArchiveDayIngestSkipError(keys.day_token, sealed_path, kind, detail)


def _raise_if_archive_day_ingest_skip(
    keys: ArchiveMembersRedisKeys,
    sealed_path: str,
    client,
):
  skip_exc = archive_day_ingest_skip_error_from_redis(
      keys, sealed_path, client=client,
  )
  if skip_exc is not None:
    raise skip_exc


def redis_lookup_full_members(keys: ArchiveMembersRedisKeys) -> Optional[dict]:
  """Return member map when ``complete=1``, else ``None``."""
  if not archive_members_redis_enabled():
    return None
  client = get_archive_members_redis_client(required=True)
  return _hgetall_members(client, keys)


def redis_member_match_when_warm(
    keys: ArchiveMembersRedisKeys,
    member_name: str,
    expected_size,
    *,
    client=None,
) -> Optional[bool]:
  """Point ``HGET`` duplicate-check when Redis L2 is fully warm.

  Returns ``None`` when the HASH is not warm (caller uses populate / sealed path).
  When warm, returns ``True``/``False`` using the same size semantics as
  ``_member_match_via_redis_or_sealed_point`` for a single ``HGET``.
  """
  if not archive_members_redis_enabled():
    return None
  if client is None:
    client = get_archive_members_redis_client(required=True)
  if not redis_members_cache_is_fully_warm(keys, client=client):
    return None
  expected_size = int(expected_size)
  raw_size = client.hget(keys.hash_key, member_name)
  if raw_size is not None:
    size = int(raw_size)
    if size == expected_size:
      return True
    if size > expected_size:
      return False
  if client.get(keys.complete_key) == "1":
    return False
  return None


def redis_members_cache_is_fully_warm(
    keys: ArchiveMembersRedisKeys,
    *,
    client=None,
) -> bool:
  """True only when Redis reports ``complete=1`` with a non-empty member HASH.

  ``complete=1`` with an empty HASH is treated as **not warm** so supervisor
  prewarm re-populates stale or partial cache entries before ingest workers run.
  """
  if not archive_members_redis_enabled():
    return False
  if client is None:
    client = get_archive_members_redis_client(required=True)
  if client.get(keys.complete_key) != "1":
    return False
  return _hash_member_count(client, keys) > 0


def redis_members_populate_is_orphaned_incomplete(
    keys: ArchiveMembersRedisKeys,
    *,
    client=None,
) -> bool:
  """True when a partial HASH remains without ``complete=1`` and no populate lock."""
  if not archive_members_redis_enabled():
    return False
  if client is None:
    client = get_archive_members_redis_client(required=False)
    if client is None:
      return False
  if client.get(keys.complete_key) == "1":
    return False
  if client.exists(keys.lock_key):
    return False
  return _hash_member_count(client, keys) > 0


def maybe_clear_orphan_incomplete_archive_members_redis(
    keys: ArchiveMembersRedisKeys,
    *,
    client=None,
    log_fn=log_print,
) -> bool:
  """Drop a partial populate HASH so the next single-flight scan can restart."""
  if not redis_members_populate_is_orphaned_incomplete(keys, client=client):
    return False
  if client is None:
    client = get_archive_members_redis_client(required=True)
  hlen = _hash_member_count(client, keys)
  log_fn(
      "WARNING: clearing orphan incomplete archive members Redis "
      "hash=%s hlen=%d complete=0 lock=0"
      % (keys.hash_key, hlen),
      flush=True,
  )
  client.delete(
      keys.hash_key,
      keys.complete_key,
      keys.progress_key,
      keys.degraded_key,
      keys.invalidate_pending_key,
  )
  return True


def _incomplete_state_keys_present(client, keys: ArchiveMembersRedisKeys) -> bool:
  """True when any incomplete populate key exists (hash/complete/progress/…)."""
  for key in (
      keys.hash_key,
      keys.complete_key,
      keys.progress_key,
      keys.degraded_key,
      keys.invalidate_pending_key,
  ):
    if client.exists(key):
      return True
  return False


def _stale_incomplete_log_redis_key(day_token: str) -> str:
  return "%s:%s" % (_STALE_INCOMPLETE_LOG_PREFIX, day_token)


def _populate_recover_redis_key(day_token: str) -> str:
  return "%s:%s" % (_POPULATE_RECOVER_PREFIX, day_token)


def _try_acquire_populate_recovery_gate(client, day_token: str) -> bool:
  """Single-flight clear+re-enqueue after orphan/degraded/incomplete recovery.

  Returns True for the recovery leader (or when day_token is unknown / Redis
  SET fails open so recovery still progresses).
  """
  if not day_token or day_token == "unknown":
    return True
  try:
    return bool(
        client.set(
            _populate_recover_redis_key(day_token),
            "1",
            nx=True,
            ex=_POPULATE_RECOVER_TTL_S,
        ),
    )
  except Exception:
    return True


def clear_stale_incomplete_archive_members_redis(
    keys: ArchiveMembersRedisKeys,
    *,
    client=None,
    log_fn=log_print,
) -> bool:
  """Clear incomplete populate state even when HASH is empty (hlen=0).

  Used after lock release without ``complete=1`` so a new single-flight can
  acquire the lock (degraded must be cleared for ``_try_acquire_populate_lock``).
  No-ops silently when no incomplete keys are present (avoids WARN stampede
  after orphan wipe).
  """
  if client is None:
    client = get_archive_members_redis_client(required=True)
  if client.exists(keys.lock_key):
    return False
  if client.get(keys.complete_key) == "1":
    return False
  if not _incomplete_state_keys_present(client, keys):
    return False
  hlen = _hash_member_count(client, keys)
  _log_stale_incomplete_if_allowed(
      keys.day_token,
      "WARNING: clearing stale incomplete archive members Redis "
      "hash=%s hlen=%d complete=%s lock=0"
      % (
          keys.hash_key,
          hlen,
          client.get(keys.complete_key) or "-",
      ),
      log_fn=log_fn,
      client=client,
  )
  client.delete(
      keys.hash_key,
      keys.complete_key,
      keys.progress_key,
      keys.degraded_key,
      keys.invalidate_pending_key,
  )
  return True


def _release_populate_lock(client, keys: ArchiveMembersRedisKeys, lock_value: str):
  script = (
      "if redis.call('get', KEYS[1]) == ARGV[1] then "
      "return redis.call('del', KEYS[1]) else return 0 end"
  )
  try:
    client.eval(script, 1, keys.lock_key, lock_value)
  except Exception:
    client.delete(keys.lock_key)
  client.delete(keys.progress_key)


def _populate_lock_is_held(client, keys: ArchiveMembersRedisKeys) -> bool:
  """True when another populate winner holds ``lock_key``."""
  lock_value = client.get(keys.lock_key)
  if not lock_value:
    return False
  pid = _parse_populate_lock_owner_pid(lock_value)
  if pid is not None and not _process_is_alive(pid):
    return False
  progress_ts = _read_populate_progress_ts(client, keys)
  if progress_ts is not None:
    return True
  return pid is not None


def _try_acquire_populate_lock(client, keys: ArchiveMembersRedisKeys) -> Optional[str]:
  if populate_degraded_is_set(keys, client=client):
    return None
  token = secrets.token_hex(16)
  lock_value = _encode_populate_lock_value(token)
  acquired = client.set(
      keys.lock_key,
      lock_value,
      nx=True,
      ex=_populate_lock_seconds(),
  )
  if not acquired:
    return None
  client.set(keys.complete_key, "0", ex=_redis_ttl_seconds())
  _touch_populate_progress(client, keys)
  return lock_value


def _flush_hset_batch(
    client,
    keys: ArchiveMembersRedisKeys,
    batch: dict,
    *,
    lock_value=None,
):
  if not batch:
    return
  pipe = client.pipeline()
  pipe.hset(
      keys.hash_key,
      mapping={name: str(int(size)) for name, size in batch.items()},
  )
  pipe.execute()
  _apply_ttl(client, keys)
  if lock_value is not None:
    _renew_populate_lock(client, keys)
  _touch_populate_progress(client, keys)


def _estimate_hash_bytes(member_count: int) -> int:
  return member_count * 128


def _populate_progress_seen(
    client,
    keys: ArchiveMembersRedisKeys,
    *,
    last_progress_ts,
    last_hlen,
):
  progress_ts = _read_populate_progress_ts(client, keys)
  hlen = _hash_member_count(client, keys)
  if progress_ts is not None and progress_ts != last_progress_ts:
    return True, progress_ts, hlen
  if hlen > last_hlen:
    return True, progress_ts, hlen
  return False, last_progress_ts, hlen


def _release_stale_populate_lock_if_owner_dead(
    client,
    keys: ArchiveMembersRedisKeys,
    *,
    last_progress_monotonic,
    require_stall: bool = True,
) -> bool:
  """Release a populate lock when the owner PID is dead after stall window."""
  if not client.exists(keys.lock_key):
    return False
  if require_stall and (time.monotonic() - last_progress_monotonic) < float(
      _populate_stall_seconds(),
  ):
    return False
  if populate_degraded_is_set(keys, client=client):
    _raise_if_archive_day_ingest_skip(keys, "", client)
  _verify_redis_ping_or_raise(client)
  owner_pid = _parse_populate_lock_owner_pid(client.get(keys.lock_key))
  if owner_pid is not None and not _process_is_alive(owner_pid):
    log_print(
        "WARNING: archive members populate lock owner pid=%d dead; "
        "releasing stale lock for %s"
        % (owner_pid, keys.hash_key),
        flush=True,
    )
    client.delete(keys.lock_key)
    client.delete(keys.progress_key)
    return True
  return False


def _check_populate_wait_limits(
    client,
    keys: ArchiveMembersRedisKeys,
    *,
    started_monotonic,
    last_progress_monotonic,
    sealed_path="",
    respect_ingest_deadline=True,
) -> bool:
  """Return True when a stale populate lock was released for retry."""
  _raise_if_ingest_deadline_exceeded_when_enabled(respect_ingest_deadline)
  _raise_if_archive_day_ingest_skip(keys, sealed_path, client)
  max_seconds = _populate_max_seconds()
  if max_seconds > 0 and (time.monotonic() - started_monotonic) >= max_seconds:
    raise ArchiveMembersPopulateStalledError(
        "Timed out waiting for archive members populate (max_seconds=%s): %s"
        % (max_seconds, keys.hash_key),
    )
  lock_held = bool(client.exists(keys.lock_key))
  complete = client.get(keys.complete_key)
  if complete == "1":
    return False
  if lock_held:
    if _release_stale_populate_lock_if_owner_dead(
        client,
        keys,
        last_progress_monotonic=last_progress_monotonic,
    ):
      return True
    if (time.monotonic() - last_progress_monotonic) >= float(
        _populate_stall_seconds(),
    ):
      # Owner still alive within populate_max_seconds: keep waiting (heartbeat
      # may have failed briefly). Fatal only after max_seconds or dead owner.
      owner_pid = _parse_populate_lock_owner_pid(client.get(keys.lock_key))
      owner_alive = (
          owner_pid is not None and _process_is_alive(owner_pid)
      )
      if (
          owner_alive
          and max_seconds > 0
          and (time.monotonic() - started_monotonic) < max_seconds
      ):
        return False
      raise ArchiveMembersPopulateStalledError(
          "Archive members populate stalled (no progress for %ss): %s"
          % (_populate_stall_seconds(), keys.hash_key),
      )
    return False
  if complete == "0" or complete is None:
    if (time.monotonic() - last_progress_monotonic) >= float(
        _populate_stall_seconds(),
    ):
      if redis_members_populate_is_orphaned_incomplete(keys, client=client):
        maybe_clear_orphan_incomplete_archive_members_redis(keys, client=client)
        return True
      # Empty incomplete (hlen=0): recover within populate_max_seconds rather
      # than fatal on first stall — caller re-enqueues and may re-resolve identity.
      # When max_seconds is 0 (unlimited / tests), keep fail-closed on first stall.
      clear_stale_incomplete_archive_members_redis(keys, client=client)
      if max_seconds <= 0:
        raise ArchiveMembersPopulateStalledError(
            "Archive members populate incomplete after lock release: %s"
            % keys.hash_key,
        )
      _log_stale_incomplete_if_allowed(
          keys.day_token,
          "WARNING: archive members populate incomplete after lock release; "
          "recovering hash=%s"
          % keys.hash_key,
      )
      return True
  return False


def _extend_populate_acquire_deadline() -> float:
  return time.monotonic() + float(_populate_lock_seconds())


def _populate_lock_timeout_diagnostics(
    client,
    keys: ArchiveMembersRedisKeys,
) -> dict:
  day_token = keys.day_token if keys.day_token != "unknown" else ""
  lock_raw = client.get(keys.lock_key)
  owner_pid = _parse_populate_lock_owner_pid(lock_raw)
  progress_raw = client.get(keys.progress_key)
  progress_age_s = None
  if progress_raw:
    try:
      progress_age_s = max(0.0, time.time() - float(progress_raw))
    except (TypeError, ValueError):
      progress_age_s = None
  return {
      "lock_owner_pid": owner_pid,
      "owner_alive": (
          _process_is_alive(owner_pid) if owner_pid is not None else None
      ),
      "complete": client.get(keys.complete_key),
      "hlen": _hash_member_count(client, keys),
      "progress_age_s": progress_age_s,
      "append_inflight": (
          archive_append_inflight_for_day(day_token) if day_token else False
      ),
      "pre_append_exempt": archive_pre_append_member_lookup_active(),
  }


def _maybe_recover_orphan_incomplete_on_populate_lock_timeout(
    client,
    keys: ArchiveMembersRedisKeys,
) -> None:
  if client.get(keys.complete_key) == "1" or client.exists(keys.lock_key):
    return
  if _hash_member_count(client, keys) == 0:
    clear_stale_incomplete_archive_members_redis(keys, client=client)
    return
  maybe_clear_orphan_incomplete_archive_members_redis(keys, client=client)


def _raise_populate_lock_acquire_timeout(
    client,
    keys: ArchiveMembersRedisKeys,
) -> None:
  diag = _populate_lock_timeout_diagnostics(client, keys)
  _maybe_recover_orphan_incomplete_on_populate_lock_timeout(client, keys)
  log_print(
      "ERROR: archive members populate lock acquire timeout lock_key=%s "
      "lock_owner_pid=%s owner_alive=%s complete=%s hlen=%s progress_age_s=%s "
      "append_inflight=%s pre_append_exempt=%s"
      % (
          keys.lock_key,
          diag["lock_owner_pid"],
          diag["owner_alive"],
          diag["complete"],
          diag["hlen"],
          diag["progress_age_s"],
          diag["append_inflight"],
          diag["pre_append_exempt"],
      ),
      flush=True,
  )
  raise ArchiveMembersRedisUnavailableError(
      "Timed out waiting for archive members populate lock: %s"
      % keys.lock_key,
  )


def archive_pre_append_member_lookup_active() -> bool:
  return bool(_archive_pre_append_member_lookup.get())


class archive_pre_append_member_lookup_context:
  """Allow archive-pool pre-append member lookup during own append_inflight."""

  def __enter__(self):
    self._token = _archive_pre_append_member_lookup.set(True)
    return self

  def __exit__(self, _exc_type, _exc, _tb):
    _archive_pre_append_member_lookup.reset(self._token)
    return False


def populate_archive_members_redis(
    keys: ArchiveMembersRedisKeys,
    scan_fn: Callable[[Callable[[str, int], None]], tuple],
    *,
    sealed_path=None,
    source_decision=None,
    scanning_mutable_tar: bool = False,
) -> dict:
  """Single-flight populate: ``scan_fn(on_member)`` returns ``(readable, saw_duplicates)`` or
  ``(readable, saw_duplicates, stream_error)``.

  Member sizes are collected via ``on_member`` callbacks during the scan.
  """
  client = get_archive_members_redis_client(required=True)
  started_monotonic = time.monotonic()
  max_seconds = _populate_max_seconds()
  acquire_deadline = _extend_populate_acquire_deadline()
  last_lock_progress_monotonic = started_monotonic
  while True:
    now = time.monotonic()
    if max_seconds > 0 and (now - started_monotonic) >= max_seconds:
      raise ArchiveMembersPopulateStalledError(
          "Timed out waiting for archive members populate (max_seconds=%s): %s"
          % (max_seconds, keys.hash_key),
      )
    if now >= acquire_deadline:
      _raise_populate_lock_acquire_timeout(client, keys)

    maybe_clear_orphan_incomplete_archive_members_redis(keys, client=client)
    existing = _hgetall_members(client, keys)
    if existing is not None:
      return existing
    token = _try_acquire_populate_lock(client, keys)
    if token is None:
      if client.exists(keys.lock_key):
        progress_seen, _, _ = _populate_progress_seen(
            client,
            keys,
            last_progress_ts=None,
            last_hlen=0,
        )
        if progress_seen:
          last_lock_progress_monotonic = time.monotonic()
        if _release_stale_populate_lock_if_owner_dead(
            client,
            keys,
            last_progress_monotonic=last_lock_progress_monotonic,
            require_stall=False,
        ):
          continue
      defer_wait = (
          keys.day_token not in ("", "unknown")
          and archive_append_inflight_for_day(keys.day_token)
      )
      time.sleep(_wait_poll_seconds())
      if defer_wait:
        acquire_deadline = _extend_populate_acquire_deadline()
      continue

    lock_value = token
    if _populate_scan_should_defer(
        keys,
        sealed_path,
        scanning_mutable_tar=scanning_mutable_tar,
    ):
      _release_populate_lock(client, keys, lock_value)
      day_token = keys.day_token
      if day_token and archive_append_inflight_for_day(day_token):
        _log_append_inflight_defer_if_allowed(day_token)
      time.sleep(_wait_poll_seconds())
      acquire_deadline = _extend_populate_acquire_deadline()
      continue
    if source_decision is not None:
      from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
          _log_populate_source_decision,
      )
      _log_populate_source_decision(
          source_decision.get("day_token", keys.day_token),
          source_decision.get("tar_path", ""),
          source_decision.get("zst_path", ""),
          source_decision.get("gz_path", ""),
          source_decision.get("sealed_path") or "",
      )
    client.delete(keys.hash_key)
    running_max: Dict[str, int] = {}
    pending_batch: Dict[str, int] = {}
    saw_duplicates = False
    seen_in_stream: set = set()
    populate_failed = False
    last_heartbeat_monotonic = time.monotonic()

    def _on_member(name: str, size: int):
      nonlocal saw_duplicates, last_heartbeat_monotonic
      last_heartbeat_monotonic = _maybe_populate_heartbeat(
          client, keys, last_heartbeat_monotonic,
      )
      size = int(size)
      if name in seen_in_stream:
        saw_duplicates = True
      seen_in_stream.add(name)
      prev = running_max.get(name)
      if prev is None or size > prev:
        running_max[name] = size
        pending_batch[name] = running_max[name]
        if _estimate_hash_bytes(len(running_max)) > _max_payload_bytes():
          raise ArchiveMembersRedisUnavailableError(
              "Archive members Redis HASH exceeds max payload for %s"
              % keys.hash_key,
          )
        if len(pending_batch) >= _hset_batch_size():
          _flush_hset_batch(
              client, keys, pending_batch, lock_value=lock_value,
          )
          pending_batch.clear()
          last_heartbeat_monotonic = time.monotonic()

    try:
      heartbeat_stop, heartbeat_thread = _start_populate_heartbeat(client, keys)
      try:
        scan_result = scan_fn(_on_member)
      finally:
        _stop_populate_heartbeat(heartbeat_stop, heartbeat_thread)
      stream_error = None
      if len(scan_result) == 3:
        readable, scan_duplicates, stream_error = scan_result
      else:
        readable, scan_duplicates = scan_result
      if not readable:
        from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
            mark_archive_day_ingest_skip_and_raise,
        )
        mark_archive_day_ingest_skip_and_raise(
            sealed_path or "", keys, client, stream_error,
        )
      if scan_duplicates:
        saw_duplicates = True
      if client.get(keys.invalidate_pending_key):
        client.delete(keys.hash_key, keys.invalidate_pending_key)
        _release_populate_lock(client, keys, lock_value)
        continue
      _flush_hset_batch(client, keys, pending_batch, lock_value=lock_value)
      if saw_duplicates and keys.day_token != "unknown":
        client.set(keys.dedupe_hint_key, "1", ex=_redis_ttl_seconds())
      client.set(keys.complete_key, "1", ex=_redis_ttl_seconds())
      _apply_ttl(client, keys)
      client.delete(keys.progress_key)
      return dict(running_max)
    except Exception:
      populate_failed = True
      raise
    finally:
      if populate_failed:
        _set_populate_degraded(client, keys)
        # Fail-closed: drop partial HASH so orphan clear (hlen≈6500) cannot thrash.
        client.delete(
            keys.hash_key,
            keys.complete_key,
            keys.progress_key,
            keys.invalidate_pending_key,
        )
      _release_populate_lock(client, keys, lock_value)


def wait_for_member_match(
    keys: ArchiveMembersRedisKeys,
    member_name: str,
    expected_size: int,
    *,
    sealed_path="",
    respect_ingest_deadline=True,
    canonical="",
) -> bool:
  """Wait for populate completion or incremental HASH hit; stall if no progress."""
  from hpcperfstats.dbload.lib.sync_timedb_ingest_sigalrm import (
      populate_wait_ingest_sigalrm_guard,
  )

  client = get_archive_members_redis_client(required=True)
  expected_size = int(expected_size)
  started = time.monotonic()
  last_progress_ts = _read_populate_progress_ts(client, keys)
  last_hlen = _hash_member_count(client, keys)
  last_progress_monotonic = started

  with populate_wait_ingest_sigalrm_guard(
      respect_ingest_deadline=respect_ingest_deadline,
  ):
    while True:
      _raise_if_ingest_deadline_exceeded_when_enabled(respect_ingest_deadline)
      if canonical:
        warm_members, keys = _maybe_reresolve_warm_members(
            client,
            keys,
            canonical=canonical,
        )
        if warm_members is not None:
          size = warm_members.get(member_name)
          if size is None:
            return False
          return int(size) == expected_size
      _raise_if_archive_day_ingest_skip(keys, sealed_path, client)
      complete = client.get(keys.complete_key)
      raw_size = client.hget(keys.hash_key, member_name)
      if raw_size is not None:
        size = int(raw_size)
        if size == expected_size:
          return True
        if size > expected_size:
          return False
      if complete == "1":
        return False

      progress_seen, last_progress_ts, last_hlen = _populate_progress_seen(
          client,
          keys,
          last_progress_ts=last_progress_ts,
          last_hlen=last_hlen,
      )
      if progress_seen:
        last_progress_monotonic = time.monotonic()

      stale_lock_released = _check_populate_wait_limits(
          client,
          keys,
          started_monotonic=started,
          last_progress_monotonic=last_progress_monotonic,
          sealed_path=sealed_path,
          respect_ingest_deadline=respect_ingest_deadline,
      )
      if stale_lock_released:
        if canonical:
          warm_members, keys = _maybe_reresolve_warm_members(
              client,
              keys,
              canonical=canonical,
          )
          if warm_members is not None:
            size = warm_members.get(member_name)
            if size is None:
              return False
            return int(size) == expected_size
          recovered = _recover_populate_wait_after_stale_lock(
              client, keys, canonical=canonical,
          )
          if recovered:
            last_progress_monotonic = time.monotonic()
            last_progress_ts = _read_populate_progress_ts(client, keys)
            last_hlen = _hash_member_count(client, keys)
        elif not canonical:
          last_progress_monotonic = time.monotonic()
          last_progress_ts = _read_populate_progress_ts(client, keys)
          last_hlen = _hash_member_count(client, keys)

      if not client.exists(keys.lock_key):
        existing = _hgetall_members(client, keys)
        if existing is not None:
          return existing.get(member_name) == expected_size

      time.sleep(_wait_poll_seconds())


def _resolve_keys_for_canonical(canonical: str) -> ArchiveMembersRedisKeys:
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      _daily_archive_members_cache_key,
      normalize_daily_compressed_path,
  )

  path = normalize_daily_compressed_path(canonical)
  return build_archive_members_redis_keys(_daily_archive_members_cache_key(path))


def _log_stale_incomplete_if_allowed(
    day_token: str,
    message: str,
    *,
    log_fn=log_print,
    client=None,
) -> None:
  """Rate-limit stale incomplete WARNs cluster-wide (Redis NX) + process-local."""
  if not day_token or day_token == "unknown":
    log_fn(message, flush=True)
    return
  now_mono = time.monotonic()
  state = _STALE_INCOMPLETE_LOG_STATE.get(day_token)
  if state is None:
    state = {"last_log_mono": 0.0, "suppressed": 0.0}
    _STALE_INCOMPLETE_LOG_STATE[day_token] = state
  last_log_mono = float(state.get("last_log_mono") or 0.0)
  if now_mono - last_log_mono < _STALE_INCOMPLETE_LOG_INTERVAL_S:
    state["suppressed"] = float(state.get("suppressed") or 0.0) + 1.0
    return
  redis_allowed = True
  try:
    redis_client = client
    if redis_client is None:
      redis_client = get_archive_members_redis_client(required=False)
    if redis_client is not None:
      redis_allowed = bool(
          redis_client.set(
              _stale_incomplete_log_redis_key(day_token),
              "1",
              nx=True,
              ex=_STALE_INCOMPLETE_LOG_REDIS_TTL_S,
          ),
      )
  except Exception:
    redis_allowed = True
  if not redis_allowed:
    state["suppressed"] = float(state.get("suppressed") or 0.0) + 1.0
    return
  suppressed_n = int(state.get("suppressed") or 0)
  state["last_log_mono"] = now_mono
  state["suppressed"] = 0.0
  suffix = (" suppressed_n=%d" % suppressed_n) if suppressed_n else ""
  log_fn("%s%s" % (message, suffix), flush=True)


def _log_identity_drift_if_allowed(day_token: str, from_key: str, to_key: str) -> None:
  """Rate-limit identity_drift logs to once per calendar day per interval."""
  if not day_token or day_token == "unknown":
    return
  now_mono = time.monotonic()
  state = _IDENTITY_DRIFT_LOG_STATE.get(day_token)
  if state is None:
    state = {"last_log_mono": 0.0, "suppressed": 0.0}
    _IDENTITY_DRIFT_LOG_STATE[day_token] = state
  last_log_mono = float(state.get("last_log_mono") or 0.0)
  if now_mono - last_log_mono < _IDENTITY_DRIFT_LOG_INTERVAL_S:
    state["suppressed"] = float(state.get("suppressed") or 0.0) + 1.0
    return
  suppressed_n = int(state.get("suppressed") or 0)
  state["last_log_mono"] = now_mono
  state["suppressed"] = 0.0
  suffix = (" suppressed_n=%d" % suppressed_n) if suppressed_n else ""
  log_print(
      "INFO: populate_wait identity_drift day=%s from=%s to=%s%s"
      % (day_token, from_key, to_key, suffix),
      flush=True,
  )


def _log_append_inflight_defer_if_allowed(day_token: str) -> None:
  """Rate-limit archive_append_inflight defer logs to once per calendar day."""
  if not day_token or day_token == "unknown":
    return
  now_mono = time.monotonic()
  state = _APPEND_INFLIGHT_DEFER_LOG_STATE.get(day_token)
  if state is None:
    state = {"last_log_mono": 0.0, "suppressed": 0.0}
    _APPEND_INFLIGHT_DEFER_LOG_STATE[day_token] = state
  last_log_mono = float(state.get("last_log_mono") or 0.0)
  if now_mono - last_log_mono < _IDENTITY_DRIFT_LOG_INTERVAL_S:
    state["suppressed"] = float(state.get("suppressed") or 0.0) + 1.0
    return
  suppressed_n = int(state.get("suppressed") or 0)
  state["last_log_mono"] = now_mono
  state["suppressed"] = 0.0
  suffix = (" suppressed_n=%d" % suppressed_n) if suppressed_n else ""
  log_print(
      "populate: defer tar scan day=%s reason=archive_append_inflight%s"
      % (day_token, suffix),
      flush=True,
  )


def _maybe_reresolve_warm_members(
    client,
    keys: ArchiveMembersRedisKeys,
    *,
    canonical="",
):
  """Return members when *canonical* identity is fully warm (may differ from *keys*)."""
  if not canonical:
    return None, keys
  try:
    current_keys = _resolve_keys_for_canonical(canonical)
  except Exception:
    return None, keys
  if current_keys.hash_key != keys.hash_key:
    _log_identity_drift_if_allowed(
        current_keys.day_token, keys.hash_key, current_keys.hash_key,
    )
    keys = current_keys
  if not redis_members_cache_is_fully_warm(keys, client=client):
    return None, keys
  members = _hgetall_members(client, keys)
  if members is None:
    return None, keys
  return members, keys


def _recover_populate_wait_after_stale_lock(
    client,
    keys: ArchiveMembersRedisKeys,
    *,
    canonical="",
) -> bool:
  """Re-enqueue populate after incomplete lock release when *canonical* is known.

  Returns True only when enqueue succeeded (caller may reset stall clock).
  """
  if not canonical:
    return False
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      daily_archive_populate_source_exists,
      normalize_daily_compressed_path,
  )
  if not daily_archive_populate_source_exists(normalize_daily_compressed_path(canonical)):
    return False
  if not _try_acquire_populate_recovery_gate(client, keys.day_token):
    return False
  return bool(enqueue_archive_members_populate(canonical, keys.day_token))


def wait_for_complete_members(
    keys: ArchiveMembersRedisKeys,
    *,
    sealed_path="",
    respect_ingest_deadline=True,
    canonical="",
) -> dict:
  """Block until ``complete=1`` and return full member map.

  When *canonical* is set, each poll re-resolves the on-disk archive identity so
  concurrent tar append (identity drift T1→T2) can succeed when the current key
  is warm. Incomplete-after-lock-release recovers by clearing stale state and
  re-enqueuing until ``populate_max_seconds``.
  """
  from hpcperfstats.dbload.lib.sync_timedb_ingest_sigalrm import (
      populate_wait_ingest_sigalrm_guard,
  )

  client = get_archive_members_redis_client(required=True)
  started = time.monotonic()
  last_progress_ts = _read_populate_progress_ts(client, keys)
  last_hlen = _hash_member_count(client, keys)
  last_progress_monotonic = started
  incomplete_recover_n = 0

  with populate_wait_ingest_sigalrm_guard(
      respect_ingest_deadline=respect_ingest_deadline,
  ):
    while True:
      _raise_if_ingest_deadline_exceeded_when_enabled(respect_ingest_deadline)
      if canonical:
        warm_members, keys = _maybe_reresolve_warm_members(
            client,
            keys,
            canonical=canonical,
        )
        if warm_members is not None:
          return warm_members
      _raise_if_archive_day_ingest_skip(keys, sealed_path, client)
      members = _hgetall_members(client, keys)
      if members is not None:
        # complete=1 with empty HASH is not fully warm when tracking identity.
        if members or not canonical:
          return members

      progress_seen, last_progress_ts, last_hlen = _populate_progress_seen(
          client,
          keys,
          last_progress_ts=last_progress_ts,
          last_hlen=last_hlen,
      )
      if progress_seen:
        last_progress_monotonic = time.monotonic()
        incomplete_recover_n = 0

      stale_lock_released = _check_populate_wait_limits(
          client,
          keys,
          started_monotonic=started,
          last_progress_monotonic=last_progress_monotonic,
          sealed_path=sealed_path,
          respect_ingest_deadline=respect_ingest_deadline,
      )
      if stale_lock_released:
        if last_hlen <= 0:
          incomplete_recover_n += 1
          if incomplete_recover_n >= 3:
            # RC-ER: identity drift during live tar append leaves empty HASH
            # fingerprints; do not sys.exit(1) the supervisor mid-append.
            day_token = str(getattr(keys, "day_token", "") or "")
            if day_token and archive_append_inflight_for_day(day_token):
              incomplete_recover_n = 0
              log_print(
                  "INFO: populate empty recover deferred day=%s "
                  "reason=archive_append_inflight"
                  % day_token,
                  flush=True,
              )
            else:
              raise ArchiveMembersPopulateStalledError(
                  "Archive members populate incomplete after lock release "
                  "(empty recover bound): %s" % keys.hash_key,
              )
        warm_members, keys = _maybe_reresolve_warm_members(
            client,
            keys,
            canonical=canonical,
        )
        if warm_members is not None:
          return warm_members
        if canonical:
          from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
              daily_archive_populate_source_exists,
              normalize_daily_compressed_path,
          )
          if not daily_archive_populate_source_exists(
              normalize_daily_compressed_path(canonical),
          ):
            return {}
          recovered = _recover_populate_wait_after_stale_lock(
              client, keys, canonical=canonical,
          )
          if recovered:
            last_progress_monotonic = time.monotonic()
            last_progress_ts = _read_populate_progress_ts(client, keys)
            last_hlen = _hash_member_count(client, keys)

      time.sleep(_wait_poll_seconds())


def describe_archive_members_populate_redis_for_day(
    day_token: str,
    tgz_archive_dir: str,
) -> str:
  """One-line Redis populate snapshot for operator stall diagnostics."""
  if not archive_members_redis_enabled() or not day_token or not tgz_archive_dir:
    return "redis_populate=disabled"
  try:
    day_date = date_cls.fromisoformat(day_token)
  except ValueError:
    return "redis_populate=invalid_day"
  from hpcperfstats.dbload.lib.archive_compress import daily_compressed_path_for_date
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      _daily_archive_members_cache_key,
      normalize_daily_compressed_path,
  )

  compressed = daily_compressed_path_for_date(tgz_archive_dir, day_date)
  canonical = normalize_daily_compressed_path(compressed)
  cache_key = _daily_archive_members_cache_key(canonical)
  keys = build_archive_members_redis_keys(cache_key)
  client = get_archive_members_redis_client(required=False)
  if client is None:
    return "redis_populate=unavailable"
  complete = client.get(keys.complete_key)
  lock = bool(client.exists(keys.lock_key))
  hlen = _hash_member_count(client, keys)
  degraded = populate_degraded_is_set(keys, client=client)
  day_skip = get_archive_day_ingest_skip(keys, client=client) is not None
  return (
      "redis_populate lock=%d complete=%s hlen=%d degraded=%d day_skip=%d"
      % (
          int(lock),
          complete if complete is not None else "-",
          hlen,
          int(degraded),
          int(day_skip),
      )
  )


def archive_members_populate_shows_progress_for_day(
    day_token: str,
    tgz_archive_dir: str,
    *,
    progress_state=None,
) -> bool:
  """True when Redis shows an active populate for ``day_token``.

  Defers supervisor imap stall abort while a populate lock is held and
  ``complete != 1``, even when progress timestamps are briefly stale.
  """
  if not archive_members_redis_enabled() or not day_token or not tgz_archive_dir:
    return False
  try:
    day_date = date_cls.fromisoformat(day_token)
  except ValueError:
    return False
  from hpcperfstats.dbload.lib.archive_compress import daily_compressed_path_for_date
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      _daily_archive_members_cache_key,
      normalize_daily_compressed_path,
  )

  compressed = daily_compressed_path_for_date(tgz_archive_dir, day_date)
  canonical = normalize_daily_compressed_path(compressed)
  cache_key = _daily_archive_members_cache_key(canonical)
  keys = build_archive_members_redis_keys(cache_key)
  client = get_archive_members_redis_client(required=False)
  if client is None:
    return False
  if client.get(keys.complete_key) == "1":
    return False
  if client.exists(keys.lock_key):
    return True
  progress_ts = _read_populate_progress_ts(client, keys)
  hlen = _hash_member_count(client, keys)
  now = time.time()
  if progress_ts is not None and (now - progress_ts) < float(_populate_stall_seconds()):
    return True
  if progress_state is not None:
    prev = progress_state.get(day_token)
    if prev is not None:
      if hlen > prev.get("hlen", -1):
        progress_state[day_token] = {"hlen": hlen, "progress_ts": progress_ts}
        return True
      if progress_ts is not None and progress_ts != prev.get("progress_ts"):
        progress_state[day_token] = {"hlen": hlen, "progress_ts": progress_ts}
        return True
    progress_state[day_token] = {"hlen": hlen, "progress_ts": progress_ts}
  return False


def store_complete_members_in_redis(
    keys: ArchiveMembersRedisKeys,
    members: dict,
    *,
    saw_duplicates: bool = False,
):
  """Write a full member map (e.g. after plain ``.tar`` read)."""
  client = get_archive_members_redis_client(required=True)
  if _estimate_hash_bytes(len(members)) > _max_payload_bytes():
    raise ArchiveMembersRedisUnavailableError(
        "Archive members Redis HASH exceeds max payload for %s"
        % keys.hash_key,
    )
  pipe = client.pipeline()
  pipe.delete(keys.hash_key)
  if members:
    pipe.hset(keys.hash_key, mapping={k: str(int(v)) for k, v in members.items()})
  pipe.set(keys.complete_key, "1", ex=_redis_ttl_seconds())
  pipe.execute()
  _apply_ttl(client, keys)
  client.delete(keys.progress_key)
  if saw_duplicates and keys.day_token != "unknown":
    client.set(keys.dedupe_hint_key, "1", ex=_redis_ttl_seconds())


def merge_appended_members_into_redis(
    cache_key,
    member_map,
    *,
    saw_duplicates=False,
):
  """HSET appended tar members without clearing the Redis L2 map (``tar_append`` path).

  Returns ``True`` when Redis L2 is enabled and merge succeeded; ``False`` when
  disabled or ``member_map`` is empty (caller may fall back to full invalidation).
  """
  if not member_map:
    return False
  if not archive_members_redis_enabled():
    return False
  keys = build_archive_members_redis_keys(cache_key)
  client = get_archive_members_redis_client(required=True)
  pending_batch = {}
  for name, size in member_map.items():
    size = int(size)
    existing = client.hget(keys.hash_key, name)
    if existing is not None:
      try:
        if int(existing) >= size:
          continue
      except (TypeError, ValueError):
        pass
    pending_batch[name] = size
    if len(pending_batch) >= _hset_batch_size():
      _flush_hset_batch(client, keys, pending_batch)
      pending_batch.clear()
  if pending_batch:
    _flush_hset_batch(client, keys, pending_batch)
  client.set(keys.complete_key, "1", ex=_redis_ttl_seconds())
  _apply_ttl(client, keys)
  client.delete(keys.progress_key, keys.degraded_key)
  if saw_duplicates and keys.day_token != "unknown":
    client.set(keys.dedupe_hint_key, "1", ex=_redis_ttl_seconds())
  return True


def invalidate_archive_members_redis(cache_key):
  """Delete Redis L2 keys for a daily archive identity."""
  if not archive_members_redis_enabled():
    return
  try:
    client = get_archive_members_redis_client(required=False)
  except ArchiveMembersRedisUnavailableError:
    return
  if client is None:
    return
  keys = build_archive_members_redis_keys(cache_key)
  lock_held = _populate_lock_is_held(client, keys)
  delete_keys = [
      keys.hash_key,
      keys.complete_key,
      keys.dedupe_hint_key,
      keys.progress_key,
      keys.degraded_key,
      keys.day_skip_key,
  ]
  if lock_held:
    ttl = _redis_ttl_seconds()
    if ttl > 0:
      client.set(keys.invalidate_pending_key, "1", ex=ttl)
    else:
      client.set(keys.invalidate_pending_key, "1")
  else:
    delete_keys.append(keys.lock_key)
    client.delete(keys.invalidate_pending_key)
  client.delete(*delete_keys)


def invalidate_archive_members_redis_bulk(
    *,
    day_tokens=None,
    dry_run: bool = False,
    client=None,
):
  """Bulk-clear archive membership Redis L2 (operator recovery).

  Thin wrapper: resolves a Redis client when omitted, then delegates to
  :func:`hpcperfstats.dbload.lib.invalidate_archive_members_ops.invalidate_archive_members_redis_bulk`
  (stdlib-safe for the host CLI; no ``print_utils`` import on that path).
  """
  from hpcperfstats.dbload.lib import invalidate_archive_members_ops as ops

  if client is None:
    try:
      client = get_archive_members_redis_client(required=False)
    except ArchiveMembersRedisUnavailableError:
      client = None
  return ops.invalidate_archive_members_redis_bulk(
      day_tokens=day_tokens,
      dry_run=dry_run,
      client=client,
  )


def list_dedupe_hint_day_tokens(client=None) -> list:
  """Return calendar day tokens with active dedupe hints."""
  if client is None:
    if not archive_members_redis_enabled():
      return []
    client = get_archive_members_redis_client(required=False)
    if client is None:
      return []
  pattern = "%s:*" % _DEDUPE_HINT_PREFIX
  days = []
  for key in client.scan_iter(match=pattern, count=100):
    token = str(key).rsplit(":", 1)[-1]
    if token and token != "unknown":
      days.append(token)
  return sorted(set(days))


def clear_dedupe_hint(day_token: str, client=None):
  if not day_token or day_token == "unknown":
    return
  if client is None:
    if not archive_members_redis_enabled():
      return
    client = get_archive_members_redis_client(required=False)
    if client is None:
      return
  client.delete("%s:%s" % (_DEDUPE_HINT_PREFIX, day_token))


def dedupe_hint_is_set(day_token: str, client=None) -> bool:
  if not day_token or day_token == "unknown":
    return False
  if client is None:
    if not archive_members_redis_enabled():
      return False
    client = get_archive_members_redis_client(required=False)
    if client is None:
      return False
  return bool(client.get("%s:%s" % (_DEDUPE_HINT_PREFIX, day_token)))


def _ingest_tar_hot_key(day_token: str) -> str:
  return "%s:%s" % (_INGEST_TAR_HOT_PREFIX, day_token)


def _daily_tar_restore_key(day_token: str) -> str:
  return "%s:%s" % (_DAILY_TAR_RESTORE_PREFIX, day_token)


def set_ingest_tar_hot(day_token: str, *, reason: str = "populate") -> None:
  """Reserve ingest hot path for ``day_token`` before fnctl read (TTL = populate max)."""
  if not day_token or day_token == "unknown":
    return
  if not archive_members_redis_enabled():
    return
  client = get_archive_members_redis_client(required=False)
  if client is None:
    return
  client.set(
      _ingest_tar_hot_key(day_token),
      reason or "populate",
      ex=_populate_max_seconds(),
  )


def clear_ingest_tar_hot(day_token: str) -> None:
  if not day_token or day_token == "unknown":
    return
  if not archive_members_redis_enabled():
    return
  client = get_archive_members_redis_client(required=False)
  if client is None:
    return
  client.delete(_ingest_tar_hot_key(day_token))


def ingest_tar_hot_for_day(day_token: str) -> bool:
  if not day_token or not archive_members_redis_enabled():
    return False
  client = get_archive_members_redis_client(required=False)
  if client is None:
    return False
  return bool(client.exists(_ingest_tar_hot_key(day_token)))


# Waiter/enqueue/prewarm self-hot must not classify mid-scan tar EOF as forever-transient.
_SELF_INGEST_TAR_HOT_REASONS = frozenset({
    "populate_wait",
    "populate_enqueue",
    "chunk_prewarm",
    "populate",
})


def ingest_tar_hot_reason_for_day(day_token: str) -> str:
  """Return Redis ``ingest_tar_hot`` reason string, or empty when unset."""
  if not day_token or not archive_members_redis_enabled():
    return ""
  client = get_archive_members_redis_client(required=False)
  if client is None:
    return ""
  raw = client.get(_ingest_tar_hot_key(day_token))
  return str(raw) if raw else ""


def ingest_tar_hot_is_self_populate_only(day_token: str) -> bool:
  """True when hot is set solely by populate wait/enqueue/prewarm (not append)."""
  if not day_token or not ingest_tar_hot_for_day(day_token):
    return False
  if archive_append_inflight_for_day(day_token):
    return False
  reason = ingest_tar_hot_reason_for_day(day_token)
  return (not reason) or reason in _SELF_INGEST_TAR_HOT_REASONS


def _calendar_day_hint_from_stats_path(path: str) -> str:
  """Best-effort calendar day from a raw stats path basename epoch."""
  if not path:
    return ""
  base = os.path.basename(str(path))
  if not base.isdigit():
    return ""
  try:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(
        int(base), tz=timezone.utc,
    ).strftime("%Y-%m-%d")
  except (TypeError, ValueError, OSError, OverflowError):
    return ""


def _calendar_days_for_idle_skip_path(path: str, tgz_archive_dir: str = "") -> list:
  """Calendar days to check for idle-skip for one pending path (not global)."""
  days = []
  seen = set()
  day = _calendar_day_hint_from_stats_path(path)
  if day and day not in seen:
    seen.add(day)
    days.append(day)
  if not tgz_archive_dir or not path:
    return days
  try:
    from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
        _derive_stats_path_date,
    )

    derived = _derive_stats_path_date(path)
  except Exception:
    return days
  if derived is None:
    return days
  token = derived.strftime("%Y-%m-%d")
  if token not in seen:
    days.append(token)
  return days


def idle_pool_recover_skip_reason_for_paths(
    paths,
    tgz_archive_dir: str = "",
) -> str:
  """Non-empty reason when idle-pool recover/ghost fatal should be skipped.

  Workers blocked in ``populate_wait`` look wchan-idle while ``pending_async``
  still holds live work — recovering within ``IDLE_POOL_RECOVER_WALL_S`` causes
  false-positive exit 124.

  Only calendar days derived from **pending paths** (filename epoch and, when
  ``tgz_archive_dir`` is set, tar-aligned derive) are checked. Unrelated days
  with ``ingest_tar_hot`` (e.g. day-close June while July is pending) must not
  skip recover/redispatch for the pending work.
  """
  for path in paths or ():
    for day in _calendar_days_for_idle_skip_path(path, tgz_archive_dir):
      reason = ingest_tar_hot_reason_for_day(day)
      if reason in ("populate_wait", "populate_enqueue", "chunk_prewarm"):
        return "populate_wait day=%s reason=%s" % (day, reason)
      if tgz_archive_dir and archive_members_populate_shows_progress_for_day(
          day, tgz_archive_dir,
      ):
        return "populate_progress day=%s" % day
  return ""


def _archive_append_inflight_key(day_token: str) -> str:
  return "%s:%s" % (_ARCHIVE_APPEND_INFLIGHT_PREFIX, day_token)


def set_archive_append_inflight(day_token: str, *, reason: str = "archive_job") -> None:
  """Signal archive-pool append job in flight for a calendar day."""
  if not day_token or day_token == "unknown":
    return
  if not archive_members_redis_enabled():
    return
  client = get_archive_members_redis_client(required=False)
  if client is None:
    return
  client.set(
      _archive_append_inflight_key(day_token),
      reason or "archive_job",
      ex=_populate_max_seconds(),
  )


def clear_archive_append_inflight(day_token: str) -> None:
  if not day_token or day_token == "unknown":
    return
  if not archive_members_redis_enabled():
    return
  client = get_archive_members_redis_client(required=False)
  if client is None:
    return
  client.delete(_archive_append_inflight_key(day_token))


def archive_append_inflight_for_day(day_token: str) -> bool:
  if not day_token or not archive_members_redis_enabled():
    return False
  client = get_archive_members_redis_client(required=False)
  if client is None:
    return False
  return bool(client.exists(_archive_append_inflight_key(day_token)))


def _populate_scan_should_defer(
    keys: ArchiveMembersRedisKeys,
    sealed_path,
    *,
    scanning_mutable_tar: bool = False,
) -> bool:
  """Defer mutable-tar populate while archive-pool append is in flight.

  ``ingest_tar_hot`` is for janitor yield (``sync-timedb-hot-path-janitor-lock-priority``),
  not for blocking the populate winner that set the flag during chunk prewarm.

  Dirty-tar scans defer on ``archive_append_inflight`` even when a sealed sibling
  path string is known — do not start a losing mutable-tar scan under append.
  """
  day_token = keys.day_token
  if not day_token or day_token == "unknown":
    return False
  if archive_pre_append_member_lookup_active():
    return False
  if not archive_append_inflight_for_day(day_token):
    return False
  if scanning_mutable_tar:
    return True
  if sealed_path:
    return False
  return True


def _daily_tar_restore_lease_seconds() -> int:
  """Lease TTL for exclusive sealed→tar restore (renewed while decompressing)."""
  try:
    return max(60, int(cfg.get_sync_daily_tar_restore_lease_seconds()))
  except Exception:
    return max(60, int(_populate_max_seconds()) or 14400)


def try_acquire_daily_tar_restore(
    day_token: str,
    *,
    reason: str,
    caller: str,
) -> str:
  """Exclusive ``SET NX EX`` lease for materializing ``{day}.tar`` from sealed.

  Returns the owner lease value on success, or ``""`` when another owner holds
  the key / Redis is unavailable / day token is empty. Callers must only touch
  ``{day}.tar.decomp.tmp`` when this returns a non-empty lease value.
  """
  if not day_token or day_token == "unknown":
    return ""
  if not archive_members_redis_enabled():
    return ""
  client = get_archive_members_redis_client(required=False)
  if client is None:
    return ""
  token = secrets.token_hex(16)
  lease_value = "%s:%s:%s:%s" % (
      reason or "missing_tar",
      caller or "",
      os.getpid(),
      token,
  )
  acquired = client.set(
      _daily_tar_restore_key(day_token),
      lease_value,
      nx=True,
      ex=_daily_tar_restore_lease_seconds(),
  )
  if not acquired:
    return ""
  log_print(
      "archive: daily_tar_restore begin day=%s reason=%s caller=%s"
      % (day_token, reason or "missing_tar", caller or ""),
      flush=True,
  )
  return lease_value


def renew_daily_tar_restore_lease(day_token: str, lease_value: str) -> bool:
  """Refresh EXPIRE on the restore lease when still owned by ``lease_value``."""
  if not day_token or not lease_value:
    return False
  if not archive_members_redis_enabled():
    return False
  client = get_archive_members_redis_client(required=False)
  if client is None:
    return False
  key = _daily_tar_restore_key(day_token)
  script = (
      "if redis.call('get', KEYS[1]) == ARGV[1] then "
      "return redis.call('expire', KEYS[1], ARGV[2]) else return 0 end"
  )
  try:
    return bool(
        client.eval(
            script,
            1,
            key,
            lease_value,
            str(_daily_tar_restore_lease_seconds()),
        )
    )
  except Exception:
    raw = client.get(key)
    if raw is not None and str(raw) == lease_value:
      try:
        client.expire(key, _daily_tar_restore_lease_seconds())
        return True
      except Exception:
        return False
    return False


def set_daily_tar_restore_in_progress(
    day_token: str,
    *,
    reason: str,
    caller: str,
) -> str:
  """Acquire exclusive restore lease; return owner token or ``""`` on conflict.

  Prefer ``try_acquire_daily_tar_restore`` at new call sites. This wrapper
  remains for tests and older callers; it never overwrites an existing lease.
  """
  return try_acquire_daily_tar_restore(
      day_token,
      reason=reason,
      caller=caller,
  )


def clear_daily_tar_restore_in_progress(
    day_token: str,
    *,
    token: str = "",
    ok: bool = True,
    reason: str = "",
) -> None:
  """Compare-and-del restore lease. Non-owner / empty token is a no-op."""
  if not day_token or day_token == "unknown":
    return
  cleared = False
  end_reason = reason or "missing_tar"
  if archive_members_redis_enabled():
    client = get_archive_members_redis_client(required=False)
    if client is None:
      return
    key = _daily_tar_restore_key(day_token)
    if not token:
      # Owner-only release: refuse unconditional DELETE (dual-zstd race).
      return
    if not reason:
      raw = client.get(key)
      if raw:
        end_reason = str(raw).split(":", 1)[0]
    script = (
        "if redis.call('get', KEYS[1]) == ARGV[1] then "
        "return redis.call('del', KEYS[1]) else return 0 end"
    )
    try:
      cleared = bool(client.eval(script, 1, key, token))
    except Exception:
      raw = client.get(key)
      if raw is not None and str(raw) == token:
        client.delete(key)
        cleared = True
    if not cleared:
      return
  elif not token:
    return
  else:
    cleared = True
  if cleared:
    log_print(
        "archive: daily_tar_restore end day=%s ok=%s reason=%s"
        % (day_token, "yes" if ok else "no", end_reason),
        flush=True,
    )
  try:
    from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
        notify_daily_tar_restore_cleared,
    )

    notify_daily_tar_restore_cleared(day_token)
  except Exception:
    pass


def daily_tar_restore_in_progress_for_day(day_token: str) -> bool:
  if not day_token or not archive_members_redis_enabled():
    return False
  client = get_archive_members_redis_client(required=False)
  if client is None:
    return False
  return bool(client.exists(_daily_tar_restore_key(day_token)))


def daily_tar_restore_reason_for_day(day_token: str) -> str:
  if not day_token or not archive_members_redis_enabled():
    return ""
  client = get_archive_members_redis_client(required=False)
  if client is None:
    return ""
  raw = client.get(_daily_tar_restore_key(day_token))
  if not raw:
    return ""
  return str(raw).split(":", 1)[0]


def wait_for_daily_tar_restore_before_populate(tar_path: str, *, log_fn=log_print) -> None:
  """Block populate fnctl read while daily tar restore is in progress for this day."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      calendar_date_from_daily_tar_path,
  )

  day = calendar_date_from_daily_tar_path(tar_path or "")
  if day is None:
    return
  day_token = day.isoformat()
  if not daily_tar_restore_in_progress_for_day(day_token):
    return
  deadline = time.monotonic() + float(_populate_max_seconds())
  last_log = 0.0
  while daily_tar_restore_in_progress_for_day(day_token):
    if time.monotonic() >= deadline:
      break
    now = time.monotonic()
    if log_fn and (now - last_log) >= 5.0:
      log_fn(
          "populate: wait daily_tar_restore day=%s reason=%s"
          % (day_token, daily_tar_restore_reason_for_day(day_token) or "missing_tar"),
          flush=True,
      )
      last_log = now
    extend_ingest_task_deadline_monotonic(1.0)
    time.sleep(1.0)


def _populate_queued_key(day_token: str) -> str:
  return "%s:%s" % (_POPULATE_QUEUED_PREFIX, day_token)


def clear_populate_queued(day_token: str) -> None:
  """Clear the per-day populate enqueue NX key (claim / completion / failure)."""
  if not day_token or day_token == "unknown":
    return
  if not archive_members_redis_enabled():
    return
  client = get_archive_members_redis_client(required=False)
  if client is None:
    return
  try:
    client.delete(_populate_queued_key(day_token))
  except Exception:
    pass


def enqueue_archive_members_populate(canonical_path, day_token):
  """Enqueue one calendar day for populate-pool workers (deduped per day)."""
  if not day_token or day_token == "unknown":
    return False
  set_ingest_tar_hot(day_token, reason="populate_enqueue")
  client = get_archive_members_redis_client(required=True)
  queued_key = _populate_queued_key(day_token)
  if not client.set(queued_key, "1", nx=True, ex=_POPULATE_QUEUED_TTL_SECONDS):
    return False
  payload = json.dumps({
      "canonical": str(canonical_path),
      "day_token": str(day_token),
  })
  client.lpush(_POPULATE_QUEUE_KEY, payload)
  return True


def _parse_populate_queue_job(raw):
  try:
    job = json.loads(raw)
  except (TypeError, ValueError):
    return None
  if not isinstance(job, dict):
    return None
  return job


def _populate_prefer_reason_rank(day_token: str) -> int:
  """Return prefer rank for a day (0 = not ingest-hot / cold day-close)."""
  if not day_token:
    return 0
  reason = ingest_tar_hot_reason_for_day(day_token)
  if reason not in _POPULATE_PREFER_INGEST_HOT_REASONS:
    return 0
  return int(_POPULATE_PREFER_REASON_RANK.get(reason, 0))


def _try_pop_populate_prefer_ingest_hot(client, *, peek_n=None):
  """Pop highest-rank ingest-hot job within a bounded FIFO peek, or None.

  LPUSH + BRPOP is oldest-first. Day-close cold jobs share this queue with
  chunk prewarm; prefer ``chunk_prewarm`` / ``populate_wait`` over bare
  ``populate_enqueue``, and any ingest-hot over days with no hot key.
  """
  peek_n = max(1, int(peek_n if peek_n is not None else _POPULATE_QUEUE_PREFER_PEEK_N))
  lrange = getattr(client, "lrange", None)
  lrem = getattr(client, "lrem", None)
  if not callable(lrange) or not callable(lrem):
    return None
  # LPUSH list: index 0 = newest, -1 = oldest (next BRPOP). LRANGE -n -1 is
  # left-to-right within that window (newer … older).
  items = list(lrange(_POPULATE_QUEUE_KEY, -peek_n, -1) or ())
  if not items:
    return None
  fifo_head_raw = items[-1]
  best_rank = 0
  best_job = None
  best_raw = None
  # Oldest-first so equal ranks keep FIFO order.
  for raw in reversed(items):
    job = _parse_populate_queue_job(raw)
    if job is None:
      continue
    day = str(job.get("day_token") or "")
    rank = _populate_prefer_reason_rank(day)
    if rank <= 0:
      continue
    if rank > best_rank:
      best_rank = rank
      best_job = job
      best_raw = raw
  if best_job is None or best_raw is None or best_rank <= 0:
    return None
  # Preferred job is already the FIFO head — let BRPOP take it.
  if best_raw == fifo_head_raw:
    return None
  removed = int(lrem(_POPULATE_QUEUE_KEY, 1, best_raw) or 0)
  if removed <= 0:
    return None
  return best_job


def _claim_populate_queue_job(job):
  """Clear populate_queued NX when a job is claimed from the queue."""
  if not isinstance(job, dict):
    return job
  day_token = str(job.get("day_token") or "")
  if day_token:
    clear_populate_queued(day_token)
  return job


def archive_members_populate_queue_brpop(*, timeout_s=1.0):
  """Blocking pop for populate-pool worker; returns job dict or None.

  Prefers ingest-hot calendar days (``chunk_prewarm`` / ``populate_wait`` over
  ``populate_enqueue``) within a bounded peek so day-close cold populate cannot
  indefinitely starve July chunk prewarm on the shared FIFO queue.

  Clears ``populate_queued`` NX on successful claim so a dead populate worker
  cannot strand re-enqueue for the NX TTL (~1h).
  """
  client = get_archive_members_redis_client(required=True)
  preferred = _try_pop_populate_prefer_ingest_hot(client)
  if preferred is not None:
    return _claim_populate_queue_job(preferred)
  timeout_s = max(0.1, float(timeout_s))
  result = client.brpop(_POPULATE_QUEUE_KEY, timeout=timeout_s)
  if not result:
    return None
  _key, raw = result
  del _key
  return _claim_populate_queue_job(_parse_populate_queue_job(raw))


def _ensure_populate_pool_running_for_enqueue():
  """Best-effort MainThread ensure/restart before enqueue (no-op in spawn workers)."""
  from hpcperfstats.dbload.lib.sync_timedb_populate_pool import (
      get_populate_pool_controller,
  )

  controller = get_populate_pool_controller()
  if controller is None:
    return None
  if not controller.is_running():
    try:
      controller.reap_and_restart()
    except Exception as exc:
      log_print(
          "WARNING: populate-pool ensure/restart failed: %s" % exc,
          flush=True,
      )
  return controller


def _enqueue_or_run_archive_members_populate(canonical, day_token):
  """Enqueue populate-pool work, or run inline only when not ingest/archive pool.

  Ingest-pool and archive-pool must never fall through to
  ``execute_archive_members_populate_for_canonical`` (sealed stream under
  SIGALRM). Spawn workers do not share MainThread's PopulatePoolController
  global — they always enqueue to Redis and wait; MainThread populate-pool
  workers BRPOP. When MainThread's controller is down, ensure/restart then
  enqueue; only fall back to inline execute when pool kind is unset (tests).
  """
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      execute_archive_members_populate_for_canonical,
  )
  from hpcperfstats.dbload.lib.sync_timedb_ingest_worker_diagnostics import (
      get_worker_pool_kind,
  )

  kind = get_worker_pool_kind()
  if kind in ("ingest-pool", "archive-pool"):
    # Never stream locally. Controller is None in spawn children — enqueue anyway.
    enqueue_archive_members_populate(canonical, day_token)
    return

  controller = _ensure_populate_pool_running_for_enqueue()
  if controller is not None and controller.is_running():
    enqueue_archive_members_populate(canonical, day_token)
    return
  # MainThread / unset pool kind (unit tests): inline populate allowed.
  execute_archive_members_populate_for_canonical(canonical)


def request_archive_members_populate_and_wait(
    archive_compressed_path,
):
  """Wait for warm Redis member map; enqueue populate-pool work when cold.

  Ingest/archive pool callers must use this instead of running sealed streams
  locally. Populate wait paths ignore per-file ingest deadlines.
  """
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      _daily_archive_members_cache_key,
      _lookup_daily_archive_members_cache,
      _resolve_sealed_daily_archive_path,
      _store_daily_archive_members_cache,
      daily_archive_populate_source_exists,
      normalize_daily_compressed_path,
  )

  canonical = normalize_daily_compressed_path(archive_compressed_path)
  if not daily_archive_populate_source_exists(canonical):
    empty = {}
    _store_daily_archive_members_cache(canonical, empty)
    return dict(empty)
  cache_key = _daily_archive_members_cache_key(canonical)
  keys = build_archive_members_redis_keys(cache_key)
  day_token = keys.day_token if keys.day_token != "unknown" else ""
  tar_path = ""
  if day_token:
    from hpcperfstats.dbload.lib.archive_compress import daily_tar_path_from_compressed
    tar_path = daily_tar_path_from_compressed(canonical)
    set_ingest_tar_hot(day_token, reason="populate_wait")
    wait_for_daily_tar_restore_before_populate(tar_path or canonical, log_fn=log_print)
  try:
    cached = _lookup_daily_archive_members_cache(canonical)
    if cached is not None:
      # Empty L1 with Redis enabled is not proof of a warm day — fall through so
      # supervisor prewarm cannot claim success while Redis stays empty.
      # Non-empty L1 also must not short-circuit when Redis L2 is cold (operator:
      # empty after prewarm source=none members_n>0 within ~10ms).
      if not archive_members_redis_enabled():
        return dict(cached)
      if cached and redis_members_cache_is_fully_warm(keys):
        return dict(cached)
    if not archive_members_redis_enabled():
      raise ArchiveMembersRedisUnavailableError(
          "request_archive_members_populate_and_wait requires Redis L2",
      )
    members = redis_lookup_full_members(keys)
    if members is not None:
      _store_daily_archive_members_cache(canonical, members)
      return dict(members)
    sealed_path = _resolve_sealed_daily_archive_path(archive_compressed_path) or ""
    client = get_archive_members_redis_client(required=True)
    respect_ingest_deadline = False
    if client.exists(keys.lock_key):
      members = wait_for_complete_members(
          keys,
          sealed_path=sealed_path,
          respect_ingest_deadline=respect_ingest_deadline,
          canonical=canonical,
      )
    elif populate_degraded_is_set(keys, client=client):
      if get_archive_day_ingest_skip(keys, client=client) is not None:
        return {}
      # Recoverable: one waiter clears+reenqueues; peers only wait (no stampede).
      if _try_acquire_populate_recovery_gate(client, keys.day_token):
        clear_stale_incomplete_archive_members_redis(keys, client=client)
        _enqueue_or_run_archive_members_populate(canonical, keys.day_token)
      members = wait_for_complete_members(
          keys,
          sealed_path=sealed_path,
          respect_ingest_deadline=respect_ingest_deadline,
          canonical=canonical,
      )
    else:
      # Cold path, or peers that arrived after a recovery leader cleared degraded.
      # While recovery gate is held, skip re-enqueue (leader already queued work).
      recover_held = False
      if keys.day_token and keys.day_token != "unknown":
        try:
          recover_held = bool(
              client.exists(_populate_recover_redis_key(keys.day_token)),
          )
        except Exception:
          recover_held = False
      if not recover_held:
        _enqueue_or_run_archive_members_populate(canonical, keys.day_token)
      members = wait_for_complete_members(
          keys,
          sealed_path=sealed_path,
          respect_ingest_deadline=respect_ingest_deadline,
          canonical=canonical,
      )
    if members is not None:
      _store_daily_archive_members_cache(canonical, members)
      return dict(members)
    raise ArchiveMembersRedisUnavailableError(
        "archive members Redis enabled but lookup did not return members for %s"
        % canonical,
    )
  finally:
    if day_token:
      clear_ingest_tar_hot(day_token)
