"""Dropped-at-cap files are rediscovered; Codon ingests report parsed rows.

hpcperfstats04 2026-10-29: 732k raw files sat on disk. Discover drops a
non-hot incomplete file while the ingest queue is at the cap
(``sync_ingest_queue_max_size``), nothing remembers it, and the full-archive
walk only runs at boot, so those files waited for the next restart. Separately
the Codon COPY-frame branch never counted ``stats_rows_parsed``, so every
Codon ingest logged ``stats_rows_parsed=0`` and recorded a zero-host mark.
"""

from __future__ import annotations

import time
from datetime import date

import pytest

from hpcperfstats.dbload import sync_timedb as st
from hpcperfstats.dbload.lib import (
  conf_parser as cfg,
  sync_timedb_job_discover as jd,
  sync_timedb_job_store as jq,
  sync_timedb_queue_orchestrator as qo,
)
from hpcperfstats.dbload.lib.sync_timedb_job_store import SyncTimedbJobStore
from hpcperfstats.dbload.lib.sync_timedb_stats_find import FindStatsRecord

TODAY = date(2026, 9, 14)


def _discover(client, records, day_for):
  return jd.stream_enqueue_ingest_from_find_records(
    client,
    records,
    tgz_archive_dir="/daily",
    today=TODAY,
    hot_days=3,
    calendar_day_fn=lambda rec: day_for(rec.path),
    ingest_is_complete_fn=lambda **_k: False,
    append_is_complete_fn=lambda **_k: True,
  )


def _full_store(monkeypatch):
  monkeypatch.setattr(jq, "queue_capacity_limit", lambda: 2)
  client = SyncTimedbJobStore("")
  for ident in ("/old/a", "/old/b"):
    score = jq.encode_ingest_score(
      band="catchup", day=date(2026, 6, 1), today=TODAY, identity=ident
    )
    jq.zadd_ingest_job(client, identity=ident, score=score)
  return client


def test_discover_counts_catchup_files_it_dropped_at_the_cap(monkeypatch):
  client = _full_store(monkeypatch)
  records = [
    FindStatsRecord(path="/archive/h/hot", mtime=1.0, size=10, inode=1),
    FindStatsRecord(path="/archive/h/old1", mtime=1.0, size=10, inode=2),
    FindStatsRecord(path="/archive/h/old2", mtime=1.0, size=10, inode=3),
  ]
  stats = _discover(
    client,
    records,
    lambda p: TODAY if p.endswith("hot") else date(2026, 6, 1),
  )
  assert stats.stopped_at_capacity is True
  assert stats.dropped_at_capacity == 2


def test_discover_drops_nothing_when_the_queue_has_room(monkeypatch):
  monkeypatch.setattr(jq, "queue_capacity_limit", lambda: 100)
  stats = _discover(
    SyncTimedbJobStore(""),
    [FindStatsRecord(path="/archive/h/old1", mtime=1.0, size=10, inode=2)],
    lambda _p: date(2026, 6, 1),
  )
  assert stats.dropped_at_capacity == 0


@pytest.fixture
def _pending(monkeypatch):
  qo._FULL_REDISCOVER_PENDING.clear()
  yield qo._FULL_REDISCOVER_PENDING
  qo._FULL_REDISCOVER_PENDING.clear()


def _run_bg_discover(monkeypatch, dropped):
  client = SyncTimedbJobStore("")
  jq.enqueue_list_job(
    client, kind=jq.JOB_KIND_DISCOVER, identity="rescan:1", dedupe=True
  )
  stats = jd.StreamingDiscoverStats(
    seen=10,
    enqueued_ingest=0,
    enqueued_append=0,
    skipped_complete=0,
    stopped_at_capacity=bool(dropped),
    dropped_at_capacity=dropped,
  )
  monkeypatch.setattr(qo, "_boot_stream_discover", lambda *a, **k: stats)
  qo._run_background_discover(
    client,
    "/archive",
    tgz_archive_dir="/daily",
    log_fn=lambda *a, **k: None,
    mtime_days=1,
    startdate=None,
    enddate=None,
  )


def test_background_discover_flags_a_full_rediscover_after_drops(
  monkeypatch, _pending
):
  _run_bg_discover(monkeypatch, dropped=5)
  assert _pending.is_set()


def test_background_discover_without_drops_flags_nothing(monkeypatch, _pending):
  _run_bg_discover(monkeypatch, dropped=0)
  assert not _pending.is_set()


def _idle_pass(
  monkeypatch,
  *,
  catchup_queued,
  cap=3000,
  interval_hours=6,
  last_full_age_h=0,
):
  submitted = []
  monkeypatch.setattr(qo, "_last_idle_reconstruct_mono", 0.0)
  monkeypatch.setattr(qo, "_discover_bg_is_busy", lambda: False)
  monkeypatch.setattr(
    qo,
    "_submit_background_discover",
    lambda *a, **k: submitted.append(k["mtime_days"]),
  )
  monkeypatch.setattr(
    qo, "_enqueue_day_closes_for_daily_dir", lambda *a, **k: 0
  )
  monkeypatch.setattr(qo, "_readmit_due_dead_letters", lambda *a, **k: 0)
  monkeypatch.setattr(qo, "sealed_integrity_sweep_once", lambda *a, **k: None)
  monkeypatch.setattr(qo, "sweep_dead_archive_scratch", lambda *a, **k: 0)
  monkeypatch.setattr(jq, "enqueue_list_job", lambda *a, **k: True)
  monkeypatch.setattr(
    jq, "ingest_queue_census", lambda _c: (0, catchup_queued, catchup_queued)
  )
  monkeypatch.setattr(jd, "discover_ingest_capacity_limit", lambda: cap)
  monkeypatch.setattr(
    cfg, "get_sync_ingest_full_rescan_interval_hours", lambda: interval_hours
  )
  monkeypatch.setattr(
    qo,
    "_last_full_rediscover_mono",
    time.monotonic() - last_full_age_h * 3600 if last_full_age_h else 0.0,
  )
  logs: list[str] = []
  qo._idle_reconstruct_pass(
    SyncTimedbJobStore(""),
    "/archive",
    tgz_archive_dir="/daily",
    log_fn=lambda msg, **_k: logs.append(msg),
    mtime_days=1,
  )
  return submitted, logs


