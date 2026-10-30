"""Discover hands the ingest queue the oldest backfill files; day close waits.

hpcperfstats04 2026-10-29: the whole-archive walk streamed fd output in
host-directory order, so the 50k-file ingest cap filled with files from every
day between 2026-08-16 and today. Each touched day was queued for day close,
restored its sealed tar, appended, resealed and deleted raw, then the next walk
found more files for the same days and reopened them (56 days in day close at
once, every batch). The walk itself is 2 s; the per-file classify is the slow
part.

Now the walker output is sorted oldest first by GNU ``sort`` (filename epoch),
discover fills the free queue room oldest first (an older file pushes out the
youngest queued backfill file), stops classifying backfill at the first day it
could not fully queue (the discover frontier), and day close holds backfill
days at or after that frontier until a later walk queues them.
"""

from __future__ import annotations

import contextlib
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime

import pytest

from hpcperfstats.dbload.lib import (
  sync_timedb_job_discover as jd,
  sync_timedb_job_store as jq,
  sync_timedb_queue_orchestrator as qo,
  sync_timedb_stats_find as sf,
)
from hpcperfstats.dbload.lib.sync_timedb_append_day_lists import (
  day_remainder,
  reset_day_remainder_for_tests,
)
from hpcperfstats.dbload.lib.sync_timedb_job_store import SyncTimedbJobStore
from hpcperfstats.dbload.lib.sync_timedb_stats_find import FindStatsRecord

TODAY = date(2026, 10, 29)


def _epoch(day: date) -> int:
  return int(datetime(day.year, day.month, day.day, 12).timestamp())


def _rec(day: date, host: str = "h", inode: int = 1) -> FindStatsRecord:
  return FindStatsRecord(
    path=f"/archive/{host}/{_epoch(day)}",
    mtime=float(_epoch(day)),
    size=10,
    inode=inode,
  )


def _queue(client: SyncTimedbJobStore, rec: FindStatsRecord) -> None:
  plan = jd.jr.ClosedPathReconstructPlan(
    path=rec.path,
    identity=rec.path,
    needs_ingest=True,
    needs_append=False,
    calendar_day=jd.calendar_day_from_find_record(rec, "/daily"),
    tar_path=None,
  )
  jd.jr.enqueue_reconstruct_jobs_for_closed_path(
    client, plan, today=TODAY, hot_days=3
  )


def _discover(client, records, calls=None):
  def _incomplete(*_a, **_k):
    if calls is not None:
      calls.append(1)
    return False

  return jd.stream_enqueue_ingest_from_find_records(
    client,
    records,
    tgz_archive_dir="/daily",
    today=TODAY,
    hot_days=3,
    ingest_is_complete_fn=_incomplete,
    append_is_complete_fn=lambda *_a, **_k: True,
  )


@pytest.fixture
def cap2(monkeypatch):
  monkeypatch.setattr(jq, "queue_capacity_limit", lambda: 2)
  reset_day_remainder_for_tests()
  yield
  reset_day_remainder_for_tests()


def _queued_days(client: SyncTimedbJobStore) -> list[date]:
  return sorted(
    jd.calendar_day_from_find_record(
      FindStatsRecord(path=i, mtime=0.0, size=0, inode=0), "/daily"
    )
    for i in client.ingest_identities()
  )


# --- walker: 16 threads on the whole archive, GNU sort oldest first ---------


def _threads(argv: list[str]) -> str:
  return argv[argv.index("--threads") + 1]


def test_whole_archive_walk_uses_16_walker_threads():
  assert _threads(sf.build_find_stats_argv("/a", mtime_days=None)) == "16"
  assert _threads(sf.build_find_stats_argv("/a", mtime_days=1)) == "4"


def _fake_walker(tmp_path, body: str) -> str:
  script = tmp_path / "fake_fd"
  script.write_text("#!/bin/sh\n" + body)
  script.chmod(script.stat().st_mode | stat.S_IEXEC)
  return str(script)


def test_sorted_walk_streams_oldest_filename_epoch_first(tmp_path):
  archive = tmp_path / "archive"
  archive.mkdir()
  root = str(archive)
  rows = [
    (f"{root}/hB/1787000300", 7, 30, 3),
    (f"{root}/hA/1787000100", 5, 10, 1),
    (f"{root}/hC/1787000200", 6, 20, 2),
  ]
  body = "".join(
    f"printf '%s\\t%s\\t%s\\t%s\\0' '{p}' {m} {s} {i}\n" for p, m, s, i in rows
  )
  chunks = sf.iter_find_stats_stdout_chunks(
    root, find_bin=_fake_walker(tmp_path, body), sort_oldest_first=True
  )
  recs = list(sf.iter_find_printf_records_streaming(chunks))
  assert [os.path.basename(r.path) for r in recs] == [
    "1787000100",
    "1787000200",
    "1787000300",
  ]
  assert (recs[0].mtime, recs[0].size, recs[0].inode) == (5.0, 10, 1)


