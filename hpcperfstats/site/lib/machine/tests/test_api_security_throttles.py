"""Security regression tests for API throttling behavior."""

from unittest.mock import MagicMock, patch

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

  from hpcperfstats.site.lib.machine import api
  from hpcperfstats.site.lib.machine.throttles import StaffIngestThrottle

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


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "security-throttle-tests-job-list",
        }
    },
    REST_FRAMEWORK={
        "DEFAULT_THROTTLE_RATES": {
            "expensive_read": "1/min",
        },
    },
)
@pytest.mark.django_db(databases=[])
def test_job_list_throttles_repeated_expensive_reads():
  """View-level 429 when ExpensiveReadThrottle budget is exhausted."""
  from django.core.cache import cache

  from hpcperfstats.site.lib.machine import api

  cache.clear()
  factory = APIRequestFactory()
  mock_qs = MagicMock()
  mock_qs.count.return_value = 0

  def _job_list_request():
    wsgi = factory.get("/api/jobs/", REMOTE_ADDR="203.0.113.9")
    wsgi.session = {"username": "reader", "is_staff": False}
    return wsgi

  with patch.object(api, "_require_auth", return_value=None), patch.object(
      api,
      "_build_job_list_queryset_from_request",
      return_value=(mock_qs, {}, None, "-end_time"),
  ), patch.object(
      api, "build_job_list_qname_and_filter_summary", return_value=(None, []),
  ):
    first = api.job_list(_job_list_request())
    second = api.job_list(_job_list_request())

  assert first.status_code == 200
  assert second.status_code == 429
