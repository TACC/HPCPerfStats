"""Tests for Redis L2 daily archive member cache (no Django)."""
from __future__ import annotations

import os
import tarfile
import threading
import time
from pathlib import Path

import pytest

from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
    _daily_archive_members_cache_key,
)
from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
    ArchiveDayIngestSkipError,
    ArchiveMembersPopulateStalledError,
    ArchiveMembersRedisUnavailableError,
    build_archive_members_redis_keys,
    clear_dedupe_hint,
    dedupe_hint_is_set,
    get_archive_day_ingest_skip,
    get_archive_members_redis_client,
    invalidate_archive_members_redis,
    invalidate_archive_members_redis_bulk,
    list_dedupe_hint_day_tokens,
    merge_appended_members_into_redis,
    populate_archive_members_redis,
    reset_archive_members_redis_client_for_tests,
    set_archive_day_ingest_skip,
    store_complete_members_in_redis,
    verify_archive_members_redis_startup,
    wait_for_complete_members,
    wait_for_member_match,
)

_COMPOSE = os.environ.get("HPCPERFSTATS_COMPOSE_NETWORK", "").strip().lower() in (
    "1",
    "yes",
    "true",
)
_LIVE = os.environ.get("HPCPERFSTATS_PYTEST_LIVE_REDIS", "").strip().lower() in (
    "1",
    "yes",
    "true",
)


class _FakePipeline:
  def __init__(self, redis):
    self._redis = redis
    self._ops = []

  def delete(self, *keys):
    self._ops.append(("delete", keys))
    return self

  def hset(self, key, field=None, value=None, mapping=None, **kwargs):
    payload = dict(mapping or kwargs)
    if field is not None:
      payload[field] = value
    self._ops.append(("hset", key, payload))
    return self

  def set(self, key, value, ex=None):
    self._ops.append(("set", key, value, ex))
    return self

  def execute(self):
    for op in self._ops:
      if op[0] == "delete":
        self._redis.delete(*op[1])
      elif op[0] == "hset":
        self._redis.hset(op[1], mapping=op[2])
      elif op[0] == "set":
        self._redis.set(op[1], op[2], ex=op[3])


class FakeRedis:
  def __init__(self):
    self._kv = {}
    self._hash = {}
    self._lists = {}
    self._lock = threading.Lock()

  def ping(self):
    return True

  def set(self, key, value, nx=False, ex=None):
    with self._lock:
      if nx and key in self._kv:
        return False
      self._kv[key] = value
      return True

  def get(self, key):
    with self._lock:
      return self._kv.get(key)

  def delete(self, *keys):
    with self._lock:
      for key in keys:
        self._kv.pop(key, None)
        self._hash.pop(key, None)
        self._lists.pop(key, None)

  def exists(self, key):
    with self._lock:
      return 1 if (key in self._kv or key in self._hash or key in self._lists) else 0

  def lpush(self, key, value):
    with self._lock:
      self._lists.setdefault(key, []).insert(0, value)
      return len(self._lists[key])

  def lrange(self, key, start, end):
    with self._lock:
      bucket = list(self._lists.get(key) or ())
    n = len(bucket)
    if n == 0:
      return []
    if start < 0:
      start = max(0, n + start)
    if end < 0:
      end = n + end
    end = min(n - 1, end)
    if start > end or start >= n:
      return []
    return bucket[start : end + 1]

  def lrem(self, key, count, value):
    with self._lock:
      bucket = self._lists.get(key)
      if not bucket:
        return 0
      removed = 0
      if count == 0:
        kept = [item for item in bucket if item != value]
        removed = len(bucket) - len(kept)
        if kept:
          self._lists[key] = kept
        else:
          del self._lists[key]
        return removed
      if count > 0:
        kept = []
        for item in bucket:
          if item == value and removed < count:
            removed += 1
            continue
          kept.append(item)
      else:
        kept = []
        to_remove = -count
        for item in reversed(bucket):
          if item == value and removed < to_remove:
            removed += 1
            continue
          kept.append(item)
        kept.reverse()
      if kept:
        self._lists[key] = kept
      else:
        del self._lists[key]
      return removed

  def brpop(self, key, timeout=0):
    deadline = time.monotonic() + float(timeout)
    while True:
      with self._lock:
        bucket = self._lists.get(key)
        if bucket:
          value = bucket.pop()
          if not bucket:
            del self._lists[key]
          return key, value
      if time.monotonic() >= deadline:
        return None
      time.sleep(min(0.02, max(0.0, deadline - time.monotonic())))

  def hset(self, key, field=None, value=None, mapping=None, **kwargs):
    with self._lock:
      bucket = self._hash.setdefault(key, {})
      if mapping is not None:
        for k, v in mapping.items():
          bucket[k] = str(v)
      elif kwargs:
        for k, v in kwargs.items():
          bucket[k] = str(v)
      elif field is not None:
        bucket[field] = str(value)

  def hget(self, key, field):
    with self._lock:
      return self._hash.get(key, {}).get(field)

  def hgetall(self, key):
    with self._lock:
      return dict(self._hash.get(key, {}))

  def hlen(self, key):
    with self._lock:
      return len(self._hash.get(key, {}))

  def expire(self, key, ttl):
    self._expire_calls = getattr(self, "_expire_calls", [])
    self._expire_calls.append((key, ttl))
    return True

  def pipeline(self):
    return _FakePipeline(self)

  def eval(self, script, _numkeys, key, token):
    with self._lock:
      if self._kv.get(key) == token:
        self._kv.pop(key, None)
        return 1
      return 0

  def scan_iter(self, match=None, count=100):
    del count
    prefix = match.rstrip("*") if match else ""
    with self._lock:
      # HASH maps live in `_hash`; string markers / progress in `_kv`; queue in `_lists`.
      universe = set(self._kv) | set(self._hash) | set(self._lists)
      keys = [key for key in sorted(universe) if key.startswith(prefix)]
    for key in keys:
      yield key


def _sample_cache_key(tmp_path, day="2026-05-09"):
  zst = tmp_path / ("%s.tar.zst" % day)
  tar = tmp_path / ("%s.tar" % day)
  zst.write_bytes(b"zst")
  tar.write_bytes(b"tar")
  sealed_id = (int(zst.stat().st_mtime_ns), int(zst.stat().st_size))
  tar_id = (int(tar.stat().st_mtime_ns), int(tar.stat().st_size))
  return (str(zst), sealed_id, tar_id)


@pytest.fixture(autouse=True)
def _redis_test_env(request, monkeypatch):
  reset_archive_members_redis_client_for_tests()
  if request.node.get_closest_marker("live_redis") and _LIVE:
    yield None
    reset_archive_members_redis_client_for_tests()
    return
  fake = FakeRedis()
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis.get_archive_members_redis_client",
      lambda required=True: fake,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_archive_members_redis_enabled",
      lambda: True,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_archive_members_redis_ttl_seconds",
      lambda: 3600,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_archive_members_redis_populate_lock_seconds",
      lambda: 5,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_archive_members_redis_populate_stall_seconds",
      lambda: 2,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_archive_members_redis_populate_max_seconds",
      lambda: 0,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_archive_members_redis_wait_poll_seconds",
      lambda: 0.01,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_archive_members_redis_hset_batch_size",
      lambda: 2,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_archive_members_redis_max_payload_bytes",
      lambda: 8388608,
  )
  yield fake
  reset_archive_members_redis_client_for_tests()


def test_redis_members_single_flight_one_scan(_redis_test_env, tmp_path):
  keys = build_archive_members_redis_keys(_sample_cache_key(tmp_path))
  scan_calls = {"n": 0}

  def _scan(on_member):
    scan_calls["n"] += 1
    on_member("host/a", 10)
    on_member("host/b", 20)
    return True, False

  m1 = populate_archive_members_redis(keys, _scan)
  m2 = populate_archive_members_redis(keys, _scan)
  assert scan_calls["n"] == 1
  assert m1 == {"host/a": 10, "host/b": 20}
  assert m2 == m1


def test_invalidate_does_not_drop_lock_during_populate(_redis_test_env, tmp_path):
  import time

  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      _populate_lock_is_held,
      invalidate_archive_members_redis,
  )

  cache_key = _sample_cache_key(tmp_path)
  keys = build_archive_members_redis_keys(cache_key)
  _redis_test_env.set(keys.lock_key, "tok:%d" % os.getpid(), ex=30)
  _redis_test_env.set(keys.complete_key, "1")
  _redis_test_env.hset(keys.hash_key, mapping={"host/a": "10"})
  _redis_test_env.set(keys.progress_key, str(time.time()))

  invalidate_archive_members_redis(cache_key)

  assert _redis_test_env.exists(keys.lock_key)
  assert _redis_test_env.get(keys.invalidate_pending_key) == "1"
  assert _redis_test_env.get(keys.complete_key) is None
  assert _populate_lock_is_held(_redis_test_env, keys)


def test_invalidate_drops_lock_when_populate_idle(_redis_test_env, tmp_path):
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      invalidate_archive_members_redis,
  )

  cache_key = _sample_cache_key(tmp_path)
  keys = build_archive_members_redis_keys(cache_key)
  _redis_test_env.set(keys.lock_key, "tok:999999", ex=30)
  _redis_test_env.set(keys.complete_key, "1")

  invalidate_archive_members_redis(cache_key)

  assert not _redis_test_env.exists(keys.lock_key)
  assert not _redis_test_env.exists(keys.complete_key)


def test_populate_retries_after_invalidate_pending(_redis_test_env, tmp_path):

  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      populate_archive_members_redis,
  )

  keys = build_archive_members_redis_keys(_sample_cache_key(tmp_path))
  scan_calls = {"n": 0}

  def _scan(on_member):
    scan_calls["n"] += 1
    on_member("host/a", 10)
    if scan_calls["n"] == 1:
      _redis_test_env.set(keys.invalidate_pending_key, "1", ex=60)
    return True, False

  members = populate_archive_members_redis(keys, _scan)
  assert scan_calls["n"] == 2
  assert members == {"host/a": 10}
  assert _redis_test_env.get(keys.complete_key) == "1"