def test_sorted_walk_fails_closed_when_the_walker_fails(tmp_path):
  archive = tmp_path / "archive"
  archive.mkdir()
  walker = _fake_walker(tmp_path, "echo boom >&2\nexit 2\n")
  with pytest.raises(sf.FindStatsDiscoveryError):
    list(
      sf.iter_find_stats_stdout_chunks(
        str(archive), find_bin=walker, sort_oldest_first=True
      )
    )


def test_boot_stream_discover_sorts_the_walk(monkeypatch):
  seen = {}

  def _chunks(_archive_dir, **kw):
    seen.update(kw)
    return iter(())

  monkeypatch.setattr(qo, "iter_find_stats_stdout_chunks", _chunks)
  monkeypatch.setattr(
    qo, "_enqueue_day_closes_for_daily_dir", lambda *a, **k: 0
  )
  qo._boot_stream_discover(
    SyncTimedbJobStore(""),
    "/archive",
    tgz_archive_dir="/daily",
    log_fn=lambda *a, **k: None,
    mtime_days=None,
  )
  assert seen.get("sort_oldest_first") is True


# --- discover: oldest first into the free room, then a frontier -------------


@pytest.mark.usefixtures("cap2")
def test_discover_fills_free_room_oldest_first_then_sets_frontier():
  client = SyncTimedbJobStore("")
  days = [date(2026, 8, d) for d in (16, 17, 18, 19)]
  calls = []
  records = [_rec(d, inode=i) for i, d in enumerate(days)]
  stats = _discover(client, records, calls)
  assert _queued_days(client) == days[:2]
  assert stats.frontier_day == days[2]
  # Nothing past the frontier is classified (the walk's slow part).
  assert len(calls) == 3
  assert stats.dropped_at_capacity == 2


@pytest.mark.usefixtures("cap2")
def test_frontier_stays_latched_when_ingest_frees_room_mid_walk():
  client = SyncTimedbJobStore("")

  def _records():
    yield from (_rec(date(2026, 8, d), inode=d) for d in (16, 17, 18))
    # An ingest worker claims a file while the walk is still running.
    assert client.claim_ingest(band="catchup", owner_token="worker")
    yield _rec(date(2026, 8, 19), inode=19)

  stats = _discover(client, _records())
  assert stats.frontier_day == date(2026, 8, 18)
  assert date(2026, 8, 19) not in _queued_days(client)


@pytest.mark.usefixtures("cap2")
def test_discover_still_admits_hot_files_past_the_frontier():
  client = SyncTimedbJobStore("")
  records = [_rec(date(2026, 8, d), inode=d) for d in (16, 17, 18)]
  records.append(_rec(TODAY, inode=99))
  stats = _discover(client, records)
  assert stats.frontier_day == date(2026, 8, 18)
  assert TODAY in _queued_days(client)


@pytest.mark.usefixtures("cap2")
def test_older_file_pushes_out_the_youngest_queued_backfill():
  client = SyncTimedbJobStore("")
  young = [_rec(date(2026, 10, 1), "y1"), _rec(date(2026, 10, 2), "y2")]
  for rec in young:
    _queue(client, rec)
  assert day_remainder("ingest", "2026-10-02") == 1
  # The walk is sorted, so it reaches the pushed-out file again later.
  stats = _discover(client, [_rec(date(2026, 8, 16), "o"), *young])
  assert _queued_days(client) == [date(2026, 8, 16), date(2026, 10, 1)]
  assert day_remainder("ingest", "2026-10-02") == 0
  assert stats.frontier_day == date(2026, 10, 2)


@pytest.mark.usefixtures("cap2")
def test_pushed_out_file_the_walk_never_reaches_again_holds_its_day():
  client = SyncTimedbJobStore("")
  for rec in (_rec(date(2026, 10, 1), "y1"), _rec(date(2026, 10, 2), "y2")):
    _queue(client, rec)
  stats = _discover(client, [_rec(date(2026, 8, 16), "o")])
  assert stats.frontier_day == date(2026, 10, 2)


@pytest.mark.usefixtures("cap2")
def test_backlog_reloaded_from_the_snapshot_can_be_pushed_out(tmp_path):
  # The random backlog from the old walks survives a restart in the snapshot.
  first = SyncTimedbJobStore(str(tmp_path))
  for rec in (_rec(date(2026, 10, 1), "y1"), _rec(date(2026, 10, 2), "y2")):
    _queue(first, rec)
  first.persist(force=True)
  first.close()
  reloaded = SyncTimedbJobStore(str(tmp_path))
  try:
    gone = reloaded.evict_youngest_catchup_ingest(date(2026, 8, 16))
    assert gone == _rec(date(2026, 10, 2), "y2").path
  finally:
    reloaded.close()


@pytest.mark.usefixtures("cap2")
def test_hot_queued_files_are_never_pushed_out():
  client = SyncTimedbJobStore("")
  for host in ("t1", "t2"):
    _queue(client, _rec(TODAY, host))
  stats = _discover(client, [_rec(date(2026, 8, 16), "o")])
  assert _queued_days(client) == [TODAY, TODAY]
  assert stats.frontier_day == date(2026, 8, 16)


