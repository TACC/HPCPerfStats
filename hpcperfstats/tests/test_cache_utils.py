"""Unit tests for cache_utils.cached_orm (Redis-backed query caching).

Uses unittest.mock to patch Django cache so tests run without Django/Redis.
"""
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _dummy_django_settings(monkeypatch):
  """Provide a minimal settings object so cache_utils._cache_debug_enabled works in unit tests."""
  class DummySettings:
    DEBUG = False

  monkeypatch.setattr("hpcperfstats.site.machine.cache_utils.settings", DummySettings())


def test_cached_orm_miss_returns_query_result():
  """On cache miss, cached_orm calls query_fn and returns its result."""
  stored = {}
  mock_cache = MagicMock()
  mock_cache.get.side_effect = lambda key, default=None: stored.get(key, default)
  mock_cache.set.side_effect = lambda key, value, timeout=None: stored.update({key: value})

  with patch("hpcperfstats.site.machine.cache_utils.cache", mock_cache):
    from hpcperfstats.site.machine import cache_utils
    result = cache_utils.cached_orm("key1", 60, lambda: {"a": 1})
  assert result == {"a": 1}
  assert stored.get("key1") == {"a": 1}


def test_cached_orm_hit_returns_cached_value():
  """On cache hit, cached_orm returns cached value without calling query_fn."""
  cached = {"k": [1, 2, 3]}
  call_count = 0
  mock_cache = MagicMock()
  mock_cache.get.side_effect = lambda key, default=None: cached if key == "key2" else default
  mock_cache.set.side_effect = lambda k, v, timeout=None: None

  def query_fn():
    nonlocal call_count
    call_count += 1
    return cached

  with patch("hpcperfstats.site.machine.cache_utils.cache", mock_cache):
    from hpcperfstats.site.machine import cache_utils
    result = cache_utils.cached_orm("key2", 60, query_fn)
  assert result == {"k": [1, 2, 3]}
  assert call_count == 0


def test_cached_orm_caches_none_as_tuple():
  """cached_orm stores None as (None,) and returns None on hit."""
  stored = {}
  mock_cache = MagicMock()
  mock_cache.get.side_effect = lambda key, default=None: stored.get(key, default)
  mock_cache.set.side_effect = lambda key, value, timeout=None: stored.update({key: value})

  with patch("hpcperfstats.site.machine.cache_utils.cache", mock_cache):
    from hpcperfstats.site.machine import cache_utils
    result = cache_utils.cached_orm("key_none", 60, lambda: None)
  assert result is None
  assert stored["key_none"] == (None,)

  mock_cache.get.side_effect = lambda key, default=None: stored.get(key, default)
  with patch("hpcperfstats.site.machine.cache_utils.cache", mock_cache):
    from hpcperfstats.site.machine import cache_utils
    result2 = cache_utils.cached_orm("key_none", 60, lambda: "should not run")
  assert result2 is None


def test_cached_orm_exception_falls_back_to_query_fn():
  """If cache.get raises, cached_orm falls back to query_fn result."""
  mock_cache = MagicMock()
  mock_cache.get.side_effect = RuntimeError("redis down")

  with patch("hpcperfstats.site.machine.cache_utils.cache", mock_cache):
    from hpcperfstats.site.machine import cache_utils
    result = cache_utils.cached_orm("key_err", 60, lambda: "fallback")
  assert result == "fallback"


def test_cached_orm_get_failure_does_not_call_set():
  """If cache.get raises, do not attempt cache.set (degraded read path)."""
  mock_cache = MagicMock()
  mock_cache.get.side_effect = ConnectionError("redis unreachable")

  with patch("hpcperfstats.site.machine.cache_utils.cache", mock_cache):
    from hpcperfstats.site.machine import cache_utils
    result = cache_utils.cached_orm("key_no_set", 60, lambda: {"ok": 1})
  assert result == {"ok": 1}
  mock_cache.set.assert_not_called()


def test_cached_orm_set_failure_still_returns_query_fn_result():
  """If cache.set raises on miss, return computed value without caching."""
  stored = {}
  mock_cache = MagicMock()
  mock_cache.get.side_effect = lambda key, default=None: stored.get(key, default)
  mock_cache.set.side_effect = RuntimeError("redis read-only replica")

  calls = []

  def query_fn():
    calls.append(1)
    return {"v": 99}

  with patch("hpcperfstats.site.machine.cache_utils.cache", mock_cache):
    from hpcperfstats.site.machine import cache_utils
    result = cache_utils.cached_orm("key_set_err", 60, query_fn)
  assert result == {"v": 99}
  assert calls == [1]
  assert "key_set_err" not in stored
  mock_cache.set.assert_called()


