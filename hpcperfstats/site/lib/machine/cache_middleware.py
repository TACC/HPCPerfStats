"""
Per-response cache timeout for @cache_page-style view caching.
"""
from __future__ import annotations

from typing import Any

from django.middleware.cache import CacheMiddleware
from django.utils.decorators import decorator_from_middleware_with_args


class DynamicTimeoutCacheMiddleware(CacheMiddleware):
  """
  CacheMiddleware that resolves ``page_timeout`` per response via.
  
  Attributes:
    _timeout_resolver: Attribute.
  """

  def __init__(
    self,
    get_response: Any,
    cache_timeout: Any | None = None,
    page_timeout: Any | None = None,
    **kwargs: Any,
  ) -> None:
    """
    Initialize a new instance.
    
    Args:
      get_response (Any): Get response passed to this helper.
      cache_timeout (Any | None): One of ``Any``, ``None``.
      page_timeout (Any | None): One of ``Any``, ``None``.
      **kwargs (Any): Extra keyword arguments forwarded to the wrapped API;
      keys and value types match that callee's signature.
    
    Returns:
      None
    
    Examples:
      >>> DynamicTimeoutCacheMiddleware(None, None, None)  # doctest: +SKIP
    """
    self._timeout_resolver = kwargs.pop("timeout_resolver", None)
    super().__init__(get_response, cache_timeout=cache_timeout, page_timeout=page_timeout, **kwargs)

  def process_response(self, request: Any, response: Any) -> Any:
    """
    Process the response.
    
    Args:
      request (Any): Request passed to this helper.
      response (Any): Response passed to this helper.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Examples:
      >>> DynamicTimeoutCacheMiddleware().process_response(None, None)
    """
    if self._timeout_resolver is not None:
      try:
        self.page_timeout = self._timeout_resolver(request)
      except Exception:
        self.page_timeout = None
    return super().process_response(request, response)


def dynamic_cache_page(
  timeout_resolver: Any,
  *,
  cache: Any | None = None,
  key_prefix: Any | None = None,
) -> Any:
  """
  Like Django's cache_page but ``timeout_resolver(request)`` sets TTL each.
  
    response.
  
  Args:
    timeout_resolver (Any): Timeout resolver passed to this helper.
    cache (Any | None): One of ``Any``, ``None``.
    key_prefix (Any | None): One of ``Any``, ``None``.
  
  Returns:
    Any: Value produced by this call (type depends on inputs).
  
  Examples:
    >>> dynamic_cache_page(None, None, None)  # doctest: +SKIP
  """
  return decorator_from_middleware_with_args(DynamicTimeoutCacheMiddleware)(
      timeout_resolver=timeout_resolver,
      page_timeout=None,
      cache_alias=cache,
      key_prefix=key_prefix,
  )