def test_redis_members_waiter_early_positive_during_scan(_redis_test_env, tmp_path):
  keys = build_archive_members_redis_keys(_sample_cache_key(tmp_path))
  started = threading.Event()
  release = threading.Event()

  def _slow_scan(on_member):
    started.set()
    on_member("target/member", 42)
    on_member("padding/member", 1)
    release.wait(timeout=2)
    on_member("other/member", 1)
    return True, False

  waiter_result = {}

  def _waiter():
    waiter_result["ok"] = wait_for_member_match(keys, "target/member", 42)

  t_scan = threading.Thread(
      target=lambda: populate_archive_members_redis(keys, _slow_scan),
  )
  t_wait = threading.Thread(target=_waiter)
  t_scan.start()
  assert started.wait(timeout=2)
  t_wait.start()
  t_wait.join(timeout=2)
  release.set()
  t_scan.join(timeout=2)
  assert waiter_result.get("ok") is True


def test_redis_members_no_false_negative_until_complete(_redis_test_env, tmp_path):
  keys = build_archive_members_redis_keys(_sample_cache_key(tmp_path))
  _redis_test_env.set(keys.lock_key, "tok", ex=30)
  _redis_test_env.set(keys.complete_key, "0")
  _redis_test_env.set(keys.progress_key, str(time.time()))
  pending = {"done": False, "exc": None}

  def _waiter():
    try:
      pending["done"] = wait_for_member_match(keys, "missing/member", 99)
    except ArchiveMembersRedisUnavailableError as exc:
      pending["exc"] = exc

  t_wait = threading.Thread(target=_waiter)
  t_wait.start()
  time.sleep(0.05)
  assert pending["done"] is False and pending["exc"] is None
  _redis_test_env.set(keys.progress_key, str(time.time()))
  time.sleep(0.05)
  assert pending["done"] is False and pending["exc"] is None
  _redis_test_env.delete(keys.lock_key)
  _redis_test_env.set(keys.complete_key, "1")
  t_wait.join(timeout=2)
  assert pending["exc"] is None
  assert pending["done"] is False


def test_redis_members_definitive_negative_after_complete(_redis_test_env, tmp_path):
  keys = build_archive_members_redis_keys(_sample_cache_key(tmp_path))
  store_complete_members_in_redis(keys, {"present": 5})
  assert wait_for_member_match(keys, "absent", 5) is False


def test_redis_members_early_false_when_size_exceeds_expected(
    _redis_test_env, tmp_path,
):
  keys = build_archive_members_redis_keys(_sample_cache_key(tmp_path))
  _redis_test_env.set(keys.lock_key, "tok")
  _redis_test_env.set(keys.complete_key, "0")
  _redis_test_env.hset(keys.hash_key, mapping={"member": "99"})
  assert wait_for_member_match(keys, "member", 5) is False


def test_redis_members_waiter_retries_after_winner_crash(_redis_test_env, tmp_path):
  keys = build_archive_members_redis_keys(_sample_cache_key(tmp_path))
  scan_calls = {"n": 0}

  def _scan(on_member):
    scan_calls["n"] += 1
    on_member("only", 7)
    return True, False

  members = populate_archive_members_redis(keys, _scan)
  assert scan_calls["n"] == 1
  assert members == {"only": 7}


def test_redis_members_parallel_populate_different_days(_redis_test_env, tmp_path):
  key_a = build_archive_members_redis_keys(
      _sample_cache_key(tmp_path, day="2026-05-09"),
  )
  key_b = build_archive_members_redis_keys(
      _sample_cache_key(tmp_path, day="2026-05-10"),
  )
  calls = {"a": 0, "b": 0}

  def _scan_a(on_member):
    calls["a"] += 1
    on_member("a", 1)
    return True, False

  def _scan_b(on_member):
    calls["b"] += 1
    on_member("b", 2)
    return True, False

  populate_archive_members_redis(key_a, _scan_a)
  populate_archive_members_redis(key_b, _scan_b)
  assert calls == {"a": 1, "b": 1}


def test_redis_unavailable_raises_when_enabled(monkeypatch):
  reset_archive_members_redis_client_for_tests()
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_archive_members_redis_enabled",
      lambda: True,
  )

  def _boom(required=True):
    raise ArchiveMembersRedisUnavailableError("down")

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis.get_archive_members_redis_client",
      _boom,
  )
  with pytest.raises(ArchiveMembersRedisUnavailableError):
    get_archive_members_redis_client(required=True)


def test_redis_member_match_when_warm_uses_hget(_redis_test_env, tmp_path):
  keys = build_archive_members_redis_keys(_sample_cache_key(tmp_path))
  store_complete_members_in_redis(keys, {"host/a": 10, "host/b": 20})
  hgetall_calls = {"n": 0}
  original_hgetall = _redis_test_env.hgetall

  def counting_hgetall(key):
    hgetall_calls["n"] += 1
    return original_hgetall(key)

  _redis_test_env.hgetall = counting_hgetall
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      redis_member_match_when_warm,
  )

  assert redis_member_match_when_warm(keys, "host/a", 10) is True
  assert redis_member_match_when_warm(keys, "host/a", 9) is False
  assert redis_member_match_when_warm(keys, "missing", 1) is False
  assert hgetall_calls["n"] == 0


def test_invalidate_clears_redis_key(_redis_test_env, tmp_path):
  cache_key = _sample_cache_key(tmp_path)
  keys = build_archive_members_redis_keys(cache_key)
  store_complete_members_in_redis(keys, {"m": 1})
  _redis_test_env.set(keys.dedupe_hint_key, "1")
  invalidate_archive_members_redis(cache_key)
  assert _redis_test_env.get(keys.complete_key) is None
  assert _redis_test_env.hgetall(keys.hash_key) == {}
  assert _redis_test_env.get(keys.dedupe_hint_key) is None


def test_populate_sets_dedupe_hint_when_duplicates(_redis_test_env, tmp_path):
  keys = build_archive_members_redis_keys(_sample_cache_key(tmp_path))

  def _scan(on_member):
    on_member("dup", 1)
    on_member("dup", 2)
    return True, True

  populate_archive_members_redis(keys, _scan)
  assert dedupe_hint_is_set("2026-05-09", client=_redis_test_env)


