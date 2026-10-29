"""
Streaming discover -> ingest ZADD helpers for the greenfield orchestrator.

Library-only (slice 3): consume fd ``-X`` GNU stat NUL records (or any
:class:`FindStatsRecord` iterator) and enqueue ingest/append jobs as each
path arrives - without waiting for the scan iterator to exhaust. Skips
identities whose reconstruct complete predicates are already true. Not wired
into ``sync_timedb.py`` until the orchestrator cutover slice.

Attributes:
  StreamingDiscoverStats: Counters returned by stream enqueue.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Any

from hpcperfstats.dbload.lib import (
  conf_parser as cfg,
  sync_timedb_job_reconstruct as jr,
  sync_timedb_job_store as jq,
)
from hpcperfstats.dbload.lib.sync_timedb_append_day_lists import (
  note_day_remainder,
  remainder_day_for_identity,
)
from hpcperfstats.dbload.lib.sync_timedb_stats_find import (
  FindStatsRecord,
  _is_lock_name,
  is_internal_archive_stats_path,
  iter_find_printf_records_streaming,
)


@dataclass(frozen=True)
class StreamingDiscoverStats:
  """
  Counters for one streaming discover enqueue pass.

  Attributes:
    seen: Records observed from the find stream.
    enqueued_ingest: Paths that received an ingest ``ZADD``.
    enqueued_append: Paths that received an append ``RPUSH``.
    skipped_complete: Paths with neither ingest nor append needed.
    enqueued_day_close: Days that received a day_close ``RPUSH``.
    stopped_at_capacity: True when the ingest member cap was hit during
      the walk. Catchup ingest stops; the find walk and hot-band ZADD
      continue.
    dropped_at_capacity: Incomplete catchup files the walk skipped because
      the cap was hit (plus backfill records past the frontier, which are
      not classified). Nothing remembers them, so a non-zero count asks the
      orchestrator for another full walk once the queue drains.
    frontier_day: Oldest backfill day the walk could not fully queue, or
      None when every incomplete backfill file it saw is queued. Day close
      holds backfill days on or after it.
  """

  seen: int
  enqueued_ingest: int
  enqueued_append: int
  skipped_complete: int
  enqueued_day_close: int = 0
  stopped_at_capacity: bool = False
  dropped_at_capacity: int = 0
  frontier_day: date | None = None


def find_record_mtime_ns(mtime: float) -> int:
  """
  Convert discover epoch seconds to nanoseconds for ingest identity.

  Args:
    mtime (float): Epoch seconds from GNU ``stat %Y`` (integer) or parser float.

  Returns:
    int: Rounded nanosecond fingerprint.

  Examples:
    >>> find_record_mtime_ns(1.5)
    1500000000
  """
  return round(float(mtime) * 1_000_000_000)


def calendar_day_from_find_record(
  rec: FindStatsRecord,
  tgz_archive_dir: str,
) -> date | None:
  """
  Resolve a find record's calendar day from its basename.

  Accepts an ISO ``YYYY-MM-DD`` prefix or a listend digit-epoch basename.
  Returns ``None`` rather than substituting today or ``rec.mtime`` when the
  name cannot be parsed - banding without a real day would mis-schedule
  catchup work. ``tgz_archive_dir`` is kept for caller compatibility.

  Args:
    rec (FindStatsRecord): One GNU find record.
    tgz_archive_dir (str): Daily archive directory (unused for day identity).

  Returns:
    date | None: Calendar day, or ``None`` when unresolved.

  Examples:
    >>> calendar_day_from_find_record(
    ...   FindStatsRecord(path="/x", mtime=1.0, size=1, inode=1),
    ...   "/nope",
    ... ) is None
    True
  """
  _ = tgz_archive_dir
  return _basename_date(rec.path)


def filter_find_records_for_date_range(
  records: Iterable[FindStatsRecord],
  *,
  startdate: Any = None,
  enddate: Any = None,
) -> Iterator[FindStatsRecord]:
  """
  Keep find records whose basename date falls in ``[startdate, enddate]``.

  Yields one record at a time so GNU find is not materialized when the
  caller streams. When both bounds are ``None``, records pass through.

  Args:
    records (Iterable[FindStatsRecord]): Streaming or materialized records.
    startdate (Any): Inclusive start ``datetime``/``date``, or ``None``.
    enddate (Any): Inclusive end ``datetime``/``date``, or ``None``.

  Yields:
    FindStatsRecord: Records that pass the date window.

  Examples:
    >>> list(
    ...   filter_find_records_for_date_range([], startdate=None, enddate=None)
    ... )
    []
  """
  start_d = _coerce_filter_date(startdate)
  end_d = _coerce_filter_date(enddate)
  if start_d is None and end_d is None:
    yield from records
    return
  for rec in records:
    rec_day = _basename_date(rec.path)
    if rec_day is None:
      continue
    if start_d is not None and rec_day < start_d:
      continue
    if end_d is not None and rec_day > end_d:
      continue
    yield rec


def _coerce_filter_date(value: Any) -> date | None:
  """
  Coerce a CLI date bound to ``date`` or ``None``.

  Args:
    value (Any): ``date``, ``datetime``, or ignored sentinel.

  Returns:
    date | None: Calendar day, or ``None`` when the bound is unset.

  Examples:
    >>> _coerce_filter_date(None) is None
    True
  """
  if isinstance(value, datetime):
    return value.date()
  if isinstance(value, date):
    return value
  return None


def _basename_date(path: str) -> date | None:
  """
  Parse a calendar day from a stats-file basename.

  Accepts an ISO ``YYYY-MM-DD`` prefix or a listend digit-epoch name
  (unix seconds ``>= 1e9``). Other names stay unresolved.

  Args:
    path (str): Raw stats path.

  Returns:
    date | None: Parsed day, or ``None``.

  Examples:
    >>> _basename_date("/a/2026-08-05T00:00:00")
    datetime.date(2026, 8, 5)
  """
  name = os.path.basename(str(path or ""))
  if name.endswith(".json"):
    return None
  if len(name) >= 10:
    try:
      return datetime.strptime(name[:10], "%Y-%m-%d").date()
    except ValueError:
      pass
  if not name.isdigit():
    return None
  try:
    epoch = int(name)
  except ValueError:
    return None
  if epoch < 1_000_000_000:
    return None
  try:
    return datetime.fromtimestamp(epoch).date()
  except OSError, OverflowError, ValueError:
    return None


def discover_ingest_capacity_limit() -> int:
  """
  Return the tighter of the job-queue member cap and ingest queue max size.

  Args:
    None.

  Returns:
    int: Positive capacity used to stop streaming discover.

  Examples:
    >>> discover_ingest_capacity_limit() >= 1
    True
  """
  try:
    ingest_cap = int(cfg.get_sync_ingest_queue_max_size())
  except Exception:
    ingest_cap = 50000
  return min(jq.queue_capacity_limit(), max(1, ingest_cap))


def iter_find_records_from_stdout_chunks(
  chunks: Iterable[bytes],
) -> Iterator[FindStatsRecord]:
  """
  Parse find ``-printf`` stdout chunks into records as they complete.

  Thin wrapper around :func:`iter_find_printf_records_streaming` for the
  discover enqueue path.

  Args:
    chunks (Iterable[bytes]): Successive stdout chunks from GNU find.

  Yields:
    FindStatsRecord: Complete path/mtime/size/inode records.

  Examples:
    >>> list(
    ...   iter_find_records_from_stdout_chunks([b"/x\\x001.0\\x001\\x002\\x00"])
    ... )[0].path
    '/x'
  """
  yield from iter_find_printf_records_streaming(chunks)


def stream_enqueue_ingest_from_find_records(
  client: Any,
  records: Iterable[FindStatsRecord],
  *,
  tgz_archive_dir: str,
  today: date,
  hot_days: int = jr.DEFAULT_INGEST_HOT_DAYS,
  archive_data_dir: str | None = None,
  listend_enabled: bool | None = None,
  calendar_day_fn: Callable[[FindStatsRecord], date | None] | None = None,
  ingest_is_complete_fn: Callable[..., bool] | None = None,
  append_is_complete_fn: Callable[..., bool] | None = None,
  startdate: Any = None,
  enddate: Any = None,
) -> StreamingDiscoverStats:
  """
  Classify and enqueue jobs for each find record as it arrives.

  Does **not** wait for ``records`` to exhaust before the first ``ZADD``.
  Already-complete identities are skipped (residual-gap reconstruct-skip).

  At the ingest cap an incomplete backfill file pushes out the youngest
  queued backfill file that is younger than it; when nothing queued is
  younger its day becomes the frontier, and later backfill records on or
  after the frontier are counted as dropped without being classified.
  With oldest-first input (``sort_oldest_first`` walks) the queue ends up
  holding the oldest incomplete backfill files and every backfill day
  before the frontier is fully queued.

  Args:
    client (Any): job store with ``zadd`` / ``rpush``.
    records (Iterable[FindStatsRecord]): Streaming find records (generator
      OK).
    tgz_archive_dir (str): Daily archive directory for append classify.
    today (date): Local today for hot/catchup score encode.
    hot_days (int): Hot-band window length.
    archive_data_dir (str | None): Archive root for mark helpers.
    listend_enabled (bool | None): Live listend override for ingest complete.
    calendar_day_fn (Callable[[FindStatsRecord], date | None] | None):
      Optional day resolver per record.
    ingest_is_complete_fn (Callable[..., bool] | None): Injectable ingest
      complete predicate.
    append_is_complete_fn (Callable[..., bool] | None): Injectable append
      complete predicate.
    startdate (Any): Inclusive CLI start date, or ``None``.
    enddate (Any): Inclusive CLI end date, or ``None``.

  Returns:
    StreamingDiscoverStats: Seen / enqueued / skipped counters and the
    frontier day.

  Examples:
    >>> class _C:
    ...   def __init__(self):
    ...     self.z = {}
    ...     self.l = {}
    ...
    ...   def zadd(self, key, mapping):
    ...     self.z.update(mapping)
    ...     return 1
    ...
    ...   def zscore(self, key, member):
    ...     return self.z.get(member)
    ...
    ...   def zcard(self, key):
    ...     return len(self.z)
    ...
    ...   def hset(self, *a, **k):
    ...     return 1
    ...
    ...   def rpush(self, key, *vals):
    ...     self.l.setdefault(key, []).extend(vals)
    ...     return len(vals)
    >>> stats = stream_enqueue_ingest_from_find_records(
    ...   _C(),
    ...   [FindStatsRecord(path="/a", mtime=1.0, size=10, inode=1)],
    ...   tgz_archive_dir="/daily",
    ...   today=date(2026, 8, 24),
    ...   calendar_day_fn=lambda rec: date(2026, 8, 20),
    ...   ingest_is_complete_fn=lambda **k: False,
    ...   append_is_complete_fn=lambda **k: True,
    ... )
    >>> stats.enqueued_ingest
    1
  """
  seen = 0
  enqueued_ingest = 0
  enqueued_append = 0
  enqueued_day_close = 0
  skipped_complete = 0
  day_close_seen: set[str] = set()
  stopped_at_capacity = False
  dropped_at_capacity = 0
  frontier: date | None = None
  evicted: list[str] = []
  day_fn = calendar_day_fn or (
    lambda rec: calendar_day_from_find_record(rec, tgz_archive_dir)
  )
  cap = discover_ingest_capacity_limit()
  if startdate is not None or enddate is not None:
    records = filter_find_records_for_date_range(
      records,
      startdate=startdate,
      enddate=enddate,
    )

  from hpcperfstats.dbload.lib import sync_timedb_progress_report as progress

  for rec in records:
    if is_internal_archive_stats_path(rec.path):
      continue
    if _is_lock_name(os.path.basename(rec.path)):
      continue
    try:
      has_cap = jq.queue_has_capacity(
        client,
        kind=jq.JOB_KIND_INGEST,
        limit=cap,
      )
    except Exception:
      has_cap = True
    if not has_cap:
      stopped_at_capacity = True
    seen += 1
    cal = day_fn(rec) if day_fn is not None else None
    day_tok = None
    if cal is None:
      progress.record(None, "unresolved_day", 1)
    else:
      try:
        day_tok = cal.isoformat()
      except Exception:
        day_tok = None
        progress.record(None, "unresolved_day", 1)
    backfill = (
      cal is not None
      and jr.select_ingest_band(cal, today=today, hot_days=hot_days) != "hot"
    )
    # Latched for the walk: room freed by ingest mid-walk must not admit
    # younger days ahead of the dropped frontier day.
    if backfill and frontier is not None and cal >= frontier:
      dropped_at_capacity += 1  # older work fills the queue; skip classify
      continue
    plan = jr.classify_closed_raw_path(
      rec.path,
      tgz_archive_dir=tgz_archive_dir,
      size=int(rec.size),
      mtime_ns=find_record_mtime_ns(rec.mtime),
      calendar_day=cal,
      archive_data_dir=archive_data_dir,
      listend_enabled=listend_enabled,
      ingest_is_complete_fn=ingest_is_complete_fn,
      append_is_complete_fn=append_is_complete_fn,
    )
    if (
      backfill
      and not has_cap
      and plan.needs_ingest
      and not _ingest_queued(client, plan.identity)
    ):
      gone = _evict_younger(client, cal)
      if gone is None:
        frontier = cal if frontier is None else min(frontier, cal)
        dropped_at_capacity += 1
        plan = replace(plan, needs_ingest=False, needs_append=False)
      else:
        evicted.append(gone)
    enqueued = jr.enqueue_reconstruct_jobs_for_closed_path(
      client,
      plan,
      today=today,
      hot_days=hot_days,
      archive_data_dir=archive_data_dir,
    )
    if enqueued.get("ingest"):
      enqueued_ingest += 1
    if enqueued.get("append"):
      enqueued_append += 1
    if enqueued.get("ingest") or enqueued.get("append"):
      progress.record(day_tok, "discover", 1)
    else:
      skipped_complete += 1
      progress.record(day_tok, "skip_complete", 1)
    tar = str(plan.tar_path or "").strip()
    if tar and tar not in day_close_seen:
      if jr.enqueue_cheap_day_close_if_needed(
        client,
        tar,
        calendar_day=plan.calendar_day,
      ):
        day_close_seen.add(tar)
        enqueued_day_close += 1

  # A pushed-out file the walk did not queue again leaves its day open.
  for gone in evicted:
    gone_day = _basename_date(gone.rsplit("|", 2)[0])
    if gone_day is not None and not _ingest_queued(client, gone):
      frontier = gone_day if frontier is None else min(frontier, gone_day)
  return StreamingDiscoverStats(
    seen=seen,
    enqueued_ingest=enqueued_ingest,
    enqueued_append=enqueued_append,
    skipped_complete=skipped_complete,
    enqueued_day_close=enqueued_day_close,
    stopped_at_capacity=stopped_at_capacity,
    dropped_at_capacity=dropped_at_capacity,
    frontier_day=frontier,
  )


def _ingest_queued(client: Any, identity: str) -> bool:
  """
  Return True when an ingest identity is queued or claimed in the store.

  Args:
    client (Any): Job store; a client without ``ingest_score`` (test fakes)
      reports nothing queued.
    identity (str): Ingest identity.

  Returns:
    bool: True when queued or in flight.

  Examples:
    >>> _ingest_queued(jq.SyncTimedbJobStore(""), "/a")
    False
  """
  score_fn = getattr(client, "ingest_score", None)
  inflight_fn = getattr(client, "inflight_contains", None)
  return bool(
    (callable(score_fn) and score_fn(identity) is not None)
    or (callable(inflight_fn) and inflight_fn(jq.JOB_KIND_INGEST, identity))
  )


def _evict_younger(client: Any, day: date) -> str | None:
  """
  Push the youngest queued backfill file younger than ``day`` out of the queue.

  Releases the pushed-out file from its day's in-memory remainder count
  (DE-32), the counterpart of the enqueue increment.

  Args:
    client (Any): Job store; a client without
      ``evict_youngest_catchup_ingest`` (test fakes) never evicts.
    day (date): Calendar day of the file waiting for room.

  Returns:
    str | None: Removed identity, or None when nothing queued is younger.

  Examples:
    >>> _evict_younger(jq.SyncTimedbJobStore(""), date(2026, 8, 1)) is None
    True
  """
  evict_fn = getattr(client, "evict_youngest_catchup_ingest", None)
  gone = evict_fn(day) if callable(evict_fn) else None
  remainder_day = remainder_day_for_identity(gone) if gone else None
  if remainder_day:
    note_day_remainder(jq.JOB_KIND_INGEST, remainder_day, -1)
  return gone


def stream_enqueue_ingest_from_find_stdout_chunks(
  client: Any,
  chunks: Iterable[bytes],
  *,
  tgz_archive_dir: str,
  today: date,
  hot_days: int = jr.DEFAULT_INGEST_HOT_DAYS,
  archive_data_dir: str | None = None,
  listend_enabled: bool | None = None,
  calendar_day_fn: Callable[[FindStatsRecord], date | None] | None = None,
  ingest_is_complete_fn: Callable[..., bool] | None = None,
  append_is_complete_fn: Callable[..., bool] | None = None,
  startdate: Any = None,
  enddate: Any = None,
) -> StreamingDiscoverStats:
  """
  Parse find stdout chunks and enqueue ingest jobs as records complete.

  Args:
    client (Any): job store with ``zadd`` / ``rpush``.
    chunks (Iterable[bytes]): GNU find ``-printf`` stdout chunks.
    tgz_archive_dir (str): Daily archive directory.
    today (date): Local today for score encode.
    hot_days (int): Hot-band window length.
    archive_data_dir (str | None): Archive root for mark helpers.
    listend_enabled (bool | None): Live listend override.
    calendar_day_fn (Callable[[FindStatsRecord], date | None] | None):
      Optional day resolver.
    ingest_is_complete_fn (Callable[..., bool] | None): Injectable ingest
      complete predicate.
    append_is_complete_fn (Callable[..., bool] | None): Injectable append
      complete predicate.
    startdate (Any): Inclusive CLI start date, or ``None``.
    enddate (Any): Inclusive CLI end date, or ``None``.

  Returns:
    StreamingDiscoverStats: Seen / enqueued / skipped counters.

  Examples:
    >>> class _C:
    ...   def __init__(self):
    ...     self.z = {}
    ...     self.l = {}
    ...
    ...   def zadd(self, key, mapping):
    ...     self.z.update(mapping)
    ...     return 1
    ...
    ...   def zscore(self, key, member):
    ...     return self.z.get(member)
    ...
    ...   def zcard(self, key):
    ...     return len(self.z)
    ...
    ...   def hset(self, *a, **k):
    ...     return 1
    ...
    ...   def rpush(self, key, *vals):
    ...     self.l.setdefault(key, []).extend(vals)
    ...     return len(vals)
    >>> raw = b"/a\\x001.0\\x0010\\x001\\x00"
    >>> stream_enqueue_ingest_from_find_stdout_chunks(
    ...   _C(),
    ...   [raw],
    ...   tgz_archive_dir="/daily",
    ...   today=date(2026, 8, 24),
    ...   calendar_day_fn=lambda rec: date(2026, 8, 20),
    ...   ingest_is_complete_fn=lambda **k: False,
    ...   append_is_complete_fn=lambda **k: True,
    ... ).seen
    1
  """
  return stream_enqueue_ingest_from_find_records(
    client,
    iter_find_records_from_stdout_chunks(chunks),
    tgz_archive_dir=tgz_archive_dir,
    today=today,
    hot_days=hot_days,
    archive_data_dir=archive_data_dir,
    listend_enabled=listend_enabled,
    calendar_day_fn=calendar_day_fn,
    ingest_is_complete_fn=ingest_is_complete_fn,
    append_is_complete_fn=append_is_complete_fn,
    startdate=startdate,
    enddate=enddate,
  )
