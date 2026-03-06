"""Memcached cache backend with larger server_max_value_length for big objects.

AI generated.
"""
import pickle

from django.core.cache.backends.memcached import MemcachedCache


class LargeMemcachedCache(MemcachedCache):
    """Memcached cache for large objects (e.g. 50MB max value).

    AI generated.
    """
    @property
    def _cache(self):
        """Lazy-initialize client with HIGHEST_PROTOCOL and server_max_value_length 50MB.

        AI generated.
        """
        if getattr(self, '_client', None) is None:
            self._client = self._lib.Client(self._servers,
                           pickleProtocol=pickle.HIGHEST_PROTOCOL,
                           server_max_value_length = 1024*1024*50)
        return self._client
