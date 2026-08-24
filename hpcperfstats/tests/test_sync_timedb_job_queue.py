"""Host FakeRedis unit tests for sync_timedb job:v1 queue helpers (slice 1)."""
from __future__ import annotations

import hashlib
import threading
from datetime import date

import pytest

from hpcperfstats.dbload.lib import sync_timedb_job_queue as jq
from hpcperfstats.dbload.lib.invalidate_archive_members_ops import (
    _KEY_PREFIX,
    _PROTECTED_COORD_PREFIXES,
    _is_protected_coord_redis_key,
    invalidate_archive_members_redis_bulk,
)


class FakeRedis:
  """Minimal Redis stand-in: KV, LIST, ZSET, SCRIPT LOAD / EVALSHA."""

  def __init__(self):
    self._kv = {}
    self._lists = {}
    self._zsets = {}
    self._scripts = {}
    self._lock = threading.Lock()

  def set(self, key, value, nx=False, ex=None):
    del ex
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
        self._lists.pop(key, None)
        self._zsets.pop(key, None)

  def rpush(self, key, *values):
    with self._lock:
      bucket = self._lists.setdefault(key, [])
      for value in values:
        bucket.append(value)
      return len(bucket)

  def lpop(self, key):
    with self._lock:
      bucket = self._lists.get(key)
      if not bucket:
        return None
      value = bucket.pop(0)
      if not bucket:
        del self._lists[key]
      return value

  def zadd(self, key, mapping):
    with self._lock:
      bucket = self._zsets.setdefault(key, {})
      added = 0
      for member, score in mapping.items():
        if member not in bucket:
          added += 1
        bucket[member] = float(score)
      return added

  def zrangebyscore(self, key, min_score, max_score, start=0, num=None):
    with self._lock:
      bucket = self._zsets.get(key) or {}
    lo = float("-inf") if min_score == "-inf" else float(min_score)
    if max_score in ("+inf", "inf"):
      hi = float("inf")
    else:
      hi = float(max_score)
    ranked = sorted(
        ((score, member) for member, score in bucket.items() if lo <= score <= hi),
        key=lambda item: (item[0], item[1]),
    )
    members = [member for _score, member in ranked]
    if num is None:
      return members[start:]
    return members[start : start + int(num)]

  def zrem(self, key, *members):
    with self._lock:
      bucket = self._zsets.get(key)
      if not bucket:
        return 0
      removed = 0
      for member in members:
        if member in bucket:
          del bucket[member]
          removed += 1
      if not bucket:
        del self._zsets[key]
      return removed

  def zcount(self, key, min_score, max_score):
    return len(self.zrangebyscore(key, min_score, max_score))

  def zcard(self, key):
    with self._lock:
      return len(self._zsets.get(key) or {})

  def script_load(self, script):
    sha = hashlib.sha1(script.encode("utf-8")).hexdigest()
    self._scripts[sha] = script
    return sha

  def evalsha(self, sha, numkeys, *args):
    script = self._scripts.get(sha)
    if script is None:
      raise Exception("NOSCRIPT")
    return self.eval(script, numkeys, *args)

  def eval(self, script, numkeys, *args):
    keys = list(args[:numkeys])
    argv = list(args[numkeys:])
    if "ZRANGEBYSCORE" in script:
      key = keys[0]
      lo, hi = argv[0], argv[1]
      members = self.zrangebyscore(key, lo, hi, start=0, num=1)
      if not members:
        return None
      self.zrem(key, members[0])
      return members[0]
    if "get" in script and "del" in script:
      key = keys[0]
      token = argv[0]
      with self._lock:
        if self._kv.get(key) != token:
          return 0
        self._kv.pop(key, None)
        return 1
    raise AssertionError("unsupported lua in FakeRedis: %r" % (script[:80],))

  def scan_iter(self, match=None, count=100):
    del count
    prefix = match.rstrip("*") if match else ""
    with self._lock:
      universe = set(self._kv) | set(self._lists) | set(self._zsets)
      keys = [key for key in sorted(universe) if key.startswith(prefix)]
    for key in keys:
      yield key


@pytest.fixture(autouse=True)
def _reset_job_queue_scripts():
  jq.reset_job_queue_script_cache_for_tests()
  yield
  jq.reset_job_queue_script_cache_for_tests()


def test_job_v1_key_names_disjoint_from_archive_members():
  ingest = jq.job_queue_key("ingest")
  assert ingest == "hpcperfstats:sync_timedb:job:v1:queue:ingest"
  assert ":archive_members:" not in ingest
  assert jq.job_queue_key("append").endswith(":queue:append")
  assert jq.job_lease_key("ingest", "id").endswith(":lease:ingest:id")
  assert jq.job_payload_key("day_close", "2026-08-01").endswith(
      ":payload:day_close:2026-08-01",
  )


def test_ingest_identity_normpath_size_mtime():
  assert jq.ingest_identity("/tmp/a/../b", 12, 34) == "/tmp/b|12|34"


def test_encode_decode_hot_newest_first_catchup_oldest_first():
  today = date(2026, 8, 24)
  newer = date(2026, 8, 23)
  older = date(2026, 8, 20)
  id_new = "new|1|1"
  id_old = "old|1|1"
  hot_new = jq.encode_ingest_score(
      band="hot", day=newer, today=today, identity=id_new,
  )
  hot_old = jq.encode_ingest_score(
      band="hot", day=older, today=today, identity=id_old,
  )
  assert hot_new < hot_old
  assert jq.decode_ingest_band(hot_new) == "hot"
  catch_old = jq.encode_ingest_score(
      band="catchup", day=older, today=today, identity=id_old,
  )
  catch_new = jq.encode_ingest_score(
      band="catchup", day=newer, today=today, identity=id_new,
  )
  assert catch_old < catch_new
  assert jq.decode_ingest_band(catch_old) == "catchup"
  assert catch_old >= jq.CATCHUP_SCORE_BASE


