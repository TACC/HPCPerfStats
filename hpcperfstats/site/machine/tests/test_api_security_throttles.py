"""Security regression tests for API throttling behavior."""

import pytest
from django.test import override_settings
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "security-throttle-tests-ingest",
        }
    },
    REST_FRAMEWORK={
        "DEFAULT_THROTTLE_RATES": {
            "staff_ingest": "1/min",
        },
    },
)
@pytest.mark.django_db
def test_sacct_ingest_throttles_repeated_staff_posts():
  from django.core.cache import cache

  from hpcperfstats.site.machine import api
  from hpcperfstats.site.machine.throttles import StaffIngestThrottle

  cache.clear()
  factory = APIRequestFactory()
  wsgi1 = factory.post(
      "/api/sacct/ingest/?date=2024-01-02",
      data=b"x",
      content_type="text/plain",
  )
  wsgi2 = factory.post(
      "/api/sacct/ingest/?date=2024-01-02",
      data=b"x",
      content_type="text/plain",
  )
  wsgi1.session = {"username": "admin", "is_staff": True}
  wsgi2.session = {"username": "admin", "is_staff": True}

  throttle = StaffIngestThrottle()
  view = api.sacct_ingest
  drf1 = Request(wsgi1)
  drf2 = Request(wsgi2)
  cache_key = throttle.get_cache_key(drf1, view)
  assert cache_key is not None
  assert throttle.get_cache_key(drf2, view) == cache_key

  assert throttle.allow_request(drf1, view) is True
  assert len(throttle.cache.get(cache_key, [])) == 1
  assert throttle.allow_request(drf2, view) is False
