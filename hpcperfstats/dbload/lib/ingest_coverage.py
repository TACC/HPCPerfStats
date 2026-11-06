"""
Background sweep of the ``(type, event)`` pairs each host writes to host_data.

The ``ingest_coverage_sweeper`` supervisord program
(``hpcperfstats/dbload/ingest_coverage_sweeper.py``) calls :func:`run_sweep` once
per ``ingest_coverage_sweep_interval_s``. A sweep:

1. lists every FQDN from Valkey ``recent_host:*`` (no sampling);
2. reads each host's newest sample with one batched
   ``latest_sample_time_by_host(fqdns, since=now - 7 days)`` call; hosts
   without a sample in that window are stored as ``no_recent_data`` and not
   scanned, so no query reaches compressed chunks (``compress_after`` 8 d);
3. scans hosts one at a time, oldest ``scanned_at`` first, with one
   literal-parameter ``GROUP BY type, event`` over ``(newest - window,
   newest]`` (lower bound never older than 7 days) under ``SET LOCAL
   statement_timeout``; a failure stores that host as ``incomplete`` and the
   sweep continues;
4. stores ``ingest_coverage:v1:host:{fqdn}`` (JSON) and each distinct pair
   list once under ``ingest_coverage:v1:sig:{sha1}``, TTL = 3 x interval;
5. stops at the interval boundary; unreached hosts keep their older
   ``scanned_at`` and are scanned first next time.

Per-host freshness comes from listend's ``ingest_rotation:v1:{fqdn}`` record
(:func:`derive_host_status`), never from a fixed hours setting
(``raw-segment-rotation-freshness-contract.mdc``). The web payload
(``admin_monitor_ingest_coverage``) reads only these Valkey keys.

The heartbeat ``ingest_coverage:v1:sweep`` is written at sweep start, every
``HEARTBEAT_EVERY_HOSTS`` hosts and at sweep end.

Attributes:
  KEY_PREFIX: Prefix of every coverage key.
  HOST_KEY_PREFIX: Per-host record prefix.
  SIG_KEY_PREFIX: Pair-list blob prefix (by signature).
  SWEEP_KEY: Heartbeat key.
  RECENT_DATA_DAYS: Lower bound for every host_data read (days).
  HEARTBEAT_EVERY_HOSTS: Hosts between mid-sweep heartbeat writes.
  STATUS_CURRENT: Closed data is fully ingested.
  STATUS_INGEST_BEHIND: Same closed file not ingested on two sweeps.
  STATUS_UNKNOWN: No usable rotation record (or backlog seen only once).
  STATUS_INCOMPLETE: The host's scan failed.
  STATUS_NO_RECENT_DATA: No host_data row in the last ``RECENT_DATA_DAYS``.
  SCAN_SQL: Per-host aggregate (literal parameters only).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from hpcperfstats.lib.ingest_rotation import load_rotation_entries

KEY_PREFIX = "ingest_coverage:v1:"
HOST_KEY_PREFIX = f"{KEY_PREFIX}host:"
SIG_KEY_PREFIX = f"{KEY_PREFIX}sig:"
SWEEP_KEY = f"{KEY_PREFIX}sweep"
RECENT_DATA_DAYS = 7
HEARTBEAT_EVERY_HOSTS = 50

STATUS_CURRENT = "current"
STATUS_INGEST_BEHIND = "ingest_behind"
STATUS_UNKNOWN = "unknown"
STATUS_INCOMPLETE = "incomplete"
STATUS_NO_RECENT_DATA = "no_recent_data"

SCAN_SQL = """
  SELECT type,
         event,
         COUNT(*)::bigint AS row_count,
         BOOL_OR(COALESCE(value, 0) <> 0 OR COALESCE(arc, 0) <> 0)
  FROM host_data
  WHERE host = %s
    AND time > %s
    AND time <= %s
    AND type NOT ILIKE '%%error%%'
    AND event NOT ILIKE '%%error%%'
  GROUP BY type, event
  ORDER BY type, event
