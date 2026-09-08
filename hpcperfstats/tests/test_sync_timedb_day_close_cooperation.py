"""Unit tests for day-close yield/defer cooperation (no live archive I/O)."""

from __future__ import annotations

import os
import time

import pytest

from hpcperfstats.dbload.lib import sync_timedb_day_close_cooperation as coop


@pytest.fixture(autouse=True)
def _clear_yield_state():
  # Current behavior: empty string normalizes to "." — clear both.
  coop.clear_day_close_yield("")
  coop.clear_day_close_yield(".")
  coop.clear_day_close_yield("/daily/2020-01-01.tar")
  yield
  coop.clear_day_close_yield("")
  coop.clear_day_close_yield(".")
  coop.clear_day_close_yield("/daily/2020-01-01.tar")


def test_tar_norm_empty_becomes_dot():
  """Empty tar paths normpath to '.' and are not treated as missing."""
  assert coop._tar_norm("") == os.path.normpath("")
  assert coop._tar_norm("") == "."
  assert coop._tar_norm(None) == "."


def test_signal_empty_tar_sets_dot_event():
  logs = []
  coop.signal_day_close_yield(
    "", reason="ingest_tar_hot", log_fn=lambda msg, **_k: logs.append(msg)
  )
  assert coop.day_close_yield_event_set("") is True
  assert coop.day_close_yield_event_set(".") is True
  assert any("yield signal tar=." in line for line in logs)


def test_signal_and_clear_named_tar():
  tar = "/daily/2020-01-01.tar"
  coop.signal_day_close_yield(tar, reason="populate_active", log_fn=None)
  assert coop.day_close_yield_event_set(tar) is True
  coop.clear_day_close_yield(tar)
  assert coop.day_close_yield_event_set(tar) is False


def test_day_close_yield_event_set_unknown_false():
  assert coop.day_close_yield_event_set("/daily/missing.tar") is False


def test_hot_path_sticky_yield_alone_does_not_abort(monkeypatch):
  tar = "/daily/2020-01-01.tar"
  coop.signal_day_close_yield(tar, reason="stale", log_fn=None)
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_archive_helpers.calendar_date_from_daily_tar_path",
    lambda _p: None,
  )
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_archive_members_coord.ingest_tar_hot_for_day",
    lambda _d: False,
  )
  hot, reason = coop._hot_path_contention_reasons(tar, tgz_archive_dir="/tgz")
  assert hot is False
  assert reason == ""
  assert coop.day_close_yield_event_set(tar) is False


def test_hot_path_ingest_tar_hot(monkeypatch):
  tar = "/daily/2020-01-01.tar"
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_archive_helpers.calendar_date_from_daily_tar_path",
    lambda _p: type("D", (), {"isoformat": lambda self: "2020-01-01"})(),
  )
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_archive_members_coord.ingest_tar_hot_for_day",
    lambda day: day == "2020-01-01",
  )
  hot, reason = coop.day_close_yield_requested(tar, tgz_archive_dir="/tgz")
  assert hot is True
  assert reason == "ingest_tar_hot"


def test_hot_path_populate_active(monkeypatch):
  tar = "/daily/2020-01-01.tar"
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_archive_helpers.calendar_date_from_daily_tar_path",
    lambda _p: type("D", (), {"isoformat": lambda self: "2020-01-01"})(),
  )
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_archive_members_coord.ingest_tar_hot_for_day",
    lambda _d: False,
  )
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_archive_members_coord.archive_members_populate_shows_progress_for_day",
    lambda day, tgz: day == "2020-01-01" and tgz == "/tgz",
  )
  hot, reason = coop._hot_path_contention_reasons(tar, tgz_archive_dir="/tgz")
  assert hot is True
  assert reason == "populate_active"


def test_should_poll_day_close_yield_boundary():
  now = time.monotonic()
  assert coop.should_poll_day_close_yield(now) is False
  assert (
    coop.should_poll_day_close_yield(
      now - coop.DAY_CLOSE_YIELD_POLL_SECONDS,
    )
    is True
  )


def test_check_day_close_yield_or_continue_skips_before_interval(monkeypatch):
  last = time.monotonic()
  monkeypatch.setattr(
    coop, "day_close_yield_requested", lambda *_a, **_k: (True, "x")
  )
  updated, requested = coop.check_day_close_yield_or_continue(
    "/daily/2020-01-01.tar",
    last_poll_monotonic=last,
  )
  assert updated == last
  assert requested is False


def test_check_day_close_yield_or_continue_raises(monkeypatch):
  monkeypatch.setattr(coop, "should_poll_day_close_yield", lambda _t: True)
  monkeypatch.setattr(
    coop,
    "day_close_yield_requested",
    lambda *_a, **_k: (True, "ingest_tar_hot"),
  )
  with pytest.raises(coop.DayCloseYieldError) as exc:
    coop.check_day_close_yield_or_continue(
      "/daily/2020-01-01.tar",
      last_poll_monotonic=0.0,
      phase="seal",
    )
  assert exc.value.reason == "ingest_tar_hot"
  assert exc.value.phase == "seal"


