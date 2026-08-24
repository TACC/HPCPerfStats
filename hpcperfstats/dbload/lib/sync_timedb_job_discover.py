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
from datetime import date

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
  """

  seen: int
  enqueued_ingest: int
  enqueued_append: int
  skipped_complete: int
  enqueued_day_close: int = 0


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

  Returns:
    StreamingDiscoverStats: Seen / enqueued / skipped counters.

  Examples:
    >>> class _C:
    ...   def __init__(self):
    ...     self.z = {}; self.l = {}
    ...   def zadd(self, key, mapping):
    ...     self.z.update(mapping); return 1
    ...   def rpush(self, key, *vals):
    ...     self.l.setdefault(key, []).extend(vals); return len(vals)
    >>> stats = stream_enqueue_ingest_from_find_records(
    ...   _C(),
    ...   [FindStatsRecord(path="/a", mtime=1.0, size=10, inode=1)],
    ...   tgz_archive_dir="/daily",
    ...   today=date(2026, 8, 24),
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
  day_fn = calendar_day_fn
  day_close_seen: set[str] = set()

  for rec in records:
    seen += 1
    cal = day_fn(rec) if day_fn is not None else None
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
    )
    if enqueued.get("ingest"):
      enqueued_ingest += 1
    if enqueued.get("append"):
      enqueued_append += 1
    if not enqueued.get("ingest") and not enqueued.get("append"):
      skipped_complete += 1
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

  Returns:
    StreamingDiscoverStats: Seen / enqueued / skipped counters.

  Examples:
    >>> class _C:
    ...   def __init__(self):
    ...     self.z = {}; self.l = {}
    ...   def zadd(self, key, mapping):
    ...     self.z.update(mapping); return 1
    ...   def rpush(self, key, *vals):
    ...     self.l.setdefault(key, []).extend(vals); return len(vals)
    >>> raw = b"/a\\x001.0\\x0010\\x001\\x00"
    >>> stream_enqueue_ingest_from_find_stdout_chunks(
    ...   _C(),
    ...   [raw],
    ...   tgz_archive_dir="/daily",
    ...   today=date(2026, 8, 24),
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
  )