def test_populate_redis_members_from_sealed_scan_wires_stream_fn(
    _redis_test_env, tmp_path,
):
  """Regression: _stream_compressed_archive_members returns 3-tuple; populate expects 2."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      _daily_archive_members_cache_key,
      _populate_redis_members_from_sealed_scan,
      normalize_daily_compressed_path,
  )
  from hpcperfstats.dbload.lib.sync_timedb_ingest_worker_diagnostics import (
      reset_worker_pool_kind,
      set_worker_pool_kind,
  )

  day_gz = tmp_path / "2024-06-10.tar.gz"
  inner = tmp_path / "payload.txt"
  inner.write_text("hello")
  with tarfile.open(day_gz, "w:gz") as tf:
    tf.add(str(inner), arcname="host/payload")

  sealed_path = str(day_gz)
  canonical = normalize_daily_compressed_path(sealed_path)
  cache_key = _daily_archive_members_cache_key(canonical)

  token = set_worker_pool_kind("populate-pool")
  try:
    members = _populate_redis_members_from_sealed_scan(sealed_path, cache_key)
  finally:
    reset_worker_pool_kind(token)
  assert members == {"host/payload": 5}


def test_populate_scan_failed_sets_day_skip(_redis_test_env, tmp_path):
  keys = build_archive_members_redis_keys(_sample_cache_key(tmp_path))
  sealed = str(tmp_path / "2024-06-10.tar.zst")
  Path(sealed).write_bytes(b"not-a-zst-frame")

  def _unreadable_scan(_on_member):
    return False, False, EOFError("unexpected end of data")

  with pytest.raises(ArchiveDayIngestSkipError) as exc_info:
    populate_archive_members_redis(
        keys, _unreadable_scan, sealed_path=sealed,
    )
  assert exc_info.value.kind == "zst_frame_invalid"
  assert get_archive_day_ingest_skip(keys, client=_redis_test_env) is not None
  assert _redis_test_env.exists(keys.lock_key) == 0
  assert _redis_test_env.get(keys.degraded_key) == "1"


def test_populate_failure_sets_day_skip_before_lock_release(_redis_test_env, tmp_path):
  keys = build_archive_members_redis_keys(_sample_cache_key(tmp_path))
  sealed = str(tmp_path / "2024-06-10.tar.zst")
  Path(sealed).write_bytes(b"bad")
  scanned = threading.Event()
  waiter_errors = []

  def _slow_unreadable_scan(_on_member):
    scanned.set()
    time.sleep(0.05)
    return False, False, EOFError("unexpected end of data")

  def _waiter():
    try:
      wait_for_member_match(
          keys, "host/member", 99, sealed_path=sealed,
      )
    except ArchiveDayIngestSkipError as exc:
      waiter_errors.append(exc)

  def _run_populate():
    try:
      populate_archive_members_redis(
          keys, _slow_unreadable_scan, sealed_path=sealed,
      )
    except ArchiveDayIngestSkipError as exc:
      pop_error["exc"] = exc

  pop_error = {}
  t_pop = threading.Thread(target=_run_populate)
  t_pop.start()
  scanned.wait(timeout=2)
  assert _redis_test_env.exists(keys.lock_key)
  t_wait = threading.Thread(target=_waiter)
  t_wait.start()
  t_pop.join(timeout=2)
  t_wait.join(timeout=2)
  assert pop_error.get("exc") is not None
  assert pop_error["exc"].kind == "zst_frame_invalid"
  assert len(waiter_errors) == 1
  assert waiter_errors[0].kind == "zst_frame_invalid"
  assert get_archive_day_ingest_skip(keys, client=_redis_test_env) is not None


def test_wait_for_member_exits_immediately_on_day_skip(_redis_test_env, tmp_path):
  keys = build_archive_members_redis_keys(_sample_cache_key(tmp_path))
  set_archive_day_ingest_skip(
      _redis_test_env, keys, "tar_truncated_or_unreadable", "unexpected end of data",
  )
  with pytest.raises(ArchiveDayIngestSkipError) as exc_info:
    wait_for_member_match(keys, "host/member", 4, sealed_path="/sealed/day.tar.zst")
  assert exc_info.value.kind == "tar_truncated_or_unreadable"


def test_stream_re_raises_archive_members_redis_unavailable_from_on_member(
    tmp_path,
):
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      _stream_compressed_archive_members,
  )

  day_gz = tmp_path / "2024-06-11.tar.gz"
  inner = tmp_path / "payload.txt"
  inner.write_text("hello")
  with tarfile.open(day_gz, "w:gz") as tf:
    tf.add(str(inner), arcname="host/payload")

  def on_member(_name, _size):
    raise ArchiveMembersRedisUnavailableError("payload limit")

  with pytest.raises(ArchiveMembersRedisUnavailableError, match="payload limit"):
    _stream_compressed_archive_members(
        str(day_gz), on_member, apply_priority_wrap=False,
    )


def test_verify_archive_members_redis_startup(_redis_test_env):
  verify_archive_members_redis_startup()


def test_list_dedupe_hint_day_tokens(_redis_test_env):
  _redis_test_env.set(
      "hpcperfstats:sync_timedb:archive_dedupe_hint:v1:2026-05-09",
      "1",
  )
  assert list_dedupe_hint_day_tokens(_redis_test_env) == ["2026-05-09"]
  clear_dedupe_hint("2026-05-09", client=_redis_test_env)
  assert list_dedupe_hint_day_tokens(_redis_test_env) == []


def test_wait_for_member_waits_until_complete(_redis_test_env, tmp_path):
  keys = build_archive_members_redis_keys(_sample_cache_key(tmp_path))
  _redis_test_env.set(keys.lock_key, "tok", ex=30)
  _redis_test_env.set(keys.complete_key, "0")
  _redis_test_env.set(keys.progress_key, str(time.time()))
  result = {"done": None}

  def _waiter():
    result["done"] = wait_for_member_match(keys, "missing/member", 99)

  t_wait = threading.Thread(target=_waiter)
  t_wait.start()
  time.sleep(0.05)
  assert result["done"] is None
  _redis_test_env.set(keys.progress_key, str(time.time()))
  time.sleep(0.05)
  assert result["done"] is None
  _redis_test_env.delete(keys.lock_key)
  _redis_test_env.set(keys.complete_key, "1")
  t_wait.join(timeout=2)
  assert result["done"] is False


def test_wait_for_member_stalls_without_progress(_redis_test_env, tmp_path):
  keys = build_archive_members_redis_keys(_sample_cache_key(tmp_path))
  _redis_test_env.set(keys.lock_key, "tok:%d" % os.getpid(), ex=30)
  _redis_test_env.set(keys.complete_key, "0")
  with pytest.raises(ArchiveMembersPopulateStalledError, match="stalled"):
    wait_for_member_match(keys, "missing/member", 99)


def test_populate_slow_scan_heartbeats_prevent_waiter_stall(
    _redis_test_env, tmp_path, monkeypatch,
):
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      "._populate_heartbeat_seconds",
      lambda: 0.05,
  )
  keys = build_archive_members_redis_keys(_sample_cache_key(tmp_path))

  def _slow_scan(on_member):
    for idx in range(4):
      time.sleep(1.5)
      on_member("member/%d" % idx, idx + 1)
    return True, False

  result = {"ok": None, "exc": None}

  def _waiter():
    try:
      result["ok"] = wait_for_member_match(keys, "member/3", 4)
    except ArchiveMembersRedisUnavailableError as exc:
      result["exc"] = exc

  t_scan = threading.Thread(
      target=lambda: populate_archive_members_redis(keys, _slow_scan),
  )
  t_wait = threading.Thread(target=_waiter)
  t_scan.start()
  time.sleep(0.1)
  t_wait.start()
  t_wait.join(timeout=12)
  t_scan.join(timeout=12)
  assert result["exc"] is None
  assert result["ok"] is True


def test_stale_populate_lock_released_when_owner_dead(_redis_test_env, tmp_path):
  import hpcperfstats.dbload.lib.sync_timedb_archive_members_redis as redis_mod

  keys = build_archive_members_redis_keys(_sample_cache_key(tmp_path))
  dead_pid = 999999997
  _redis_test_env.set(keys.lock_key, "tok:%d" % dead_pid, ex=30)
  _redis_test_env.set(keys.complete_key, "0")
  _redis_test_env.set(keys.progress_key, str(time.time() - 1000))
  released = redis_mod._check_populate_wait_limits(
      _redis_test_env,
      keys,
      started_monotonic=time.monotonic() - 10,
      last_progress_monotonic=time.monotonic() - 10,
  )
  assert released is True
  assert _redis_test_env.exists(keys.lock_key) == 0
  assert _redis_test_env.get(keys.progress_key) is None


def test_populate_lock_renewed_on_flush(_redis_test_env, tmp_path):
  keys = build_archive_members_redis_keys(_sample_cache_key(tmp_path))
  _redis_test_env._expire_calls = []

  def _scan(on_member):
    on_member("a", 1)
    on_member("b", 2)
    on_member("c", 3)
    return True, False

  populate_archive_members_redis(keys, _scan)
  lock_expires = [
      key for key, _ttl in _redis_test_env._expire_calls
      if key == keys.lock_key
  ]
  assert lock_expires


def test_wait_for_member_match_respects_ingest_deadline(_redis_test_env, tmp_path):
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      IngestArchiveLookupBudgetExceededError,
      reset_ingest_task_deadline_monotonic,
      set_ingest_task_deadline_monotonic,
  )

  keys = build_archive_members_redis_keys(_sample_cache_key(tmp_path))
  _redis_test_env.set(keys.lock_key, "tok:999999997", ex=30)
  _redis_test_env.set(keys.complete_key, "0")
  token = set_ingest_task_deadline_monotonic(time.monotonic() - 1.0)
  try:
    with pytest.raises(IngestArchiveLookupBudgetExceededError):
      wait_for_member_match(keys, "missing/member", 99)
  finally:
    reset_ingest_task_deadline_monotonic(token)


def test_redis_members_cache_is_fully_warm_requires_nonempty_hash(_redis_test_env, tmp_path):
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      redis_members_cache_is_fully_warm,
  )

  keys = build_archive_members_redis_keys(_sample_cache_key(tmp_path))
  assert redis_members_cache_is_fully_warm(keys, client=_redis_test_env) is False
  _redis_test_env.set(keys.complete_key, "1")
  assert redis_members_cache_is_fully_warm(keys, client=_redis_test_env) is False
  _redis_test_env.hset(keys.hash_key, mapping={"host/a": "1"})
  assert redis_members_cache_is_fully_warm(keys, client=_redis_test_env) is True


def test_orphan_incomplete_hash_detected_and_cleared(_redis_test_env, tmp_path):
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      maybe_clear_orphan_incomplete_archive_members_redis,
      redis_members_populate_is_orphaned_incomplete,
  )

  keys = build_archive_members_redis_keys(_sample_cache_key(tmp_path))
  _redis_test_env.hset(keys.hash_key, mapping={"host/a": "1000"})
  _redis_test_env.set(keys.complete_key, "0")
  assert redis_members_populate_is_orphaned_incomplete(keys, client=_redis_test_env)
  assert maybe_clear_orphan_incomplete_archive_members_redis(
      keys, client=_redis_test_env,
  )
  assert _redis_test_env.hlen(keys.hash_key) == 0
  assert redis_members_populate_is_orphaned_incomplete(keys, client=_redis_test_env) is False


def test_populate_acquire_clears_orphan_incomplete_hash(monkeypatch, tmp_path):
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      populate_archive_members_redis,
      reset_archive_members_redis_client_for_tests,
  )

  fake = FakeRedis()
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_archive_members_redis_enabled",
      lambda: True,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".get_archive_members_redis_client",
      lambda required=True: fake,
  )
  reset_archive_members_redis_client_for_tests()
  keys = build_archive_members_redis_keys(_sample_cache_key(tmp_path))
  fake.hset(keys.hash_key, mapping={"orphan/m": "1"})
  fake.set(keys.complete_key, "0")

  def _scan(on_member):
    on_member("fresh/m", 2)
    return True, False

  members = populate_archive_members_redis(keys, _scan, sealed_path=str(tmp_path))
  assert members == {"fresh/m": 2}
  assert fake.get(keys.complete_key) == "1"


def test_archive_members_populate_shows_progress_for_day(_redis_test_env, tmp_path):
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      archive_members_populate_shows_progress_for_day,
  )

  keys = build_archive_members_redis_keys(_sample_cache_key(tmp_path))
  _redis_test_env.set(keys.lock_key, "tok:1", ex=30)
  _redis_test_env.set(keys.complete_key, "0")
  _redis_test_env.set(keys.progress_key, str(time.time()))
  state = {}
  assert archive_members_populate_shows_progress_for_day(
      "2026-05-09",
      str(tmp_path),
      progress_state=state,
  ) is True
  _redis_test_env.set(keys.complete_key, "1")
  assert archive_members_populate_shows_progress_for_day(
      "2026-05-09",
      str(tmp_path),
      progress_state=state,
  ) is False


def test_archive_members_populate_shows_progress_with_stale_progress_ts(
    _redis_test_env,
    tmp_path,
):
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      archive_members_populate_shows_progress_for_day,
  )

  keys = build_archive_members_redis_keys(_sample_cache_key(tmp_path))
  _redis_test_env.set(keys.lock_key, "tok:1", ex=30)
  _redis_test_env.set(keys.complete_key, "0")
  _redis_test_env.set(keys.progress_key, str(time.time() - 10000))
  assert archive_members_populate_shows_progress_for_day(
      "2026-05-09",
      str(tmp_path),
  ) is True


def test_process_is_alive_treats_zombie_as_dead(monkeypatch):
  import hpcperfstats.dbload.lib.sync_timedb_archive_members_redis as redis_mod

  monkeypatch.setattr(redis_mod.os, "kill", lambda pid, sig: None)
  monkeypatch.setattr(redis_mod, "_process_is_zombie", lambda pid: True)
  assert redis_mod._process_is_alive(4242) is False


def test_stale_populate_lock_released_when_owner_zombie(
    _redis_test_env,
    tmp_path,
    monkeypatch,
):
  import hpcperfstats.dbload.lib.sync_timedb_archive_members_redis as redis_mod

  keys = build_archive_members_redis_keys(_sample_cache_key(tmp_path))
  zombie_pid = 424242
  _redis_test_env.set(keys.lock_key, "tok:%d" % zombie_pid, ex=30)
  _redis_test_env.set(keys.complete_key, "0")
  _redis_test_env.set(keys.progress_key, str(time.time() - 1000))
  monkeypatch.setattr(redis_mod.os, "kill", lambda pid, sig: None)
  monkeypatch.setattr(redis_mod, "_process_is_zombie", lambda pid: pid == zombie_pid)
  released = redis_mod._check_populate_wait_limits(
      _redis_test_env,
      keys,
      started_monotonic=time.monotonic() - 10,
      last_progress_monotonic=time.monotonic() - 10,
  )
  assert released is True
  assert _redis_test_env.exists(keys.lock_key) == 0
  assert _redis_test_env.get(keys.progress_key) is None


def test_stream_logs_generic_failure(tmp_path, capsys, monkeypatch):
  from contextlib import contextmanager

  from hpcperfstats.dbload.lib import sync_timedb_archive_helpers as helpers
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      _MemberStreamEarlyExit,
      _stream_compressed_archive_members,
  )

  day_gz = tmp_path / "2024-06-12.tar.gz"
  inner = tmp_path / "payload.txt"
  inner.write_text("hello")
  with tarfile.open(day_gz, "w:gz") as tf:
    tf.add(str(inner), arcname="host/payload")

  def on_member(_name, _size):
    raise _MemberStreamEarlyExit()

  with pytest.raises(_MemberStreamEarlyExit):
    _stream_compressed_archive_members(
        str(day_gz), on_member, apply_priority_wrap=False,
    )

  @contextmanager
  def _fail_read_lock(_path, timeout_seconds=None, expiry_seconds=None):
    del timeout_seconds, expiry_seconds
    raise IOError("disk read error")

  monkeypatch.setattr(helpers, "file_read_lock_wait", _fail_read_lock)
  readable, members, dups, stream_error = _stream_compressed_archive_members(str(day_gz))
  assert readable is False
  assert members == {}
  assert dups is False
  assert stream_error is not None
  captured = capsys.readouterr()
  assert "sealed archive member stream failed" in captured.out
  assert "disk read error" in captured.out


@pytest.mark.skipif(
    not (_COMPOSE and _LIVE),
    reason=(
        "Requires Docker Compose network and live Redis "
        "(HPCPERFSTATS_COMPOSE_NETWORK=1 and HPCPERFSTATS_PYTEST_LIVE_REDIS=1). "
        "Run: tests/run_redis_cache_pytest_workflow.sh"
    ),
)
@pytest.mark.live_redis
def test_archive_members_redis_populate_single_flight_compose(tmp_path):
  """Real redis hostname: single-flight populate and day_skip TTL."""
  reset_archive_members_redis_client_for_tests()
  verify_archive_members_redis_startup()
  cache_key = _sample_cache_key(tmp_path, day="2026-06-09")
  keys = build_archive_members_redis_keys(cache_key)
  invalidate_archive_members_redis(cache_key)
  scan_calls = {"n": 0}
  sealed_path = cache_key[0]

  def _scan(on_member):
    scan_calls["n"] += 1
    on_member("onlymember", 7)
    return True, False

  members = populate_archive_members_redis(
      keys, _scan, sealed_path=sealed_path,
  )
  assert members == {"onlymember": 7}
  assert scan_calls["n"] == 1
  client = get_archive_members_redis_client(required=True)
  assert get_archive_day_ingest_skip(keys, client=client) is None
  set_archive_day_ingest_skip(
      client, keys, "tar_truncated_or_unreadable", "compose smoke",
  )
  skip = get_archive_day_ingest_skip(keys, client=client)
  assert skip is not None
  assert skip[0] == "tar_truncated_or_unreadable"
  invalidate_archive_members_redis(cache_key)


def test_merge_appended_members_into_redis_keeps_existing(_redis_test_env, tmp_path):
  """Incremental merge HSETs new members without clearing the HASH."""
  fake = _redis_test_env
  cache_key = _sample_cache_key(tmp_path)
  keys = build_archive_members_redis_keys(cache_key)
  store_complete_members_in_redis(keys, {"existing": 42}, saw_duplicates=False)

  assert merge_appended_members_into_redis(
      cache_key,
      {"appended": 7},
      saw_duplicates=False,
  ) is True
  assert fake.hget(keys.hash_key, "existing") == "42"
  assert fake.hget(keys.hash_key, "appended") == "7"
  assert fake.get(keys.complete_key) == "1"


def test_ingest_populate_wait_ignores_per_file_deadline(monkeypatch, tmp_path):
  """Populate wait paths must not consult per-file ingest deadline ContextVar."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      request_archive_members_populate_and_wait,
      reset_ingest_task_deadline_monotonic,
      set_ingest_task_deadline_monotonic,
  )

  fake = FakeRedis()
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_archive_members_cache_enabled",
      lambda: True,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_sync_archive_members_redis_enabled",
      lambda: True,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".get_archive_members_redis_client",
      lambda required=True: fake,
  )
  reset_archive_members_redis_client_for_tests()

  day_gz = tmp_path / "2024-06-13.tar.gz"
  inner = tmp_path / "raw.txt"
  inner.write_text("data")
  with tarfile.open(day_gz, "w:gz") as tf:
    tf.add(str(inner), arcname="host/raw")
  keys = build_archive_members_redis_keys(_sample_cache_key(tmp_path))
  fake.set(keys.lock_key, "tok:999999997", ex=30)
  fake.set(keys.complete_key, "0")

  populate_done = threading.Event()

  def _finish_populate():
    time.sleep(0.05)
    fake.hset(keys.hash_key, mapping={"host/raw": "4"})
    fake.set(keys.complete_key, "1")
    populate_done.set()

  threading.Thread(target=_finish_populate, daemon=True).start()
  token = set_ingest_task_deadline_monotonic(time.monotonic() - 1.0)
  try:
    members = request_archive_members_populate_and_wait(str(day_gz))
  finally:
    reset_ingest_task_deadline_monotonic(token)
  assert populate_done.wait(timeout=2.0)
  assert members.get("host/raw") == 4


