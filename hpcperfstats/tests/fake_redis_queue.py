"""
Shared in-process Redis stand-in for ``job:v1`` queue unit tests.

The queue primitives are implemented as Lua so that pop + lease + in-flight
bookkeeping is one atomic server-side step. Host unit tests cannot run Lua, so
this stand-in dispatches on the marker comment each script carries and
re-implements the *same* semantics in Python under one lock. Real Lua
execution is covered by the live-Redis tests in
``test_sync_timedb_job_queue_redis.py``.

Attributes:
  FakeRedis: Minimal Redis stand-in supporting KV/LIST/ZSET/HASH/SET plus the
    queue Lua contracts.
"""
from __future__ import annotations

from typing import Dict, List

import hashlib
import threading


class FakeRedis:
  """
  Minimal Redis stand-in for queue tests.

  Supports the subset of commands the ``job:v1`` helpers use: string GET/SET
  with ``NX``/``EX``, LIST, ZSET, HASH, SET, ``SCRIPT LOAD``, and ``EVALSHA``
  dispatch for the queue Lua scripts.
  """

  def __init__(self) -> None:
    """
    Create an empty in-memory keyspace.

    Returns:
      None

    Examples:
      >>> FakeRedis().zcard("k")
      0
    """
    self._kv: Dict[str, str] = {}
    self._ttl: Dict[str, int] = {}
    self._lists: Dict[str, List[str]] = {}
    self._zsets: Dict[str, Dict[str, float]] = {}
    self._hashes: Dict[str, Dict[str, str]] = {}
    self._sets: Dict[str, set] = {}
    self._scripts: Dict[str, str] = {}
    self._config: Dict[str, str] = {"maxmemory-policy": "noeviction"}
    self._lock = threading.RLock()

  # --- strings ---

  def set(self, key, value, nx=False, ex=None):
    """
    Set a string key, honoring ``NX`` and recording ``EX``.

    Args:
      key (str): Key name.
      value (str): Value to store.
      nx (bool): Only set when the key is absent.
      ex (int | None): TTL seconds to record.

    Returns:
      bool: True when the key was written.

    Examples:
      >>> FakeRedis().set("k", "v")
      True
    """
    with self._lock:
      if nx and key in self._kv:
        return False
      self._kv[key] = str(value)
      if ex is not None:
        self._ttl[key] = int(ex)
      return True

  def get(self, key):
    """
    Read a string key.

    Args:
      key (str): Key name.

    Returns:
      str | None: Stored value, or ``None``.

    Examples:
      >>> FakeRedis().get("missing") is None
      True
    """
    with self._lock:
      return self._kv.get(key)

  def ttl(self, key):
    """
    Return the recorded TTL for a key.

    Args:
      key (str): Key name.

    Returns:
      int: TTL seconds, ``-1`` when persistent, ``-2`` when missing.

    Examples:
      >>> FakeRedis().ttl("missing")
      -2
    """
    with self._lock:
      if key in self._ttl:
        return self._ttl[key]
      exists = (
          key in self._kv
          or key in self._lists
          or key in self._zsets
          or key in self._hashes
          or key in self._sets
      )
      return -1 if exists else -2

  def expire(self, key, seconds):
    """
    Record a TTL on an existing key.

    Args:
      key (str): Key name.
      seconds (int): TTL seconds.

    Returns:
      bool: True when the key exists.

    Examples:
      >>> c = FakeRedis(); c.set("k", "v"); c.expire("k", 5)
      True
      True
    """
    with self._lock:
      if key not in self._kv:
        return False
      self._ttl[key] = int(seconds)
      return True

  def delete(self, *keys):
    """
    Delete keys across every supported type.

    Args:
      *keys (str): Key names.

    Returns:
      int: Number of keys removed.

    Examples:
      >>> FakeRedis().delete("a")
      0
    """
    removed = 0
    with self._lock:
      for key in keys:
        hit = False
        for store in (
            self._kv, self._lists, self._zsets, self._hashes, self._sets,
        ):
          if key in store:
            del store[key]
            hit = True
        self._ttl.pop(key, None)
        removed += 1 if hit else 0
    return removed

  def config_get(self, name):
    """
    Return a fake server config mapping.

    Args:
      name (str): Config parameter name.

    Returns:
      dict: Mapping with the requested parameter when known.

    Examples:
      >>> FakeRedis().config_get("maxmemory-policy")["maxmemory-policy"]
      'noeviction'
    """
    with self._lock:
      if name in self._config:
        return {name: self._config[name]}
      return {}

  def set_config_for_tests(self, name, value):
    """
    Override a fake server config value.

    Args:
      name (str): Config parameter name.
      value (str): Value to report.

    Returns:
      None

    Examples:
      >>> FakeRedis().set_config_for_tests("maxmemory-policy", "allkeys-lru")
    """
    with self._lock:
      self._config[str(name)] = str(value)

  # --- lists ---

  def rpush(self, key, *values):
    """
    Append values to a list.

    Args:
      key (str): List key.
      *values (str): Values to append.

    Returns:
      int: List length after the push.

    Examples:
      >>> FakeRedis().rpush("k", "a")
      1
    """
    with self._lock:
      bucket = self._lists.setdefault(key, [])
      for value in values:
        bucket.append(str(value))
      return len(bucket)

  def lpop(self, key):
    """
    Pop the head of a list.

    Args:
      key (str): List key.

    Returns:
      str | None: Popped value, or ``None`` when empty.

    Examples:
      >>> FakeRedis().lpop("k") is None
      True
    """
    with self._lock:
      return self._lpop_locked(key)

  def _lpop_locked(self, key):
    """
    Pop the head of a list assuming the lock is held.

    Args:
      key (str): List key.

    Returns:
      str | None: Popped value, or ``None`` when empty.

    Examples:
      >>> c = FakeRedis()
      >>> c._lpop_locked("k") is None
      True
    """
    bucket = self._lists.get(key)
    if not bucket:
      return None
    value = bucket.pop(0)
    if not bucket:
      del self._lists[key]
    return value

  def llen(self, key):
    """
    Return list length.

    Args:
      key (str): List key.

    Returns:
      int: Number of elements.

    Examples:
      >>> FakeRedis().llen("k")
      0
    """
    with self._lock:
      return len(self._lists.get(key) or [])

  def lrange(self, key, start, stop):
    """
    Return a slice of a list.

    Args:
      key (str): List key.
      start (int): Inclusive start index.
      stop (int): Inclusive stop index (``-1`` for end).

    Returns:
      list: Selected elements.

    Examples:
      >>> FakeRedis().lrange("k", 0, -1)
      []
    """
    with self._lock:
      bucket = list(self._lists.get(key) or [])
    if stop == -1:
      return bucket[start:]
    return bucket[start : stop + 1]

  # --- zsets ---

  def zadd(self, key, mapping):
    """
    Add or update sorted-set members.

    Args:
      key (str): ZSET key.
      mapping (dict): Member to score mapping.

    Returns:
      int: Number of newly added members.

    Examples:
      >>> FakeRedis().zadd("k", {"a": 1})
      1
    """
    with self._lock:
      return self._zadd_locked(key, mapping)

  def _zadd_locked(self, key, mapping):
    """
    Add or update sorted-set members assuming the lock is held.

    Args:
      key (str): ZSET key.
      mapping (dict): Member to score mapping.

    Returns:
      int: Number of newly added members.

    Examples:
      >>> FakeRedis()._zadd_locked("k", {"a": 1})
      1
    """
    bucket = self._zsets.setdefault(key, {})
    added = 0
    for member, score in mapping.items():
      if member not in bucket:
        added += 1
      bucket[str(member)] = float(score)
    return added

  def zrangebyscore(self, key, min_score, max_score, start=0, num=None):
    """
    Return members within a score range, ordered by score then member.

    Args:
      key (str): ZSET key.
      min_score (Any): Inclusive lower bound (``-inf`` accepted).
      max_score (Any): Inclusive upper bound (``+inf`` accepted).
      start (int): Offset into the ordered result.
      num (int | None): Maximum members to return.

    Returns:
      list: Matching members.

    Examples:
      >>> FakeRedis().zrangebyscore("k", "-inf", "+inf")
      []
    """
    with self._lock:
      return self._zrangebyscore_locked(key, min_score, max_score, start, num)

  def _zrangebyscore_locked(self, key, min_score, max_score, start, num):
    """
    Score-range scan assuming the lock is held.

    Args:
      key (str): ZSET key.
      min_score (Any): Inclusive lower bound.
      max_score (Any): Inclusive upper bound.
      start (int): Offset into the ordered result.
      num (int | None): Maximum members to return.

    Returns:
      list: Matching members.

    Examples:
      >>> FakeRedis()._zrangebyscore_locked("k", "-inf", "+inf", 0, None)
      []
    """
    bucket = self._zsets.get(key) or {}
    lo = float("-inf") if str(min_score) == "-inf" else float(min_score)
    if str(max_score) in ("+inf", "inf"):
      hi = float("inf")
    else:
      hi = float(max_score)
    ranked = sorted(
        (
            (score, member)
            for member, score in bucket.items()
            if lo <= score <= hi
        ),
        key=lambda item: (item[0], item[1]),
    )
    members = [member for _score, member in ranked]
    if num is None:
      return members[start:]
    return members[start : start + int(num)]

  def zrem(self, key, *members):
    """
    Remove sorted-set members.

    Args:
      key (str): ZSET key.
      *members (str): Members to remove.

    Returns:
      int: Number removed.

    Examples:
      >>> FakeRedis().zrem("k", "a")
      0
    """
    with self._lock:
      return self._zrem_locked(key, *members)

  def _zrem_locked(self, key, *members):
    """
    Remove sorted-set members assuming the lock is held.

    Args:
      key (str): ZSET key.
      *members (str): Members to remove.

    Returns:
      int: Number removed.

    Examples:
      >>> FakeRedis()._zrem_locked("k", "a")
      0
    """
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

  def zscore(self, key, member):
    """
    Return a member score.

    Args:
      key (str): ZSET key.
      member (str): Member name.

    Returns:
      float | None: Score, or ``None`` when absent.

    Examples:
      >>> FakeRedis().zscore("k", "a") is None
      True
    """
    with self._lock:
      return (self._zsets.get(key) or {}).get(member)

  def zcount(self, key, min_score, max_score):
    """
    Count members within a score range.

    Args:
      key (str): ZSET key.
      min_score (Any): Inclusive lower bound.
      max_score (Any): Inclusive upper bound.

    Returns:
      int: Matching member count.

    Examples:
      >>> FakeRedis().zcount("k", "-inf", "+inf")
      0
    """
    return len(self.zrangebyscore(key, min_score, max_score))

  def zcard(self, key):
    """
    Return sorted-set cardinality.

    Args:
      key (str): ZSET key.

    Returns:
      int: Member count.

    Examples:
      >>> FakeRedis().zcard("k")
      0
    """
    with self._lock:
      return len(self._zsets.get(key) or {})

  # --- hashes ---

  def hset(self, key, field=None, value=None, mapping=None):
    """
    Set hash fields.

    Args:
      key (str): Hash key.
      field (str | None): Single field name.
      value (Any | None): Single field value.
      mapping (dict | None): Multiple field/value pairs.

    Returns:
      int: Number of new fields created.

    Examples:
      >>> FakeRedis().hset("k", "f", "v")
      1
    """
    with self._lock:
      bucket = self._hashes.setdefault(key, {})
      created = 0
      items = dict(mapping or {})
      if field is not None:
        items[field] = value
      for name, val in items.items():
        if name not in bucket:
          created += 1
        bucket[str(name)] = str(val)
      return created

  def hget(self, key, field):
    """
    Read one hash field.

    Args:
      key (str): Hash key.
      field (str): Field name.

    Returns:
      str | None: Field value, or ``None``.

    Examples:
      >>> FakeRedis().hget("k", "f") is None
      True
    """
    with self._lock:
      return (self._hashes.get(key) or {}).get(field)

  def hgetall(self, key):
    """
    Read every field of a hash.

    Args:
      key (str): Hash key.

    Returns:
      dict: Field to value mapping.

    Examples:
      >>> FakeRedis().hgetall("k")
      {}
    """
    with self._lock:
      return dict(self._hashes.get(key) or {})

  def hdel(self, key, *fields):
    """
    Delete hash fields.

    Args:
      key (str): Hash key.
      *fields (str): Field names.

    Returns:
      int: Number of fields removed.

    Examples:
      >>> FakeRedis().hdel("k", "f")
      0
    """
    with self._lock:
      return self._hdel_locked(key, *fields)

  def _hdel_locked(self, key, *fields):
    """
    Delete hash fields assuming the lock is held.

    Args:
      key (str): Hash key.
      *fields (str): Field names.

    Returns:
      int: Number of fields removed.

    Examples:
      >>> FakeRedis()._hdel_locked("k", "f")
      0
    """
    bucket = self._hashes.get(key)
    if not bucket:
      return 0
    removed = 0
    for field in fields:
      if field in bucket:
        del bucket[field]
        removed += 1
    if not bucket:
      del self._hashes[key]
    return removed

  def hlen(self, key):
    """
    Return hash field count.

    Args:
      key (str): Hash key.

    Returns:
      int: Field count.

    Examples:
      >>> FakeRedis().hlen("k")
      0
    """
    with self._lock:
      return len(self._hashes.get(key) or {})

  def hexists(self, key, field):
    """
    Return whether a hash field exists.

    Args:
      key (str): Hash key.
      field (str): Field name.

    Returns:
      bool: True when present.

    Examples:
      >>> FakeRedis().hexists("k", "f")
      False
    """
    with self._lock:
      return field in (self._hashes.get(key) or {})

  def hincrby(self, key, field, amount=1):
    """
    Increment an integer hash field.

    Args:
      key (str): Hash key.
      field (str): Field name.
      amount (int): Increment amount.

    Returns:
      int: Value after the increment.

    Examples:
      >>> FakeRedis().hincrby("k", "f", 2)
      2
    """
    with self._lock:
      bucket = self._hashes.setdefault(key, {})
      current = int(bucket.get(field, "0") or 0)
      current += int(amount)
      bucket[field] = str(current)
      return current

  # --- sets ---

  def sadd(self, key, *members):
    """
    Add set members.

    Args:
      key (str): Set key.
      *members (str): Members to add.

    Returns:
      int: Number newly added.

    Examples:
      >>> FakeRedis().sadd("k", "a")
      1
    """
    with self._lock:
      return self._sadd_locked(key, *members)

  def _sadd_locked(self, key, *members):
    """
    Add set members assuming the lock is held.

    Args:
      key (str): Set key.
      *members (str): Members to add.

    Returns:
      int: Number newly added.

    Examples:
      >>> FakeRedis()._sadd_locked("k", "a")
      1
    """
    bucket = self._sets.setdefault(key, set())
    added = 0
    for member in members:
      if str(member) not in bucket:
        bucket.add(str(member))
        added += 1
    return added

  def srem(self, key, *members):
    """
    Remove set members.

    Args:
      key (str): Set key.
      *members (str): Members to remove.

    Returns:
      int: Number removed.

    Examples:
      >>> FakeRedis().srem("k", "a")
      0
    """
    with self._lock:
      return self._srem_locked(key, *members)

  def _srem_locked(self, key, *members):
    """
    Remove set members assuming the lock is held.

    Args:
      key (str): Set key.
      *members (str): Members to remove.

    Returns:
      int: Number removed.

    Examples:
      >>> FakeRedis()._srem_locked("k", "a")
      0
    """
    bucket = self._sets.get(key)
    if not bucket:
      return 0
    removed = 0
    for member in members:
      if str(member) in bucket:
        bucket.discard(str(member))
        removed += 1
    if not bucket:
      del self._sets[key]
    return removed

  def sismember(self, key, member):
    """
    Return set membership.

    Args:
      key (str): Set key.
      member (str): Member name.

    Returns:
      bool: True when present.

    Examples:
      >>> FakeRedis().sismember("k", "a")
      False
    """
    with self._lock:
      return str(member) in (self._sets.get(key) or set())

  def scard(self, key):
    """
    Return set cardinality.

    Args:
      key (str): Set key.

    Returns:
      int: Member count.

    Examples:
      >>> FakeRedis().scard("k")
      0
    """
    with self._lock:
      return len(self._sets.get(key) or set())

  # --- scripting ---

  def script_load(self, script):
    """
    Register a Lua script and return its sha.

    Args:
      script (str): Lua source.

    Returns:
      str: Script sha1 hex digest.

    Examples:
      >>> len(FakeRedis().script_load("return 1"))
      40
    """
    sha = hashlib.sha1(script.encode("utf-8")).hexdigest()
    with self._lock:
      self._scripts[sha] = script
    return sha

  def evalsha(self, sha, numkeys, *args):
    """
    Run a previously loaded script by sha.

    Args:
      sha (str): Script sha.
      numkeys (int): Leading key count.
      *args (Any): Keys followed by ARGV values.

    Returns:
      Any: Emulated Lua return value.

    Raises:
      Exception: ``NOSCRIPT`` when the sha is unknown.

    Examples:
      >>> c = FakeRedis()
      >>> c.evalsha(c.script_load("-- HPS_ACK\\n"), 4, "i", "l", "p", "s",
      ...           "id", "tok")
      0
    """
    with self._lock:
      script = self._scripts.get(sha)
    if script is None:
      raise Exception("NOSCRIPT No matching script")
    return self.eval(script, numkeys, *args)

  def eval(self, script, numkeys, *args):
    """
    Emulate one of the queue Lua scripts.

    Args:
      script (str): Lua source (dispatched on its marker comment).
      numkeys (int): Leading key count.
      *args (Any): Keys followed by ARGV values.

    Returns:
      Any: Emulated Lua return value.

    Raises:
      AssertionError: When the script has no emulation.

    Examples:
      >>> FakeRedis().eval("-- HPS_REAP\\n", 2, "i", "w", "0", "1", "z",
      ...                  "pfx:", "1")
      []
    """
    keys = [str(k) for k in args[:numkeys]]
    argv = [str(a) for a in args[numkeys:]]
    with self._lock:
      if "HPS_CLAIM_ZSET" in script:
        return self._lua_claim_zset(keys, argv)
      if "HPS_CLAIM_LIST" in script:
        return self._lua_claim_list(keys, argv)
      if "HPS_ACK" in script:
        return self._lua_ack(keys, argv)
      if "HPS_REQUEUE" in script:
        return self._lua_requeue(keys, argv)
      if "HPS_RENEW" in script:
        return self._lua_renew(keys, argv)
      if "HPS_REAP" in script:
        return self._lua_reap(keys, argv)
      if "HPS_ENQUEUE_LIST" in script:
        return self._lua_enqueue_list(keys, argv)
      if "ZRANGEBYSCORE" in script:
        members = self._zrangebyscore_locked(keys[0], argv[0], argv[1], 0, 1)
        if not members:
          return False
        self._zrem_locked(keys[0], members[0])
        return members[0]
      if "get" in script and "del" in script:
        if self._kv.get(keys[0]) != argv[0]:
          return 0
        self._kv.pop(keys[0], None)
        self._ttl.pop(keys[0], None)
        return 1
    raise AssertionError("unsupported lua in FakeRedis: %r" % (script[:60],))

  def _set_nx_ex_locked(self, key, value, ex):
    """
    ``SET NX EX`` assuming the lock is held.

    Args:
      key (str): Key name.
      value (str): Value to store.
      ex (int): TTL seconds.

    Returns:
      bool: True when written.

    Examples:
      >>> FakeRedis()._set_nx_ex_locked("k", "v", 5)
      True
    """
    if key in self._kv:
      return False
    self._kv[key] = str(value)
    self._ttl[key] = int(ex)
    return True

  def _lua_claim_zset(self, keys, argv):
    """
    Emulate the atomic ranged ZSET claim.

    Args:
      keys (list): ``[work_zset, inflight_hash]``.
      argv (list): ``[lo, hi, owner, ttl, lease_prefix, deadline, penalty,
        probe]``.

    Returns:
      Any: ``[member, score]`` on success, else ``False``.

    Examples:
      >>> FakeRedis()._lua_claim_zset(["w", "i"],
      ...     ["-inf", "+inf", "t", "60", "L:", "1.0", "10", "8"])
      False
    """
    work, inflight = keys[0], keys[1]
    lo, hi, owner, ttl, prefix, deadline, penalty, probe = argv[:8]
    for _ in range(int(probe)):
      members = self._zrangebyscore_locked(work, lo, hi, 0, 1)
      if not members:
        return False
      member = members[0]
      score = self._zsets[work][member]
      if self._set_nx_ex_locked(prefix + member, owner, int(ttl)):
        self._zrem_locked(work, member)
        self._hashes.setdefault(inflight, {})[member] = "%s|%s|%s" % (
            deadline, owner, _fmt_score(score),
        )
        return [member, _fmt_score(score)]
      self._zadd_locked(work, {member: score + float(penalty)})
    return False

  def _lua_claim_list(self, keys, argv):
    """
    Emulate the atomic LIST claim.

    Args:
      keys (list): ``[work_list, inflight_hash]``.
      argv (list): ``[owner, ttl, lease_prefix, deadline, probe]``.

    Returns:
      Any: Claimed member, or ``False``.

    Examples:
      >>> FakeRedis()._lua_claim_list(["w", "i"], ["t", "60", "L:", "1.0", "8"])
      False
    """
    work, inflight = keys[0], keys[1]
    owner, ttl, prefix, deadline, probe = argv[:5]
    for _ in range(int(probe)):
      member = self._lpop_locked(work)
      if member is None:
        return False
      if self._set_nx_ex_locked(prefix + member, owner, int(ttl)):
        self._hashes.setdefault(inflight, {})[member] = "%s|%s|" % (
            deadline, owner,
        )
        return member
      self._lists.setdefault(work, []).append(member)
    return False

  def _lua_ack(self, keys, argv):
    """
    Emulate the owner-checked terminal ack.

    Args:
      keys (list): ``[inflight, lease, payload, pending]``.
      argv (list): ``[identity, owner]``.

    Returns:
      int: ``1`` when this owner cleared its in-flight entry.

    Examples:
      >>> FakeRedis()._lua_ack(["i", "l", "p", "s"], ["id", "tok"])
      0
    """
    inflight, lease, payload, pending = keys[:4]
    identity, owner = argv[0], argv[1]
    owned = 0
    current = (self._hashes.get(inflight) or {}).get(identity)
    if current is not None and _owner_of(current) == owner:
      self._hdel_locked(inflight, identity)
      owned = 1
    if self._kv.get(lease) == owner:
      self._kv.pop(lease, None)
      self._ttl.pop(lease, None)
    self._hashes.pop(payload, None)
    self._srem_locked(pending, identity)
    return owned

  def _lua_requeue(self, keys, argv):
    """
    Emulate the owner-checked requeue.

    Args:
      keys (list): ``[inflight, lease, work]``.
      argv (list): ``[identity, owner, mode, score]``.

    Returns:
      int: ``1`` when this owner requeued its claim.

    Examples:
      >>> FakeRedis()._lua_requeue(["i", "l", "w"], ["id", "tok", "l", "0"])
      0
    """
    inflight, lease, work = keys[:3]
    identity, owner, mode, score = argv[:4]
    owned = 0
    current = (self._hashes.get(inflight) or {}).get(identity)
    if current is not None and _owner_of(current) == owner:
      self._hdel_locked(inflight, identity)
      owned = 1
    if self._kv.get(lease) == owner:
      self._kv.pop(lease, None)
      self._ttl.pop(lease, None)
    if owned:
      if mode == "z":
        self._zadd_locked(work, {identity: float(score)})
      else:
        self._lists.setdefault(work, []).append(identity)
    return owned

  def _lua_renew(self, keys, argv):
    """
    Emulate compare-and-extend lease renewal.

    Args:
      keys (list): ``[lease, inflight]``.
      argv (list): ``[identity, owner, ttl, deadline]``.

    Returns:
      int: ``1`` when the lease was still owned.

    Examples:
      >>> FakeRedis()._lua_renew(["l", "i"], ["id", "tok", "60", "9.0"])
      0
    """
    lease, inflight = keys[:2]
    identity, owner, ttl, deadline = argv[:4]
    if self._kv.get(lease) != owner:
      return 0
    self._ttl[lease] = int(ttl)
    current = (self._hashes.get(inflight) or {}).get(identity)
    if current is not None and _owner_of(current) == owner:
      self._hashes[inflight][identity] = "%s|%s|%s" % (
          deadline, owner, _score_of(current),
      )
    return 1

  def _lua_reap(self, keys, argv):
    """
    Emulate expired in-flight recovery.

    Args:
      keys (list): ``[inflight, work]``.
      argv (list): ``[cutoff, limit, mode, lease_prefix, penalty]``.

    Returns:
      list: Recovered identities.

    Examples:
      >>> FakeRedis()._lua_reap(["i", "w"], ["0", "1", "z", "L:", "1"])
      []
    """
    inflight, work = keys[:2]
    cutoff, limit, mode, prefix, penalty = argv[:5]
    out: List[str] = []
    for identity, value in list((self._hashes.get(inflight) or {}).items()):
      try:
        deadline = float(value.split("|", 1)[0])
      except (IndexError, ValueError):
        deadline = 0.0
      if deadline > float(cutoff):
        continue
      owner = _owner_of(value)
      score_text = _score_of(value)
      self._hdel_locked(inflight, identity)
      lease = prefix + identity
      if self._kv.get(lease) == owner:
        self._kv.pop(lease, None)
        self._ttl.pop(lease, None)
      if mode == "z":
        base = float(score_text) if score_text else 0.0
        self._zadd_locked(work, {identity: base + float(penalty)})
      else:
        self._lists.setdefault(work, []).append(identity)
      out.append(identity)
      if len(out) >= int(limit):
        break
    return out

  def _lua_enqueue_list(self, keys, argv):
    """
    Emulate the dedupe-guarded LIST enqueue.

    Args:
      keys (list): ``[work_list, pending_set, inflight_hash]``.
      argv (list): ``[identity]``.

    Returns:
      int: ``1`` when pushed, ``0`` when deduped.

    Examples:
      >>> FakeRedis()._lua_enqueue_list(["w", "s", "i"], ["id"])
      1
    """
    work, pending, inflight = keys[:3]
    identity = argv[0]
    if identity in (self._hashes.get(inflight) or {}):
      return 0
    if self._sadd_locked(pending, identity) == 0:
      return 0
    self._lists.setdefault(work, []).append(identity)
    return 1

  def scan_iter(self, match=None, count=100):
    """
    Iterate keys matching a prefix pattern.

    Args:
      match (str | None): Prefix pattern ending in ``*``.
      count (int): Ignored batch hint.

    Returns:
      Iterator[str]: Matching key names.

    Examples:
      >>> list(FakeRedis().scan_iter(match="x*"))
      []
    """
    del count
    prefix = match.rstrip("*") if match else ""
    with self._lock:
      universe = (
          set(self._kv) | set(self._lists) | set(self._zsets)
          | set(self._hashes) | set(self._sets)
      )
      names = [key for key in sorted(universe) if key.startswith(prefix)]
    for key in names:
      yield key


def _fmt_score(score: float) -> str:
  """
  Render a ZSET score the way Redis returns it to Lua.

  Args:
    score (float): Member score.

  Returns:
    str: Integer text when integral, else repr-style float text.

  Examples:
    >>> _fmt_score(5.0)
    '5'
  """
  if float(score).is_integer():
    return str(int(score))
  return repr(float(score))


def _owner_of(value: str) -> str:
  """
  Extract the owner token from an in-flight value.

  Args:
    value (str): ``\"deadline|owner|score\"`` text.

  Returns:
    str: Owner token, or empty string.

  Examples:
    >>> _owner_of("1.0|tok|7")
    'tok'
  """
  parts = str(value).split("|", 2)
  return parts[1] if len(parts) > 1 else ""


def _score_of(value: str) -> str:
  """
  Extract the score text from an in-flight value.

  Args:
    value (str): ``\"deadline|owner|score\"`` text.

  Returns:
    str: Score text, or empty string.

  Examples:
    >>> _score_of("1.0|tok|7")
    '7'
  """
  parts = str(value).split("|", 2)
  return parts[2] if len(parts) > 2 else ""
