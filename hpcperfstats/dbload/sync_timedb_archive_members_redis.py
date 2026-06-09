"""Redis L2 for daily archive member maps (single-flight populate, incremental HASH)."""
from __future__ import annotations

import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Callable, Dict, Optional

from hpcperfstats import conf_parser as cfg

_KEY_PREFIX = "hpcperfstats:sync_timedb"
_HASH_PREFIX = "%s:archive_members:hash:v1" % _KEY_PREFIX
_COMPLETE_PREFIX = "%s:archive_members:complete:v1" % _KEY_PREFIX
_LOCK_PREFIX = "%s:archive_members:lock:v1" % _KEY_PREFIX
_DEDUPE_HINT_PREFIX = "%s:archive_dedupe_hint:v1" % _KEY_PREFIX
_DEGRADED_PREFIX = "%s:archive_populate_degraded:v1" % _KEY_PREFIX
_DAY_SKIP_PREFIX = "%s:archive_day_ingest_skip:v1" % _KEY_PREFIX
_PROGRESS_SUFFIX = ":progress"

_REDIS_CLIENT = None
_REDIS_CLIENT_URL = None


class ArchiveMembersRedisUnavailableError(RuntimeError):
  """Raised when Redis is required for archive member cache but unreachable."""


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
  from hpcperfstats.dbload.sync_timedb_archive_helpers import (
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
  )


def _redis_ttl_seconds() -> int:
  return cfg.get_sync_archive_members_redis_ttl_seconds()


def _populate_lock_seconds() -> int:
  return cfg.get_sync_archive_members_redis_populate_lock_seconds()


