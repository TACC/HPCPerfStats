"""Ensure ``django.utils.timezone.utc`` exists (alias removed in Django 5.0+)."""
from __future__ import annotations

from datetime import timezone as _dt_timezone

import django.utils.timezone as _django_tz

if not hasattr(_django_tz, "utc"):
  _django_tz.utc = _dt_timezone.utc
