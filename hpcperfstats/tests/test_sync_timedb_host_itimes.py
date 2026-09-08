"""Unit tests for host_data Unix-second probes with mocked ORM (no Postgres)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from django.db.utils import OperationalError

from hpcperfstats.dbload.lib import sync_timedb_host_itimes as host_itimes
from hpcperfstats.site.lib.machine.models import host_data


@pytest.fixture(autouse=True)
def _reset_caches():
  host_itimes.reset_host_itimes_caches()
  yield
  host_itimes.reset_host_itimes_caches()


def test_sampled_empty_seconds_returns_false():
  assert (
    host_itimes.host_sampled_timestamp_seconds_all_present("h", set())
    is False
  )
  assert (
    host_itimes.host_sampled_timestamp_seconds_all_present("h", []) is False
  )
  assert (
    host_itimes.host_sampled_timestamp_seconds_all_present("h", None)
    is False
  )


def test_iter_host_itimes_chunk_bounds_single_and_multi_day():
  ts0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
  ts1 = ts0 + timedelta(hours=12)
  chunks = list(host_itimes._iter_host_itimes_chunk_bounds(ts0, ts1))
  assert chunks == [(ts0, ts1)]
  ts2 = ts0 + timedelta(hours=49)
  chunks = list(host_itimes._iter_host_itimes_chunk_bounds(ts0, ts2))
  assert len(chunks) == 3
  assert chunks[0][0] == ts0
  assert chunks[-1][1] == ts2
  for low, high in chunks:
    assert high - low <= timedelta(hours=24) or high == ts2
    assert low < high


def test_collect_skips_none_and_naive_overflow(monkeypatch):
  naive = datetime(2026, 1, 1, 0, 0, 1)
  aware = datetime(2026, 1, 1, 0, 0, 2, tzinfo=timezone.utc)

  class _QS:
    def iterator(self):
      return iter([None, naive, aware])

    def distinct(self):
      return self

    def values_list(self, *_a, **_k):
      return self

  class _Mgr:
    def filter(self, **_k):
      return _QS()

  monkeypatch.setattr(host_data, "objects", _Mgr())
  monkeypatch.setattr(
    host_itimes.cfg,
    "get_sync_host_itimes_cache_max_timestamps_per_entry",
    lambda: 1,
  )
  itimes = set()
  overflow = host_itimes._collect_distinct_unix_seconds(
    "h",
    datetime(2026, 1, 1, tzinfo=timezone.utc),
    datetime(2026, 1, 2, tzinfo=timezone.utc),
    itimes,
  )
  assert overflow is host_itimes.HOST_ITIMES_SET_OVERFLOW
  assert int(naive.replace(tzinfo=timezone.utc).timestamp()) in itimes


def test_cached_reuses_and_trims(monkeypatch):
  calls = {"n": 0}

  class _QS:
    def iterator(self):
      return iter([datetime(2026, 1, 1, tzinfo=timezone.utc)])

    def distinct(self):
      return self

    def values_list(self, *_a, **_k):
      return self

  class _Mgr:
    def filter(self, **_k):
      calls["n"] += 1
      return _QS()

  monkeypatch.setattr(host_data, "objects", _Mgr())
  monkeypatch.setattr(
    host_itimes.cfg,
    "get_sync_host_itimes_cache_max_timestamps_per_entry",
    lambda: 100,
  )
  ts_low = datetime(2026, 1, 1, tzinfo=timezone.utc)
  ts_high = datetime(2026, 1, 1, 1, tzinfo=timezone.utc)
  a = host_itimes.host_recent_timestamps_cached("h1", ts_low, ts_high)
  b = host_itimes.host_recent_timestamps_cached("h1", ts_low, ts_high)
  assert a == b
  assert calls["n"] == 1


def test_bounded_query_failure_returns_overflow(monkeypatch):
  class _QS:
    def iterator(self):
      raise OperationalError(
        "canceling statement due to statement timeout"
      )

    def distinct(self):
      return self

    def values_list(self, *_a, **_k):
      return self

  class _Mgr:
    def filter(self, **_k):
      return _QS()

  monkeypatch.setattr(host_data, "objects", _Mgr())
  monkeypatch.setattr(
    host_itimes,
    "is_query_bounded_failure_error",
    lambda _exc: True,
  )
  ts_low = datetime(2026, 1, 1, tzinfo=timezone.utc)
  ts_high = datetime(2026, 1, 1, 1, tzinfo=timezone.utc)
  out = host_itimes.host_recent_timestamps_cached("h1", ts_low, ts_high)
  assert out is host_itimes.HOST_ITIMES_SET_OVERFLOW
  # second call logs once
  out2 = host_itimes.host_recent_timestamps_cached("h1", ts_low, ts_high)
  assert out2 is host_itimes.HOST_ITIMES_SET_OVERFLOW


def test_second_present_cache_and_all_present_overflow_fallback(monkeypatch):
  exists_calls = {"n": 0}

  class _QS:
    def exists(self):
      exists_calls["n"] += 1
      return True

  class _Mgr:
    def filter(self, **_k):
      return _QS()

  monkeypatch.setattr(host_data, "objects", _Mgr())
  assert host_itimes.host_timestamp_second_present_in_db(" h1 ", 100) is True
  assert host_itimes.host_timestamp_second_present_in_db("h1", 100) is True
  assert exists_calls["n"] == 1

  monkeypatch.setattr(
    host_itimes,
    "host_recent_timestamps_cached",
    lambda *_a, **_k: host_itimes.HOST_ITIMES_SET_OVERFLOW,
  )
  assert (
    host_itimes.host_sampled_timestamp_seconds_all_present("h1", {100, 200})
    is True
  )


def test_sampled_missing_second_false(monkeypatch):
  monkeypatch.setattr(
    host_itimes,
    "host_recent_timestamps_cached",
    lambda *_a, **_k: {100},
  )
  assert (
    host_itimes.host_sampled_timestamp_seconds_all_present("h1", {100, 200})
    is False
  )
  assert (
    host_itimes.host_sampled_timestamp_seconds_all_present("h1", {100})
    is True
  )


def test_zero_unix_second_set_is_valid_boundary(monkeypatch):
  monkeypatch.setattr(
    host_itimes,
    "host_recent_timestamps_cached",
    lambda *_a, **_k: {0},
  )
  assert (
    host_itimes.host_sampled_timestamp_seconds_all_present("h1", {0})
    is True
  )
