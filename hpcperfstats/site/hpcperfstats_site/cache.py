"""Memcached cache backend with larger server_max_value_length for big objects.

AI generated.
"""
import pickle
import threading

from django.core.cache.backends.memcached import MemcachedCache


class LargeMemcachedCache(MemcachedCache):
  """Memcached cache for large objects (e.g. 50MB max value).

    AI generated.
    """

  def __init__(self, server, params):
    super().__init__(server, params)
    self._client_lock = threading.Lock()

  @property
  def _cache(self):
    """Lazy-initialize client with HIGHEST_PROTOCOL and server_max_value_length 50MB.

        Thread-safe: first access from multiple threads uses a lock so only one
        client is created.
        """
    if getattr(self, '_client', None) is None:
      with self._client_lock:
        if getattr(self, '_client', None) is None:
          self._client = self._lib.Client(
              self._servers,
              pickleProtocol=pickle.HIGHEST_PROTOCOL,
              server_max_value_length=1024 * 1024 * 50)
    return self._client