def test_ingest_band_slot_caps_pool16_is_10_and_6():
  assert jq.ingest_band_slot_caps(16) == (10, 6)
  assert jq.ingest_band_slot_caps(2) == (1, 1)
  assert jq.ingest_band_slot_caps(1) == (1, 0)
  hot, catch = jq.ingest_band_slot_caps(3)
  assert hot + catch == 3
  assert catch >= 1


def test_set_nx_ex_lease_and_non_owner_release(monkeypatch):
  monkeypatch.setattr(jq, "job_lease_ttl_seconds", lambda: 86400)
  client = FakeRedis()
  token = jq.try_acquire_job_lease(
      client, kind="ingest", identity="p|1|2", owner_token="abc:111",
  )
  assert token == "abc:111"
  assert client.get(jq.job_lease_key("ingest", "p|1|2")) == "abc:111"
  assert (
      jq.try_acquire_job_lease(
          client, kind="ingest", identity="p|1|2", owner_token="other:222",
      )
      == ""
  )
  assert not jq.release_job_lease(
      client, kind="ingest", identity="p|1|2", owner_token="other:222",
  )
  assert client.get(jq.job_lease_key("ingest", "p|1|2")) == "abc:111"
  assert jq.release_job_lease(
      client, kind="ingest", identity="p|1|2", owner_token="abc:111",
  )
  assert client.get(jq.job_lease_key("ingest", "p|1|2")) is None


def test_steal_lease_when_owner_pid_dead():
  client = FakeRedis()
  key = jq.job_lease_key("append", "day")
  client.set(key, "deadtoken:99999", nx=True, ex=86400)
  assert jq.steal_job_lease_if_owner_dead(
      client,
      kind="append",
      identity="day",
      pid_alive_fn=lambda _pid: False,
  )
  assert client.get(key) is None
  client.set(key, "livetoken:1", nx=True, ex=86400)
  assert not jq.steal_job_lease_if_owner_dead(
      client,
      kind="append",
      identity="day",
      pid_alive_fn=lambda _pid: True,
  )
  assert client.get(key) == "livetoken:1"


def test_ranged_lua_pop_prefers_hot_range_without_starving_catchup():
  client = FakeRedis()
  today = date(2026, 8, 24)
  hot_id = "hot|1|1"
  catch_id = "catch|1|1"
  jq.zadd_ingest_job(
      client,
      identity=hot_id,
      score=jq.encode_ingest_score(
          band="hot", day=date(2026, 8, 23), today=today, identity=hot_id,
      ),
  )
  jq.zadd_ingest_job(
      client,
      identity=catch_id,
      score=jq.encode_ingest_score(
          band="catchup",
          day=date(2026, 6, 1),
          today=today,
          identity=catch_id,
      ),
  )
  assert jq.pop_ingest_job_ranged(client, band="hot") == hot_id
  assert jq.pop_ingest_job_ranged(client, band="hot") is None
  assert jq.pop_ingest_job_ranged(client, band="catchup") == catch_id
  assert client.zcard(jq.job_queue_key("ingest")) == 0


def test_zadd_same_member_reband_overwrites_score():
  client = FakeRedis()
  today = date(2026, 8, 24)
  ident = "p|9|9"
  hot = jq.encode_ingest_score(
      band="hot", day=date(2026, 8, 23), today=today, identity=ident,
  )
  catch = jq.encode_ingest_score(
      band="catchup", day=date(2026, 8, 23), today=today, identity=ident,
  )
  jq.zadd_ingest_job(client, identity=ident, score=hot)
  jq.zadd_ingest_job(client, identity=ident, score=catch)
  assert client.zcard(jq.job_queue_key("ingest")) == 1
  assert jq.pop_ingest_job_ranged(client, band="hot") is None
  assert jq.pop_ingest_job_ranged(client, band="catchup") == ident


def test_list_queue_fifo_for_append():
  client = FakeRedis()
  jq.enqueue_list_job(client, kind="append", identity="a")
  jq.enqueue_list_job(client, kind="append", identity="b")
  assert jq.pop_list_job(client, kind="append") == "a"
  assert jq.pop_list_job(client, kind="append") == "b"
  assert jq.pop_list_job(client, kind="append") is None


def test_invalidate_protects_job_v1_keys():
  job_prefix = "%s:job:v1" % _KEY_PREFIX
  assert job_prefix in _PROTECTED_COORD_PREFIXES
  assert _is_protected_coord_redis_key("%s:queue:ingest" % job_prefix)
  assert _is_protected_coord_redis_key("%s:lease:ingest:x" % job_prefix)
  client = FakeRedis()
  hash_key = "%s:archive_members:hash:v1:2026-08-01:id" % _KEY_PREFIX
  job_key = "%s:queue:ingest" % job_prefix
  client.set(hash_key, "1")
  client.set(job_key, "should-survive-if-scanned")
  # Force-protection path: scan would not normally find job keys; inject via
  # a membership-shaped key is N/A — assert protected helper + bulk leaves
  # explicit job key when only membership patterns delete.
  result = invalidate_archive_members_redis_bulk(
      day_tokens=["2026-08-01"], dry_run=False, client=client,
  )
  assert result["deleted"] >= 1
  assert client.get(job_key) == "should-survive-if-scanned"