def test_discover_with_room_for_everything_sets_no_frontier(monkeypatch):
  monkeypatch.setattr(jq, "queue_capacity_limit", lambda: 100)
  records = [_rec(date(2026, 8, d), inode=d) for d in (16, 17)]
  assert _discover(SyncTimedbJobStore(""), records).frontier_day is None


# --- orchestrator: the frontier follows whole walks; day close holds --------


@pytest.fixture
def frontier():
  qo._set_discover_frontier(None)
  qo._BOOT_FULL_DISCOVER_PENDING.clear()
  qo._FULL_REDISCOVER_PENDING.clear()
  yield
  qo._set_discover_frontier(None)
  qo._BOOT_FULL_DISCOVER_PENDING.clear()
  qo._FULL_REDISCOVER_PENDING.clear()


def _bg_walk(monkeypatch, *, mtime_days, frontier_day=None, raises=False):
  client = SyncTimedbJobStore("")
  jq.enqueue_list_job(
    client, kind=jq.JOB_KIND_DISCOVER, identity="walk", dedupe=True
  )
  stats = jd.StreamingDiscoverStats(
    seen=1,
    enqueued_ingest=0,
    enqueued_append=0,
    skipped_complete=0,
    frontier_day=frontier_day,
  )

  def _walk(*_a, **_k):
    if raises:
      raise RuntimeError("walk failed")
    return stats

  monkeypatch.setattr(qo, "_boot_stream_discover", _walk)
  monkeypatch.setattr(
    qo, "_discover_mtime_days_from_identity", lambda *_a: mtime_days
  )
  ctx = pytest.raises(RuntimeError) if raises else contextlib.nullcontext()
  with ctx:
    qo._run_background_discover(
      client,
      "/archive",
      tgz_archive_dir="/daily",
      log_fn=lambda *a, **k: None,
      mtime_days=mtime_days,
      startdate=None,
      enddate=None,
    )


def test_whole_walk_sets_the_frontier_and_a_clean_walk_clears_it(
  monkeypatch, frontier
):
  _bg_walk(monkeypatch, mtime_days=None, frontier_day=date(2026, 9, 1))
  assert qo._discover_frontier() == date(2026, 9, 1)
  _bg_walk(monkeypatch, mtime_days=None, frontier_day=None)
  assert qo._discover_frontier() is None


def test_windowed_walk_only_lowers_the_frontier(monkeypatch, frontier):
  qo._set_discover_frontier(date(2026, 9, 1))
  _bg_walk(monkeypatch, mtime_days=1, frontier_day=None)
  assert qo._discover_frontier() == date(2026, 9, 1)
  _bg_walk(monkeypatch, mtime_days=1, frontier_day=date(2026, 8, 20))
  assert qo._discover_frontier() == date(2026, 8, 20)


def test_failed_whole_walk_keeps_the_frontier(monkeypatch, frontier):
  qo._set_discover_frontier(date(2026, 9, 1))
  _bg_walk(monkeypatch, mtime_days=None, raises=True)
  assert qo._discover_frontier() == date(2026, 9, 1)


def _fill(monkeypatch, tmp_path, idents):
  client = SyncTimedbJobStore("")
  jq.reset_job_queue_script_cache_for_tests()
  for ident in idents:
    qo._DAY_CLOSE_YIELD_BACKOFF.clear_tar(ident)
    jq.enqueue_list_job(client, kind="day_close", identity=ident)
  ran = []
  monkeypatch.setattr(
    qo,
    "_run_day_close_job",
    lambda ident, **_k: ran.append(ident) or "complete",
  )
  monkeypatch.setattr(qo.cfg, "get_sync_day_close_max_inflight", lambda: 8)
  monkeypatch.setattr(qo, "_hot_days", lambda: 3)
  monkeypatch.setattr(qo, "_local_today", lambda: TODAY)
  with ThreadPoolExecutor(max_workers=1) as ex:
    _n, skips = qo._fill_day_close_slots(
      client,
      executor=ex,
      inflight={},
      leases={},
      tgz_archive_dir=str(tmp_path),
      archive_data_dir=str(tmp_path),
      log_fn=lambda *a, **k: None,
    )
  return ran, skips, client


def test_day_close_holds_backfill_days_at_or_after_the_frontier(
  monkeypatch, tmp_path, frontier
):
  qo._set_discover_frontier(date(2026, 9, 1))
  idents = [
    "/d/2026-08-31.tar",
    "/d/2026-09-01.tar",
    "/d/2026-09-02.tar",
    f"/d/{TODAY.isoformat()}.tar",
  ]
  ran, skips, client = _fill(monkeypatch, tmp_path, idents)
  assert sorted(ran) == sorted([idents[0], idents[3]])
  assert skips == 2
  # Held days stay queued for the next walk.
  assert jq.queue_depth(client, kind="day_close") == 2


def test_day_close_holds_every_backfill_day_until_the_boot_walk_ends(
  monkeypatch, tmp_path, frontier
):
  qo._BOOT_FULL_DISCOVER_PENDING.set()
  idents = ["/d/2026-08-31.tar", f"/d/{TODAY.isoformat()}.tar"]
  ran, _skips, _client = _fill(monkeypatch, tmp_path, idents)
  assert ran == [idents[1]]
