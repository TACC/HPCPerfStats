"""django.utils.timezone.utc compatibility shim used by dbload."""
from __future__ import annotations


def test_django_timezone_utc_shim_defines_utc():
  import django.utils.timezone as django_tz

  import hpcperfstats.dbload.django_timezone_utc_shim  # noqa: F401

  assert hasattr(django_tz, "utc")
  assert django_tz.utc is not None
