"""
Redis ``job:v1`` work-queue helpers for the sync_timedb greenfield orchestrator.

Provides key names, ingest ZSET band scores, reserved slot caps, and the
atomic claim / ack / requeue / renew / reap primitives that make a claimed job
crash-safe: every claim moves work out of the queue and into an in-flight hash
under a SET NX EX lease (EX = per-file ingest timeout max, default 86400s;
OQ-1: no coordinator heartbeat renew), and every terminal path either acks (drop) or requeues
(retry with attempt accounting).

Attributes:
  CATCHUP_SCORE_BASE: Score floor for catchup-band ingest ZSET members.
  ClaimedJob: One atomically claimed job (identity, owner, deadline, score).
  HOT_SCORE_BASE: Score floor for hot-band ingest ZSET members.
  INFLIGHT_REAP_GRACE_FLOOR_S: Minimum grace added past an in-flight deadline.
  JOB_ATTEMPT_MAX_DEFAULT: Default attempt ceiling before dead-lettering.
  JOB_KIND_APPEND: Job kind string for append LIST queue.
  JOB_KIND_DAY_CLOSE: Job kind string for day_close LIST queue.
  JOB_KIND_DISCOVER: Job kind string for discover LIST queue.
  JOB_KIND_INGEST: Job kind string for ingest ZSET queue.
  JOB_KINDS_ALL: Tuple of every durable job kind.
  JOB_KINDS_LIST: Tuple of kinds that use Redis LIST queues.
  JOB_LEASE_TTL_CEILING_S: Unused leftover ceiling (OQ-1 uses per-file max).
  JOB_LEASE_TTL_FLOOR_S: Minimum lease EX seconds when INI values are tiny.
  JOB_LEASE_TTL_POLL_MULTIPLIER: Lease TTL as a multiple of the pool poll.
  JOB_V1_PREFIX: Redis key prefix ``…:job:v1``.
  KEY_PREFIX: Shared ``hpcperfstats:sync_timedb`` namespace root.
  LEASE_CONFLICT_SCORE_PENALTY: Score bump applied when a claim hits a lease.
  LeaseOwner: Parsed lease owner token (nonce, host, boot id, pid).
  QUEUE_DEAD_LETTER_KIND: Persistence artifact kind for queue dead letters.
  QUEUE_MAX_MEMBERS_FLOOR: Minimum accepted queue capacity bound.
  SCORE_STRIDE: Day/tie-break stride inside a band score range.
  UNSAFE_EVICTION_POLICY_PREFIX: Redis ``maxmemory-policy`` prefix that would
    silently evict durable queue keys.
  _ACK_LUA: Lua source for owner-checked terminal ack.
  _BOOT_ID_CACHE: Memoized host boot identifier.
  _CLAIM_LIST_LUA: Lua source for atomic LIST claim (pop + lease + in-flight).
  _CLAIM_ZSET_LUA: Lua source for atomic ranged ZSET claim.
  _ENQUEUE_LIST_DEDUPE_LUA: Lua source for dedupe-guarded LIST enqueue.
  _INGEST_RANGED_POP_LUA: Lua source for one atomic ranged ZSET pop.
  _LEASE_COMPARE_DEL_LUA: Lua source for compare-and-del lease release.
  _REAP_LUA: Lua source for expired in-flight recovery.
  _RENEW_LUA: Lua source for compare-and-extend lease renewal.
  _REQUEUE_LUA: Lua source for owner-checked requeue.
  _STEAL_REQUEUE_LUA: Lua source for dead-owner steal that requeues inflight.
  _SCRIPT_CACHE_LOCK: Guards ``_SCRIPT_SHA_CACHE`` mutation.
  _SCRIPT_SHA_CACHE: Lua source -> ``SCRIPT LOAD`` sha memo.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import hashlib
import os
import secrets
import socket
import threading
import time
from datetime import date

from hpcperfstats.dbload.lib import conf_parser as cfg

KEY_PREFIX = "hpcperfstats:sync_timedb"
JOB_V1_PREFIX = "%s:job:v1" % KEY_PREFIX

JOB_KIND_DISCOVER = "discover"
JOB_KIND_INGEST = "ingest"
JOB_KIND_APPEND = "append"
JOB_KIND_DAY_CLOSE = "day_close"
JOB_KINDS_LIST = (JOB_KIND_DISCOVER, JOB_KIND_APPEND, JOB_KIND_DAY_CLOSE)
JOB_KINDS_ALL = (JOB_KIND_INGEST,) + JOB_KINDS_LIST

HOT_SCORE_BASE = 0
CATCHUP_SCORE_BASE = 10**15
SCORE_STRIDE = 10**6

JOB_LEASE_TTL_FLOOR_S = 60
JOB_LEASE_TTL_CEILING_S = 900
JOB_LEASE_TTL_POLL_MULTIPLIER = 6
INFLIGHT_REAP_GRACE_FLOOR_S = 30
LEASE_CONFLICT_SCORE_PENALTY = SCORE_STRIDE
JOB_ATTEMPT_MAX_DEFAULT = 5
QUEUE_MAX_MEMBERS_FLOOR = 1000
QUEUE_DEAD_LETTER_KIND = "queue_dead_letter"
UNSAFE_EVICTION_POLICY_PREFIX = "allkeys"

_INGEST_RANGED_POP_LUA = (
    "local members = redis.call('ZRANGEBYSCORE', KEYS[1], ARGV[1], ARGV[2], "
    "'LIMIT', 0, 1)\n"
    "if #members == 0 then return false end\n"
    "redis.call('ZREM', KEYS[1], members[1])\n"
    "return members[1]\n"
)

_LEASE_COMPARE_DEL_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('del', KEYS[1]) else return 0 end"
)

# KEYS: work zset, inflight hash.
# ARGV: lo, hi, owner, ttl, lease_prefix, deadline, penalty, probe.
_CLAIM_ZSET_LUA = (
    "-- HPS_CLAIM_ZSET\n"
    "for _ = 1, tonumber(ARGV[8]) do\n"
    "  local res = redis.call('ZRANGEBYSCORE', KEYS[1], ARGV[1], ARGV[2],"
    " 'WITHSCORES', 'LIMIT', 0, 1)\n"
    "  if #res == 0 then return false end\n"
    "  local member = res[1]\n"
    "  local score = res[2]\n"
    "  local lease = ARGV[5] .. member\n"
    "  if redis.call('SET', lease, ARGV[3], 'NX', 'EX', tonumber(ARGV[4]))"
    " then\n"
    "    redis.call('ZREM', KEYS[1], member)\n"
    "    redis.call('HSET', KEYS[2], member,"
    " ARGV[6] .. '|' .. ARGV[3] .. '|' .. score)\n"
    "    return {member, score}\n"
    "  end\n"
    "  redis.call('ZADD', KEYS[1], tonumber(score) + tonumber(ARGV[7]),"
    " member)\n"
    "end\n"
    "return false\n"
)

# KEYS: work list, inflight hash.
# ARGV: owner, ttl, lease_prefix, deadline, probe.
_CLAIM_LIST_LUA = (
    "-- HPS_CLAIM_LIST\n"
    "for _ = 1, tonumber(ARGV[5]) do\n"
    "  local member = redis.call('LPOP', KEYS[1])\n"
    "  if not member then return false end\n"
    "  local lease = ARGV[3] .. member\n"
    "  if redis.call('SET', lease, ARGV[1], 'NX', 'EX', tonumber(ARGV[2]))"
    " then\n"
    "    redis.call('HSET', KEYS[2], member, ARGV[4] .. '|' .. ARGV[1] .. '|')\n"
    "    return member\n"
    "  end\n"
    "  redis.call('RPUSH', KEYS[1], member)\n"
    "end\n"
    "return false\n"
)

# KEYS: inflight hash, lease key, payload key, pending set.
# ARGV: identity, owner.
_ACK_LUA = (
    "-- HPS_ACK\n"
    "local owned = 0\n"
    "local cur = redis.call('HGET', KEYS[1], ARGV[1])\n"
    "if cur then\n"
    "  local owner = string.match(cur, '^[^|]*|([^|]*)|')\n"
    "  if owner == ARGV[2] then\n"
    "    redis.call('HDEL', KEYS[1], ARGV[1])\n"
    "    owned = 1\n"
    "  end\n"
    "end\n"
    "if redis.call('GET', KEYS[2]) == ARGV[2] then\n"
    "  redis.call('DEL', KEYS[2])\n"
    "end\n"
    "-- Payload + pending are owner-gated: a late ack from a reaped owner must\n"
    "-- not wipe the new owner's attempt counter / fingerprint or clear dedupe.\n"
    "if owned == 1 then\n"
    "  redis.call('DEL', KEYS[3])\n"
    "  redis.call('SREM', KEYS[4], ARGV[1])\n"
    "end\n"
    "return owned\n"
)

# KEYS: work list, pending set, inflight hash. ARGV: identity, llen_cap.
_ENQUEUE_LIST_DEDUPE_LUA = (
    "-- HPS_ENQUEUE_LIST\n"
    "if redis.call('HEXISTS', KEYS[3], ARGV[1]) == 1 then return 0 end\n"
    "if redis.call('SADD', KEYS[2], ARGV[1]) == 0 then return 0 end\n"
    "local cap = tonumber(ARGV[2]) or 0\n"
    "if cap > 0 and redis.call('LLEN', KEYS[1]) >= cap then\n"
    "  redis.call('SREM', KEYS[2], ARGV[1])\n"
    "  return 0\n"
    "end\n"
    "redis.call('RPUSH', KEYS[1], ARGV[1])\n"
    "return 1\n"
)

# KEYS: inflight hash, lease key, work key.
# ARGV: identity, owner, mode ('z'|'l'), score.
_REQUEUE_LUA = (
    "-- HPS_REQUEUE\n"
    "local owned = 0\n"
    "local cur = redis.call('HGET', KEYS[1], ARGV[1])\n"
    "if cur then\n"
    "  local owner = string.match(cur, '^[^|]*|([^|]*)|')\n"
    "  if owner == ARGV[2] then\n"
    "    redis.call('HDEL', KEYS[1], ARGV[1])\n"
    "    owned = 1\n"
    "  end\n"
    "end\n"
    "if redis.call('GET', KEYS[2]) == ARGV[2] then\n"
    "  redis.call('DEL', KEYS[2])\n"
    "end\n"
    "if owned == 1 then\n"
    "  if ARGV[3] == 'z' then\n"
    "    redis.call('ZADD', KEYS[3], tonumber(ARGV[4]), ARGV[1])\n"
    "  else\n"
    "    redis.call('RPUSH', KEYS[3], ARGV[1])\n"
    "  end\n"
    "end\n"
    "return owned\n"
)

# KEYS: lease, inflight hash, work key.
# ARGV: identity, expected_owner, mode ('z'|'l'), score, penalty.
_STEAL_REQUEUE_LUA = (
    "-- HPS_STEAL_REQUEUE\n"
    "if redis.call('GET', KEYS[1]) ~= ARGV[2] then return 0 end\n"
    "redis.call('DEL', KEYS[1])\n"
    "local cur = redis.call('HGET', KEYS[2], ARGV[1])\n"
    "local score = tonumber(ARGV[4]) or 0\n"
    "if cur then\n"
    "  local stored = tonumber(string.match(cur, '^[^|]*|[^|]*|(.*)$') or '')\n"
    "  if stored ~= nil then score = stored end\n"
    "  redis.call('HDEL', KEYS[2], ARGV[1])\n"
    "  if ARGV[3] == 'z' then\n"
    "    redis.call('ZADD', KEYS[3], score + tonumber(ARGV[5]), ARGV[1])\n"
    "  else\n"
    "    redis.call('RPUSH', KEYS[3], ARGV[1])\n"
    "  end\n"
    "end\n"
    "return 1\n"
)

# KEYS: lease key, inflight hash. ARGV: identity, owner, ttl, deadline.
_RENEW_LUA = (
    "-- HPS_RENEW\n"
    "if redis.call('GET', KEYS[1]) ~= ARGV[2] then return 0 end\n"
    "redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3]))\n"
    "local cur = redis.call('HGET', KEYS[2], ARGV[1])\n"
    "if cur then\n"
    "  local owner = string.match(cur, '^[^|]*|([^|]*)|')\n"
    "  local score = string.match(cur, '^[^|]*|[^|]*|(.*)$')\n"
    "  if owner == ARGV[2] then\n"
    "    redis.call('HSET', KEYS[2], ARGV[1],"
    " ARGV[4] .. '|' .. ARGV[2] .. '|' .. (score or ''))\n"
    "  end\n"
    "end\n"
    "return 1\n"
)

# KEYS: inflight hash, work key.
# ARGV: cutoff, limit, mode ('z'|'l'), lease_prefix, penalty, cursor, count.
# Uses HSCAN (never HGETALL) so large inflight maps do not block Redis.
_REAP_LUA = (
    "-- HPS_REAP\n"
    "local cursor = ARGV[6] or '0'\n"
    "local count = tonumber(ARGV[7]) or 64\n"
    "local scanned = redis.call('HSCAN', KEYS[1], cursor, 'COUNT', count)\n"
    "local next_c = scanned[1]\n"
    "local all = scanned[2]\n"
    "local out = {next_c}\n"
    "local limit = tonumber(ARGV[2])\n"
    "local cutoff = tonumber(ARGV[1])\n"
    "local idx = 1\n"
    "while idx < #all do\n"
    "  local member = all[idx]\n"
    "  local value = all[idx + 1]\n"
    "  local deadline = tonumber(string.match(value, '^([^|]*)|') or '0')\n"
    "  if deadline == nil then deadline = 0 end\n"
    "  if deadline <= cutoff then\n"
    "    local owner = string.match(value, '^[^|]*|([^|]*)|')\n"
    "    local score = tonumber(string.match(value, '^[^|]*|[^|]*|(.*)$') or '')\n"
    "    redis.call('HDEL', KEYS[1], member)\n"
    "    local lease = ARGV[4] .. member\n"
    "    if redis.call('GET', lease) == owner then\n"
    "      redis.call('DEL', lease)\n"
    "    end\n"
    "    if ARGV[3] == 'z' then\n"
    "      if score == nil then score = 0 end\n"
    "      redis.call('ZADD', KEYS[2], score + tonumber(ARGV[5]), member)\n"
    "    else\n"
    "      redis.call('RPUSH', KEYS[2], member)\n"
    "    end\n"
    "    out[#out + 1] = member\n"
    "    if (#out - 1) >= limit then return out end\n"
    "  end\n"
    "  idx = idx + 2\n"
    "end\n"
    "return out\n"
)

_SCRIPT_SHA_CACHE: Dict[str, str] = {}
_SCRIPT_CACHE_LOCK = threading.Lock()
_BOOT_ID_CACHE: Optional[str] = None


def job_queue_key(kind: str) -> str:
  """
  Return the Redis LIST or ZSET key for a durable job kind.

  Ingest uses one ZSET; discover / append / day_close use LIST keys. Populate
  keeps the existing ``archive_members:populate_queue:v1`` key (not ``job:v1``).

  Args:
    kind (str): One of ``discover``, ``ingest``, ``append``, ``day_close``.

  Returns:
    str: Full Redis key under ``job:v1:queue:…``.

  Raises:
    ValueError: When ``kind`` is empty or unknown.

  Examples:
    >>> job_queue_key("ingest").endswith(":queue:ingest")
    True
  """
  text = str(kind or "").strip()
  if text == JOB_KIND_INGEST:
    return "%s:queue:ingest" % JOB_V1_PREFIX
  if text in JOB_KINDS_LIST:
    return "%s:queue:%s" % (JOB_V1_PREFIX, text)
  raise ValueError("unknown job kind %r" % (kind,))


def job_lease_key(kind: str, identity: str) -> str:
  """
  Return the ``SET NX EX`` lease key for a job identity.

  Args:
    kind (str): Job kind string (``ingest``, ``append``, …).
    identity (str): Idempotency identity (path fingerprint or day token).

  Returns:
    str: Full Redis lease key.

  Raises:
    ValueError: When ``kind`` or ``identity`` is empty.

  Examples:
    >>> ":lease:ingest:" in job_lease_key("ingest", "p|1|2")
    True
  """
  kind_text = str(kind or "").strip()
  ident = str(identity or "").strip()
  if not kind_text or not ident:
    raise ValueError("kind and identity are required for a lease key")
  return "%s:lease:%s:%s" % (JOB_V1_PREFIX, kind_text, ident)


def job_payload_key(kind: str, identity: str) -> str:
  """
  Return the optional payload HASH key for a job identity.

  Args:
    kind (str): Job kind string.
    identity (str): Idempotency identity.

  Returns:
    str: Full Redis payload key.

  Raises:
    ValueError: When ``kind`` or ``identity`` is empty.

  Examples:
    >>> job_payload_key("append", "day").endswith(":payload:append:day")
    True
  """
  kind_text = str(kind or "").strip()
  ident = str(identity or "").strip()
  if not kind_text or not ident:
    raise ValueError("kind and identity are required for a payload key")
  return "%s:payload:%s:%s" % (JOB_V1_PREFIX, kind_text, ident)


def ingest_identity(path: str, size: int = 0, mtime_ns: int = 0) -> str:
  """
  Build the ingest ZSET member / lease identity for a raw stats path.

  The lease and ZSET member are the stable normalized path. Size and mtime
  stay in the payload fingerprint so a growing file cannot fork a second
  lease. ``size`` and ``mtime_ns`` are accepted for caller compatibility
  and ignored when forming the member.

  Args:
    path (str): Absolute or relative raw stats path (normalized with
      ``os.path.normpath``).
    size (int): File size in bytes at enqueue time (fingerprint only).
    mtime_ns (int): ``st_mtime_ns`` fingerprint at enqueue time.

  Returns:
    str: Normalized path used as the durable identity.

  Raises:
    ValueError: When ``path`` is empty.

  Examples:
    >>> ingest_identity("/a/../b", 10, 20)
    '/b'
  """
  del size, mtime_ns
  text = str(path or "").strip()
  if not text:
    raise ValueError("path is required for ingest identity")
  return os.path.normpath(text)


def ingest_fingerprint(size: int, mtime_ns: int) -> str:
  """
  Encode a size/mtime pair for the ingest payload HASH.

  Args:
    size (int): File size in bytes.
    mtime_ns (int): ``st_mtime_ns`` at enqueue or re-stat time.

  Returns:
    str: ``size|mtime_ns`` fingerprint.

  Examples:
    >>> ingest_fingerprint(10, 20)
    '10|20'
  """
  return "%s|%s" % (int(size), int(mtime_ns))


def write_job_fingerprint(
  client: Any,
  *,
  kind: str,
  identity: str,
  fingerprint: str,
) -> None:
  """
  Store a job fingerprint on the payload HASH without giving the key a TTL.

  Args:
    client (Any): Redis client with ``hset``.
    kind (str): Job kind.
    identity (str): Job identity.
    fingerprint (str): Size/mtime encoding from :func:`ingest_fingerprint`.

  Returns:
    None

  Examples:
    >>> class _C:
    ...   def hset(self, *a, **k):
    ...     return 1
    >>> write_job_fingerprint(
    ...   _C(), kind="ingest", identity="/a", fingerprint="1|2",
    ... )
  """
  if client is None or not fingerprint:
    return
  client.hset(
      job_payload_key(kind, identity),
      mapping={"fingerprint": str(fingerprint)},
  )


def read_job_fingerprint(
  client: Any,
  *,
  kind: str,
  identity: str,
) -> str:
  """
  Return the stored fingerprint for a job identity, or empty.

  Args:
    client (Any): Redis client with ``hget``.
    kind (str): Job kind.
    identity (str): Job identity.

  Returns:
    str: Fingerprint text, or ``\"\"`` when unset.

  Examples:
    >>> class _C:
    ...   def hget(self, key, field):
    ...     return None
    >>> read_job_fingerprint(_C(), kind="ingest", identity="/a")
    ''
  """
  if client is None:
    return ""
  raw = client.hget(job_payload_key(kind, identity), "fingerprint")
  return "" if raw is None else str(raw)


def fingerprint_matches_path(path: str, fingerprint: str) -> bool:
  """
  Return True when ``path``'s current size/mtime matches ``fingerprint``.

  Args:
    path (str): Filesystem path to ``stat``.
    fingerprint (str): ``size|mtime_ns`` from :func:`ingest_fingerprint`.

  Returns:
    bool: True when the live stat matches.

  Examples:
    >>> fingerprint_matches_path("/missing", "1|2")
    False
  """
  text = str(fingerprint or "")
  if "|" not in text or not path:
    return False
  try:
    st = os.stat(path)
  except OSError:
    return False
  return text == ingest_fingerprint(st.st_size, st.st_mtime_ns)


def _tie_break_from_identity(identity: str) -> int:
  """
  Map an identity string into ``[0, SCORE_STRIDE)`` for same-day ordering.

  Args:
    identity (str): Ingest identity (or any stable member string).

  Returns:
    int: Non-negative tie-break strictly less than ``SCORE_STRIDE``.

  Examples:
    >>> 0 <= _tie_break_from_identity("x") < SCORE_STRIDE
    True
  """
  digest = hashlib.sha1(str(identity).encode("utf-8")).digest()
  return int.from_bytes(digest[:4], "big") % SCORE_STRIDE


def encode_ingest_score(
  *,
  band: str,
  day: date,
  today: date,
  identity: str,
) -> int:
  """
  Encode hot/catchup band + calendar day into an ingest ZSET score.

  Hot scores sit below ``CATCHUP_SCORE_BASE`` (newest calendar day first).
  Catchup scores start at ``CATCHUP_SCORE_BASE`` (oldest calendar day first).
  Band is recomputed at lease time; callers ``ZADD`` the same member with a
  new score to reband.

  Args:
    band (str): ``\"hot\"`` or ``\"catchup\"``.
    day (date): Calendar day of the raw file.
    today (date): Local \"today\" used for hot-window math.
    identity (str): Ingest identity (feeds same-day tie-break).

  Returns:
    int: Redis ZSET score.

  Raises:
    ValueError: When ``band`` is not ``hot`` or ``catchup``.

  Examples:
    >>> d = date(2026, 8, 20)
    >>> encode_ingest_score(band="hot", day=d, today=d, identity="a") < CATCHUP_SCORE_BASE
    True
  """
  tie = _tie_break_from_identity(identity)
  day_ord = int(day.toordinal())
  today_ord = int(today.toordinal())
  text = str(band or "").strip().lower()
  if text == "hot":
    raw = int(HOT_SCORE_BASE + (today_ord - day_ord) * SCORE_STRIDE + tie)
    return max(int(HOT_SCORE_BASE), raw)
  if text == "catchup":
    return int(CATCHUP_SCORE_BASE + day_ord * SCORE_STRIDE + tie)
  raise ValueError("band must be 'hot' or 'catchup', got %r" % (band,))


def decode_ingest_band(score: float | int) -> str:
  """
  Classify an ingest ZSET score as ``hot`` or ``catchup``.

  Args:
    score (float | int): ZSET score previously produced by
      :func:`encode_ingest_score`.

  Returns:
    str: ``\"hot\"`` when ``score < CATCHUP_SCORE_BASE``, else ``\"catchup\"``.

  Examples:
    >>> decode_ingest_band(0)
    'hot'
    >>> decode_ingest_band(CATCHUP_SCORE_BASE)
    'catchup'
  """
  if float(score) < float(CATCHUP_SCORE_BASE):
    return "hot"
  return "catchup"


def decode_catchup_calendar_day(score: float | int) -> date | None:
  """
  Recover the calendar day encoded in a catchup-band ingest ZSET score.

  Args:
    score (float | int): Score from :func:`encode_ingest_score` (catchup).

  Returns:
    date | None: Calendar day, or ``None`` when the score is hot or invalid.

  Examples:
    >>> d = date(2025, 5, 5)
    >>> s = encode_ingest_score(
    ...   band="catchup", day=d, today=d, identity="a",
    ... )
    >>> decode_catchup_calendar_day(s) == d
    True
  """
  if decode_ingest_band(score) != "catchup":
    return None
  day_ord = int(
      (float(score) - float(CATCHUP_SCORE_BASE)) // float(SCORE_STRIDE),
  )
  try:
    return date.fromordinal(day_ord)
  except (ValueError, OverflowError):
    return None


def ingest_score_range(band: str) -> tuple[float, float]:
  """
  Return inclusive ``(min_score, max_score)`` for a ranged ingest pop.

  The hot window is open at the bottom so a clock skew or a future-dated
  ``mtime`` cannot produce a negative score that no band can claim.

  Args:
    band (str): ``\"hot\"`` or ``\"catchup\"``.

  Returns:
    tuple[float, float]: Score window for ``ZRANGEBYSCORE`` / Lua pop.

  Raises:
    ValueError: When ``band`` is unknown.

  Examples:
    >>> ingest_score_range("hot")[1] < CATCHUP_SCORE_BASE
    True
    >>> ingest_score_range("hot")[0] == float("-inf")
    True
  """
  text = str(band or "").strip().lower()
  if text == "hot":
    return (float("-inf"), float(CATCHUP_SCORE_BASE) - 1.0)
  if text == "catchup":
    return (float(CATCHUP_SCORE_BASE), float("+inf"))
  raise ValueError("band must be 'hot' or 'catchup', got %r" % (band,))


def _score_arg(value: float) -> str:
  """
  Render a score bound as a Redis range argument.

  Args:
    value (float): Finite score, ``-inf``, or ``+inf``.

  Returns:
    str: ``\"-inf\"``, ``\"+inf\"``, or the integer score as text.

  Examples:
    >>> _score_arg(float("-inf"))
    '-inf'
    >>> _score_arg(7.0)
    '7'
  """
  if value == float("-inf"):
    return "-inf"
  if value == float("+inf"):
    return "+inf"
  return str(int(value))


def ingest_band_slot_caps(pool_size: int) -> tuple[int, int]:
  """
  Compute reserved hot/catchup ingest slot caps for a pool size.

  When both bands have work: hot gets ``max(1, (2 * pool) // 3)`` and catchup
  gets the remainder (floored to 1 when ``pool >= 2``). Empty band may use all
  slots at fill time (caller responsibility).

  Args:
    pool_size (int): ``sync_ingest_pool_processes`` (must be >= 1).

  Returns:
    tuple[int, int]: ``(hot_cap, catchup_cap)`` summing to ``pool_size``.

  Raises:
    ValueError: When ``pool_size`` is less than 1.

  Examples:
    >>> ingest_band_slot_caps(16)
    (10, 6)
    >>> ingest_band_slot_caps(1)
    (1, 0)
  """
  pool = int(pool_size)
  if pool < 1:
    raise ValueError("pool_size must be >= 1")
  if pool == 1:
    return (1, 0)
  hot_cap = max(1, (2 * pool) // 3)
  catchup_cap = pool - hot_cap
  if catchup_cap < 1:
    catchup_cap = 1
    hot_cap = pool - 1
  return (hot_cap, catchup_cap)


def job_lease_ttl_seconds() -> int:
  """
  Return lease EX seconds for durable job leases (OQ-1 option B).

  EX matches ``get_sync_ingest_per_file_timeout_max_s()`` (default 86400).
  Coordinators do **not** heartbeat-renew these leases; recovery is dead-owner
  steal plus :func:`reap_expired_inflight` after the deadline.

  Returns:
    int: Positive EX seconds (at least ``JOB_LEASE_TTL_FLOOR_S``).

  Examples:
    >>> job_lease_ttl_seconds() >= JOB_LEASE_TTL_FLOOR_S
    True
  """
  try:
    raw = int(cfg.get_sync_ingest_per_file_timeout_max_s())
  except Exception:
    raw = 86400
  if raw < JOB_LEASE_TTL_FLOOR_S:
    return JOB_LEASE_TTL_FLOOR_S
  return int(raw)


def inflight_reap_grace_seconds(ttl_s: int | None = None) -> int:
  """
  Return the grace period past an in-flight deadline before reaping.

  With OQ-1 long TTLs, grace stays small (not ``ttl // 2``) so a dead owner
  is not invisible for half a day after the deadline.

  Args:
    ttl_s (int | None): Lease TTL in seconds; unused for grace sizing under
      OQ-1 (kept so existing callers need no signature change).

  Returns:
    int: Grace seconds (``INFLIGHT_REAP_GRACE_FLOOR_S``).

  Examples:
    >>> inflight_reap_grace_seconds(86400) == INFLIGHT_REAP_GRACE_FLOOR_S
    True
  """
  del ttl_s
  return int(INFLIGHT_REAP_GRACE_FLOOR_S)


def job_max_attempts() -> int:
  """
  Return the attempt ceiling before a job is routed to the dead letter.

  Returns:
    int: Positive attempt ceiling.

  Examples:
    >>> job_max_attempts() >= 1
    True
  """
  try:
    raw = int(cfg.get_sync_archive_retry_max_attempts())
  except Exception:
    raw = JOB_ATTEMPT_MAX_DEFAULT
  return max(1, raw)


def current_boot_id() -> str:
  """
  Return a stable per-boot identifier for this host.

  Used so a lease minted before a reboot is recognized as stale even when the
  recycled PID happens to be alive again.

  Returns:
    str: Short boot identifier (``\"unknown\"`` when unavailable).

  Examples:
    >>> isinstance(current_boot_id(), str)
    True
  """
  global _BOOT_ID_CACHE
  if _BOOT_ID_CACHE is not None:
    return _BOOT_ID_CACHE
  boot = ""
  try:
    with open("/proc/sys/kernel/random/boot_id", "r", encoding="utf-8") as fh:
      boot = fh.read().strip().replace("-", "")[:16]
  except OSError:
    boot = ""
  if not boot:
    try:
      with open("/proc/stat", "r", encoding="utf-8") as fh:
        for line in fh:
          if line.startswith("btime "):
            boot = line.split()[1].strip()
            break
    except OSError:
      boot = ""
  if not boot:
    try:
      boot = str(int(time.time() - time.monotonic()))
    except (OSError, ValueError, OverflowError):
      boot = "unknown"
  _BOOT_ID_CACHE = _sanitize_token_field(boot) or "unknown"
  return _BOOT_ID_CACHE


def _sanitize_token_field(value: Any) -> str:
  """
  Strip lease-token separators from an owner token field.

  Args:
    value (Any): Raw field value (hostname, boot id, …).

  Returns:
    str: Field text with ``:`` and ``|`` replaced by ``_``.

  Examples:
    >>> _sanitize_token_field("a:b|c")
    'a_b_c'
  """
  text = str(value or "").strip()
  return text.replace(":", "_").replace("|", "_")


@dataclass(frozen=True)
class LeaseOwner:
  """
  Parsed lease owner token.

  Attributes:
    nonce: Random per-acquisition component.
    hostname: Host that minted the token (empty for legacy tokens).
    boot_id: Boot identifier of that host (empty for legacy tokens).
    pid: Owning process id, or ``None`` when unparsable.
  """

  nonce: str
  hostname: str
  boot_id: str
  pid: Optional[int]


def make_lease_owner_token(
  *,
  pid: int | None = None,
  hostname: str | None = None,
  boot_id: str | None = None,
) -> str:
  """
  Build a lease owner token ``{nonce}:{host}:{boot}:{pid}``.

  Host and boot identity are embedded so PID-liveness probes are only trusted
  when the probing process actually shares the owner's PID namespace.

  Args:
    pid (int | None): Owner PID; defaults to ``os.getpid()``.
    hostname (str | None): Owner hostname; defaults to the local hostname.
    boot_id (str | None): Owner boot id; defaults to :func:`current_boot_id`.

  Returns:
    str: Owner token string stored as the lease value.

  Examples:
    >>> tok = make_lease_owner_token(pid=42, hostname="h", boot_id="b")
    >>> tok.endswith(":h:b:42")
    True
  """
  owner_pid = int(os.getpid() if pid is None else pid)
  if hostname is None:
    try:
      host = socket.gethostname()
    except OSError:
      host = "unknown"
  else:
    host = hostname
  boot = current_boot_id() if boot_id is None else boot_id
  return "%s:%s:%s:%s" % (
      secrets.token_hex(16),
      _sanitize_token_field(host) or "unknown",
      _sanitize_token_field(boot) or "unknown",
      owner_pid,
  )


def parse_lease_owner(owner_token: str) -> LeaseOwner:
  """
  Parse an owner token into its identity components.

  Args:
    owner_token (str): Value stored as a lease value.

  Returns:
    LeaseOwner: Parsed components; ``hostname``/``boot_id`` are empty for
    legacy two-field tokens and ``pid`` is ``None`` when unparsable.

  Examples:
    >>> parse_lease_owner("n:h:b:7").hostname
    'h'
    >>> parse_lease_owner("n:7").hostname
    ''
  """
  text = str(owner_token or "")
  parts = text.split(":")
  pid: Optional[int] = None
  if len(parts) >= 2:
    try:
      pid = int(parts[-1])
    except ValueError:
      pid = None
  if len(parts) >= 4:
    return LeaseOwner(parts[0], parts[-3], parts[-2], pid)
  nonce = parts[0] if parts else ""
  return LeaseOwner(nonce, "", "", pid)


def lease_owner_is_locally_evaluable(
  owner: LeaseOwner,
  *,
  hostname: str | None = None,
  boot_id: str | None = None,
) -> bool:
  """
  Return True when this process may judge an owner's PID liveness.

  A PID probe is only meaningful when the token was minted by the same host
  **and** the same boot; otherwise a recycled PID on another node would look
  alive (or a foreign PID would look dead) and the lease would be stolen from
  a healthy worker.

  Args:
    owner (LeaseOwner): Parsed owner token.
    hostname (str | None): Local hostname override (tests).
    boot_id (str | None): Local boot id override (tests).

  Returns:
    bool: True when host and boot identity both match locally.

  Examples:
    >>> lease_owner_is_locally_evaluable(
    ...     parse_lease_owner("n:h:b:1"), hostname="h", boot_id="b",
    ... )
    True
    >>> lease_owner_is_locally_evaluable(
    ...     parse_lease_owner("n:other:b:1"), hostname="h", boot_id="b",
    ... )
    False
  """
  if not owner.hostname or not owner.boot_id:
    return False
  if hostname is None:
    try:
      hostname = socket.gethostname()
    except OSError:
      hostname = "unknown"
  local_host = _sanitize_token_field(hostname) or "unknown"
  local_boot = current_boot_id() if boot_id is None else (
      _sanitize_token_field(boot_id) or "unknown"
  )
  return owner.hostname == local_host and owner.boot_id == local_boot


def parse_lease_owner_pid(owner_token: str) -> int | None:
  """
  Extract the PID suffix from a lease owner token.

  Args:
    owner_token (str): Value returned by :func:`make_lease_owner_token`.

  Returns:
    int | None: Parsed PID, or ``None`` when the token shape is invalid.

  Examples:
    >>> parse_lease_owner_pid("abcd:99")
    99
    >>> parse_lease_owner_pid("bad") is None
    True
  """
  text = str(owner_token or "")
  if ":" not in text:
    return None
  suffix = text.rsplit(":", 1)[-1]
  try:
    return int(suffix)
  except ValueError:
    return None


def _pid_is_alive(pid: int) -> bool:
  """
  Return True when ``pid`` appears alive on this host.

  Args:
    pid (int): Process id to probe with ``os.kill(pid, 0)``.

  Returns:
    bool: True when the process exists (or permission denies the probe).

  Examples:
    >>> _pid_is_alive(os.getpid())
    True
  """
  if pid <= 0:
    return False
  try:
    os.kill(pid, 0)
  except ProcessLookupError:
    return False
  except PermissionError:
    return True
  except OSError:
    return False
  return True


def try_acquire_job_lease(
  client: Any,
  *,
  kind: str,
  identity: str,
  owner_token: str | None = None,
  ttl_s: int | None = None,
) -> str:
  """
  Acquire a job lease with ``SET NX EX``; return owner token or ``\"\"``.

  Args:
    client (Any): Redis client with ``set(..., nx=True, ex=…)``.
    kind (str): Job kind.
    identity (str): Job identity.
    owner_token (str | None): Optional prebuilt token; otherwise minted.
    ttl_s (int | None): Optional EX override; default
      :func:`job_lease_ttl_seconds`.

  Returns:
    str: Owner token on success, or empty string when the lease is held /
    Redis rejects the set.

  Examples:
    >>> class _C:
    ...   def set(self, *a, **k):
    ...     return True
    >>> bool(try_acquire_job_lease(_C(), kind="ingest", identity="x", owner_token="t:1"))
    True
  """
  if client is None:
    return ""
  token = owner_token or make_lease_owner_token()
  ex = int(ttl_s) if ttl_s is not None else job_lease_ttl_seconds()
  key = job_lease_key(kind, identity)
  acquired = client.set(key, token, nx=True, ex=ex)
  if not acquired:
    return ""
  return token


def release_job_lease(
  client: Any,
  *,
  kind: str,
  identity: str,
  owner_token: str,
) -> bool:
  """
  Compare-and-del a job lease; non-owner tokens are a no-op.

  Args:
    client (Any): Redis client supporting ``eval`` / ``evalsha`` / ``get``.
    kind (str): Job kind.
    identity (str): Job identity.
    owner_token (str): Token returned by :func:`try_acquire_job_lease`.

  Returns:
    bool: True when this owner deleted the lease key.

  Raises:
    Exception: Redis transport or script errors propagate so a failed release
      is visible instead of silently leaving a lease to expire.

  Examples:
    >>> class _C:
    ...   def __init__(self):
    ...     self.v = "tok:1"
    ...   def evalsha(self, sha, n, key, token):
    ...     if self.v == token:
    ...       self.v = None
    ...       return 1
    ...     return 0
    ...   def script_load(self, s):
    ...     return "sha"
    >>> release_job_lease(_C(), kind="ingest", identity="x", owner_token="tok:1")
    True
  """
  if client is None or not owner_token:
    return False
  key = job_lease_key(kind, identity)
  raw = eval_job_script(client, _LEASE_COMPARE_DEL_LUA, 1, key, owner_token)
  return bool(raw)


def job_lease_scan_pattern() -> str:
  """
  Return the ``SCAN MATCH`` pattern for every ``job:v1`` lease key.

  Args:
    None.

  Returns:
    str: Pattern ending in ``:lease:*``.

  Examples:
    >>> job_lease_scan_pattern().endswith(":lease:*")
    True
  """
  return "%s:lease:*" % JOB_V1_PREFIX


def parse_job_lease_key(key: str) -> tuple[str, str] | None:
  """
  Split a lease Redis key into ``(kind, identity)``.

  Args:
    key (str): Full ``job:v1:lease:…`` key.

  Returns:
    tuple[str, str] | None: Kind and identity, or ``None`` when malformed.

  Examples:
    >>> parse_job_lease_key(job_lease_key("ingest", "/a"))
    ('ingest', '/a')
  """
  prefix = "%s:lease:" % JOB_V1_PREFIX
  text = str(key or "")
  if not text.startswith(prefix):
    return None
  rest = text[len(prefix):]
  kind, sep, identity = rest.partition(":")
  if not sep or kind not in JOB_KINDS_ALL or not identity:
    return None
  return kind, identity


def steal_dead_owner_leases(
  client: Any,
  *,
  pid_alive_fn: Any | None = None,
  hostname: str | None = None,
  boot_id: str | None = None,
) -> int:
  """
  SCAN lease keys and steal those whose local owner PID is dead.

  Uses ``SCAN MATCH`` (never ``KEYS``). Foreign-host and other-boot tokens
  are left for TTL expiry plus the in-flight reaper.

  Args:
    client (Any): Redis client with ``scan_iter`` / ``get``.
    pid_alive_fn (Any | None): Optional ``callable(pid) -> bool``.
    hostname (str | None): Local hostname override (tests).
    boot_id (str | None): Local boot id override (tests).

  Returns:
    int: Number of leases deleted.

  Examples:
    >>> steal_dead_owner_leases(None)
    0
  """
  if client is None:
    return 0
  scan = getattr(client, "scan_iter", None)
  if scan is None:
    return 0
  stolen = 0
  for key in scan(match=job_lease_scan_pattern(), count=200):
    parsed = parse_job_lease_key(str(key))
    if parsed is None:
      continue
    kind, identity = parsed
    if steal_job_lease_if_owner_dead(
        client,
        kind=kind,
        identity=identity,
        pid_alive_fn=pid_alive_fn,
        hostname=hostname,
        boot_id=boot_id,
    ):
      stolen += 1
  return stolen


def steal_job_lease_if_owner_dead(
  client: Any,
  *,
  kind: str,
  identity: str,
  pid_alive_fn: Any | None = None,
  hostname: str | None = None,
  boot_id: str | None = None,
) -> bool:
  """
  Delete a lease when a **locally owned** lease's owner PID is dead.

  Missing lease is not \"job done\" — callers still reconstruct from disk+DB.
  A lease minted by another host, another boot, or with a legacy token shape
  is never stolen on a PID probe, because this process cannot observe that
  owner's liveness; those leases are recovered by TTL expiry plus
  :func:`reap_expired_inflight` instead.

  Args:
    client (Any): Redis client with ``get`` / ``delete``.
    kind (str): Job kind.
    identity (str): Job identity.
    pid_alive_fn (Any | None): Optional ``callable(pid) -> bool``; defaults
      to :func:`_pid_is_alive`.
    hostname (str | None): Local hostname override (tests).
    boot_id (str | None): Local boot id override (tests).

  Returns:
    bool: True when a dead-owner lease was stolen. The job is requeued
    only when an inflight HASH entry existed; a nil inflight never
    fabricates a LIST/ZSET member.

  Examples:
    >>> client = type("C", (), {})()  # doctest: +SKIP
    >>> steal_job_lease_if_owner_dead(
    ...     client, kind="ingest", identity="x", pid_alive_fn=lambda _p: False,
    ... )
    False
  """
  if client is None:
    return False
  key = job_lease_key(kind, identity)
  raw = client.get(key)
  if raw is None:
    return False
  owner = parse_lease_owner(str(raw))
  if owner.pid is None:
    return False
  if not lease_owner_is_locally_evaluable(
      owner, hostname=hostname, boot_id=boot_id,
  ):
    return False
  alive = (_pid_is_alive if pid_alive_fn is None else pid_alive_fn)(owner.pid)
  if alive:
    return False
  raw_owner = str(raw)
  mode = "z" if str(kind) == JOB_KIND_INGEST else "l"
  score = 0.0
  if mode == "z":
    try:
      entries = read_inflight_entries(client, kind=kind)
    except Exception:
      entries = {}
    if identity in entries:
      _deadline, _own, stored_score = entries[identity]
      if stored_score is not None:
        score = float(stored_score)
  stolen = eval_job_script(
      client,
      _STEAL_REQUEUE_LUA,
      3,
      key,
      job_inflight_key(kind),
      job_queue_key(kind),
      str(identity),
      raw_owner,
      mode,
      "%d" % int(score),
      LEASE_CONFLICT_SCORE_PENALTY,
  )
  return bool(stolen)


def _script_sha(client: Any, lua: str) -> str:
  """
  Return the cached ``SCRIPT LOAD`` sha for a Lua source.

  Args:
    client (Any): Redis client with ``script_load``.
    lua (str): Lua source text.

  Returns:
    str: Script SHA usable with ``EVALSHA``.

  Examples:
    >>> class _C:
    ...   def script_load(self, s):
    ...     return "deadbeef"
    >>> _script_sha(_C(), "return 1")
    'deadbeef'
  """
  with _SCRIPT_CACHE_LOCK:
    sha = _SCRIPT_SHA_CACHE.get(lua)
  if sha:
    return sha
  sha = str(client.script_load(lua))
  with _SCRIPT_CACHE_LOCK:
    _SCRIPT_SHA_CACHE[lua] = sha
  return sha


def _forget_script_sha(lua: str) -> None:
  """
  Drop a cached script sha after a ``NOSCRIPT`` error.

  Args:
    lua (str): Lua source text whose sha is stale.

  Returns:
    None

  Examples:
    >>> _forget_script_sha("return 1")
  """
  with _SCRIPT_CACHE_LOCK:
    _SCRIPT_SHA_CACHE.pop(lua, None)


def _is_noscript_error(exc: BaseException) -> bool:
  """
  Return True when an exception means the server forgot the script.

  Args:
    exc (BaseException): Exception raised by ``EVALSHA``.

  Returns:
    bool: True when the error is a ``NOSCRIPT`` condition.

  Examples:
    >>> _is_noscript_error(Exception("NOSCRIPT No matching script"))
    True
    >>> _is_noscript_error(Exception("LOADING"))
    False
  """
  return "NOSCRIPT" in str(exc).upper()


def eval_job_script(client: Any, lua: str, numkeys: int, *args: Any) -> Any:
  """
  Run a queue Lua script by sha, reloading once on ``NOSCRIPT``.

  Unlike a bare ``try``/``except`` fallback to ``EVAL``, connectivity and
  logic errors propagate: a claim that cannot be evaluated must not look like
  an empty queue.

  Args:
    client (Any): Redis client with ``script_load`` / ``evalsha``.
    lua (str): Lua source text.
    numkeys (int): Number of leading ``args`` that are Redis keys.
    *args (Any): Keys followed by ARGV values.

  Returns:
    Any: Raw Lua return value.

  Raises:
    Exception: Any Redis error other than a recoverable ``NOSCRIPT``.

  Examples:
    >>> class _C:
    ...   def script_load(self, s):
    ...     return "sha"
    ...   def evalsha(self, sha, n, *a):
    ...     return 1
    >>> eval_job_script(_C(), "return 1", 0)
    1
  """
  sha = _script_sha(client, lua)
  try:
    return client.evalsha(sha, numkeys, *args)
  except Exception as exc:
    if not _is_noscript_error(exc):
      raise
    _forget_script_sha(lua)
    sha = _script_sha(client, lua)
    return client.evalsha(sha, numkeys, *args)


def ensure_ingest_ranged_pop_sha(client: Any) -> str:
  """
  ``SCRIPT LOAD`` the ranged ingest pop Lua and return its sha.

  Args:
    client (Any): Redis client with ``script_load``.

  Returns:
    str: Script SHA for ``EVALSHA``.

  Examples:
    >>> class _C:
    ...   def script_load(self, s):
    ...     return "deadbeef"
    >>> ensure_ingest_ranged_pop_sha(_C())
    'deadbeef'
  """
  return _script_sha(client, _INGEST_RANGED_POP_LUA)


def reset_job_queue_script_cache_for_tests() -> None:
  """
  Clear cached Lua SHAs and host identity memos (unit tests only).

  Returns:
    None

  Examples:
    >>> reset_job_queue_script_cache_for_tests()
  """
  global _BOOT_ID_CACHE
  with _SCRIPT_CACHE_LOCK:
    _SCRIPT_SHA_CACHE.clear()
  _BOOT_ID_CACHE = None


def zadd_ingest_job(
  client: Any,
  *,
  identity: str,
  score: float | int,
  fingerprint: str | None = None,
) -> int:
  """
  ``ZADD`` an ingest identity onto the single ingest ZSET (overwrite score).

  New members are refused when the queue is at capacity; overwriting the
  score of an identity that is already queued is always allowed so a reband
  cannot be stranded behind the cap.

  Args:
    client (Any): Redis client with ``zadd`` / ``zscore``.
    identity (str): Ingest member identity.
    score (float | int): Band-encoded score from :func:`encode_ingest_score`.
    fingerprint (str | None): Optional size/mtime payload to store TTL-free.

  Returns:
    int: Redis ``ZADD`` return (new members added), or ``0`` when capped.

  Examples:
    >>> class _C:
    ...   def zscore(self, key, member):
    ...     return None
    ...   def zcard(self, key):
    ...     return 0
    ...   def zadd(self, key, mapping):
    ...     return 1
    >>> zadd_ingest_job(_C(), identity="/a", score=0)
    1
  """
  key = job_queue_key(JOB_KIND_INGEST)
  ident = str(identity)
  try:
    if client.hexists(job_inflight_key(JOB_KIND_INGEST), ident):
      return 0
  except Exception:
    pass
  try:
    existing = client.zscore(key, ident)
  except Exception:
    existing = None
  if existing is None and not queue_has_capacity(client, kind=JOB_KIND_INGEST):
    return 0
  added = int(client.zadd(key, {ident: float(score)}))
  if fingerprint:
    write_job_fingerprint(
        client,
        kind=JOB_KIND_INGEST,
        identity=ident,
        fingerprint=str(fingerprint),
    )
  return added


def pop_ingest_job_ranged(
  client: Any,
  *,
  band: str,
) -> str | None:
  """
  Atomically pop one ingest identity from a score range (never whole-key).

  Uses ``EVALSHA`` of ranged ``ZRANGEBYSCORE`` + ``ZREM``. Whole-key
  ``ZPOPMIN`` is forbidden (would starve catchup).

  Args:
    client (Any): Redis client with ``evalsha`` / ``eval`` / ``script_load``.
    band (str): ``\"hot\"`` or ``\"catchup\"``.

  Returns:
    str | None: Popped identity, or ``None`` when the range is empty.

  Examples:
    >>> class _C:
    ...   def script_load(self, s):
    ...     return "sha"
    ...   def evalsha(self, sha, n, key, lo, hi):
    ...     return None
    >>> pop_ingest_job_ranged(_C(), band="hot") is None
    True
  """
  lo, hi = ingest_score_range(band)
  key = job_queue_key(JOB_KIND_INGEST)
  raw = eval_job_script(
      client, _INGEST_RANGED_POP_LUA, 1, key, _score_arg(lo), _score_arg(hi),
  )
  if raw is None or raw is False:
    return None
  text = str(raw)
  return text if text else None


def enqueue_list_job(
  client: Any,
  *,
  kind: str,
  identity: str,
  dedupe: bool = False,
) -> int:
  """
  ``RPUSH`` a discover/append/day_close identity onto its LIST queue.

  With ``dedupe=True`` the push is guarded by a pending SET and the in-flight
  hash, so a repeated discover pass cannot grow the queue without bound with
  identities that are already queued or already being worked.

  Args:
    client (Any): Redis client with ``rpush`` (plus ``evalsha`` when
      ``dedupe`` is set).
    kind (str): One of :data:`JOB_KINDS_LIST`.
    identity (str): Job identity string.
    dedupe (bool): Skip the push when the identity is queued or in flight.

  Returns:
    int: List length after push, or ``0`` when a dedupe check skipped it.

  Raises:
    ValueError: When ``kind`` is not a LIST kind.

  Examples:
    >>> class _C:
    ...   def rpush(self, key, value):
    ...     return 1
    >>> enqueue_list_job(_C(), kind="append", identity="p")
    1
  """
  if str(kind) not in JOB_KINDS_LIST:
    raise ValueError("kind %r is not a LIST queue kind" % (kind,))
  if not dedupe:
    cap = queue_capacity_limit()
    try:
      depth = int(client.llen(job_queue_key(kind)) or 0)
    except Exception:
      depth = 0
    if cap > 0 and depth >= cap:
      return 0
    return int(client.rpush(job_queue_key(kind), str(identity)))
  raw = eval_job_script(
      client,
      _ENQUEUE_LIST_DEDUPE_LUA,
      3,
      job_queue_key(kind),
      job_pending_set_key(kind),
      job_inflight_key(kind),
      str(identity),
      queue_capacity_limit(),
  )
  return int(raw or 0)


def job_inflight_key(kind: str) -> str:
  """
  Return the in-flight HASH key that holds claimed jobs for a kind.

  Each field is a job identity and each value is
  ``\"{deadline}|{owner_token}|{score}\"``, so a reaper can restore work
  without consulting any other structure.

  Args:
    kind (str): Job kind string.

  Returns:
    str: Full Redis in-flight key.

  Raises:
    ValueError: When ``kind`` is unknown.

  Examples:
    >>> job_inflight_key("ingest").endswith(":inflight:ingest")
    True
  """
  text = str(kind or "").strip()
  if text not in JOB_KINDS_ALL:
    raise ValueError("unknown job kind %r" % (kind,))
  return "%s:inflight:%s" % (JOB_V1_PREFIX, text)


def job_pending_set_key(kind: str) -> str:
  """
  Return the dedupe SET key mirroring queued LIST identities.

  Args:
    kind (str): Job kind string.

  Returns:
    str: Full Redis pending-set key.

  Raises:
    ValueError: When ``kind`` is unknown.

  Examples:
    >>> job_pending_set_key("append").endswith(":pending:append")
    True
  """
  text = str(kind or "").strip()
  if text not in JOB_KINDS_ALL:
    raise ValueError("unknown job kind %r" % (kind,))
  return "%s:pending:%s" % (JOB_V1_PREFIX, text)


def job_lease_prefix(kind: str) -> str:
  """
  Return the lease key prefix for a kind (Lua builds ``prefix .. identity``).

  Args:
    kind (str): Job kind string.

  Returns:
    str: Lease key prefix ending in ``:``.

  Raises:
    ValueError: When ``kind`` is unknown.

  Examples:
    >>> job_lease_prefix("ingest").endswith(":lease:ingest:")
    True
  """
  text = str(kind or "").strip()
  if text not in JOB_KINDS_ALL:
    raise ValueError("unknown job kind %r" % (kind,))
  return "%s:lease:%s:" % (JOB_V1_PREFIX, text)


@dataclass(frozen=True)
class ClaimedJob:
  """
  One atomically claimed job.

  Attributes:
    kind: Job kind the claim came from.
    identity: Job identity (ZSET member or LIST element).
    owner_token: Lease owner token proving this claim.
    deadline: Epoch seconds after which a reaper may recover the job.
    score: Original ingest score, or ``None`` for LIST kinds.
  """

  kind: str
  identity: str
  owner_token: str
  deadline: float
  score: Optional[float]


def _claim_probe_depth() -> int:
  """
  Return how many contended candidates one claim may skip past.

  Returns:
    int: Probe depth used by the claim Lua loops.

  Examples:
    >>> _claim_probe_depth() >= 1
    True
  """
  return 8


def ingest_claim_probe_depth(*, hot_q: int = 0, pool: int = 1) -> int:
  """
  Return claim probe depth, elevated when the hot queue exceeds pool size.

  Args:
    hot_q (int): Hot-band queued depth.
    pool (int): Ingest pool process count.

  Returns:
    int: Bounded probe depth for :func:`claim_ingest_job`.

  Examples:
    >>> ingest_claim_probe_depth(hot_q=0, pool=16)
    8
    >>> ingest_claim_probe_depth(hot_q=500, pool=16) > 8
    True
  """
  base = _claim_probe_depth()
  if int(hot_q or 0) <= int(pool or 1):
    return base
  elevated = max(base, int(hot_q) // max(1, int(pool)))
  return min(64, elevated)


def claim_ingest_job(
  client: Any,
  *,
  band: str,
  owner_token: str,
  ttl_s: int | None = None,
  now_s: float | None = None,
  probe_depth: int | None = None,
) -> Optional[ClaimedJob]:
  """
  Atomically claim one ingest job: ranged pop + lease + in-flight record.

  The pop, the ``SET NX EX`` lease, and the in-flight write happen inside one
  Lua script, so a crash between them is impossible: either the job is still
  queued or it is recorded in flight with a deadline.

  Args:
    client (Any): Redis client with ``script_load`` / ``evalsha``.
    band (str): ``\"hot\"`` or ``\"catchup\"``.
    owner_token (str): Token from :func:`make_lease_owner_token`.
    ttl_s (int | None): Lease TTL override; default
      :func:`job_lease_ttl_seconds`.
    now_s (float | None): Clock override (tests).
    probe_depth (int | None): Lua skip depth override; default
      :func:`_claim_probe_depth`.

  Returns:
    Optional[ClaimedJob]: Claim, or ``None`` when the band has no free work.

  Raises:
    ValueError: When ``band`` is unknown or ``owner_token`` is empty.

  Examples:
    >>> class _C:
    ...   def script_load(self, s):
    ...     return "sha"
    ...   def evalsha(self, *a, **k):
    ...     return False
    >>> claim_ingest_job(_C(), band="hot", owner_token="t:h:b:1") is None
    True
  """
  if not owner_token:
    raise ValueError("owner_token is required to claim a job")
  lo, hi = ingest_score_range(band)
  ttl = int(job_lease_ttl_seconds() if ttl_s is None else ttl_s)
  now = time.time() if now_s is None else float(now_s)
  deadline = now + ttl
  depth = int(probe_depth if probe_depth is not None else _claim_probe_depth())
  raw = eval_job_script(
      client,
      _CLAIM_ZSET_LUA,
      2,
      job_queue_key(JOB_KIND_INGEST),
      job_inflight_key(JOB_KIND_INGEST),
      _score_arg(lo),
      _score_arg(hi),
      owner_token,
      ttl,
      job_lease_prefix(JOB_KIND_INGEST),
      "%.3f" % deadline,
      LEASE_CONFLICT_SCORE_PENALTY,
      depth,
  )
  if not raw:
    return None
  member = raw[0] if isinstance(raw, (list, tuple)) else raw
  score_raw = raw[1] if isinstance(raw, (list, tuple)) and len(raw) > 1 else None
  identity = member.decode() if isinstance(member, bytes) else str(member)
  try:
    score = float(score_raw) if score_raw is not None else None
  except (TypeError, ValueError):
    score = None
  return ClaimedJob(
      kind=JOB_KIND_INGEST,
      identity=identity,
      owner_token=owner_token,
      deadline=deadline,
      score=score,
  )


def claim_list_job(
  client: Any,
  *,
  kind: str,
  owner_token: str,
  ttl_s: int | None = None,
  now_s: float | None = None,
) -> Optional[ClaimedJob]:
  """
  Atomically claim one discover/append/day_close job.

  Args:
    client (Any): Redis client with ``script_load`` / ``evalsha``.
    kind (str): One of :data:`JOB_KINDS_LIST`.
    owner_token (str): Token from :func:`make_lease_owner_token`.
    ttl_s (int | None): Lease TTL override.
    now_s (float | None): Clock override (tests).

  Returns:
    Optional[ClaimedJob]: Claim, or ``None`` when the queue has no free work.

  Raises:
    ValueError: When ``kind`` is not a LIST kind or ``owner_token`` is empty.

  Examples:
    >>> class _C:
    ...   def script_load(self, s):
    ...     return "sha"
    ...   def evalsha(self, *a, **k):
    ...     return False
    >>> claim_list_job(_C(), kind="append", owner_token="t:h:b:1") is None
    True
  """
  if str(kind) not in JOB_KINDS_LIST:
    raise ValueError("kind %r is not a LIST queue kind" % (kind,))
  if not owner_token:
    raise ValueError("owner_token is required to claim a job")
  ttl = int(job_lease_ttl_seconds() if ttl_s is None else ttl_s)
  now = time.time() if now_s is None else float(now_s)
  deadline = now + ttl
  raw = eval_job_script(
      client,
      _CLAIM_LIST_LUA,
      2,
      job_queue_key(kind),
      job_inflight_key(kind),
      owner_token,
      ttl,
      job_lease_prefix(kind),
      "%.3f" % deadline,
      _claim_probe_depth(),
  )
  if not raw:
    return None
  identity = raw.decode() if isinstance(raw, bytes) else str(raw)
  return ClaimedJob(
      kind=str(kind),
      identity=identity,
      owner_token=owner_token,
      deadline=deadline,
      score=None,
  )


def ack_job(
  client: Any,
  *,
  kind: str,
  identity: str,
  owner_token: str,
) -> bool:
  """
  Mark a claimed job terminal: clear in-flight, lease, payload, and dedupe.

  Only the recorded owner clears the in-flight entry, so a late ack from a
  reaped worker cannot erase the accounting of the worker that took over.

  Args:
    client (Any): Redis client with ``script_load`` / ``evalsha``.
    kind (str): Job kind.
    identity (str): Job identity.
    owner_token (str): Owner token from the claim.

  Returns:
    bool: True when this owner cleared its own in-flight entry.

  Examples:
    >>> class _C:
    ...   def script_load(self, s):
    ...     return "sha"
    ...   def evalsha(self, *a, **k):
    ...     return 0
    >>> ack_job(_C(), kind="append", identity="d", owner_token="t:h:b:1")
    False
  """
  if client is None or not owner_token:
    return False
  raw = eval_job_script(
      client,
      _ACK_LUA,
      4,
      job_inflight_key(kind),
      job_lease_key(kind, identity),
      job_payload_key(kind, identity),
      job_pending_set_key(kind),
      str(identity),
      owner_token,
  )
  return bool(raw)


def requeue_job(
  client: Any,
  *,
  kind: str,
  identity: str,
  owner_token: str,
  score: float | int | None = None,
) -> bool:
  """
  Return a claimed job to its queue after a retryable failure.

  Args:
    client (Any): Redis client with ``script_load`` / ``evalsha``.
    kind (str): Job kind.
    identity (str): Job identity.
    owner_token (str): Owner token from the claim.
    score (float | int | None): Ingest score to restore; ignored for LIST
      kinds and defaulted to :data:`CATCHUP_SCORE_BASE` when omitted for
      ingest so a retry never jumps ahead of fresh hot work.

  Returns:
    bool: True when this owner requeued its own claim.

  Examples:
    >>> class _C:
    ...   def script_load(self, s):
    ...     return "sha"
    ...   def evalsha(self, *a, **k):
    ...     return 1
    >>> requeue_job(_C(), kind="append", identity="d", owner_token="t:h:b:1")
    True
  """
  if client is None or not owner_token:
    return False
  is_ingest = str(kind) == JOB_KIND_INGEST
  mode = "z" if is_ingest else "l"
  if is_ingest:
    restore = CATCHUP_SCORE_BASE if score is None else float(score)
  else:
    restore = 0.0
  raw = eval_job_script(
      client,
      _REQUEUE_LUA,
      3,
      job_inflight_key(kind),
      job_lease_key(kind, identity),
      job_queue_key(kind),
      str(identity),
      owner_token,
      mode,
      "%d" % int(restore),
  )
  return bool(raw)


def renew_job_lease(
  client: Any,
  *,
  kind: str,
  identity: str,
  owner_token: str,
  ttl_s: int | None = None,
  now_s: float | None = None,
) -> bool:
  """
  Compare-and-extend a held lease and push out its in-flight deadline.

  Long-running work must call this well inside :func:`job_lease_ttl_seconds`
  or a reaper will treat the job as abandoned.

  Args:
    client (Any): Redis client with ``script_load`` / ``evalsha``.
    kind (str): Job kind.
    identity (str): Job identity.
    owner_token (str): Owner token from the claim.
    ttl_s (int | None): TTL override; default :func:`job_lease_ttl_seconds`.
    now_s (float | None): Clock override (tests).

  Returns:
    bool: True when the lease was still owned and got extended.

  Examples:
    >>> class _C:
    ...   def script_load(self, s):
    ...     return "sha"
    ...   def evalsha(self, *a, **k):
    ...     return 1
    >>> renew_job_lease(_C(), kind="ingest", identity="x", owner_token="t:h:b:1")
    True
  """
  if client is None or not owner_token:
    return False
  ttl = int(job_lease_ttl_seconds() if ttl_s is None else ttl_s)
  now = time.time() if now_s is None else float(now_s)
  raw = eval_job_script(
      client,
      _RENEW_LUA,
      2,
      job_lease_key(kind, identity),
      job_inflight_key(kind),
      str(identity),
      owner_token,
      ttl,
      "%.3f" % (now + ttl),
  )
  return bool(raw)


def reap_expired_inflight(
  client: Any,
  *,
  kind: str,
  now_s: float | None = None,
  limit: int = 64,
  ttl_s: int | None = None,
) -> List[str]:
  """
  Requeue in-flight jobs whose deadline (plus grace) has passed.

  This is the recovery path for a worker that died, was ``SIGKILL``ed, or hung
  past its lease without renewing. Requeued ingest jobs are deprioritized by
  :data:`LEASE_CONFLICT_SCORE_PENALTY` so a poison identity cannot monopolize
  the hot band.

  Args:
    client (Any): Redis client with ``script_load`` / ``evalsha``.
    kind (str): Job kind.
    now_s (float | None): Clock override (tests).
    limit (int): Maximum identities to recover in one call.
    ttl_s (int | None): Lease TTL used to size the reap grace period.

  Returns:
    List[str]: Identities returned to the queue.

  Examples:
    >>> class _C:
    ...   def script_load(self, s):
    ...     return "sha"
    ...   def evalsha(self, *a, **k):
    ...     return []
    >>> reap_expired_inflight(_C(), kind="ingest")
    []
  """
  if client is None:
    return []
  now = time.time() if now_s is None else float(now_s)
  cutoff = now - inflight_reap_grace_seconds(ttl_s)
  mode = "z" if str(kind) == JOB_KIND_INGEST else "l"
  want = max(1, int(limit))
  cursor = "0"
  scan_count = max(want, 64)
  out: List[str] = []
  # Bounded HSCAN pages — never one-shot HGETALL of the full inflight HASH.
  for _ in range(256):
    raw = eval_job_script(
        client,
        _REAP_LUA,
        2,
        job_inflight_key(kind),
        job_queue_key(kind),
        "%.3f" % cutoff,
        want - len(out),
        mode,
        job_lease_prefix(kind),
        LEASE_CONFLICT_SCORE_PENALTY,
        cursor,
        scan_count,
    )
    if not raw:
      break
    decoded = [
        item.decode() if isinstance(item, bytes) else str(item)
        for item in raw
    ]
    cursor = decoded[0]
    out.extend(decoded[1:])
    if len(out) >= want or cursor in ("0", 0, b"0"):
      break
  return out[:want]


def read_inflight_entries(
  client: Any,
  *,
  kind: str,
) -> Dict[str, Tuple[float, str, Optional[float]]]:
  """
  Return the in-flight map for a kind as parsed tuples.

  Args:
    client (Any): Redis client with ``hgetall``.
    kind (str): Job kind.

  Returns:
    Dict[str, Tuple[float, str, Optional[float]]]: Identity mapped to
    ``(deadline, owner_token, score)``.

  Examples:
    >>> class _C:
    ...   def hgetall(self, key):
    ...     return {"a": "1.5|tok|7"}
    >>> read_inflight_entries(_C(), kind="ingest")["a"][1]
    'tok'
  """
  if client is None:
    return {}
  raw = client.hgetall(job_inflight_key(kind)) or {}
  out: Dict[str, Tuple[float, str, Optional[float]]] = {}
  for key, value in raw.items():
    ident = key.decode() if isinstance(key, bytes) else str(key)
    text = value.decode() if isinstance(value, bytes) else str(value)
    parts = text.split("|", 2)
    try:
      deadline = float(parts[0])
    except (IndexError, ValueError):
      deadline = 0.0
    owner = parts[1] if len(parts) > 1 else ""
    score: Optional[float]
    try:
      score = float(parts[2]) if len(parts) > 2 and parts[2] else None
    except ValueError:
      score = None
    out[ident] = (deadline, owner, score)
  return out


def count_inflight_by_band(client: Any) -> Tuple[int, int]:
  """
  Count in-flight ingest claims per band from recorded scores.

  Args:
    client (Any): Redis client with ``hgetall``.

  Returns:
    Tuple[int, int]: ``(hot_inflight, catchup_inflight)``.

  Examples:
    >>> class _C:
    ...   def hgetall(self, key):
    ...     return {"a": "1|t|0", "b": "1|t|%d" % CATCHUP_SCORE_BASE}
    >>> count_inflight_by_band(_C())
    (1, 1)
  """
  hot = 0
  catchup = 0
  for _ident, (_deadline, _owner, score) in read_inflight_entries(
      client, kind=JOB_KIND_INGEST,
  ).items():
    if score is None or decode_ingest_band(score) == "hot":
      hot += 1
    else:
      catchup += 1
  return (hot, catchup)


def bump_job_attempt(client: Any, *, kind: str, identity: str) -> int:
  """
  Increment and return the attempt counter for a job identity.

  Args:
    client (Any): Redis client with ``hincrby``.
    kind (str): Job kind.
    identity (str): Job identity.

  Returns:
    int: Attempt count after the increment (``0`` when Redis is unavailable).

  Examples:
    >>> class _C:
    ...   def hincrby(self, key, field, amount):
    ...     return 3
    >>> bump_job_attempt(_C(), kind="ingest", identity="x")
    3
  """
  if client is None:
    return 0
  try:
    return int(client.hincrby(job_payload_key(kind, identity), "attempt", 1))
  except (TypeError, ValueError):
    return 0


def read_job_attempt(client: Any, *, kind: str, identity: str) -> int:
  """
  Read the recorded attempt counter for a job identity.

  Args:
    client (Any): Redis client with ``hget``.
    kind (str): Job kind.
    identity (str): Job identity.

  Returns:
    int: Attempt count, or ``0`` when unset.

  Examples:
    >>> class _C:
    ...   def hget(self, key, field):
    ...     return None
    >>> read_job_attempt(_C(), kind="ingest", identity="x")
    0
  """
  if client is None:
    return 0
  raw = client.hget(job_payload_key(kind, identity), "attempt")
  if raw is None:
    return 0
  try:
    return int(raw)
  except (TypeError, ValueError):
    return 0


def queue_dead_letter_path(archive_data_dir: str) -> str:
  """
  Return the sidecar path for queue dead letters.

  Args:
    archive_data_dir (str): Archive data directory root.

  Returns:
    str: Absolute path to the queue dead-letter artifact.

  Examples:
    >>> queue_dead_letter_path("/a").endswith("queue_dead_letter.json")
    True
  """
  from hpcperfstats.dbload.lib.sync_timedb_persistence import artifact_path

  return artifact_path(archive_data_dir, QUEUE_DEAD_LETTER_KIND)


def append_queue_dead_letter(
  archive_data_dir: str,
  *,
  kind: str,
  identity: str,
  attempt: int,
  reason: str,
  max_entries: int = 5000,
) -> bool:
  """
  Record a job that exhausted its retries so it is not silently dropped.

  Args:
    archive_data_dir (str): Archive data directory root.
    kind (str): Job kind.
    identity (str): Job identity.
    attempt (int): Attempt count at give-up time.
    reason (str): Short failure reason.
    max_entries (int): Cap on retained entries (oldest trimmed first).

  Returns:
    bool: True when the entry was persisted.

  Examples:
    >>> append_queue_dead_letter("/nonexistent-dir", kind="ingest",
    ...     identity="x", attempt=9, reason="boom")  # doctest: +SKIP
  """
  from hpcperfstats.dbload.lib.sync_timedb_persistence import (
      load_persistence_document,
      save_persistence_document,
  )

  path = queue_dead_letter_path(archive_data_dir)
  entries = load_persistence_document(
      path, QUEUE_DEAD_LETTER_KIND, default=[],
  )
  if not isinstance(entries, list):
    entries = []
  entries.append({
      "kind": str(kind),
      "identity": str(identity),
      "attempt": int(attempt),
      "reason": str(reason)[:500],
      "recorded_at": time.time(),
  })
  if len(entries) > max_entries:
    entries = entries[-max_entries:]
  try:
    save_persistence_document(path, QUEUE_DEAD_LETTER_KIND, entries)
  except OSError:
    return False
  return True


def identity_in_queue_dead_letter(
  archive_data_dir: str,
  *,
  kind: str,
  identity: str,
) -> bool:
  """
  Return True when ``kind``/``identity`` is already in the queue dead-letter.

  Reconstruct and discover must not re-enqueue parked poison (P1-17).

  Args:
    archive_data_dir (str): Archive data directory root.
    kind (str): Job kind.
    identity (str): Job identity.

  Returns:
    bool: True when a matching dead-letter entry exists.

  Examples:
    >>> identity_in_queue_dead_letter("/nope", kind="ingest", identity="x")
    False
  """
  from hpcperfstats.dbload.lib.sync_timedb_persistence import (
      load_persistence_document,
  )

  if not archive_data_dir or not identity:
    return False
  path = queue_dead_letter_path(archive_data_dir)
  entries = load_persistence_document(
      path, QUEUE_DEAD_LETTER_KIND, default=[],
  )
  if not isinstance(entries, list):
    return False
  want_kind = str(kind)
  want_id = str(identity)
  for entry in entries:
    if not isinstance(entry, dict):
      continue
    if str(entry.get("kind") or "") != want_kind:
      continue
    if str(entry.get("identity") or "") == want_id:
      return True
  return False


def queue_capacity_limit() -> int:
  """
  Return the maximum member count allowed per durable queue key.

  Returns:
    int: Positive capacity bound.

  Examples:
    >>> queue_capacity_limit() >= QUEUE_MAX_MEMBERS_FLOOR
    True
  """
  try:
    raw = int(cfg.get_sync_job_queue_max_members())
  except Exception:
    raw = 2_000_000
  return max(QUEUE_MAX_MEMBERS_FLOOR, raw)


def queue_depth(client: Any, *, kind: str) -> int:
  """
  Return the queued (not in-flight) depth for a job kind.

  Args:
    client (Any): Redis client with ``zcard`` / ``llen``.
    kind (str): Job kind.

  Returns:
    int: Queued member count.

  Examples:
    >>> class _C:
    ...   def zcard(self, key):
    ...     return 4
    >>> queue_depth(_C(), kind="ingest")
    4
  """
  if client is None:
    return 0
  key = job_queue_key(kind)
  if str(kind) == JOB_KIND_INGEST:
    return int(client.zcard(key) or 0)
  return int(client.llen(key) or 0)


def queue_has_capacity(
  client: Any,
  *,
  kind: str,
  limit: int | None = None,
) -> bool:
  """
  Return True when a queue is below its configured capacity bound.

  An unbounded queue turns a discover storm into an out-of-memory Redis, so
  producers check capacity before enqueueing more work.

  Args:
    client (Any): Redis client with ``zcard`` / ``llen``.
    kind (str): Job kind.
    limit (int | None): Capacity override; default :func:`queue_capacity_limit`.

  Returns:
    bool: True when another enqueue is allowed.

  Examples:
    >>> class _C:
    ...   def zcard(self, key):
    ...     return 1
    >>> queue_has_capacity(_C(), kind="ingest", limit=10)
    True
  """
  cap = queue_capacity_limit() if limit is None else int(limit)
  return queue_depth(client, kind=kind) < cap


def queue_census(client: Any) -> Dict[str, Dict[str, int]]:
  """
  Return per-kind queued and in-flight counts for one log line.

  Args:
    client (Any): Redis client with ``zcard`` / ``llen`` / ``hlen``.

  Returns:
    Dict[str, Dict[str, int]]: ``{kind: {"queued": n, "inflight": n}}``.

  Examples:
    >>> class _C:
    ...   def zcard(self, key):
    ...     return 2
    ...   def llen(self, key):
    ...     return 0
    ...   def hlen(self, key):
    ...     return 1
    >>> queue_census(_C())["ingest"]["queued"]
    2
  """
  out: Dict[str, Dict[str, int]] = {}
  for kind in JOB_KINDS_ALL:
    try:
      inflight = int(client.hlen(job_inflight_key(kind)) or 0)
    except (TypeError, ValueError, AttributeError):
      inflight = 0
    out[kind] = {
        "queued": queue_depth(client, kind=kind),
        "inflight": inflight,
    }
  return out


def format_queue_census(census: Dict[str, Dict[str, int]]) -> str:
  """
  Render :func:`queue_census` output as a compact log field.

  Args:
    census (Dict[str, Dict[str, int]]): Census mapping.

  Returns:
    str: ``kind=current/queued`` (inflight/queued) pairs joined by spaces.

  Examples:
    >>> format_queue_census({"ingest": {"queued": 2, "inflight": 1}})
    'ingest=1/2'
  """
  parts = []
  for kind in JOB_KINDS_ALL:
    entry = census.get(kind)
    if not entry:
      continue
    parts.append(
        "%s=%d/%d"
        % (kind, entry.get("inflight", 0), entry.get("queued", 0)),
    )
  return " ".join(parts)


def unsafe_eviction_policy(policy: str) -> bool:
  """
  Return True when a Redis ``maxmemory-policy`` may evict queue keys.

  Any ``allkeys-*`` policy lets Redis delete durable ``job:v1`` keys under
  memory pressure, which would silently drop queued work.

  Args:
    policy (str): Value of ``maxmemory-policy``.

  Returns:
    bool: True when the policy is unsafe for durable queues.

  Examples:
    >>> unsafe_eviction_policy("allkeys-lru")
    True
    >>> unsafe_eviction_policy("noeviction")
    False
  """
  return str(policy or "").strip().lower().startswith(
      UNSAFE_EVICTION_POLICY_PREFIX,
  )


def check_redis_queue_safety(client: Any) -> List[str]:
  """
  Return operator-facing problems that would silently lose queued jobs.

  Checks the server eviction policy and asserts no durable queue key carries
  a TTL (a TTL on a queue key deletes work that has not been done yet).

  Args:
    client (Any): Redis client with ``config_get`` and ``ttl``.

  Returns:
    List[str]: Human-readable problem strings (empty when safe).

  Examples:
    >>> class _C:
    ...   def config_get(self, name):
    ...     return {"maxmemory-policy": "noeviction"}
    ...   def ttl(self, key):
    ...     return -1
    >>> check_redis_queue_safety(_C())
    []
  """
  problems: List[str] = []
  if client is None:
    return ["redis client unavailable for queue safety check"]
  try:
    conf = client.config_get("maxmemory-policy") or {}
    policy = str(conf.get("maxmemory-policy", ""))
  except Exception as exc:
    policy = ""
    problems.append("cannot read maxmemory-policy: %s" % (exc,))
  if policy and unsafe_eviction_policy(policy):
    problems.append(
        "maxmemory-policy=%s evicts durable job:v1 keys; use noeviction or a "
        "volatile-* policy" % policy,
    )
  for kind in JOB_KINDS_ALL:
    for key in (job_queue_key(kind), job_inflight_key(kind)):
      try:
        ttl = int(client.ttl(key))
      except Exception:
        continue
      if ttl >= 0:
        problems.append("queue key %s has a TTL (%ss)" % (key, ttl))
  return problems


def pop_list_job(client: Any, *, kind: str) -> str | None:
  """
  ``LPOP`` one identity from a LIST job queue (FIFO with :func:`enqueue_list_job`).

  Args:
    client (Any): Redis client with ``lpop``.
    kind (str): One of :data:`JOB_KINDS_LIST`.

  Returns:
    str | None: Identity, or ``None`` when empty.

  Raises:
    ValueError: When ``kind`` is not a LIST kind.

  Examples:
    >>> class _C:
    ...   def lpop(self, key):
    ...     return None
    >>> pop_list_job(_C(), kind="discover") is None
    True
  """
  if str(kind) not in JOB_KINDS_LIST:
    raise ValueError("kind %r is not a LIST queue kind" % (kind,))
  raw = client.lpop(job_queue_key(kind))
  if raw is None:
    return None
  return str(raw)