def test_populate_wait_succeeds_when_tar_identity_drifts_to_warm_key(
    _redis_test_env, tmp_path, monkeypatch,
):
  day = "2026-06-07"
  tar = tmp_path / ("%s.tar" % day)
  zst = tmp_path / ("%s.tar.zst" % day)
  tar.write_bytes(b"v1")
  canonical = str(zst)
  keys_t1 = build_archive_members_redis_keys(
      _daily_archive_members_cache_key(canonical),
  )
  assert "none:none" in keys_t1.hash_key
  result = {"members": None, "exc": None}

  def _waiter():
    try:
      result["members"] = wait_for_complete_members(
          keys_t1, canonical=canonical,
      )
    except Exception as exc:
      result["exc"] = exc

  t_wait = threading.Thread(target=_waiter)
  t_wait.start()
  time.sleep(0.05)
  tar.write_bytes(b"v1-appended-bytes")
  keys_t2 = build_archive_members_redis_keys(
      _daily_archive_members_cache_key(canonical),
  )
  assert keys_t1.hash_key != keys_t2.hash_key
  _redis_test_env.hset(keys_t2.hash_key, mapping={"host/a": "10"})
  _redis_test_env.set(keys_t2.complete_key, "1")
  t_wait.join(timeout=3)
  assert result["exc"] is None
  assert result["members"] == {"host/a": 10}


def test_wait_for_member_match_reresolves_warm_sealed_when_canonical(
    _redis_test_env, tmp_path,
):
  """Locked incomplete identity must not hang when sealed warm exists for canonical."""
  day = "2026-07-17"
  tar = tmp_path / ("%s.tar" % day)
  zst = tmp_path / ("%s.tar.zst" % day)
  tar.write_bytes(b"v1")
  canonical = str(zst)
  keys_dirty = build_archive_members_redis_keys(
      _daily_archive_members_cache_key(canonical),
  )
  _redis_test_env.set(keys_dirty.lock_key, "tok:1", ex=30)
  _redis_test_env.set(keys_dirty.complete_key, "0")
  tar.write_bytes(b"v1-appended")
  keys_warm = build_archive_members_redis_keys(
      _daily_archive_members_cache_key(canonical),
  )
  assert keys_dirty.hash_key != keys_warm.hash_key
  _redis_test_env.hset(keys_warm.hash_key, mapping={"host/m": "42"})
  _redis_test_env.set(keys_warm.complete_key, "1")
  t0 = time.monotonic()
  matched = wait_for_member_match(
      keys_dirty,
      "host/m",
      42,
      canonical=canonical,
      respect_ingest_deadline=False,
  )
  assert matched is True
  assert time.monotonic() - t0 < 2.0


def test_member_match_call_passes_canonical(monkeypatch):
  from hpcperfstats.dbload.lib import sync_timedb_archive_helpers as helpers

  captured = {}

  def _fake_wait(keys, member_name, expected_size, **kwargs):
    captured["kwargs"] = kwargs
    captured["member"] = member_name
    return True

  class _Client:
    def exists(self, key):
      return 1

    def get(self, key):
      return None

    def hget(self, *args):
      return None

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".wait_for_member_match",
      _fake_wait,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".populate_degraded_is_set",
      lambda *_a, **_k: False,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_ingest_worker_diagnostics"
      ".update_worker_substage",
      lambda *_a, **_k: None,
  )
  monkeypatch.setattr(
      helpers,
      "_raise_if_ingest_day_skipped",
      lambda *_a, **_k: None,
  )
  keys = build_archive_members_redis_keys(
      _daily_archive_members_cache_key("/data/2026-07-17.tar.zst"),
  )
  helpers._member_match_via_redis_or_sealed_point(
      "/data/2026-07-17.tar.zst",
      "day:test",
      keys,
      "/data/2026-07-17.tar.zst",
      "host/m",
      10,
      client=_Client(),
  )
  assert captured.get("kwargs", {}).get("canonical") == "/data/2026-07-17.tar.zst"


def test_incomplete_after_lock_release_reenqueues_before_fatal(
    _redis_test_env, tmp_path, monkeypatch,
):
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser"
      ".get_sync_archive_members_redis_populate_stall_seconds",
      lambda: 1,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser"
      ".get_sync_archive_members_redis_populate_max_seconds",
      lambda: 5,
  )
  day = "2026-06-07"
  tar = tmp_path / ("%s.tar" % day)
  zst = tmp_path / ("%s.tar.zst" % day)
  tar.write_bytes(b"tar")
  canonical = str(zst)
  keys = build_archive_members_redis_keys(
      _daily_archive_members_cache_key(canonical),
  )
  _redis_test_env.set(keys.complete_key, "0")
  enqueued = []

  def _enqueue(path, day_token):
    enqueued.append((path, day_token))
    return True

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".enqueue_archive_members_populate",
      _enqueue,
  )
  result = {"members": None, "exc": None}

  def _waiter():
    try:
      result["members"] = wait_for_complete_members(
          keys, canonical=canonical,
      )
    except Exception as exc:
      result["exc"] = exc

  t_wait = threading.Thread(target=_waiter)
  t_wait.start()
  time.sleep(1.5)
  assert enqueued, "expected re-enqueue after incomplete lock release"
  _redis_test_env.hset(keys.hash_key, mapping={"host/b": "2"})
  _redis_test_env.set(keys.complete_key, "1")
  t_wait.join(timeout=3)
  assert result["exc"] is None
  assert result["members"] == {"host/b": 2}