def test_get_site_content_cache_timeout_fresh_when_newest_within_window():
  """Newest job end within SITE_FRESHNESS_WINDOW_DAYS => 3600."""
  from datetime import datetime, timedelta, timezone as dt_tz

  from hpcperfstats.site.machine import cache_utils

  now = datetime(2026, 4, 2, 12, 0, 0, tzinfo=dt_tz.utc)
  newest = now - timedelta(days=10)
  with patch.object(cache_utils, "get_site_newest_job_end_time", return_value=newest), patch.object(
      cache_utils.timezone, "now", return_value=now
  ):
    assert cache_utils.get_site_content_cache_timeout() == 3600


def test_get_site_content_cache_timeout_none_when_newest_stale():
  """Newest job end older than window => None (no Redis expiry)."""
  from datetime import datetime, timedelta, timezone as dt_tz

  from hpcperfstats.site.machine import cache_utils

  now = datetime(2026, 4, 2, 12, 0, 0, tzinfo=dt_tz.utc)
  newest = now - timedelta(days=20)
  with patch.object(cache_utils, "get_site_newest_job_end_time", return_value=newest), patch.object(
      cache_utils.timezone, "now", return_value=now
  ):
    assert cache_utils.get_site_content_cache_timeout() is None


def test_get_site_newest_job_end_time_coerces_unix_int_from_cache():
  """Redis/serializer may return epoch seconds as int; still drives TTL logic."""
  from datetime import datetime, timezone as dt_tz

  from hpcperfstats.site.machine import cache_utils

  epoch = int(datetime(2026, 4, 1, 0, 0, 0, tzinfo=dt_tz.utc).timestamp())
  stored = {cache_utils.KEY_SITE_NEWEST_JOB_END: epoch}
  mock_cache = MagicMock()
  mock_cache.get.side_effect = lambda key, default=None: stored.get(key, default)
  mock_cache.set.side_effect = lambda key, value, timeout=None: stored.update({key: value})
  mock_cache.delete.side_effect = lambda key: stored.pop(key, None)

  with patch("hpcperfstats.site.machine.cache_utils.cache", mock_cache):
    m = cache_utils.get_site_newest_job_end_time()
  assert isinstance(m, datetime)
  assert m.tzinfo is not None
  assert abs(m.timestamp() - epoch) < 1.0


def test_get_site_newest_job_end_time_deletes_cache_on_corrupt_value():
  """Unparseable cached probe is dropped and DB path is used."""
  from datetime import datetime, timezone as dt_tz

  from hpcperfstats.site.machine import cache_utils

  db_dt = datetime(2026, 4, 1, 0, 0, 0, tzinfo=dt_tz.utc)
  stored = {cache_utils.KEY_SITE_NEWEST_JOB_END: "not-a-date"}
  mock_cache = MagicMock()
  mock_cache.get.side_effect = lambda key, default=None: stored.get(key, default)
  mock_cache.set.side_effect = lambda key, value, timeout=None: stored.update({key: value})
  mock_cache.delete.side_effect = lambda key: stored.pop(key, None)

  mock_job_data = MagicMock()
  mock_job_data.objects.aggregate.return_value = {"x": db_dt}

  with patch("hpcperfstats.site.machine.cache_utils.cache", mock_cache):
    with patch("hpcperfstats.site.machine.models.job_data", mock_job_data):
      m = cache_utils.get_site_newest_job_end_time()

  mock_cache.delete.assert_called_with(cache_utils.KEY_SITE_NEWEST_JOB_END)
  assert m == db_dt
  mock_job_data.objects.aggregate.assert_called()


def test_invalidate_after_job_data_ingest_noop_when_zero():
  mock_cache = MagicMock()
  with patch("hpcperfstats.site.machine.cache_utils.cache", mock_cache):
    from hpcperfstats.site.machine import cache_utils

    cache_utils.invalidate_after_job_data_ingest(0)
  mock_cache.delete.assert_not_called()


def test_invalidate_home_options_query_cache_deletes_keys():
  mock_cache = MagicMock()
  with patch("hpcperfstats.site.machine.cache_utils.cache", mock_cache):
    from hpcperfstats.site.machine import cache_utils

    cache_utils.invalidate_home_options_query_cache()
  assert mock_cache.delete.call_count == 5


def test_invalidate_after_job_data_ingest_deletes_keys():
  mock_cache = MagicMock()
  with patch("hpcperfstats.site.machine.cache_utils.cache", mock_cache):
    from hpcperfstats.site.machine import cache_utils

    cache_utils.invalidate_after_job_data_ingest(3)
  assert mock_cache.delete.call_count == 5