def test_idle_pass_runs_a_full_walk_once_the_queue_is_below_low_water(
  monkeypatch, _pending
):
  _pending.set()
  submitted, logs = _idle_pass(monkeypatch, catchup_queued=1000)
  assert submitted == [None]
  assert not _pending.is_set()
  assert any("full rediscover" in m for m in logs)


def test_idle_pass_waits_while_the_queue_is_above_low_water(
  monkeypatch, _pending
):
  _pending.set()
  submitted, _logs = _idle_pass(monkeypatch, catchup_queued=2000)
  assert submitted == [1]
  assert _pending.is_set()


def test_idle_pass_keeps_the_incremental_window_without_drops(
  monkeypatch, _pending
):
  submitted, _logs = _idle_pass(monkeypatch, catchup_queued=0)
  assert submitted == [1]


def test_idle_pass_walks_the_whole_archive_every_interval(
  monkeypatch, _pending
):
  submitted, logs = _idle_pass(
    monkeypatch, catchup_queued=10, last_full_age_h=7
  )
  assert submitted == [None]
  assert any("reason=periodic" in m for m in logs)


def test_idle_pass_skips_the_periodic_walk_inside_the_interval(
  monkeypatch, _pending
):
  submitted, _logs = _idle_pass(
    monkeypatch, catchup_queued=10, last_full_age_h=2
  )
  assert submitted == [1]


def test_periodic_walk_waits_for_the_queue_to_drain(monkeypatch, _pending):
  submitted, _logs = _idle_pass(
    monkeypatch, catchup_queued=2900, last_full_age_h=7
  )
  assert submitted == [1]


def test_periodic_walk_can_be_disabled(monkeypatch, _pending):
  submitted, _logs = _idle_pass(
    monkeypatch, catchup_queued=0, interval_hours=0, last_full_age_h=999
  )
  assert submitted == [1]


def test_dropped_files_are_still_rediscovered_when_the_periodic_walk_is_off(
  monkeypatch, _pending
):
  _pending.set()
  submitted, _logs = _idle_pass(monkeypatch, catchup_queued=0, interval_hours=0)
  assert submitted == [None]


def _codon_ingest(monkeypatch, tmp_path, host_copy, proc_copy):
  stats_file = tmp_path / "host.example.com" / "1789000000"
  stats_file.parent.mkdir(parents=True)
  stats_file.write_text("x\n", encoding="utf-8")

  def fake_parse(_path, *, on_chunk, **_kw):
    on_chunk({"host_copy": host_copy, "proc_copy": proc_copy}, {})

  monkeypatch.setattr(st, "_ensure_ingest_codon_sticky_slot", lambda: None)
  monkeypatch.setattr(st, "update_worker_substage", lambda *a, **k: None)
  monkeypatch.setattr(st, "close_old_connections", lambda: None)
  monkeypatch.setattr(st, "_release_ingest_worker_heap", lambda *a, **k: None)
  monkeypatch.setattr(
    st, "_resolve_streaming_ingest_start", lambda *_a, **_k: (False, (0, True))
  )
  monkeypatch.setattr(st, "parse_stats_file_streaming_incremental", fake_parse)
  monkeypatch.setattr(
    st, "_submit_stats_payload_write", lambda *a, **k: (None, True, True)
  )
  monkeypatch.setattr(
    st, "_drain_ingest_write_pipeline", lambda _o: (True, True)
  )
  result = st._add_stats_file_to_db_streaming_incremental(
    str(stats_file), time.time()
  )
  return st._unpack_ingest_worker_result(result)


def test_codon_ingest_reports_the_host_rows_it_parsed(monkeypatch, tmp_path):
  unpacked = _codon_ingest(monkeypatch, tmp_path, b"r1\nr2\nr3\n", b"p1\np2\n")
  meta = unpacked[-1]
  assert meta["stats_rows"] == 3
  assert meta["stats_rows_parsed"] == 3
  assert meta["proc_rows"] == 2


def test_codon_ingest_with_host_rows_does_not_qualify_for_a_zero_host_mark(
  monkeypatch, tmp_path
):
  from hpcperfstats.dbload.lib import sync_timedb_zero_host_ingest_mark as zh

  meta = _codon_ingest(monkeypatch, tmp_path, b"r1\nr2\n", b"")[-1]
  recorded = []
  monkeypatch.setattr(
    zh,
    "record_zero_host_ingest_mark",
    lambda *a, **k: recorded.append(a) or True,
  )
  assert not zh.maybe_record_zero_host_ingest_mark_from_outcome(
    "/x/1789000000",
    ingest_ok=True,
    outcome="ingested",
    stats_rows=meta["stats_rows"],
    stats_rows_parsed=meta["stats_rows_parsed"],
  )
  assert not recorded
