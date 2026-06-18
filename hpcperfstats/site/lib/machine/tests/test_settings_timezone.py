"""Tests that Django TIME_ZONE comes from hpcperfstats.ini DEFAULT.timezone."""

import os
import inspect
import subprocess
import sys
from pathlib import Path

from django.conf import settings

import hpcperfstats.dbload.lib.conf_parser as cfg
import hpcperfstats.site.hpcperfstats_site.settings as site_settings


def test_time_zone_from_ini():
  """TIME_ZONE matches timezone value configured in hpcperfstats.ini."""
  assert settings.TIME_ZONE == cfg.get_timezone()


def test_django_utils_timezone_utc_alias_from_settings():
  """Django 5+ removed timezone.utc; settings bind datetime.timezone.utc for compatibility."""
  from datetime import timezone as dt_timezone
  from django.utils import timezone as dj_timezone

  assert dj_timezone.utc is dt_timezone.utc


def test_cache_key_warning_is_suppressed_globally():
  """Settings include an explicit CacheKeyWarning suppression rule."""
  settings_source = inspect.getsource(site_settings)
  assert "warnings.filterwarnings(" in settings_source
  assert "CacheKeyWarning" in settings_source


def test_staticfiles_dirs_only_contains_existing_directories():
  """STATICFILES_DIRS includes only paths that exist on disk."""
  for static_dir in settings.STATICFILES_DIRS:
    assert os.path.isdir(static_dir)


def test_manage_check_uses_test_ini_without_staticfiles_w004(temp_ini):
  """manage.py check succeeds with test INI and no staticfiles.W004 warning."""
  repo_root = Path(__file__).resolve().parents[5]
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