def test_invalidate_after_job_data_ingest_granular_public_metrics_calls_for_jids():
  mock_cache = MagicMock()
  with patch("hpcperfstats.site.machine.cache_utils.cache", mock_cache):
    with patch(
        "hpcperfstats.site.machine.public_metrics_artifacts.invalidate_public_metrics_artifacts_for_jids",
    ) as mock_for_jids:
      with patch(
          "hpcperfstats.site.machine.public_metrics_artifacts.invalidate_all_public_metrics_artifacts",
      ) as mock_all:
        from hpcperfstats.site.machine import cache_utils

        cache_utils.invalidate_after_job_data_ingest(2, inserted_jids=["a", "b"])
  mock_for_jids.assert_called_once_with(["a", "b"])
  mock_all.assert_not_called()
  assert mock_cache.delete.call_count == 5


def test_invalidate_after_job_data_ingest_without_jids_marks_all_public_metrics_stale():
  mock_cache = MagicMock()
  with patch("hpcperfstats.site.machine.cache_utils.cache", mock_cache):
    with patch(
        "hpcperfstats.site.machine.public_metrics_artifacts.invalidate_public_metrics_artifacts_for_jids",
    ) as mock_for_jids:
      with patch(
          "hpcperfstats.site.machine.public_metrics_artifacts.invalidate_all_public_metrics_artifacts",
      ) as mock_all:
        from hpcperfstats.site.machine import cache_utils

        cache_utils.invalidate_after_job_data_ingest(1)
  mock_for_jids.assert_not_called()
  mock_all.assert_called_once()
  assert mock_cache.delete.call_count == 5


def test_warm_job_cache_entries_sets_job_keys():
  mock_cache = MagicMock()
  mock_job = MagicMock()
  mock_job.jid = "j1"
  with patch("hpcperfstats.site.machine.cache_utils.cache", mock_cache):
    from hpcperfstats.site.machine import cache_utils

    cache_utils.warm_job_cache_entries([mock_job], 3600)
  mock_cache.set.assert_called_once()
  args, kwargs = mock_cache.set.call_args
  assert args[0] == f"{cache_utils.KEY_JOB}:j1"
  assert args[1] is mock_job
  assert kwargs.get("timeout") == 3600


def test_invalidate_jid_derived_cache_keys_deletes_jid_table_window():
  """Ingest invalidation must drop jid_table window cache rows for the same jids."""
  mock_cache = MagicMock()
  with patch("hpcperfstats.site.machine.cache_utils.cache", mock_cache), patch(
      "hpcperfstats.site.machine.models.job_data.objects.filter",
  ) as mock_filter, patch(
      "hpcperfstats.site.machine.cache_utils.invalidate_jid_host_window_row_count_cache",
  ):
    qs = MagicMock()
    mock_filter.return_value = qs
    from hpcperfstats.site.machine import cache_utils

    cache_utils.invalidate_jid_derived_cache_keys(["j1", "j2"])
  delete_keys = [c.args[0] for c in mock_cache.delete.call_args_list if c.args]
  assert cache_utils.make_cache_key(cache_utils.KEY_JOB_JID_TABLE_WINDOW, "j1") in delete_keys
  assert cache_utils.make_cache_key(cache_utils.KEY_JOB_JID_TABLE_WINDOW, "j2") in delete_keys


def test_job_instance_cache_key_distinct_from_jid_table_window_key():
  """``jid_table`` caches a values_list tuple; API caches ``job_data`` — keys must not collide."""
  from hpcperfstats.site.machine.cache_utils import (
      KEY_JOB,
      KEY_JOB_JID_TABLE_WINDOW,
      make_cache_key,
  )

  assert make_cache_key(KEY_JOB, "676388") != make_cache_key(
      KEY_JOB_JID_TABLE_WINDOW, "676388"
  )


def test_make_cache_key_bounded_short_parts_unchanged():
  from hpcperfstats.site.machine.cache_utils import make_cache_key_bounded

  k = make_cache_key_bounded("agg_df", "j1", "t", "arc", "lb2048")
  assert k.count(":") == 4
  assert "j1" in k
  assert len(k) < 250


def test_make_cache_key_bounded_hashes_long_event_list():
  from hpcperfstats.site.machine.cache_utils import make_cache_key_bounded

  long_ev = ":".join("e{}".format(i) for i in range(200))
  k = make_cache_key_bounded(
      "agg_df", "jid9", "typ1", "value", long_ev, "lb2048",
  )
  assert len(k) < 250
  assert long_ev not in k
