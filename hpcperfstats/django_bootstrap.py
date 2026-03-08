"""Centralized Django environment setup for scripts that need the ORM.

Use ensure_django() before importing Django models or using the Django DB connection.
wsgi.py and manage.py only set DJANGO_SETTINGS_MODULE and should not call this.
"""
import os

DJANGO_SETTINGS_MODULE = "hpcperfstats.site.hpcperfstats_site.settings"


def ensure_django():
  """Set DJANGO_SETTINGS_MODULE if unset and run django.setup(). Idempotent after first call."""
  os.environ.setdefault("DJANGO_SETTINGS_MODULE", DJANGO_SETTINGS_MODULE)
  import django
  django.setup()
