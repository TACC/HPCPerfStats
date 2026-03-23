"""Tests that Django TIME_ZONE comes from hpcperfstats.ini DEFAULT.timezone."""

import os
import warnings

from django.conf import settings
from django.core.cache.backends.base import CacheKeyWarning


def test_time_zone_from_ini():
  """TIME_ZONE matches timezone value configured in hpcperfstats.ini."""
  assert settings.TIME_ZONE == "UTC"


def test_cache_key_warning_is_suppressed_globally():
  """Django cache key warning is filtered to avoid noisy memcached logs."""
  with warnings.catch_warnings(record=True) as caught:
    warnings.warn_explicit(
        "Cache key will cause errors if used with memcached: 'x' (longer than 250)",
        category=CacheKeyWarning,
        filename="django/core/cache/backends/base.py",
        lineno=119,
        module="django.core.cache.backends.base",
    )
  assert not caught


def test_staticfiles_dirs_only_contains_existing_directories():
  """STATICFILES_DIRS includes only paths that exist on disk."""
  for static_dir in settings.STATICFILES_DIRS:
    assert os.path.isdir(static_dir)