"""


def host_key(fqdn: str) -> str:
  """
  Return the per-host coverage record key.

  Args:
    fqdn (str): Host FQDN.

  Returns:
    str: ``ingest_coverage:v1:host:{fqdn}``.

  Examples:
    >>> host_key("c101-001.example.com")
    'ingest_coverage:v1:host:c101-001.example.com'
  """
  return f"{HOST_KEY_PREFIX}{fqdn}"


def sig_key(signature: str) -> str:
  """
  Return the key holding one distinct pair list.

  Args:
    signature (str): Hex sha1 from :func:`pair_set_signature`.

  Returns:
    str: ``ingest_coverage:v1:sig:{signature}``.

  Examples:
    >>> sig_key("ab")
    'ingest_coverage:v1:sig:ab'
  """
  return f"{SIG_KEY_PREFIX}{signature}"


def pair_set_signature(pairs: Iterable[Sequence[str]]) -> str:
  """
  Return a stable sha1 of a set of ``(type, event)`` pairs.

  Order and duplicates do not matter; the empty set has a signature too.

  Args:
    pairs (Iterable[Sequence[str]]): Pairs.

  Returns:
    str: 40-character hex digest.

  Examples:
    >>> a = pair_set_signature([("b", "1"), ("a", "2")])
    >>> a == pair_set_signature([("a", "2"), ("b", "1"), ("a", "2")])
    True
  """
  text = "\n".join(sorted({f"{p[0]}\t{p[1]}" for p in pairs}))
  return hashlib.sha1(text.encode("utf-8"), usedforsecurity=False).hexdigest()


def epoch_seconds(value: Any) -> int | None:
  """
  Convert a datetime or number to whole Unix seconds (None passes through).

  Whole seconds match the monitor file timestamps (``int(float(t))``), so
  float or microsecond rounding cannot create a false backlog.

  Args:
    value (Any): ``datetime`` (naive means UTC), int/float seconds, or None.

  Returns:
    int | None: Whole seconds.

  Examples:
    >>> epoch_seconds(datetime(1970, 1, 1, 0, 0, 5, 900000, tzinfo=UTC))
    5
    >>> epoch_seconds(None) is None
    True
  """
  if value is None:
    return None
  if isinstance(value, datetime):
    if value.tzinfo is None:
      value = value.replace(tzinfo=UTC)
    return int(value.timestamp())
  return int(float(value))


def derive_host_status(
  rotations: Sequence[Mapping[str, Any]],
  newest_sample: Any,
  previous: Mapping[str, Any] | None,
  now: float,
) -> dict[str, Any]:
  """
  Per-host freshness from listend rotations vs the newest host_data sample.

  * ``closed_until`` = newest ``rotated_at``; ``open_segment_age_s`` =
    ``now - closed_until`` (data still in ``current``; normal, never
    flagged).
  * ``closed_last_sample_at`` = the newest rotation that has one (a failed
    tail read stores null, so older rotations are the fallback).
  * ``ingest_backlog_s`` = ``closed_last_sample_at - newest_sample``, floor
    0, both monitor sample seconds.
  * ``current`` when the backlog is 0; ``ingest_behind`` when the previous
    sweep saw a backlog for the **same** ``closed_last_sample_at``;
    otherwise ``unknown`` (no usable rotation, or backlog seen once).

  Args:
    rotations (Sequence[Mapping[str, Any]]): ``ingest_rotation`` entries,
      newest first.
    newest_sample (Any): Host's newest ``host_data.time``.
    previous (Mapping[str, Any] | None): The host's previous coverage
      record (two-sweep memory), or None.
    now (float): Current epoch seconds.

  Returns:
    dict[str, Any]: ``status``, ``status_reason``, ``closed_until``,
    ``closed_last_sample_at``, ``open_segment_age_s``, ``ingest_backlog_s``,
    ``prev_closed_last_sample_at``, ``prev_backlog_s``.

  Examples:
    >>> rot = [{"rotated_at": 1000, "closed_last_sample_at": 990}]
    >>> derive_host_status(rot, 990, None, 1100)["status"]
    'current'
    >>> derive_host_status([], 990, None, 1100)["status"]
    'unknown'
  """
  prev = previous or {}
  closed_until = max(
    (epoch_seconds(r.get("rotated_at")) or 0 for r in rotations), default=None
  )
  closed_last = next(
    (
      epoch_seconds(r["closed_last_sample_at"])
      for r in rotations
      if r.get("closed_last_sample_at") is not None
    ),
    None,
  )
  newest = epoch_seconds(newest_sample)
  backlog = (
    max(0, closed_last - newest)
    if closed_last is not None and newest is not None
    else None
  )
  prev_closed = prev.get("closed_last_sample_at")
  prev_backlog = prev.get("ingest_backlog_s")
  if backlog is None:
    status, reason = STATUS_UNKNOWN, "no rotation with a closed sample time"
  elif backlog == 0:
    status, reason = STATUS_CURRENT, "closed segments are in host_data"
  elif prev_closed == closed_last and (prev_backlog or 0) > 0:
    status, reason = STATUS_INGEST_BEHIND, "closed file not ingested"
  else:
    status, reason = STATUS_UNKNOWN, "backlog seen once; confirming"
  return {
    "status": status,
    "status_reason": reason,
    "closed_until": closed_until,
    "closed_last_sample_at": closed_last,
    "open_segment_age_s": (
      max(0, int(now) - closed_until) if closed_until else None
    ),
    "ingest_backlog_s": backlog,
    "prev_closed_last_sample_at": prev_closed,
    "prev_backlog_s": prev_backlog,
  }


def load_json(client: Any, key: str) -> dict[str, Any] | None:
  """
  GET and decode a JSON object; None when absent, malformed or on error.

  Args:
    client (Any): valkey-py client.
    key (str): Key.

  Returns:
    dict[str, Any] | None: Decoded object.

  Examples:
    >>> class _C:
    ...   def get(self, k):
    ...     return b'{"a": 1}'
    >>> load_json(_C(), "k")
    {'a': 1}
  """
  try:
    raw = client.get(key)
  except Exception:
    return None
  return decode_json_object(raw)


def decode_json_object(raw: Any) -> dict[str, Any] | None:
  """
  Decode one JSON object from ``str`` / ``bytes``; None when not an object.

  Args:
    raw (Any): Raw Valkey value or None.

  Returns:
    dict[str, Any] | None: Decoded object.

  Examples:
    >>> decode_json_object('{"a": 1}'), decode_json_object("[1]")
    ({'a': 1}, None)
  """
  if raw is None:
    return None
  if isinstance(raw, bytes):
    raw = raw.decode("utf-8", "replace")
  try:
    doc = json.loads(raw)
  except TypeError, ValueError:
    return None
  return doc if isinstance(doc, dict) else None


def _set_json(client: Any, key: str, doc: Mapping[str, Any], ttl: int) -> None:
  """
  SET a compact JSON object with a TTL.

  Args:
    client (Any): valkey-py client.
    key (str): Key.
    doc (Mapping[str, Any]): Object.
    ttl (int): Seconds.

  Returns:
    None

  Examples:
    >>> class _C:
    ...   def set(self, k, v, ex=None):
    ...     self.last = (k, v, ex)
    >>> c = _C()
    >>> _set_json(c, "k", {"a": 1}, 9)
    >>> c.last
    ('k', '{"a":1}', 9)
  """
  client.set(
    key,
    json.dumps(dict(doc), separators=(",", ":"), sort_keys=True, default=str),
    ex=max(1, int(ttl)),
  )


def scan_host_pairs(
  host: str, lo: datetime, hi: datetime, *, timeout_ms: int
) -> list[tuple[str, str, int, bool]]:
  """
  Run the per-host ``GROUP BY type, event`` over ``(lo, hi]``.

  Literal client-side parameters only: hosts or bounds from a CTE / JOIN
  lose plan-time chunk exclusion (hpcperfstats04 finding 5). Runs under
  ``SET LOCAL statement_timeout`` with parallel workers disabled.

  Args:
    host (str): ``host_data.host`` value.
    lo (datetime): Exclusive lower bound (never older than 7 days).
    hi (datetime): Inclusive upper bound (the host's newest sample).
    timeout_ms (int): Statement timeout.

  Returns:
    list[tuple[str, str, int, bool]]: ``(type, event, rows, has_nonzero)``.

  Examples:
    >>> scan_host_pairs(
    ...   "a.example.com", lo, hi, timeout_ms=60000
    ... )  # doctest: +SKIP
    [('host_cpu', 'user', 60, True)]
  """
  from django.db import connection, transaction

  using = getattr(connection, "alias", None) or "default"
  with transaction.atomic(using=using), connection.cursor() as cursor:
    cursor.execute("SET LOCAL statement_timeout = %s", [int(timeout_ms)])
    cursor.execute("SET LOCAL max_parallel_workers_per_gather = 0")
    cursor.execute(SCAN_SQL, [host, lo, hi])
    return [
      (str(t), str(e), int(n or 0), bool(nz))
      for t, e, n, nz in cursor.fetchall()
    ]


def run_sweep(
  *,
  client: Any,
  fqdns: Sequence[str],
  latest_fn: Callable[..., Mapping[str, Any]],
  scan_fn: Callable[..., Sequence[Sequence[Any]]],
  interval_s: int,
  window_minutes: int,
  timeout_ms: int,
  now_fn: Callable[[], float] = time.time,
  log_fn: Callable[[str], Any] = print,
) -> dict[str, Any]:
  """
  Run one coverage sweep and return its summary (also the heartbeat).

  Args:
    client (Any): valkey-py client (``get`` / ``set``, ``lrange``).
    fqdns (Sequence[str]): Inventory from ``recent_host:*``.
    latest_fn (Callable[..., Mapping[str, Any]]):
      ``latest_sample_time_by_host``; called once as
      ``latest_fn(fqdns, since=...)``.
    scan_fn (Callable[..., Sequence[Sequence[Any]]]): :func:`scan_host_pairs`
      signature ``(host, lo, hi, *, timeout_ms)``.
    interval_s (int): Sweep interval; the sweep stops at this budget.
    window_minutes (int): Per-host window length.
    timeout_ms (int): Per-host statement timeout.
    now_fn (Callable[[], float]): Clock (tests).
    log_fn (Callable[[str], Any]): Logger for the one summary line.

  Returns:
    dict[str, Any]: Heartbeat document with ``pid``, ``started_at``,
    ``finished_at``, ``interval_s``, ``window_minutes``, ``hosts_done``,
    ``hosts_total``, ``incomplete``, ``no_recent_data``, ``last_error``.

  Examples:
    >>> run_sweep(
    ...   client=c,
    ...   fqdns=[],
    ...   latest_fn=f,
    ...   scan_fn=s,
    ...   interval_s=3600,
    ...   window_minutes=60,
    ...   timeout_ms=60000,
    ... )["last_error"]  # doctest: +SKIP
    'empty recent_host inventory'
  """
  started = now_fn()
  ttl = 3 * int(interval_s)
  previous_beat = load_json(client, SWEEP_KEY) or {}
  beat: dict[str, Any] = {
    "pid": os.getpid(),
    "started_at": int(started),
    "finished_at": previous_beat.get("finished_at"),
    "interval_s": int(interval_s),
    "window_minutes": int(window_minutes),
    "hosts_done": 0,
    "hosts_total": len(fqdns),
    "incomplete": 0,
    "no_recent_data": 0,
    "last_error": None,
  }
  _set_json(client, SWEEP_KEY, beat, ttl)

  def _finish(error: str | None) -> dict[str, Any]:
    """
    Stamp ``finished_at`` (and an optional error) and write the heartbeat.

    Args:
      error (str | None): Sweep-level error, or None.

    Returns:
      dict[str, Any]: The final heartbeat.

    Examples:
      >>> _finish(None)  # doctest: +SKIP
    """
    if error is not None:
      beat["last_error"] = error
    beat["finished_at"] = int(now_fn())
    _set_json(client, SWEEP_KEY, beat, ttl)
    return beat

  if not fqdns:
    log_fn("ingest_coverage sweep: empty recent_host inventory; wrote nothing")
    return _finish("empty recent_host inventory")
  since = datetime.fromtimestamp(started, tz=UTC) - timedelta(
    days=RECENT_DATA_DAYS
  )
  try:
    latest = dict(latest_fn(list(fqdns), since=since))
  except Exception as exc:
    log_fn(f"ingest_coverage sweep: newest-sample probe failed: {exc}")
    return _finish(f"latest_sample_time_by_host: {exc}")
  fleet_newest = max(
    (epoch_seconds(t) for t in latest.values() if t is not None),
    default=None,
  )
  previous = {h: load_json(client, host_key(h)) or {} for h in fqdns}
  order = sorted(fqdns, key=lambda h: (previous[h].get("scanned_at") or 0, h))
  window = timedelta(minutes=int(window_minutes))
  deadline = started + int(interval_s)
  sigs_written: set[str] = set()
  for host in order:
    now = now_fn()
    if now >= deadline:
      break
    newest = latest.get(host)
    newest_s = epoch_seconds(newest)
    record: dict[str, Any] = {
      "fqdn": host,
      "scanned_at": int(now),
      "newest_sample": newest_s,
      "window_minutes": int(window_minutes),
      "lag_vs_fleet_s": (
        fleet_newest - newest_s
        if newest_s is not None and fleet_newest is not None
        else None
      ),
    }
    if newest is None:
      record["status"] = STATUS_NO_RECENT_DATA
      beat["no_recent_data"] += 1
    else:
      lo = max(newest - window, since)
      try:
        rows = scan_fn(host, lo, newest, timeout_ms=int(timeout_ms))
      except Exception as exc:
        record["status"] = STATUS_INCOMPLETE
        record["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        beat["incomplete"] += 1
        beat["last_error"] = record["error"]
      else:
        pairs = sorted({(str(r[0]), str(r[1])) for r in rows})
        sig = pair_set_signature(pairs)
        if sig not in sigs_written:
          _set_json(client, sig_key(sig), {"pairs": pairs}, ttl)
          sigs_written.add(sig)
        record["sig"] = sig
        record["pair_count"] = len(pairs)
        record["all_zero_pairs"] = sorted(
          [str(r[0]), str(r[1])] for r in rows if not r[3]
        )
        record.update(
          derive_host_status(
            load_rotation_entries(client, host),
            newest,
            previous[host],
            now,
          )
        )
    _set_json(client, host_key(host), record, ttl)
    beat["hosts_done"] += 1
    if beat["hosts_done"] % HEARTBEAT_EVERY_HOSTS == 0:
      _set_json(client, SWEEP_KEY, beat, ttl)
  _finish(None)
  log_fn(
    "ingest_coverage sweep: "
    f"hosts_done={beat['hosts_done']}/{beat['hosts_total']} "
    f"incomplete={beat['incomplete']} "
    f"no_recent_data={beat['no_recent_data']} "
    f"distinct_sigs={len(sigs_written)} "
    f"secs={beat['finished_at'] - beat['started_at']}"
  )
  return beat
