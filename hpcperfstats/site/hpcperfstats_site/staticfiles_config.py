"""Staticfiles AppConfig that omits source maps from collectstatic.

Attributes:
  HPCStaticFilesConfig: StaticFilesConfig subclass that ignores ``*.map``.
"""

from __future__ import annotations

from django.contrib.staticfiles.apps import StaticFilesConfig


class HPCStaticFilesConfig(StaticFilesConfig):
  """
  Django staticfiles AppConfig that also ignores ``*.map`` source maps.

  Superclass ``StaticFilesConfig`` registers ``django.contrib.staticfiles``
  and the default collectstatic ignore patterns (``CVS``, ``.*``, ``*~``).
  This subclass adds ``*.map`` so ``collectstatic`` never copies browser
  source maps into ``STATIC_ROOT``. Lives outside ``apps.py`` so Django does
  not auto-select this class (or the imported superclass) when loading the
  site package from ``INSTALLED_APPS``.

  Attributes:
    ignore_patterns (list[str]): Glob patterns omitted from collectstatic,
      including ``*.map``.

  Examples:
    >>> "*.map" in HPCStaticFilesConfig.ignore_patterns
    True
  """

  ignore_patterns = [*StaticFilesConfig.ignore_patterns, "*.map"]
