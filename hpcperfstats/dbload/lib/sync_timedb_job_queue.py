"""
Redis ``job:v1`` work-queue helpers for the sync_timedb greenfield orchestrator.

Library-only (slice 1): key names, ingest ZSET score encode/decode, reserved
slot caps, ``SET NX EX`` leases, and ranged Lua pop via ``EVALSHA``. Not wired
into ``sync_timedb.py`` until the orchestrator cutover slice.

Attributes:
  CATCHUP_SCORE_BASE: Score floor for catchup-band ingest ZSET members.
  HOT_SCORE_BASE: Score floor for hot-band ingest ZSET members.
  JOB_KIND_APPEND: Job kind string for append LIST queue.
  JOB_KIND_DAY_CLOSE: Job kind string for day_close LIST queue.
  JOB_KIND_DISCOVER: Job kind string for discover LIST queue.
  JOB_KIND_INGEST: Job kind string for ingest ZSET queue.
  JOB_KINDS_LIST: Tuple of kinds that use Redis LIST queues.
  JOB_LEASE_TTL_FLOOR_S: Minimum lease EX seconds when INI max is tiny.
  JOB_V1_PREFIX: Redis key prefix ``…:job:v1``.
  KEY_PREFIX: Shared ``hpcperfstats:sync_timedb`` namespace root.
  SCORE_STRIDE: Day/tie-break stride inside a band score range.
  _INGEST_RANGED_POP_LUA: Lua source for one atomic ranged ZSET pop.
  _INGEST_RANGED_POP_SHA: Cached SCRIPT LOAD sha for the ranged-pop Lua.
  _LEASE_COMPARE_DEL_LUA: Lua source for compare-and-del lease release.
  _LEASE_COMPARE_DEL_SHA: Cached SCRIPT LOAD sha for lease compare-and-del.
"""
from __future__ import annotations

from typing import Any

import hashlib
import os
import secrets
from datetime import date

from hpcperfstats.dbload.lib import conf_parser as cfg

KEY_PREFIX = "hpcperfstats:sync_timedb"
JOB_V1_PREFIX = "%s:job:v1" % KEY_PREFIX

JOB_KIND_DISCOVER = "discover"
JOB_KIND_INGEST = "ingest"
JOB_KIND_APPEND = "append"
JOB_KIND_DAY_CLOSE = "day_close"
JOB_KINDS_LIST = (JOB_KIND_DISCOVER, JOB_KIND_APPEND, JOB_KIND_DAY_CLOSE)

HOT_SCORE_BASE = 0
CATCHUP_SCORE_BASE = 10**15
SCORE_STRIDE = 10**6
JOB_LEASE_TTL_FLOOR_S = 60

_INGEST_RANGED_POP_LUA = (
    "local members = redis.call('ZRANGEBYSCORE', KEYS[1], ARGV[1], ARGV[2], "
    "'LIMIT', 0, 1)\n"
    "if #members == 0 then return false end\n"
    "redis.call('ZREM', KEYS[1], members[1])\n"
    "return members[1]\n"
)
_INGEST_RANGED_POP_SHA: str | None = None

_LEASE_COMPARE_DEL_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('del', KEYS[1]) else return 0 end"
)
_LEASE_COMPARE_DEL_SHA: str | None = None


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


def ingest_identity(path: str, size: int, mtime_ns: int) -> str:
  """
  Build the ingest ZSET member / lease identity for a raw stats path.

  Args:
    path (str): Absolute or relative raw stats path (normalized with
      ``os.path.normpath``).
    size (int): File size in bytes at enqueue time.
    mtime_ns (int): ``st_mtime_ns`` fingerprint at enqueue time.

  Returns:
    str: Stable identity ``normpath|size|mtime_ns``.

  Raises:
    ValueError: When ``path`` is empty.

  Examples:
    >>> ingest_identity("/a/../b", 10, 20)
    '/b|10|20'
  """
  text = str(path or "").strip()
  if not text:
    raise ValueError("path is required for ingest identity")
  return "%s|%s|%s" % (os.path.normpath(text), int(size), int(mtime_ns))


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
    return int(HOT_SCORE_BASE + (today_ord - day_ord) * SCORE_STRIDE + tie)
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


