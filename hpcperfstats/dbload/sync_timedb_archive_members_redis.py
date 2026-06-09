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

_REDIS_CLIENT = None
_REDIS_CLIENT_URL = None


class ArchiveMembersRedisUnavailableError(RuntimeError):
  """Raised when Redis is required for archive member cache but unreachable."""


@dataclass(frozen=True)
class ArchiveMembersRedisKeys:
  day_token: str
  hash_key: str
  complete_key: str
  lock_key: str
  dedupe_hint_key: str


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


def _try_acquire_populate_lock(client, keys: ArchiveMembersRedisKeys) -> Optional[str]:
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
  return token


def _flush_hset_batch(client, keys: ArchiveMembersRedisKeys, batch: dict):
  if not batch:
    return
  pipe = client.pipeline()
  pipe.hset(
      keys.hash_key,
      mapping={name: str(int(size)) for name, size in batch.items()},
  )
  pipe.execute()
  _apply_ttl(client, keys)


def _estimate_hash_bytes(member_count: int) -> int:
  return member_count * 128


def populate_archive_members_redis(
    keys: ArchiveMembersRedisKeys,
    scan_fn: Callable[[Callable[[str, int], None]], tuple],
) -> dict:
  """Single-flight populate: ``scan_fn(on_member)`` returns ``(readable, saw_duplicates)``.

  ``scan_fn`` must return exactly two values; member sizes are collected via
  ``on_member`` callbacks during the scan (not from a third return value).
  """
  client = get_archive_members_redis_client(required=True)
  deadline = time.monotonic() + float(_populate_lock_seconds())
  while time.monotonic() < deadline:
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
          _flush_hset_batch(client, keys, pending_batch)
          pending_batch.clear()

    try:
      readable, scan_duplicates = scan_fn(_on_member)
      if not readable:
        raise ArchiveMembersRedisUnavailableError(
            "Archive member scan failed for %s" % keys.hash_key,
        )
      if scan_duplicates:
        saw_duplicates = True
      _flush_hset_batch(client, keys, pending_batch)
      if saw_duplicates and keys.day_token != "unknown":
        client.set(keys.dedupe_hint_key, "1", ex=_redis_ttl_seconds())
      client.set(keys.complete_key, "1", ex=_redis_ttl_seconds())
      _apply_ttl(client, keys)
      return dict(running_max)
    finally:
      _release_populate_lock(client, keys, token)

  raise ArchiveMembersRedisUnavailableError(
      "Timed out waiting for archive members populate lock: %s"
      % keys.lock_key,
  )


def wait_for_member_match(
    keys: ArchiveMembersRedisKeys,
    member_name: str,
    expected_size: int,
) -> bool:
  """Three-state point lookup with incremental HASH polling."""
  client = get_archive_members_redis_client(required=True)
  deadline = time.monotonic() + float(_populate_lock_seconds())
  expected_size = int(expected_size)
  while time.monotonic() < deadline:
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
    if not client.exists(keys.lock_key):
      existing = _hgetall_members(client, keys)
      if existing is not None:
        return existing.get(member_name) == expected_size
      token = _try_acquire_populate_lock(client, keys)
      if token is not None:
        _release_populate_lock(client, keys, token)
    time.sleep(_wait_poll_seconds())
  raise ArchiveMembersRedisUnavailableError(
      "Timed out waiting for member %s in %s"
      % (member_name, keys.hash_key),
  )


def wait_for_complete_members(keys: ArchiveMembersRedisKeys) -> dict:
  """Block until ``complete=1`` and return full member map."""
  client = get_archive_members_redis_client(required=True)
  deadline = time.monotonic() + float(_populate_lock_seconds())
  while time.monotonic() < deadline:
    members = _hgetall_members(client, keys)
    if members is not None:
      return members
    if not client.exists(keys.lock_key):
      time.sleep(_wait_poll_seconds())
      members = _hgetall_members(client, keys)
      if members is not None:
        return members
    time.sleep(_wait_poll_seconds())
  raise ArchiveMembersRedisUnavailableError(
      "Timed out waiting for complete archive members: %s" % keys.hash_key,
  )


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
  client.delete(keys.hash_key, keys.complete_key, keys.lock_key, keys.dedupe_hint_key)


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
