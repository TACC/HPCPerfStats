"""
Centralized Django environment setup for scripts that need the ORM.

Use ensure_django() before importing Django models or using the Django DB
connection. wsgi.py and manage.py only set DJANGO_SETTINGS_MODULE and should not
call this.

``django.setup()`` is **not** safe to run from several threads at once:
``LazySettings.__setattr__`` clears the instance ``__dict__`` before storing
``_wrapped``, so a concurrent reader falls back to the ``LazyObject`` class
attribute ``_wrapped = None`` and raises ``AttributeError: 'NoneType' object
has no attribute 'LOGGING'``. ensure_django() therefore serializes setup and
runs it once per process.

Attributes:
  DJANGO_SETTINGS_MODULE: Dotted path to the site settings module.
  _SETUP_LOCK: Reentrant mutex serializing ``django.setup()``.
  _SETUP_COMPLETE: True once ``django.setup()`` returned successfully.
  _SETUP_OWNER_IDENT: Thread ident currently inside ``django.setup()``.
"""
from __future__ import annotations

import os
import threading

DJANGO_SETTINGS_MODULE = "hpcperfstats.site.hpcperfstats_site.settings"

_SETUP_LOCK = threading.RLock()
_SETUP_COMPLETE = False
_SETUP_OWNER_IDENT: int | None = None


def ensure_django() -> None:
  """
  Set DJANGO_SETTINGS_MODULE if unset and run django.setup() exactly once.

  Safe to call from many threads (listend live-DB workers each call it at
  startup): the first caller runs ``django.setup()`` under a lock while the
  others block, then every later call returns immediately. A failed setup is
  not latched, so the next caller retries. A reentrant call from the thread
  already inside ``django.setup()`` (an app module bootstrapping at import
  time) is a no-op rather than a ``populate() isn't reentrant`` error.

  Returns:
    None

  Examples:
    >>> from hpcperfstats.dbload.lib.django_bootstrap import ensure_django
    >>> ensure_django()  # doctest: +SKIP
    >>> from django.db import connections  # doctest: +SKIP
  """
  global _SETUP_COMPLETE
  global _SETUP_OWNER_IDENT
  if _SETUP_COMPLETE:
    return
  with _SETUP_LOCK:
    if _SETUP_COMPLETE:
      return
    if _SETUP_OWNER_IDENT == threading.get_ident():
      return
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", DJANGO_SETTINGS_MODULE)
    import django

    _SETUP_OWNER_IDENT = threading.get_ident()
    try:
      django.setup()
      _SETUP_COMPLETE = True
    finally:
      _SETUP_OWNER_IDENT = None