def test_incomplete_after_lock_release_fatal_when_max_seconds_zero(
    _redis_test_env, tmp_path,
):
  keys = build_archive_members_redis_keys(_sample_cache_key(tmp_path))
  _redis_test_env.set(keys.complete_key, "0")
  with pytest.raises(
      ArchiveMembersPopulateStalledError,
      match="incomplete after lock release",
  ):
    wait_for_complete_members(keys)


def test_request_populate_no_wait_when_no_daily_archive(
    _redis_test_env, tmp_path, monkeypatch,
):
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      request_archive_members_populate_and_wait,
  )

  day = "2026-06-09"
  canonical = str(tmp_path / ("%s.tar.zst" % day))
  enqueued = []
  waited = []

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".enqueue_archive_members_populate",
      lambda path, day_token: enqueued.append((path, day_token)) or True,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".wait_for_complete_members",
      lambda *args, **kwargs: waited.append((args, kwargs)) or {},
  )
  members = request_archive_members_populate_and_wait(canonical)
  assert members == {}
  assert not enqueued
  assert not waited


def test_recovery_no_reenqueue_no_source(_redis_test_env, tmp_path, monkeypatch):
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser"
      ".get_sync_archive_members_redis_populate_stall_seconds",
      lambda: 1,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser"
      ".get_sync_archive_members_redis_populate_max_seconds",
      lambda: 5,
  )
  day = "2026-06-09"
  canonical = str(tmp_path / ("%s.tar.zst" % day))
  keys = build_archive_members_redis_keys(
      _daily_archive_members_cache_key(canonical),
  )
  _redis_test_env.set(keys.complete_key, "0")
  enqueued = []

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".enqueue_archive_members_populate",
      lambda path, day_token: enqueued.append((path, day_token)) or True,
  )
  members = wait_for_complete_members(keys, canonical=canonical)
  assert members == {}
  assert not enqueued


def test_read_lock_timeout_does_not_sticky_day_skip(_redis_test_env, tmp_path):
  """Fix G: transient fnctl lock timeout must not set archive_day_ingest_skip."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      mark_archive_day_ingest_skip_and_raise,
  )

  keys = build_archive_members_redis_keys(_sample_cache_key(tmp_path))
  timeout = TimeoutError("timed out waiting for fnctl.lock on /data/day.tar")
  with pytest.raises(ArchiveMembersRedisUnavailableError, match="fnctl read lock timeout"):
    mark_archive_day_ingest_skip_and_raise("", keys, _redis_test_env, timeout)
  assert get_archive_day_ingest_skip(keys, client=_redis_test_env) is None


def test_populate_tar_fallback_after_sealed_truncated(
    _redis_test_env, tmp_path, monkeypatch,
):
  """Fix C: tar fallback when sealed stream fails but mutable tar is readable."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      _populate_redis_members_from_sealed_scan,
  )
  from hpcperfstats.dbload.lib.sync_timedb_ingest_worker_diagnostics import (
      reset_worker_pool_kind,
      set_worker_pool_kind,
  )
  from hpcperfstats.dbload.lib.archive_compress import (
      normalize_daily_compressed_path,
  )

  day_tar = tmp_path / "2024-06-07.tar"
  day_zst = tmp_path / "2024-06-07.tar.zst"
  inner = tmp_path / "payload.txt"
  inner.write_text("hello")
  with tarfile.open(day_tar, "w") as tf:
    tf.add(str(inner), arcname="host/payload")
  day_zst.write_bytes(b"not-a-valid-zst-frame")

  sealed_path = str(day_zst)
  canonical = normalize_daily_compressed_path(sealed_path)
  cache_key = _daily_archive_members_cache_key(canonical)

  token = set_worker_pool_kind("populate-pool")
  try:
    members = _populate_redis_members_from_sealed_scan(
        sealed_path, cache_key, tar_path=str(day_tar),
    )
  finally:
    reset_worker_pool_kind(token)

  assert members == {"host/payload": inner.stat().st_size}
  keys = build_archive_members_redis_keys(cache_key)
  assert get_archive_day_ingest_skip(keys, client=_redis_test_env) is None


def test_clear_stale_day_skip_when_sealed_gone_and_tar_readable(
    _redis_test_env, tmp_path, monkeypatch,
):
  """Fix D: auto-clear sticky skip when sealed is gone and tar is readable."""
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers
  from hpcperfstats.dbload.lib.archive_compress import (
      normalize_daily_compressed_path,
  )

  day_tar = tmp_path / "2024-06-07.tar"
  inner = tmp_path / "payload.txt"
  inner.write_text("hello")
  with tarfile.open(day_tar, "w") as tf:
    tf.add(str(inner), arcname="host/payload")
  canonical = str(tmp_path / "2024-06-07.tar.zst")
  cache_key = _daily_archive_members_cache_key(normalize_daily_compressed_path(canonical))
  keys = build_archive_members_redis_keys(cache_key)
  set_archive_day_ingest_skip(
      _redis_test_env,
      keys,
      "tar_truncated_or_unreadable",
      "unexpected end of data",
  )

  helpers._clear_stale_day_ingest_skip_if_tar_repaired(
      _redis_test_env,
      keys,
      str(day_tar),
      str(tmp_path / "2024-06-07.tar.zst"),
      str(tmp_path / "2024-06-07.tar.gz"),
      sealed_path=None,
  )
  assert get_archive_day_ingest_skip(keys, client=_redis_test_env) is None


def test_is_transient_fnctl_populate_unavailable():
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      ArchiveMembersRedisConnectionError,
      ArchiveMembersPopulateStalledError,
      ArchiveMembersRedisUnavailableError,
      is_populate_pool_unavailable_error,
      is_transient_fnctl_populate_unavailable,
  )

  fnctl = ArchiveMembersRedisUnavailableError(
      "transient fnctl read lock timeout during tar populate path=/x.tar",
  )
  assert is_transient_fnctl_populate_unavailable(fnctl) is True
  assert is_transient_fnctl_populate_unavailable(
      ArchiveMembersRedisConnectionError("connection refused"),
  ) is False
  assert is_transient_fnctl_populate_unavailable(
      ArchiveMembersPopulateStalledError("stalled"),
  ) is False
  assert is_transient_fnctl_populate_unavailable(
      ArchiveMembersRedisUnavailableError("populate degraded"),
  ) is False
  refuse = ArchiveMembersRedisUnavailableError(
      "populate-pool unavailable; refusing sealed stream on ingest-pool for /x.tar.zst",
  )
  assert is_populate_pool_unavailable_error(refuse) is True
  assert is_populate_pool_unavailable_error(fnctl) is False
  assert is_populate_pool_unavailable_error(
      ArchiveMembersRedisConnectionError("connection refused"),
  ) is False


def test_execute_populate_clears_degraded_and_retries_scan(
    _redis_test_env, tmp_path, monkeypatch,
):
  """Populate-pool re-entry clears degraded and runs scan instead of raising."""
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers
  from hpcperfstats.dbload.lib.sync_timedb_ingest_worker_diagnostics import (
      reset_worker_pool_kind,
      set_worker_pool_kind,
  )

  day = "2026-06-08"
  tar = tmp_path / ("%s.tar" % day)
  zst = tmp_path / ("%s.tar.zst" % day)
  tar.write_bytes(b"tar")
  zst.write_bytes(b"z")
  canonical = str(zst)
  keys = build_archive_members_redis_keys(
      _daily_archive_members_cache_key(canonical),
  )
  _redis_test_env.set(keys.degraded_key, "1")
  scan_calls = {"n": 0}

  def _fake_tar_scan(tar_path, cache_key):
    del tar_path, cache_key
    scan_calls["n"] += 1
    return {"host/a": 10}

  monkeypatch.setattr(
      helpers.cfg, "get_sync_archive_members_redis_enabled", lambda: True,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".get_archive_members_redis_client",
      lambda required=True: _redis_test_env,
  )
  monkeypatch.setattr(helpers, "_populate_redis_members_from_tar_scan", _fake_tar_scan)
  monkeypatch.setattr(helpers, "_resolve_sealed_daily_archive_path", lambda _p: None)
  token = set_worker_pool_kind("populate-pool")
  try:
    members = helpers.execute_archive_members_populate_for_canonical(canonical)
  finally:
    reset_worker_pool_kind(token)
  assert scan_calls["n"] == 1
  assert members == {"host/a": 10}
  assert _redis_test_env.get(keys.degraded_key) is None


def test_member_match_degraded_routes_to_populate_wait(
    _redis_test_env, tmp_path, monkeypatch,
):
  """Degraded Redis state routes duplicate-check through populate wait."""
  import hpcperfstats.dbload.lib.sync_timedb_archive_helpers as helpers

  day = "2026-06-09"
  zst = tmp_path / ("%s.tar.zst" % day)
  zst.write_bytes(b"z")
  canonical = str(zst)
  cache_key = _daily_archive_members_cache_key(canonical)
  keys = build_archive_members_redis_keys(cache_key)
  _redis_test_env.set(keys.degraded_key, "1")
  request_calls = {"n": 0}

  def _fake_request(path, *, role="ingest"):
    del role
    request_calls["n"] += 1
    assert path == canonical
    return {"host/raw": 42}

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".request_archive_members_populate_and_wait",
      _fake_request,
  )
  matched = helpers._member_match_via_redis_or_sealed_point(
      canonical,
      cache_key,
      keys,
      "",
      "host/raw",
      42,
      client=_redis_test_env,
  )
  assert matched is True
  assert request_calls["n"] == 1


