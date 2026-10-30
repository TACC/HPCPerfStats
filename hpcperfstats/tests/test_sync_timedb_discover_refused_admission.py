"""Discover never silently drops a file the ingest queue refused.

hpcperfstats04 2026-10-29: the boot snapshot restored 49,936 queued jobs and
the recent-days walk added 377 hot files past the 50k cap (hot is always
admitted). The whole-archive walk then evicted one younger job per older
file, which left the queue still over the cap, so ``zadd_ingest`` refused the
older file. Discover counted it as queued and set no frontier, so the ~313
oldest files (189 first-of-day files for 2026-08-25, one for 08-17) were
dropped, 08-25 sealed at 18:07 without them, and the 19:27 rediscover forced
a restore + append + reseal.

Now discover evicts until the older file fits, and any older file that still
ends up not queued (and is not dead-lettered) sets the frontier to its day so
day close holds that day until a later walk queues it.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from hpcperfstats.dbload.lib import (
  sync_timedb_job_discover as jd,
  sync_timedb_job_store as jq,
)
from hpcperfstats.dbload.lib.sync_timedb_append_day_lists import (
  reset_day_remainder_for_tests,
)
from hpcperfstats.dbload.lib.sync_timedb_job_store import SyncTimedbJobStore
from hpcperfstats.dbload.lib.sync_timedb_stats_find import FindStatsRecord

TODAY = date(2026, 10, 29)


def _epoch(day: date) -> int:
  return int(datetime(day.year, day.month, day.day, 12).timestamp())


def _rec(day: date, host: str = "h") -> FindStatsRecord:
  return FindStatsRecord(
    path=f"/archive/{host}/{_epoch(day)}",
    mtime=float(_epoch(day)),
    size=10,
    inode=1,
  )


def _plan(rec: FindStatsRecord) -> jd.jr.ClosedPathReconstructPlan:
  return jd.jr.ClosedPathReconstructPlan(
    path=rec.path,
    identity=rec.path,
    needs_ingest=True,
    needs_append=False,
    calendar_day=jd.calendar_day_from_find_record(rec, "/daily"),
    tar_path=None,
  )


def _queue(client: SyncTimedbJobStore, rec: FindStatsRecord) -> dict:
  return jd.jr.enqueue_reconstruct_jobs_for_closed_path(
    client, _plan(rec), today=TODAY, hot_days=3
  )


def _discover(client, records, **kw):
  return jd.stream_enqueue_ingest_from_find_records(
    client,
    records,
    tgz_archive_dir="/daily",
    today=TODAY,
    hot_days=3,
    ingest_is_complete_fn=lambda *_a, **_k: False,
    append_is_complete_fn=lambda *_a, **_k: True,
    **kw,
  )


@pytest.fixture
def cap2(monkeypatch):
  monkeypatch.setattr(jq, "queue_capacity_limit", lambda: 2)
  reset_day_remainder_for_tests()
  yield
  reset_day_remainder_for_tests()


@pytest.mark.usefixtures("cap2")
def test_older_file_is_admitted_when_hot_files_hold_the_queue_over_cap():
  client = SyncTimedbJobStore("")
  # Two younger backfill jobs fill the cap; a hot file then joins past it.
  _queue(client, _rec(date(2026, 10, 1), "y1"))
  _queue(client, _rec(date(2026, 10, 2), "y2"))
  _queue(client, _rec(TODAY, "t1"))
  assert client.queued_count(jq.JOB_KIND_INGEST) == 3
  old = _rec(date(2026, 8, 25), "o")
  stats = _discover(client, [old])
  assert client.ingest_score(old.path) is not None
  assert stats.enqueued_ingest == 1
  assert stats.frontier_day == date(2026, 10, 1)


@pytest.mark.usefixtures("cap2")
def test_refused_older_file_sets_the_frontier_to_its_day(monkeypatch):
  client = SyncTimedbJobStore("")
  old = _rec(date(2026, 8, 25), "o")
  # Room at the capacity check, refused at the add (a concurrent hot add).
  monkeypatch.setattr(client, "zadd_ingest", lambda *_a, **_k: 0)
  stats = _discover(client, [old])
  assert client.ingest_score(old.path) is None
  assert stats.frontier_day == date(2026, 8, 25)
  assert stats.dropped_at_capacity == 1
  assert stats.enqueued_ingest == 0


@pytest.mark.usefixtures("cap2")
def test_dead_lettered_file_does_not_set_the_frontier(monkeypatch):
  client = SyncTimedbJobStore("")
  monkeypatch.setattr(jq, "dead_letter_blocked", lambda *_a, **_k: True)
  stats = _discover(
    client, [_rec(date(2026, 8, 25), "o")], archive_data_dir="/a"
  )
  assert stats.frontier_day is None
  assert stats.dropped_at_capacity == 0


@pytest.mark.usefixtures("cap2")
def test_enqueue_reports_ingest_only_when_the_file_is_queued(monkeypatch):
  client = SyncTimedbJobStore("")
  monkeypatch.setattr(client, "zadd_ingest", lambda *_a, **_k: 0)
  out = _queue(client, _rec(date(2026, 8, 25), "o"))
  assert out == {"ingest": False, "append": False}


@pytest.mark.usefixtures("cap2")
def test_enqueue_reports_an_already_queued_file_as_queued():
  client = SyncTimedbJobStore("")
  rec = _rec(date(2026, 8, 25), "o")
  assert _queue(client, rec)["ingest"] is True
  assert _queue(client, rec)["ingest"] is True
