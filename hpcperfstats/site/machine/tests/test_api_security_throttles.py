"""Security regression tests for API throttling behavior."""

from unittest.mock import patch

from django.test import RequestFactory, override_settings


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "security-throttle-tests-ingest",
        }
    },
    REST_FRAMEWORK={
        "DEFAULT_THROTTLE_CLASSES": [
            "hpcperfstats.site.machine.throttles.AuthenticatedUserOrApiKeyThrottle",
        ],
        "DEFAULT_THROTTLE_RATES": {
            "authenticated_user_or_api_key": "1/min",
            "expensive_read": "1/min",
            "staff_ingest": "1/min",
        },
    },
)
def test_sacct_ingest_throttles_repeated_staff_posts():
    from hpcperfstats.site.machine import api

    request1 = RequestFactory().post(
        "/api/sacct/ingest/?date=2024-01-02",
        data=b"x",
        content_type="text/plain",
    )
    request2 = RequestFactory().post(
        "/api/sacct/ingest/?date=2024-01-02",
        data=b"x",
        content_type="text/plain",
    )
    request1.session = {"username": "admin", "is_staff": True}
    request2.session = {"username": "admin", "is_staff": True}

    with patch.object(api, "_require_staff", return_value=None), patch.object(
        api, "sync_acct_from_content", return_value=1
    ), patch.object(api, "job_data") as mock_job_data:
        mock_job_data.objects.filter.return_value.values_list.return_value.iterator.return_value = (
            iter([])
        )
        response1 = api.sacct_ingest(request1)
        response2 = api.sacct_ingest(request2)

    assert response1.status_code == 200
    assert response2.status_code == 429