def test_populate_source_decision_only_lock_winner_logs(
    _redis_test_env, tmp_path, monkeypatch,
):
  """Lock winner alone logs populate_source_decision (not every racing waiter)."""
  keys = build_archive_members_redis_keys(_sample_cache_key(tmp_path))
  decision_logs = []

  def _capture_decision(*args, **kwargs):
    decision_logs.append(args)

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_helpers"
      "._log_populate_source_decision",
      _capture_decision,
  )
  barrier = threading.Barrier(4)
  scan_calls = {"n": 0}
  scan_gate = threading.Lock()
  source_decision = {
      "day_token": keys.day_token,
      "tar_path": str(tmp_path / "day.tar"),
      "zst_path": "",
      "gz_path": "",
      "sealed_path": "",
  }

  def _slow_scan(on_member):
    with scan_gate:
      scan_calls["n"] += 1
    time.sleep(0.08)
    on_member("host/a", 10)
    return True, False

  def _run():
    barrier.wait()
    populate_archive_members_redis(
        keys, _slow_scan, source_decision=source_decision,
    )

  threads = [threading.Thread(target=_run) for _ in range(4)]
  for thread in threads:
    thread.start()
  for thread in threads:
    thread.join(timeout=5)
  assert scan_calls["n"] == 1
  assert len(decision_logs) == 1

def test_identity_drift_rate_limited_per_day(monkeypatch):
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      _IDENTITY_DRIFT_LOG_STATE,
      _log_identity_drift_if_allowed,
  )

  _IDENTITY_DRIFT_LOG_STATE.clear()
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      "._IDENTITY_DRIFT_LOG_INTERVAL_S",
      0.05,
  )
  logs = []
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis.log_print",
      lambda msg, **kw: logs.append(msg),
  )
  day = "2026-06-07"
  _log_identity_drift_if_allowed(day, "from-a", "to-b")
  _log_identity_drift_if_allowed(day, "from-c", "to-d")
  _log_identity_drift_if_allowed(day, "from-e", "to-f")
  drift_logs = [line for line in logs if "identity_drift" in line]
  assert len(drift_logs) == 1
  time.sleep(0.06)
  _log_identity_drift_if_allowed(day, "from-g", "to-h")
  drift_logs = [line for line in logs if "identity_drift" in line]
  assert len(drift_logs) == 2
  assert "suppressed_n=2" in drift_logs[1]


def test_append_inflight_defer_log_rate_limited(monkeypatch):
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      _APPEND_INFLIGHT_DEFER_LOG_STATE,
      _log_append_inflight_defer_if_allowed,
  )

  _APPEND_INFLIGHT_DEFER_LOG_STATE.clear()
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      "._IDENTITY_DRIFT_LOG_INTERVAL_S",
      0.05,
  )
  logs = []
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis.log_print",
      lambda msg, **kw: logs.append(msg),
  )
  day = "2026-06-03"
  _log_append_inflight_defer_if_allowed(day)
  _log_append_inflight_defer_if_allowed(day)
  _log_append_inflight_defer_if_allowed(day)
  defer_logs = [line for line in logs if "defer tar scan" in line]
  assert len(defer_logs) == 1
  assert "reason=archive_append_inflight" in defer_logs[0]
  assert "suppressed_n=" not in defer_logs[0]
  time.sleep(0.06)
  _log_append_inflight_defer_if_allowed(day)
  defer_logs = [line for line in logs if "defer tar scan" in line]
  assert len(defer_logs) == 2
  assert "suppressed_n=2" in defer_logs[1]


def test_tar_populate_eof_during_append_does_not_set_day_skip(
    _redis_test_env, tmp_path,
):
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      mark_archive_day_ingest_skip_and_raise,
  )
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      set_archive_append_inflight,
  )

  keys = build_archive_members_redis_keys(_sample_cache_key(tmp_path))
  set_archive_append_inflight(keys.day_token, reason="archive_job")

  with pytest.raises(
      ArchiveMembersRedisUnavailableError,
      match="transient tar populate EOF",
  ):
    mark_archive_day_ingest_skip_and_raise(
        "",
        keys,
        _redis_test_env,
        EOFError("unexpected end of data"),
    )
  assert get_archive_day_ingest_skip(keys, client=_redis_test_env) is None


def test_prewarm_populate_completes_when_ingest_tar_hot_set(
    _redis_test_env, tmp_path,
):
  """Chunk prewarm sets ingest_tar_hot before populate; scan must still run."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      set_ingest_tar_hot,
  )

  keys = build_archive_members_redis_keys(_sample_cache_key(tmp_path))
  set_ingest_tar_hot(keys.day_token, reason="chunk_prewarm")
  scan_calls = {"n": 0}

  def _scan(on_member):
    scan_calls["n"] += 1
    on_member("host/a", 10)
    on_member("host/b", 20)
    return True, False

  members = populate_archive_members_redis(keys, _scan, sealed_path=None)
  assert scan_calls["n"] == 1
  assert members == {"host/a": 10, "host/b": 20}
  assert _redis_test_env.get(keys.complete_key) == "1"


def test_populate_defers_tar_scan_while_archive_append_inflight(
    _redis_test_env, tmp_path, monkeypatch,
):
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      clear_archive_append_inflight,
      set_archive_append_inflight,
  )

  keys = build_archive_members_redis_keys(_sample_cache_key(tmp_path))
  set_archive_append_inflight(keys.day_token, reason="archive_job")
  scan_calls = {"n": 0}
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser"
      ".get_sync_archive_members_redis_populate_lock_seconds",
      lambda: 0.15,
  )

  def _scan(on_member):
    scan_calls["n"] += 1
    on_member("host/a", 1)
    return True, False

  result = {"members": None, "exc": None}

  def _populate():
    try:
      result["members"] = populate_archive_members_redis(
          keys, _scan, sealed_path=None,
      )
    except Exception as exc:
      result["exc"] = exc

  t = threading.Thread(target=_populate)
  t.start()
  time.sleep(0.08)
  assert scan_calls["n"] == 0
  assert result["exc"] is None
  clear_archive_append_inflight(keys.day_token)
  t.join(timeout=5)
  assert result["exc"] is None
  assert scan_calls["n"] == 1
  assert result["members"] == {"host/a": 1}


def test_populate_acquire_deadline_pauses_during_append_inflight_defer(
    _redis_test_env, tmp_path, monkeypatch,
):
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      clear_archive_append_inflight,
      set_archive_append_inflight,
  )

  keys = build_archive_members_redis_keys(_sample_cache_key(tmp_path))
  set_archive_append_inflight(keys.day_token, reason="archive_job")
  scan_calls = {"n": 0}
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser"
      ".get_sync_archive_members_redis_populate_lock_seconds",
      lambda: 0.12,
  )

  def _scan(on_member):
    scan_calls["n"] += 1
    on_member("host/a", 1)
    return True, False

  result = {"members": None, "exc": None}

  def _populate():
    try:
      result["members"] = populate_archive_members_redis(
          keys, _scan, sealed_path=None,
      )
    except Exception as exc:
      result["exc"] = exc

  def _clear_inflight_later():
    time.sleep(0.25)
    clear_archive_append_inflight(keys.day_token)

  t = threading.Thread(target=_populate)
  t_clear = threading.Thread(target=_clear_inflight_later)
  t.start()
  t_clear.start()
  t.join(timeout=5)
  t_clear.join(timeout=5)
  assert result["exc"] is None
  assert scan_calls["n"] == 1


def test_acquire_loop_releases_stale_lock_when_owner_dead(
    _redis_test_env, tmp_path, monkeypatch,
):
  keys = build_archive_members_redis_keys(_sample_cache_key(tmp_path))
  dead_pid = 999999997
  _redis_test_env.set(keys.lock_key, "tok:%d" % dead_pid, ex=30)
  _redis_test_env.set(keys.complete_key, "0")
  _redis_test_env.set(keys.progress_key, str(time.time() - 1000))
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser"
      ".get_sync_archive_members_redis_populate_stall_seconds",
      lambda: 0.05,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser"
      ".get_sync_archive_members_redis_populate_lock_seconds",
      lambda: 2.0,
  )
  scan_calls = {"n": 0}

  def _scan(on_member):
    scan_calls["n"] += 1
    on_member("host/a", 1)
    return True, False

  members = populate_archive_members_redis(keys, _scan, sealed_path=None)
  assert scan_calls["n"] == 1
  assert members == {"host/a": 1}


def test_populate_lock_timeout_when_external_holder_alive(
    _redis_test_env, tmp_path, monkeypatch,
):
  keys = build_archive_members_redis_keys(_sample_cache_key(tmp_path))
  alive_pid = os.getpid()
  _redis_test_env.set(keys.lock_key, "tok:%d" % alive_pid, ex=30)
  _redis_test_env.set(keys.complete_key, "0")
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser"
      ".get_sync_archive_members_redis_populate_lock_seconds",
      lambda: 0.12,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser"
      ".get_sync_archive_members_redis_populate_stall_seconds",
      lambda: 9999.0,
  )
  scan_calls = {"n": 0}

  def _scan(on_member):
    scan_calls["n"] += 1
    return True, False

  with pytest.raises(ArchiveMembersRedisUnavailableError, match="populate lock"):
    populate_archive_members_redis(keys, _scan, sealed_path=None)
  assert scan_calls["n"] == 0


def test_populate_lock_timeout_clears_orphan_incomplete_hlen_zero(
    _redis_test_env, tmp_path, monkeypatch, capsys,
):
  keys = build_archive_members_redis_keys(_sample_cache_key(tmp_path))
  alive_pid = os.getpid()
  _redis_test_env.set(keys.lock_key, "tok:%d" % alive_pid, ex=30)
  _redis_test_env.set(keys.complete_key, "0")
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser"
      ".get_sync_archive_members_redis_populate_lock_seconds",
      lambda: 0.08,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser"
      ".get_sync_archive_members_redis_populate_stall_seconds",
      lambda: 9999.0,
  )

  def _scan(on_member):
    return True, False

  with pytest.raises(ArchiveMembersRedisUnavailableError, match="populate lock"):
    populate_archive_members_redis(keys, _scan, sealed_path=None)
  out = capsys.readouterr().out
  assert "lock acquire timeout" in out
  assert "lock_owner_pid=%s" % alive_pid in out


def test_archive_pre_append_lookup_exempt_from_append_inflight_defer(
    _redis_test_env, tmp_path,
):
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      archive_pre_append_member_lookup_context,
      set_archive_append_inflight,
  )

  keys = build_archive_members_redis_keys(_sample_cache_key(tmp_path))
  set_archive_append_inflight(keys.day_token, reason="archive_job")
  scan_calls = {"n": 0}

  def _scan(on_member):
    scan_calls["n"] += 1
    on_member("host/a", 1)
    return True, False

  with archive_pre_append_member_lookup_context():
    members = populate_archive_members_redis(keys, _scan, sealed_path=None)
  assert scan_calls["n"] == 1
  assert members == {"host/a": 1}


def test_populate_completes_after_tar_append_merge(
    _redis_test_env, tmp_path, monkeypatch,
):
  """Waiters succeed after archive job ends and merge warms Redis."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      clear_archive_append_inflight,
      set_archive_append_inflight,
      wait_for_complete_members,
  )

  day = "2026-06-07"
  tar = tmp_path / ("%s.tar" % day)
  zst = tmp_path / ("%s.tar.zst" % day)
  tar.write_bytes(b"v1")
  canonical = str(zst)
  keys_t1 = build_archive_members_redis_keys(
      _daily_archive_members_cache_key(canonical),
  )
  set_archive_append_inflight(day, reason="archive_job")
  result = {"members": None, "exc": None}

  def _waiter():
    try:
      result["members"] = wait_for_complete_members(
          keys_t1, canonical=canonical,
      )
    except Exception as exc:
      result["exc"] = exc

  def _finish_append():
    time.sleep(0.08)
    clear_archive_append_inflight(day)
    tar.write_bytes(b"v1-appended")
    keys_t2 = build_archive_members_redis_keys(
        _daily_archive_members_cache_key(canonical),
    )
    _redis_test_env.hset(keys_t2.hash_key, mapping={"host/a": "10"})
    _redis_test_env.set(keys_t2.complete_key, "1")

  t_wait = threading.Thread(target=_waiter)
  t_finish = threading.Thread(target=_finish_append)
  t_wait.start()
  t_finish.start()
  t_wait.join(timeout=3)
  t_finish.join(timeout=3)
  assert result["exc"] is None
  assert result["members"] == {"host/a": 10}