def ingest_score_range(band: str) -> tuple[float, float]:
  """
  Return inclusive ``(min_score, max_score)`` for a ranged ingest pop.

  Args:
    band (str): ``\"hot\"`` or ``\"catchup\"``.

  Returns:
    tuple[float, float]: Score window for ``ZRANGEBYSCORE`` / Lua pop.

  Raises:
    ValueError: When ``band`` is unknown.

  Examples:
    >>> ingest_score_range("hot")[1] < CATCHUP_SCORE_BASE
    True
  """
  text = str(band or "").strip().lower()
  if text == "hot":
    return (float(HOT_SCORE_BASE), float(CATCHUP_SCORE_BASE) - 1.0)
  if text == "catchup":
    return (float(CATCHUP_SCORE_BASE), float("+inf"))
  raise ValueError("band must be 'hot' or 'catchup', got %r" % (band,))


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
  Return lease EX seconds for ingest/append/discover/day_close jobs.

  TTL option B: ``get_sync_ingest_per_file_timeout_max_s()`` (default 86400).
  No heartbeat renew on these leases.

  Returns:
    int: Positive EX seconds (floor ``JOB_LEASE_TTL_FLOOR_S``).

  Examples:
    >>> job_lease_ttl_seconds() >= JOB_LEASE_TTL_FLOOR_S
    True
  """
  try:
    raw = int(cfg.get_sync_ingest_per_file_timeout_max_s())
  except Exception:
    raw = 86400
  return max(JOB_LEASE_TTL_FLOOR_S, raw)


def make_lease_owner_token(*, pid: int | None = None) -> str:
  """
  Build a lease owner token ``{hex}:{pid}`` for ``SET NX EX``.

  Args:
    pid (int | None): Owner PID; defaults to ``os.getpid()``.

  Returns:
    str: Owner token string stored as the lease value.

  Examples:
    >>> tok = make_lease_owner_token(pid=42)
    >>> tok.endswith(":42")
    True
  """
  owner_pid = int(os.getpid() if pid is None else pid)
  return "%s:%s" % (secrets.token_hex(16), owner_pid)


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

  Examples:
    >>> class _C:
    ...   def __init__(self):
    ...     self.v = "tok:1"
    ...   def eval(self, script, n, key, token):
    ...     if self.v == token:
    ...       self.v = None
    ...       return 1
    ...     return 0
    ...   def evalsha(self, *a, **k):
    ...     raise Exception("nosha")
    ...   def script_load(self, s):
    ...     return "sha"
    >>> release_job_lease(_C(), kind="ingest", identity="x", owner_token="tok:1")
    True
  """
  if client is None or not owner_token:
    return False
  key = job_lease_key(kind, identity)
  global _LEASE_COMPARE_DEL_SHA
  try:
    if not _LEASE_COMPARE_DEL_SHA:
      _LEASE_COMPARE_DEL_SHA = str(client.script_load(_LEASE_COMPARE_DEL_LUA))
    raw = client.evalsha(_LEASE_COMPARE_DEL_SHA, 1, key, owner_token)
  except Exception:
    try:
      raw = client.eval(_LEASE_COMPARE_DEL_LUA, 1, key, owner_token)
    except Exception:
      return False
  return bool(raw)


def steal_job_lease_if_owner_dead(
  client: Any,
  *,
  kind: str,
  identity: str,
  pid_alive_fn: Any | None = None,
) -> bool:
  """
  Delete a lease when the recorded owner PID is dead (boot steal).

  Missing lease is not \"job done\" — callers still reconstruct from disk+DB.
  A live-but-silent owner may hold the lease until TTL.

  Args:
    client (Any): Redis client with ``get`` / ``delete``.
    kind (str): Job kind.
    identity (str): Job identity.
    pid_alive_fn (Any | None): Optional ``callable(pid) -> bool``; defaults
      to :func:`_pid_is_alive`.

  Returns:
    bool: True when a dead-owner lease was deleted.

  Examples:
    >>> class _C:
    ...   def __init__(self):
    ...     self.store = {}
    ...   def get(self, key):
    ...     return self.store.get(key)
    ...   def delete(self, key):
    ...     self.store.pop(key, None)
    >>> c = _C()
    >>> c.store[job_lease_key("ingest", "x")] = "t:1"
    >>> steal_job_lease_if_owner_dead(
    ...     c, kind="ingest", identity="x", pid_alive_fn=lambda _p: False,
    ... )
    True
  """
  if client is None:
    return False
  key = job_lease_key(kind, identity)
  raw = client.get(key)
  if raw is None:
    return False
  pid = parse_lease_owner_pid(str(raw))
  if pid is None:
    return False
  alive = (_pid_is_alive if pid_alive_fn is None else pid_alive_fn)(pid)
  if alive:
    return False
  client.delete(key)
  return True


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
  global _INGEST_RANGED_POP_SHA
  if _INGEST_RANGED_POP_SHA:
    return _INGEST_RANGED_POP_SHA
  sha = str(client.script_load(_INGEST_RANGED_POP_LUA))
  _INGEST_RANGED_POP_SHA = sha
  return sha


def reset_job_queue_script_cache_for_tests() -> None:
  """
  Clear cached Lua SHAs (unit tests only).

  Returns:
    None

  Examples:
    >>> reset_job_queue_script_cache_for_tests()
  """
  global _INGEST_RANGED_POP_SHA, _LEASE_COMPARE_DEL_SHA
  _INGEST_RANGED_POP_SHA = None
  _LEASE_COMPARE_DEL_SHA = None


def zadd_ingest_job(
  client: Any,
  *,
  identity: str,
  score: float | int,
) -> int:
  """
  ``ZADD`` an ingest identity onto the single ingest ZSET (overwrite score).

  Args:
    client (Any): Redis client with ``zadd``.
    identity (str): Ingest member identity.
    score (float | int): Band-encoded score from :func:`encode_ingest_score`.

  Returns:
    int: Redis ``ZADD`` return (new members added).

  Examples:
    >>> class _C:
    ...   def zadd(self, key, mapping):
    ...     return 1
    >>> zadd_ingest_job(_C(), identity="a|1|2", score=0)
    1
  """
  return int(client.zadd(job_queue_key(JOB_KIND_INGEST), {str(identity): float(score)}))


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
  lo_s = str(int(lo)) if lo != float("-inf") else "-inf"
  hi_s = "+inf" if hi == float("+inf") else str(int(hi))
  try:
    sha = ensure_ingest_ranged_pop_sha(client)
    raw = client.evalsha(sha, 1, key, lo_s, hi_s)
  except Exception:
    raw = client.eval(_INGEST_RANGED_POP_LUA, 1, key, lo_s, hi_s)
  if raw is None or raw is False:
    return None
  text = str(raw)
  return text if text else None


def enqueue_list_job(client: Any, *, kind: str, identity: str) -> int:
  """
  ``RPUSH`` a discover/append/day_close identity onto its LIST queue.

  Args:
    client (Any): Redis client with ``rpush``.
    kind (str): One of :data:`JOB_KINDS_LIST`.
    identity (str): Job identity string.

  Returns:
    int: List length after push.

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
  return int(client.rpush(job_queue_key(kind), str(identity)))


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
