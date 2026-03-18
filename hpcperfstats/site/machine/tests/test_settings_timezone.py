"""Tests that Django TIME_ZONE comes from hpcperfstats.ini DEFAULT.timezone."""

from django.conf import settings


def test_time_zone_from_ini():
  """TIME_ZONE matches timezone value configured in hpcperfstats.ini."""
  assert settings.TIME_ZONE == "UTC"