def test_populate_lock_held_stall_recoverable_within_max_seconds(
    _redis_test_env, tmp_path, monkeypatch,
):
  """Alive lock owner + stale progress within max_seconds must not fatal."""
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser"
      ".get_sync_archive_members_redis_populate_stall_seconds",
      lambda: 0.05,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser"
      ".get_sync_archive_members_redis_populate_max_seconds",
      lambda: 5,
  )
  keys = build_archive_members_redis_keys(_sample_cache_key(tmp_path))
  _redis_test_env.set(keys.lock_key, "tok:%d" % os.getpid(), ex=30)
  _redis_test_env.set(keys.complete_key, "0")
  # Stale progress timestamp
  _redis_test_env.set(keys.progress_key, str(time.time() - 10), ex=30)

  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      _check_populate_wait_limits,
  )

  started = time.monotonic()
  # First check after stall window: owner alive → keep waiting (False).
  time.sleep(0.06)
  assert _check_populate_wait_limits(
      _redis_test_env,
      keys,
      started_monotonic=started,
      last_progress_monotonic=started - 10,
  ) is False


def test_populate_fail_deletes_partial_hash_no_orphan(
    _redis_test_env, tmp_path, monkeypatch,
):
  """Mid-scan failure must delete partial HASH (no orphan hlen thrash)."""
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser"
      ".get_sync_archive_members_redis_hset_batch_size",
      lambda: 1,
  )
  keys = build_archive_members_redis_keys(_sample_cache_key(tmp_path))

  def _scan(on_member):
    on_member("host/a", 10)
    on_member("host/b", 20)
    raise RuntimeError("simulated mid-scan failure")

  with pytest.raises(RuntimeError, match="simulated mid-scan failure"):
    populate_archive_members_redis(keys, _scan, sealed_path=None)

  assert _redis_test_env.hlen(keys.hash_key) == 0
  assert _redis_test_env.get(keys.complete_key) is None
  assert _redis_test_env.get(keys.degraded_key) == "1"
  assert _redis_test_env.exists(keys.lock_key) == 0
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      redis_members_populate_is_orphaned_incomplete,
  )
  assert redis_members_populate_is_orphaned_incomplete(
      keys, client=_redis_test_env,
  ) is False


def test_tar_populate_eof_self_hot_only_prefers_sealed_fallback(
    _redis_test_env, tmp_path, monkeypatch,
):
  """populate_wait self-hot + sealed sibling → sealed fallback, not forever EOF."""
  from hpcperfstats.dbload.lib import sync_timedb_archive_helpers as helpers
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      set_ingest_tar_hot,
  )
  from hpcperfstats.dbload.lib.sync_timedb_ingest_worker_diagnostics import (
      reset_worker_pool_kind,
      set_worker_pool_kind,
  )

  day = "2026-06-07"
  daily = tmp_path / "daily"
  daily.mkdir()
  tar = daily / ("%s.tar" % day)
  zst = daily / ("%s.tar.zst" % day)
  tar.write_bytes(b"dirty-tar")
  zst.write_bytes(b"sealed-zst")
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_daily_archive_dir_path",
      lambda: str(daily),
  )
  set_ingest_tar_hot(day, reason="populate_wait")
  keys = build_archive_members_redis_keys(
      _daily_archive_members_cache_key(str(zst)),
  )

  with pytest.raises(
      ArchiveMembersRedisUnavailableError,
      match="prefer sealed fallback",
  ):
    helpers.mark_archive_day_ingest_skip_and_raise(
        "",
        keys,
        _redis_test_env,
        EOFError("unexpected end of data"),
    )
  assert get_archive_day_ingest_skip(keys, client=_redis_test_env) is None

  sealed_calls = {"n": 0}

  def _fake_sealed(sealed_path, cache_key, tar_path=None):
    del sealed_path, cache_key, tar_path
    sealed_calls["n"] += 1
    return {"host/from_sealed": 42}

  def _raise_prefer(_keys, _scan, **_kw):
    raise ArchiveMembersRedisUnavailableError(
        "tar populate EOF prefer sealed fallback day=%s" % day,
    )

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".populate_archive_members_redis",
      _raise_prefer,
  )
  monkeypatch.setattr(
      helpers, "_populate_redis_members_from_sealed_scan", _fake_sealed,
  )
  monkeypatch.setattr(
      helpers, "_resolve_sealed_daily_archive_path", lambda _p: str(zst),
  )
  token = set_worker_pool_kind("populate-pool")
  try:
    members = helpers._populate_redis_members_from_tar_scan(
        str(tar), _daily_archive_members_cache_key(str(zst)),
    )
  finally:
    reset_worker_pool_kind(token)
  assert sealed_calls["n"] == 1
  assert members == {"host/from_sealed": 42}


def test_tar_populate_eof_during_populate_wait_hot_does_not_forever_transient(
    _redis_test_env, tmp_path, monkeypatch,
):
  """Self-hot alone without sealed must not use forever-transient hot/append path."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      mark_archive_day_ingest_skip_and_raise,
  )
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      set_ingest_tar_hot,
  )

  day = "2026-06-07"
  daily = tmp_path / "daily"
  daily.mkdir()
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.conf_parser.get_daily_archive_dir_path",
      lambda: str(daily),
  )
  keys = build_archive_members_redis_keys(
      _sample_cache_key(tmp_path, day=day),
  )
  for name in ("%s.tar.zst" % day, "%s.tar" % day):
    p = tmp_path / name
    if p.is_file():
      p.unlink()
  set_ingest_tar_hot(day, reason="populate_wait")
  with pytest.raises(ArchiveDayIngestSkipError):
    mark_archive_day_ingest_skip_and_raise(
        "",
        keys,
        _redis_test_env,
        EOFError("unexpected end of data"),
    )
  assert get_archive_day_ingest_skip(keys, client=_redis_test_env) is not None


def test_idle_pool_recover_skip_reason_for_populate_wait(
    _redis_test_env, tmp_path,
):
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      idle_pool_recover_skip_reason_for_paths,
      set_ingest_tar_hot,
  )

  day = "2026-06-07"
  # 2026-06-07 12:00:00 UTC
  epoch = 1780833600
  path = "/archive/host/%d" % epoch
  set_ingest_tar_hot(day, reason="populate_wait")
  reason = idle_pool_recover_skip_reason_for_paths([path])
  assert "populate_wait" in reason
  assert day in reason
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      clear_ingest_tar_hot,
  )
  clear_ingest_tar_hot(day)
  assert idle_pool_recover_skip_reason_for_paths(["/nope/not-epoch"]) == ""
  assert idle_pool_recover_skip_reason_for_paths([path]) == ""


def test_idle_pool_recover_skip_reason_ignores_unrelated_day_hot(
    _redis_test_env, tmp_path,
):
  """July pending must not idle-skip solely because June has populate_wait."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      clear_ingest_tar_hot,
      idle_pool_recover_skip_reason_for_paths,
      set_ingest_tar_hot,
  )

  set_ingest_tar_hot("2026-06-02", reason="populate_wait")
  # 2026-07-17 12:00:00 UTC
  july_path = "/archive/host/1784289600"
  assert idle_pool_recover_skip_reason_for_paths([july_path]) == ""
  set_ingest_tar_hot("2026-07-17", reason="chunk_prewarm")
  reason = idle_pool_recover_skip_reason_for_paths([july_path])
  assert "populate_wait" in reason
  assert "2026-07-17" in reason
  clear_ingest_tar_hot("2026-06-02")
  clear_ingest_tar_hot("2026-07-17")


def test_populate_queue_brpop_prefers_ingest_hot_over_cold_fifo(
    _redis_test_env, tmp_path,
):
  """Cold June queued first must not block July chunk_prewarm populate."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      archive_members_populate_queue_brpop,
      enqueue_archive_members_populate,
      set_ingest_tar_hot,
  )

  june_tar = str(tmp_path / "2026-06-02.tar")
  july_tar = str(tmp_path / "2026-07-17.tar")
  assert enqueue_archive_members_populate(june_tar, "2026-06-02")
  assert enqueue_archive_members_populate(july_tar, "2026-07-17")
  set_ingest_tar_hot("2026-07-17", reason="chunk_prewarm")
  job = archive_members_populate_queue_brpop(timeout_s=0.2)
  assert job is not None
  assert job.get("day_token") == "2026-07-17"
  job2 = archive_members_populate_queue_brpop(timeout_s=0.2)
  assert job2 is not None
  assert job2.get("day_token") == "2026-06-02"


def test_populate_defers_dirty_tar_scan_when_append_inflight_and_sealed_known(
    _redis_test_env, tmp_path, monkeypatch,
):
  """Dirty-tar populate defers on append even when sealed sibling path is known."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      _populate_scan_should_defer,
      clear_archive_append_inflight,
      set_archive_append_inflight,
  )

  keys = build_archive_members_redis_keys(_sample_cache_key(tmp_path))
  sealed = str(tmp_path / "2026-05-09.tar.zst")
  set_archive_append_inflight(keys.day_token, reason="archive_job")
  assert _populate_scan_should_defer(
      keys, sealed, scanning_mutable_tar=True,
  ) is True
  assert _populate_scan_should_defer(keys, sealed) is False
  clear_archive_append_inflight(keys.day_token)
  assert _populate_scan_should_defer(
      keys, sealed, scanning_mutable_tar=True,
  ) is False


