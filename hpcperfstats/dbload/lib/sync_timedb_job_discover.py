"""
Streaming discover → ingest ZADD helpers for the greenfield orchestrator.

Library-only (slice 3): consume GNU find ``-printf`` records (or any
:class:`FindStatsRecord` iterator) and enqueue ingest/append jobs as each
path arrives — without waiting for the scan iterator to exhaust. Skips
identities whose reconstruct complete predicates are already true. Not wired
into ``sync_timedb.py`` until the orchestrator cutover slice.

Attributes:
  StreamingDiscoverStats: Counters returned by stream enqueue.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable, Iterator

from dataclasses import dataclass
from datetime import date, datetime
import os

from hpcperfstats.dbload.lib import conf_parser as cfg
from hpcperfstats.dbload.lib import sync_timedb_job_queue as jq
from hpcperfstats.dbload.lib import sync_timedb_job_reconstruct as jr
from hpcperfstats.dbload.lib.sync_timedb_stats_find import (
    FindStatsRecord,
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
    stopped_at_capacity: True when enqueue stopped because the ingest queue
      hit its configured member cap.
  """

  seen: int
  enqueued_ingest: int
  enqueued_append: int
  skipped_complete: int
  enqueued_day_close: int = 0
  stopped_at_capacity: bool = False


def find_record_mtime_ns(mtime: float) -> int:
  """
  Convert find ``%T@`` epoch seconds to nanoseconds for ingest identity.

  Args:
    mtime (float): Epoch seconds from GNU find ``%T@``.

  Returns:
    int: Rounded nanosecond fingerprint.

  Examples:
    >>> find_record_mtime_ns(1.5)
    1500000000
  """
  return int(round(float(mtime) * 1_000_000_000))


def calendar_day_from_find_record(
  rec: FindStatsRecord,
  tgz_archive_dir: str,
) -> date | None:
  """
  Resolve a find record's calendar day from its daily tar path.

  Returns ``None`` rather than substituting today when the tar day cannot be
  derived — banding without a real day would mis-schedule catchup work.

  Args:
    rec (FindStatsRecord): One GNU find record.
    tgz_archive_dir (str): Daily archive directory.

  Returns:
    date | None: Calendar day, or ``None`` when unresolved.

  Examples:
    >>> calendar_day_from_find_record(
    ...   FindStatsRecord(path="/x", mtime=1.0, size=1, inode=1),
    ...   "/nope",
    ... ) is None
    True
  """
  from hpcperfstats.dbload.lib.sync_timedb_archive_helpers import (
      calendar_date_from_daily_tar_path,
      daily_tar_path_for_stats_path,
  )

  if _basename_date(rec.path) is None:
    return None
  tar = daily_tar_path_for_stats_path(
      rec.path, tgz_archive_dir, rec.mtime,
  )
  if not tar:
    return None
  return calendar_date_from_daily_tar_path(tar)


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
    >>> list(filter_find_records_for_date_range([], startdate=None, enddate=None))
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
  Parse ``YYYY-MM-DD`` from a stats-file basename prefix.

  Args:
    path (str): Raw stats path.

  Returns:
    date | None: Parsed day, or ``None``.

  Examples:
    >>> _basename_date("/a/2026-08-05T00:00:00")
    datetime.date(2026, 8, 5)
  """
  name = os.path.basename(str(path or ""))
  if len(name) < 10:
    return None
  try:
    return datetime.strptime(name[:10], "%Y-%m-%d").date()
  except ValueError:
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
    ingest_cap = 3000
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
    ...   iter_find_records_from_stdout_chunks(
    ...     [b"/x\\x001.0\\x001\\x002\\x00"]
    ...   )
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

  Args:
    client (Any): Redis client with ``zadd`` / ``rpush``.
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
    StreamingDiscoverStats: Seen / enqueued / skipped counters.

  Examples:
    >>> class _C:
    ...   def __init__(self):
    ...     self.z = {}; self.l = {}
    ...   def zadd(self, key, mapping):
    ...     self.z.update(mapping); return 1
    ...   def zscore(self, key, member):
    ...     return self.z.get(member)
    ...   def zcard(self, key):
    ...     return len(self.z)
    ...   def hset(self, *a, **k):
    ...     return 1
    ...   def rpush(self, key, *vals):
    ...     self.l.setdefault(key, []).extend(vals); return len(vals)
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
  day_fn = calendar_day_fn or (
      lambda rec: calendar_day_from_find_record(rec, tgz_archive_dir)
  )
  cap = discover_ingest_capacity_limit()
  if startdate is not None or enddate is not None:
    records = filter_find_records_for_date_range(
        records, startdate=startdate, enddate=enddate,
    )

  from hpcperfstats.dbload.lib import sync_timedb_progress_report as progress

  for rec in records:
    try:
      has_cap = jq.queue_has_capacity(
          client, kind=jq.JOB_KIND_INGEST, limit=cap,
      )
    except Exception:
      has_cap = True
    if not has_cap:
      stopped_at_capacity = True
      break
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
      if jr.enqueue_day_close_if_needed(
          client,
          tar,
          calendar_day=plan.calendar_day,
      ):
        day_close_seen.add(tar)
        enqueued_day_close += 1

  return StreamingDiscoverStats(
      seen=seen,
      enqueued_ingest=enqueued_ingest,
      enqueued_append=enqueued_append,
      skipped_complete=skipped_complete,
      enqueued_day_close=enqueued_day_close,
      stopped_at_capacity=stopped_at_capacity,
  )


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
    client (Any): Redis client with ``zadd`` / ``rpush``.
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
    ...     self.z = {}; self.l = {}
    ...   def zadd(self, key, mapping):
    ...     self.z.update(mapping); return 1
    ...   def zscore(self, key, member):
    ...     return self.z.get(member)
    ...   def zcard(self, key):
    ...     return len(self.z)
    ...   def hset(self, *a, **k):
    ...     return 1
    ...   def rpush(self, key, *vals):
    ...     self.l.setdefault(key, []).extend(vals); return len(vals)
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