def test_janitor_defer_empty_tar_is_dot_not_skipped():
  tracker = coop.JanitorDeferTracker()
  tracker.record_defer("", reason="write_lock_contended")
  assert tracker.defer_cap_exceeded("") is False
  assert tracker.write_lock_backoff_active("", now=time.time()) is True


def test_janitor_write_lock_backoff_boundary_schedule():
  tracker = coop.JanitorDeferTracker()
  tar = "/daily/2020-01-01.tar"
  now = 1_000_000.0
  monkeypatch_time = now

  def _fake_time():
    return monkeypatch_time

  orig = time.time
  try:
    time.time = _fake_time
    tracker.record_defer(tar, reason="write_lock_contended")
    assert tracker.write_lock_backoff_active(tar, now=now + 29.9) is True
    assert tracker.write_lock_backoff_active(tar, now=now + 30.1) is False
    tracker.record_defer(tar, reason="write_lock_contended")
    assert tracker.write_lock_backoff_active(tar, now=now + 59.9) is True
  finally:
    time.time = orig


def test_janitor_yield_backoff_and_skip_tars():
  tracker = coop.JanitorDeferTracker()
  tar = "/daily/2020-01-01.tar"
  tracker.record_yield_backoff(tar)
  assert tracker.yield_backoff_active(tar, now=time.time()) is True
  assert (
    tracker.yield_backoff_active("/missing.tar", now=time.time()) is False
  )
  skipped = tracker.write_lock_backoff_skip_tars([tar, ""], now=time.time())
  assert skipped == set()  # yield backoff is not write_lock backoff
  tracker.record_defer(tar, reason="write_lock_contended")
  skipped = tracker.write_lock_backoff_skip_tars([tar], now=time.time())
  assert os.path.normpath(tar) in skipped
  assert tracker.write_lock_backoff_skip_tars(None) == set()


def test_janitor_defer_cap_ticks_and_clear():
  tracker = coop.JanitorDeferTracker()
  tar = "/daily/2020-01-01.tar"
  assert tracker.defer_cap_exceeded(tar) is False
  for _ in range(coop.JANITOR_DEFER_CAP_TICKS):
    tracker.record_defer(tar, reason="ingest_tar_hot")
  assert tracker.defer_cap_exceeded(tar) is True
  tracker.clear_tar(tar)
  assert tracker.defer_cap_exceeded(tar) is False


def test_daily_tar_janitor_mutation_restore_and_disqualified(monkeypatch):
  tar = "/daily/2020-01-01.tar"
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_archive_helpers.calendar_date_from_daily_tar_path",
    lambda _p: type("D", (), {"isoformat": lambda self: "2020-01-01"})(),
  )
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_archive_members_coord.daily_tar_restore_in_progress_for_day",
    lambda day: day == "2020-01-01",
  )
  defer, reason = coop.daily_tar_janitor_mutation_should_defer(
    tar,
    tgz_archive_dir="/tgz",
    disqualified_daily_tars=set(),
  )
  assert defer is True
  assert reason == "daily_tar_restore"

  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_archive_members_coord.daily_tar_restore_in_progress_for_day",
    lambda _d: False,
  )
  monkeypatch.setattr(
    coop, "_hot_path_contention_reasons", lambda *_a, **_k: (False, "")
  )
  defer, reason = coop.daily_tar_janitor_mutation_should_defer(
    tar,
    tgz_archive_dir="/tgz",
    disqualified_daily_tars={os.path.normpath(tar)},
  )
  assert defer is True
  assert reason == "inflight_append"


def test_daily_tar_janitor_chunk_in_progress_day(monkeypatch):
  tar = "/daily/2020-01-01.tar"
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_archive_helpers.calendar_date_from_daily_tar_path",
    lambda _p: type("D", (), {"isoformat": lambda self: "2020-01-01"})(),
  )
  monkeypatch.setattr(
    "hpcperfstats.dbload.lib.sync_timedb_archive_members_coord.daily_tar_restore_in_progress_for_day",
    lambda _d: False,
  )
  monkeypatch.setattr(
    coop, "_hot_path_contention_reasons", lambda *_a, **_k: (False, "")
  )
  defer, reason = coop.daily_tar_janitor_mutation_should_defer(
    tar,
    tgz_archive_dir="/tgz",
    disqualified_daily_tars=set(),
    chunk_in_progress=True,
    chunk_day_tokens={"2020-01-01"},
  )
  assert defer is True
  assert reason == "chunk_in_progress_day"


def test_log_helpers_none_log_fn_no_raise():
  coop.log_janitor_day_close_defer(
    "/t", phase="seal", reason="x", log_fn=None
  )
  coop.log_janitor_day_close_yield(
    "/t", phase="seal", reason="x", log_fn=None
  )
  logs = []
  coop.log_janitor_day_close_defer(
    "/t",
    phase="seal",
    reason="x",
    log_fn=lambda msg, **_k: logs.append(msg),
  )
  assert logs
