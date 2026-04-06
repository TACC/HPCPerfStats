"""Tests for DynamicTimeoutCacheMiddleware."""

from unittest.mock import MagicMock, patch

from django.middleware.cache import CacheMiddleware

from hpcperfstats.site.machine.cache_middleware import (
    DynamicTimeoutCacheMiddleware,
    dynamic_cache_page,
)


def test_process_response_uses_timeout_resolver():
  captured = []

  def spy_process(self, request, response):
    captured.append(self.page_timeout)
    return response

  get_response = MagicMock()
  mw = DynamicTimeoutCacheMiddleware(
      get_response, page_timeout=1, timeout_resolver=lambda r: 42
  )
  request = MagicMock()
  response = MagicMock()
  with patch.object(CacheMiddleware, "process_response", spy_process):
    out = mw.process_response(request, response)
  assert out is response
  assert captured == [42]


def test_process_response_resolver_exception_sets_none_timeout():
  captured = []

  def spy_process(self, request, response):
    captured.append(self.page_timeout)
    return response

  mw = DynamicTimeoutCacheMiddleware(
      MagicMock(),
      page_timeout=99,
      timeout_resolver=lambda r: (_ for _ in ()).throw(RuntimeError("x")),
  )
  with patch.object(CacheMiddleware, "process_response", spy_process):
    mw.process_response(MagicMock(), MagicMock())
  assert captured == [None]


def test_dynamic_cache_page_returns_decorator():
  dec = dynamic_cache_page(lambda r: 10)
  assert callable(dec)