def _populate_stall_seconds() -> int:
  return cfg.get_sync_archive_members_redis_populate_stall_seconds()


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
      raise ArchiveMembersRedisUnavailableError(
          "redis package is not installed",
      ) from exc
    return None
  try:
    client = redis.from_url(url, decode_responses=True)
    client.ping()
  except Exception as exc:
    if required:
      raise ArchiveMembersRedisUnavailableError(
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


def verify_archive_members_redis_startup():
  """Fail closed at supervisor startup when Redis L2 is enabled."""
  if not archive_members_redis_enabled():
    return
  client = get_archive_members_redis_client(required=True)
  probe = "hpcperfstats:sync_timedb:startup_probe:%s" % uuid.uuid4().hex
  try:
    client.set(probe, "1", ex=30)
    if client.get(probe) != "1":
      raise ArchiveMembersRedisUnavailableError(
          "Redis SET/GET smoke failed at [CACHE] redis_location=%s"
          % cfg.get_redis_location(),
      )
    client.delete(probe)
  except ArchiveMembersRedisUnavailableError:
    raise
  except Exception as exc:
    raise ArchiveMembersRedisUnavailableError(
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


def _release_populate_lock(client, keys: ArchiveMembersRedisKeys, token: str):
  script = (
      "if redis.call('get', KEYS[1]) == ARGV[1] then "
      "return redis.call('del', KEYS[1]) else return 0 end"
  )
  try:
    client.eval(script, 1, keys.lock_key, token)
  except Exception:
    client.delete(keys.lock_key)
  client.delete(keys.progress_key)


def _try_acquire_populate_lock(client, keys: ArchiveMembersRedisKeys) -> Optional[str]:
  if populate_degraded_is_set(keys, client=client):
    return None
  token = secrets.token_hex(16)
  acquired = client.set(
      keys.lock_key,
      token,
      nx=True,
      ex=_populate_lock_seconds(),
  )
  if not acquired:
    return None
  client.set(keys.complete_key, "0", ex=_redis_ttl_seconds())
  _touch_populate_progress(client, keys)
  return token


def _flush_hset_batch(
    client,
    keys: ArchiveMembersRedisKeys,
    batch: dict,
    *,
    lock_token=None,
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
  if lock_token is not None:
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


def _check_populate_wait_limits(
    client,
    keys: ArchiveMembersRedisKeys,
    *,
    started_monotonic,
    last_progress_monotonic,
    sealed_path="",
):
  _raise_if_archive_day_ingest_skip(keys, sealed_path, client)
  max_seconds = _populate_max_seconds()
  if max_seconds > 0 and (time.monotonic() - started_monotonic) >= max_seconds:
    raise ArchiveMembersRedisUnavailableError(
        "Timed out waiting for archive members populate (max_seconds=%s): %s"
        % (max_seconds, keys.hash_key),
    )
  lock_held = bool(client.exists(keys.lock_key))
  complete = client.get(keys.complete_key)
  if complete == "1":
    return
  if lock_held:
    if (time.monotonic() - last_progress_monotonic) >= float(
        _populate_stall_seconds(),
    ):
      raise ArchiveMembersRedisUnavailableError(
          "Archive members populate stalled (no progress for %ss): %s"
          % (_populate_stall_seconds(), keys.hash_key),
      )
    return
  if complete == "0" or complete is None:
    if (time.monotonic() - last_progress_monotonic) >= float(
        _populate_stall_seconds(),
    ):
      raise ArchiveMembersRedisUnavailableError(
          "Archive members populate incomplete after lock release: %s"
          % keys.hash_key,
      )


def populate_archive_members_redis(
    keys: ArchiveMembersRedisKeys,
    scan_fn: Callable[[Callable[[str, int], None]], tuple],
    *,
    sealed_path=None,
) -> dict:
  """Single-flight populate: ``scan_fn(on_member)`` returns ``(readable, saw_duplicates)`` or
  ``(readable, saw_duplicates, stream_error)``.

  Member sizes are collected via ``on_member`` callbacks during the scan.
  """
  client = get_archive_members_redis_client(required=True)
  acquire_deadline = time.monotonic() + float(_populate_lock_seconds())
  while time.monotonic() < acquire_deadline:
    existing = _hgetall_members(client, keys)
    if existing is not None:
      return existing
    token = _try_acquire_populate_lock(client, keys)
    if token is None:
      time.sleep(_wait_poll_seconds())
      continue

    client.delete(keys.hash_key)
    running_max: Dict[str, int] = {}
    pending_batch: Dict[str, int] = {}
    saw_duplicates = False
    seen_in_stream: set = set()
    populate_failed = False

    def _on_member(name: str, size: int):
      nonlocal saw_duplicates
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
              client, keys, pending_batch, lock_token=token,
          )
          pending_batch.clear()

    try:
      scan_result = scan_fn(_on_member)
      stream_error = None
      if len(scan_result) == 3:
        readable, scan_duplicates, stream_error = scan_result
      else:
        readable, scan_duplicates = scan_result
      if not readable:
        from hpcperfstats.dbload.sync_timedb_archive_helpers import (
            mark_archive_day_ingest_skip_and_raise,
        )
        mark_archive_day_ingest_skip_and_raise(
            sealed_path or "", keys, client, stream_error,
        )
      if scan_duplicates:
        saw_duplicates = True
      _flush_hset_batch(client, keys, pending_batch, lock_token=token)
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
      _release_populate_lock(client, keys, token)

  raise ArchiveMembersRedisUnavailableError(
      "Timed out waiting for archive members populate lock: %s"
      % keys.lock_key,
  )


def wait_for_member_match(
    keys: ArchiveMembersRedisKeys,
    member_name: str,
    expected_size: int,
    *,
    sealed_path="",
) -> bool:
  """Wait for populate completion or incremental HASH hit; stall if no progress."""
  client = get_archive_members_redis_client(required=True)
  expected_size = int(expected_size)
  started = time.monotonic()
  last_progress_ts = _read_populate_progress_ts(client, keys)
  last_hlen = _hash_member_count(client, keys)
  last_progress_monotonic = started

  while True:
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

    _check_populate_wait_limits(
        client,
        keys,
        started_monotonic=started,
        last_progress_monotonic=last_progress_monotonic,
        sealed_path=sealed_path,
    )

    if not client.exists(keys.lock_key):
      existing = _hgetall_members(client, keys)
      if existing is not None:
        return existing.get(member_name) == expected_size

    time.sleep(_wait_poll_seconds())


def wait_for_complete_members(
    keys: ArchiveMembersRedisKeys,
    *,
    sealed_path="",
) -> dict:
  """Block until ``complete=1`` and return full member map."""
  client = get_archive_members_redis_client(required=True)
  started = time.monotonic()
  last_progress_ts = _read_populate_progress_ts(client, keys)
  last_hlen = _hash_member_count(client, keys)
  last_progress_monotonic = started

  while True:
    _raise_if_archive_day_ingest_skip(keys, sealed_path, client)
    members = _hgetall_members(client, keys)
    if members is not None:
      return members

    progress_seen, last_progress_ts, last_hlen = _populate_progress_seen(
        client,
        keys,
        last_progress_ts=last_progress_ts,
        last_hlen=last_hlen,
    )
    if progress_seen:
      last_progress_monotonic = time.monotonic()

    _check_populate_wait_limits(
        client,
        keys,
        started_monotonic=started,
        last_progress_monotonic=last_progress_monotonic,
        sealed_path=sealed_path,
    )

    time.sleep(_wait_poll_seconds())


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
  client.delete(
      keys.hash_key,
      keys.complete_key,
      keys.lock_key,
      keys.dedupe_hint_key,
      keys.progress_key,
      keys.degraded_key,
      keys.day_skip_key,
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
