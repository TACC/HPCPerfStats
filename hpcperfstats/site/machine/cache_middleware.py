"""Per-response cache timeout for @cache_page-style view caching."""
from django.middleware.cache import CacheMiddleware
from django.utils.decorators import decorator_from_middleware_with_args


class DynamicTimeoutCacheMiddleware(CacheMiddleware):
  """CacheMiddleware that resolves ``page_timeout`` per response via ``timeout_resolver``."""

  def __init__(self, get_response, cache_timeout=None, page_timeout=None, **kwargs):
    self._timeout_resolver = kwargs.pop("timeout_resolver", None)
    super().__init__(get_response, cache_timeout=cache_timeout, page_timeout=page_timeout, **kwargs)

  def process_response(self, request, response):
    if self._timeout_resolver is not None:
      try:
        self.page_timeout = self._timeout_resolver(request)
      except Exception:
        self.page_timeout = None
    return super().process_response(request, response)


def dynamic_cache_page(timeout_resolver, *, cache=None, key_prefix=None):
  """Like Django's cache_page but ``timeout_resolver(request)`` sets TTL each response."""
  return decorator_from_middleware_with_args(DynamicTimeoutCacheMiddleware)(
      timeout_resolver=timeout_resolver,
      page_timeout=None,
      cache_alias=cache,
      key_prefix=key_prefix,
  )
