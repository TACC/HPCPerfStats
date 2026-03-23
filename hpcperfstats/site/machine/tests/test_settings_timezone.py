"""Tests that Django TIME_ZONE comes from hpcperfstats.ini DEFAULT.timezone."""

import os
import subprocess
import sys
from pathlib import Path
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


def test_manage_check_uses_test_ini_without_staticfiles_w004(temp_ini):
  """manage.py check succeeds with test INI and no staticfiles.W004 warning."""
  repo_root = Path(__file__).resolve().parents[4]
  env = os.environ.copy()
  env["HPCPERFSTATS_INI"] = temp_ini
  env.setdefault("SECRET_KEY", "test-secret-key-not-for-production")

  result = subprocess.run(
      [sys.executable, "-m", "hpcperfstats.site.manage", "check"],
      cwd=str(repo_root),
      env=env,
      capture_output=True,
      text=True,
      check=False,
  )

  output = f"{result.stdout}\n{result.stderr}"
  assert result.returncode == 0, output
  assert "staticfiles.W004" not in output

