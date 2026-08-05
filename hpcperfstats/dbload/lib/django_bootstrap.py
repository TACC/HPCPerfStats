"""
Centralized Django environment setup for scripts that need the ORM.

Use ensure_django() before importing Django models or using the Django DB
connection. wsgi.py and manage.py only set DJANGO_SETTINGS_MODULE and should not
call this.

Attributes:
  DJANGO_SETTINGS_MODULE: Attribute.
"""
from __future__ import annotations

import os

DJANGO_SETTINGS_MODULE = "hpcperfstats.site.hpcperfstats_site.settings"


def ensure_django() -> None:
  """
  Set DJANGO_SETTINGS_MODULE if unset and run django.setup(). Idempotent after.
  
    first call.
  
  Returns:
    None
  
  Examples:
    >>> ensure_django()  # doctest: +SKIP
  """
  os.environ.setdefault("DJANGO_SETTINGS_MODULE", DJANGO_SETTINGS_MODULE)
  import django
  django.setup()
