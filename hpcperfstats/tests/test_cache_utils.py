"""Unit tests for cache_utils.cached_orm (Redis-backed query caching).

Uses unittest.mock to patch Django cache so tests run without Django/Redis.
"""
from unittest.mock import MagicMock, patch

import pytest


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
