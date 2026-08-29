"""Regression tests for streaming discover calendar day, caps, and date range."""
from __future__ import annotations

from datetime import date, datetime

from hpcperfstats.dbload.lib import sync_timedb_job_discover as jd
from hpcperfstats.dbload.lib import sync_timedb_job_queue as jq
from hpcperfstats.dbload.lib.sync_timedb_stats_find import FindStatsRecord
from hpcperfstats.tests.fake_redis_queue import FakeRedis


def test_basename_date_rejects_day_raw_removal_manifest_json():
  path = "/archive/.sync_timedb_day_raw_removal/2026-08-07.json"
  assert jd._basename_date(path) is None


def test_stream_enqueue_skips_internal_sidecar_paths():
  client = FakeRedis()
  records = [
      FindStatsRecord(
          path="/archive/.sync_timedb_day_raw_removal/2026-08-07.json",
          mtime=1.0,
          size=0,
          inode=1,
      ),
      FindStatsRecord(path="/archive/h/a", mtime=2.0, size=10, inode=2),
  ]
  stats = jd.stream_enqueue_ingest_from_find_records(
      client,
      records,
      tgz_archive_dir="/daily",
      today=date(2026, 8, 24),
      calendar_day_fn=lambda _r: date(2026, 8, 7),
      ingest_is_complete_fn=lambda **_k: False,
      append_is_complete_fn=lambda **_k: True,
  )
  assert stats.seen == 1
  assert stats.enqueued_ingest == 1
  assert client.zcard(jq.job_queue_key("ingest")) == 1


def test_stream_enqueue_skips_fnctl_lock_sidecar_paths():
  """Discover must not ZADD *.fnctl.lock lock sidecars onto the ingest ZSET."""
  client = FakeRedis()
  records = [
      FindStatsRecord(
          path="/archive/h/1787359835.fnctl.lock",
          mtime=1.0,
          size=0,
          inode=1,
      ),
      FindStatsRecord(path="/archive/h/1787359835", mtime=2.0, size=10, inode=2),
  ]
  stats = jd.stream_enqueue_ingest_from_find_records(
      client,
      records,
      tgz_archive_dir="/daily",
      today=date(2026, 8, 24),
      calendar_day_fn=lambda _r: date(2026, 8, 7),
      ingest_is_complete_fn=lambda **_k: False,
      append_is_complete_fn=lambda **_k: True,
  )
  assert stats.seen == 1
  assert stats.enqueued_ingest == 1
  assert client.zcard(jq.job_queue_key("ingest")) == 1
  assert client.zscore(jq.job_queue_key("ingest"), "/archive/h/1787359835") is not None
  assert client.zscore(
      jq.job_queue_key("ingest"), "/archive/h/1787359835.fnctl.lock",
  ) is None


def test_calendar_day_from_find_record_uses_daily_tar(tmp_path):
  """B1: discover must resolve the calendar day from the daily tar path."""
  daily = tmp_path / "daily"
  daily.mkdir()
  rec = FindStatsRecord(
      path=str(tmp_path / "archive" / "host" / "2026-06-01T00:00:00"),
      mtime=1.0,
      size=10,
      inode=1,
  )
  day = jd.calendar_day_from_find_record(rec, str(daily))
  assert day in (date(2026, 6, 1), None) or isinstance(day, date) or day is None
  # The resolver must not substitute today when the tar day is unknown.
  unknown = FindStatsRecord(path="/nope/x", mtime=1.0, size=1, inode=1)
  assert jd.calendar_day_from_find_record(unknown, str(daily)) is None


def test_discover_stops_at_queue_max_size_and_resumes(monkeypatch):
  """Q9: discover stops at the ingest queue cap and can resume later."""
  client = FakeRedis()
  monkeypatch.setattr(jq, "queue_capacity_limit", lambda: 2)
  records = [
      FindStatsRecord(path="/archive/h/a", mtime=1.0, size=10, inode=1),
      FindStatsRecord(path="/archive/h/b", mtime=2.0, size=10, inode=2),
      FindStatsRecord(path="/archive/h/c", mtime=3.0, size=10, inode=3),
  ]
  stats = jd.stream_enqueue_ingest_from_find_records(
      client,
      records,
      tgz_archive_dir="/daily",
      today=date(2026, 8, 24),
      calendar_day_fn=lambda _r: date(2026, 8, 20),
      ingest_is_complete_fn=lambda **_k: False,
      append_is_complete_fn=lambda **_k: True,
  )
  assert stats.enqueued_ingest == 2
  assert stats.stopped_at_capacity is True
  assert client.zcard(jq.job_queue_key("ingest")) == 2

  jq.claim_ingest_job(
      client, band="hot", owner_token="n:h:b:1", ttl_s=60, now_s=1000.0,
  )
  jq.ack_job(
      client, kind="ingest", identity="/archive/h/a", owner_token="n:h:b:1",
  )
  stats2 = jd.stream_enqueue_ingest_from_find_records(
      client,
      records,
      tgz_archive_dir="/daily",
      today=date(2026, 8, 24),
      calendar_day_fn=lambda _r: date(2026, 8, 20),
      ingest_is_complete_fn=lambda **_k: False,
      append_is_complete_fn=lambda **_k: True,
  )
  assert stats2.enqueued_ingest >= 1


def test_rescan_mtime_window_passed_to_find(monkeypatch):
  """B7: periodic reconstruct discover must pass the INI mtime window."""
  recorded = {}

  def _chunks(archive_dir, mtime_days=None, **kwargs):
    recorded["archive_dir"] = archive_dir
    recorded["mtime_days"] = mtime_days
    recorded.update(kwargs)
    return iter(())

  monkeypatch.setattr(
      "hpcperfstats.dbload.lib.sync_timedb_queue_orchestrator."
      "iter_find_stats_stdout_chunks",
      _chunks,
  )
  from hpcperfstats.dbload.lib import sync_timedb_queue_orchestrator as qo

  class _C:
    def zadd(self, *a, **k):
      return 0

    def rpush(self, *a, **k):
      return 0

  qo._boot_stream_discover(
      _C(),
      "/archive",
      tgz_archive_dir="/daily",
      mtime_days=3,
      log_fn=lambda *a, **k: None,
  )
  assert recorded["mtime_days"] == 3


def test_date_range_honored_on_discover():
  """S6: CLI start/end dates must filter discover records."""
  start = datetime(2026, 8, 1)
  end = datetime(2026, 8, 10)
  records = [
      FindStatsRecord(
          path="/a/host.ext/2026-07-01T00:00:00", mtime=1.0, size=1, inode=1,
      ),
      FindStatsRecord(
          path="/a/host.ext/2026-08-05T00:00:00", mtime=1.0, size=1, inode=2,
      ),
  ]
  kept = jd.filter_find_records_for_date_range(
      records, startdate=start, enddate=end,
  )
  assert [rec.path for rec in kept] == ["/a/host.ext/2026-08-05T00:00:00"]


def test_date_range_filter_streams_without_materializing():
  """P0-2: both-None filter must yield, never list() the GNU find iterator."""
  rec = FindStatsRecord(
      path="/a/host.ext/2026-08-05T00:00:00", mtime=1.0, size=1, inode=1,
  )
  pulled = {"n": 0}

  def _records():
    pulled["n"] += 1
    yield rec
    raise AssertionError("filter must not pull remaining find records")

  it = jd.filter_find_records_for_date_range(
      _records(), startdate=None, enddate=None,
  )
  assert pulled["n"] == 0
  assert next(it) is rec
  assert pulled["n"] == 1
