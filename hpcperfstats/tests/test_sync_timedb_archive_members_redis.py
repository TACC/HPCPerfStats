"""Tests for Redis L2 daily archive member cache (no Django)."""
from __future__ import annotations

import os
import tarfile
import threading
import time
from pathlib import Path

import pytest

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
    list_dedupe_hint_day_tokens,
    merge_appended_members_into_redis,
    populate_archive_members_redis,
    reset_archive_members_redis_client_for_tests,
    set_archive_day_ingest_skip,
    store_complete_members_in_redis,
    verify_archive_members_redis_startup,
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

  def exists(self, key):
    with self._lock:
      return 1 if key in self._kv else 0

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
    prefix = match.rstrip("*") if match else ""
    with self._lock:
      for key in sorted(self._kv):
        if key.startswith(prefix):
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

  day_gz = tmp_path / "2024-06-10.tar.gz"
  inner = tmp_path / "payload.txt"
  inner.write_text("hello")
  with tarfile.open(day_gz, "w:gz") as tf:
    tf.add(str(inner), arcname="host/payload")

  sealed_path = str(day_gz)
  canonical = normalize_daily_compressed_path(sealed_path)
  cache_key = _daily_archive_members_cache_key(canonical)

  members = _populate_redis_members_from_sealed_scan(sealed_path, cache_key)
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