def test_invalidate_archive_members_redis_bulk_all(_redis_test_env, tmp_path):
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      archive_append_inflight_for_day,
      daily_tar_restore_in_progress_for_day,
      enqueue_archive_members_populate,
      ingest_tar_hot_for_day,
      set_archive_append_inflight,
      set_daily_tar_restore_in_progress,
      set_ingest_tar_hot,
  )

  fake = _redis_test_env
  keys_a = build_archive_members_redis_keys(
      _sample_cache_key(tmp_path, "2026-06-01"),
  )
  keys_b = build_archive_members_redis_keys(
      _sample_cache_key(tmp_path, "2026-06-02"),
  )
  store_complete_members_in_redis(keys_a, {"a": 1})
  store_complete_members_in_redis(keys_b, {"b": 2})
  set_archive_day_ingest_skip(fake, keys_a, "test", "bulk")
  assert enqueue_archive_members_populate(str(tmp_path / "2026-06-01.tar"), "2026-06-01")
  set_ingest_tar_hot("2026-06-01", reason="chunk_prewarm")
  set_archive_append_inflight("2026-06-01", reason="archive_job")
  set_daily_tar_restore_in_progress(
      "2026-06-01", reason="missing_tar", caller="test",
  )

  result = invalidate_archive_members_redis_bulk(
      day_tokens=None, dry_run=False, client=fake,
  )
  assert result["scanned"] >= 2
  assert result["deleted"] == result["scanned"]
  assert result["dry_run"] is False
  assert fake.get(keys_a.complete_key) is None
  assert fake.get(keys_b.complete_key) is None
  assert get_archive_day_ingest_skip(keys_a, client=fake) is None
  assert ingest_tar_hot_for_day("2026-06-01") is True
  assert archive_append_inflight_for_day("2026-06-01") is True
  assert daily_tar_restore_in_progress_for_day("2026-06-01") is True


def test_invalidate_archive_members_redis_bulk_by_day(_redis_test_env, tmp_path):
  fake = _redis_test_env
  keys_keep = build_archive_members_redis_keys(
      _sample_cache_key(tmp_path, "2026-06-01"),
  )
  keys_drop = build_archive_members_redis_keys(
      _sample_cache_key(tmp_path, "2026-06-02"),
  )
  store_complete_members_in_redis(keys_keep, {"keep": 1})
  store_complete_members_in_redis(keys_drop, {"drop": 2})

  result = invalidate_archive_members_redis_bulk(
      day_tokens=["2026-06-02"], dry_run=False, client=fake,
  )
  assert result["days"] == ["2026-06-02"]
  assert result["deleted"] >= 1
  assert fake.get(keys_keep.complete_key) == "1"
  assert fake.get(keys_drop.complete_key) is None


def test_invalidate_archive_members_redis_bulk_dry_run(_redis_test_env, tmp_path):
  fake = _redis_test_env
  keys = build_archive_members_redis_keys(_sample_cache_key(tmp_path))
  store_complete_members_in_redis(keys, {"m": 1})
  result = invalidate_archive_members_redis_bulk(
      day_tokens=None, dry_run=True, client=fake,
  )
  assert result["dry_run"] is True
  assert result["scanned"] >= 1
  assert result["deleted"] == 0
  assert fake.get(keys.complete_key) == "1"


def test_clear_stale_incomplete_noop_silent_when_empty(_redis_test_env, tmp_path):
  """Empty Redis must not WARN on clear_stale (post-orphan stampede)."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      clear_stale_incomplete_archive_members_redis,
  )

  keys = build_archive_members_redis_keys(_sample_cache_key(tmp_path))
  logs = []
  assert clear_stale_incomplete_archive_members_redis(
      keys, client=_redis_test_env, log_fn=lambda m, **_k: logs.append(m),
  ) is False
  assert logs == []


def test_stale_incomplete_log_rate_limited_cluster_wide(_redis_test_env, tmp_path):
  """Two processes sharing Redis NX gate emit at most one stale-incomplete WARN."""
  import hpcperfstats.dbload.lib.sync_timedb_archive_members_redis as amr
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      clear_stale_incomplete_archive_members_redis,
  )

  keys = build_archive_members_redis_keys(_sample_cache_key(tmp_path))
  logs = []

  def _log(msg, **_kwargs):
    logs.append(msg)

  _redis_test_env.set(keys.degraded_key, "1")
  assert clear_stale_incomplete_archive_members_redis(
      keys, client=_redis_test_env, log_fn=_log,
  ) is True
  assert sum(1 for m in logs if "clearing stale incomplete" in m) == 1

  # Simulate another process: clear process-local gate, re-set incomplete keys.
  amr._STALE_INCOMPLETE_LOG_STATE.clear()
  _redis_test_env.set(keys.degraded_key, "1")
  assert clear_stale_incomplete_archive_members_redis(
      keys, client=_redis_test_env, log_fn=_log,
  ) is True
  assert sum(1 for m in logs if "clearing stale incomplete" in m) == 1


def test_degraded_waiters_single_flight_clear_and_enqueue(
    _redis_test_env, tmp_path, monkeypatch,
):
  """N degraded waiters: one clear+enqueue; peers wait only."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      clear_stale_incomplete_archive_members_redis,
      request_archive_members_populate_and_wait,
  )

  day = "2026-06-02"
  zst = tmp_path / ("%s.tar.zst" % day)
  zst.write_bytes(b"sealed-bytes")
  canonical = str(zst)
  keys = build_archive_members_redis_keys(_daily_archive_members_cache_key(canonical))
  _redis_test_env.set(keys.degraded_key, "1")
  enqueued = []
  clear_logs = []
  barrier = threading.Barrier(5)

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".enqueue_archive_members_populate",
      lambda path, day_token: enqueued.append((path, day_token)) or True,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".wait_for_complete_members",
      lambda *a, **k: {},
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      "._ensure_populate_pool_running_for_enqueue",
      lambda: type("C", (), {"is_running": lambda self: True})(),
  )

  real_clear = clear_stale_incomplete_archive_members_redis

  def _counting_clear(keys_arg, *, client=None, log_fn=None):
    def _log(msg, **kwargs):
      clear_logs.append(msg)
      if log_fn is not None:
        log_fn(msg, **kwargs)

    return real_clear(keys_arg, client=client, log_fn=_log)

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".clear_stale_incomplete_archive_members_redis",
      _counting_clear,
  )

  errors = []

  def _worker():
    try:
      barrier.wait(timeout=5)
      request_archive_members_populate_and_wait(canonical)
    except Exception as exc:  # noqa: BLE001 — collect for assert
      errors.append(exc)

  threads = [threading.Thread(target=_worker) for _ in range(5)]
  for t in threads:
    t.start()
  for t in threads:
    t.join(timeout=10)
  assert not errors
  assert len(enqueued) == 1
  assert sum(1 for m in clear_logs if "clearing stale incomplete" in m) <= 1


def test_dead_owner_orphan_then_waiters_single_flight_recover(
    _redis_test_env, tmp_path, monkeypatch,
):
  """Dead lock owner → orphan wipe once; N waiters ≤1 stale WARN and ≤1 re-enqueue."""
  from hpcperfstats.dbload.lib.sync_timedb_archive_members_redis import (
      clear_stale_incomplete_archive_members_redis,
      maybe_clear_orphan_incomplete_archive_members_redis,
      request_archive_members_populate_and_wait,
  )

  day = "2026-06-02"
  zst = tmp_path / ("%s.tar.zst" % day)
  zst.write_bytes(b"sealed-bytes")
  canonical = str(zst)
  keys = build_archive_members_redis_keys(_daily_archive_members_cache_key(canonical))
  # Partial sealed populate (orphan after dead owner released lock).
  mapping = {"host/%d" % i: str(i + 1) for i in range(50)}
  _redis_test_env.hset(keys.hash_key, mapping=mapping)
  _redis_test_env.set(keys.complete_key, "0")

  orphan_logs = []
  assert maybe_clear_orphan_incomplete_archive_members_redis(
      keys,
      client=_redis_test_env,
      log_fn=lambda m, **_k: orphan_logs.append(m),
  ) is True
  assert len(orphan_logs) == 1
  assert _redis_test_env.hlen(keys.hash_key) == 0

  # After orphan wipe, degraded/incomplete recovery stampede (as in backlog).
  _redis_test_env.set(keys.degraded_key, "1")
  enqueued = []
  clear_logs = []
  barrier = threading.Barrier(6)

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".enqueue_archive_members_populate",
      lambda path, day_token: enqueued.append((path, day_token)) or True,
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".wait_for_complete_members",
      lambda *a, **k: {},
  )
  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      "._ensure_populate_pool_running_for_enqueue",
      lambda: type("C", (), {"is_running": lambda self: True})(),
  )

  real_clear = clear_stale_incomplete_archive_members_redis

  def _counting_clear(keys_arg, *, client=None, log_fn=None):
    def _log(msg, **kwargs):
      clear_logs.append(msg)
      if log_fn is not None:
        log_fn(msg, **kwargs)

    return real_clear(keys_arg, client=client, log_fn=_log)

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_archive_members_redis"
      ".clear_stale_incomplete_archive_members_redis",
      _counting_clear,
  )

  errors = []

  def _worker():
    try:
      barrier.wait(timeout=5)
      request_archive_members_populate_and_wait(canonical)
    except Exception as exc:  # noqa: BLE001
      errors.append(exc)

  threads = [threading.Thread(target=_worker) for _ in range(6)]
  for t in threads:
    t.start()
  for t in threads:
    t.join(timeout=10)
  assert not errors
  assert len(enqueued) == 1
  assert sum(1 for m in clear_logs if "clearing stale incomplete" in m) <= 1
